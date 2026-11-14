# agent-android

> **给 AI 编程助手装上一双眼睛和一双手，让它自己操作 Android 手机 / 模拟器。**

[简体中文](README.md) | [English](README.en.md)

[![CI](https://github.com/DanTagege/agent-android/actions/workflows/ci.yml/badge.svg)](https://github.com/DanTagege/agent-android/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Android-3ddc84.svg)](https://www.android.com/)

手机是最后一个还没被 AI 编程助手真正接管的开发环境：`adb` 只给你一张截图或者
一坨 XML，模型只能靠猜坐标。`agent-android` 把屏幕翻译成一张带标签的元素表，
让 Agent 像读网页 DOM 一样读手机，然后用一条命令完成点击、输入、滑动、装包、
抓日志，形成「看 → 动 → 再看」的闭环。

它不依赖 Appium，不引入第三方库，也不会把设备控制权暴露到网络上。

改完 Android 代码，你可以直接对 Agent 说：

> 装上新 APK，打开 App，点登录，看看有没有崩，把 logcat 里的错误贴给我。

它会自己跑完，不用你手动点模拟器、复制报错。

## 30 秒上手

```bash
git clone https://github.com/DanTagege/agent-android.git
cd agent-android

python -m agent_android setup     # 没设备？它告诉你是接真机还是装模拟器
python -m agent_android doctor    # 8 项环境体检
python -m agent_android snapshot  # 看屏幕：每个元素带 id、文字、坐标
python -m agent_android tap text=设置
```

零第三方依赖，有 Python 3.9+ 就能跑；不需要 Appium，也不需要客户端库。

## 目录

- [30 秒上手](#30-秒上手)
- [它怎么工作](#它怎么工作)
- [快速开始（3 分钟）](#快速开始3-分钟)
- [命令一览](#命令一览)
- [MCP 接入](#mcp-接入claude-desktop--cursor)
- [批量回归](#批量回归流程-runner)
- [出错怎么办](#出错怎么办自愈协议)
- [已知限制](#已知限制)
- [项目综述](PROJECT_OVERVIEW.md)（架构、实测数据、边界）

---

## 它怎么工作

```
   AI 编程 Agent (Claude Code / Codex / Cursor / ...)
        │  自然语言意图
        ▼
   agent-android CLI            ← 本仓库主体，零第三方依赖
        │  adb shell / uiautomator dump
        ▼
   Android 手机 / MuMu 模拟器
        ▲
        └── 结构化 JSON 回流：元素 id、文字、坐标、可点击性、日志
```

关键不是「包一层 adb」，而是**翻译**：

| adb 原始输出 | agent-android 输出 |
| --- | --- |
| 一张 PNG，Agent 只能猜按钮在哪 | 每个控件都有 `id` / `text` / `bounds` / `clickable` |
| 一坨 XML，人看的 | 扁平节点表，`tap n12` 即可引用 |
| 滚动的 logcat 文本 | `{"lines": [...]}` 数组，可直接解析 |

所有命令的 **stdout 只有一个 JSON 对象**，诊断信息走 stderr，Agent 可以
`json.loads(result.stdout)`，不用做脆弱的文本解析。

---

## 两种模式

| | CLI 模式（默认） | Companion 模式（可选） |
| --- | --- | --- |
| 装什么 | 电脑上一个 Python 包 | 手机里一个 App（`android/` 子工程） |
| 依赖 | Python 3.9+ + Android SDK platform-tools | JDK 17 + Android SDK 34 + 无障碍权限 |
| 中文输入 | ❌ `adb input text` 不支持中文 | ✅ `ACTION_SET_TEXT` |
| UI 层级 | `uiautomator dump`（偶尔拿到过期窗口） | 实时窗口树 |
| 适合 | 快速上手、CI、绝大多数自动化 | 需要中文输入 / 更可靠层级的场景 |

**只想要 CLI？直接用仓库根目录就行，`android/` 整个删掉也不影响。**

---

## 模拟器适配

所有模拟器都实现标准 adb 协议，所以**协议层天然通用**——雷电、夜神、蓝叠、WSA、Genymotion 不需要任何额外适配。真正的差异只有三处：ADB 开关、监听端口、以及有没有裁掉 `uiautomator`。

| 模拟器 | 连接地址 | 备注 |
| --- | --- | --- |
| MuMu 12 | `127.0.0.1:16384` | 本仓库主测环境；旧版 MuMu 6 是 7555 |
| 雷电 LDPlayer 9/14 | `127.0.0.1:5555` | 多开 `5555 + index*2`（5557 / 5559…） |
| 夜神 Nox | `127.0.0.1:62001` | 多开依次 62025、62026… |
| 逍遥 MEmu | `127.0.0.1:21503` | |
| 蓝叠 BlueStacks 5 | `127.0.0.1:5555`（以设置页为准） | **ADB 默认关闭**，需在 设置 > 高级 打开 |
| 天天模拟器 | `127.0.0.1:6555` | |
| 海马玩 | `127.0.0.1:53001` | |
| WSA（Win11 安卓子系统） | `127.0.0.1:58526` | 微软已停止支持，仅遗留环境可用 |
| Genymotion | `192.168.x.x:5555` | 桌面版用虚拟机网络地址，SaaS 用远程 IP |
| Android Studio AVD | `emulator-5554` 自动出现 | 不需要 connect |

不想记端口就用工具自动扫：

```bash
agent-android connect --list      # 打印上面这张表
agent-android connect             # 扫描本机监听中的端口并逐个连接
agent-android connect 127.0.0.1:62001
```

`devices` 和 `health` 会顺便告诉你连上的是哪家模拟器。

**唯一可能不兼容的情况**：部分精简版/游戏版模拟器裁掉了 `uiautomator`，`snapshot` 会失败。
这时改用 `android/` 伴侣 App —— 它走无障碍服务读实时窗口树，不依赖 `uiautomator`，
对这类模拟器反而更稳。`health` 会检测并明确提示这一点。

---

## 出错怎么办（自愈协议）

与其枚举所有机型/模拟器/系统版本，不如让 Agent 有能力**自己恢复**。
所以每个错误都带三个机器可用字段：

```json
{
  "ok": false,
  "error": "没有在线的 Android 设备",
  "error_code": "device_offline",
  "retryable": true,
  "hint": "用 `agent-android connect` 自动扫描…",
  "actions": ["agent-android recover", "agent-android connect", "adb kill-server && adb connect 127.0.0.1:16384"]
}
```

Agent 拿到 `retryable` 就能判断该不该重试，拿到 `actions` 就能**按顺序执行**，
不需要理解背后的 adb 细节。

两个配套命令：

```bash
agent-android doctor     # 体检：adb、设备、uiautomator、屏幕方向、缓存、伴侣 App
agent-android recover    # 自动修复：重启 adb → 重连模拟器 → 清缓存
agent-android recover --fix-animations   # 顺带关动画，提高 dump 成功率
```

`doctor` 会主动识别本机有没有**多个不同版本的 adb**（模拟器自带 adb 与
platform-tools 混用是掉线最常见的原因），并把可执行命令汇总到 `next_actions`。
共 8 项检查，其中 `resources` 专门看电量和存储余量——存储耗尽时
`uiautomator dump` 会静默失败，看起来却像 uiautomator 坏了。

### 一台设备都没有时

`agent-android setup` 会先判断你处于哪种处境，再给**唯一**推荐路径，
不让人在一堆选项里挑：

| situation | 含义 | 推荐动作 |
| --- | --- | --- |
| `no_device` | 没插真机，也没装模拟器 | 装 MuMu 12（Windows 首选）/ 雷电 9 / Android Studio AVD |
| `emulator_installed_not_running` | 装了但没启动 | 启动它，再 `connect` |
| `emulator_starting` | 进程在跑，ADB 未就绪 | 等几秒再 `connect` |
| `emulator_offline` | 模拟器串号 offline | 跑 `recover`，**不要拔线** |
| `usb_unauthorized` | 手机插了没授权 | 手机上点「允许 USB 调试」 |
| `usb_offline` | 真机 offline | 换线 / 换 USB 口 / 改 MTP 模式 |
| `ready` | 已有设备 | 直接干活 |

加 `--markdown` 拿到一段可直接贴给用户的中文说明：

```bash
agent-android setup --markdown
```

真机接入的标准步骤（`setup` 会按平台补上驱动提示）：

1. 设置 → 关于手机 → 连点 7 次「版本号」，打开开发者选项
2. 开发者选项 → 打开「USB 调试」
3. 数据线连电脑，USB 用途选「文件传输 / MTP」（**不要选「仅充电」**）
4. 手机弹窗「允许 USB 调试吗？」→ 勾选「一律允许」→ 允许
5. `adb devices` 显示 `<串号> device` 即成功

Android 11+ 也可以无线调试：开发者选项 → 无线调试 → 用配对码
`adb pair <ip>:<port>`，再 `adb connect <ip>:<port>`。

### 内置的降级链

| 失败点 | 自动/建议的下一步 |
| --- | --- |
| 没有在线设备 | 自动扫描常见模拟器端口并连接；再失败 → `recover` |
| `uiautomator dump` 失败 | 关动画重试 → 改用 `--bridge`（无障碍服务，不依赖 uiautomator） |
| 中文输入 | 自动报 `input_non_ascii`，`actions` 直接给出 `--bridge input` 命令 |
| 元素找不到 / 已离屏 | 返回 `element_not_found` + 「重新 snapshot」动作 |
| 多个串号 | 用 `android_id` 判断是否同一台（MuMu 会暴露 3 个端口指向同一设备），否则要求 `--device` |
| 模拟器 adb 端口残留（offline） | 返回 `emulator_offline` + `recover`，不会让你去拔线 |
| `uiautomator` 等不到静止（界面持续动画） | **自动降级**到伴侣 App 的无障碍服务，`snapshot.source` 变成 `accessibility` |
| adb 串号失效（server 重启/拔插） | 底层自动重连并换当前在线串号，重试一次，命令无感恢复 |
| 伴侣 App 掉线（无障碍被系统回收 / 进程被杀） | 自动重写 secure settings 并拉起 App，刷新令牌后重试一次 |
| 参数写错（如漏传 target） | 返回 `invalid_argument` + `retryable: false`，明确告诉你「别重试，改参数」 |

---

## 快速开始（3 分钟）

### 1. 准备环境

- Python 3.9 及以上（零第三方依赖，clone 下来就能跑）
- Android SDK 的 `platform-tools`（里面有 `adb`）
- 一台模拟器或真机。不想记端口就用自动扫描：

```bash
agent-android connect           # 扫描本机监听中的常见模拟器端口并连接
agent-android devices           # 确认已连上，并显示识别到的模拟器名称
```

已知端口也可以手动连（完整表格见下方「模拟器适配」）：

```bash
adb connect 127.0.0.1:16384     # MuMu 12
adb connect 127.0.0.1:5555      # 雷电实例 0
adb connect 127.0.0.1:62001     # 夜神首实例
```

如果 `adb` 不在 PATH，二选一：

```bash
export ANDROID_HOME=/path/to/Android/sdk        # 推荐
export AGENT_ANDROID_ADB=/path/to/sdk/platform-tools/adb
```

### 2. 安装 CLI

```bash
git clone https://github.com/DanTagege/agent-android.git
cd agent-android
pip install -e .          # 或者直接用 python -m agent_android，免安装
```

### 3. 自检并抓第一张屏幕

```bash
agent-android health
agent-android --pretty snapshot
```

返回大致长这样：

```json
{
  "ok": true,
  "device": "127.0.0.1:16384",
  "screen": [1080, 1920],
  "package": "com.coco.demo",
  "node_count": 4,
  "nodes": [
    { "id": "n3", "text": "登录", "resource_id": "com.coco.demo:id/btn_login",
      "class": "Button", "bounds": [100, 820, 980, 930],
      "center": [540, 875], "clickable": true, "editable": false }
  ],
  "usage": "用 element.id 作为 tap/input 的 target；每次操作后重新 snapshot 确认结果"
}
```

### 4. 让 Agent 动手

```bash
agent-android install app/build/outputs/apk/debug/app-debug.apk
agent-android launch com.coco.demo
agent-android snapshot                  # 看
agent-android tap text=登录              # 动
agent-android snapshot                  # 再看，确认界面变了
agent-android logcat --level E          # 出错时捞日志
agent-android screenshot -o shot.png    # 交叉验证（截图才是界面真相）
```

> **重要**：`uiautomator dump` 偶尔会返回几十秒前的旧层级。
> 所以「每步之后重新 snapshot」不是建议，是必须遵守的规则；
> 涉及关键判断时以 `screenshot` 为准。

---

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `devices` | 列出已连接设备，并识别是哪家模拟器 |
| `health` | 检查 adb / 设备 / uiautomator 是否可用 |
| `connect [地址] [--list]` | 连接模拟器：不给地址时自动扫描常见端口 |
| `doctor` | 完整体检（8 项），输出可执行的修复命令 |
| `setup [--markdown]` | 没有设备时：判断处境，给出接真机 / 装模拟器的步骤 |
| `recover [--fix-animations]` | 自动重启 adb、重连模拟器、清缓存 |
| `snapshot [--full] [--compact] [--prefer]` | **核心**：抓取 UI 元素表；默认先走无障碍通道（快一倍），`--compact` 省一半 token |
| `notify list\|tap\|shade\|collapse` | 通知栏：读可见通知 / 点第 N 条 / 下拉 / 收起 |
| `--lock N`（所有命令通用） | 多 Agent 共用一台设备时排队拿锁，避免互相打断 |
| `tap <target>` | 点击：`n12` / `text=登录` / `id=btn_login` / `360,900` |
| `tap text=确定@2` | 同名元素选第 2 个（从 1 开始） |
| `long-press <target>` | 长按 |
| `double-tap <target>` | 双击 |
| `drag <起点> <x,y 或目标>` | 拖拽，失败自动退回长 swipe |
| `scroll-to <target> [--max 8]` | 滚动查找，滚到界面切走/内容不变会主动停手 |
| `wake` | 点亮屏幕（灭屏/锁屏时先执行） |
| `input <text> [--target n5] [--clear]` | 输入文字（仅 ASCII，见下方限制） |
| `swipe up\|down\|left\|right` | 滑动，也支持 `'100,800 100,200'` |
| `key back\|home\|enter\|recents` | 系统按键 |
| `install <apk>` | 安装 APK（自动 `-r` 覆盖） |
| `launch <package>` | 按包名启动应用 |
| `screenshot -o shot.png` | 截图存到本机 |
| `logcat --lines 50 --level E` | 读取最近日志 |
| `crashes [--limit 3]` | 只取崩溃记录（进程 / 异常 / 调用栈），不用翻几百行 |
| `run <flow.json> [--report out.json]` | **批量回归**：按 JSON 流程执行，含断言与逐步报告 |
| `mcp` | 以 MCP Server 运行（stdio），供 Claude Desktop / Cursor 接入 |
| `serve --port 8787` | 启动本地 HTTP 服务，给不能跑终端的 Agent 用（与设备端 8377 错开） |

全局选项：`--device <serial>`（多设备时必填）、`--pretty`（缩进输出）、
`--bridge`（改走设备端伴侣 App）、`--token`（伴侣 App 访问令牌）。
也可以用环境变量 `ANDROID_SERIAL` 指定设备。这些选项写在子命令前后都可以。

### target 的四种写法

| 写法 | 含义 |
| --- | --- |
| `n12` | 最近一次 snapshot 里的元素 id（**最稳定，推荐**） |
| `text=登录` | 精确匹配 text 或 content-desc |
| `id=btn_login` | 匹配 resource-id 后缀 |
| `360,900` | 直接给坐标（兜底，不推荐） |

裸文本（`登录`）会先精确匹配、再包含匹配；同名多处按从上到下、从左到右排序。
**Compose 的文字节点通常不可点击**，工具会自动向上找最近的可点击祖先，不用手动处理。

默认会过滤掉没有文字的纯布局容器，所以 `snapshot` 里的 id **可能不连续**（n3、n7…），
这是正常的：编号始终按完整层级分配，保证你引用的永远是同一个控件，不会因为过滤而漂移。

---

## 让你的 Agent 会用它

### 方式一：装技能包（推荐）

仓库里带了标准的 `SKILL.md`：

```bash
mkdir -p .claude/skills        # Claude Code
cp -r skills/agent-android .claude/skills/
# 或 Codex / Cursor 对应的 skills 目录
# 或：npx skills add DanTagege/agent-android
```

它写清了「何时用、标准工作流、四种 target 写法、六个必踩的坑」，
Agent 读完就知道该按「看 → 动 → 再看」循环操作。

### 方式二：在 `CLAUDE.md` / `AGENTS.md` 里加一段

```markdown
操作 Android 设备时使用 agent-android：
先 `agent-android health`，再 `snapshot` 拿到元素 id，
用 `tap n12` 执行动作，每次动作后重新 snapshot 验证，
失败时 `logcat --level E` 和 `screenshot` 交叉排查。
`input` 不支持中文，多设备必须传 --device。
```

### 方式三：HTTP 模式

```bash
agent-android serve --port 8787
curl http://127.0.0.1:8787/snapshot
curl -X POST http://127.0.0.1:8787/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"tap","target":"text=登录"}'
```

接口：`GET /health`、`/snapshot`、`/logs`、`/screenshot`，`POST /action`。
可设置 `AGENT_ANDROID_TOKEN`，请求需带同名 `X-Agent-Token` 头。

---

## 设备端伴侣 App（可选，`android/`）

一个完整的 Kotlin + Jetpack Compose 工程。开启无障碍服务后，它在手机本地
`127.0.0.1:8377` 提供与 CLI 同款的 JSON 接口，并补上两个 CLI 做不到的能力：
**中文输入**和**实时 UI 层级**。

### 编译

```bash
cd android
./gradlew assembleDebug
# 产物：app/build/outputs/apk/debug/app-debug.apk
```

已验证的版本组合：Gradle 8.2.1 · AGP 8.2.1 · Kotlin 1.9.24 ·
Compose Compiler 1.5.14 · Compose BOM 2024.06.00 · compileSdk 34 · JDK 17。
国内网络下 `settings.gradle.kts` 已把 aliyun 镜像排在 `google()` 前面。

Windows 本机也可用 `scripts/build-android.sh` 一键编译（自动设置
`JAVA_HOME` / `ANDROID_HOME` / 临时目录，绕开常见的沙箱构建坑）。

### 启用

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
# 打开 App → 点「开启无障碍服务」→ 找到 Agent Bridge → 打开
adb forward tcp:8377 tcp:8377

# 端口等价于远程控制手机，所以必须带令牌（App 界面上那行 token）
export TOKEN=<App 界面显示的 6 位令牌>
curl -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8377/snapshot
curl -X POST http://127.0.0.1:8377/action \
  -H "X-Agent-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"input","target":"n5","text":"张三"}'
```

CLI 可以直接转发过去，不用手写 curl（会自动 `adb forward` 并带令牌）：

```bash
agent-android --bridge --token "$TOKEN" input "张三"
agent-android --bridge --token "$TOKEN" tap text=登录
```

`/health` 是唯一免令牌的接口，用于探活。

**令牌不用手抄**：App 启动时会把 `TOKEN=xxxxxx` 打到 logcat，
`agent-android --bridge` 会自动读取。App 界面上也有一个「伴侣能力自测」输入框，
可以直接验证中文输入是否真的通了。

`/action` 支持 `tap` / `long_press` / `double_tap` / `drag` / `scroll_to` /
`input`（中文可用）/ `swipe` / `key` / `wake` / `snapshot` / `crashes`。
`input` 走 `ACTION_SET_TEXT`，天然覆盖旧内容，所以 `clear` 字段只是回显。

动作支持 `tap` / `long_press` / `input` / `swipe` / `key` / `snapshot`，
字段名与 CLI 完全一致，Agent 无需改代码即可切换后端。

---

## Windows PowerShell 用户

上面的示例是 bash 语法，PowerShell 下等价写法：

```powershell
$env:AGENT_ANDROID_ADB = "D:\Program Files\Netease\MuMu\nx_main\adb.exe"
$env:ANDROID_SERIAL   = "127.0.0.1:16384"

python -m agent_android setup
python -m agent_android devices
python -m agent_android snapshot --pretty | Out-File -Encoding utf8 snap.json
```

四个容易踩的点：

- 环境变量是 `$env:NAME = "value"`，不是 `export`
- 命令串联用 `;`，PowerShell 5.1 **不支持 `&&`**（7.x 才支持）
- JSON 含中文时不要用 `Invoke-WebRequest` 直接管道，先 `Out-File -Encoding utf8` 再读
- `curl.exe` 比 `curl` 别名可靠（PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名）

## 已知限制

- **`input` 不支持中文**：Android 原生 `input text` 的限制。用 `agent-android --bridge input "中文"`
  转发给 Companion App（需安装 APK 并开启无障碍），或安装 ADBKeyBoard。
- **`input` 不接受换行/制表符**：`adb shell` 会把换行当命令分隔符（有注入风险），工具会直接拒绝，请按行拆分。
- **UI 层级可能过期**：`uiautomator dump` 在窗口动画中可能返回旧界面，务必每步重新 snapshot，关键判断用 screenshot。
- **多设备必须显式指定**：工具不会擅自挑一台，避免误操作真机。
- **`tap` 偶发不投递**：发现没反应先重拍 snapshot 再重试，不要连点。
- **无障碍服务需用户手动开启**：Android 不允许应用自行打开该开关。
- **部分模拟器裁掉了 `uiautomator`**：`snapshot` 会失败，改用 Companion 模式；`health` 会提示。
- **模拟器 ADB 默认可能没开**：BlueStacks 必须在设置里手动启用，否则端口根本不监听。
- **端口会撞车**：同时开雷电 9 和雷电 14 的实例 0 会抢 5555；CLI 的 `serve` 默认 8787，刻意与设备端 8377 错开。
- **密码框默认打码**：`password="true"` 的节点，`text` 会被替换成 `••••••` 并带 `masked: true`。
  这些内容会进模型上下文、磁盘缓存和日志，明文出去就收不回。调试时可用
  `AGENT_ANDROID_UNMASK=1` 临时关掉，但不要在生产流程里用。
- **双击/拖拽是模拟手势**：`double-tap` 用两次 80ms 间隔的 tap；`drag` 优先
  `input draganddrop`，个别设备不支持时退回长时长 swipe。
- **键盘不在 UI 树里**：输入法弹出时 `snapshot` 会返回 `ime_visible: true` 并提示，
  但键盘窗口本身不会被 dump，点下半屏前先 `key back` 收起。
- **App 崩溃会被识别**：`snapshot` 发现界面回到桌面会提示，`crashes` 直接给
  进程 + 异常 + 调用栈。

## 不支持什么

明确不做的事，避免白花时间：

| 场景 | 原因 | 替代方案 |
| --- | --- | --- |
| **iOS / iPhone** | 只实现 Android 的 adb 与无障碍通道 | [idb](https://github.com/facebook/idb)、[Maestro](https://maestro.mobile.dev) |
| 验证码 / 滑块 | 合规边界，不提供自动破解 | 脚本跑到这里停下，交给人 |
| 锁屏密码 | 不尝试绕过系统安全 | `wake` 只点亮屏幕，解锁交给用户 |
| 游戏 / Unity 等自绘界面 | 拿不到控件树 | `screenshot` + 坐标点击；`snapshot` 会返回 `likely_custom_drawn: true` 提示 |
| 分屏另一侧的界面 | 只读聚焦窗口 | 先切到目标窗口再操作；`snapshot` 会返回 `multi_window: true` |
| 同一台设备的并发操作 | 两个 Agent 会互相刷走界面 | 加 `--lock 30` 排队（文件锁） |

## 哪些命令会改设备

默认只读，但下面这些会真的改动状态，生产设备上请留意：

| 命令 | 改了什么 |
| --- | --- |
| `tap` / `long-press` / `double-tap` / `drag` / `swipe` / `input` / `key` | 界面状态 |
| `install` | 安装或覆盖 APK |
| `launch` | 启动应用并切到前台 |
| `recover` | 重启 adb、重连模拟器、删本地快照缓存、**重写无障碍设置并拉起伴侣 App** |
| `--bridge` 首次调用 | 若无障碍服务未启用，会写 `settings put secure enabled_accessibility_services` |
| `--lock` | 在 `~/.agent_android/` 下创建/删除锁文件 |

## 在 CI 里跑

没有真机也能跑。仓库里的 `.github/workflows/e2e.yml` 是完整模板，核心是用官方
模拟器 runner 拉起一个 AVD：

```yaml
- uses: reactivecircus/android-emulator-runner@v2
  with:
    api-level: 33
    target: google_apis
    script: |
      python -m agent_android doctor
      python -m agent_android run examples/settings-info.json
```

用云真机则更简单：把厂商给的 adb 连接串喂给 `--device`。

```bash
adb connect <云真机地址>
agent-android --device <地址> snapshot
```

CI 里没有设备时，`doctor` 会明确输出「无在线设备」并给出两条接入建议，
而不是抛一堆 adb 报错。

## 安全说明

⚠️ 这个工具等价于把手机控制权交给本机进程。

- CLI 的 `serve` 和 Companion App 都**只监听 127.0.0.1**，请不要改成 `0.0.0.0` 或映射到公网。
- 不要把 `local.properties`、`.env`、截图、APK 提交到仓库（已在 `.gitignore` 中）。
- 本项目只做界面观察与操作，不包含任何绕过登录、验证码或平台风控的能力。

---

## 目录结构

```
agent-android/
├── agent_android/            # PC 端 CLI（零第三方依赖）
│   ├── cli.py                # 命令入口，统一 JSON 输出
│   ├── adb.py                # adb 封装：设备发现 / shell / 自检 / connect
│   ├── emulators.py          # 模拟器端口表与自动发现
│   ├── snapshot.py           # 核心：UI XML → 结构化元素表
│   ├── actions.py            # 点击 / 输入 / 滑动 / 装包 / 日志
│   ├── bridge.py             # 转发到设备端伴侣 App（中文输入）
│   ├── diagnostics.py        # doctor / recover：诊断与自动修复
│   └── server.py             # 可选本地 HTTP 服务
├── skills/agent-android/
│   └── SKILL.md              # 给 coding agent 的说明书
├── tests/                    # 11 个纯逻辑单测，无需真机
├── android/                  # 可选：设备端伴侣 App（完整 Gradle 工程）
│   └── app/src/main/         # Manifest / Compose UI / 无障碍服务 / HTTP 桥
├── scripts/build-android.sh  # Windows 本机一键编译脚本
├── .github/workflows/ci.yml  # CLI 单测 + Android 编译
├── pyproject.toml
├── LICENSE
└── README.md
```

> 备注：本仓库在本机被放在 `D:\agent-andorid` 目录（拼写笔误）。
> 目录名不影响任何功能；推到 GitHub 时建议仓库名用正确的 **`agent-android`**。

## 路线图

- [ ] `element` diff：只返回两次 snapshot 之间的变化，进一步省 token
- [ ] MCP Server 模式，直接被 Claude Desktop / Cursor 挂载
- [ ] 录制与回放：把一次人工操作导出成可复用的 agent 脚本
- [ ] Companion 端截图与 `uiautomator` 结果自动交叉校验

## MCP 接入（Claude Desktop / Cursor）

```bash
agent-android mcp
```

启动一个 stdio MCP Server，把能力暴露成 12 个 tools。客户端配置（Claude Desktop
的 `claude_desktop_config.json`、Cursor 的 MCP 设置都吃这个格式，完整示例见
`examples/mcp-config.json`）：

```json
{
  "mcpServers": {
    "agent-android": {
      "command": "python",
      "args": ["-m", "agent_android", "mcp"],
      "env": { "AGENT_ANDROID_ADB": "<你的 adb 路径>" }
    }
  }
}
```

| tool | 能力 |
| --- | --- |
| `android_snapshot` | 读当前屏幕的结构化元素表 |
| `android_tap` / `android_input` / `android_swipe` / `android_key` | 操作 |
| `android_scroll_to` | 滚动查找 |
| `android_launch` | 按包名启动 |
| `android_screenshot` | 返回图片（image content），模型可以直接看 |
| `android_crashes` | 崩溃记录 |
| `android_devices` / `android_doctor` | 设备与环境 |
| `android_run_flow` | 跑批量流程 |

设计约定：

- `stdout` 只有协议消息，日志全走 `stderr`——否则客户端会解析失败直接断连；
- 工具出错统一 `isError: true`，正文里带 `error_code` / `retryable` / `actions`；
- 必填参数在「解析设备」之前校验，避免「你忘了传 target」被报成「找不到 adb」。

## 批量回归（流程 runner）

把一组操作写成 JSON，一条命令跑完并给出逐步报告：

```json
{
  "name": "打开设置并读取设备信息",
  "on_failure": "stop",
  "steps": [
    {"launch": "com.android.settings"},
    {"wait": 2},
    {"assert": {"package": "com.android.settings", "node_count_min": 5}},
    {"scroll_to": "关于手机"},
    {"tap": "关于手机"},
    {"assert": {"text": ["型号"]}},
    {"screenshot": "about.png"}
  ]
}
```

```bash
agent-android run examples/settings-info.json --report report.json
```

支持的步骤：`launch` / `tap` / `input` / `swipe` / `key` / `scroll_to` /
`wait` / `assert` / `screenshot` / `snapshot`。

- `assert` 一次抓屏同时校验多个条件：`text` 包含、`package` 相等、`node_count_min`；
- 某步失败默认**立即停止**，加 `--continue-on-error` 跑完全部再汇总；
- 报告里每步都有 `ok` / `cost_s` / `detail`，失败步骤带 `error_code` 和 `actions`。

实测：`examples/settings-info.json` **10/10 步通过，24.8 秒**。

`examples/` 下备了 10 个可直接跑的流程：

| 文件 | 内容 | 可移植性 |
| --- | --- | --- |
| `settings-info.json` | 打开设置读取设备信息 | 通用 |
| `app-launch.json` | 启动应用并确认前台 | 换包名即可 |
| `browser-search.json` | 浏览器搜索「金融」 | 地址栏坐标按分辨率调 |
| `permission-dialog.json` | 处理定位权限弹窗 | 模板 |
| `wifi-toggle.json` | 进入网络设置 | 文案随 ROM |
| `scroll-list.json` | 长列表滚动查找 | 通用 |
| `crash-recovery.json` | 崩溃后重启恢复 | 换包名即可 |
| `screenshot-walk.json` | 桌面三屏截图 | 通用 |
| `multi-app-switch.json` | 多 App 来回切换 | 换包名即可 |
| `text-input.json` | 中文输入并回读 | 需伴侣 App，坐标按分辨率调 |

## 真机冒烟测试

单元测试不碰设备，所以另有一个端到端脚本，在真实手机/模拟器上跑完整闭环：

```bash
python scripts/e2e_smoke.py                 # 完整 12 步（含中文输入）
python scripts/e2e_smoke.py --skip-input    # 没装伴侣 App 时跳过中文输入
```

它验证的不是「命令能跑」，而是 Agent 真正依赖的能力：
定位元素 → 点击 → 界面确实变了 → 能读回新界面。12 步覆盖：

体检 → 伴侣 App 就绪 → 唤醒 → 启动设置 → **按文本点击** → 返回 →
**滚动查找** → 进子页读信息 → 截图 → 回桌面 → 崩溃检测 → **中文输入**。

退出码 0 表示全过。任何一步失败会打印具体原因和耗时。

## 测试

```bash
python -m unittest discover -s tests -v     # 无需连接真机
cd android && ./gradlew assembleDebug       # 需要 JDK 17 + Android SDK 34
```

## License

[MIT](LICENSE)
