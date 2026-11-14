"""接入引导的处境判断测试。"""

from __future__ import annotations

import unittest

from agent_android import onboarding


def _devices(items: list[dict]) -> dict:
    return {
        "all": items,
        "usb": [d for d in items
                if ":" not in d["serial"] and not d["serial"].startswith("emulator-")],
        "emulator": [d for d in items
                     if ":" in d["serial"] or d["serial"].startswith("emulator-")],
        "unauthorized": [d for d in items if d["state"] == "unauthorized"],
        "offline": [d for d in items if d["state"] == "offline"],
    }


def _emu(running: bool) -> dict:
    return {"name": "MuMu 12", "port": 16384, "installed_at": "D:/MuMu", "running": running}


class ClassifyTest(unittest.TestCase):
    def test_ready(self) -> None:
        self.assertEqual(
            onboarding.classify(_devices([{"serial": "R58M", "state": "device"}]), []),
            ("ready", "none"),
        )

    def test_usb_unauthorized(self) -> None:
        self.assertEqual(
            onboarding.classify(_devices([{"serial": "R58M", "state": "unauthorized"}]), []),
            ("usb_unauthorized", "authorize_usb"),
        )

    def test_usb_offline(self) -> None:
        self.assertEqual(
            onboarding.classify(_devices([{"serial": "R58M", "state": "offline"}]), []),
            ("usb_offline", "replug_usb"),
        )

    def test_emulator_offline_is_not_usb(self) -> None:
        # 模拟器串号 offline 时不能建议用户拔线，应该重启/重连
        self.assertEqual(
            onboarding.classify(_devices([{"serial": "emulator-5554", "state": "offline"}]), []),
            ("emulator_offline", "restart_emulator"),
        )

    def test_emulator_running_but_not_ready(self) -> None:
        self.assertEqual(
            onboarding.classify(_devices([]), [_emu(running=True)]),
            ("emulator_starting", "wait_emulator"),
        )

    def test_emulator_installed_not_running(self) -> None:
        self.assertEqual(
            onboarding.classify(_devices([]), [_emu(running=False)]),
            ("emulator_installed_not_running", "start_emulator"),
        )

    def test_nothing_at_all(self) -> None:
        self.assertEqual(
            onboarding.classify(_devices([]), []),
            ("no_device", "install_emulator"),
        )


class CatalogTest(unittest.TestCase):
    def test_emulators_have_download_and_port(self) -> None:
        # 推荐必须有可执行信息：下载地址 + 默认端口 + 是否默认开 ADB
        for item in onboarding.EMULATORS:
            self.assertTrue(item["download"].startswith("https://"))
            self.assertIsInstance(item["port"], int)
            self.assertIsInstance(item["adb_default"], bool)

    def test_mumu_is_recommended_first(self) -> None:
        # 清单顺序即推荐顺序，第一个应该是开箱即用的 MuMu
        self.assertIn("MuMu", onboarding.EMULATORS[0]["name"])


if __name__ == "__main__":
    unittest.main()
