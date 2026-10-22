"""Verify/plan a release; --apply performs a backed-up OpenWrt installation."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

MODULES = {"__init__", "app", "audio", "backend", "broker", "commands", "config", "controller",
           "store", "protocol", "legacy_audio"}
EXPECTED = {"mira-player/mira_player/" + name + ".py" for name in MODULES}
EXPECTED |= {"mira-player/start.sh", "mira-player/mira_player.init", "mira-player/config.example.json"}
MANIFEST = "mira-player/MANIFEST.json"


def verify(path, expected_sha):
    data = Path(path).read_bytes()
    if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected_sha.lower():
        raise ValueError("Release size/SHA256 mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if {m.name for m in members} != EXPECTED | {MANIFEST} or len(members) != len(EXPECTED) + 1:
            raise ValueError("Unexpected/duplicate archive entries")
        if sum(m.size for m in members) > 512 * 1024 or any(not m.isfile() or m.size > 1024 * 1024 or m.size < 0 for m in members):
            raise ValueError("Only bounded regular files are allowed")
        payload = {m.name: archive.extractfile(m).read() for m in members}
    hashes = json.loads(payload[MANIFEST])
    if not isinstance(hashes, dict) or set(hashes) != EXPECTED:
        raise ValueError("Invalid manifest")
    for name in EXPECTED:
        if hashlib.sha256(payload[name]).hexdigest() != hashes[name]:
            raise ValueError("Manifest mismatch: " + name)
        if name.endswith(".py"):
            compile(payload[name], name, "exec")
    return payload


def service(init, action, check=False):
    if not init.exists():
        return False
    result = subprocess.run([str(init), action], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError(str(init) + " " + action + " failed")
    return result.returncode == 0


def payload_version(payload):
    for line in payload["mira-player/mira_player/__init__.py"].decode().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise ValueError("Release has no __version__")


def apply(payload, config):
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("--apply requires root on OpenWrt")
    target, init = Path("/root/mira-player"), Path("/etc/init.d/mira_player")
    old_init = Path("/etc/init.d/somics_mqtt")
    if target.is_symlink() or init.is_symlink():
        raise RuntimeError("Unexpected target symlink")
    mounts = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
    if not any(m[1] == "/mnt/mmcblk0p1" and "rw" in m[3].split(",") for m in mounts):
        raise RuntimeError("Writable SD required")
    import paho.mqtt.client
    with tempfile.TemporaryDirectory(prefix="mira-release-") as folder:
        staging = Path(folder) / "mira-player"
        for name, content in payload.items():
            relative = PurePosixPath(name).relative_to("mira-player")
            path = staging / str(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        shutil.copy2(config, staging / "config.json")
        env = dict(os.environ, PYTHONPATH=str(staging))
        for flag in ("--check-hardware", "--check-connection"):
            subprocess.run([sys.executable, "-m", "mira_player.app", "--config",
                            str(staging / "config.json"), flag], env=env, check=True, timeout=40)
        backup = Path("/mnt/mmcblk0p1/backups") / ("mira-install-%d-%d" % (time.time(), os.getpid()))
        backup.mkdir(parents=True, mode=0o700)
        had_target, had_init = target.exists(), init.exists()
        states = {
            "mira_player": dict(enabled=service(init, "enabled"), running=service(init, "status")),
            "somics_mqtt": dict(enabled=service(old_init, "enabled"), running=service(old_init, "status")),
        }
        if had_target:
            shutil.copytree(target, backup / "mira-player", symlinks=True)
        if had_init:
            shutil.copy2(init, backup / "mira_player.init")
        (backup / "services.json").write_text(json.dumps(states, indent=2))
        previous = Path("/root/.mira-player-previous-%d" % os.getpid())
        if previous.exists():
            raise RuntimeError("Previous installation directory already exists")
        moved = new_target = changed_init = False
        try:
            service(init, "stop", check=init.exists())
            service(old_init, "stop", check=old_init.exists())
            service(old_init, "disable", check=old_init.exists())
            if had_target:
                target.rename(previous)
                moved = True
            new_target = True
            shutil.copytree(staging, target)
            os.chmod(target / "config.json", 0o600)
            os.chmod(target / "start.sh", 0o755)
            changed_init = True
            shutil.copy2(target / "mira_player.init", init)
            os.chmod(init, 0o755)
            ready = Path("/tmp/mira-player-ready.json")
            if ready.exists():
                ready.unlink()
            service(init, "enable", True)
            service(init, "start", True)
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                try:
                    status = json.loads(ready.read_text())
                    cmdline = Path("/proc/%d/cmdline" % status["pid"]).read_bytes().split(b"\0")
                    if (status["version"] == payload_version(payload) and b"mira_player.app" in cmdline
                            and b"/root/mira-player/config.json" in cmdline):
                        print("INSTALLED_AND_READY backup=" + str(backup))
                        return
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(1)
            raise RuntimeError("Service did not become ready")
        except Exception:
            service(init, "stop")
            service(init, "disable")
            if new_target and target.exists():
                shutil.rmtree(target)
            if moved and previous.exists():
                previous.rename(target)
            if had_init:
                shutil.copy2(backup / "mira_player.init", init)
            elif changed_init and init.exists():
                init.unlink()
            for path, name in ((init, "mira_player"), (old_init, "somics_mqtt")):
                state = states[name]
                service(path, "enable" if state["enabled"] else "disable")
                if state["running"]:
                    service(path, "start")
            print("Installation rolled back; backup=" + str(backup), file=sys.stderr)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--config")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    payload = verify(args.archive, args.sha256)
    print("VERIFIED %d payload files" % len(payload))
    if not args.apply:
        print("PLAN: install /root/mira-player and /etc/init.d/mira_player;")
        print("back up existing app/services, check hardware and MQTT, stop old SOMICS,")
        print("enable/start new service, wait for readiness, roll back on failure.")
        print("No deployment performed. Supply --apply and --config only after approval.")
        return
    if not args.config:
        parser.error("--apply requires --config")
    apply(payload, args.config)


if __name__ == "__main__":
    main()
