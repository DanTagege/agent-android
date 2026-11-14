"""环境诊断与自动修复。

设计目标：把「出错了怎么办」从人的经验，变成 Agent 能直接执行的数据。
每个检查项都带 status + detail + fix（可执行的命令列表），
`recover` 则按安全顺序自动尝试修复，不需要人盯着。

这也是本项目不追求「适配所有模拟器」的原因：适配是有限的，
而诊断 + 降级 + 重试是通用的。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from typing import Any

from . import adb, emulators, snapshot
from .util import AgentAndroidError, cache_dir, snapshot_path


def _candidates() -> list[dict[str, str]]:
    """列出本机可能存在的多个 adb。混用不同版本是掉线的最常见原因。"""
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(path: str | None, source: str) -> None:
        if not path or path in seen or not os.path.isfile(path):
            return
        seen.add(path)
        found.append({"path": path, "source": source})

    add(os.environ.get("AGENT_ANDROID_ADB"), "AGENT_ANDROID_ADB")
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env)
        if root:
            name = "adb.exe" if os.name == "nt" else "adb"
            add(os.path.join(root, "platform-tools", name), env)
    add(shutil.which("adb"), "PATH")

    # 模拟器自带的 adb 常与 platform-tools 版本不同，两者交替启动 server 会互相 kill
    if os.name == "nt":
        for base in (r"D:\Program Files\Netease\MuMu", r"C:\Program Files\Netease\MuMu"):
            add(os.path.join(base, "nx_main", "adb.exe"), "MuMu bundled")
    return found


def _version(path: str) -> str:
    try:
        proc = subprocess.run([path, "version"], capture_output=True, timeout=8)
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("Version"):
                return line.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def doctor(device: str | None = None) -> dict[str, Any]:
    """体检并给出可执行的修复步骤。任何一项失败都不抛异常。"""
    checks: list[dict[str, Any]] = []

    # 先把设备拉起来：adb server 可能刚随上一条命令被清理，
    # 不触发一次自动连接，第一项检查就会基于过期的空设备列表。
    _safe_device(device)

    # 1) adb 可执行文件与版本
    try:
        adb_path = adb.find_adb()
        candidates = _candidates()
        versions = {item["path"]: _version(item["path"]) for item in candidates}
        distinct = {v for v in versions.values() if v and v != "unknown"}
        status = "ok" if len(distinct) <= 1 else "warn"
        checks.append({
            "name": "adb",
            "status": status,
            "detail": f"使用 {adb_path}（{versions.get(adb_path, 'unknown')}）",
            "candidates": candidates,
            "fix": [] if status == "ok" else [
                "统一只用一个 adb：设置 AGENT_ANDROID_ADB 指向模拟器自带或 platform-tools 的其中一个",
                "不要在两个终端里分别调用不同版本的 adb",
            ],
        })
    except AgentAndroidError as exc:
        checks.append({"name": "adb", "status": "fail", "detail": exc.message,
                       "fix": [exc.hint] if exc.hint else []})

    # 2) 设备列表
    try:
        devices = adb.devices()
        online = [d for d in devices if d["state"] == "device"]
        if online:
            detail = f"{len(online)} 个在线串号：" + ", ".join(
                f"{d['serial']}({emulators.identify(d['serial']) or 'unknown'})" for d in online
            )
            status = "ok"
            fix: list[str] = []
            if len(online) > 1:
                # 多个串号可能是同一台（MuMu 典型），交给 resolve_device 判断
                try:
                    chosen = adb.resolve_device(device)
                    detail += f"；实际使用 {chosen}"
                except AgentAndroidError as exc:
                    status, fix = "warn", exc.actions
        else:
            status = "fail"
            detail = "没有在线设备" + (
                "；有 " + ", ".join(f"{d['serial']}({d['state']})" for d in devices) if devices else ""
            )
            fix = ["agent-android recover", "agent-android connect"]
        checks.append({"name": "devices", "status": status, "detail": detail, "fix": fix})
    except AgentAndroidError as exc:
        checks.append({"name": "devices", "status": "fail", "detail": exc.message,
                       "fix": exc.actions or [exc.hint]})

    # 3) uiautomator 可用性（CLI 主路径依赖它）
    dump_ok = False
    dump_detail = "未检测（无在线设备）"
    try:
        target = adb.resolve_device(device)
        probe = adb.dump_path()
        out = adb.shell(f"uiautomator dump {probe}", device=target, timeout=30)
        dump_ok = "dumped to" in out
        adb.shell(f"rm -f {probe}", device=target)
        dump_detail = "可用" if dump_ok else f"不可用：{out[:120]}"
        checks.append({
            "name": "uiautomator",
            "status": "ok" if dump_ok else "warn",
            "detail": dump_detail,
            "fix": [] if dump_ok else [
                "关掉动画：adb shell settings put global window_animation_scale 0",
                "改用 agent-android --bridge snapshot（无障碍服务，不依赖 uiautomator）",
            ],
        })
    except AgentAndroidError as exc:
        checks.append({"name": "uiautomator", "status": "warn",
                       "detail": f"无法检测：{exc.message}", "fix": exc.actions or []})

    # 4) 屏幕尺寸/旋转（横屏模拟器最容易在这里翻车）
    try:
        target = adb.resolve_device(device)
        size = adb.screen_size(target)
        checks.append({
            "name": "screen",
            "status": "ok",
            "detail": f"当前坐标系 {size[0]}x{size[1]}"
                      + ("（横屏）" if size[0] > size[1] else "（竖屏）"),
            "fix": [],
        })
    except AgentAndroidError as exc:
        checks.append({"name": "screen", "status": "warn", "detail": exc.message, "fix": []})

    # 5) 快照缓存可写
    try:
        path = cache_dir()
        probe_file = path / ".write_test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        checks.append({"name": "cache", "status": "ok", "detail": str(path), "fix": []})
    except OSError as exc:
        checks.append({
            "name": "cache", "status": "fail", "detail": f"缓存目录不可写: {exc}",
            "fix": ["设置 AGENT_ANDROID_HOME 指向一个可写目录"],
        })

    # 6) 伴侣 App 是否在线（中文输入 / uiautomator 降级都靠它）
    # adb forward 会随 adb server 重启失效，探测前先补一次转发，否则会误报「未安装」
    companion = False
    try:
        forward_target = _safe_device(device)
        if forward_target:
            adb.run(["forward", "tcp:8377", "tcp:8377"], device=forward_target, timeout=10)
    except AgentAndroidError:
        pass
    companion = _port_open(8377)
    checks.append({
        "name": "companion",
        "status": "ok" if companion else "info",
        "detail": "127.0.0.1:8377 可连接" if companion
                  else "未检测到伴侣 App（需要中文输入时再启用）",
        "fix": [] if companion else [
            "安装 android/ 下的 APK 并开启无障碍服务",
            "adb forward tcp:8377 tcp:8377",
        ],
    })

    # 7) 电量与存储：存储耗尽会让 uiautomator dump 静默失败
    try:
        target = _safe_device(device)
        if target:
            res = adb.resources(target)
            battery = res["battery"]
            free_mb = res["storage_free_mb"]
            problems = []
            if isinstance(battery, int) and battery < 15:
                problems.append(f"电量 {battery}%")
            if isinstance(free_mb, int) and free_mb < 200:
                problems.append(f"存储仅剩 {free_mb} MB")
            checks.append({
                "name": "resources",
                "status": "warn" if problems else "ok",
                "detail": f"电量 {battery if battery is not None else '?'}%，"
                          f"可用存储 {free_mb if free_mb is not None else '?'} MB"
                          + (f"；{'、'.join(problems)}" if problems else ""),
                "fix": [] if not problems else [
                    "清理设备存储后再试（存储满时 dump 会失败）",
                    "保持电量 > 15%，省电模式会显著拖慢操作",
                ],
            })
        else:
            checks.append({"name": "resources", "status": "info",
                           "detail": "无设备，跳过", "fix": []})
    except Exception as exc:
        checks.append({"name": "resources", "status": "info",
                       "detail": f"检测失败: {exc}", "fix": []})

    # 8) 环境接入：没设备时，明确告诉 Agent 该接真机还是装模拟器
    try:
        from . import onboarding

        found = onboarding.inspect_emulators()
        situation, recommended = onboarding.classify(
            onboarding.inspect_devices(), found
        )
        detail = f"{situation}；建议 {recommended}"
        if found:
            detail += "；已安装 " + ", ".join(
                e["name"] + ("（运行中）" if e["running"] else "") for e in found
            )
        checks.append({
            "name": "environment",
            "status": "ok" if situation == "ready" else "warn",
            "detail": detail,
            "fix": [] if situation == "ready" else ["agent-android setup"],
        })
    except Exception as exc:  # 诊断项本身不能把 doctor 拖挂
        checks.append({"name": "environment", "status": "warn",
                       "detail": f"检测失败: {exc}", "fix": ["agent-android setup"]})

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    return {
        "ok": not failed,
        "summary": f"{len(checks)} 项检查：{len(failed)} 失败、{len(warned)} 警告",
        "checks": checks,
        # 把所有可执行的修复命令汇总，Agent 可以逐条跑
        "next_actions": [cmd for c in checks for cmd in c.get("fix", [])],
    }


def recover(device: str | None = None, *, fix_animations: bool = False) -> dict[str, Any]:
    """按安全顺序自动尝试修复。自身永不抛异常，只记录每一步结果。"""
    steps: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        steps.append({"step": name, "ok": ok, "detail": detail})

    # 1) 重启 adb server：解决 90% 的 offline / not found
    try:
        adb.run(["kill-server"], timeout=10)
        adb.run(["start-server"], timeout=15)
        record("restart-adb", True, "adb server 已重启")
    except AgentAndroidError as exc:
        record("restart-adb", False, exc.message)

    # 2) 扫描并连接常见模拟器端口
    try:
        connected = adb.connect_known()
        record("connect-emulators", bool(connected),
               f"连上 {len(connected)} 个端口: " +
               ", ".join(str(c["address"]) for c in connected) if connected else "未发现监听端口")
    except AgentAndroidError as exc:
        record("connect-emulators", False, exc.message)

    # 3) 清理损坏的快照缓存
    try:
        target = adb.resolve_device(device)
        path = snapshot_path(target)
        if path.exists():
            path.unlink()
            record("clear-cache", True, f"已删除 {path.name}")
        else:
            record("clear-cache", True, "无缓存需要清理")
    except (AgentAndroidError, OSError) as exc:
        record("clear-cache", False, str(exc))

    # 4) 伴侣 App：无障碍被系统回收时尝试重新启用
    try:
        from . import bridge

        target = _safe_device(device)
        if target:
            ok = bridge.ensure_ready(target)
            record("restart-companion", ok,
                   "无障碍服务已重新启用" if ok else "无法自动启用（真机需手动开启）")
        else:
            record("restart-companion", False, "无可用设备")
    except Exception as exc:
        record("restart-companion", False, str(exc))

    # 5) 可选：关掉动画，提高 dump 成功率
    if fix_animations:
        try:
            target = adb.resolve_device(device)
            for key in ("window_animation_scale", "transition_animation_scale",
                        "animator_duration_scale"):
                adb.shell(f"settings put global {key} 0", device=target)
            record("disable-animations", True, "动画已关闭（测完记得设回 1）")
        except AgentAndroidError as exc:
            record("disable-animations", False, exc.message)

    diagnosis = doctor(device)
    return {
        "ok": diagnosis["ok"],
        "steps": steps,
        "device": _safe_device(device),
        "diagnosis": diagnosis,
    }


def _safe_device(device: str | None) -> str:
    try:
        return adb.resolve_device(device)
    except AgentAndroidError:
        return ""
