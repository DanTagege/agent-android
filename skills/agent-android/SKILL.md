---
name: agent-android
description: 让 AI 通过 adb 观察和操作 Android 手机或模拟器。当用户要求验证 App 界面、复现崩溃、自动点击/输入、安装并启动 APK、抓取 UI 层级、查看 logcat 或对 Android 应用做端到端自测时使用。适用于 Claude Code、Codex、Cursor 等能执行终端命令的编程 Agent。
license: MIT
metadata:
  author: coco
  version: "0.1.0"
---

# agent-android

把 Android 屏幕变成一张「带标签的元素表」，而不是一张需要猜坐标的图片。
所有命令输出 JSON，可以直接解析。

## 何时使用

- 改完 Android 代码后，想让 Agent 自己装 APK、点进去验证效果
- 复现「点某个按钮会崩」并自动捞崩溃日志
- 需要批量走一遍登录、表单填写、列表滚动等 UI 流程
- 想知道当前屏幕上有哪些控件、文字、resource-id

## 前置条件

1. 本机装了 Android SDK platform-tools，`adb devices` 能看到设备
2. 模拟器（MuMu 用 `adb connect 127.0.0.1:16384`）或已授权 USB 调试的真机
3. 项目根目录执行 `pip install -e .`，或直接用 `python -m agent_android`

## 标准工作流

严格按「看 → 动 → 再看」循环，不要连发一串动作：

1. `agent-android connect`（连模拟器时）然后 `agent-android health` — 确认 adb、设备、uiautomator 都可用
2. `agent-android install <apk>` 然后 `agent-android launch <包名>`（如已装好可跳过）
3. `agent-android snapshot` — 拿到 `nodes`，每个元素有 `id`、`text`、`bounds`、`clickable`
4. 用元素的 `id` 执行动作，例如 `agent-android tap n12` 或 `agent-android tap text=登录`
5. **再执行一次 snapshot** 确认界面真的变了，没变就重试或换定位方式
6. 出问题时 `agent-android logcat --level E` 和 `agent-android screenshot -o shot.png` 交叉验证
7. 修完代码 → 重新编译 → 回到第 2 步，形成闭环

## target 的四种写法

| 写法 | 含义 |
| --- | --- |
| `n12` | 最近一次 snapshot 里的元素 id（推荐，最稳定） |
| `text=登录` | 精确匹配 text 或 content-desc |
| `id=btn_login` | 匹配 resource-id 后缀 |
| `360,900` | 直接给坐标（兜底，不推荐） |

裸文本如 `登录` 会先精确匹配、再包含匹配；同名多处按从上到下、从左到右排序，
必要时用更精确的 `id=` 定位。

## 动作清单

| 命令 | 用途 |
| --- | --- |
| `snapshot [--compact]` | 看屏幕；`--compact` 体积减半，定位够用 |
| `tap <target>` / `text=确定@2` | 点击；`@N` 选同名元素第 N 个 |
| `long-press` / `double-tap` | 长按 / 双击 |
| `drag <起点> <x,y 或 target>` | 拖拽 |
| `scroll-to <target> --max 8` | 滚动查找（滚到界面切换或内容不变会自动停） |
| `wake` | 灭屏/锁屏时先点亮，否则 snapshot 只有锁屏界面 |
| `crashes` | 复现崩溃后第一时间看，不用翻几百行 logcat |
| `run <flow.json>` | **批量回归**：把一串操作写成 JSON，一次跑完并出报告 |

`snapshot` 返回里的 `source` 字段说明这次用的是哪条通道：

- `uiautomator`：默认通道
- `accessibility`：界面持续动画、uiautomator 等不到静止时**自动降级**到伴侣 App，
  节点结构完全一致，可以照常 `tap` / `input`
| `input <text> --target <id>` | ASCII 输入；中文走 `--bridge input` |

## 关键约束（踩过的坑，务必遵守）

- **每次动作后必须重新 snapshot**。`uiautomator dump` 偶尔返回几十秒前的旧层级，
  拿旧坐标点下去会打到完全无关的界面。涉及关键判断时以 `screenshot` 为准。
