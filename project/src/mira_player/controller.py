"""All state/audio changes run in one loop; downloads run separately."""
import hashlib
import json
from pathlib import Path
import queue
import socket
import threading
import time
import urllib.error
from .commands import parse
from .protocol import download, validate_event
from .store import Store


class Controller:
    def __init__(self, cfg, audio, executor, publish, fetch=download, clock=time.time, recovery_path=None):
        self.cfg, self.audio, self.executor = cfg, audio, executor
        self.publish, self.fetch, self.clock = publish, fetch, clock
        self.store = Store(cfg["state_dir"], cfg["volume"], recovery_path=recovery_path)
        self.cache = Path(cfg["cache_dir"])
        self.cache.mkdir(parents=True, exist_ok=True)
        self.inbox = queue.Queue(maxsize=64)
        self.stop_event = threading.Event()
        self.requests, self.play_queue = {}, []
        self.orphans = []  # (future, path) of downloads whose entry was replaced
        self.current = self.pending_command = None
        self.state, self.error, self.elapsed = "idle", None, 0.0
        self.duration = None
        self.mqtt_ready = False
        self.done_media = None  # finished track the web keeps showing as DONE
        self.error_until = 0    # an error state clears itself after ERROR_HOLD seconds
        self.start_failures = {}
        self.last_report = self.last_poll = 0
        self.audio.set_volume(self.store.data["volume"])
        # Never replay a command or auto-start audio after restart.
        for key, record in self.store.data["media"].items():
            event = validate_event(json.dumps(record["event"]).encode(),
                                   cfg["allowed_hosts"], cfg["allowed_ports"])
            if event["id"] != key:
                raise ValueError("Invalid media journal key")
            if record["status"] in ("queued", "downloading"):
                record.update(status="failed", error="service_restarted")
        for record in self.store.data["commands"].values():
            if record["status"] == "accepted":
                record.update(status="failed", error="service_restarted")
        # The process lock prevents another downloader from owning these files.
        for path in self.cache.glob("*.part"):
            path.unlink()
        self.dedupe_library()
        self.store.save()

    def emit(self, kind, value, retain=False):
        # kind: state | result | event; the broker decides the topic.
        self.publish(kind, value, retain)

    def submit(self, kind, payload, retained=False):
        if kind in ("command", "file") and (not isinstance(payload, bytes) or len(payload) > 16384):
            self.emit("event", {"type": "rejected", "error": "payload_too_large_or_invalid"})
            return False
        if retained and kind in ("command", "file"):
            self.emit("event", {"type": "rejected", "error": "retained_action_ignored"})
            return False
        try:
            self.inbox.put_nowait((kind, payload))
            return True
        except queue.Full:
            self.emit("event", {"type": "rejected", "error": "inbox_full"})
            return False

    def snapshot(self):
        return {"device_id": self.cfg["device_id"], "state": self.state,
                "media_id": self.current, "last_media_id": self.store.data["last_media"],
                "volume": self.store.data["volume"], "elapsed": self.elapsed,
                "repeat": self.store.data.get("repeat", "off"),
                "queued": list(self.play_queue), "downloads": len(self.requests),
                "mqtt_ready": self.mqtt_ready, "error": self.error, "timestamp": self.clock()}

    ERROR_HOLD = 10        # seconds the web shows ERROR before the player is idle again
    MAX_START_RETRIES = 30  # ~5 minutes of retries while MPD/audio is busy

    def fail(self, error):
        self.error, self.state = str(error), "error"
        self.error_until = self.clock() + self.ERROR_HOLD
        self.report()

    # Web player panel (demo-somics src/lib/playback.ts) reads these fields.
    WEB_STATES = {"preparing": "PLAYING", "playing": "PLAYING", "paused": "PAUSED", "error": "ERROR"}

    def web_status(self):
        snap = self.snapshot()
        media = self.current or (self.done_media if self.state == "idle" else None)
        state = self.WEB_STATES.get(self.state, "DONE" if media else "IDLE")
        record = self.store.data["media"].get(media) if media else None
        event = record["event"] if record else {}
        return dict(snap, playerState=snap["state"], state=state, fileId=media,
                    fileName=event.get("fileName"),
                    durationSeconds=self.duration if self.current and self.duration else event.get("durationSeconds"),
                    positionSeconds=round(self.elapsed, 1) if self.current else None)

    def report(self):
        self.emit("state", self.web_status(), True)
        self.last_report = self.clock()

    def result(self, key, status, error=None):
        record = self.store.data["commands"][key]
        record.update(status=status, error=error, updated_at=self.clock())
        self.store.save()
        self.emit("result", dict(record, command_id=key, device_id=self.cfg["device_id"]))
        self.report()

    def path_for(self, event):
        key = hashlib.sha256(event["id"].encode()).hexdigest()
        return self.cache / (key + Path(event["fileName"]).suffix.lower())

    def verified_path(self, key):
        record = self.store.data["media"].get(key)
        if not record or record["status"] != "ready":
            raise ValueError("media_not_ready")
        path = self.path_for(record["event"])
        if not path.is_file():
            raise ValueError("media_evicted")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        if path.stat().st_size != record["event"]["size"] or digest.hexdigest() != record["event"]["sha256"].lower():
            raise ValueError("cached_media_corrupt")
        return path

    def reserve(self, event):
        reserved_names = {self.path_for(job["event"]).name for job in self.requests.values()}
        used = sum(p.stat().st_size for p in self.cache.iterdir()
                   if p.is_file() and p.name not in reserved_names and not p.name.endswith(".part"))
        reserved = sum(job["event"]["size"] for job in self.requests.values())
        needed = used + reserved + event["size"] - self.cfg["max_cache_bytes"]
        pinned = set(self.play_queue) | set(self.requests) | {self.current}
        for key, record in sorted(self.store.data["media"].items(),
                                  key=lambda pair: pair[1].get("last_used", 0)):
            if needed <= 0:
                break
            if key in pinned:
                continue
            path = self.path_for(record["event"])
            if path.is_file():
                size = path.stat().st_size
                path.unlink()
                needed -= size
                record.update(status="evicted", error=None)
        if needed > 0:
            raise ValueError("cache_full")
        self.store.save()

    # -- Library: arrival order (FIFO), one entry per file name --------------

    def same_name(self, event):
        """Library keys holding another upload with this file name."""
        name = event["fileName"].casefold()
        return [key for key, record in self.store.data["media"].items()
                if key != event["id"] and record["event"]["fileName"].casefold() == name]

    def forget_media(self, key):
        """Drop a library entry and its cached audio, stopping it if it plays."""
        if key == self.current:
            self.halt()
        self.play_queue = [x for x in self.play_queue if x != key]
        record = self.store.data["media"].pop(key)
        path = self.path_for(record["event"])
        job = self.requests.pop(key, None)
        if job and job["future"] and not job["future"].done():
            self.orphans.append((job["future"], path))  # delete once the download ends
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def place(self, key, record, index):
        """Insert a library entry at a given position, keeping the rest in order."""
        media = self.store.data["media"]
        items = [(k, v) for k, v in media.items() if k != key]
        items.insert(index, (key, record))
        media.clear()
        media.update(items)

    def dedupe_library(self):
        """Merge same-name entries left by older versions: keep the newest
        playable upload at the position of the first one."""
        groups = {}
        for key, record in self.store.data["media"].items():
            groups.setdefault(record["event"]["fileName"].casefold(), []).append(key)
        for keys in groups.values():
            if len(keys) < 2:
                continue
            media = self.store.data["media"]
            ready = [key for key in keys if media[key]["status"] == "ready"]
            keep = ready[-1] if ready else keys[-1]
            index, record = list(media).index(keys[0]), media[keep]
            for key in keys:
                if key != keep:
                    self.forget_media(key)
            self.place(keep, record, index)
            if self.store.data["last_media"] in keys:
                self.store.data["last_media"] = keep

    def receive_file(self, payload):
        event = validate_event(payload, self.cfg["allowed_hosts"], self.cfg["allowed_ports"])
        key = event["id"]
        previous = self.store.data["media"].get(key)
        if previous:
            if previous["event"] != event:
                raise ValueError("conflicting_media_id")
            if previous["status"] not in ("failed", "evicted"):
                self.emit("event", {"type": "duplicate_file", "media_id": key})
                return
        # Same file name sent again: the new upload replaces the old entry in place.
        replaced = self.same_name(event)
        index = list(self.store.data["media"]).index(replaced[0]) if replaced else None
        was_last = self.store.data["last_media"] in replaced
        for old in replaced:
            self.forget_media(old)
        if len(set(self.play_queue) | set(self.requests)) >= self.cfg["max_pending"]:
            raise ValueError("pending_queue_full")
        if not previous and len(self.store.data["media"]) >= self.cfg["max_media"]:
            raise ValueError("media_registry_full")
        self.reserve(event)
        record = dict(event=event, status="downloading", error=None, last_used=self.clock())
        if index is None:
            self.store.data["media"][key] = record
        else:
            self.place(key, record, index)
            if was_last:
                self.store.data["last_media"] = key
            self.emit("event", {"type": "replaced", "media_id": key, "replaced": replaced})
        self.store.save()
        job = dict(event=event, future=None, retry_at=0)
        self.requests[key] = job
        self.play_queue.append(key)
        self.launch_download(job)
        self.emit("event", {"type": "downloading", "media_id": key})
        self.report()

    def launch_download(self, job):
        def work():
            self.fetch(job["event"], self.path_for(job["event"]), self.cfg["allowed_hosts"],
                       self.stop_event, self.cfg["allowed_ports"])
        job["future"] = self.executor.submit(work)

    def begin(self, key, command_id=None):
        path = self.verified_path(key)
        try:
            self.audio.start(path, self.store.data["volume"])
            self.current, self.pending_command = key, command_id
            self.state, self.error, self.elapsed = "preparing", None, 0.0
            self.duration = None
            self.done_media = None
            self.play_queue = [x for x in self.play_queue if x != key]
            self.store.data["last_media"] = key
            self.store.data["media"][key]["last_used"] = self.clock()
            self.store.save()
        except Exception:
            self.current = self.pending_command = None
            self.audio.stop()
            raise
        self.report()

    def neighbor(self, step):
        """Next/previous cached track in arrival order, wrapping around."""
        ready = [key for key, record in self.store.data["media"].items() if record["status"] == "ready"]
        if not ready:
            raise ValueError("no_media")
        anchor = self.current or self.store.data["last_media"]
        if anchor in ready:
            return ready[(ready.index(anchor) + step) % len(ready)]
        return ready[0] if step > 0 else ready[-1]

    def halt(self):
        self.audio.stop()
        if self.pending_command:
            self.result(self.pending_command, "failed", "stopped_before_start")
        self.pending_command = self.current = None
        self.state, self.elapsed = "idle", 0.0

    def command(self, payload):
        cmd = parse(payload, self.cfg["device_id"], self.clock())
        key, history = cmd["command_id"], self.store.data["commands"]
        if key in history:
            if history[key]["command"] != cmd:
                raise ValueError("conflicting_command_id")
            self.emit("result", dict(history[key], command_id=key, device_id=self.cfg["device_id"]))
            return
        for old in list(history):
            if history[old]["command"]["expires_at"] < self.clock() and old != self.pending_command:
                del history[old]
        if len(history) >= 512:
            raise ValueError("command_history_full")
        history[key] = dict(command=cmd, status="accepted", error=None)
        self.store.save()
        self.emit("result", dict(history[key], command_id=key, device_id=self.cfg["device_id"]))
        try:
            action = cmd["action"]
            if action == "set_volume":
                self.audio.set_volume(cmd["volume"])
                self.store.data["volume"] = cmd["volume"]
            elif action == "pause":
                if self.state not in ("playing", "paused"):
                    raise ValueError("not_playing")
                self.audio.pause()
                self.state = "paused"
            elif action == "resume" and self.state == "idle" and not self.current:
                # "Tiếp tục" after a track ended: play the last track again.
                media = self.store.data["last_media"]
                if not media:
                    raise ValueError("not_paused")
                self.verified_path(media)
                self.begin(media, key)
                return
            elif action == "resume":
                if self.state not in ("playing", "paused"):
                    raise ValueError("not_paused")
                self.audio.resume()
                self.state = "playing"
            elif action in ("next", "previous"):
                media = self.neighbor(1 if action == "next" else -1)
                self.verified_path(media)
                self.halt()
                self.begin(media, key)
                return
            elif action == "stop":
                self.halt()
                self.play_queue.clear()
                self.done_media = None
            elif action == "set_repeat":
                self.store.data["repeat"] = cmd["repeat"]
            elif action == "seek":
                if cmd["media_id"] != self.current or self.state not in ("playing", "paused"):
                    raise ValueError("not_playing_this_media")
                self.elapsed = self.audio.seek(cmd["position"])
            else:
                media = cmd.get("media_id") or self.current or self.store.data["last_media"]
                if not media:
                    raise ValueError("no_media_to_replay")
                self.verified_path(media)
                if action == "play" and self.state in ("preparing", "playing", "paused"):
                    raise ValueError("player_busy")
                if action == "replay":
                    self.halt()
                self.begin(media, key)
                return
            self.error = None
            self.result(key, "completed")
        except Exception as error:
            self.error = str(error)
            self.result(key, "failed", str(error))

    def collect_downloads(self):
        for future, path in list(self.orphans):
            if future.done():
                self.orphans.remove((future, path))
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        for key, job in list(self.requests.items()):
            future = job["future"]
            if future is None:
                if self.clock() >= job["retry_at"]:
                    self.launch_download(job)
                continue
            if not future.done():
                continue
            try:
                future.result()
                self.store.data["media"][key].update(status="ready", error=None)
                self.emit("event", {"type": "downloaded", "media_id": key})
            except Exception as error:
                # socket.timeout is not a TimeoutError before Python 3.10 (the board runs 3.9).
                retryable = ((isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout))
                              and not isinstance(error, urllib.error.HTTPError)) or (
                    isinstance(error, urllib.error.HTTPError) and
                    (error.code in (408, 429) or 500 <= error.code <= 599)))
                if retryable and not self.stop_event.is_set():
                    job["future"], job["retry_at"] = None, self.clock() + 30
                    self.store.data["media"][key]["error"] = "network_retry"
                    self.store.save()
                    continue
                self.store.data["media"][key].update(status="failed", error=str(error))
                self.play_queue = [x for x in self.play_queue if x != key]
                self.emit("event", {"type": "download_failed", "media_id": key, "error": str(error)})
            del self.requests[key]
            self.store.save()
            self.report()

    def poll_audio(self):
        try:
            status = self.audio.poll()
            previous = self.state
            # Without a current track an error is transient, so queued files still play.
            self.state = status["state"] if self.current else (
                "error" if previous == "error" and self.clock() < self.error_until else "idle")
            self.elapsed = status.get("elapsed", 0.0) if self.current else 0.0
            self.duration = status.get("duration") if self.current else None
            if previous == "preparing" and self.state == "playing" and self.pending_command:
                key, self.pending_command = self.pending_command, None
                self.result(key, "completed")
            if self.current and self.state == "idle":
                key = self.current
                self.halt()
                self.emit("event", {"type": "finished", "media_id": key})
                if self.store.data.get("repeat") == "one":
                    self.begin(key)  # loop until repeat is turned off or another track is chosen
                else:
                    self.done_media = key
                    self.report()
            elif previous != self.state:
                self.report()
        except Exception as error:
            pending, self.pending_command = self.pending_command, None
            try:
                self.audio.stop()
            finally:
                self.current, self.state, self.error = None, "error", str(error)
                self.error_until = self.clock() + self.ERROR_HOLD
                if pending:
                    self.result(pending, "failed", str(error))
                self.report()

    def tick(self):
        for _ in range(16):
            try:
                kind, payload = self.inbox.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "command":
                    self.command(payload)
                elif kind == "file":
                    self.receive_file(payload)
                elif kind == "mqtt":
                    self.mqtt_ready = bool(payload)
                    self.report()
            except Exception as error:
                key = None
                if kind == "command":
                    try:
                        data = json.loads(payload)
                        if isinstance(data, dict) and isinstance(data.get("command_id"), str):
                            key = data["command_id"][:128]
                    except (TypeError, ValueError):
                        pass
                self.emit("result" if kind == "command" else "event",
                          dict(command_id=key, device_id=self.cfg["device_id"],
                               status="rejected", error=str(error)))
        self.collect_downloads()
        if self.clock() - self.last_poll >= 0.25:
            self.last_poll = self.clock()
            self.poll_audio()
        if self.state == "idle" and self.play_queue:
            key = self.play_queue[0]
            if self.store.data["media"][key]["status"] == "ready":
                self.play_queue.pop(0)
                try:
                    self.begin(key)
                    self.start_failures.pop(key, None)
                except RuntimeError as error:
                    # Audio/MPD busy (e.g. firmware still owns the speaker after boot):
                    # keep the track at the head and try again after the error hold.
                    attempts = self.start_failures.get(key, 0) + 1
                    self.start_failures[key] = attempts
                    if attempts < self.MAX_START_RETRIES:
                        self.play_queue.insert(0, key)
                    self.fail(error)
                except Exception as error:
                    self.fail(error)  # bad/evicted media: skip it, the next track follows
        if self.clock() - self.last_report >= self.cfg["heartbeat"]:
            self.report()

    def close(self):
        self.stop_event.set()
        self.play_queue.clear()
        try:
            self.halt()
        finally:
            self.store.save()
            self.report()
