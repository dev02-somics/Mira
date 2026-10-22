import json
import logging
from pathlib import Path
import shutil
import time
from .protocol import atomic_json


class Store:
    def __init__(self, folder, volume, recovery_path=None):
        self.path = Path(folder) / "state.json"
        self.recovery_path = Path(recovery_path) if recovery_path else None
        self.checkpoint_at = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {"version": 1, "volume": volume, "last_media": None,
                     "media": {}, "commands": {}}
        errors = []
        candidates = [self.path, Path(str(self.path) + ".bak")]
        if self.recovery_path:
            candidates += [self.recovery_path, Path(str(self.recovery_path) + ".bak")]
        for path in candidates:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(data, dict) or data.get("version") != 1
                        or type(data.get("volume")) is not int or not 0 <= data["volume"] <= 100
                        or not isinstance(data.get("media"), dict)
                        or not isinstance(data.get("commands"), dict)):
                    raise ValueError("Invalid state journal")
                self.data = data
                if self.recovery_path and path in candidates[2:]:
                    # Preserve damaged journals; recovery never replays old commands.
                    for damaged in candidates[:2]:
                        if damaged.exists():
                            damaged.rename(damaged.with_name(damaged.name + ".damaged-" + str(time.time_ns())))
                    logging.warning("Recovered player state from independent flash checkpoint")
                return
            except (OSError, ValueError) as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("State and backup unreadable; preserved for inspection")

    def save(self):
        atomic_json(self.path, self.data)
        # The SD's two copies share a FAT filesystem. A separate, infrequent
        # flash checkpoint survives loss of both; cap size and flash wear.
        if self.recovery_path and time.monotonic() >= self.checkpoint_at:
            self.checkpoint_at = time.monotonic() + 3600
            content = json.dumps(self.data, ensure_ascii=False).encode("utf-8")
            if len(content) > 65536:
                logging.warning("Flash checkpoint skipped: state exceeds 64 KiB")
                return
            try:
                self.recovery_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if shutil.disk_usage(self.recovery_path.parent).free < 196608:
                    logging.warning("Flash checkpoint skipped: low free space")
                    return
                if self.recovery_path.exists() and self.recovery_path.read_bytes() == content:
                    return
                atomic_json(self.recovery_path, self.data)
            except OSError as error:
                logging.warning("Flash checkpoint unavailable: %s", type(error).__name__)
