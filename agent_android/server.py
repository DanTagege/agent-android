"""可选的本地 HTTP 服务。

CLI 适合 Claude Code / Codex 这类能跑终端的 Agent；
HTTP 模式则让浏览器插件、脚本、其他语言的 Agent 也能用同一套能力。

安全：默认只监听 127.0.0.1。这个接口等价于「远程控制手机」，
绝对不要绑定到 0.0.0.0 或映射到公网端口。
"""

from __future__ import annotations

import json
import os
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import actions, adb, snapshot
from .util import AgentAndroidError, fail, has_snapshot, log, ok

# 内存里的操作日志，方便在 App 界面或 /logs 接口里回看
RECENT: deque[dict[str, Any]] = deque(maxlen=200)


def _device_for(query: dict[str, list[str]], fallback: str | None) -> str:
    """优先用请求参数里的 device，其次用启动服务时指定的设备。"""
    return (query.get("device") or [fallback or ""])[0] or adb.resolve_device(None)


def _require(body: dict[str, Any], key: str, example: str) -> str:
    """取必填字段。

    直接 body[key] 在缺字段时会抛 KeyError，被兜底的 except 吞成 500，
    调用方无法区分「我写错了」和「服务挂了」，容易无脑重试放大问题。
    """
    value = body.get(key)
    if value is None or str(value).strip() == "":
        raise AgentAndroidError(
            f"缺少 {key}", f"示例: {example}",
            code="missing_field", retryable=False,
            actions=[f"补上 {key} 字段后重试"],
        )
    return str(value)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "agent-android/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # 默认实现会把访问日志打到 stdout，改成 stderr 保持输出干净
        log(fmt % args)

    def _send(self, status: int, payload: dict[str, Any] | bytes,
              content_type: str = "application/json; charset=utf-8") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        """设置 AGENT_ANDROID_TOKEN 后，请求需带同样的 X-Agent-Token 头。

        没设令牌时**只允许回环来源**：否则 `--host 0.0.0.0` 就等于把设备
        控制权开放给整个局域网，而默认无令牌会让这层保护形同虚设。
        """
        token = os.environ.get("AGENT_ANDROID_TOKEN", "")
        if not token:
            host = self.client_address[0]
            return host in ("127.0.0.1", "::1") or host.startswith("::ffff:127.")
        return self.headers.get("X-Agent-Token") == token

    def do_GET(self) -> None:  # noqa: N802  (标准库约定的方法名)
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized():
            self._send(401, fail("未授权", "请求头带上 X-Agent-Token"))
            return
        try:
            if parsed.path == "/health":
                self._send(200, ok(**adb.health(query.get("device", [None])[0])))
                return
            device = _device_for(query, getattr(self.server, "default_device", None))
            if parsed.path == "/snapshot":
                full = query.get("full", ["0"])[0] in ("1", "true")
                compact = query.get("compact", ["0"])[0] in ("1", "true")
                self._send(200, ok(**snapshot.take(device, full=full, compact=compact)))
            elif parsed.path == "/logs":
                self._send(200, ok(logs=list(RECENT)))
            elif parsed.path == "/screenshot":
                # 直接返回 PNG 二进制，省掉一次落盘
                data = adb.run(["exec-out", "screencap", "-p"], device=device,
                               timeout=30, binary=True)
                if not (isinstance(data, bytes) and data.startswith(b"\x89PNG")):
                    # Windows 上部分 adb 会做换行转换，直接把坏数据发出去更糟
                    raise AgentAndroidError(
                        "截图数据不是有效 PNG",
                        "改用 CLI：agent-android screenshot -o shot.png（带落盘 fallback）",
                        code="screenshot_invalid", retryable=True,
                        actions=["agent-android screenshot -o shot.png"],
                    )
                self._send(200, data, "image/png")
            else:
                self._send(404, fail("未知路径", "可用: /health /snapshot /logs /screenshot /action"))
        except AgentAndroidError as exc:
            self._send(400, fail(exc.message, exc.hint, code=exc.code,
                                 retryable=exc.retryable, actions=exc.actions))
        except Exception as exc:  # 网络服务必须对异常兜底，否则连接会挂住
            self._send(500, fail(f"内部错误: {exc}"))

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, fail("未授权"))
            return
        parsed = urlparse(self.path)
        if parsed.path != "/action":
            self._send(404, fail("未知路径", "动作请 POST /action"))
            return
        try:
            # 上限 1 MiB：动作请求都很小，避免超大 Content-Length 直接吃掉内存
            length = max(0, min(int(self.headers.get("Content-Length", "0") or 0), 1_048_576))
            payload_obj = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload_obj, dict):
                raise AgentAndroidError(
                    "请求体必须是 JSON 对象",
                    '示例: {"action":"tap","target":"text=登录"}',
                    code="invalid_body",
                )
            query = parse_qs(parsed.query)
            device = _device_for(query, getattr(self.server, "default_device", None))
            result = self._run_action(payload_obj, device)
            # 只存摘要：snapshot 的 nodes 可能有几 MB，200 条足以把内存吃光
            RECENT.append({k: v for k, v in result.items() if k != "nodes"})
            self._send(200, ok(**result))
        except (AgentAndroidError, json.JSONDecodeError, ValueError) as exc:
            # 这里必须把 code/retryable/actions 一起透传，否则 HTTP 调用方
            # 拿到的 error_code 永远是 unknown，等于白带这三个字段
            if isinstance(exc, AgentAndroidError):
                self._send(400, fail(exc.message, exc.hint, code=exc.code,
                                     retryable=exc.retryable, actions=exc.actions))
            else:
                self._send(400, fail(f"请求体不是合法 JSON: {exc}", "",
                                     code="invalid_json", retryable=False))
        except Exception as exc:
            self._send(500, fail(f"内部错误: {exc}"))

    @staticmethod
    def _run_action(body: dict[str, Any], device: str) -> dict[str, Any]:
        """把 JSON 动作分派到 actions 模块。"""
        name = body.get("action", "")
        # 按元素 id 操作依赖上一次快照；HTTP 客户端可能没先调 /snapshot，
        # 这里自动补一次，避免直接返回「没有可用的屏幕快照」这种无用错误。
        if name in ("tap", "long_press", "input") and not has_snapshot(device):
            snapshot.take(device)
        if name in ("tap", "long_press"):
            # 用 get 而不是 []：缺字段时 KeyError 会落到 500，调用方无法区分
            # 是自己请求写错了还是服务挂了，容易被无脑重试放大。
            target = str(body.get("target") or "").strip()
            if not target:
                raise AgentAndroidError(
                    "缺少 target",
                    '示例: {"action":"tap","target":"text=登录"}',
                )
            if name == "tap":
                return actions.tap(target, device=device)
            return actions.long_press(target, device=device)
        if name == "input":
            text = _require(body, "text", '{"action":"input","text":"hello world"}')
            return actions.input_text(text, device=device,
                                      target=body.get("target"),
                                      clear=bool(body.get("clear", False)))
        if name == "swipe":
            direction = _require(body, "direction", '{"action":"swipe","direction":"up"}')
            return actions.swipe(direction, device=device)
        if name == "key":
            key_name = _require(body, "name", '{"action":"key","name":"back"}')
            return actions.key(key_name, device=device)
        if name == "wake":
            return actions.wake(device=device)
        if name in ("double_tap", "double-tap"):
            target = _require(body, "target", '{"action":"double_tap","target":"n1"}')
            return actions.double_tap(target, device=device)
        if name == "drag":
            frm = _require(body, "from", '{"action":"drag","from":"n1","to":"500,300"}')
            to = _require(body, "to", '{"action":"drag","from":"n1","to":"500,300"}')
            return actions.drag(frm, to, device=device)
        if name in ("scroll_to", "scroll-to"):
            target = _require(body, "target", '{"action":"scroll_to","target":"设置"}')
            return actions.scroll_to(target, device=device,
                                     max_swipes=int(body.get("max", 8)))
        if name == "crashes":
            return actions.crashes(device=device, limit=int(body.get("limit", 3)))
        if name == "install":
            apk = _require(body, "apk", '{"action":"install","apk":"D:/app.apk"}')
            return actions.install(apk, device=device)
        if name == "launch":
            package = _require(body, "package", '{"action":"launch","package":"com.android.settings"}')
            return actions.launch(package, device=device)
        if name == "snapshot":
            return snapshot.take(device, full=bool(body.get("full")),
                                 compact=bool(body.get("compact")))
        raise AgentAndroidError(
            f"未知动作: {name}",
            "可用: tap / long_press / double_tap / drag / scroll_to / input / swipe / "
            "key / wake / snapshot / crashes / install / launch",
        )


def serve(host: str = "127.0.0.1", port: int = 8787,
          device: str | None = None) -> dict[str, Any]:
    """启动 HTTP 服务（阻塞），返回启动信息由 CLI 打印一次。"""
    if host not in ("127.0.0.1", "localhost", "::1"):
        log(f"警告：监听 {host} 会把手机控制权暴露到网络，请确认已设置访问令牌")
    httpd = ThreadingHTTPServer((host, port), BridgeHandler)
    httpd.default_device = device  # type: ignore[attr-defined]
    info = {"host": host, "port": port, "device": device or "auto",
            "endpoints": ["/health", "/snapshot", "/logs", "/screenshot", "/action"]}
    log(f"HTTP 服务已启动: http://{host}:{port}  (Ctrl+C 退出)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return {"server": "stopped", **info}
