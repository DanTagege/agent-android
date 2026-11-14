"""环境接入引导：没有模拟器、也没有真机时，告诉 Agent 下一步该做什么。

和单纯报错的区别：
  - 先判断现在处于哪种情况（没装 / 装了没开 / 插了没授权 / 授权了但离线）；
  - 再按情况给出唯一推荐路径，而不是把一堆选项丢给人；
  - 步骤是可执行的命令 + 手机上要点哪里，Agent 能直接转述；
  - `markdown` 字段可以直接贴给用户，不用二次加工。
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from . import adb

IS_WINDOWS = os.name == "nt"

# 模拟器清单：只收录「ADB 开箱可用」或「明确知道怎么开」的，避免推荐完还要排错
EMULATORS: tuple[dict[str, Any], ...] = (
    {
        "name": "MuMu 12",
        "vendor": "网易",
        "port": 16384,
        "paths": [r"C:\Program Files\Netease\MuMu", r"D:\Program Files\Netease\MuMu"],
        "process": "MuMuNxMain.exe",
        "download": "https://mumu.163.com/",
        "adb_default": True,
        "why": "轻量、启动快、ADB 默认开启，开箱即用；自动化场景最省事",
        "note": "默认横屏，agent-android 已自动适配旋转",
    },
    {
        "name": "雷电 9 / LDPlayer",
        "vendor": "雷电",
        "port": 5555,
        "paths": [r"C:\LDPlayer", r"D:\LDPlayer", r"C:\Program Files\LDPlayer"],
        "process": "dnplayer.exe",
        "download": "https://www.ldmnq.com/",
        "adb_default": True,
        "why": "兼容性好、多开强；多开端口 5555 + index*2",
        "note": "同机同时开雷电 9 和雷电 14 的实例 0 会抢 5555",
    },
    {
        "name": "BlueStacks 5",
        "vendor": "BlueStacks",
        "port": 5555,
        "paths": [r"C:\Program Files\BlueStacks_nxt", r"C:\Program Files\BlueStacks"],
        "process": "HD-Player.exe",
        "download": "https://www.bluestacks.com/",
        "adb_default": False,
        "why": "生态成熟、兼容性好，适合跑大型 App",
        "note": "ADB 默认关闭：设置 > 高级 > 打开 Android Debug Bridge，端口以设置页为准",
    },
    {
        "name": "Android Studio AVD",
        "vendor": "Google",
        "port": 5554,
        "paths": [r"C:\Program Files\Android\Android Studio"],
        "process": "qemu-system-x86_64.exe",
        "download": "https://developer.android.com/studio",
        "adb_default": True,
        "why": "官方模拟器，最干净、最接近真机；串号自动出现，无需 connect",
        "note": "占用内存较大（建议 8GB 以上），首次要下载系统镜像",
    },
    {
        "name": "Genymotion",
        "vendor": "Genymotion",
        "port": 5555,
        "paths": [r"C:\Program Files\Genymobile\Genymotion"],
        "process": "genymotion.exe",
        "download": "https://www.genymotion.com/",
        "adb_default": True,
        "why": "启动快、适合 CI；桌面版需要先有 VirtualBox",
        "note": "桌面版是 192.168.x.x:5555，要用虚拟机网络地址连接",
    },
)


def _installed(item: dict[str, Any]) -> str | None:
    """返回安装目录；只做路径探测，不读注册表，避免权限问题。"""
    for raw in item.get("paths", []):
        path = Path(raw)
        if path.exists():
            return str(path)
    return None


def _running(process_name: str) -> bool:
    try:
        if IS_WINDOWS:
            proc = subprocess.run(["tasklist"], capture_output=True, timeout=8, check=False)
            return process_name.lower() in proc.stdout.decode("utf-8", "replace").lower()
        proc = subprocess.run(["pgrep", "-f", process_name], capture_output=True,
                              timeout=5, check=False)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def inspect_emulators() -> list[dict[str, Any]]:
    """本机装了哪些模拟器、有没有在跑。"""
    result: list[dict[str, Any]] = []
    for item in EMULATORS:
        path = _installed(item)
        running = _running(item["process"])
        if path or running:
            result.append({
                "name": item["name"],
                "port": item["port"],
                "installed_at": path,
                "running": running,
                "adb_default": item["adb_default"],
                "note": item["note"],
            })
    return result


def inspect_devices() -> dict[str, Any]:
    """区分「USB 真机」与「模拟器串号」，并标出未授权/离线。"""
    try:
        devices = adb.devices()
    except Exception:  # adb 都不可用时不要在这里炸，交给上层
        return {"all": [], "usb": [], "emulator": [], "unauthorized": [], "offline": []}

    usb = [d for d in devices
           if ":" not in d["serial"] and not d["serial"].startswith("emulator-")]
    emulator = [d for d in devices if d not in usb]
    return {
        "all": devices,
        "usb": usb,
        "emulator": emulator,
        "unauthorized": [d for d in devices if d["state"] == "unauthorized"],
        "offline": [d for d in devices if d["state"] == "offline"],
    }


def classify(devices: dict[str, Any],
             emulators_installed: list[dict[str, Any]]) -> tuple[str, str]:
    """判断当前处境，并给出唯一推荐路径。

    返回值：(situation, recommended_path)
    situation 是稳定枚举，Agent 可以直接分支；recommended_path 只给一个，避免选择困难。
    """
    if any(d["state"] == "device" for d in devices["all"]):
        return "ready", "none"
    if devices["unauthorized"]:
        return "usb_unauthorized", "authorize_usb"

    # offline 要分两类：真机该拔插，模拟器该重启/重连 —— 否则会给出错误指引
    usb_serials = {d["serial"] for d in devices["usb"]}
    usb_offline = [d for d in devices["offline"] if d["serial"] in usb_serials]
    emulator_offline = [d for d in devices["offline"] if d["serial"] not in usb_serials]
    if usb_offline:
        return "usb_offline", "replug_usb"
    if emulator_offline:
        return "emulator_offline", "restart_emulator"
    if any(e["running"] for e in emulators_installed):
        return "emulator_starting", "wait_emulator"
    if emulators_installed:
        return "emulator_installed_not_running", "start_emulator"
    return "no_device", "install_emulator"


def _real_device_steps() -> list[str]:
    steps = [
        "手机上打开「开发者选项」：设置 → 关于手机 → 连续点 7 次「版本号」",
        "进入「开发者选项」→ 打开「USB 调试」",
        "用数据线连接电脑，手机上的 USB 用途选择「文件传输 / MTP」（不要选「仅充电」）",
        "手机弹出「允许 USB 调试吗？」→ 勾选「一律允许」→ 允许",
        "在电脑上验证：adb devices 应显示 <串号> device",
    ]
    if IS_WINDOWS:
        steps.append(
            "如果电脑完全看不到设备：换一根能传数据的线、换 USB 口；"
            "小米/华为/OPPO/vivo 需装厂商 USB 驱动，并在开发者选项里打开"
            "「USB 调试（安全设置）」"
        )
    return steps


def _emulator_steps(item: dict[str, Any] | None) -> list[str]:
    if item is None:
        return [
            "从下表挑一个模拟器下载安装（Windows 首选 MuMu 12，开箱即用）",
            "启动模拟器，等它进入桌面",
        ]
    steps = [f"启动 {item['name']}（已安装在 {item.get('installed_at') or '本机'}）"]
    if not item.get("adb_default"):
        steps.append(f"打开它的 ADB 开关：{item['note']}")
    steps += [
        f"确认端口 {item['port']} 在监听，或直接运行 agent-android connect 自动扫描",
        "验证：agent-android devices 能看到设备",
    ]
    return steps


def guide() -> dict[str, Any]:
    """生成完整的接入指南（JSON + 可直接转述的 markdown）。"""
    devices = inspect_devices()
    installed = inspect_emulators()
    situation, recommended = classify(devices, installed)

    paths: list[dict[str, Any]] = []

    # 路径 A：真机 USB
    paths.append({
        "id": "real_device_usb",
        "title": "用 USB 连接真机（最真实，推荐用于最终验证）",
        "steps": _real_device_steps(),
        "verify": ["adb devices", "agent-android devices", "agent-android health"],
        "troubleshoot": [
            "显示 unauthorized：手机上点「允许」，或在开发者选项里「撤销 USB 调试授权」后重插",
            "显示 offline：换线/换口，或 adb kill-server 后重插",
            "列表为空：多半是线只能充电，或缺少厂商驱动",
        ],
    })

    # 路径 B：无线调试（Android 11+）
    paths.append({
        "id": "wireless",
        "title": "无线调试（手机和电脑在同一 Wi-Fi）",
        "steps": [
            "手机：开发者选项 → 无线调试 → 使用配对码配对",
            "在电脑执行：adb pair <手机IP>:<配对端口>，输入手机上的 6 位配对码",
            "再执行：adb connect <手机IP>:<调试端口>",
            "验证：agent-android devices",
        ],
        "verify": ["adb devices", "agent-android devices"],
        "note": "端口每次开关无线调试都会变，以手机页面显示为准",
    })

    # 路径 C：模拟器
    options = []
    for item in EMULATORS:
        installed_path = _installed(item)
        options.append({
            "name": item["name"],
            "vendor": item["vendor"],
            "download": item["download"],
            "port": item["port"],
            "adb_default": item["adb_default"],
            "why": item["why"],
            "note": item["note"],
            "installed": bool(installed_path),
            "installed_at": installed_path,
        })
    paths.append({
        "id": "emulator",
        "title": "用模拟器（没有真机时首选）",
        "steps": _emulator_steps(
            next((e for e in installed if e["running"]), None)
            or (installed[0] if installed else None)
        ),
        "options": options,
        "verify": ["agent-android connect", "agent-android devices"],
    })

    # 唯一推荐路径
    if recommended == "install_emulator":
        recommended_id = "emulator"
    elif recommended in ("start_emulator", "wait_emulator"):
        recommended_id = "emulator"
    else:
        recommended_id = "real_device_usb"

    markdown = _to_markdown(situation, recommended, installed, devices)

    return {
        "situation": situation,
        "recommended_path": recommended_id,
        "os": platform.system().lower(),
        "detected": {
            "emulators": installed,
            "usb_devices": devices["usb"],
            "unauthorized": devices["unauthorized"],
            "offline": devices["offline"],
        },
        "paths": paths,
        "next_command": "agent-android connect" if recommended_id == "emulator"
                        else "agent-android devices",
        "markdown": markdown,
    }


def _to_markdown(situation: str, recommended: str,
                 installed: list[dict[str, Any]], devices: dict[str, Any]) -> str:
    """生成一段可以直接贴给用户的中文说明。"""
    reason = {
        "no_device": "没有检测到已连接的手机，也没有安装任何模拟器。",
        "emulator_installed_not_running": "检测到本机安装了模拟器，但当前没有运行。",
        "emulator_starting": "模拟器进程已启动，但 ADB 还没就绪（通常再等几秒）。",
        "usb_unauthorized": "手机已插上，但还没有在手机上允许 USB 调试。",
        "usb_offline": "手机插着，但 adb 状态是 offline。",
        "emulator_offline": "模拟器的 adb 连接处于 offline（通常是端口残留或模拟器还没初始化完）。",
        "ready": "已经有可用设备了。",
    }.get(situation, "当前没有可用设备。")

    lines = ["## 还没有可用的 Android 设备", "", f"**现状**：{reason}", ""]

    if installed:
        names = ", ".join(e["name"] + ("（运行中）" if e["running"] else "") for e in installed)
        lines.append(f"**本机已安装**：{names}")
        lines.append("")

    if recommended in ("install_emulator", "start_emulator", "wait_emulator"):
        lines += [
            "**建议先走模拟器这条路**：",
            "",
            "1. Windows 首选 **MuMu 12**（https://mumu.163.com/），ADB 默认开启，"
            "装完启动就能用",
            "2. 备选 **雷电 9**（https://www.ldmnq.com/），多开方便，默认端口 5555",
            "3. 想要最接近真机就用 **Android Studio AVD**"
            "（https://developer.android.com/studio），占内存但最干净",
            "",
            "装好并启动后执行：",
            "",
            "```bash",
            "agent-android connect",
            "agent-android devices",
            "```",
        ]
    elif recommended == "authorize_usb":
        lines += [
            "**手机已经插上了，只差授权**：",
            "",
            "1. 看手机屏幕，应该有一个「允许 USB 调试吗？」的弹窗",
            "2. 勾选「一律允许此计算机」→ 点「允许」",
            "3. 没看到弹窗：开发者选项 → 撤销 USB 调试授权 → 拔插数据线",
            "",
            "完成后执行 `agent-android devices` 确认状态变成 `device`。",
        ]
    elif recommended == "replug_usb":
        lines += [
            "**手机在，但 adb 状态是 offline**：",
            "",
            "1. 拔掉数据线重插（换一个 USB 口）",
            "2. 手机 USB 用途改成「文件传输 / MTP」",
            "3. 还不行：`adb kill-server` 后重试",
        ]
    elif recommended == "restart_emulator":
        lines += [
            "**模拟器的 adb 连接是 offline**（不是手机，不用拔线）：",
            "",
            "1. 直接跑 `agent-android recover` —— 会重启 adb server 并重新扫描端口",
            "2. 还不行就重启模拟器，等它进桌面后再 `agent-android connect`",
            "3. 如果同时装了两个版本的 adb（模拟器自带 + platform-tools），"
            "统一用其中一个（设置 `AGENT_ANDROID_ADB`）",
        ]
    else:
        lines += [
            "**建议先接真机**（最终验证更可信）：",
            "",
            "1. 设置 → 关于手机 → 连点 7 次「版本号」打开开发者选项",
            "2. 开发者选项 → 打开「USB 调试」",
            "3. 数据线连接电脑，USB 用途选「文件传输」",
            "4. 手机上允许 USB 调试",
            "",
            "完成后执行 `agent-android devices` 确认。",
        ]

    lines += [
        "",
        "任何一步卡住，执行 `agent-android doctor` 拿到完整诊断，"
        "或 `agent-android setup` 重新获取这份指南。",
    ]
    return "\n".join(lines)
