"""流程 runner 与资源解析的单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_android import adb, runner
from agent_android.util import AgentAndroidError


class LoadFlowTest(unittest.TestCase):
    def _write(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        handle.write(content)
        handle.close()
        return handle.name

    def test_missing_file(self) -> None:
        with self.assertRaises(AgentAndroidError) as ctx:
            runner.load_flow("definitely-not-here.json")
        self.assertEqual(ctx.exception.code, "flow_not_found")

    def test_bad_json(self) -> None:
        path = self._write("{not json")
        with self.assertRaises(AgentAndroidError) as ctx:
            runner.load_flow(path)
        self.assertEqual(ctx.exception.code, "flow_invalid_json")
        Path(path).unlink()

    def test_unknown_step_points_at_index(self) -> None:
        path = self._write(json.dumps({"steps": [{"tap": "a"}, {"fly": "b"}]}))
        with self.assertRaises(AgentAndroidError) as ctx:
            runner.load_flow(path)
        self.assertEqual(ctx.exception.code, "flow_unknown_step")
        self.assertIn("第 2 步", ctx.exception.message)
        Path(path).unlink()

    def test_multi_key_step_rejected(self) -> None:
        path = self._write(json.dumps({"steps": [{"tap": "a", "wait": 1}]}))
        with self.assertRaises(AgentAndroidError):
            runner.load_flow(path)
        Path(path).unlink()

    def test_valid(self) -> None:
        path = self._write(json.dumps({"name": "x", "steps": [{"tap": "a"}]}))
        flow = runner.load_flow(path)
        self.assertEqual(flow["name"], "x")
        Path(path).unlink()


class RunFlowTest(unittest.TestCase):
    def test_assert_passes_with_one_snapshot(self) -> None:
        data = {"package": "com.demo", "node_count": 12,
                "nodes": [{"text": "欢迎回来"}, {"text": "首页"}]}
        with patch("agent_android.runner.snapshot.take", return_value=data) as take:
            result = runner.run_flow(
                {"steps": [{"assert": {"text": ["欢迎", "首页"], "package": "com.demo"}}]},
                "dev",
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(take.call_count, 1)  # 多个条件只抓一次屏

    def test_assert_failure_reports_missing_text(self) -> None:
        data = {"package": "com.demo", "node_count": 3, "nodes": [{"text": "首页"}]}
        with patch("agent_android.runner.snapshot.take", return_value=data):
            result = runner.run_flow({"steps": [{"assert": {"text": "登录成功"}}]}, "dev")
        self.assertFalse(result["ok"])
        self.assertEqual(result["steps"][0]["detail"]["error_code"], "assert_failed")

    def test_stop_on_failure(self) -> None:
        with patch("agent_android.runner.actions.tap",
                   side_effect=AgentAndroidError("找不到元素", code="element_not_found")):
            result = runner.run_flow(
                {"steps": [{"tap": "a"}, {"wait": 0.01}, {"tap": "b"}]}, "dev")
        self.assertEqual(result["executed"], 1)
        self.assertEqual(result["failed"], 1)

    def test_continue_on_failure(self) -> None:
        with patch("agent_android.runner.actions.tap",
                   side_effect=AgentAndroidError("找不到元素")):
            result = runner.run_flow(
                {"on_failure": "continue",
                 "steps": [{"tap": "a"}, {"wait": 0.01}]}, "dev")
        self.assertEqual(result["executed"], 2)
        self.assertEqual(result["passed"], 1)

    def test_chinese_input_goes_through_bridge(self) -> None:
        with patch("agent_android.actions.input_text") as ascii_path, \
             patch("agent_android.bridge.read_token", return_value="ABC123"), \
             patch("agent_android.bridge.action", return_value={"ok": True}) as act:
            runner.run_flow({"steps": [{"input": {"text": "中文"}}]}, "dev")
        self.assertFalse(ascii_path.called, "中文不该走 adb 通道")
        sent = act.call_args[0][1]
        self.assertEqual(sent["text"], "中文")


class ServerConflictTest(unittest.TestCase):
    """5037 端口冲突要和「设备失效」区分开，否则会给出错误的修复指引。"""

    def test_detects_daemon_failures(self) -> None:
        for sample in (
            "cannot connect to daemon at tcp:5037",
            "error: failed to start the daemon",
            "connection refused 5037",
        ):
            matched = any(p.search(sample.lower()) for p in adb._SERVER_PATTERNS)
            self.assertTrue(matched, sample)

    def test_device_errors_are_not_port_conflicts(self) -> None:
        sample = "adb.exe: device '127.0.0.1:16384' not found"
        self.assertFalse(any(p.search(sample.lower()) for p in adb._SERVER_PATTERNS))


class ResourcesTest(unittest.TestCase):
    SAMPLE = (
        "  level: 37\n"
        "Filesystem       1K-blocks     Used Available Use% Mounted on\n"
        "/dev/block/dm-0    1048576   900000    148576  86% /sdcard\n"
    )

    def test_parse_battery_and_storage(self) -> None:
        parsed = adb._parse_resources(self.SAMPLE)
        self.assertEqual(parsed["battery"], 37)
        self.assertEqual(parsed["storage_free_mb"], 145)

    def test_parse_tolerates_garbage(self) -> None:
        parsed = adb._parse_resources("no data here")
        self.assertIsNone(parsed["battery"])
        self.assertIsNone(parsed["storage_free_mb"])


if __name__ == "__main__":
    unittest.main()
