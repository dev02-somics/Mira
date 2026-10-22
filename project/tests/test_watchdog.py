import unittest

from mira_player.app import Watchdog


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.dog = Watchdog(300, clock=lambda: self.now)

    def test_restarts_only_after_limit_without_mqtt(self):
        self.now = 299
        self.assertFalse(self.dog.expired(False))
        self.now = 300
        self.assertTrue(self.dog.expired(False))

    def test_any_healthy_moment_resets_the_timer(self):
        self.now = 250
        self.assertFalse(self.dog.expired(True))
        self.now = 500
        self.assertFalse(self.dog.expired(False))
        self.now = 550
        self.assertTrue(self.dog.expired(False))


if __name__ == "__main__":
    unittest.main()
