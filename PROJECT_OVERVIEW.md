# agent-android 项目综述

> 让 AI Agent 真正「看得见、点得动、出错能自愈」地操作 Android 手机与模拟器。

---

## 一、项目名称与定位

| 项 | 内容 |
| --- | --- |
| 项目名 | **agent-android** |
| 版本 | 0.1.0（Alpha） |
| 形态 | 命令行工具 + 可选设备端伴侣 App + 可选本地 HTTP 服务 |
| 语言 | Python 3.9+（零第三方依赖）、Kotlin / Jetpack Compose |
| 平台 | Android（真机 / 模拟器），Windows / macOS / Linux 均可运行 CLI |
| 一句话 | 把 Android 屏幕翻译成 Agent 能读的 JSON，把 Agent 的意图翻译成可靠的手势 |

它的定位是「把手机变成 Agent 可读写的设备」：不做跨端遥控，只把 Android 这一块做透——
读得懂界面、点得准控件、出错能自己恢复。

---

## 二、解决什么问题

AI 编程助手要操作手机，天然面临四个断点：

| 断点 | 具体表现 | 本项目的解法 |
| --- | --- | --- |
| **看不见** | `adb` 只给 PNG 或一坨 XML，模型只能猜坐标 | 把 UI 层级解析成带 `id / 文本 / 坐标 / 可点击性` 的结构化元素表 |
| **点不准** | 文字节点不可点击、同名按钮多个、元素在屏幕外 | 自动向上找可点击祖先、`@N` 选第几个、`scroll-to` 滚动查找 |
| **输不进** | `adb input text` 不支持中文 | 伴侣 App 走无障碍 `ACTION_SET_TEXT`，CLI 一条 `--bridge` 自动转发 |
| **连不上 / 出错就卡死** | 模拟器端口不固定、adb 掉线、界面动画导致抓取失败 | 端口自动发现、串号失效自动重连、uiautomator 失败自动降级 |

核心判断：**不追求适配所有机型，而是让 Agent 自己具备恢复能力。**
每个错误都返回 `error_code / retryable / actions`，Agent 拿到就能分支处理。

---

## 三、整体架构

```
┌──────────────────────────────────────────────────────────┐
│  Agent（Claude Code / Codex / 任意能跑命令的助手）          │
└───────────────┬──────────────────────────────────────────┘
                │ 只看一个 JSON：{ok, nodes[], error_code, actions[]}
┌───────────────▼──────────────────────────────────────────┐
│  CLI 层  agent-android                                   │
│  snapshot / tap / input / swipe / scroll-to / crashes …  │
├──────────────────────────────────────────────────────────┤
│  能力层                                                  │
│  snapshot.py     UI XML → 结构化节点（核心）              │
│  actions.py      点击 / 输入 / 滑动 / 拖拽 / 崩溃检测      │
│  adb.py          adb 封装：设备发现、自动重连、屏幕尺寸     │
│  emulators.py    模拟器端口表与自动识别                    │
│  diagnostics.py  doctor / recover：诊断与自动修复          │
│  onboarding.py   setup：没有设备时教你怎么接入             │
│  bridge.py       伴侣 App 的 HTTP 客户端（中文输入 / 降级） │
│  server.py       可选本地 HTTP 服务，供非 Python 的 Agent  │
└───────────────┬──────────────────────────────────────────┘
                │ adb（USB / 网络）
┌───────────────▼──────────────────────────────────────────┐
│  设备端                                                  │
│  ① uiautomator（系统自带，默认通道）                       │
│  ② 伴侣 App（无障碍服务 + 127.0.0.1:8377，可选增强）        │
└──────────────────────────────────────────────────────────┘
```

两条通道互补：

- **uiautomator**：零安装，覆盖大多数场景；
- **伴侣 App**：中文输入、持续动画页面（uiautomator 等不到 idle 时自动降级过来）。

---

## 四、核心能力

### 4.1 观察

| 命令 | 能力 |
| --- | --- |
| `snapshot` | 当前屏幕的结构化元素表，每个节点带稳定 id |
| `snapshot --compact` | 只留操作必需字段，实测省 54% token |
| `screenshot` | 界面真相，用于交叉验证可能过期的层级 |
| `crashes` | 崩溃记录（进程 / 异常 / 调用栈），不用翻几百行日志 |
| `wake` | 灭屏 / 锁屏时先点亮（不绕过密码） |

### 4.2 操作

| 命令 | 说明 |
| --- | --- |
| `tap <target>` | 支持 `n12` / `text=登录` / `id=btn` / `360,900` / `text=确定@2` |
| `long-press` / `double-tap` / `drag` | 长按 / 双击 / 拖拽 |
| `input <text>` | ASCII 直连 adb；中文自动给出 `--bridge` 方案 |
| `swipe` | 方向或自定义坐标 `"100,800 100,200"` |
| `scroll-to <target>` | 滚动查找，滚到界面切走或内容不变会主动停手 |
| `key` | back / home / enter / recents / volume… |
| `run <flow.json>` | 批量回归：把一串操作写成 JSON，一次跑完并出逐步报告 |