- **Compose 的文字节点通常不可点击**，真正响应点击的是父容器。本工具会自动
  向上找最近的可点击祖先，不需要手动处理。
- **`input` 不支持中文**（Android 原生限制）。用
  `agent-android --bridge input "中文"` 转发给 `android/` 伴侣 App（需开启无障碍），
  或安装 ADBKeyBoard。
- **连不上模拟器先 `connect`**：`agent-android connect` 会自动扫描 MuMu / 雷电 /
  夜神 / 蓝叠 / WSA 等常见端口；`devices` 会显示识别到的模拟器名称。
- **同一台设备会有多个串号**：MuMu 同时暴露 16384 / 7555 / emulator-5554，
  工具用 `android_id` 判定为同一台并自动选一个，不需要手动 `--device`。
- **密码框默认打码**（`••••••` + `masked: true`）：不要试图读取、还原或绕过，
  也不要用 `AGENT_ANDROID_UNMASK` 去拿明文。需要填密码时直接 `input`，
  不需要先知道里面是什么。
- **`scroll-to` 自带停手保护**：返回 `not_scrollable` 说明到底了或界面不可滚；
  返回 `ui_changed_while_scrolling` 说明滚出了当前 App，先 `launch` 回去再试。
- **键盘会遮住下半屏，但不在 UI 树里**：`snapshot` 返回 `ime_visible: true` 时，
  先 `key back` 收起键盘，再去点下半部分的元素。
- **点完界面没变化先别连点**：可能是坐标过期（重新 snapshot）、键盘遮挡、
  或元素被悬浮窗拦截。连续点只会制造误触。
- **多设备必须显式指定** `--device <serial>`，工具不会擅自挑一台，避免误操作真机。
- `tap` 偶发不投递，发现没反应时先重拍一次 snapshot 再重试，别连点。
- HTTP 模式的 `serve` 默认只监听 127.0.0.1，**等价于远程控制手机，绝不要暴露到公网**。

## 环境是空白的（没有模拟器，也没有真机）

不要猜、不要让用户自己去找教程，直接执行：

```bash
agent-android setup --markdown
```

把返回的 `markdown` 原样转述给用户。它会先判断处境，再给唯一推荐路径：

| situation | 你该怎么说 |
| --- | --- |
| `no_device` | Windows 首选 MuMu 12（ADB 默认开启，装完启动即用）；备选雷电 9、Android Studio AVD |
| `emulator_installed_not_running` | 「你装了 MuMu，先启动它，然后我重新连」 |
| `emulator_starting` | 「模拟器在启动，等 5 秒我再试」——不要重复 connect |
| `usb_unauthorized` | 「手机上有个『允许 USB 调试』弹窗，请勾选一律允许」——**不要**让用户重装驱动 |
| `usb_offline` | 「换根数据线 / 换个 USB 口，USB 用途选文件传输」 |
| `emulator_offline` | 「跑 `agent-android recover`」——**不要**让用户拔数据线（那是模拟器不是手机） |

真机接入四步（转述时按顺序说）：开发者选项（连点 7 次版本号）→ 打开 USB 调试 →
数据线选「文件传输」→ 手机上点「允许」。

## MCP 模式

客户端支持 MCP（Claude Desktop / Cursor / Continue）时，不用跑命令：

```bash
agent-android mcp      # stdio MCP Server
```

工具名统一是 `android_*`，参数与 CLI 完全对齐；`android_screenshot` 直接返回图片，
`android_snapshot` 返回结构化节点表。出错时 `isError: true`，正文仍是带
`error_code` / `actions` 的 JSON。

## 批量回归

验证「改完代码有没有搞坏」时，不要一步步手敲，写成 JSON 一次跑完：

```bash
agent-android run flow.json --report report.json
```

```json
{"steps": [
  {"launch": "com.demo.app"},
  {"tap": "text=登录"},
  {"input": {"text": "hello", "target": "text=用户名"}},
  {"assert": {"text": ["欢迎"], "package": "com.demo.app"}},
  {"screenshot": "home.png"}
]}
```

