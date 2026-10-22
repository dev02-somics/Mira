import json
import unittest

from helpers import Fixture


class LibraryTests(unittest.TestCase):
    """Library keeps arrival order (FIFO) and one entry per file name."""

    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.cleanup()

    def send(self, media_id, name, finish=True):
        event = dict(self.f.event(media_id), fileName=name)
        self.f.controller.submit("file", json.dumps(event).encode())
        self.f.tick()
        if finish:
            self.f.executor.finish(len(self.f.executor.items) - 1)
            self.f.tick()

    def names(self):
        return [(key, record["event"]["fileName"]) for key, record in
                self.f.controller.store.data["media"].items()]

    def end_track(self):
        self.f.audio.state = "idle"
        self.f.tick()

    def test_files_play_in_arrival_order(self):
        self.send("a", "a.mp3")
        self.send("b", "b.mp3")
        self.send("c", "c.mp3")
        self.assertEqual(self.f.controller.current, "a")
        self.end_track()
        self.assertEqual(self.f.controller.current, "b")
        self.end_track()
        self.assertEqual(self.f.controller.current, "c")

    def test_same_name_replaces_entry_in_place(self):
        self.send("a", "song.mp3")
        self.end_track()
        self.send("b", "other.mp3")
        self.end_track()
        old_path = self.f.controller.path_for(self.f.controller.store.data["media"]["a"]["event"])
        self.send("c", "SONG.mp3")      # same name, different case, new upload id
        self.assertEqual(self.names(), [("c", "SONG.mp3"), ("b", "other.mp3")])
        self.assertFalse(old_path.exists())
        self.assertEqual(self.f.controller.current, "c")
        self.assertTrue(any(body.get("type") == "replaced" and body["replaced"] == ["a"]
                            for _, body, _ in self.f.messages))

    def test_replacing_the_playing_track_stops_it_and_plays_new_version(self):
        self.send("a", "song.mp3")
        self.assertEqual(self.f.controller.current, "a")
        self.send("b", "song.mp3")
        self.assertEqual(self.names(), [("b", "song.mp3")])
        self.assertEqual(self.f.controller.current, "b")
        self.assertEqual(len(self.f.audio.starts), 2)

    def test_replacing_a_download_in_progress_cleans_up_its_file(self):
        self.send("a", "song.mp3", finish=False)
        self.send("b", "song.mp3", finish=False)
        self.assertEqual(self.names(), [("b", "song.mp3")])
        self.f.executor.finish(0)        # late finish of the replaced download
        self.f.tick()
        orphan = self.f.controller.path_for(dict(self.f.event("a"), fileName="song.mp3"))
        self.assertFalse(orphan.exists())
        self.f.executor.finish(1)
        self.f.tick()
        self.f.tick()
        self.assertEqual(self.f.controller.current, "b")

    def test_next_previous_follow_arrival_order_after_replacement(self):
        for key, name in (("a", "a.mp3"), ("b", "b.mp3"), ("c", "c.mp3")):
            self.send(key, name)
            self.end_track()
        self.send("b2", "b.mp3")         # replaces b, keeps the middle position
        self.end_track()
        self.f.command("next", command_id="n1")
        self.assertEqual(self.f.controller.current, "c")
        self.f.command("previous", command_id="p1")
        self.assertEqual(self.f.controller.current, "b2")
        self.f.command("previous", command_id="p2")
        self.assertEqual(self.f.controller.current, "a")

    def test_restart_merges_old_duplicates(self):
        for key in ("a", "b", "c"):
            self.send(key, "x.mp3" if key != "b" else "y.mp3")
            self.end_track()
        media = self.f.controller.store.data["media"]
        # Simulate a journal written by an older version that allowed duplicates.
        media["d"] = dict(media["c"], event=dict(self.f.event("d"), fileName="x.mp3"), status="failed")
        dup = dict(self.f.event("a2"), fileName="x.mp3")
        media["a2"] = dict(event=dup, status="ready", error=None, last_used=0)
        self.f.controller.path_for(dup).write_bytes(b"ID3-test-audio")
        self.f.controller.store.save()
        self.f.controller = self.f.new_controller()
        self.assertEqual(self.names(), [("a2", "x.mp3"), ("b", "y.mp3")])


if __name__ == "__main__":
    unittest.main()
