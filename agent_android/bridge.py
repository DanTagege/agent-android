"""设备端伴侣 App 的 HTTP 客户端。

CLI 默认走 adb（零安装、能力全），但 `adb input text` 不支持中文。
这时改走伴侣 App 的 `/action`（用 ACTION_SET_TEXT），并自动建立 adb forward。

用法：
    agent-android --bridge input "登录"
    agent-android --bridge snapshot

这是 README 里「Companion 模式」的实际入口 —— 不再是只写在文档里的承诺。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from . import adb
from .util import AgentAndroidError, log

DEFAULT_PORT = 8377
COMPONENT = ("com.coco.agentandroid/"
             "com.coco.agentandroid.BridgeAccessibilityService")

# 只有这些命令需要（也只能）走伴侣 App；其余命令继续走 adb，
# 这样 `--bridge wake` 不会莫名其妙报「不支持」。
SUPPORTED = frozenset({"snapshot", "tap", "long-press", "input", "swipe", "key"})


def _base_url(device: str, port: int) -> str:
    # adb forward 是幂等的：重复执行只会覆盖同一条转发规则
    adb.run(["forward", f"tcp:{port}", f"tcp:{port}"], device=device)
    return f"http://127.0.0.1:{port}"


def ensure_ready(device: str) -> bool:
    """伴侣 App 掉线时尝试把它拉回来。

    常见原因：系统回收无障碍服务、用户手动关掉、App 被 force-stop。
    能写 secure settings 的环境（模拟器、已授权设备）可以全自动；
    真机上写不了会返回 False，由调用方给出手动步骤。
    """
    try:
        current = adb.shell("settings get secure enabled_accessibility_services",
                            device=device, timeout=8, check=False)
    except AgentAndroidError:
        return False

    if "com.coco.agentandroid" not in current:
        # 必须追加而不是覆盖：直接 put 会把 TalkBack、其它自动化工具一起清掉，
        # 等于为了修一个 App 把用户的整套无障碍设置改坏。
        existing = current.strip()
        merged = f"{existing}:{COMPONENT}" if existing else COMPONENT
        try:
            adb.shell(
                f"settings put secure enabled_accessibility_services '{merged}'",
                device=device, timeout=8,
            )
            adb.shell("settings put secure accessibility_enabled 1",
                      device=device, timeout=8)
            log("伴侣 App 的无障碍服务已重新启用（保留原有服务）")
        except AgentAndroidError:
            return False

    # 服务绑定后进程可能还没起来；am start 是幂等的，不会重复创建
    try:
        adb.shell("am start -n com.coco.agentandroid/.MainActivity",
                  device=device, timeout=10, check=False)
    except AgentAndroidError:
        pass
    time.sleep(2)
    return True


def request(device: str, path: str, payload: dict[str, Any] | None = None,
            *, port: int = DEFAULT_PORT, token: str = "",
            timeout: int = 15, _retry: bool = True) -> dict[str, Any]:
    """发一个请求给伴侣 App，返回它的 JSON。

    Token 由 App 启动时随机生成并显示在界面上；不匹配时 App 返回 401。
    """
    url = _base_url(device, port) + path
    data = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None else None)
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Agent-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401 and not token:
            # 没拿到令牌时，401 的真实原因通常是「App 是旧版本 / 进程没重启」，
            # 直接抛 401 会让人以为是代码问题。
            raise AgentAndroidError(
                "伴侣 App 要求令牌，但没能自动获取",
                "App 启动时会把 TOKEN 打到日志里；拿不到说明进程是旧版本或日志被清。"
                "重启 App 即可，也可以从界面手抄后用 --token 指定",
                code="bridge_token_missing", retryable=True,
                actions=[
                    "adb shell am force-stop com.coco.agentandroid",
                    "adb shell am start -n com.coco.agentandroid/.MainActivity",
                    "agent-android --bridge --token <6位令牌> <命令>",
                ],
            ) from exc
        hint = (
            "检查 token 是否与 App 界面显示的一致（或设置 AGENT_ANDROID_TOKEN）"
            if exc.code == 401 else "检查动作字段是否完整"
        )
        raise AgentAndroidError(
            f"伴侣 App 返回 HTTP {exc.code}", f"{detail or hint}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 掉线是最常见的情况（系统回收无障碍 / App 被杀），先尝试自动拉起再重试一次
        if _retry and ensure_ready(device):
            log("伴侣 App 掉线，已尝试重新拉起，正在重试…")
            return request(device, path, payload, port=port,
                           token=read_token(device) or token,
                           timeout=timeout, _retry=False)
        raise AgentAndroidError(
            f"连不上伴侣 App: {exc}",
            "确认 APK 已安装、无障碍服务已开启（App 里点「开启无障碍服务」），"
            "且设备端端口未被其它进程占用",
            code="bridge_unreachable", retryable=True,
            actions=[
                "adb shell settings put secure enabled_accessibility_services "
                f"{COMPONENT}",
                "adb shell am start -n com.coco.agentandroid/.MainActivity",
                "agent-android doctor  # 看 companion 一项",
            ],
        ) from exc


def action(device: str, payload: dict[str, Any], *, port: int = DEFAULT_PORT,
           token: str = "", timeout: int = 15) -> dict[str, Any]:
    return request(device, "/action", payload, port=port, token=token, timeout=timeout)


_TOKEN_RE = re.compile(r"^[A-Z0-9]{4,12}$")

# App 会把令牌写到这两个位置，按可靠性排序：
# 1) 外部目录：adb shell 可直接 cat
# 2) 内部目录：debug 包可用 run-as 读
_TOKEN_SOURCES = (
    "cat /sdcard/Android/data/com.coco.agentandroid/files/token.txt",
    "run-as com.coco.agentandroid cat files/token.txt",
)


def read_token(device: str) -> str:
    """读取伴侣 App 的访问令牌。

    优先读文件：logcat 的 main buffer 会滚动，App 启动时打的那行很快就被
    别的日志挤出去，靠它取令牌在真实使用中并不可靠。logcat 只作最后兜底。
    """
    for command in _TOKEN_SOURCES:
        try:
            out = adb.shell(command, device=device, timeout=8, check=False).strip()
        except AgentAndroidError:
            continue
        # cat 失败时会把错误信息打出来，用严格格式排除
        if _TOKEN_RE.fullmatch(out):
            return out

    try:
        # 不要加 `-t N`：实测 `-s TAG:I -t N` 会返回空
        out = adb.shell("logcat -d -s AgentBridge:I", device=device, timeout=10)
    except AgentAndroidError as exc:
        log(f"读取伴侣 App 令牌失败：{exc.message}")
        return ""
    # 取最后一条：App 每次重启都会生成新令牌
    matches = re.findall(r"TOKEN=([A-Z0-9]{4,12})", out)
    return matches[-1] if matches else ""


def dispatch(command: str, args: Any, device: str) -> dict[str, Any]:
    """把 CLI 子命令映射成伴侣 App 的动作，保持两端返回结构一致。"""
    port = getattr(args, "bridge_port", DEFAULT_PORT)
    token = getattr(args, "token", "") or os.environ.get("AGENT_ANDROID_TOKEN", "")
    if not token:
        # 自动从 logcat 取，免去手动抄 6 位令牌
        token = read_token(device)

    if command == "snapshot":
        return request(device, "/snapshot", port=port, token=token)
    if command == "tap":
        return action(device, {"action": "tap", "target": args.target},
                      port=port, token=token)
    if command == "long-press":
        return action(device, {"action": "long_press", "target": args.target},
                      port=port, token=token)
    if command == "input":
        result = action(device, {
            "action": "input",
            "text": args.text,
            "target": args.target,
        }, port=port, token=token)
        result["verify"] = (
            "重新 snapshot 确认内容；WebView 的 HTML 输入框可能被页面 JS 覆盖，"
            "遇到就改用地址栏或页面原生搜索框"
        )
        return result
    if command == "swipe":
        return action(device, {"action": "swipe", "direction": args.direction},
                      port=port, token=token)
    if command == "key":
        return action(device, {"action": "key", "name": args.name},
                      port=port, token=token)

    raise AgentAndroidError(
        f"--bridge 模式不支持 `{command}`",
        "bridge 仅支持 snapshot / tap / long-press / input / swipe / key；"
        "install / launch / screenshot / logcat 请去掉 --bridge 走 adb",
    )
