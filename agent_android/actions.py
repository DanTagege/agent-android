"""动作层：点击、输入、滑动、按键、装包、截图、日志。

约定：每个动作都返回一段 JSON，并附上下一步建议（重新 snapshot）。
Agent 因此可以形成「看 → 动 → 再看」的闭环，而不是盲发一串命令。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from . import adb, snapshot
from .util import AgentAndroidError, log

# 常用按键名到 Android KeyEvent 的映射
KEYCODES = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "enter": "KEYCODE_ENTER",
    "recents": "KEYCODE_APP_SWITCH",
    "menu": "KEYCODE_MENU",
    "delete": "KEYCODE_DEL",
    "tab": "KEYCODE_TAB",
    "power": "KEYCODE_POWER",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
}


def _result(device: str, action: str, **extra: Any) -> dict[str, Any]:
    return {"device": device, "action": action, **extra,
            "next": "重新执行 snapshot 确认界面变化"}


def _screen_signature(data: dict[str, Any]) -> tuple[str, frozenset[str]]:
    """用「包名 + 可见文字集合」近似表示一次界面状态。"""
    return (
        str(data.get("package", "")),
        frozenset(n["text"] for n in data.get("nodes", []) if n.get("text")),
    )


def tap(target: str, *, device: str, delay_ms: int = 0,
        verify: bool = False, auto_scroll: bool = False) -> dict[str, Any]:
    """点击目标元素。target 支持 n12 / text=登录 / id=btn / 360,900。

    verify=True：点击前后各抓一次快照，明确告诉 Agent 界面到底变没变。
    auto_scroll=True：找不到目标时先滚着找一段（默认关闭，因为自动滚可能点到意外元素）。
    """
    try:
        element = snapshot.find_element(target, device)
    except AgentAndroidError as exc:
        if exc.code == "element_invisible" and not auto_scroll:
            # 无障碍通道偶尔给出未布局的坐标（负数高度），点下去必然落空。
            # 换 uiautomator 重抓一次通常就正常了 —— 这是通道问题，不是元素不存在。
            log("当前通道给的坐标不可用，换 uiautomator 重新抓取")
            snapshot.take(device, prefer="uiautomator")
            element = snapshot.find_element(target, device)
        elif auto_scroll:
            # 滚一次再找：常见于「元素在列表下方，但 Agent 没想到要先滚」
            moved = scroll_to(target, device=device, max_swipes=3)
            element = snapshot.find_element(target, device)
            if moved.get("warning"):
                log(f"自动滚动：{moved['warning']}")
        else:
            raise

    before = snapshot.take(device) if verify else None
    x, y = element["center"]
    # input tap 偶发不投递，短暂延时可显著提高稳定性。
    # 在 PC 端等待：设备端 toybox 的 sleep 未必支持小数秒。
    if delay_ms:
        time.sleep(delay_ms / 1000)
    adb.shell(f"input tap {x} {y}", device=device)

    result = _result(device, "tap", target=target, element=element["id"],
                     text=element.get("text", ""), at=[x, y])
    if verify and before is not None:
        after = snapshot.take(device)
        changed = _screen_signature(before) != _screen_signature(after)
        result["verified"] = changed
        if changed:
            result["next"] = f"界面已变化，新页面是 {after.get('package')}；重新 snapshot 看新元素"
        else:
            result["warning"] = (
                "点击已发出，但界面没有任何变化：可能点在不可交互区域、"
                "被弹窗遮挡，或这个控件需要长按"
            )
            result["next"] = "agent-android screenshot  # 看图确认，或改用 long-press"
    return result


def long_press(target: str, *, device: str, duration_ms: int = 800) -> dict[str, Any]:
    """长按：用 input swipe 起止点相同来模拟按住。"""
    element = snapshot.find_element(target, device)
    x, y = element["center"]
    adb.shell(f"input swipe {x} {y} {x} {y} {duration_ms}", device=device)
    return _result(device, "long_press", target=target, at=[x, y])


def _escape_input(text: str) -> str:
    """把文本转成 `adb shell input text` 能接受的形式。

    空格必须写成 %s；`%` 本身是占位符前缀，不转义会让 "50%" 这类文本
    被当成格式符吞掉字符；其余 shell 元字符逐个加反斜杠。
    """
    # 换行会被设备端 sh 当成命令分隔符 —— 这既是输入失败，也是命令注入。
    # input text 本来就不支持多行，直接拒绝并让调用方拆行。
    if any(ch in "\r\n\t" for ch in text):
        raise AgentAndroidError(
            "输入文本不能包含换行或制表符",
            "adb shell 会把换行当作命令分隔符（有注入风险）；请按行拆开，逐条 input",
            code="invalid_argument", retryable=False,
            actions=["把文本按行拆开，逐条 input"],
        )
    specials = set("&|;<>()$`\\\"'!*?#~")
    out: list[str] = []
    for ch in text:
        if ch == " ":
            out.append("%s")
        elif ch == "%":
            out.append("\\%")
        elif ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def double_tap(target: str, *, device: str, interval_ms: int = 80) -> dict[str, Any]:
    """双击。adb 没有原生双击，用两次间隔很短的 tap 模拟。"""
    element = snapshot.find_element(target, device)
    x, y = element["center"]
    adb.shell(f"input tap {x} {y}", device=device)
    time.sleep(interval_ms / 1000)
    adb.shell(f"input tap {x} {y}", device=device)
    return _result(device, "double_tap", target=target, element=element["id"], at=[x, y])


def drag(from_target: str, to: str, *, device: str, duration_ms: int = 600) -> dict[str, Any]:
    """拖拽：起点是元素或坐标，终点也可以是元素或坐标。

    `input draganddrop` 不是所有设备都支持，失败时退回长时间 swipe。
    """
    start = snapshot.find_element(from_target, device)
    x1, y1 = start["center"]

    if re.fullmatch(r"\d+\s*,\s*\d+", to):
        x2, y2 = (int(v.strip()) for v in to.split(","))
    else:
        end = snapshot.find_element(to, device)
        x2, y2 = end["center"]

    try:
        adb.shell(f"input draganddrop {x1} {y1} {x2} {y2} {duration_ms}", device=device)
        method = "draganddrop"
    except AgentAndroidError:
        adb.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", device=device)
        method = "swipe-fallback"
    return _result(device, "drag", **{"from": [x1, y1], "to": [x2, y2], "method": method})


def scroll_to(target: str, *, device: str, max_swipes: int = 8,
              direction: str = "up") -> dict[str, Any]:
    """滚动直到目标出现在屏幕上。

    每次滚动后重新 snapshot —— 不复用旧层级，否则会拿过期坐标点错位置。
    """
    last_error: AgentAndroidError | None = None
    first_package: str | None = None
    prev_signature: tuple[str, ...] | None = None

    for attempt in range(max_swipes + 1):
        data = snapshot.take(device)
        package = str(data.get("package", ""))

        # 防止把界面滚跑：滚到一半跳出当前 App 就立刻停手
        if first_package is None:
            first_package = package
        elif package != first_package:
            raise AgentAndroidError(
                f"滚动过程中界面切走了（{first_package} → {package}），已停止",
                "重新 snapshot 确认当前界面，必要时先 launch 回到目标 App",
                code="ui_changed_while_scrolling", retryable=True,
                actions=["agent-android snapshot", "agent-android launch <包名>"],
            )

        try:
            element = snapshot.find_element(target, device)
            return {
                "action": "scroll_to",
                "found": True,
                "attempts": attempt,
                "target": target,
                "element": element["id"],
                "at": element["center"],
                "next": "用 tap <element> 点击它",
            }
        except AgentAndroidError as exc:
            last_error = exc

        # 滚了但内容没变 = 到底了或这个界面根本不可滚，别再无意义地刷屏
        signature = tuple(sorted(n["text"] for n in data["nodes"] if n["text"]))
        if signature == prev_signature:
            raise AgentAndroidError(
                f"滚动后界面没有变化，已到底或当前界面不可滚动（找不到「{target}」）",
                "换个 direction 反向找，或确认你要找的元素在哪个 App 里",
                code="not_scrollable", retryable=True,
                actions=["agent-android snapshot  # 看当前界面到底有什么",
                         "agent-android scroll-to <目标> --max 3  # 可搭配反向"],
            )
        prev_signature = signature

        if attempt == max_swipes:
            break
        swipe(direction, device=device, duration_ms=300)
        time.sleep(0.3)

    raise AgentAndroidError(
        f"已滚动 {max_swipes} 次仍未找到「{target}」",
        last_error.message if last_error else "确认目标文案是否正确",
        code="element_not_found", retryable=True,
        actions=[
            "agent-android snapshot  # 看当前屏幕到底有什么",
            "换 direction 反向再试（down）",
        ],
    )


def input_text(text: str, *, device: str, target: str | None = None,
               clear: bool = False) -> dict[str, Any]:
    """输入文字。可先点某个输入框（target），再输入。

    注意：`input text` 不支持中文（Android 原生限制）。检测到非 ASCII
    会明确报错并给出替代方案，而不是静默输入乱码。
    """
    if any(ord(ch) > 127 for ch in text):
        raise AgentAndroidError(
            "adb input text 不支持中文/非 ASCII 字符",
            '改走设备端伴侣 App：agent-android --bridge input "中文内容"'
            "（需先安装 android/ 下的 APK 并开启无障碍服务）；"
            "或安装 ADBKeyBoard 输入法",
            code="input_non_ascii", retryable=False,
            actions=[
                'agent-android --bridge input "<原文>"',
                "agent-android --bridge snapshot  # 确认伴侣 App 已连通",
            ],
        )
    if target:
        tap(target, device=device)
    if clear:
        # input keyevent 支持一次传多个 keycode：先移到末尾，再连发 60 次 DEL。
        # 比「长按删除」可靠（部分模拟器不认 --longpress），而且只往返一次。
        keys = " ".join(["KEYCODE_MOVE_END"] + ["KEYCODE_DEL"] * 60)
        adb.shell(f"input keyevent {keys}", device=device, timeout=30)
    payload = _escape_input(text)
    adb.shell(f"input text {payload}", device=device)
    return _result(
        device, "input", text=text, target=target,
        # WebView 里的 HTML input 收不到点击/输入是常态，提前告诉 Agent 怎么验证
        verify="重新 snapshot 确认内容是否真的写进去了；WebView 输入框可能被页面 JS 覆盖",
    )


def swipe(direction: str, *, device: str, duration_ms: int = 400) -> dict[str, Any]:
    """滑动。支持 up/down/left/right，或 "x1,y1 x2,y2" 自定义坐标。"""
    width, height = adb.screen_size(device)
    cx, cy = width // 2, height // 2
    presets = {
        "up": (cx, int(height * 0.75), cx, int(height * 0.25)),
        "down": (cx, int(height * 0.25), cx, int(height * 0.75)),
        "left": (int(width * 0.8), cy, int(width * 0.2), cy),
        "right": (int(width * 0.2), cy, int(width * 0.8), cy),
    }
    if direction in presets:
        x1, y1, x2, y2 = presets[direction]
    else:
        nums = re.findall(r"\d+", direction)
        if len(nums) != 4:
            raise AgentAndroidError(
                f"无法解析滑动参数: {direction}",
                "用 up/down/left/right，或 '100,800 100,200'",
                code="invalid_argument", retryable=False,
            )
        x1, y1, x2, y2 = (int(v) for v in nums)
    adb.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", device=device)
    return _result(device, "swipe", direction=direction,
                   points=[x1, y1, x2, y2])


def key(name: str, *, device: str) -> dict[str, Any]:
    """发送系统按键（返回、Home、回车等）。"""
    code = KEYCODES.get(name.lower())
    if not code:
        raise AgentAndroidError(
            f"未知按键: {name}", f"可用: {', '.join(KEYCODES)}",
            code="invalid_argument", retryable=False,
        )
    adb.shell(f"input keyevent {code}", device=device)
    return _result(device, "key", name=name, keycode=code)


def wake(*, device: str) -> dict[str, Any]:
    """点亮屏幕。

    灭屏或锁屏时，snapshot 拿到的只是锁屏界面，Agent 会误以为 App 没启动。
    先唤醒再操作；但**不会尝试绕过锁屏密码**——那需要用户自己解锁。
    """
    adb.shell("input keyevent KEYCODE_WAKEUP", device=device)
    state = adb.shell("dumpsys power | grep mWakefulness=", device=device).strip()
    awake = "Awake" in state
    return {
        "action": "wake",
        "awake": awake,
        "state": state.splitlines()[0][:80] if state else "",
        "next": "若仍是锁屏，需要用户手动解锁（工具不绕过锁屏）" if not awake
                else "执行 snapshot 确认当前界面",
    }


def install(apk: str, *, device: str, replace: bool = True) -> dict[str, Any]:
    """安装 APK。路径会转成绝对路径，避免相对路径在 adb 进程里解析失败。"""
    path = Path(apk).expanduser().resolve()
    if not path.is_file():
        raise AgentAndroidError(
            f"APK 不存在: {path}", "确认路径拼写；路径会先转成绝对路径",
            code="invalid_argument", retryable=False,
        )
    args = ["install", "-r" if replace else "", str(path)]
    out = adb.run([a for a in args if a], device=device, timeout=120)
    if "Success" not in out:
        raise AgentAndroidError(
            "安装失败", out[:300],
            code="install_failed", retryable=True,
            actions=[
                "adb install -r <apk>  # 看完整报错",
                "检查是否签名冲突（先 adb uninstall <包名>）",
                "确认设备存储空间足够（agent-android doctor）",
            ],
        )
    return _result(device, "install", apk=str(path))


def _launcher_component(package: str, device: str) -> str:
    """解析入口 Activity，作为 monkey 的兜底。"""
    try:
        out = adb.shell(f"cmd package resolve-activity --brief {package}",
                        device=device, timeout=15, check=False)
    except AgentAndroidError:
        return ""
    for line in reversed(out.splitlines()):
        line = line.strip()
        if "/" in line and package in line:
            return line
    return ""


def launch(package: str, *, device: str) -> dict[str, Any]:
    """启动应用。

    先试 monkey（自动处理别名和多入口），但**不能只看退出码**：
    部分模拟器上它会返回 1 却已经启动了。以输出里的「Events injected」为准，
    没有再用 `cmd package resolve-activity` + `am start` 兜底。
    """
    # check=False：monkey 在部分模拟器上「启动成功却返回 1」，
    # 所以以输出里有没有 Events injected 为准，而不是退出码。
    try:
        outcome = adb.shell(
            f"monkey -p {package} -c android.intent.category.LAUNCHER 1",
            device=device, timeout=30, check=False,
        )
    except AgentAndroidError as exc:
        outcome = exc.hint or exc.message

    if "No activities found" in outcome:
        raise AgentAndroidError(
            f"包 {package} 没有 LAUNCHER 入口",
            "确认包名是否正确；需要指定 Activity 时用 `am start -n <包名>/<Activity>`",
            code="launch_no_entry", retryable=False,
            actions=["adb shell pm list packages | grep <关键字>",
                     "adb shell cmd package resolve-activity --brief <包名>"],
        )
    if "Events injected" in outcome:
        return _result(device, "launch", package=package, method="monkey")

    component = _launcher_component(package, device)
    if not component:
        raise AgentAndroidError(
            f"启动失败: {package}",
            (outcome or "monkey 没有注入事件，也没解析到入口 Activity")[:200],
            code="launch_failed", retryable=True,
            actions=["adb shell am start -n <包名>/<Activity>"],
        )
    out = adb.shell(f"am start -n {component}", device=device, timeout=30)
    if "Error" in out or "Exception" in out:
        raise AgentAndroidError(f"启动失败: {package}", out[:200], code="launch_failed")
    return _result(device, "launch", package=package, method="am_start",
                   component=component)


def screenshot(output: str, *, device: str) -> dict[str, Any]:
    """截图保存到本机。截图是「界面真相」，用来交叉验证可能过期的 UI 层级。"""
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = adb.run(["exec-out", "screencap", "-p"], device=device,
                   timeout=30, binary=True)
    if isinstance(data, bytes) and data.startswith(b"\x89PNG"):
        path.write_bytes(data)
    else:
        # Windows 上部分 adb 版本会对二进制做换行转换，导致 PNG 头损坏；
        # 退化成「设备端先落盘，再 pull 回来」，这条路径更稳。
        remote = "/sdcard/_agent_android_shot.png"
        adb.shell(f"screencap -p {remote}", device=device, timeout=30)
        adb.run(["pull", remote, str(path)], device=device, timeout=60)
        adb.shell(f"rm -f {remote}", device=device)
        if not path.exists() or not path.read_bytes().startswith(b"\x89PNG"):
            raise AgentAndroidError(
                "截图失败：没有拿到有效的 PNG 数据",
                "确认设备未处于锁屏/安全窗口；模拟器可改用 android/ 伴侣 App 的方式验证",
                code="screenshot_failed", retryable=True,
                actions=[
                    "agent-android wake  # 先点亮屏幕",
                    "agent-android screenshot -o shot.png  # 重试一次",
                    "agent-android doctor",
                ],
            )
    # 用落盘后的真实大小：fallback 分支里 data 只是 exec-out 的原始字节，
    # 并不是最终 PNG 的长度
    return _result(device, "screenshot", path=str(path), bytes=path.stat().st_size)


def parse_crashes(text: str, limit: int = 3) -> list[dict[str, Any]]:
    """从 crash buffer 里拆出结构化的崩溃记录（纯函数，方便单测）。"""
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        payload = line.split("AndroidRuntime:", 1)[-1].strip() if "AndroidRuntime:" in line else ""
        if "FATAL EXCEPTION" in line:
            current = {"process": "", "exception": "", "frames": []}
            blocks.append(current)
        elif current is not None:
            # 只看 AndroidRuntime 那段，避免普通行里恰好出现 "Process:" 把进程名冲掉
            if "Process:" in payload:
                current["process"] = payload.replace("Process:", "", 1).split(", PID:")[0].strip()
            elif ("Exception" in payload or "Error" in payload) and not current["exception"]:
                current["exception"] = payload[:200]
            elif payload.startswith("at ") and len(current["frames"]) < 5:
                current["frames"].append(payload[:160])
    return blocks[-limit:]


def crashes(*, device: str, limit: int = 3) -> dict[str, Any]:
    """读取最近的崩溃记录。

    直接看 logcat 会被几百行噪音淹没；这里只取 crash buffer 并按
    process / exception / 调用栈拆好，Agent 可以立刻判断是哪个 App 崩了。
    """
    # crash buffer 本身很小，全量读即可；`-t` 与过滤组合在部分设备上会返回空
    out = adb.shell("logcat -d -b crash", device=device, timeout=30)
    items = parse_crashes(out, limit)
    return {
        "action": "crashes",
        "count": len(items),
        "crashes": items,
        "next": "需要完整堆栈时用 logcat --level E" if items else "没有崩溃记录",
    }


def logcat(*, device: str, lines: int = 50, level: str = "") -> dict[str, Any]:
    """读取最近的 logcat 日志（-d 表示 dump 完就退出，不会挂住 Agent）。"""
    cmd = f"logcat -d -t {max(1, min(lines, 2000))}"
    if level:
        # 必须加引号：不加的话设备端 shell 会把 * 当通配符展开，直接报 "no matches"
        cmd += f" '*:{level.upper()}'"
    out = adb.shell(cmd, device=device, timeout=30)
    return _result(device, "logcat", lines=lines, output=out.splitlines())
