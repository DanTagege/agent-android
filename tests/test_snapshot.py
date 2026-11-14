"""纯逻辑单元测试：不需要连接真机即可运行。

用 `python -m unittest discover -s tests -v` 执行。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from agent_android import actions, snapshot
from agent_android.actions import _escape_input
from agent_android.util import AgentAndroidError, fail

FIXTURE = Path(__file__).parent / "fixtures" / "ui.xml"


class ParseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.xml = FIXTURE.read_text(encoding="utf-8")
        self.nodes = snapshot.parse_nodes(self.xml)

    def test_node_count_and_order(self) -> None:
        # 根节点 + 标题 + 输入框 + 按钮 + 按钮内文字，深度优先共 5 个
        self.assertEqual(len(self.nodes), 5)
        self.assertEqual([n["id"] for n in self.nodes], ["n0", "n1", "n2", "n3", "n4"])
        self.assertEqual(self.nodes[4]["parent"], "n3")

    def test_bounds_and_center(self) -> None:
        button = self.nodes[3]
        self.assertEqual(button["bounds"], [100, 820, 980, 930])
        self.assertEqual(button["center"], [540, 875])

    def test_flags(self) -> None:
        self.assertTrue(self.nodes[3]["clickable"])
        self.assertFalse(self.nodes[4]["clickable"])
        # uiautomator 没有 editable 属性，应从 EditText 类名推断
        self.assertTrue(self.nodes[2]["editable"])
        self.assertEqual(self.nodes[1]["text"], "欢迎回来")
        self.assertEqual(self.nodes[3]["resource_id"], "com.coco.demo:id/btn_login")

    def test_meaningful_filter(self) -> None:
        # 纯布局容器被过滤，有文字/可交互的节点保留
        self.assertFalse(snapshot.is_meaningful(self.nodes[0]))
        self.assertTrue(snapshot.is_meaningful(self.nodes[1]))
        self.assertTrue(snapshot.is_meaningful(self.nodes[3]))
        kept = [n for n in self.nodes if snapshot.is_meaningful(n)]
        self.assertEqual(len(kept), 4)


class FindElementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = snapshot.parse_nodes(FIXTURE.read_text(encoding="utf-8"))
        self.patcher = patch(
            "agent_android.snapshot.load_snapshot",
            return_value={"nodes": self.nodes},
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_by_id(self) -> None:
        self.assertEqual(snapshot.find_element("n3", "dev")["id"], "n3")

    def test_text_finds_clickable_ancestor(self) -> None:
        # 「登录」文字本身不可点击，应自动上溯到 Button
        element = snapshot.find_element("登录", "dev")
        self.assertEqual(element["id"], "n3")
        self.assertTrue(element["clickable"])

    def test_text_equals_and_id_prefix(self) -> None:
        self.assertEqual(
            snapshot.find_element("text=欢迎回来", "dev")["id"], "n1"
        )
        self.assertEqual(
            snapshot.find_element("id=username", "dev")["id"], "n2"
        )

    def test_content_desc(self) -> None:
        self.assertEqual(snapshot.find_element("desc=登录按钮", "dev")["id"], "n3")

    def test_coordinate(self) -> None:
        self.assertEqual(snapshot.find_element("360,900", "dev")["center"], [360, 900])

    def test_not_found(self) -> None:
        with self.assertRaises(AgentAndroidError):
            snapshot.find_element("不存在的按钮", "dev")

    def test_empty_target_rejected(self) -> None:
        # 空 target 会匹配到无文字的布局节点，必须直接报错而不是随机点
        with self.assertRaises(AgentAndroidError):
            snapshot.find_element("   ", "dev")


class InputEscapeTest(unittest.TestCase):
    def test_space_and_specials(self) -> None:
        self.assertEqual(_escape_input("hello world"), "hello%sworld")
        self.assertEqual(_escape_input("a&b"), "a\\&b")
        self.assertEqual(_escape_input("abc123"), "abc123")

    def test_newline_and_tab_rejected(self) -> None:
        # 换行会被设备端 sh 当成命令分隔符，必须拒绝而不是转义
        for bad in ("a\nb", "a\rb", "a\tb"):
            with self.assertRaises(AgentAndroidError):
                _escape_input(bad)

    def test_percent_is_escaped(self) -> None:
        # % 是 input text 的占位符前缀，不转义会把后面的字符吞掉
        self.assertEqual(_escape_input("50%"), "50\\%")


class ErrorContractTest(unittest.TestCase):
    """失败响应必须是 Agent 能直接决策的结构，而不只是一句人话。"""

    def test_fail_has_machine_fields(self) -> None:
        payload = fail("没有设备", "先 connect", code="device_offline",
                       retryable=True, actions=["agent-android recover"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "device_offline")
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["actions"], ["agent-android recover"])


class NthMatchTest(unittest.TestCase):
    """同名元素按 @N 选第几个（列表里多个「确定」是高频场景）。"""

    XML = ('<hierarchy><node text="确定" class="Button" clickable="true" '
           'bounds="[0,100][100,150]" /><node text="确定" class="Button" '
           'clickable="true" bounds="[0,300][100,350]" /></hierarchy>')

    def setUp(self) -> None:
        nodes = snapshot.parse_nodes(self.XML)
        self.patcher = patch(
            "agent_android.snapshot.load_snapshot",
            return_value={"nodes": nodes},
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_first(self) -> None:
        self.assertEqual(snapshot.find_element("确定@1", "dev")["id"], "n0")

    def test_second(self) -> None:
        self.assertEqual(snapshot.find_element("确定@2", "dev")["id"], "n1")
        self.assertEqual(snapshot.find_element("text=确定@2", "dev")["id"], "n1")

    def test_out_of_range(self) -> None:
        with self.assertRaises(AgentAndroidError):
            snapshot.find_element("确定@3", "dev")


class CrashParseTest(unittest.TestCase):
    SAMPLE = (
        "11-14 13:38:25.057  5089  5089 E AndroidRuntime: FATAL EXCEPTION: main\n"
        "11-14 13:38:25.057  5089  5089 E AndroidRuntime: Process: com.coco.agentandroid, PID: 5089\n"
        "11-14 13:38:25.057  5089  5089 E AndroidRuntime: java.lang.NullPointerException: boom\n"
        "11-14 13:38:25.057  5089  5089 E AndroidRuntime: \tat com.coco.MainActivity.onCreate(MainActivity.kt:12)\n"
        "11-14 13:38:25.057  5089  5089 E AndroidRuntime: \tat android.app.Activity.performCreate(Activity.java:8000)\n"
    )

    def test_parse_single_crash(self) -> None:
        items = actions.parse_crashes(self.SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["process"], "com.coco.agentandroid")
        self.assertIn("NullPointerException", items[0]["exception"])
        self.assertEqual(len(items[0]["frames"]), 2)

    def test_limit_keeps_latest(self) -> None:
        text = self.SAMPLE + self.SAMPLE.replace("com.coco.agentandroid", "com.other.app")
        items = actions.parse_crashes(text, limit=1)
        self.assertEqual(items[0]["process"], "com.other.app")


class PasswordMaskTest(unittest.TestCase):
    """密码框默认脱敏：snapshot 会进模型、进缓存、进日志，明文出去就收不回。"""

    XML = ('<hierarchy><node text="hunter2" class="android.widget.EditText" '
           'password="true" bounds="[0,0][100,50]" /></hierarchy>')

    def test_masked_by_default(self) -> None:
        node = snapshot.parse_nodes(self.XML)[0]
        self.assertEqual(node["text"], "••••••")
        self.assertTrue(node["masked"])

    def test_unmask_env_switch(self) -> None:
        import os
        os.environ["AGENT_ANDROID_UNMASK"] = "1"
        try:
            node = snapshot.parse_nodes(self.XML)[0]
            self.assertEqual(node["text"], "hunter2")
        finally:
            os.environ.pop("AGENT_ANDROID_UNMASK", None)


class CompactTest(unittest.TestCase):
    def test_drops_heavy_fields(self) -> None:
        node = {
            "id": "n0", "text": "登录", "class": "Button",
            "bounds": [0, 0, 10, 10], "center": [5, 5],
            "clickable": True, "editable": False, "scrollable": False,
            "checked": False, "parent": "n-1", "depth": 0,
            "password": True, "resource_id": "x:id/y",
        }
        out = snapshot._compact(node)
        self.assertEqual(out["id"], "n0")
        # 父节点、深度、密码标记、resource_id 对执行动作没用来，省掉
        for heavy in ("parent", "depth", "password", "resource_id"):
            self.assertNotIn(heavy, out)


class InvisibleNodeTest(unittest.TestCase):
    """零面积节点（离屏占位）不能参与排序，否则点下去会落在 (0,0)。"""

    def _node(self, node_id: str, bounds: list[int]) -> dict:
        return {"id": node_id, "bounds": bounds, "clickable": True, "text": ""}

    def test_zero_area_nodes_dropped(self) -> None:
        nodes = [self._node("n0", [0, 0, 0, 0]), self._node("n1", [10, 10, 100, 100])]
        kept = snapshot._drop_invisible(nodes)
        self.assertEqual([n["id"] for n in kept], ["n1"])

    def test_all_invisible_falls_back(self) -> None:
        # 全为零面积时不静默返回空列表，交给 _ensure_visible 报明确错误
        nodes = [self._node("n0", [0, 0, 0, 0])]
        self.assertEqual(len(snapshot._drop_invisible(nodes)), 1)

    def test_ensure_visible_raises(self) -> None:
        with self.assertRaises(AgentAndroidError):
            snapshot._ensure_visible(self._node("n0", [0, 0, 0, 0]))

    def test_negative_bounds_supported(self) -> None:
        # 分屏/自由窗口下坐标可为负，正则必须带负号
        xml = ('<?xml version="1.0"?><hierarchy rotation="0">'
               '<node text="确定" class="android.widget.Button" clickable="true" '
               'bounds="[-40,100][200,180]" /></hierarchy>')
        node = snapshot.parse_nodes(xml)[0]
        self.assertEqual(node["bounds"], [-40, 100, 200, 180])


if __name__ == "__main__":
    unittest.main()
