"""MPD/ALSA owner; pause keeps the session and MGWP suspension."""
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import time
from . import legacy_audio as hw
from .protocol import atomic_json

LOG = logging.getLogger(__name__)


class HardwareAudio:
    def __init__(self, cfg, factory=hw.MPD):
        self.cfg, self.factory = cfg, factory
        self.client = self.song_id = self.preparing = self.record = None
        self.volume, self.controls = cfg["volume"], set()
        self.lease = Path("/tmp/mira-audio-lease.json")
        if self.lease.exists():
            self.recover()

    def boot_id(self):
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def identity(self, pid):
        return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[19]

    def check(self):
        if "WM8960" not in Path("/proc/asound/cards").read_text().upper():
            raise RuntimeError("WM8960_not_detected")
        text = hw.run_tool(["/usr/bin/amixer", "-c", "0", "scontrols"])
        controls = set(re.findall(r"Simple mixer control '(.+)',\d+", text))
        if "Headphone" not in controls:
            raise RuntimeError("Headphone_control_missing")
        return controls

    def save_lease(self):
        atomic_json(self.lease, self.record)

    def recover(self):
        """Undo a session left by a killed process; never block service startup."""
        record = json.loads(self.lease.read_text())
        if record["boot_id"] != self.boot_id():
            self.lease.unlink()
            return
        self.record = record
        self.song_id = record.get("song_id")
        try:
            self.client = self.factory()
        except Exception as error:
            # stop() still restores GPIO and resumes MGWP without MPD.
            LOG.warning("MPD unavailable during audio recovery: %s", error)
            self.client = None
        try:
            self.stop()
        except RuntimeError as error:
            # A permanent crash loop is worse than a partially restored session.
            LOG.warning("Previous audio session only partly restored: %s", error)
            self.client = self.record = self.song_id = self.preparing = None
            self.drop_lease()

    def pin(self, name, level):
        hw.gpio(self.cfg[name], level)

    def acquire(self):
        if self.client:
            return
        self.controls = self.check()
        self.client = self.factory()
        old = self.client.command("status")
        if old.get("state") in ("play", "pause"):
            self.client.close()
            self.client = None
            raise RuntimeError("MPD_busy_with_another_player")
        self.record = dict(boot_id=self.boot_id(), suspended=[], old=old, song_id=None)
        self.save_lease()
        try:
            for pid in hw.find_mgwp():
                text = Path("/proc/%d/status" % pid).read_text()
                stopped = any(line.startswith("State:") and "T" in line.split()[1]
                              for line in text.splitlines())
                if not stopped:
                    self.record["suspended"].append([pid, self.identity(pid)])
                    self.save_lease()
                    os.kill(pid, signal.SIGSTOP)
            if self.client.command("status").get("state") in ("play", "pause"):
                raise RuntimeError("MPD_became_busy")
            # A stopped song can leave a persistent decoder error across boots.
            # Clear it only after acquiring idle MPD; preserve the other playlist.
            self.client.command("clearerror")
            for key, value in (("repeat", "0"), ("random", "0"), ("consume", "0"), ("single", "1")):
                self.client.command(key + " " + value)
            self.pin("mute_gpio", 1 - self.cfg["unmute_level"])
            self.pin("amp_gpio", 1 - self.cfg["amp_active"])
            for control in ("Headphone", "Speaker"):
                if control in self.controls:
                    hw.mixer(control, "0%")
            for control in ("Left Output Mixer PCM", "Right Output Mixer PCM"):
                if control in self.controls:
                    hw.mixer(control, "on")
            if "DAC" in self.controls:
                hw.mixer("DAC", "90%")
            elif "Playback" in self.controls:
                hw.mixer("Playback", "100%")
        except BaseException:
            self.stop()
            raise

    def start(self, path, volume):
        if self.song_id is not None or self.preparing:
            raise RuntimeError("audio_session_busy")
        root = Path(self.cfg["music_root"]).resolve()
        song = str(Path(path).resolve().relative_to(root)).replace(os.sep, "/")
        if any(ord(c) < 32 for c in song):
            raise ValueError("Invalid_audio_path")
        quoted = '"' + song.replace("\\", "\\\\").replace('"', '\\"') + '"'
        self.acquire()
        try:
            self.set_volume(volume)
            self.client.command("update " + quoted)
            self.preparing = (quoted, time.monotonic() + 90)
        except BaseException:
            self.stop()
            raise

    def poll(self):
        if not self.client:
            return dict(state="idle", elapsed=0)
        status = self.client.command("status")
        if status.get("error"):
            raise RuntimeError(status["error"])
        if self.preparing:
            quoted, deadline = self.preparing
            if time.monotonic() > deadline:
                raise RuntimeError("MPD_update_timeout")
            if "updating_db" in status:
                return dict(state="preparing", elapsed=0)
            self.song_id = self.client.command("addid " + quoted)["Id"]
            self.record["song_id"] = self.song_id
            self.save_lease()
            self.pin("amp_gpio", self.cfg["amp_active"])
            self.pin("mute_gpio", self.cfg["unmute_level"])
            self.client.command("playid " + self.song_id)
            self.preparing = None
            status = self.client.command("status")
        state = status.get("state", "stop")
        if state in ("play", "pause") and status.get("songid") != self.song_id:
            raise RuntimeError("MPD_audio_ownership_lost")
        return dict(state={"play": "playing", "pause": "paused", "stop": "idle"}[state],
                    elapsed=float(status.get("elapsed", 0)), duration=float(status.get("duration", 0)))

    def pause(self):
        if not self.client or self.song_id is None:
            raise RuntimeError("No_active_song")
        self.client.command("pause 1")
        self.pin("mute_gpio", 1 - self.cfg["unmute_level"])

    def resume(self):
        if not self.client or self.song_id is None:
            raise RuntimeError("No_active_song")
        self.client.command("pause 0")
        self.pin("mute_gpio", self.cfg["unmute_level"])

    def seek(self, position):
        if not self.client or self.song_id is None:
            raise RuntimeError("No_active_song")
        if type(position) not in (int, float) or not math.isfinite(position) or position < 0:
            raise ValueError("Invalid_seek_position")
        status = self.client.command("status")
        if status.get("songid") != self.song_id or status.get("state") not in ("play", "pause"):
            raise RuntimeError("MPD_audio_ownership_lost")
        duration = float(status.get("duration", 0))
        if duration > 0 and position > duration:
            raise ValueError("Seek_exceeds_duration")
        self.client.command("seekid %s %.3f" % (self.song_id, position))
        # MPD can round to an audio frame. Report its result, not the requested value.
        return float(self.client.command("status").get("elapsed", position))

    def set_volume(self, volume):
        if type(volume) is not int or not 0 <= volume <= 100:
            raise ValueError("Invalid_volume")
        if self.client:
            hw.mixer("Headphone", str(volume) + "%")
        self.volume = volume

    def stop(self):
        if not self.client and not self.record:
            return
        errors, record = [], self.record or dict(suspended=[], old={})
        def attempt(call):
            try:
                call()
            except Exception as error:
                errors.append(str(error))
        if self.client:
            if self.song_id is not None:
                def remove_song():
                    if self.client.command("status").get("songid") == self.song_id:
                        self.client.command("stop")
                    try:
                        self.client.command("deleteid " + self.song_id)
                    except RuntimeError as error:
                        # MPD restarted (mgwp_mpd_monitor) or the queue was cleared.
                        if "No such song" not in str(error):
                            raise
                attempt(remove_song)
            for key in ("repeat", "random", "consume", "single"):
                if key in record["old"]:
                    attempt(lambda key=key: self.client.command(key + " " + record["old"][key]))
            if record["old"].get("volume", "-1") != "-1":
                attempt(lambda: self.client.command("setvol " + record["old"]["volume"]))
        attempt(lambda: self.pin("mute_gpio", 1 - self.cfg["unmute_level"]))
        attempt(lambda: self.pin("amp_gpio", 1 - self.cfg["amp_active"]))
        attempt(lambda: hw.mixer("Headphone", "0%"))
        for pid, identity in record["suspended"]:
            def restore(pid=pid, identity=identity):
                try:
                    if self.identity(pid) == identity:
                        os.kill(pid, signal.SIGCONT)
                except (FileNotFoundError, ProcessLookupError):
                    pass
            attempt(restore)
        if self.client:
            attempt(self.client.close)
        self.client = self.record = self.song_id = self.preparing = None
        if errors:
            raise RuntimeError("Audio_cleanup_failed: " + "; ".join(errors))
        self.drop_lease()

    def drop_lease(self):
        for path in (self.lease, Path(str(self.lease) + ".bak")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
