# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- `notify list|tap|shade|collapse`：读通知栏可见内容、点击第 N 条通知。
- `--lock N`：多 Agent 共用一台设备时用文件锁串行化，抢不到可排队等待。
- `--prefer auto|uiautomator|accessibility`：指定快照走哪条通道。
- `py.typed`：mypy / pyright 能读到类型信息。
- `README.en.md`：英文版文档。

### 变更

- `snapshot` 默认优先走伴侣 App 的无障碍通道，不可用才回落 uiautomator
  （设置页 4.0s → 1.8s；持续动画的详情页 14s → 1.6s）。
- 模拟器端口探测与 connect 改为并发，`devices` 3.5s → 1.3s。
- CI 增加覆盖率统计。

### 修复

- `tap` 遇到无障碍通道返回的无效坐标（负高度）时，自动换 uiautomator 重抓一次。
- `snapshot` 检测到整屏坐标失效（旋转后未刷新）时自动切换通道，并写入 `warning`。
- `find_element` 找不到目标时，返回最接近的候选与屏幕外元素，而不是只说「没找到」。
- `snapshot` 新增 `raw_node_count` / `filtered_out` / `likely_custom_drawn` / `multi_window`，
  解释「为什么两个通道节点数差这么多」以及自绘界面、分屏的可见性限制。

### 增强

- `run` 支持 `repeat` / `if` / `while` 三种控制流，`--dry-run` 只校验 target，
  `--resume <报告>` 跳过上次已通过的步骤。
- `tap --verify` 点击前后各抓一次快照，明确返回界面到底变没变。
- `tap --auto-scroll` 找不到目标时先滚一段再找（默认关闭）。
- `snapshot --node n12` 单独查看某个元素的完整属性。
- 新增 `.github/workflows/e2e.yml`：在真实模拟器上跑流程的 CI 模板。
- README 增加「不支持什么」「哪些命令会改设备」「在 CI 里跑」三节。

## [0.1.0] - 2026-11-14

首个公开版本。

### 新增

- **CLI**（零第三方依赖）：`snapshot` / `tap` / `long-press` / `double-tap` /
  `drag` / `input` / `swipe` / `scroll-to` / `key` / `install` / `launch` /
  `screenshot` / `logcat` / `crashes` / `wake`，输出统一为一个 JSON 对象。
- **结构化快照**：把 uiautomator 的 XML 翻译成带 `id` / 文本 / 坐标 / 可点击性的
  元素表；`--compact` 只保留操作必需字段，大列表页省一半以上 token。
- **自愈**：每个错误携带 `error_code` / `retryable` / `actions`（36 种）；
  adb 串号失效自动重连并切换到当前在线串号；uiautomator 等不到 idle 时自动降级到
  无障碍服务；伴侣 App 掉线自动重新启用无障碍、拉起进程并刷新令牌。
- **诊断三件套**：`doctor`（8 项体检）、`recover`（4 步自动修复）、
  `setup`（7 种接入处境的分步引导，输出可直接转述的 markdown）。
- **MCP Server**：`agent-android mcp`，stdio 传输，12 个 tools，
  Claude Desktop / Cursor 挂上即用。
- **本地 HTTP 服务**：`serve`，令牌鉴权，只监听回环地址。
- **流程 runner**：`run <flow.json>`，JSON 编排操作与断言，输出逐步报告。
- **设备端伴侣 App**（Kotlin + Compose）：无障碍服务读 UI 树、
  `ACTION_SET_TEXT` 实现中文输入，本地 HTTP 服务只监听 `127.0.0.1`。
- **示例**：10 个可直接运行的流程，覆盖设置读取、浏览器搜索、权限弹窗、
  多 App 切换、崩溃恢复等场景。

### 安全

- 密码框默认脱敏为 `••••••`（`AGENT_ANDROID_UNMASK=1` 仅用于调试）。
- 输入文本拒绝换行与制表符，避免 `adb shell` 命令注入。
- HTTP 服务默认只监听回环地址，并要求 `X-Agent-Token`。
- 不实现验证码 / 滑块的自动破解，不绕过锁屏密码。

### 已知限制

- WebView 内的 HTML 输入框，`ACTION_SET_TEXT` 可能被页面 JS 覆盖，中文输入建议走地址栏。
- 部分 ROM 的控件点击区域与 `bounds` 有偏差，需要坐标微调。
- `long-press` 在伴侣 App 通道未实现，走 adb 通道正常。
- 游戏 / Unity 等自绘界面拿不到控件树，需退回截图 + 坐标点击。

[Unreleased]: https://github.com/DanTagege/agent-android/commits/main
[0.1.0]: https://github.com/DanTagege/agent-android/releases
