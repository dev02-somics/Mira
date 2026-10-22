import json
from pathlib import Path
import tempfile
import unittest

from mira_wifi.mqtt import MqttSetup


class Result:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode, self.stderr = returncode, stderr


class MqttSetupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.config, self.status = self.home / "config.json", self.home / "status.json"
        self.calls, self.result, self.now = [], Result(), 1000.0

    def tearDown(self):
        self.dir.cleanup()

    def setup(self):
        def run(command, **kwargs):
            self.calls.append(command)
            return self.result if "--activate" in command else Result()
        return MqttSetup(home=self.home, service="/etc/init.d/s", status=self.status,
                         run=run, clock=lambda: self.now)

    def test_status_hides_password_and_reads_link(self):
        self.config.write_text(json.dumps({"device_id": "SOMICS-1", "password": "secret",
                                           "activated_at": "t"}))
        self.status.write_text(json.dumps({"mqtt_ready": True, "timestamp": self.now - 3}))
        status = self.setup().status()
        self.assertEqual(status, {"installed": True, "activated": True, "device_id": "SOMICS-1",
                                  "activated_at": "t", "link": "connected"})
        self.assertNotIn("secret", json.dumps(status))

    def test_stale_status_means_stopped(self):
        self.config.write_text(json.dumps({"device_id": "SOMICS-1"}))
        self.status.write_text(json.dumps({"mqtt_ready": True, "timestamp": self.now - 120}))
        status = self.setup().status()
        self.assertEqual((status["activated"], status["link"]), (False, "stopped"))

    def test_status_without_player(self):
        self.assertFalse(self.setup().status()["installed"])

    def test_activate_runs_player_then_restarts_service(self):
        result = self.setup().activate(" somics-000001 ", "abcd-efgh-jklm")
        self.assertIn("SOMICS-000001", result["message"])
        self.assertEqual(self.calls[0][-4:], ["--activate", "ABCD-EFGH-JKLM", "--device-id", "SOMICS-000001"])
        self.assertEqual(self.calls[1], ["/etc/init.d/s", "restart"])

    def test_failed_activation_keeps_service_and_explains(self):
        self.result = Result(1, b"2026 ERROR /api/iot/activate HTTP 502: Bad Gateway (code not consumed)\n")
        with self.assertRaisesRegex(ValueError, "Mã chưa bị dùng"):
            self.setup().activate("SOMICS-000001", "ABCD-EFGH-JKLM")
        self.assertEqual(len(self.calls), 1)

    def test_invalid_input_never_runs(self):
        for device, code in (("", "ABCD"), ("SOMICS-1", "a b"), ("x;rm", "ABCD"), (None, None)):
            with self.assertRaises(ValueError):
                self.setup().activate(device, code)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
