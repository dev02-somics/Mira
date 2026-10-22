import unittest

from helpers import Fixture


class StartRecoveryTests(unittest.TestCase):
    """A failed start must never leave the player stuck in ERROR."""

    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.cleanup()

    def busy_for(self, times):
        original, calls = self.f.audio.start, {"n": 0}
        def start(path, volume):
            calls["n"] += 1
            if calls["n"] <= times:
                raise RuntimeError("MPD_busy_with_another_player")
            return original(path, volume)
        self.f.audio.start = start

    def ticks(self, n):
        for _ in range(n):
            self.f.tick()

    def test_busy_audio_after_boot_retries_same_track(self):
        self.busy_for(2)
        self.f.loaded("song")
        self.assertEqual(self.f.controller.state, "error")
        self.ticks(30)
        self.assertEqual((self.f.controller.current, self.f.controller.state), ("song", "playing"))

    def test_error_clears_so_later_uploads_still_play(self):
        self.busy_for(10 ** 6)          # speaker never frees: give up on that track
        self.f.loaded("stuck")
        self.ticks(self.f.controller.MAX_START_RETRIES * 12)
        self.assertNotIn("stuck", self.f.controller.play_queue)
        self.f.audio.start = type(self.f.audio).start.__get__(self.f.audio)
        self.f.loaded("next")
        self.ticks(12)
        self.assertEqual(self.f.controller.current, "next")

    def test_bad_media_is_skipped_and_queue_continues(self):
        self.f.submit_file("bad")
        self.f.submit_file("good")
        self.f.executor.finish(0)
        self.f.executor.finish(1)
        self.f.controller.path_for(self.f.event("bad")).write_bytes(b"corrupted!!!!!")
        self.ticks(15)
        self.assertEqual(self.f.controller.current, "good")


if __name__ == "__main__":
    unittest.main()
