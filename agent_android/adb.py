"""adb 封装层：设备发现、命令执行、文件路径处理。

Android Debug Bridge 是 PC 与手机/模拟器之间唯一的通道，本模块把
「拼命令、处理超时、解码输出」这些脏活集中起来，上层只关心语义。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from typing import Iterable

from . import emulators
from .util import AgentAndroidError, log

DEFAULT_TIMEOUT = 20


def dump_path() -> str:
    """每次快照用独立文件名。

    固定路径 /sdcard/window.xml 在并发下会互相踩踏：A 的 rm 删掉 B 刚 dump
    的文件，B 的 cat 就读到空内容，表现为「设备返回空层级」的假故障。
    """
    return f"/sdcard/_agent_android_{uuid.uuid4().hex}.xml"


def find_adb() -> str:
    """按优先级定位 adb：显式环境变量 → ANDROID_HOME/SDK → PATH。"""
    explicit = os.environ.get("AGENT_ANDROID_ADB")
    if explicit:
        if not os.path.isfile(explicit):
            raise AgentAndroidError(
                f"AGENT_ANDROID_ADB 指向的文件不存在: {explicit}",
                "检查环境变量，或改用 ANDROID_HOME 指向 Android SDK 根目录",
                code="adb_not_found", retryable=False,
                actions=[
                    "检查 AGENT_ANDROID_ADB 路径是否正确",
                    "agent-android doctor  # 看本机实际有哪些 adb",
                ],
            )
        return explicit

    for env_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env_name)
        if root:
            name = "adb.exe" if os.name == "nt" else "adb"
            candidate = os.path.join(root, "platform-tools", name)
            if os.path.isfile(candidate):
                return candidate

    found = shutil.which("adb")
    if found:
        return found
    raise AgentAndroidError(
        "找不到 adb",
        "安装 Android SDK platform-tools 后，设置 ANDROID_HOME，"
        "或用 AGENT_ANDROID_ADB 直接指定 adb 可执行文件",
        code="adb_not_found", retryable=False,
        actions=[
            "下载 platform-tools: https://developer.android.com/tools/releases/platform-tools",
            "解压后设置 AGENT_ANDROID_ADB=<解压目录>/adb.exe",
            "agent-android doctor  # 确认已经能找到 adb",
        ],
    )


# 注意用正则：真实错误是 `device '127.0.0.1:16384' not found`，
# 中间插了串号，用固定子串 "device not found" 永远匹配不上，
# 自动重连就永远不会触发。
_STALE_PATTERNS = (
    re.compile(r"device\s+.*?not found"),
    re.compile(r"device\s+offline"),
    re.compile(r"no devices/emulators found"),
    re.compile(r"device\s+still connecting"),
    re.compile(r"device\s+unauthorized"),
)

# adb server 本身连不上（端口被占 / server 没起），和设备无关
_SERVER_PATTERNS = (
    re.compile(r"cannot connect to daemon"),
    re.compile(r"failed to (?:connect|start) the daemon"),
    re.compile(r"connection refused.*5037"),
)


def _reconnect(device: str) -> None:
    """串号失效时把连接拉回来：网络串号重连原地址，其它串号重扫端口。"""
    if ":" in device:
        try:
            run(["connect", device], timeout=15, retry=False)
        except AgentAndroidError:
            pass
    try:
        connect_known()
    except AgentAndroidError:
        pass


def run(args: Iterable[str], *, device: str | None = None,
        timeout: int = DEFAULT_TIMEOUT, binary: bool = False,
        retry: bool = True, check: bool = True) -> str | bytes:
    """执行一条 adb 命令。

    device 非空时自动插入 `-s <serial>`，多设备场景必须显式指定。
    返回 stdout（默认按文本解码）；失败时把 stderr 一并塞进异常信息。
    """
    cmd = [find_adb()]
    if device:
        cmd += ["-s", device]
    cmd += list(args)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentAndroidError(
            f"adb 命令超时（{timeout}s）: {' '.join(cmd)}",
            "设备可能卡在动画/弹窗上，重连 adb 后再试",
            code="adb_timeout", retryable=True,
            actions=["agent-android recover", "agent-android snapshot  # 重试"],
        ) from exc
    except OSError as exc:
        raise AgentAndroidError(
            f"无法启动 adb: {exc}",
            "adb 可执行文件可能被删除、被安全软件拦截，或路径含特殊字符",
            code="adb_spawn_failed", retryable=True,
            actions=["agent-android doctor  # 看 adb 路径是否有效"],
        ) from exc

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        out_text = proc.stdout.decode("utf-8", "replace").strip()
        combined = f"{err}\n{out_text}".lower()
        stale = bool(device) and any(p.search(combined) for p in _STALE_PATTERNS)

        # 端口冲突要单独成码：它和「设备失效」是两回事，
        # 换端口能立刻解决，重连设备没用。
        if any(p.search(combined) for p in _SERVER_PATTERNS):
            raise AgentAndroidError(
                "连不上 adb server（5037 端口可能被占用）",
                "常见于同时开了两个不同版本的 adb，或被安全软件拦截",
                code="adb_server_conflict", retryable=True,
                actions=[
                    "adb kill-server",
                    "设置 ADB_SERVER_PORT=5038 后重试（Windows: set / macOS: export）",
                    "netstat -ano | findstr :5037  # 看谁占用了端口",
                ],
            )

        # 串号失效是最常见的中断：adb server 重启、模拟器端口重连、拔插数据线。
        # 在底层统一做「重连 + 换当前在线串号 + 重试一次」，所有命令都受益。
        if stale and retry:
            log(f"设备 {device} 已失效，尝试重连后重试…")
            _reconnect(device)
            target = device
            try:
                fresh = devices()
                if not any(d["serial"] == device and d["state"] == "device" for d in fresh):
                    target = resolve_device(None)
            except AgentAndroidError:
                pass
            return run(args, device=target, timeout=timeout, binary=binary,
                       retry=False, check=check)

        if check:
            raise AgentAndroidError(
                f"adb 命令失败 ({proc.returncode}): {' '.join(cmd)}",
                err or out_text or "检查设备是否已授权 USB 调试",
                code="adb_command_failed", retryable=bool(stale),
                actions=["agent-android recover"] if stale else [],
            )
        # check=False：设备端命令（monkey / cmd package 等）成功时也可能返回非零，
        # 调用方明确表示「以输出内容为准」，把 stdout 交回去让它自己判断。
        return proc.stdout if binary else out_text

    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace").strip()


def devices() -> list[dict[str, str]]:
    """解析 `adb devices -l`，返回 [{serial, state, model}]。"""
    out = run(["devices", "-l"])
    result: list[dict[str, str]] = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1] if len(parts) > 1 else "unknown"
        model = ""
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
        result.append({"serial": serial, "state": state, "model": model})
    return result


def connect(address: str, *, timeout: int = 15) -> dict[str, object]:
    """连接网络调试端口（模拟器、WSA、Genymotion Cloud 都走这条路）。

    注意：`adb connect` 失败时退出码通常仍是 0，只能靠输出文本判断。
    """
    out = run(["connect", address], timeout=timeout)
    connected = "connected to" in out or "already connected" in out
    return {
        "address": address,
        "connected": connected,
        "emulator": emulators.identify(address),
        "output": out,
        "hint": "" if connected else (
            "确认模拟器已启动、ADB 开关已打开；BlueStacks 需在 设置 > 高级 手动启用"
        ),
    }


def connect_known(timeout: float = 0.2) -> list[dict[str, object]]:
    """并发连接已探测到的模拟器端口，返回成功连上的列表。

    串行 connect 三个端口要 1-3 秒，是冷启动里最明显的一段开销。
    """
    candidates = emulators.probe(timeout)
    if not candidates:
        return []

    def attempt(item: dict[str, object]) -> dict[str, object] | None:
        try:
            info = connect(str(item["address"]))
        except AgentAndroidError:
            return None
        return {**item, "connected": True} if info["connected"] else None

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
        results = list(pool.map(attempt, candidates))
    return [item for item in results if item]


def resources(device: str) -> dict[str, object]:
    """一次往返读取电量和存储余量。

    存储耗尽时 `uiautomator dump` 写 /sdcard 会失败，但报错很隐晦（看起来像
    uiautomator 坏了）。提前体检能避免朝着错误方向排障。
    """
    try:
        out = shell(
            "dumpsys battery | grep -E 'level|status'; df /sdcard | tail -1",
            device=device, timeout=8,
        )
    except AgentAndroidError:
        return {"battery": None, "storage_free_mb": None}
    return _parse_resources(out)


def _parse_resources(text: str) -> dict[str, object]:
    """解析 dumpsys battery + df 的输出（纯函数，便于单测）。"""
    battery: int | None = None
    match = re.search(r"level:\s*(\d+)", text)
    if match:
        battery = int(match.group(1))

    free_mb: int | None = None
    for line in text.splitlines():
        parts = line.split()
        # toybox df: Filesystem 1K-blocks Used Available Use% Mounted
        if len(parts) >= 4 and parts[0].startswith("/dev") and parts[3].isdigit():
            free_mb = int(parts[3]) // 1024
    return {"battery": battery, "storage_free_mb": free_mb}


def ime_visible(device: str) -> bool:
    """输入法键盘是否正在显示。

    键盘是独立窗口，通常**不在 uiautomator 的 UI 树里**，但它挡住了下半屏。
    不检测的话，Agent 会对着被遮住的区域点，表现为「点了没反应」。
    """
    try:
        out = shell("dumpsys input_method | grep mInputShown", device=device, timeout=6)
    except AgentAndroidError:
        return False
    return "mInputShown=true" in out


def android_id(serial: str) -> str:
    """读取设备唯一标识，用于判断多个串号是不是同一台设备。"""
    try:
        return shell("settings get secure android_id", device=serial, timeout=5).strip()
    except AgentAndroidError:
        return ""


def ensure_online() -> None:
    """没有在线设备时，自动扫一遍常见模拟器端口。

    背景：部分环境（含模拟器自带 adb 与 platform-tools 混用）会让 adb server
    随上一条命令结束被清理，设备列表因此每次都是空的 —— 把重连自动化掉。
    """
    if os.environ.get("AGENT_ANDROID_NO_AUTOCONNECT"):
        return
    if any(d["state"] == "device" for d in devices()):
        return
    log("没有在线设备，自动扫描常见模拟器端口…")
    try:
        connect_known()
    except AgentAndroidError:
        pass


def resolve_device(requested: str | None = None) -> str:
    """确定目标设备：显式指定 → 环境变量 → 唯一在线设备。

    多设备且未指定时直接报错，绝不自作主张挑一台（避免 Agent 误操作真机）。
    """
    if requested:
        return requested
    env = os.environ.get("ANDROID_SERIAL")
    if env:
        return env
    ensure_online()
    all_devices = devices()
    online = [d["serial"] for d in all_devices if d["state"] == "device"]

    if not online:
        # 有设备但状态不对（offline / unauthorized）时，直接给对症的修法
        stuck = [f"{d['serial']}({d['state']})" for d in all_devices]
        if stuck:
            hint = (f"检测到 {', '.join(stuck)}，尝试 `adb kill-server` 后重新 connect；"
                    "若同时装了多个 adb（如模拟器自带），统一用一个版本")
        else:
            hint = ("没有检测到任何模拟器或真机。运行 `agent-android setup` "
                    "获取「用 USB 接真机」或「装哪个模拟器」的具体步骤")
        raise AgentAndroidError(
            "没有在线的 Android 设备", hint,
            code="device_offline", retryable=True,
            actions=[
                "agent-android setup  # 先看要接真机还是装模拟器",
                "agent-android recover",
                "agent-android connect",
            ],
        )

    if len(online) > 1:
        # MuMu 会同时暴露 16384 / 7555 / emulator-5554，三个串号指向同一台设备。
        # 用 android_id 判断是否同一台，避免把「一台设备」误报成「多台设备」。
        groups: dict[str, list[str]] = {}
        for serial in online:
            groups.setdefault(android_id(serial) or serial, []).append(serial)
        if len(groups) == 1:
            # 优先选 127.0.0.1:port 形式的串号：它对应明确的模拟器端口，
            # 识别更准（emulator-5554 会被同时认成 AVD 和雷电），也更稳定。
            preferred = next(
                (s for s in online if s.startswith("127.0.0.1:")), online[0]
            )
            log(f"{len(online)} 个串号指向同一台设备，使用 {preferred}")
            return preferred
        raise AgentAndroidError(
            f"检测到多台设备，必须指定: {', '.join(online)}",
            "使用 --device <serial> 或设置 ANDROID_SERIAL",
            code="multiple_devices", retryable=False,
            actions=["agent-android devices", "重跑原命令并追加 --device <serial>"],
        )
    return online[0]


def shell(command: str, *, device: str, timeout: int = DEFAULT_TIMEOUT,
          check: bool = True) -> str:
    """在设备上执行 shell 命令（等价于 adb shell <command>）。

    check=False 时忽略退出码、直接返回输出 —— 用于 monkey / cmd package
    这类「退出码不可靠、要以输出为准」的命令。
    """
    return run(["shell", command], device=device, timeout=timeout, check=check)


def screen_size(device: str) -> tuple[int, int]:
    """当前坐标系下的屏幕尺寸，作为 swipe 等手势的基准。

    优先取 `dumpsys window displays` 里的 cur=WxH：它**随旋转变化**。
    只用 `wm size` 会踩两个坑：模拟器常被配成横屏（物理 1080x1920，实际
    显示 1920x1080），或用户用 `wm size` 改过分辨率（Override 才是坐标系）。
    """
    try:
        out = shell("dumpsys window displays", device=device, timeout=8)
        match = re.search(r"cur=(\d+)x(\d+)", out)
        if match:
            return int(match.group(1)), int(match.group(2))
    except AgentAndroidError:
        pass

    # 退化路径：Override 优先，其次 Physical
    out = shell("wm size", device=device)
    match = re.search(r"Override size:\s*(\d+)x(\d+)", out)
    if not match:
        match = re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    if not match:
        return (1080, 1920)  # 常见竖屏兜底值
    return int(match.group(1)), int(match.group(2))


def _package_from(text: str) -> str:
    match = re.search(r"mResumedActivity.*?\s([\w.]+)/([\w.]+)", text)
    if not match:
        match = re.search(r"topResumedActivity.*?\s([\w.]+)/([\w.]+)", text)
    return match.group(1) if match else ""


def foreground_package(device: str) -> str:
    """尽力获取当前前台包名，用于让 Agent 知道「现在在哪个 App 里」。"""
    try:
        # dumpsys 在低端模拟器上可能很慢，不能让它拖垮一次 snapshot
        out = shell("dumpsys activity activities", device=device, timeout=8)
    except AgentAndroidError:
        return ""
    return _package_from(out)


_PROBE_MARK = "@@@AGENT@@@"


def probe(device: str) -> dict[str, object]:
    """一次往返拿到「屏幕尺寸 / 键盘可见性 / 前台包名」。

    这三项原本各要一次 dumpsys。加上 dump 的 4 次往返，一次 snapshot 要
    启动 7 个 adb 进程 —— 在慢模拟器上就是 3-5 秒。合并后只要 2 次，
    并且用设备端 grep 把输出压到几百字节，传输也更快。
    """
    try:
        out = shell(
            f"echo {_PROBE_MARK}W; dumpsys window displays | grep -E 'cur='; "
            f"echo {_PROBE_MARK}I; dumpsys input_method | grep mInputShown; "
            f"echo {_PROBE_MARK}A; dumpsys activity activities "
            "| grep -E 'mResumedActivity|topResumedActivity|mMultiWindowMode'",
            device=device, timeout=15,
        )
    except AgentAndroidError:
        return {"size": (0, 0), "ime": False, "package": ""}

    sections: dict[str, str] = {}
    current = ""
    for line in out.splitlines():
        if line.startswith(_PROBE_MARK):
            current = line[len(_PROBE_MARK):].strip()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"

    size = (0, 0)
    match = re.search(r"cur=(\d+)x(\d+)", sections.get("W", ""))
    if match:
        size = (int(match.group(1)), int(match.group(2)))

    return {
        "size": size,
        "ime": "mInputShown=true" in sections.get("I", ""),
        "package": _package_from(sections.get("A", "")),
        "multi_window": "mMultiWindowMode=true" in sections.get("A", ""),
    }


def health(device: str | None = None) -> dict[str, object]:
    """环境自检：adb、设备、uiautomator 是否都可用。

    注意 `uiautomator dump` 失败时退出码往往仍是 0，只能检查输出文本，
    否则会把「模拟器裁掉了 uiautomator」误报成可用。
    """
    adb_path = find_adb()
    all_devices = devices()
    # 设备都在 offline/unauthorized 时 resolve 会抛错，但 health 的职责是
    # 「报告现状」，不应该跟着失败 —— 否则最需要诊断的场景反而用不了。
    target = ""
    if all_devices:
        try:
            target = resolve_device(device)
        except AgentAndroidError:
            target = ""
    dump_ok = False
    dump_output = ""
    if target:
        probe_path = dump_path()
        try:
            dump_output = shell(f"uiautomator dump {probe_path}", device=target, timeout=30)
            dump_ok = "dumped to" in dump_output
            shell(f"rm -f {probe_path}", device=target)
        except AgentAndroidError as exc:
            dump_output = exc.message

    hint = ""
    if target and not dump_ok:
        hint = (
            "该设备/模拟器可能裁掉了 uiautomator。改用 android/ 伴侣 App 的 "
            "/snapshot（无障碍服务，不依赖 uiautomator）"
        )
    return {
        "adb": adb_path,
        "devices": all_devices,
        "selected": target,
        "emulator": emulators.identify(target) if target else None,
        "screen": screen_size(target) if target else None,
        "uiautomator": dump_ok,
        "uiautomator_output": dump_output[:200],
        "hint": hint,
    }
