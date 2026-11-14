"""通用工具：错误类型、JSON 输出、日志与快照缓存。"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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


LOCK_TTL = 120  # 秒；持有者崩溃后留下的锁超过这个时间会被回收


@contextmanager
def device_lock(device: str, *, wait: float = 0) -> Iterator[None]:
    """同一台设备的操作串行化，避免两个 Agent 互相打断。

    场景：A 刚点开菜单，B 的 snapshot 把界面刷走，A 的下一次 tap 就点错了。
    用 O_CREAT|O_EXCL 的锁文件实现；持有者崩溃留下的锁超过 LOCK_TTL 自动回收。

    wait=0 表示不排队，抢不到立刻报 device_locked。
    """
    # Windows 的文件名不允许 < > : " / \ | ? *，设备串号里最常见的是冒号
    safe = re.sub(r'[<>:"/\\|?*]', "_", device)
    path = cache_dir() / f".lock_{safe}"
    deadline = time.time() + max(0.0, wait)
    handle: int | None = None

    while True:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(handle, f"{os.getpid()}\n".encode())
            except OSError:
                # 写入 pid 只为排障用；失败也不该让锁失效或泄漏 fd
                pass
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > LOCK_TTL:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass

            if time.time() >= deadline:
                raise AgentAndroidError(
                    f"设备 {device} 正被另一个进程占用",
                    f"锁文件：{path}。可排队等待，或确认没有别的 Agent 后删除它",
                    code="device_locked", retryable=True,
                    actions=[
                        "agent-android <命令> --lock 30  # 最多排队 30 秒",
                        f"rm '{path}'  # 确认对方已退出后强制解锁",
                    ],
                )
            time.sleep(0.2)

    try:
        yield
    finally:
        if handle is not None:
            os.close(handle)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


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