### 4.3 自愈

| 命令 | 能力 |
| --- | --- |
| `setup` | 没有设备时：判断处境（7 种），给出接真机 / 装模拟器的唯一推荐路径 + 可转述的 markdown |
| `doctor` | 8 项体检：adb 多版本冲突、设备、uiautomator、屏幕方向、缓存、伴侣 App、电量与存储、接入环境 |
| `recover` | 自动修复：重启 adb → 重连模拟器 → 清缓存 → 重新启用无障碍服务（可选关动画） |

### 4.4 多入口

- **CLI**：默认，零依赖，输出纯 JSON；
- **MCP**（`agent-android mcp`）：stdio 协议，13 个 tools，Claude Desktop / Cursor 挂上即用；
- **HTTP**（`serve --port 8787`）：给不能跑终端的 Agent / 其他语言；
- **伴侣 App**：设备端本地接口，`X-Agent-Token` 鉴权。

---

## 五、工程实现要点

1. **一切都是 JSON**：stdout 只有一个对象，日志走 stderr，Agent 不用做文本解析。
2. **错误即协议**：`error_code`（稳定枚举）+ `retryable`（是否重试）+ `actions`（可直接执行的命令列表）。
3. **三层降级链**：uiautomator → 伴侣 App 无障碍 → 截图 + 坐标；连不上就 `recover`，再不行 `setup` 教用户接设备。
   降级是自动的：`uiautomator` 等不到 idle 时直接切无障碍，`snapshot.source` 会标明这次走的哪条路。
4. **并发安全**：快照缓存原子写（`os.replace`）、每台设备独立缓存文件、dump 用 uuid 临时路径。
5. **安全边界**：密码框默认脱敏（`••••••`）、设备端接口必须带 token、HTTP 默认只监听回环、输入文本拒绝换行（防 shell 注入）。
6. **跨版本兼容**：屏幕尺寸取 `dumpsys window displays` 的 `cur=`（随旋转变化）、adb 多版本冲突检测。
7. **快照优先走无障碍通道**：`snapshot` 默认先试伴侣 App（HTTP + 无障碍遍历），
   不可用才回落 uiautomator。设置页 **4.0s → 1.8s**；持续动画的详情页 **14s → 1.6s**。
8. **冷启动并发化**：模拟器端口探测与 connect 改为线程池并发，`devices` **3.5s → 1.3s**。

---

## 六、实测验证

### 6.1 端到端冒烟（`scripts/e2e_smoke.py`，MuMu 12）

```
12/12 通过
体检 → 伴侣就绪 → 唤醒 → 启动设置 → 按文本点击 → 返回
→ 滚动查找 → 进子页读信息 → 截图 → 回桌面 → 崩溃检测 → 中文输入
```

关键几步的真实输出：

- 按文本点击：`滚动 1 次后点击；界面 → ['互联网', '开启', 'SIM 卡']`
- 进子页读信息：`读到 ['型号', 'Android 版本', '版本号']`（该页面 uiautomator 等不到 idle，**自动降级到无障碍**）
- 中文输入：`输入框内容 → ['端到端测试 2026']`

### 6.2 流程回归（`agent-android run`）

把上面的步骤写成 JSON，就能一条命令复跑：

```
flow: 打开设置并读取设备信息 | 10/10 步通过 | 24.8 秒
  OK # 3 assert      2.44s  package=com.android.settings, nodes=80
  OK # 5 scroll_to   2.43s  attempts=0, element=n90
  OK # 8 assert     12.53s  文本含「型号」（该页 uiautomator 等不到 idle，自动降级）
  OK # 9 screenshot  0.33s  91 KB
```

### 6.3 真实浏览器搜索（一次性人工验证）

任务：让 Agent 用浏览器搜索「金融」。

| 步骤 | Agent 的动作 | 结果 |
| --- | --- | --- |
| 1 | 回桌面，`snapshot` 找「浏览器」 | 定位到 `n69` |
| 2 | `tap n69` | 打开 Chromium，加载百度首页 |
| 3 | 点地址栏 `700,161` | 地址栏进入编辑态，`focused=true` |
| 4 | `--bridge input "金融"` | 地址栏显示「金融」 |
| 5 | `key enter` | 导航被百度首页 hash 拦截，未出结果 |
| 6 | 改输入完整 URL `https://www.baidu.com/s?wd=金融` | 正常导航 |
| 7 | 遇到定位权限弹窗，`tap 禁止` | 弹窗关闭 |
| 8 | `snapshot` | **读出「金融 - 百度」及 303 个结果节点** |

这一轮同时暴露了两条真实边界（已记录在 README）：

- Chromium 地址栏的点击区域会被左侧「站点信息」图标占用，点中心会打开信息面板，需点文本区；
- 百度首页的搜索框是 WebView 内的 HTML input，`ACTION_SET_TEXT` 会被页面 JS 覆盖，中文输入应走地址栏。