步骤支持 `launch / tap / input / swipe / key / scroll_to / wait / assert /
screenshot / snapshot`。`assert` 一次抓屏校验多个条件；失败默认立即停止，
`--continue-on-error` 跑完全部。报告里每步都有 `ok / cost_s / detail`。

## 故障自救（按顺序试，不要反复重试同一条）

**先读错误 JSON 里的 `error_code` / `retryable` / `actions`，不要匹配 `error` 文案。**
`actions` 是可以直接执行的有序命令列表。

| error_code | 含义 | 该怎么办 |
| --- | --- | --- |
| `device_offline` | 没有在线设备 | `agent-android recover`；仍失败再 `connect` |
| `multiple_devices` | 多台设备 | `devices` 看列表，再用 `--device <serial>` |
| `uiautomator_unavailable` | dump 失败（模拟器裁掉了，或动画卡住） | 关动画重试；仍失败改 `--bridge snapshot` |
| `snapshot_missing` | 按 id 操作但没有快照 | 先 `snapshot`，再重跑原命令 |
| `element_not_found` | 界面已经变了 | 重新 `snapshot`；或改用 `text=` / `id=` |
| `element_invisible` | 元素无可见区域（离屏/动画中） | 等 300ms 后重新 `snapshot` |
| `input_non_ascii` | 中文输入 | 改 `--bridge input "..."`（需伴侣 App） |
| `element_index_out_of_range` | `@N` 超出匹配数量 | 去掉 `@N`，先 `snapshot` 看列表 |
| `not_scrollable` | 滚不动了（到底或界面不可滚） | 换反向，或确认元素在哪个 App 里 |
| `adb_server_conflict` | 5037 被别的程序占用 | `adb kill-server`；或换 `ADB_SERVER_PORT=5038` |
| `adb_timeout` | adb 命令超时 | `recover` 后重试 |
| `adb_not_found` | 本机没有 adb | 装 platform-tools 并设 `ANDROID_HOME` |
| `invalid_argument` | **参数写错了** | **不要重试**，按提示改参数 |
| `install_failed` | 装包失败 | 看 apk 路径 / 签名冲突 / 存储空间 |
| `screenshot_failed` | 截图拿不到有效 PNG | `wake` 后重试 |
| `bridge_unreachable` | 伴侣 App 连不上 | 会自动重开一次；仍失败看 `doctor` |
| `bridge_token_missing` | 拿不到令牌 | 重启 App（会自动读 token 文件） |
| `ui_xml_invalid` | 层级 XML 坏了 | 重试一次，或 `--bridge snapshot` |
| `snapshot_corrupt` | 本地缓存损坏 | 重新 `snapshot` 覆盖 |
| `launch_no_entry` / `launch_failed` | 启动失败 | 确认包名，或用 `am start -n` |

**通用降级顺序**（当前一步失败时）：

1. 原地重试一次（仅当 `retryable: true`）
2. `agent-android recover`
3. 换后端：`--bridge`（走无障碍服务，不依赖 uiautomator）
4. 降级到「只看」：`screenshot` 确认界面，再决定下一步
5. 仍然失败：把 `doctor` 的完整 JSON 交给用户，不要编造原因

**永远不要**：连续快速点同一个元素（坐标可能已过期，会误触）；
在没有重新 `snapshot` 的情况下复用旧 id；拿 `error` 文案当分支条件。

## 完整示例

```bash
agent-android health
agent-android install app/build/outputs/apk/debug/app-debug.apk
agent-android launch com.coco.demo
agent-android snapshot
agent-android tap n7
agent-android snapshot
agent-android input TestHomework01 --target n5 --clear
agent-android key back
agent-android logcat --level E
```

## HTTP 模式（给不能跑终端的 Agent）

```bash
agent-android serve --port 8377
curl http://127.0.0.1:8377/snapshot
curl -X POST http://127.0.0.1:8377/action \
     -H 'Content-Type: application/json' \
     -d '{"action":"tap","target":"text=登录"}'
```
