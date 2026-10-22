import json
import unittest

from helpers import Fixture
from mira_player.backend import classify
from mira_player.commands import parse


class WebPlayerTests(unittest.TestCase):
    """Repeat, seek and the status fields the SOMICS web player panel reads."""

    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.cleanup()

    def last_state(self):
        return [body for kind, body, _ in self.f.messages if kind == "state"][-1]

    def end_track(self):
        self.f.audio.state = "idle"
        self.f.tick()

    def test_status_uses_web_fields_while_playing_and_after_done(self):
        self.f.loaded("aaaaaaaa-0000-0000-0000-000000000001")
        self.f.audio.position = 12.34
        self.f.tick()
        self.f.controller.report()
        body = self.last_state()
        self.assertEqual((body["state"], body["fileId"], body["fileName"], body["positionSeconds"]),
                         ("PLAYING", "aaaaaaaa-0000-0000-0000-000000000001",
                          "aaaaaaaa-0000-0000-0000-000000000001.mp3", 12.3))
        self.assertEqual((body["repeat"], body["volume"], body["playerState"]), ("off", 30, "playing"))
        self.end_track()
        body = self.last_state()
        self.assertEqual((body["state"], body["fileId"]), ("DONE", "aaaaaaaa-0000-0000-0000-000000000001"))

    def test_idle_without_track_reports_idle(self):
        self.f.controller.report()
        body = self.last_state()
        self.assertEqual((body["state"], body["fileId"]), ("IDLE", None))

    def test_repeat_one_loops_current_track_and_off_stops(self):
        self.f.loaded("song")
        self.f.command("set_repeat", command_id="r1", repeat="one")
        self.end_track()
        self.assertEqual(self.f.controller.current, "song")
        self.assertEqual(len(self.f.audio.starts), 2)
        self.f.tick()
        self.f.command("set_repeat", command_id="r2", repeat="off")
        self.end_track()
        self.assertIsNone(self.f.controller.current)
        self.assertEqual(self.f.controller.store.data["repeat"], "off")

    def test_repeat_survives_restart(self):
        self.f.command("set_repeat", repeat="one")
        self.f.controller.close()
        self.f.controller = self.f.new_controller()
        self.assertEqual(self.f.controller.snapshot()["repeat"], "one")

    def test_seek_moves_current_track_only(self):
        self.f.loaded("song")
        self.f.command("seek", command_id="s1", media_id="song", position=42.5)
        self.assertEqual((self.f.audio.position, self.f.controller.elapsed), (42.5, 42.5))
        self.f.command("seek", command_id="s2", media_id="other", position=5)
        self.assertEqual(self.f.controller.store.data["commands"]["s2"]["error"], "not_playing_this_media")

    def test_web_repeat_and_seek_buttons_map_to_commands(self):
        cfg = dict(self.f.cfg)
        now = self.f.time
        _, payload = classify(json.dumps({"id": "r", "type": "control", "action": "set_repeat",
                                          "repeat": "one"}).encode(), cfg, now)
        self.assertEqual(parse(payload, "mira-01", now)["repeat"], "one")
        _, payload = classify(json.dumps({"id": "s", "type": "control", "action": "seek",
                                          "fileId": "abc", "positionSeconds": 30}).encode(), cfg, now)
        command = parse(payload, "mira-01", now)
        self.assertEqual((command["media_id"], command["position"]), ("abc", 30))

    def test_seek_reports_actual_position_and_keeps_pause(self):
        self.f.loaded("song")
        self.f.command("pause")
        def seek(position):
            self.f.audio.position = 29.97
            return 29.97
        self.f.audio.seek = seek
        self.f.command("seek", media_id="song", position=30)
        self.assertEqual(self.f.controller.elapsed, 29.97)
        self.assertEqual(self.last_state()["state"], "PAUSED")
        self.assertEqual(self.last_state()["positionSeconds"], 30.0)

    def test_hardware_duration_available_without_upload_metadata(self):
        self.f.loaded("song")
        self.f.audio.poll = lambda: dict(state="playing", elapsed=12, duration=156.2)
        self.f.controller.poll_audio()
        self.f.controller.report()
        self.assertEqual(self.last_state()["durationSeconds"], 156.2)

    def test_invalid_repeat_and_seek_rejected(self):
        now = self.f.time
        base = dict(command_id="x", device_id="mira-01", expires_at=now + 60)
        for bad in (dict(action="set_repeat", repeat="all"), dict(action="seek", position=3),
                    dict(action="seek", media_id="m", position=-1), dict(action="pause", repeat="one")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse(json.dumps(dict(base, **bad)).encode(), "mira-01", now)

    def test_upload_duration_is_reported(self):
        command = dict(type="file", action="download", fileId="d1", fileName="d1.mp3",
                       mimeType="audio/mpeg", size=14, sha256=self.f.event("d1")["sha256"],
                       durationSeconds=183.4)
        kind, payload = classify(json.dumps(command).encode(), self.f.cfg)
        self.f.controller.submit(kind, payload)
        self.f.tick()
        self.f.executor.finish(len(self.f.executor.items) - 1)
        self.f.tick()
        self.f.tick()
        self.assertEqual(self.last_state()["durationSeconds"], 183.4)


if __name__ == "__main__":
    unittest.main()
