import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("mira_health", Path(__file__).resolve().parents[1] / "deploy/mira_health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTests(unittest.TestCase):
    def test_restart_waits_for_procd_kill_timeout(self):
        marker = dict(pid=7, monotonic=1000)
        self.assertTrue(health.restart_pending(marker, 7, 1060))
        self.assertTrue(health.restart_pending(marker, 7, 1120))
        self.assertFalse(health.restart_pending(marker, 7, 1180))
        self.assertFalse(health.restart_pending(marker, 8, 1060))
        self.assertFalse(health.restart_pending({}, 7, 1060))

    def test_fresh_worker_ignores_wrong_rtc(self):
        self.assertIsNone(health.heartbeat_problem(dict(pid=7, monotonic=999, timestamp=-999999), 7, 1000, 500))

    def test_frozen_worker_detected_even_if_process_exists(self):
        self.assertIsNotNone(health.heartbeat_problem(dict(pid=7, monotonic=700), 7, 1000, 500))

    def test_old_process_heartbeat_cannot_hide_failure(self):
        self.assertIsNotNone(health.heartbeat_problem(dict(pid=6, monotonic=999), 7, 1000, 500))

    def test_startup_grace_and_missing_heartbeat(self):
        self.assertIsNone(health.heartbeat_problem({}, 7, 1000, 179))
        self.assertIsNotNone(health.heartbeat_problem({}, 7, 1000, 180))
