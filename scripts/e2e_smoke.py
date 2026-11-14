"""端到端冒烟测试：在真实设备/模拟器上跑一遍完整的「看 → 动 → 再看」。

它验证的不是「命令能跑」，而是 Agent 真正依赖的闭环能力：
定位元素 → 点击 → 界面确实变了 → 能读回新界面。

用法：
    python scripts/e2e_smoke.py
    python scripts/e2e_smoke.py --device 127.0.0.1:16384
    python scripts/e2e_smoke.py --skip-input     # 不测中文输入（未装伴侣 App 时）

退出码：全部通过 0，否则 1。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_android import actions, adb, bridge, diagnostics, snapshot  # noqa: E402
from agent_android.util import AgentAndroidError  # noqa: E402


class Runner:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str, float]] = []

    def step(self, name: str, func) -> bool:
        """跑一步并记录耗时；func 返回 (ok, detail)。"""
        started = time.time()
        try:
            ok, detail = func()
        except Exception as exc:  # 单步失败不该中断整轮，否则看不到全貌
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        cost = time.time() - started
        self.results.append((name, ok, str(detail), cost))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name:<22} {cost:5.1f}s  {detail}")
        return ok

    def summary(self) -> int:
        passed = sum(1 for _, ok, _, _ in self.results if ok)
        total = len(self.results)
        print("\n" + "-" * 62)
        print(f"结果：{passed}/{total} 通过")
        failed = [name for name, ok, _, _ in self.results if not ok]
        if failed:
            print("失败项：" + "、".join(failed))
            return 1
        print("所有关键链路均可用。")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default=None)
    parser.add_argument("--skip-input", action="store_true",
                        help="跳过中文输入（未安装伴侣 App 时用）")
    parser.add_argument("--out", default="e2e-shot.png")
    args = parser.parse_args()

    r = Runner()
    device = adb.resolve_device(args.device)
    print(f"目标设备：{device}\n")

    state: dict[str, object] = {}

    # 1) 环境体检
    def check_doctor():
        report = diagnostics.doctor(device)
        return report["ok"], report["summary"]

    r.step("环境体检", check_doctor)

    # 1.5) 伴侣 App 就绪：它既是中文输入的通道，也是 uiautomator 失败时的降级通道
    if not args.skip_input:
        def prepare_bridge():
            adb.shell(
                "settings put secure enabled_accessibility_services "
                "com.coco.agentandroid/com.coco.agentandroid.BridgeAccessibilityService",
                device=device,
            )
            adb.shell("settings put secure accessibility_enabled 1", device=device)
            actions.launch("com.coco.agentandroid", device=device)
            time.sleep(3)
            token = bridge.read_token(device)
            if token:
                adb.run(["forward", "tcp:8377", "tcp:8377"], device=device)
            return bool(token), f"token={token or '未获取'}"

        r.step("伴侣 App 就绪", prepare_bridge)

    # 2) 点亮屏幕
    def check_wake():
        info = actions.wake(device=device)
        return info["awake"], info["state"]

    r.step("点亮屏幕", check_wake)

    # 3) 启动系统设置
    def check_launch():
        actions.launch("com.android.settings", device=device)
        time.sleep(2)
        data = snapshot.take(device)
        state["home"] = data
        ok = data["package"] == "com.android.settings" and data["node_count"] > 5
        return ok, f"{data['package']} / {data['node_count']} 节点"

    r.step("启动设置并读界面", check_launch)

    # 4) 用「文本」定位并点击；点不到就滚动再点 —— 这正是 Agent 的真实行为
    def check_tap_by_text():
        before = state.get("home") or {}
        method = "直接点击"
        try:
            actions.tap("网络和互联网", device=device)
        except AgentAndroidError:
            # 目标在屏幕外（列表可能已滚到底），反向滚动查找
            found = actions.scroll_to("网络和互联网", device=device,
                                      max_swipes=5, direction="down")
            actions.tap(found["element"], device=device)
            method = f"滚动 {found['attempts']} 次后点击"
        time.sleep(1.5)
        after = snapshot.take(device)
        state["network"] = after
        changed = (after["node_count"] != before.get("node_count")
                   or after["package"] != before.get("package"))
        texts = [n["text"] for n in after["nodes"] if n["text"]][:3]
        return changed, f"{method}；界面 → {texts}"

    r.step("按文本点击并确认变化", check_tap_by_text)

    # 5) 返回上一级：从网络页回到设置首页
    def check_back():
        actions.key("back", device=device)
        time.sleep(1.2)
        data = snapshot.take(device)
        ok = data["package"] == "com.android.settings"
        return ok, f"{data['package']} / {data['node_count']} 节点"

    r.step("返回上一级", check_back)

    # 6) 滚动查找（目标在列表更下方）
    def check_scroll():
        info = actions.scroll_to("关于手机", device=device, max_swipes=6, direction="up")
        state["about"] = info
        return info["found"], f"滚动 {info['attempts']} 次后找到 {info['element']}"

    r.step("滚动查找元素", check_scroll)

    # 7) 点进「关于」，读设备信息
    def check_enter_about():
        info = state.get("about") or {}
        if not info.get("element"):
            return False, "上一步没有找到元素"
        actions.tap(info["element"], device=device)
        time.sleep(1.5)
        data = snapshot.take(device)
        texts = [n["text"] for n in data["nodes"] if n["text"]]
        hit = [t for t in texts if any(k in t for k in ("版本", "型号", "Android", "内核"))]
        return bool(hit), f"读到 {hit[:3] or texts[:3]}"

    r.step("进入子页面并读信息", check_enter_about)

    # 8) 截图
    def check_screenshot():
        info = actions.screenshot(args.out, device=device)
        size = Path(args.out).stat().st_size
        return size > 10_000, f"{size // 1024} KB"

    r.step("截图落盘", check_screenshot)

    # 9) 回桌面
    def check_home():
        actions.key("home", device=device)
        time.sleep(1.2)
        data = snapshot.take(device)
        ok = "launcher" in data["package"].lower() or data["package"] != "com.android.settings"
        return ok, data["package"]

    r.step("回到桌面", check_home)

    # 10) 崩溃检测
    def check_crashes():
        info = actions.crashes(device=device, limit=3)
        return True, f"最近 {info['count']} 条崩溃记录（不影响本轮）"

    r.step("读取崩溃记录", check_crashes)

    # 11) 中文输入（需要伴侣 App）
    if not args.skip_input:
        def check_chinese_input():
            # 实例已在「伴侣 App 就绪」那步准备好，这里只把它拉回前台
            actions.launch("com.coco.agentandroid", device=device)
            time.sleep(2)
            data = snapshot.take(device)
            editables = [n for n in data["nodes"] if n["editable"]]
            if not editables:
                return False, "伴侣 App 未安装或界面里没有输入框"
            token = bridge.read_token(device)
            text = "端到端测试 2026"
            bridge.action(device, {"action": "input", "text": text,
                                   "target": editables[0]["id"]}, token=token)
            time.sleep(0.8)
            after = snapshot.take(device)
            values = [n["text"] for n in after["nodes"] if n["editable"]]
            return text in (values[0] if values else ""), f"输入框内容 → {values}"

        r.step("中文输入（伴侣 App）", check_chinese_input)

    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
