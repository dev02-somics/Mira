import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from helpers import Fixture
from mira_player.audio import HardwareAudio
from mira_player.broker import Broker


class MPD:
    def __init__(self):
        self.calls = []
        self.state, self.song, self.updating = "stop", None, False
        self.error = None
        self.elapsed, self.duration = 12.5, 120.0
    def command(self, text):
        self.calls.append(text)
        if text == "status":
            value = dict(state=self.state, volume="-1", elapsed=str(self.elapsed), duration=str(self.duration))
            if self.song is not None:
                value["songid"] = self.song
            if self.updating:
                value["updating_db"] = "1"
            if self.error:
                value["error"] = self.error
            return value
        if text == "clearerror":
            self.error = None
        if text.startswith("seekid "):
            self.elapsed = float(text.split()[2])
        if text.startswith("addid "):
            self.song = "7"
            return dict(Id="7")
        if text.startswith("playid "):
            self.state = "play"
        elif text == "pause 1":
            self.state = "pause"
        elif text == "pause 0":
            self.state = "play"
        elif text == "stop":
            self.state = "stop"
        elif text.startswith("deleteid "):
            self.song = None
        return {}
    def close(self):
        self.calls.append("close")


class Client:
    def __init__(self):
        self.published, self.subscriptions, self.will = [], [], None
    def will_set(self, *args, **kwargs):
        self.will = (args, kwargs)
    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))
        return 0, len(self.subscriptions)
    def publish(self, *args, **kwargs):
        self.published.append((args, kwargs))
        return SimpleNamespace(rc=0, wait_for_publish=lambda **kwargs: None)
    def disconnect(self):
        pass
    def loop_stop(self):
        pass


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.f, self.mpd = Fixture(), MPD()
        self.audio = HardwareAudio.__new__(HardwareAudio)
        self.audio.cfg, self.audio.factory, self.audio.client = self.f.cfg, lambda: self.mpd, self.mpd
        self.audio.song_id = self.audio.preparing = None
        self.audio.volume, self.audio.controls = 30, {"Headphone"}
        self.audio.record = dict(old={}, suspended=[], boot_id="test", song_id=None)
        self.audio.lease = Path(self.f.tmp.name) / "lease.json"
        self.mixer = patch("mira_player.audio.hw.mixer").start()
        self.gpio = patch("mira_player.audio.hw.gpio").start()
    def tearDown(self):
        patch.stopall()
        self.f.cleanup()

    def test_mpd_preparation_nonblocking(self):
        self.mpd.updating = True
        self.audio.start(Path(self.f.cfg["cache_dir"]) / "song.mp3", 30)
        self.assertEqual(self.audio.poll()["state"], "preparing")
        self.assertFalse(any(cmd.startswith("playid") for cmd in self.mpd.calls))
        self.mpd.updating = False
        self.assertEqual(self.audio.poll()["state"], "playing")

    def test_old_missing_file_error_does_not_block_new_song(self):
        self.audio.client = None
        self.audio.boot_id = lambda: "test"
        self.audio.check = lambda: {"Headphone"}
        self.mpd.error = "Failed to open old.mp3: No such file or directory"
        with patch("mira_player.audio.hw.find_mgwp", return_value=[]):
            self.audio.start(Path(self.f.cfg["cache_dir"]) / "new.mp3", 30)
        self.assertEqual(self.audio.poll()["state"], "playing")
        self.assertIn("clearerror", self.mpd.calls)
        self.assertNotIn("clear", self.mpd.calls)

    def test_busy_player_error_is_not_cleared(self):
        self.audio.client = None
        self.audio.check = lambda: {"Headphone"}
        self.mpd.state, self.mpd.error = "pause", "another player error"
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.audio.acquire()
        self.assertNotIn("clearerror", self.mpd.calls)

    def test_pause_resume_keeps_song_and_connection(self):
        self.audio.start(Path(self.f.cfg["cache_dir"]) / "song.mp3", 30)
        self.audio.poll()
        self.audio.pause()
        self.assertEqual(self.audio.poll()["state"], "paused")
        self.audio.resume()
        self.assertEqual(self.audio.poll()["state"], "playing")
        self.assertFalse(any(cmd.startswith("deleteid") for cmd in self.mpd.calls))
        self.assertNotIn("close", self.mpd.calls)

    def test_volume_changes_alsa_without_restart(self):
        self.audio.set_volume(45)
        self.mixer.assert_called_with("Headphone", "45%")
        self.assertEqual(self.mpd.calls, [])

    def test_cleanup_resumes_mgwp_even_when_mpd_fails(self):
        self.audio.record["suspended"] = [[99, "identity"]]
        self.audio.song_id, self.audio.identity = "7", lambda pid: "identity"
        self.mpd.command = lambda text: (_ for _ in ()).throw(RuntimeError("MPD unavailable"))
        with patch("mira_player.audio.signal.SIGCONT", 18, create=True), patch("mira_player.audio.os.kill") as kill:
            with self.assertRaises(RuntimeError):
                self.audio.stop()
        import signal
        kill.assert_called_once_with(99, 18)
        self.gpio.assert_any_call(self.f.cfg["amp_gpio"], 0)

    def no_such_song(self):
        original = self.mpd.command
        def command(text):
            if text.startswith("deleteid "):
                raise RuntimeError("ACK [50@0] {deleteid} No such song")
            return original(text)
        self.mpd.command = command

    def test_stop_tolerates_song_already_gone_from_mpd(self):
        self.audio.song_id = "14"
        self.no_such_song()
        self.audio.lease.write_text("{}")
        self.audio.stop()
        self.assertFalse(self.audio.lease.exists())

    def test_startup_recovery_never_crash_loops(self):
        # Lease left by a killed process; MPD was restarted so the song is gone
        # and another cleanup step fails too: startup must still succeed.
        self.audio.lease.write_text(json.dumps(dict(boot_id="boot", suspended=[], old={}, song_id="14")))
        self.audio.client = self.audio.record = None
        self.audio.boot_id = lambda: "boot"
        self.no_such_song()
        self.mixer.side_effect = RuntimeError("amixer failed")
        self.audio.recover()
        self.assertFalse(self.audio.lease.exists())
        self.assertIsNone(self.audio.record)

    def test_other_player_ownership_loss(self):
        self.audio.song_id, self.mpd.song, self.mpd.state = "7", "8", "play"
        with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
            self.audio.poll()

    def test_seek_paused_track_preserves_pause_and_reports_mpd_position(self):
        self.audio.song_id, self.mpd.song, self.mpd.state = "7", "7", "pause"
        self.assertEqual(self.audio.seek(42.5), 42.5)
        self.assertIn("seekid 7 42.500", self.mpd.calls)
        self.assertEqual(self.audio.poll()["state"], "paused")
        self.assertEqual(self.audio.poll()["duration"], 120)

    def test_seek_rejects_wrong_owner_and_out_of_range(self):
        self.audio.song_id, self.mpd.song, self.mpd.state = "7", "8", "play"
        with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
            self.audio.seek(30)
        self.mpd.song = "7"
        for position in [-1, float("nan"), float("inf"), 121]:
            with self.assertRaises(ValueError):
                self.audio.seek(position)
        self.assertFalse(any(cmd.startswith("seekid") for cmd in self.mpd.calls))


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.f, self.client = Fixture(), Client()
        self.broker = Broker(self.f.cfg, client=self.client)
        self.broker.controller = self.f.controller
    def tearDown(self):
        self.f.cleanup()

    def test_subscribes_only_the_device_command_topic(self):
        self.broker.on_connect(self.client, None, {}, 0)
        self.assertEqual(self.client.subscriptions,
                         [("station/player/device/mira-01/command", 1)])
        self.broker.on_subscribe(self.client, None, 1, (128,))
        self.assertFalse(self.broker.ready.is_set())
        self.broker.on_subscribe(self.client, None, 1, (1,))
        self.assertTrue(self.broker.ready.is_set())
        self.f.tick()
        self.assertTrue(self.f.controller.mqtt_ready)

    def test_every_message_goes_to_status_topic_tagged_and_unretained(self):
        self.broker.publish("event", {"type": "downloading", "media_id": "m"}, True)
        (topic, body), options = self.client.published[-1]
        self.assertEqual(topic, "station/player/device/mira-01/status")
        self.assertEqual(json.loads(body), {"type": "downloading", "media_id": "m",
                                            "kind": "event", "deviceId": "mira-01"})
        self.assertEqual(options, {"qos": 1})

    def test_web_file_command_becomes_file_event(self):
        event = self.f.event("abc-123")
        command = dict(type="file", action="download", fileId="abc-123", fileName=event["fileName"],
                       mimeType=event["mimeType"], size=event["size"], sha256=event["sha256"],
                       downloadEndpoint="/api/files/abc-123", sentAt="2026-10-07T00:00:00Z")
        self.broker.on_message(self.client, None, SimpleNamespace(
            topic=self.broker.command_topic, payload=json.dumps(command).encode(), retain=False))
        kind, payload = self.f.controller.inbox.get_nowait()
        self.assertEqual(kind, "file")
        self.assertEqual(json.loads(payload), dict(event, downloadUrl=
                         "https://files.example.invalid/api/files/abc-123"))

    def test_web_control_button_becomes_command(self):
        for body, extra in (({"action": "set_volume", "volume": 42}, {"volume": 42}),
                            ({"action": "next"}, {})):
            message = dict(body, id="3f1c-uuid", type="control", sentAt="2026-10-07T07:00:00Z")
            self.broker.on_message(self.client, None, SimpleNamespace(
                topic=self.broker.command_topic, payload=json.dumps(message).encode(), retain=False))
            kind, payload = self.f.controller.inbox.get_nowait()
            command = json.loads(payload)
            self.assertEqual(kind, "command")
            self.assertEqual(command, dict(extra, command_id="3f1c-uuid", device_id="mira-01",
                                           action=body["action"], expires_at=command["expires_at"]))

    def test_web_volume_button_changes_volume(self):
        message = {"id": "v1", "type": "control", "action": "set_volume", "volume": 42}
        # Expiry is stamped from the device clock; align it with the fixture clock.
        with patch("mira_player.backend.time.time", return_value=self.f.time):
            self.broker.on_message(self.client, None, SimpleNamespace(
                topic=self.broker.command_topic, payload=json.dumps(message).encode(), retain=False))
        self.f.tick()
        self.assertEqual(self.f.audio.volume, 42)

    def test_unknown_web_file_action_ignored(self):
        self.broker.on_message(self.client, None, SimpleNamespace(
            topic=self.broker.command_topic, payload=b'{"type":"file","action":"delete"}', retain=False))
        self.assertTrue(self.f.controller.inbox.empty())

    def test_retained_command_not_executed(self):
        self.broker.on_message(self.client, None,
            SimpleNamespace(topic=self.broker.command_topic, payload=b"{}", retain=True))
        self.assertTrue(self.f.controller.inbox.empty())

    def test_check_mode_no_publish_or_control(self):
        client = Client()
        broker = Broker(self.f.cfg, check=True, client=client)
        broker.controller = self.f.controller
        broker.on_connect(client, None, {}, 0)
        broker.on_subscribe(client, None, 1, (1, 1))
        broker.on_message(client, None, SimpleNamespace(topic=broker.command_topic, payload=b"{}", retain=False))
        self.assertEqual(client.published, [])
        self.assertIsNone(client.will)
        self.assertTrue(self.f.controller.inbox.empty())

    def test_disconnect_not_ready(self):
        self.broker.ready.set()
        self.broker.on_disconnect(self.client, None, 1)
        self.f.tick()
        self.assertFalse(self.broker.ready.is_set())
        self.assertFalse(self.f.controller.mqtt_ready)


if __name__ == "__main__":
    unittest.main()
