import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time
from . import __version__
from .config import load
from .protocol import atomic_json, require_card, validate_event

STATUS_FILE = "/tmp/mira-player-status.json"
WATCHDOG_EXIT = 3


class Watchdog:
    """Exit for a fresh start when MQTT stays down; procd respawns the service."""

    def __init__(self, limit, clock=time.monotonic):
        self.limit, self.clock = limit, clock
        self.since = clock()

    def expired(self, ready):
        if ready:
            self.since = self.clock()
            return False
        return self.clock() - self.since >= self.limit


def main(argv=None):
    parser = argparse.ArgumentParser(description="MIRA MQTT/MPD device " + __version__)
    parser.add_argument("--config", default="/root/mira-player/config.json")
    parser.add_argument("--check-connection", action="store_true")
    parser.add_argument("--check-hardware", action="store_true")
    parser.add_argument("--validate-event")
    parser.add_argument("--activate", metavar="CODE",
                        help="Exchange a one-time activation code for MQTT credentials and exit")
    parser.add_argument("--device-id", help="Device ID for --activate, e.g. SOMICS-000001")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load(args.config, require_password=not bool(
        args.validate_event or args.check_hardware or args.activate))
    if args.activate:
        from .backend import ApiError, activate
        device_id = (args.device_id or cfg["device_id"]).strip().upper()
        try:
            mqtt = activate(args.config, cfg, device_id, args.activate.strip().upper())
        except (ApiError, OSError) as error:
            # One clean line: the Wi-Fi portal shows it to the user.
            logging.error("%s", error)
            return 1
        logging.info("Activated %s: username=%s password=*** saved to %s",
                     device_id, mqtt["username"], args.config)
        return 0
    if args.validate_event:
        event = validate_event(Path(args.validate_event).read_bytes(), cfg["allowed_hosts"], cfg["allowed_ports"])
        print("VALID media_id=" + event["id"])
        return 0
    if args.check_connection:
        from .broker import Broker
        broker = Broker(cfg, check=True)
        try:
            broker.start()
            deadline = time.monotonic() + 30
            while not broker.ready.is_set() and not broker.rejected.is_set() and time.monotonic() < deadline:
                time.sleep(0.1)
            print("MQTT_READY" if broker.ready.is_set() else "MQTT_NOT_READY")
            return 0 if broker.ready.is_set() else 1
        finally:
            broker.close()
    if args.check_hardware:
        from .audio import HardwareAudio
        audio = HardwareAudio.__new__(HardwareAudio)
        audio.cfg = cfg
        print(json.dumps(dict(controls=sorted(audio.check()), version=__version__)))
        return 0
    import fcntl
    lock = open("/tmp/mira-player.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    audio_lock = open("/tmp/main-run-mp3.lock", "w")
    fcntl.flock(audio_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    require_card(Path(cfg["cache_dir"]))
    from .audio import HardwareAudio
    from .backend import Tokens, authorized_fetch
    from .broker import Broker
    from .controller import Controller
    stop = threading.Event()
    def shutdown(signum, frame):
        stop.set()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    audio = HardwareAudio(cfg)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mira-download")
    broker, controller = None, None
    ready_path = Path("/tmp/mira-player-ready.json")
    status_path = Path(STATUS_FILE)
    try:
        broker = Broker(cfg)
        controller = Controller(cfg, audio, executor, broker.publish,
                                fetch=authorized_fetch(Tokens(cfg)),
                                recovery_path="/etc/mira-player/recovery.json")
        broker.controller = controller
        broker.start()
        marked_ready, status_at = False, 0.0
        watchdog = Watchdog(cfg["mqtt_watchdog"])
        while not stop.wait(0.1):
            controller.tick()
            broker.tick()
            # Never cut a track that is playing; restart once it has finished.
            if watchdog.expired(controller.mqtt_ready or controller.state in ("preparing", "playing")):
                logging.warning("MQTT down for %ss; restarting for a fresh connection",
                                cfg["mqtt_watchdog"])
                return WATCHDOG_EXIT
            if controller.mqtt_ready and not marked_ready:
                atomic_json(ready_path, dict(pid=os.getpid(), version=__version__, device_id=cfg["device_id"]))
                marked_ready = True
            if time.monotonic() >= status_at:
                # Local status for the Wi-Fi portal (no secrets).
                status_at = time.monotonic() + 5
                atomic_json(status_path, dict(pid=os.getpid(), version=__version__,
                                              device_id=cfg["device_id"], mqtt_ready=controller.mqtt_ready,
                                              state=controller.state, timestamp=time.time(),
                                              monotonic=time.monotonic()))
        return 0
    finally:
        try:
            if controller:
                controller.close()
            else:
                audio.stop()
        finally:
            if controller:
                controller.stop_event.set()
            executor.shutdown(wait=True)
            if broker:
                broker.close()
            for path in (ready_path, Path(str(ready_path) + ".bak"),
                         status_path, Path(str(status_path) + ".bak")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.exception("MIRA service stopped")
        sys.exit(1)
