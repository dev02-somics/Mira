"""Bounded, independent service checks, called once a minute by cron."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def command(args, timeout=15):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, check=True).stdout


def log(message):
    command(["logger", "-t", "mira-health", message], timeout=3)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def heartbeat_problem(status, pid, now, age):
    # Boot/worker start may take time. Never compare wall time (NTP can jump).
    if age < 180:
        return None
    if status.get("pid") != pid:
        return "missing heartbeat for current process"
    stamp = status.get("monotonic")
    if not isinstance(stamp, (int, float)) or not 0 <= now - stamp <= 180:
        return "worker heartbeat stopped"
    return None


def audio_owned():
    lease = read_json("/tmp/mira-audio-lease.json")
    status = read_json("/tmp/mira-player-status.json")
    try:
        pid = int(status["pid"])
        cmd = Path("/proc/%d/cmdline" % pid).read_bytes().split(b"\0")
        return (b"mira_player.app" in cmd and lease.get("boot_id") ==
                Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    except (OSError, ValueError, KeyError):
        return False


def writable_card():
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if fields[1] == "/mnt/mmcblk0p1":
            return "rw" in fields[3].split(",")
    return False


def check_service(name, health, services, now):
    instances = services.get(name, {}).get("instances", {})
    running = [v for v in instances.values() if v.get("running") and v.get("pid")]
    if not running:
        log(name + ": not running; starting through procd")
        command(["/etc/init.d/" + name, "start"], timeout=20)
        return
    pid = running[0]["pid"]
    try:
        ticks = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[19]
        age = now - int(ticks) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return  # procd may already have respawned it
    reason = heartbeat_problem(read_json(health), pid, now, age)
    if reason:
        # procd stops asynchronously. Repeating restart each minute can reset
        # its 90-second kill timer forever when a process is truly frozen.
        marker = Path("/tmp/mira-health-restart-" + name + ".json")
        previous = read_json(marker)
        if restart_pending(previous, pid, now):
            return
        log(name + ": " + reason + "; restarting through procd")
        command(["/etc/init.d/" + name, "restart"], timeout=110)
        marker.write_text(json.dumps(dict(pid=pid, monotonic=now)))


def restart_pending(previous, pid, now):
    stamp = previous.get("monotonic")
    return (previous.get("pid") == pid and isinstance(stamp, (int, float))
            and 0 <= now - stamp < 180)


def mpd_status():
    with socket.create_connection(("127.0.0.1", 6600), timeout=3) as sock:
        with sock.makefile("rwb") as stream:
            if not stream.readline(4096).startswith(b"OK MPD "):
                raise RuntimeError("bad MPD greeting")
            stream.write(b"status\n")
            stream.flush()
            result = {}
            for unused in range(80):
                line = stream.readline(4096).decode().strip()
                if line == "OK":
                    return result
                if not line or line.startswith("ACK"):
                    raise RuntimeError("MPD status failed")
                if ": " in line:
                    key, value = line.split(": ", 1)
                    result[key] = value
            raise RuntimeError("MPD status too long")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-owned", action="store_true")
    args = parser.parse_args()
    if args.audio_owned:
        return 0 if audio_owned() else 1
    import fcntl
    with open("/tmp/mira-health.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        now = time.monotonic()
        if now < 240 or Path("/tmp/mira-maintenance").exists():
            return 0
        services = json.loads(command(["ubus", "call", "service", "list"]))
        check_service("mira_wifi", "/tmp/mira-wifi-health.json", services, now)
        if not writable_card():
            log("SD missing/read-only: automatic repair and reboot disabled; preserve data")
            return 0
        try:
            status = mpd_status()
        except (OSError, RuntimeError):
            log("MPD does not respond; restarting through procd")
            command(["/etc/init.d/mpd", "restart"], timeout=25)
            return 0
        if status.get("error"):
            log("MPD responds but reports a playback error; not healthy")
        check_service("mira_player", "/tmp/mira-player-status.json", services, now)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        log("health check incomplete: " + type(error).__name__)
        raise SystemExit(1)
