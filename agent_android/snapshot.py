"""屏幕快照：把 uiautomator 的 XML 翻译成 Agent 能读懂的结构化元素表。

这是整个项目最核心的一步。adb 原始给的是一张 PNG 或一坨 XML，
Agent 看图只能「猜」按钮在哪；这里把每个控件的文字、类型、坐标、
可点击性都标好并分配稳定 id，Agent 就能像读网页 DOM 一样读手机屏幕。
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any

from . import adb
from .util import snapshot_path
from .util import AgentAndroidError, load_snapshot, log, save_snapshot

# 匹配 "[120,840][360,910]" 形式的 bounds
# 分屏/自由窗口下坐标可以是负数，必须允许负号，否则会解析失败退化成 [0,0,0,0]
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_TRUTHY = {"true", "True", "TRUE", "1"}


def dump_xml(device: str) -> str:
    """在设备上生成 UI 层级 XML 并读回本机，随后清理临时文件。

    页面切换/动画进行中时，uiautomator 会返回 "could not get idle state"
    或者只 dump 出半个窗口。这里重试 3 次（间隔递增），比直接失败更符合
    「点完立刻抓图」这个最高频的使用方式。
    """
    last_error = ""
    for attempt in range(3):
        # 每次用独立文件名，避免并发请求互相删掉对方刚 dump 的文件
        path = adb.dump_path()
        try:
            # 一条命令完成 dump + 读回 + 清理：原本 4 次往返压成 1 次。
            # dump 成功时 `&&` 才会执行 cat；`;` 保证无论如何都删临时文件。
            out = adb.shell(
                f"uiautomator dump {path} 2>&1 && cat {path}; rm -f {path}",
                device=device, timeout=45, check=False,
            )
            start = out.find("<?xml")
            if start < 0:
                start = out.find("<hierarchy")
            xml = out[start:] if start >= 0 else ""
            if "<hierarchy" not in xml:
                last_error = out[:200]
                # 「等不到静止」是持续动画导致的，重试多少次都一样，
                # 直接抛给上层去走无障碍降级，别在这里白等 30 秒
                if "could not get idle state" in out:
                    raise AgentAndroidError(
                        "界面持续动画，uiautomator 等不到静止状态",
                        "改用无障碍服务抓取（不依赖 idle）",
                        code="uiautomator_not_idle", retryable=True,
                        actions=["agent-android --bridge snapshot"],
                    )
                time.sleep(0.3 * (attempt + 1))
                continue
            return xml
        except AgentAndroidError as exc:
            # 「等不到静止」可能出现在 stdout，也可能随 adb 的 stderr 一起抛上来，
            # 两处都要认出来：它是持续动画，重试不会变好，直接交给上层降级。
            text = f"{exc.message}\n{exc.hint}"
            if "could not get idle state" in text:
                raise AgentAndroidError(
                    "界面持续动画，uiautomator 等不到静止状态",
                    "改用无障碍服务抓取（不依赖 idle）",
                    code="uiautomator_not_idle", retryable=True,
                    actions=["agent-android --bridge snapshot"],
                ) from exc
            if exc.code == "uiautomator_not_idle":
                raise
            last_error = exc.hint or exc.message
            time.sleep(0.3 * (attempt + 1))

    raise AgentAndroidError(
        "uiautomator dump 失败（已重试 3 次）",
        f"设备返回: {last_error[:200]}。界面可能一直处于动画/加载中；"
        "关掉动画后重试，或改用 --bridge snapshot（无障碍服务）",
        code="uiautomator_unavailable", retryable=True,
        actions=[
            "adb shell settings put global window_animation_scale 0",
            "重跑 agent-android snapshot",
            "改用 agent-android --bridge snapshot（不依赖 uiautomator）",
            "agent-android doctor",
        ],
    )


def _parse_bounds(raw: str) -> list[int]:
    """把 bounds 字符串解析成 [left, top, right, bottom]。"""
    match = _BOUNDS_RE.search(raw or "")
    if not match:
        return [0, 0, 0, 0]
    return [int(v) for v in match.groups()]


def parse_nodes(xml: str) -> list[dict[str, Any]]:
    """深度优先遍历层级树，输出扁平节点列表。

    扁平结构比嵌套树更适合 Agent：一句 `tap n12` 就能引用任意控件，
    不必描述一条从根节点下来的路径。
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        # 直接把原始异常抛给 Agent 会得到一大段 traceback，这里转成可读建议
        raise AgentAndroidError(
            "UI 层级 XML 解析失败",
            f"返回内容可能被截断（{exc}）。重试一次；若仍失败，改用 android/ "
            "伴侣 App 的 /snapshot，它不依赖 uiautomator",
            code="ui_xml_invalid", retryable=True,
            actions=["agent-android snapshot  # 重试一次",
                     "agent-android --bridge snapshot"],
        ) from exc
    nodes: list[dict[str, Any]] = []

    def walk(element: ET.Element, depth: int, parent: str | None) -> None:
        attrs = element.attrib
        bounds = _parse_bounds(attrs.get("bounds", ""))
        # id 按原始 DFS 顺序编号，即使后面做过滤也不会变，保证 tap 引用稳定
        node_id = f"n{len(nodes)}"
        node: dict[str, Any] = {
            "id": node_id,
            "parent": parent,
            "depth": depth,
            "index": int(attrs.get("index", "0") or 0),
            "text": attrs.get("text", ""),
            "content_desc": attrs.get("content-desc", ""),
            "resource_id": attrs.get("resource-id", ""),
            "class": attrs.get("class", "").rsplit(".", 1)[-1],
            "package": attrs.get("package", ""),
            "bounds": bounds,
            "center": [(bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2],
            "clickable": attrs.get("clickable") in _TRUTHY,
            "long_clickable": attrs.get("long-clickable") in _TRUTHY,
            "scrollable": attrs.get("scrollable") in _TRUTHY,
            # uiautomator 的 XML 没有 editable 属性，输入框要从 class 推断
            "editable": attrs.get("editable") in _TRUTHY
                        or "EditText" in attrs.get("class", ""),
            "enabled": attrs.get("enabled") in _TRUTHY,
            "checked": attrs.get("checked") in _TRUTHY,
            "focused": attrs.get("focused") in _TRUTHY,
            "password": attrs.get("password") in _TRUTHY,
        }
        # 密码框默认脱敏：工具会把 snapshot 交给模型、写进缓存、打到日志，
        # 明文密码一旦流出就收不回来。需要调试时用 AGENT_ANDROID_UNMASK=1。
        if node["password"] and not os.environ.get("AGENT_ANDROID_UNMASK"):
            node["text"] = "••••••"
            node["masked"] = True

        nodes.append(node)
        for child in list(element):
            walk(child, depth + 1, node_id)

    # <hierarchy> 只是容器标签，不是控件；真正的控件树是它的子节点
    roots = list(root) if root.tag == "hierarchy" else [root]
    for child in roots:
        walk(child, 0, None)
    return nodes


def is_meaningful(node: dict[str, Any]) -> bool:
    """判断节点是否值得给 Agent 看。

    纯布局容器（无文字、不可交互）会占掉大量 token 却没有信息量，
    默认过滤掉；加 --full 可以拿到完整层级。
    """
    return bool(
        node["text"]
        or node["content_desc"]
        or node["resource_id"]
        or node["clickable"]
        or node["scrollable"]
        or node["editable"]
        or node["checked"]
    )


# content_desc 必须保留：图标按钮往往只有 content-desc，没有 text
_COMPACT_FIELDS = ("id", "text", "content_desc", "class", "bounds",
                   "clickable", "editable", "scrollable", "checked")


def _compact(node: dict[str, Any]) -> dict[str, Any]:
    """只保留操作必需字段，显著降低 token 消耗（大列表页能省一半以上）。"""
    return {key: node[key] for key in _COMPACT_FIELDS if key in node}


def _bridge_snapshot(device: str, *, recover: bool = False) -> dict[str, Any] | None:
    """伴侣 App 的无障碍快照（不依赖 uiautomator 的 idle 判断）。

    函数内部自己吞异常，返回 None 表示「这条路不可用」。

    recover=False 时**不会**触发自动拉起：auto 模式每次快照都会先探一下这条路，
    若每次都去写 secure settings + am start，会把设备搞得鸡犬不宁。
    """
    try:
        from . import bridge

        token = bridge.read_token(device)
        if not token:
            # 没装 App 或读不到令牌：立刻放弃，不要浪费一次连接的往返
            return None
        payload = bridge.request(device, "/snapshot", token=token, _retry=recover)
    except AgentAndroidError as exc:
        log(f"无障碍快照不可用：{exc.message}（{exc.hint[:60]}）")
        return None
    if not isinstance(payload, dict) or not payload.get("nodes"):
        return None
    return payload


def take(device: str, *, full: bool = False, compact: bool = False,
         prefer: str = "auto") -> dict[str, Any]:
    """抓一次屏幕快照，返回可直接交给 Agent 的 JSON 字典。

    prefer 决定优先走哪条通道：
      - auto（默认）：先试无障碍（实测快 2 倍），不可用再走 uiautomator
      - uiautomator：强制系统通道，失败才降级
      - accessibility：只走伴侣 App，不可用直接报错
    """
    # 上一次的包名用来判断「App 是不是崩了退回桌面」
    prev_package = ""
    try:
        prev_package = load_snapshot(device).get("package", "")
    except AgentAndroidError:
        pass

    warnings: list[str] = []
    source = "uiautomator"
    all_nodes = None

    if prefer in ("auto", "accessibility"):
        payload = _bridge_snapshot(device)
        if payload:
            all_nodes = payload["nodes"]
            source = "accessibility"
        elif prefer == "accessibility":
            raise AgentAndroidError(
                "无障碍通道不可用",
                "确认伴侣 App 已安装、无障碍服务已开启",
                code="bridge_unreachable", retryable=True,
                actions=["agent-android --bridge snapshot", "agent-android doctor"],
            )

    if all_nodes is None:
        try:
            all_nodes = parse_nodes(dump_xml(device))
        except AgentAndroidError:
            # uiautomator 拿不到（典型：持续动画界面）时自动切无障碍
            fallback = _bridge_snapshot(device)
            if fallback is None:
                raise
            all_nodes = fallback["nodes"]
            source = "accessibility"
            warnings.append(
                "uiautomator 拿不到层级（界面持续动画），已自动改用伴侣 App 的无障碍服务"
            )

    # 无障碍通道偶尔在旋转/多显示器后返回整屏错误坐标（bottom 为负）。
    # 文字仍然是对的，但点不了。**必须在计算 visible 之前换通道**，
    # 否则输出给 Agent 的还是旧节点、缓存却已换成新节点，id 会整体错位。
    if source == "accessibility" and all_nodes:
        broken = sum(
            1 for n in all_nodes
            if n["bounds"][2] <= n["bounds"][0] or n["bounds"][3] <= n["bounds"][1]
        )
        if broken * 2 > len(all_nodes):
            try:
                all_nodes = parse_nodes(dump_xml(device))
                source = "uiautomator"
                warnings.append(
                    "无障碍通道返回的坐标大面积无效（可能是旋转后未刷新），"
                    "已自动改用 uiautomator；元素 id 已随之变化，请重新读取"
                )
            except AgentAndroidError:
                warnings.append(
                    "无障碍通道返回的坐标大面积无效，且 uiautomator 也不可用；"
                    "文字可读但点击可能落空，建议重启伴侣 App"
                )

    # 零面积节点（WebView 的离屏项最常见）既看不到也点不到，
    # 留在输出里只会有两个后果：token 翻倍、Agent 点到 (0,0)。
    # 缓存里仍然保留全量，这样 find_element 能找到它们并给出「存在但不可见」的准确错误。
    def _has_area(node: dict[str, Any]) -> bool:
        left, top, right, bottom = node["bounds"]
        return right > left and bottom > top

    visible = [n for n in all_nodes if (full or is_meaningful(n)) and _has_area(n)]
    hidden = len(all_nodes) - len(visible)

    # 一次往返拿到三样东西，避免为每个字段单独起一个 adb 进程
    probed = adb.probe(device)
    ime = bool(probed["ime"])
    package = str(probed["package"])
    size = probed["size"]

    # 提前识别「看到的不是全部」的情况，避免 Agent 白试
    if ime:
        warnings.append("键盘已弹出：下半屏被遮挡且不在 UI 树里，需要时用 key back 收起")
    if prev_package and prev_package != package and (
        not package or "launcher" in package.lower()
    ):
        warnings.append(
            f"界面从 {prev_package} 回到了桌面/系统界面，App 可能已崩溃；"
            "`agent-android crashes` 查看 FATAL EXCEPTION"
        )
    if any("WebView" in n["class"] for n in visible):
        warnings.append("检测到 WebView：部分内容可能不在 UI 树里，必要时用 screenshot 交叉验证")
    if hidden > len(visible):
        warnings.append(
            f"已隐藏 {hidden} 个不可见节点（零面积/离屏），需要时用 scroll-to 滚动查找"
        )
    if len(visible) <= 3:
        warnings.append(
            "有效节点极少：可能是游戏/Flutter/Unity 自绘界面，uiautomator 拿不到控件，"
            "改用 screenshot + 坐标点击（tap x,y）"
        )
    screen = list(size) if size and size[0] else [0, 0]
    if not screen[0]:
        try:
            screen = list(adb.screen_size(device))
        except AgentAndroidError:
            # wm size 在个别模拟器上会失败，但不该让整次快照不可用
            screen = [0, 0]
    # 自绘界面（游戏 / Unity / 部分 Flutter）：整屏几乎没有可用文字。
    # 提前说清楚，避免 Agent 反复 snapshot 却永远找不到按钮。
    text_nodes = sum(1 for n in visible if (n.get("text") or "").strip())
    has_surface = any("SurfaceView" in (n.get("class") or "") for n in all_nodes)
    likely_custom = has_surface and text_nodes <= 2 and len(visible) > 0
    if likely_custom:
        warnings.append(
            "界面几乎没有文字节点，可能是自绘界面（游戏 / Unity / Flutter）："
            "控件树不可用，请改用 screenshot + 坐标点击"
        )
    if probed.get("multi_window"):
        warnings.append(
            "当前处于多窗口/分屏模式，只能读到聚焦窗口；另一侧的界面不可见"
        )

    data = {
        "device": device,
        "screen": screen,
        "package": package,
        "node_count": len(visible),
        "raw_node_count": len(all_nodes),
        "filtered_out": hidden,
        "likely_custom_drawn": likely_custom,
        "multi_window": bool(probed.get("multi_window")),
        "nodes": [_compact(n) for n in visible] if compact else visible,
        "compact": compact,
        "ime_visible": ime,
        "source": source,
        # 给 Agent 的操作提示：明确告诉它下一步能干什么
        "usage": "用 element.id 作为 tap/input 的 target；每次操作后重新 snapshot 确认结果",
        "warning": "；".join(
            ["uiautomator 可能返回过期层级；涉及关键判断时请配合 screenshot 交叉验证"]
            + warnings
        ),
    }
    # 缓存完整层级，保证 tap n12 即使被默认过滤也能命中
    save_snapshot(device, {**data, "nodes": all_nodes, "node_count": len(all_nodes)})
    log(f"snapshot: {len(visible)}/{len(all_nodes)} 个节点")
    return data


def _clickable_ancestor(node: dict[str, Any],
                        by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """向上找最近的可点击祖先。

    Compose 里文字节点本身通常 clickable=false，真正响应点击的是父容器；
    直接点文字节点的中心经常没反应，这是实机调试中最常见的坑。
    """
    current: dict[str, Any] | None = node
    for _ in range(8):
        if current is None:
            break
        if current["clickable"] or current["editable"]:
            left, top, right, bottom = current["bounds"]
            # 无障碍通道偶尔返回负高度的「折叠」祖先（例如 [126,42,336,-197]），
            # 点下去必然落空；跳过它继续往上找，最后回退到原节点。
            if right > left and bottom > top:
                return current
        parent_id = current.get("parent")
        current = by_id.get(parent_id) if parent_id else None
    return node


def _drop_invisible(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """剔除面积为 0 的节点。

    uiautomator 对离屏/不可见控件常给出 [0,0][0,0]，而排序键用的是 top/left，
    这些假节点会排到最前面，点下去实际落在 (0,0) 状态栏 —— 表现为「点了没反应」。
    全是零面积时退回原列表，交给 _ensure_visible 给出明确错误。
    """
    visible = [n for n in nodes if n["bounds"][2] > n["bounds"][0]
               and n["bounds"][3] > n["bounds"][1]]
    return visible or nodes


def _ensure_visible(node: dict[str, Any]) -> dict[str, Any]:
    """兜底校验：没有可见区域的元素不能点。"""
    left, top, right, bottom = node["bounds"]
    if right <= left or bottom <= top:
        raise AgentAndroidError(
            f"元素 {node['id']} 没有可见区域（bounds={node['bounds']}）",
            "界面可能正在动画，或这个节点被折叠/离屏。重新 snapshot 后再定位；"
            "若反复遇到，改用 --prefer uiautomator 换一条通道",
            code="element_invisible", retryable=True,
            actions=["agent-android snapshot",
                     "agent-android snapshot --prefer uiautomator",
                     "agent-android scroll-to <目标>  # 可能在屏幕外"],
        )
    return node


def _norm(text: str | None) -> str:
    """把文本归一化成 NFC 再比较。

    同一个「é」在 Unicode 里可以是预组合字符（U+00E9），
    也可以是 e + 组合重音符（U+0065 U+0301）。界面和用户输入来自不同来源时，
    字节不同但视觉相同 —— 不归一化就会「明明看到却匹配不到」。
    德语变音、西班牙语重音、法语连字都会踩这个坑。
    """
    return unicodedata.normalize("NFC", text or "")


def explain_miss(nodes: list[dict[str, Any]], target: str,
                 screen: list[int]) -> tuple[list[str], list[str]]:
    """找不到元素时，给出最接近的候选和可能的原因。

    「报错说找不到但明明在屏幕上」是最常见的求助，光说 not found 没有信息量。
    """
    from difflib import SequenceMatcher

    needle = _norm(target)
    scored: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        label = _norm(((node.get("text") or "") + " " + (node.get("content_desc") or "")).strip())
        if not label:
            continue
        score = SequenceMatcher(None, needle, label).ratio()
        if score >= 0.45:
            scored.append((score, node))
    scored.sort(key=lambda item: -item[0])

    closest = [
        f"「{((n.get('text') or n.get('content_desc')) or '')}」({n['id']}, 相似度 {s:.0%})"
        for s, n in scored[:3]
    ]

    width, height = (screen + [0, 0])[:2]
    offscreen = [
        f"「{n.get('text')}」({n['id']}, 坐标 {n['bounds']})"
        for n in nodes
        if (n.get("text") or "").strip() and (
            n["bounds"][0] >= width or n["bounds"][1] >= height
            or n["bounds"][2] <= 0 or n["bounds"][3] <= 0
        )
    ][:3]
    return closest, offscreen


def find_element(target: str, device: str) -> dict[str, Any]:
    """定位元素；缓存过期时先刷新再找一次。

    Agent 常常「操作完立刻操作下一步」（MCP 调用间隔只有 0.1 秒），
    缓存里还是上一个界面，于是报「找不到」——其实字面意思没错，但没帮上忙。
    这里在失败路径上补一次刷新，正常路径仍然零额外开销。
    """
    try:
        return _lookup(target, device)
    except AgentAndroidError as exc:
        if exc.code not in ("element_not_found", "element_invisible"):
            raise
        try:
            age = time.time() - snapshot_path(device).stat().st_mtime
        except OSError:
            age = 0.0
        if age < 2.0:
            raise  # 刚抓过还找不到，说明界面上确实没有
        log(f"快照已过期 {age:.1f}s，重新抓取后再找一次")
        take(device)
        return _lookup(target, device)


def _lookup(target: str, device: str) -> dict[str, Any]:
    """把人类/Agent 给的目标解析成一个具体控件。

    支持四种写法：
      n12                 快照里的元素 id
      text=登录            精确匹配 text 或 content-desc
      id=btn_login        resource-id 后缀匹配
      360,900             直接给坐标（此时返回伪节点）
      登录                 先精确、再包含匹配
    """
    if not target or not target.strip():
        # 空字符串会匹配到一堆无文字的布局节点，导致随机点击，必须挡住
        raise AgentAndroidError(
            "target 不能为空",
            "用 snapshot 查看元素 id，或传 text=登录 / id=btn_login / 360,900",
        )
    # 支持「同名元素选第 N 个」：text=确定@2 / 确定@2（从 1 开始）
    nth = 1
    match = re.search(r"@(\d+)$", target)
    # 限制在 1..99：像「text=邮箱@1234」这种文本不该被当成「选第 1234 个」
    if match and 1 <= int(match.group(1)) <= 99:
        nth = int(match.group(1))
        target = target[:match.start()].strip()

    data = load_snapshot(device)
    nodes: list[dict[str, Any]] = data["nodes"]
    by_id = {n["id"]: n for n in nodes}

    # 1) 元素 id
    if re.fullmatch(r"n\d+", target):
        node = by_id.get(target)
        if not node:
            raise AgentAndroidError(
                f"元素 {target} 不在最近一次快照里",
                "先执行 snapshot 刷新屏幕状态",
                code="element_not_found", retryable=True,
                actions=["agent-android snapshot", "重跑原命令"],
            )
        return _ensure_visible(_clickable_ancestor(node, by_id))

    # 2) 裸坐标 x,y
    if re.fullmatch(r"\d+\s*,\s*\d+", target):
        x, y = (int(v.strip()) for v in target.split(","))
        return {"id": "coord", "text": f"({x},{y})", "center": [x, y],
                "bounds": [x, y, x, y], "clickable": False, "class": "Coordinate"}

    # 3) text= / id= 前缀
    keyword, _, value = target.partition("=")
    if keyword.lower() in ("text", "desc", "id") and value:
        field = {"text": "text", "desc": "content_desc", "id": "resource_id"}[keyword.lower()]
        needle = _norm(value)
        candidates = [
            n for n in nodes
            if needle == _norm(n[field])
            or (field == "resource_id" and n[field].endswith("/" + value))
        ]
        if not candidates:
            closest, offscreen = explain_miss(nodes, value, data.get("screen", [0, 0]))
            hint = f"当前界面有 {len(nodes)} 个元素"
            if closest:
                hint += "；最接近的：" + "、".join(closest)
            if offscreen:
                hint += "；屏幕外：" + "、".join(offscreen)
            raise AgentAndroidError(
                f"找不到 {keyword}={value}", hint,
                code="element_not_found", retryable=True,
                actions=["agent-android snapshot",
                         "agent-android scroll-to <目标>  # 可能在屏幕外"],
            )
        # 优先可点击的，其次按屏幕从上到下、从左到右排序，避免点错同名项
        candidates = _drop_invisible(candidates)
        candidates.sort(key=lambda n: (not n["clickable"], n["bounds"][1], n["bounds"][0]))
        if len(candidates) < nth:
            raise AgentAndroidError(
                f"「{value}」只匹配到 {len(candidates)} 个元素，第 {nth} 个不存在",
                "去掉 @N，或先 snapshot 看完整列表",
                code="element_index_out_of_range", retryable=True,
                actions=["agent-android snapshot"],
            )
        return _ensure_visible(_clickable_ancestor(candidates[nth - 1], by_id))

    # 4) 裸文本：先精确匹配，再退化为包含匹配；同名多处按位置排序
    needle = _norm(target)
    exact = [n for n in nodes
             if needle in (_norm(n["text"]), _norm(n["content_desc"]))]
    pool = exact or [n for n in nodes
                     if needle in _norm(n["text"]) or needle in _norm(n["content_desc"])]
    if not pool:
        closest, offscreen = explain_miss(nodes, target, data.get("screen", [0, 0]))
        hint = f"当前界面有 {len(nodes)} 个元素"
        if closest:
            hint += "；最接近的：" + "、".join(closest)
        if offscreen:
            hint += "；屏幕外：" + "、".join(offscreen)
        raise AgentAndroidError(
            f"屏幕上找不到「{target}」", hint,
            code="element_not_found", retryable=True,
            actions=["agent-android snapshot",
                     "agent-android scroll-to <目标>  # 可能在屏幕外",
                     "agent-android tap <目标> --auto-scroll  # 让它自己滚着找"],
        )
    pool = _drop_invisible(pool)
    pool.sort(key=lambda n: (not n["clickable"], n["bounds"][1], n["bounds"][0]))
    if len(pool) < nth:
        raise AgentAndroidError(
            f"「{target}」只匹配到 {len(pool)} 个元素，第 {nth} 个不存在",
            "去掉 @N，或先 snapshot 看完整列表",
            code="element_index_out_of_range", retryable=True,
            actions=["agent-android snapshot"],
        )
    return _ensure_visible(_clickable_ancestor(pool[nth - 1], by_id))
