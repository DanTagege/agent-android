"""通用工具：错误类型、JSON 输出、日志与快照缓存。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 所有命令失败时抛这个异常，由 cli.py 统一转成 JSON 错误 + 非零退出码
class AgentAndroidError(RuntimeError):
    """可被 Agent 直接理解的业务错误。

    除了人能读的 message/hint，还带三个机器可用的字段：
      code       稳定的错误码，Agent 可以据此分支处理（不要匹配文案）
      retryable  原样重试是否有意义
      actions    按顺序可尝试的修复命令，Agent 可以直接执行
    """

    def __init__(self, message: str, hint: str = "", *,
                 code: str = "unknown", retryable: bool = False,
                 actions: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        self.retryable = retryable
        self.actions = actions or []


def ok(**payload: Any) -> dict[str, Any]:
    """构造成功响应。所有命令的 stdout 都只有这一个 JSON 对象。"""
    return {"ok": True, "ts": round(time.time(), 3), **payload}


def fail(message: str, hint: str = "", *, code: str = "unknown",
         retryable: bool = False, actions: list[str] | None = None) -> dict[str, Any]:
    """构造失败响应，字段与异常保持一致，方便 Agent 统一处理。"""
    return {
        "ok": False,
        "ts": round(time.time(), 3),
        "error": message,
        "error_code": code,
        "retryable": retryable,
        "hint": hint,
        "actions": actions or [],
    }


def emit(payload: dict[str, Any], pretty: bool = False) -> None:
    """把响应写到 stdout。日志一律走 stderr，保证 stdout 是纯 JSON。"""
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def log(message: str) -> None:
    """诊断信息写 stderr，避免污染机器可读的 stdout。"""
    print(f"[agent-android] {message}", file=sys.stderr)


def cache_dir() -> Path:
    """快照缓存目录。上一次 snapshot 的结果会被 tap/input 用来定位元素。"""
    root = os.environ.get("AGENT_ANDROID_HOME")
    path = Path(root) if root else Path.home() / ".agent_android"
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_path(device: str) -> Path:
    """按设备分文件，多设备同时操作时互不干扰。"""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in device)
    return cache_dir() / f"snapshot_{safe}.json"


def has_snapshot(device: str) -> bool:
    """HTTP 模式用它判断是否需要先补一次快照。"""
    return snapshot_path(device).exists()


def save_snapshot(device: str, data: dict[str, Any]) -> Path:
    """保存最近一次快照，供后续按 id 点击时查询。

    先写临时文件再 os.replace：HTTP 服务是多线程的，直接 write_text 会让
    并发读拿到半截 JSON（json.loads 抛 JSONDecodeError，表现为 500）。
    """
    path = snapshot_path(device)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_snapshot(device: str) -> dict[str, Any]:
    """读取最近一次快照；不存在时给出可操作的提示。"""
    path = snapshot_path(device)
    if not path.exists():
        raise AgentAndroidError(
            "没有可用的屏幕快照",
            "请先执行 `agent-android snapshot`，或用 `tap x,y` / `tap text=登录` 直接定位",
            code="snapshot_missing", retryable=True,
            actions=["agent-android snapshot", "重跑原命令"],
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        # 缓存损坏（磁盘写满、进程被杀在写一半）不该以 traceback 形式抛出
        raise AgentAndroidError(
            "快照缓存损坏，需要重新抓取",
            "执行一次 `agent-android snapshot` 即可覆盖",
            code="snapshot_corrupt", retryable=True,
            actions=["agent-android snapshot", "重跑原命令"],
        ) from exc
