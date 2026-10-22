import json
import unittest
import urllib.error
from unittest.mock import patch
from helpers import Fixture


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
    def tearDown(self):
        self.f.cleanup()

    def test_download_auto_plays_and_reports_truth(self):
        self.f.loaded()
        self.assertEqual(self.f.controller.state, "playing")
        self.assertTrue(any(body.get("state") == "PLAYING" and retained
                            for topic, body, retained in self.f.messages if topic == "state"))

    def test_pause_resume_preserve_position_and_session(self):
        self.f.loaded()
        self.f.audio.position = 12.5
        stops = self.f.audio.stops
        self.f.command("pause")
        self.assertEqual(self.f.controller.state, "paused")
        self.f.command("resume")
        self.assertEqual(self.f.controller.state, "playing")
        self.assertEqual(self.f.audio.position, 12.5)
        self.assertEqual(self.f.audio.stops, stops)
        self.assertEqual(len(self.f.audio.starts), 1)

    def test_volume_responsive_during_download(self):
        self.f.submit_file()
        self.f.command("set_volume", volume=40)
        self.assertEqual(self.f.audio.volume, 40)
        self.assertEqual(len(self.f.controller.requests), 1)

    def test_replay_cached_file_from_zero(self):
        self.f.loaded()
        self.f.audio.position = 42
        self.f.command("replay")
        self.f.tick()
        self.assertEqual(self.f.audio.position, 0)
        self.assertEqual(len(self.f.audio.starts), 2)
        self.assertEqual(len(self.f.executor.items), 1)

    def test_stop_cancels_autoplay_of_pending_download(self):
        self.f.submit_file()
        self.f.command("stop")
        self.f.executor.finish()
        self.f.tick()
        self.assertEqual(self.f.audio.starts, [])
        self.assertEqual(self.f.controller.store.data["media"]["media-1"]["status"], "ready")

    def test_play_result_completes_only_after_mpd_starts(self):
        self.f.loaded()
        self.f.command("stop")
        self.f.audio.poll = lambda: dict(state="preparing")
        self.f.command("play", media_id="media-1")
        item = self.f.controller.store.data["commands"]["cmd-play"]
        self.assertEqual(item["status"], "accepted")
        self.f.audio.poll = lambda: dict(state="playing", elapsed=0)
        self.f.tick()
        self.assertEqual(item["status"], "completed")

    def test_duplicate_command_no_second_effect(self):
        self.f.loaded()
        value = self.f.command("replay")
        starts = len(self.f.audio.starts)
        self.f.controller.submit("command", json.dumps(value).encode())
        self.f.tick()
        self.assertEqual(len(self.f.audio.starts), starts)

    def test_conflicting_command_id_rejected(self):
        self.f.command("set_volume", command_id="same", volume=25)
        self.f.command("set_volume", command_id="same", volume=70)
        self.assertEqual(self.f.audio.volume, 25)
        self.assertTrue(any(body.get("error") == "conflicting_command_id"
                            for _, body, _ in self.f.messages))

    def test_expired_or_wrong_device_command_ignored(self):
        for changes in (dict(expires_at=self.f.time - 1), dict(device_id="another")):
            value = dict(command_id="bad", device_id="mira-01", action="set_volume",
                         volume=75, expires_at=self.f.time + 60)
            value.update(changes)
            self.f.controller.submit("command", json.dumps(value).encode())
            self.f.tick()
        self.assertEqual(self.f.audio.volume, 30)

    def test_retained_actions_ignored(self):
        self.assertFalse(self.f.controller.submit("file", json.dumps(self.f.event()).encode(), True))
        self.assertFalse(self.f.controller.submit("command", b"{}", True))
        self.f.tick()
        self.assertEqual(self.f.controller.requests, {})

    def test_duplicate_file_never_downloads_twice(self):
        self.f.loaded()
        self.f.submit_file()
        self.assertEqual(len(self.f.executor.items), 1)
        self.assertEqual(len(self.f.audio.starts), 1)

    def test_fifo_even_if_later_download_finishes_first(self):
        self.f.submit_file("first")
        self.f.submit_file("second")
        self.f.executor.finish(1)
        self.f.tick()
        self.assertEqual(self.f.audio.starts, [])
        self.f.executor.finish(0)
        self.f.tick()
        self.f.tick()
        self.assertEqual(self.f.controller.current, "first")

    def test_finished_track_kept_for_replay_and_next_starts(self):
        self.f.loaded("first")
        self.f.submit_file("second")
        self.f.executor.finish(1)
        self.f.tick()
        self.f.audio.state = "idle"
        self.f.tick()
        self.assertEqual(self.f.controller.current, "second")
        self.assertTrue(self.f.controller.verified_path("first").exists())

    def two_tracks_played(self):
        self.f.loaded("first")
        self.f.submit_file("second")
        self.f.executor.finish(1)
        self.f.tick()
        self.f.audio.state = "idle"   # first ends -> second auto-plays
        self.f.tick()
        self.f.audio.state = "idle"   # second ends
        self.f.tick()
        self.assertEqual((self.f.controller.state, self.f.controller.current), ("idle", None))

    def test_next_and_previous_cycle_through_cached_tracks(self):
        self.two_tracks_played()
        self.f.command("next", command_id="n1")
        self.assertEqual(self.f.controller.current, "first")       # wraps after last
        self.f.command("next", command_id="n2")
        self.assertEqual(self.f.controller.current, "second")
        self.f.command("previous", command_id="p1")
        self.assertEqual(self.f.controller.current, "first")
        self.f.tick()
        self.assertEqual(self.f.controller.store.data["commands"]["p1"]["status"], "completed")

    def test_resume_after_track_ended_replays_last(self):
        self.two_tracks_played()
        self.f.command("resume")
        self.assertEqual(self.f.controller.current, "second")

    def test_resume_with_empty_library_fails(self):
        self.f.command("resume")
        self.assertEqual(self.f.controller.store.data["commands"]["cmd-resume"]["error"], "not_paused")

    def test_next_with_empty_library_fails(self):
        self.f.command("next")
        self.assertEqual(self.f.controller.store.data["commands"]["cmd-next"]["error"], "no_media")

    def test_restart_idle_with_volume_and_library(self):
        self.f.loaded()
        self.f.command("set_volume", volume=47)
        self.f.controller.close()
        restarted = self.f.new_controller()
        self.assertEqual(restarted.state, "idle")
        self.assertEqual(restarted.play_queue, [])
        self.assertEqual(restarted.store.data["volume"], 47)
        self.assertTrue(restarted.verified_path("media-1").exists())
        restarted.close()

    def test_pending_command_failed_after_restart(self):
        self.f.loaded()
        self.f.command("stop")
        self.f.audio.poll = lambda: dict(state="preparing")
        self.f.command("play", media_id="media-1")
        restarted = self.f.new_controller()
        self.assertEqual(restarted.store.data["commands"]["cmd-play"]["status"], "failed")
        self.assertEqual(restarted.play_queue, [])

    def test_corrupt_cache_not_played(self):
        self.f.loaded()
        self.f.command("stop")
        self.f.controller.verified_path("media-1").write_bytes(b"corrupt")
        starts = len(self.f.audio.starts)
        self.f.command("play", media_id="media-1")
        self.assertEqual(len(self.f.audio.starts), starts)
        self.assertEqual(self.f.controller.store.data["commands"]["cmd-play"]["error"], "cached_media_corrupt")

    def test_read_timeout_on_weak_wifi_is_retried(self):
        class Py39SocketTimeout(OSError):
            pass  # on Python 3.9 socket.timeout is an OSError, not a TimeoutError
        self.f.submit_file()
        with patch("mira_player.controller.socket.timeout", Py39SocketTimeout):
            self.f.executor.finish(error=Py39SocketTimeout("timed out"))
            self.f.tick()
        self.assertEqual(self.f.controller.store.data["media"]["media-1"]["error"], "network_retry")
        self.f.time += 31
        self.f.tick()
        self.f.executor.finish(1)
        self.f.tick()
        self.f.tick()
        self.assertEqual(self.f.controller.state, "playing")

    def test_network_retry_without_blocking_volume(self):
        self.f.submit_file()
        self.f.executor.finish(error=urllib.error.URLError("offline"))
        self.f.tick()
        self.f.command("set_volume", volume=15)
        self.assertEqual(self.f.audio.volume, 15)
        self.f.time += 31
        self.f.tick()
        self.f.executor.finish(1)
        self.f.tick()
        self.f.tick()
        self.assertEqual(self.f.controller.state, "playing")

    def test_hash_failure_and_404_terminal(self):
        for error in (ValueError("hash mismatch"),
                      urllib.error.HTTPError("https://example.invalid", 404, "missing", {}, None)):
            f = Fixture()
            try:
                f.submit_file()
                f.executor.finish(error=error)
                f.tick()
                self.assertEqual(f.controller.store.data["media"]["media-1"]["status"], "failed")
                self.assertEqual(f.audio.starts, [])
            finally:
                f.cleanup()

    def test_cache_evicts_idle_media(self):
        self.f.cfg["max_cache_bytes"] = 20
        self.f.loaded("first")
        self.f.command("stop")
        self.f.submit_file("second")
        self.assertEqual(self.f.controller.store.data["media"]["first"]["status"], "evicted")

    def test_active_media_never_evicted(self):
        self.f.cfg["max_cache_bytes"] = 20
        self.f.loaded("first")
        self.f.submit_file("second")
        self.assertEqual(len(self.f.executor.items), 1)
        self.assertTrue(self.f.controller.verified_path("first").exists())

    def test_pending_bound_survives_stop(self):
        self.f.cfg["max_pending"] = 1
        self.f.submit_file("first")
        self.f.command("stop")
        self.f.submit_file("second")
        self.assertEqual(len(self.f.executor.items), 1)

    def test_oversized_payload_rejected_before_queueing(self):
        self.assertFalse(self.f.controller.submit("file", b"x" * 16385))
        self.assertFalse(self.f.controller.submit("command", b"x" * 16385))
        self.assertTrue(self.f.controller.inbox.empty())

    def test_partial_download_not_double_counted_in_cache_budget(self):
        self.f.cfg["max_cache_bytes"] = 30
        self.f.submit_file("first")
        part = self.f.controller.path_for(self.f.event("first")).with_suffix(".mp3.part")
        part.write_bytes(b"x" * 10)
        self.f.submit_file("second")
        self.assertEqual(len(self.f.executor.items), 2)

    def test_invalid_pause_returns_failure(self):
        self.f.command("pause")
        self.assertEqual(self.f.controller.store.data["commands"]["cmd-pause"]["error"], "not_playing")


if __name__ == "__main__":
    unittest.main()
