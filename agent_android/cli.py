"""命令行入口。

所有子命令的 stdout 只有一个 JSON 对象；诊断信息走 stderr。
这样 Agent 可以直接 `json.loads(result.stdout)`，不用做脆弱的文本解析。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Sequence

from . import (
    __version__, actions, adb, bridge, diagnostics, emulators, notify,
    onboarding, runner, snapshot,
)
from .util import (
    AgentAndroidError, device_lock, emit, fail, load_snapshot, ok,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-android",
        description="让 AI 编程助手通过 adb 观察并操作 Android 手机/模拟器",
        epilog="示例: agent-android snapshot | agent-android tap text=登录",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # 全局选项要放在子命令之前；也可以用环境变量 ANDROID_SERIAL 代替
    parser.add_argument("--device", "-d", help="设备序列号（多设备时必填）")
    parser.add_argument("--pretty", action="store_true", help="缩进输出，便于人阅读")
    parser.add_argument("--bridge", action="store_true",
                        help="改用设备端伴侣 App 的 HTTP 接口（支持中文输入）")
    parser.add_argument("--bridge-port", type=int, default=8377,
                        help="伴侣 App 端口，默认 8377")
    parser.add_argument("--token", default=os.environ.get("AGENT_ANDROID_TOKEN", ""),
                        help="伴侣 App 访问令牌（见 App 界面），也可用 AGENT_ANDROID_TOKEN")
    parser.add_argument("--lock", type=float, default=0, metavar="SECONDS",
                        help="操作前获取设备锁，多个 Agent 共用一台设备时避免互相打断；"
                             "抢不到最多排队 N 秒")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="列出已连接的设备（含模拟器识别）")
    sub.add_parser("health", help="环境自检：adb / 设备 / uiautomator / 伴侣 App")
    sub.add_parser("doctor", help="完整体检，输出可执行的修复命令")
    p = sub.add_parser(
        "setup",
        help="没有任何设备时：给出连接真机 / 安装模拟器的具体步骤",
    )
    p.add_argument("--markdown", action="store_true",
                   help="只输出可直接转述给用户的 markdown")
    p = sub.add_parser("recover", help="自动尝试修复：重启 adb、重连模拟器、清缓存")
    p.add_argument("--fix-animations", action="store_true",
                   help="顺带关闭系统动画，提高 dump 成功率（测完记得设回 1）")

    p = sub.add_parser(
        "connect",
        help="连接模拟器：不给地址时自动扫描常见端口（MuMu/雷电/夜神/BlueStacks/WSA…）",
    )
    p.add_argument("address", nargs="?", help="如 127.0.0.1:62001；省略则自动扫描")
    p.add_argument("--list", action="store_true", help="只打印常见模拟器端口表")
    p.add_argument("--timeout", type=float, default=0.2, help="端口探测超时（秒）")

    p = sub.add_parser("snapshot", help="抓取当前屏幕的 UI 元素表（核心命令）")
    p.add_argument("--full", action="store_true", help="包含无文字的纯布局节点")
    p.add_argument("--compact", action="store_true",
                   help="只输出操作必需字段，大幅降低 token 消耗")
    p.add_argument("--prefer", choices=["auto", "uiautomator", "accessibility"],
                   default="auto",
                   help="优先通道：auto 先试无障碍（实测快一倍），不可用回落 uiautomator")
    p.add_argument("--node", metavar="ID",
                   help="只看某个元素的完整属性（如 --node n12），不重新抓屏")

    sub.add_parser("wake", help="点亮屏幕（灭屏/锁屏时先执行）")

    p = sub.add_parser("tap", help="点击元素：n12 / text=登录 / id=btn / 360,900")
    p.add_argument("target")
    p.add_argument("--delay", type=int, default=0, help="点击前延时（毫秒）")
    p.add_argument("--verify", action="store_true",
                   help="点击前后各抓一次快照，确认界面真的变了（慢一倍，但可信）")
    p.add_argument("--auto-scroll", action="store_true",
                   help="找不到目标时先滚动查找（默认关闭，避免滚到意外位置后误点）")

    p = sub.add_parser("long-press", help="长按元素")
    p.add_argument("target")
    p.add_argument("--duration", type=int, default=800, help="按住时长（毫秒）")

    p = sub.add_parser("double-tap", help="双击元素")
    p.add_argument("target")

    p = sub.add_parser("drag", help="拖拽：从某个元素拖到坐标或另一个元素")
    p.add_argument("target", help="起点元素")
    p.add_argument("to", help="终点：x,y 或另一个 target")

    p = sub.add_parser("scroll-to", help="滚动直到目标出现在屏幕上")
    p.add_argument("target")
    p.add_argument("--max", type=int, default=8, help="最多滚动几次")

    p = sub.add_parser("input", help="输入文字（仅支持 ASCII）")
    p.add_argument("text")
    p.add_argument("--target", help="先点击的输入框")
    p.add_argument("--clear", action="store_true", help="输入前清空输入框")

    p = sub.add_parser("swipe", help="滑动：up/down/left/right 或 'x1,y1 x2,y2'")
    p.add_argument("direction")
    p.add_argument("--duration", type=int, default=400, help="滑动时长（毫秒）")

    p = sub.add_parser("key", help="系统按键：back/home/enter/recents/...")
    p.add_argument("name")

    p = sub.add_parser("install", help="安装 APK")
    p.add_argument("apk")

    p = sub.add_parser("launch", help="按包名启动应用")
    p.add_argument("package")

    p = sub.add_parser("screenshot", help="截图保存到本机 PNG")
    p.add_argument("-o", "--output", default="screen.png")

    p = sub.add_parser("logcat", help="读取最近的日志")
    p.add_argument("--lines", type=int, default=50)
    p.add_argument("--level", default="", help="过滤级别，如 E / W / I")

    p = sub.add_parser("crashes", help="只取崩溃记录（进程 / 异常 / 调用栈）")
    p.add_argument("--limit", type=int, default=3)

    p = sub.add_parser("notify", help="通知栏：下拉 / 收起 / 读取 / 点击")
    p.add_argument("action", choices=["list", "shade", "collapse", "tap"],
                   help="list 读可见通知；tap 点第 N 条")
    p.add_argument("--index", type=int, default=1, help="notify tap 的序号，从 1 开始")
    p.add_argument("--limit", type=int, default=8, help="list 最多返回几条")
    p.add_argument("--keep-open", action="store_true",
                   help="list 之后不收通知栏，便于紧接着操作")

    sub.add_parser(
        "mcp",
        help="以 MCP Server 运行（stdio），供 Claude Desktop / Cursor 直接接入",
    )

    p = sub.add_parser("run", help="按 JSON 流程批量执行（含断言与报告）")
    p.add_argument("flow", help="流程文件路径，示例见 examples/settings-info.json")
    p.add_argument("--report", help="把逐步报告写到这个文件")
    p.add_argument("--continue-on-error", action="store_true",
                   help="某步失败后继续执行（默认立即停止）")
    p.add_argument("--dry-run", action="store_true",
                   help="只检查 target 在当前界面能否找到，不真的点击/输入")
    p.add_argument("--resume", metavar="REPORT",
                   help="读上次报告，跳过其中已通过的步骤")

    p = sub.add_parser("serve", help="启动本地 HTTP 服务，供任意 Agent 调用")
    # 故意不用 8377：那是设备端伴侣 App 的端口，两者同时跑会占不到端口
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1",
                   help="监听地址，务必保持本机回环地址")

    # argparse 的全局选项只能写在子命令前面，但人（和 Agent）习惯写在后面：
    #   agent-android tap n1 --pretty   ← 直觉写法，默认会报 unrecognized arguments
    # 给每个子命令补一份同名选项；default=SUPPRESS 保证「没写时不覆盖全局值」。
    for subparser in sub.choices.values():
        subparser.add_argument("--pretty", action="store_true",
                               default=argparse.SUPPRESS,
                               help="缩进输出，便于人阅读")
        subparser.add_argument("--device", "-d", default=argparse.SUPPRESS,
                               help="设备序列号（多设备时必填）")
        subparser.add_argument("--bridge", action="store_true",
                               default=argparse.SUPPRESS,
                               help="改用设备端伴侣 App 的 HTTP 接口")
        subparser.add_argument("--lock", type=float, default=argparse.SUPPRESS,
                               metavar="SECONDS",
                               help="操作前获取设备锁，抢不到最多排队 N 秒")
        subparser.add_argument("--bridge-port", type=int,
                               default=argparse.SUPPRESS,
                               help="伴侣 App 端口")
        subparser.add_argument("--token", default=argparse.SUPPRESS,
                               help="伴侣 App 访问令牌")

    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    """把解析后的参数分派到具体动作，返回要输出的 JSON 载荷。"""
    cmd = args.command

    # 诊断/接入类命令不依赖具体平台，放最前面
    if cmd == "setup":
        info = onboarding.guide()
        if getattr(args, "markdown", False):
            return {"markdown": info["markdown"]}
        return info
    if cmd == "doctor":
        return diagnostics.doctor(args.device)
    if cmd == "recover":
        return diagnostics.recover(
            args.device, fix_animations=getattr(args, "fix_animations", False)
        )

    if cmd == "serve":
        # serve 是长驻进程，交给 server.py 单独处理
        from .server import serve
        return serve(host=args.host, port=args.port, device=args.device)

    if cmd == "devices":
        # 和其它命令保持一致：没有在线设备时先自动尝试连接，
        # 否则 devices 会显示一排 offline，但后面 snapshot 又能用，容易让人困惑
        adb.ensure_online()
        items = adb.devices()
        for item in items:
            item["emulator"] = emulators.identify(item["serial"])
        return {"devices": items}
    if cmd == "health":
        return adb.health(args.device)
    if cmd == "connect":
        if args.list:
            return {"emulators": emulators.catalog()}
        if args.address:
            return adb.connect(args.address)
        return {
            "connected": adb.connect_known(args.timeout),
            "hint": "若列表为空，确认模拟器已启动且打开了 ADB 开关；"
                    "BlueStacks 需在 设置 > 高级 手动启用",
        }
    # 其余命令都需要一台确定的设备
    device = adb.resolve_device(args.device)

    # --bridge 只对伴侣 App 支持的命令生效，其余命令（wake/install/logcat…）
    # 继续走 adb —— 否则用户会莫名其妙收到「不支持」。
    if getattr(args, "bridge", False) and cmd in bridge.SUPPORTED:
        return bridge.dispatch(cmd, args, device)

    if cmd == "snapshot":
        node_id = getattr(args, "node", None)
        if node_id:
            # 从最近一次快照里取：不重新抓屏，也不受当前界面变化影响
            cached = load_snapshot(device)
            node = next((n for n in cached["nodes"] if n["id"] == node_id), None)
            if node is None:
                raise AgentAndroidError(
                    f"最近一次快照里没有 {node_id}",
                    f"共 {cached.get('node_count', 0)} 个元素；先 snapshot 刷新",
                    code="element_not_found", retryable=True,
                    actions=["agent-android snapshot"],
                )
            return {"node": node, "package": cached.get("package"),
                    "screen": cached.get("screen"), "source": cached.get("source")}
        return snapshot.take(device, full=args.full,
                             compact=getattr(args, "compact", False),
                             prefer=getattr(args, "prefer", "auto"))
    if cmd == "wake":
        return actions.wake(device=device)
    if cmd == "tap":
        return actions.tap(args.target, device=device, delay_ms=args.delay,
                           verify=getattr(args, "verify", False),
                           auto_scroll=getattr(args, "auto_scroll", False))
    if cmd == "long-press":
        return actions.long_press(args.target, device=device, duration_ms=args.duration)
    if cmd == "double-tap":
        return actions.double_tap(args.target, device=device)
    if cmd == "drag":
        return actions.drag(args.target, args.to, device=device)
    if cmd == "scroll-to":
        return actions.scroll_to(args.target, device=device, max_swipes=args.max)
    if cmd == "input":
        # input_any 内部按字符集选通道：ASCII 走 adb，其余自动走伴侣 App
        return actions.input_any(args.text, device=device,
                                 target=args.target, clear=args.clear)
    if cmd == "swipe":
        return actions.swipe(args.direction, device=device, duration_ms=args.duration)
    if cmd == "key":
        return actions.key(args.name, device=device)
    if cmd == "install":
        return actions.install(args.apk, device=device)
    if cmd == "launch":
        return actions.launch(args.package, device=device)
    if cmd == "screenshot":
        return actions.screenshot(args.output, device=device)
    if cmd == "logcat":
        return actions.logcat(device=device, lines=args.lines, level=args.level)
    if cmd == "crashes":
        return actions.crashes(device=device, limit=args.limit)
    if cmd == "notify":
        if args.action == "shade":
            return notify.shade(device)
        if args.action == "collapse":
            return notify.collapse(device)
        if args.action == "tap":
            return notify.tap(device, args.index)
        return notify.list_visible(device, limit=args.limit,
                                   keep_open=args.keep_open)
    if cmd == "run":
        return runner.run_flow_file(
            args.flow, device,
            on_failure="continue" if args.continue_on_error else None,
            report=args.report,
            resume=getattr(args, "resume", None),
            dry_run=getattr(args, "dry_run", False),
        )

    raise AgentAndroidError(f"未实现的命令: {cmd}")


def main(argv: Sequence[str] | None = None) -> int:
    """返回进程退出码：0 成功，2 业务错误，130 用户中断。"""
    # Windows 中文环境默认 cp936：界面文本含 emoji/日文时 print 会抛
    # UnicodeEncodeError，Agent 拿到的是 traceback 而不是 JSON。强制 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    # MCP 是长驻的 stdio 服务，stdout 只能有协议消息，不能走 emit 那套 JSON 输出
    if args.command == "mcp":
        from .mcp_server import serve_stdio
        return serve_stdio(args.device)

    try:
        wait = getattr(args, "lock", 0)
        if wait:
            # 并发场景：拿到锁再动设备，避免两个 Agent 互相把界面刷走
            with device_lock(getattr(args, "device", None) or "default", wait=wait):
                payload = dispatch(args)
        else:
            payload = dispatch(args)
        emit(ok(**payload) if "ok" not in payload else payload, pretty=args.pretty)
        # 业务失败（doctor 体检不过、断言不成立）必须给非零退出码，
        # 否则 CI 里 `doctor` 永远绿，等于没有安全网
        return 0 if payload.get("ok", True) is not False else 2
    except AgentAndroidError as exc:
        emit(fail(exc.message, exc.hint, code=exc.code,
                  retryable=exc.retryable, actions=exc.actions), pretty=args.pretty)
        return 2
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
