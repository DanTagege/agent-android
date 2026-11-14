"""MCP Server：把 agent-android 的能力暴露成 MCP tools。

任何支持 MCP 的客户端（Claude Desktop / Cursor / Continue）挂上就能用，
不需要它会跑命令、也不需要自己拼 JSON。

传输：stdio，**每行一个 JSON-RPC 对象**（MCP 的 stdio transport 规定）。
严格遵守一条：stdout 只能有协议消息，所有日志走 stderr，
否则客户端会因为解析失败直接断连。
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import __version__, actions, adb, diagnostics, runner, snapshot
from .util import AgentAndroidError, fail, log

PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "android_snapshot",
        "description": (
            "抓取当前屏幕的 UI 元素表。每个节点有 id / text / content_desc / "
            "class / bounds / clickable / editable。用节点的 id 作为 tap、input、"
            "scroll_to 的 target。这是所有操作的第一步，也是每次操作后确认结果的唯一方式。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "设备串号；只有一台时可省略"},
                "compact": {"type": "boolean",
                            "description": "只返回操作必需字段，大页面能省一半 token"},
                "full": {"type": "boolean", "description": "包含无文字的纯布局节点"},
            },
        },
    },
    {
        "name": "android_tap",
        "description": (
            "点击元素。target 支持四种写法：节点 id（n12）、text=登录、"
            "id=btn_login、坐标 360,900；同名元素用 text=确定@2 选第 2 个。"
            "会自动向上找最近的可点击祖先（文字节点本身常常不可点）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "n12 / text=登录 / id=btn / 360,900"},
                "device": {"type": "string"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "android_input",
        "description": (
            "输入文字。ASCII 直接走 adb；中文等非 ASCII 自动改走设备端伴侣 App"
            "（adb 原生不支持中文）。输入后务必再 snapshot 确认内容真的写进去了。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "target": {"type": "string", "description": "先点击的输入框（可选）"},
                "clear": {"type": "boolean", "description": "输入前清空"},
                "device": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "android_swipe",
        "description": "滑动：up / down / left / right，或自定义坐标 \"100,800 100,200\"。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string"},
                "device": {"type": "string"},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "android_key",
        "description": "系统按键：back / home / enter / recents / menu / delete / tab / "
                       "power / volume_up / volume_down。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "device": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "android_scroll_to",
        "description": (
            "滚动直到目标出现在屏幕上，适合长列表里找东西。每滚一次都会重新抓屏；"
            "滚出当前 App 或内容不再变化时会主动停手并说明原因。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "max": {"type": "integer", "description": "最多滚几次，默认 8"},
                "direction": {"type": "string", "description": "up（默认，看更下面的内容）或 down"},
                "device": {"type": "string"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "android_launch",
        "description": "按包名启动应用。monkey 不可靠时会自动用 am start 兜底。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package": {"type": "string"},
                "device": {"type": "string"},
            },
            "required": ["package"],
        },
    },
    {
        "name": "android_screenshot",
        "description": "截取当前屏幕并以图片形式返回，用于交叉验证可能过期的 UI 层级。",
        "inputSchema": {
            "type": "object",
            "properties": {"device": {"type": "string"}},
        },
    },
    {
        "name": "android_crashes",
        "description": "读取最近的崩溃记录（进程 / 异常 / 调用栈），不用翻几百行日志。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回几条，默认 3"},
                "device": {"type": "string"},
            },
        },
    },
    {
        "name": "android_devices",
        "description": "列出已连接的设备与模拟器识别结果。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "android_doctor",
        "description": (
            "环境体检：adb 多版本冲突、设备、uiautomator、屏幕方向、缓存、"
            "伴侣 App、电量与存储、接入环境。连不上设备时先跑这个。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"device": {"type": "string"}},
        },
    },
    {
        "name": "android_run_flow",
        "description": (
            "按 JSON 流程批量执行操作并断言结果，适合回归验证。流程里每一步是"
            '只含一个键的对象，例如 {"tap": "text=登录"}。'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "flow": {"type": "string", "description": "流程文件路径，或内联的 JSON 字符串"},
                "continue_on_error": {"type": "boolean"},
                "device": {"type": "string"},
            },
            "required": ["flow"],
        },
    },
]

_DEFAULT_DEVICE: str | None = None

# 必填参数先于「解析设备」检查：否则环境里没有 adb 时，
# 「你忘了传 target」会被错报成「找不到 adb」，排查方向完全跑偏。
_REQUIRED: dict[str, tuple[str, ...]] = {
    "android_tap": ("target",),
    "android_input": ("text",),
    "android_swipe": ("direction",),
    "android_key": ("name",),
    "android_scroll_to": ("target",),
    "android_launch": ("package",),
    "android_run_flow": ("flow",),
}


def _device(arguments: dict[str, Any], fallback: str | None) -> str:
    """优先用本次调用传的设备，其次是服务启动时的默认设备。"""
    global _DEFAULT_DEVICE
    explicit = arguments.get("device") or fallback
    if explicit:
        return str(explicit)
    if _DEFAULT_DEVICE:
        return _DEFAULT_DEVICE
    _DEFAULT_DEVICE = adb.resolve_device(None)
    return _DEFAULT_DEVICE


def _text(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False)}]}


def call_tool(name: str, arguments: dict[str, Any],
              fallback_device: str | None = None) -> dict[str, Any]:
    """执行一个工具，返回 MCP 的 content 结构。异常转成 isError 结果。"""
    try:
        missing = [key for key in _REQUIRED.get(name, ())
                   if not str(arguments.get(key, "")).strip()]
        if missing:
            return {"content": [{"type": "text",
                                 "text": f"参数错误: 缺少 {', '.join(missing)}"}],
                    "isError": True}

        if name == "android_devices":
            items = adb.devices()
            for item in items:
                item["emulator"] = emulators_identify(item["serial"])
            return _text({"devices": items})

        device = _device(arguments, fallback_device)

        if name == "android_snapshot":
            return _text(snapshot.take(device,
                                       full=bool(arguments.get("full")),
                                       compact=bool(arguments.get("compact"))))
        if name == "android_tap":
            return _text(actions.tap(str(arguments["target"]), device=device))
        if name == "android_input":
            return _text(actions.input_any(
                str(arguments.get("text", "")), device=device,
                target=arguments.get("target"),
                clear=bool(arguments.get("clear")),
            ))
        if name == "android_swipe":
            return _text(actions.swipe(str(arguments["direction"]), device=device))
        if name == "android_key":
            return _text(actions.key(str(arguments["name"]), device=device))
        if name == "android_scroll_to":
            return _text(actions.scroll_to(
                str(arguments["target"]), device=device,
                max_swipes=int(arguments.get("max", 8)),
                direction=str(arguments.get("direction", "up")),
            ))
        if name == "android_launch":
            return _text(actions.launch(str(arguments["package"]), device=device))
        if name == "android_crashes":
            return _text(actions.crashes(device=device,
                                         limit=int(arguments.get("limit", 3))))
        if name == "android_doctor":
            return _text(diagnostics.doctor(arguments.get("device")))
        if name == "android_screenshot":
            path = Path(tempfile.gettempdir()) / f"agent_android_{int(time.time())}.png"
            actions.screenshot(str(path), device=device)
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            path.unlink(missing_ok=True)
            return {"content": [
                {"type": "image", "data": encoded, "mimeType": "image/png"},
                {"type": "text", "text": "当前屏幕截图"},
            ]}
        if name == "android_run_flow":
            raw = str(arguments["flow"])
            if raw.lstrip().startswith("{"):
                flow = json.loads(raw)
            else:
                flow = runner.load_flow(raw)
            return _text(runner.run_flow(
                flow, device,
                on_failure="continue" if arguments.get("continue_on_error") else None,
            ))

        return {"content": [{"type": "text",
                             "text": f"未知工具: {name}"}], "isError": True}
    except AgentAndroidError as exc:
        return {"content": [{"type": "text", "text": json.dumps(
            fail(exc.message, exc.hint, code=exc.code,
                 retryable=exc.retryable, actions=exc.actions),
            ensure_ascii=False,
        )}], "isError": True}
    except (KeyError, ValueError) as exc:
        return {"content": [{"type": "text",
                             "text": f"参数错误: {exc}"}], "isError": True}
    except Exception as exc:  # 工具里任何异常都不能把 server 打挂
        return {"content": [{"type": "text",
                             "text": f"内部错误: {type(exc).__name__}: {exc}"}],
                "isError": True}


def emulators_identify(serial: str) -> str:
    """延迟导入，避免和 adb 模块形成循环。"""
    from . import emulators

    return emulators.identify(serial)


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(msg_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id,
           "error": {"code": code, "message": message}})


def serve_stdio(device: str | None = None) -> int:
    """stdio 主循环：一行一个 JSON-RPC 请求，直到 stdin 关闭。"""
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    log("MCP server 已启动（stdio）")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _error(None, -32700, "Parse error")
            continue
        if not isinstance(message, dict):
            _error(None, -32600, "Invalid Request")
            continue

        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        # 通知没有 id，不需要回复
        if method == "notifications/initialized" or method == "notifications/cancelled":
            continue

        if method == "initialize":
            _result(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-android", "version": __version__},
            })
        elif method == "ping":
            _result(msg_id, {})
        elif method == "tools/list":
            _result(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = str(params.get("name", ""))
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                _error(msg_id, -32602, "arguments 必须是对象")
                continue
            try:
                tool_result = call_tool(name, arguments, device)
            except Exception as exc:  # call_tool 已兜底，这里只防万一
                tool_result = {"content": [{"type": "text", "text": str(exc)}],
                               "isError": True}
            _result(msg_id, tool_result)
        elif method in ("resources/list", "prompts/list"):
            # 声明了 tools 能力，但客户端可能探测其它能力，回空列表比报错友好
            key = method.split("/")[0]
            _result(msg_id, {key: []})
        else:
            _error(msg_id, -32601, f"Method not found: {method}")

    log("MCP server 已退出")
    return 0