### 6.4 MCP 接入验证

按 MCP 报文真实起进程对话：

```
initialize → serverInfo: agent-android 0.1.0, protocolVersion: 2024-11-05
tools/list → 13 个 tools，每个都有 inputSchema
tools/call android_devices → 返回设备列表
tools/call android_key home → 回到桌面
tools/call android_snapshot → app.lawnchair / 67 节点
tools/call android_tap text=浏览器 → 进入 com.android.chromium / 303 节点
tools/call android_screenshot → 返回 image content（1.7 MB）
tools/call android_tap（缺 target）→ isError + 「参数错误: 缺少 target」
```

### 6.5 自愈能力实测

手动制造故障，看工具能否自己恢复：

| 制造的故障 | 结果 |
| --- | --- |
| `settings delete enabled_accessibility_services`（模拟系统回收无障碍） | 自动重写 secure settings + 拉起 App + 刷新令牌，重试成功，`source=accessibility` |
| `adb kill-server`（模拟 server 被清理） | 底层检测到串号失效，自动重连并换在线串号，命令无感恢复 |
| 设置详情页持续动画（`could not get idle state`） | 自动降级到无障碍，耗时 18 秒而不是报错 |
| App 崩溃退回桌面 | `snapshot` 提示「可能已崩溃」，`crashes` 直接给进程 + 异常 + 调用栈 |

### 6.6 示例流程

`examples/` 下 10 个流程全部通过 `load_flow` 校验，真机抽跑：

| 流程 | 结果 |
| --- | --- |
| `app-launch.json` | 6/6，8.3 s |
| `scroll-list.json` | 5/5，10.8 s |
| `screenshot-walk.json` | 11/11，9.7 s |
| `settings-info.json` | 10/10，24.8 s |

### 6.6 静态质量

- 62 个单元测试，覆盖快照解析、流程断言、MCP 协议、资源解析；
- Android `assembleDebug` 零警告；
- CI 同时跑 CLI 测试与 APK 构建。

---

## 七、目录结构

```
agent-andorid/
├── agent_android/            # PC 端 CLI（零第三方依赖）
│   ├── cli.py                # 命令入口，统一 JSON 输出
│   ├── adb.py                # adb 封装：设备发现 / 自动重连 / 自检
│   ├── emulators.py          # 模拟器端口表与自动发现
│   ├── snapshot.py           # 核心：UI XML → 结构化元素表
│   ├── actions.py            # 点击 / 输入 / 滑动 / 拖拽 / 崩溃检测
│   ├── bridge.py             # 伴侣 App 的 HTTP 客户端（中文输入）
│   ├── diagnostics.py        # doctor / recover：诊断与自动修复
│   ├── onboarding.py         # setup：没有设备时的接入引导
│   ├── server.py             # 可选本地 HTTP 服务
│   └── util.py               # 错误类型、JSON 输出、快照缓存
├── android/                  # 设备端伴侣 App（Kotlin + Compose）
│   └── app/src/main/java/com/coco/agentandroid/
│       ├── BridgeAccessibilityService.kt   # 无障碍服务：读树 + 手势 + 输入
│       ├── BridgeHttpServer.kt             # 本地 HTTP 服务（带 token 鉴权）
│       ├── UiTreeBuilder.kt                # 无障碍节点树 → JSON
│       ├── BridgeState.kt                  # 全局状态：令牌、日志、服务引用
│       └── MainActivity.kt                 # 状态面板 + 自测输入框
├── skills/agent-android/SKILL.md           # 给 Claude Code 的能力说明与故障手册
├── scripts/e2e_smoke.py                    # 真机端到端冒烟（12 步）
├── tests/                                  # 43 个单元测试
└── README.md
```

---

## 八、边界与限制

明确不做的：

- **验证码 / 滑块**：不做自动破解（合规），遇到会明确报错并交还给人；
- **锁屏密码**：`wake` 只点亮屏幕，不尝试绕过；
- **自绘界面（游戏 / Unity）**：uiautomator 拿不到节点，会提示改用 `screenshot` + 坐标点击。

已知限制：

- WebView 内的 HTML 输入框，`ACTION_SET_TEXT` 可能被页面 JS 覆盖；
- 部分 ROM 的地址栏 / 控件点击区域与 `bounds` 有偏差，需要坐标微调；
- `long-press` 在伴侣 App 通道未实现（走 adb 通道正常）。

---

## 九、下一步（按价值排序）

1. **PyPI 发布**：`pip install agent-android` 一行装好，降低上手门槛；
2. **设备锁**：多人 / 多 Agent 共用一台设备时用文件锁串行化；
3. **录制回放**：记录一次成功操作序列，自动生成 flow 文件；
4. **视觉回归**：截图 diff，捕捉 UI 改动。

---

*文档与实际代码同步维护；命令细节见 `README.md`，Agent 使用规范见 `skills/agent-android/SKILL.md`。*
