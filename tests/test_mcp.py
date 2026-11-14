"""MCP 协议层测试：真的起一个 stdio 进程，按 MCP 的报文格式对话。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class McpProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "agent_android", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, cwd=ROOT, env=env,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        self.assertTrue(line, "server 没有返回任何内容")
        return json.loads(line)

    def test_full_handshake(self) -> None:
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        init = self._recv()
        self.assertEqual(init["id"], 1)
        self.assertEqual(init["result"]["serverInfo"]["name"], "agent-android")
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", init["result"]["capabilities"])

        # 通知不需要回复，紧接着发 tools/list
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listing = self._recv()
        self.assertEqual(listing["id"], 2)
        tools = listing["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertIn("android_snapshot", names)
        self.assertIn("android_run_flow", names)
        self.assertGreaterEqual(len(names), 10)
        for tool in tools:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_method_returns_error(self) -> None:
        self._send({"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"})
        reply = self._recv()
        self.assertEqual(reply["id"], 9)
        self.assertEqual(reply["error"]["code"], -32601)

    def test_bad_json_line_does_not_kill_server(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write("{not json}\n")
        self.proc.stdin.flush()
        reply = self._recv()
        self.assertEqual(reply["error"]["code"], -32700)

        # 服务仍然活着
        self._send({"jsonrpc": "2.0", "id": 10, "method": "ping"})
        self.assertEqual(self._recv()["id"], 10)

    def test_tool_missing_argument(self) -> None:
        self._send({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                    "params": {"name": "android_tap", "arguments": {}}})
        reply = self._recv()
        self.assertTrue(reply["result"].get("isError"))
        self.assertIn("参数错误", reply["result"]["content"][0]["text"])

    def test_unknown_tool(self) -> None:
        self._send({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                    "params": {"name": "android_teleport", "arguments": {}}})
        reply = self._recv()
        self.assertTrue(reply["result"].get("isError"))


if __name__ == "__main__":
    unittest.main()
