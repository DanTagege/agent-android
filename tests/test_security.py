"""安全相关：密码脱敏、命令参数校验。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent_android import actions, snapshot
from agent_android.util import AgentAndroidError


def node(node_id: str, text: str, *, password: bool = False,
         clickable: bool = False) -> dict:
    return {
        "id": node_id, "index": 0, "text": text, "content_desc": "",
        "resource_id": "", "class": "EditText" if password else "TextView",
        "package": "com.demo", "bounds": [0, 0, 200, 80], "center": [100, 40],
        "clickable": clickable, "long_clickable": False, "scrollable": False,
        "editable": password, "enabled": True, "checked": False,
        "focused": False, "password": password,
    }


class PasswordMaskTest(unittest.TestCase):
    def test_bridge_node_gets_masked(self) -> None:
        """bridge 通道的节点不经过 parse_nodes，必须在合流处脱敏。"""
        nodes = [node("n1", "hunter2", password=True), node("n2", "公开文字")]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_ANDROID_UNMASK", None)
            snapshot.mask_passwords(nodes)
        self.assertEqual(nodes[0]["text"], "••••••")
        self.assertEqual(nodes[1]["text"], "公开文字")

    def test_unmask_env_keeps_plaintext(self) -> None:
        nodes = [node("n1", "hunter2", password=True)]
        with patch.dict(os.environ, {"AGENT_ANDROID_UNMASK": "1"}):
            snapshot.mask_passwords(nodes)
        self.assertEqual(nodes[0]["text"], "hunter2")

    def test_take_masks_bridge_payload(self) -> None:
        """端到端：auto 模式走 bridge 时，缓存与输出都不能有明文。"""
        payload = {"nodes": [node("n1", "s3cret", password=True),
                             node("n2", "正常文字", clickable=True)]}
        probe = {"ime": False, "package": "com.demo",
                 "size": [1080, 1920], "multi_window": False}

        with patch.dict(os.environ, {}, clear=False), \
             patch.object(snapshot, "_bridge_snapshot", return_value=payload), \
             patch("agent_android.adb.probe", return_value=probe), \
             patch.object(snapshot, "save_snapshot") as saved:
            os.environ.pop("AGENT_ANDROID_UNMASK", None)
            data = snapshot.take("dev", prefer="accessibility")

        self.assertEqual(data["nodes"][0]["text"], "••••••")
        self.assertEqual(data["source"], "accessibility")
        # 落盘的那份也必须是脱敏后的
        stored = saved.call_args[0][1]["nodes"]
        self.assertEqual(stored[0]["text"], "••••••")


class IdentValidationTest(unittest.TestCase):
    def test_rejects_shell_metacharacters(self) -> None:
        for bad in ["com.x; rm -rf /sdcard", "com.x && id", "com.x|id",
                    "com.x`id`", "com.x$(id)"]:
            with self.assertRaises(AgentAndroidError) as ctx:
                actions._safe_ident(bad, "包名")
            self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_accepts_normal_identifiers(self) -> None:
        for good in ["com.android.settings", "com.x/.MainActivity",
                     "org.foo_bar.app", "com.x-y.z"]:
            self.assertEqual(actions._safe_ident(good, "包名"), good)

    def test_logcat_rejects_bad_level(self) -> None:
        with self.assertRaises(AgentAndroidError) as ctx:
            actions.logcat(device="dev", level="E'; id; '")
        self.assertEqual(ctx.exception.code, "invalid_argument")


if __name__ == "__main__":
    unittest.main()
