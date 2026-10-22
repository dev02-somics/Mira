from concurrent.futures import Future
import hashlib
import json
from pathlib import Path
import tempfile
from mira_player.config import DEFAULTS
from mira_player.controller import Controller


class Audio:
    def __init__(self):
        self.state, self.position = "idle", 0
        self.starts, self.stops, self.volume = [], 0, 0
    def set_volume(self, value):
        self.volume = value
    def start(self, path, volume):
        self.starts.append(str(path))
        self.position, self.state = 0, "preparing"
    def poll(self):
        if self.state == "preparing":
            self.state = "playing"
        return dict(state=self.state, elapsed=self.position)
    def pause(self):
        self.state = "paused"
    def resume(self):
        self.state = "playing"
    def seek(self, position):
        self.position = position
        return position
    def stop(self):
        self.stops += 1
        self.state, self.position = "idle", 0


class Executor:
    def __init__(self):
        self.items = []
    def submit(self, fn):
        future = Future()
        self.items.append((future, fn))
        return future
    def finish(self, index=0, error=None):
        future, fn = self.items[index]
        if error:
            future.set_exception(error)
        else:
            try:
                fn()
                future.set_result(None)
            except BaseException as failure:
                future.set_exception(failure)


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = dict(DEFAULTS, device_id="mira-01", host="localhost", username="test",
                        password="test-only", allowed_hosts=["files.example.invalid"],
                        api_base="https://files.example.invalid",
                        cache_dir=str(root / "audio/cache"), music_root=str(root / "audio"),
                        state_dir=str(root / "state"))
        self.time, self.audio, self.executor, self.messages = 1800000000, Audio(), Executor(), []
        def fetch(event, path, hosts, stop, ports):
            path.write_bytes(b"ID3-test-audio")
        self.fetch = fetch
        self.controller = self.new_controller()
    def new_controller(self):
        return Controller(self.cfg, self.audio, self.executor,
                          lambda topic, body, retain: self.messages.append((topic, body, retain)),
                          self.fetch, clock=lambda: self.time)
    def event(self, media_id="media-1"):
        data = b"ID3-test-audio"
        return dict(id=media_id, type="file", delivery="http", fileName=media_id + ".mp3",
                    mimeType="audio/mpeg", size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    downloadUrl="https://files.example.invalid/api/files/" + media_id)
    def submit_file(self, key="media-1"):
        self.controller.submit("file", json.dumps(self.event(key)).encode())
        self.tick()
    def command(self, action, command_id=None, **extra):
        value = dict(command_id=command_id or "cmd-" + action, device_id="mira-01",
                     action=action, expires_at=self.time + 60, **extra)
        self.controller.submit("command", json.dumps(value).encode())
        self.tick()
        return value
    def tick(self):
        self.time += 1
        self.controller.tick()
    def loaded(self, key="media-1"):
        self.submit_file(key)
        self.executor.finish(len(self.executor.items) - 1)
        self.tick()
        self.tick()
    def cleanup(self):
        self.controller.close()
        self.tmp.cleanup()
