"""屏幕快照：把 uiautomator 的 XML 翻译成 Agent 能读懂的结构化元素表。

这是整个项目最核心的一步。adb 原始给的是一张 PNG 或一坨 XML，
Agent 看图只能「猜」按钮在哪；这里把每个控件的文字、类型、坐标、
可点击性都标好并分配稳定 id，Agent 就能像读网页 DOM 一样读手机屏幕。
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from . import adb
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

    visible = all_nodes if full else [n for n in all_nodes if is_meaningful(n)]

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
    data = {
        "device": device,
        "screen": screen,
        "package": package,
        "node_count": len(visible),
        "total_node_count": len(all_nodes),
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
            "界面可能正在动画或该节点已离屏，重新 snapshot 后再定位",
        )
    return node


def find_element(target: str, device: str) -> dict[str, Any]:
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
        candidates = [
            n for n in nodes
            if value == n[field] or (field == "resource_id" and n[field].endswith("/" + value))
        ]
        if not candidates:
            raise AgentAndroidError(
                f"找不到 {keyword}={value}", "用 snapshot 查看当前屏幕可用元素",
                code="element_not_found", retryable=True,
                actions=["agent-android snapshot", "改用文本匹配：tap text=<文字>"],
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
    exact = [n for n in nodes if target in (n["text"], n["content_desc"])]
    pool = exact or [n for n in nodes
                     if target in n["text"] or target in n["content_desc"]]
    if not pool:
        raise AgentAndroidError(
            f"屏幕上找不到「{target}」", "用 snapshot 列出当前所有元素",
            code="element_not_found", retryable=True,
            actions=["agent-android snapshot", "agent-android scroll-to <目标>  # 可能在屏幕外"],
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
