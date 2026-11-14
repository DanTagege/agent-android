"""模拟器适配表与自动发现。

所有主流 Android 模拟器都实现标准 adb 协议，所以本项目天然通用；
真正的差异只有三处：
  1. ADB 默认是关的（BlueStacks 必须手动打开）；
  2. 监听端口各不相同，多开还要按公式递增；
  3. 部分精简版/游戏版裁掉了 uiautomator，导致 dump 失败。

第 3 种情况正好由仓库里的 android/ 伴侣 App 兜底（走无障碍服务，不依赖 uiautomator）。
"""

from __future__ import annotations

import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Emulator:
    name: str
    port: int
    note: str


# 端口来源：各模拟器官方的 ADB/命令行文档与实测汇总。
# 同一端口可能被多个产品占用（5555 尤其常见），identify() 会列出全部可能。
KNOWN_EMULATORS: tuple[Emulator, ...] = (
    Emulator("MuMu 12", 16384, "网易 MuMu 12 默认端口，也是本仓库主测环境"),
    Emulator("MuMu 6", 7555, "旧版 MuMu / 部分 12 的兼容端口"),
    Emulator("LDPlayer", 5555, "雷电 9/14 实例 0；多开 5555 + index*2"),
    Emulator("LDPlayer", 5554, "部分版本在 adb devices 里显示为 emulator-5554"),
    Emulator("Android AVD", 5554, "Android Studio 自带模拟器，串号与雷电同为 emulator-5554，需看 ro.product.model 区分"),
    Emulator("Nox", 62001, "夜神首实例；多开依次 62025、62026…"),
    Emulator("MEmu", 21503, "逍遥安卓默认端口"),
    Emulator("BlueStacks 5", 5555, "需在 设置 > 高级 打开 Android Debug Bridge"),
    Emulator("BlueStacks 5", 5625, "多开实例的端口不固定，以设置页显示为准"),
    Emulator("WSA", 58526, "Windows 安卓子系统；微软已停止支持，仅遗留环境可用"),
    Emulator("Genymotion", 5555, "桌面版通常是 192.168.x.x:5555，SaaS 用远程 IP"),
    Emulator("TT", 6555, "天天模拟器"),
    Emulator("Haimawan", 53001, "海马玩模拟器"),
)


def port_of(serial: str) -> int | None:
    """从 `127.0.0.1:16384` / `emulator-5554` 这样的串号里取出端口。"""
    if ":" in serial:
        _, _, tail = serial.rpartition(":")
        return int(tail) if tail.isdigit() else None
    if serial.startswith("emulator-"):
        tail = serial.removeprefix("emulator-")
        return int(tail) if tail.isdigit() else None
    return None


def identify(serial: str) -> str | None:
    """尽力判断串号属于哪个模拟器，用于 health 输出和排错提示。"""
    port = port_of(serial)
    if port is None:
        return None
    names = [e.name for e in KNOWN_EMULATORS if e.port == port]
    if not names:
        return None
    # 去重但保持顺序（5555 会同时命中雷电和 BlueStacks）
    unique = list(dict.fromkeys(names))
    return " / ".join(unique)


def _port_open(port: int, timeout: float = 0.2) -> bool:
    """先用 TCP 探测端口，比逐个 adb connect 快得多（后者每次约 1s）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(timeout: float = 0.2) -> list[dict[str, object]]:
    """扫描已知端口，返回当前处于监听状态的模拟器。

    只探测回环地址：模拟器的 ADB 默认只绑定本机，扫远程地址既慢也没有意义。
    """
    found: list[dict[str, object]] = []
    seen: set[int] = set()
    for emulator in KNOWN_EMULATORS:
        if emulator.port in seen:
            continue
        seen.add(emulator.port)
        if _port_open(emulator.port, timeout):
            found.append({
                "address": f"127.0.0.1:{emulator.port}",
                "emulator": emulator.name,
                "note": emulator.note,
            })
    return found


def catalog() -> list[dict[str, object]]:
    """输出完整端口表，供 `connect --list` 使用。"""
    return [
        {"emulator": e.name, "address": f"127.0.0.1:{e.port}", "note": e.note}
        for e in KNOWN_EMULATORS
    ]
