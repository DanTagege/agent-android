"""模拟器端口表与识别的单元测试。"""

from __future__ import annotations

import unittest

from agent_android import emulators


class PortParseTest(unittest.TestCase):
    def test_ip_port(self) -> None:
        self.assertEqual(emulators.port_of("127.0.0.1:16384"), 16384)
        self.assertEqual(emulators.port_of("127.0.0.1:62001"), 62001)

    def test_emulator_serial(self) -> None:
        self.assertEqual(emulators.port_of("emulator-5554"), 5554)

    def test_usb_serial_has_no_port(self) -> None:
        self.assertIsNone(emulators.port_of("R58M12ABCDE"))

    def test_identify_mumu(self) -> None:
        self.assertIn("MuMu", emulators.identify("127.0.0.1:16384") or "")

    def test_identify_ldplayer(self) -> None:
        self.assertIn("LDPlayer", emulators.identify("emulator-5554") or "")

    def test_identify_unknown_port(self) -> None:
        self.assertIsNone(emulators.identify("127.0.0.1:9999"))

    def test_catalog_covers_main_emulators(self) -> None:
        names = {item["emulator"] for item in emulators.catalog()}
        for expected in ("MuMu 12", "LDPlayer", "Nox", "BlueStacks 5", "WSA"):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()
