"""多语言相关测试：Unicode 归一化、非 ASCII 输入路由。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_android import actions, snapshot


def make_node(node_id: str, text: str, *, clickable: bool = True,
              desc: str = "") -> dict:
    return {
        "id": node_id, "index": 0, "text": text, "content_desc": desc,
        "resource_id": "", "class": "TextView", "package": "com.demo",
        "bounds": [0, 0, 200, 80], "center": [100, 40],
        "clickable": clickable, "long_clickable": False, "scrollable": False,
        "editable": False, "enabled": True, "checked": False, "focused": False,
    }


def cache(nodes: list[dict]) -> dict:
    return {"nodes": nodes, "screen": [1080, 1920], "package": "com.demo"}


class UnicodeMatchTest(unittest.TestCase):
    """同一个字在不同来源可能编码不同（预组合 vs 分解），必须归一化后再比。"""

    def test_decomposed_ui_matches_precomposed_input(self) -> None:
        # 界面给的是 e + U+0301（分解形式），用户输入的是预组合 é（U+00E9）
        node = make_node("n1", "Cafe\u0301 Madrid")
        with patch("agent_android.snapshot.load_snapshot", return_value=cache([node])):
            found = snapshot.find_element("Café", "dev")
        self.assertEqual(found["id"], "n1")

    def test_precomposed_ui_matches_decomposed_input(self) -> None:
        node = make_node("n1", "Größe")
        decomposed = "Gro\u0308ße"  # o + 组合分音符
        with patch("agent_android.snapshot.load_snapshot", return_value=cache([node])):
            found = snapshot.find_element(f"text={decomposed}", "dev")
        self.assertEqual(found["id"], "n1")

    def test_korean_and_cyrillic(self) -> None:
        nodes = [make_node("n1", "안녕하세요"), make_node("n2", "Привет")]
        with patch("agent_android.snapshot.load_snapshot", return_value=cache(nodes)):
            self.assertEqual(snapshot.find_element("안녕", "dev")["id"], "n1")
            self.assertEqual(snapshot.find_element("text=Привет", "dev")["id"], "n2")

    def test_japanese_mixed_scripts(self) -> None:
        # 汉字 + 平假名 + 片假名混排是日文界面的常态
        node = make_node("n1", "設定を開く（アプリ）")
        with patch("agent_android.snapshot.load_snapshot", return_value=cache([node])):
            self.assertEqual(snapshot.find_element("設定", "dev")["id"], "n1")
            self.assertEqual(snapshot.find_element("アプリ", "dev")["id"], "n1")

    def test_miss_reports_closest_candidate(self) -> None:
        nodes = [make_node("n1", "网络和互联网")]
        with patch("agent_android.snapshot.load_snapshot", return_value=cache(nodes)):
            with self.assertRaises(Exception) as ctx:
                snapshot.find_element("网络与互联网", "dev")
        hint = getattr(ctx.exception, "hint", "")
        self.assertIn("互联网", hint)


class InputRoutingTest(unittest.TestCase):
    """非 ASCII 必须走伴侣 App：adb shell input text 会把这些字符弄丢。"""

    def _routed_to_bridge(self, text: str) -> bool:
        sent: dict[str, object] = {}

        def fake_bridge(device, payload, **kwargs):
            sent.update(payload)
            return {"ok": True}

        with patch("agent_android.actions.input_text") as ascii_path, \
             patch("agent_android.bridge.read_token", return_value="T"), \
             patch("agent_android.bridge.action", side_effect=fake_bridge):
            actions.input_any(text, device="dev")
        if ascii_path.called:
            return False
        return sent.get("text") == text

    def test_latin_accents_go_to_bridge(self) -> None:
        self.assertTrue(self._routed_to_bridge("Größe niño açúcar"))

    def test_cjk_goes_to_bridge(self) -> None:
        self.assertTrue(self._routed_to_bridge("中文 日本語 한국어"))

    def test_cyrillic_goes_to_bridge(self) -> None:
        self.assertTrue(self._routed_to_bridge("Привет мир"))


if __name__ == "__main__":
    unittest.main()
