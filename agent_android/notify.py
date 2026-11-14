"""通知栏操作：下拉、收起、读取、点击。

很多流程会卡在通知上（验证码、下载完成、权限提醒），而通知不属于任何 App
的窗口层级，必须先把 shade 拉下来才能被 snapshot 看到。
"""

from __future__ import annotations

import time
from typing import Any

from . import adb, snapshot
from .util import AgentAndroidError, ok


def shade(device: str) -> dict[str, Any]:
    """下拉通知栏。"""
    out = adb.shell("cmd statusbar expand-notifications", device=device,
                    timeout=10, check=False)
    if "Error" in out or "Exception" in out:
        raise AgentAndroidError(
            "无法下拉通知栏",
            out[:200],
            code="shade_failed", retryable=True,
            actions=["adb shell cmd statusbar expand-notifications",
                     "agent-android swipe down  # 坐标滑动兜底"],
        )
    time.sleep(0.5)
    return ok(device=device, action="notify.shade", next="snapshot 查看通知内容")


def collapse(device: str) -> dict[str, Any]:
    """收起通知栏。"""
    adb.shell("cmd statusbar collapse", device=device, timeout=10, check=False)
    time.sleep(0.4)
    return ok(device=device, action="notify.collapse")


def list_visible(device: str, *, limit: int = 8, keep_open: bool = False) -> dict[str, Any]:
    """拉下通知栏，读出当前可见的文字。

    不用 dumpsys notification 解析：那玩意儿格式随版本变，而且拿到的是
    「系统里存在的通知」而不是「用户看到的通知」。界面上的才是 Agent 要处理的。
    """
    shade(device)
    try:
        data = snapshot.take(device)
    finally:
        if not keep_open:
            collapse(device)

    texts: list[str] = []
    for node in data["nodes"]:
        text = (node.get("text") or "").strip()
        if text and text not in texts:
            texts.append(text)

    return ok(
        device=device,
        action="notify.list",
        count=len(texts),
        visible=texts[:limit],
        package=data["package"],
        hint="要点击某条通知，用 notify tap <序号>，或直接 tap text=<文字>",
    )


def tap(device: str, index: int = 1) -> dict[str, Any]:
    """点击通知栏里第 index 个可点击的通知卡片（按从上到下的顺序）。

    下拉栏里前几项常常是快捷开关（WLAN / 蓝牙 / 亮度），所以只挑
    「有文字且高度足够」的可点击节点，避免误触开关。
    """
    shade(device)
    time.sleep(0.2)
    data = snapshot.take(device)

    candidates = [
        node for node in data["nodes"]
        if node["clickable"] and (node.get("text") or "").strip()
        and (node["bounds"][3] - node["bounds"][1]) >= 60
        and node["bounds"][2] - node["bounds"][0] >= data["screen"][0] // 3
    ]
    candidates.sort(key=lambda n: (n["bounds"][1], n["bounds"][0]))

    if not candidates:
        collapse(device)
        raise AgentAndroidError(
            "通知栏里没有可点击的通知",
            "可能没有未读通知，或通知已被系统折叠",
            code="element_not_found", retryable=False,
            actions=["agent-android notify list  # 先看看有什么"],
        )
    if index < 1 or index > len(candidates):
        collapse(device)
        raise AgentAndroidError(
            f"通知序号超出范围：{index}",
            f"当前只有 {len(candidates)} 条可点通知",
            code="element_index_out_of_range", retryable=False,
            actions=["agent-android notify list"],
        )

    target = candidates[index - 1]
    from . import actions

    result = actions.tap(target["id"], device=device)
    result["notify_index"] = index
    result["text"] = target.get("text", "")
    return result
