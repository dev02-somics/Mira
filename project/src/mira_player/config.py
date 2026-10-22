import json
import os
from pathlib import Path
import re
import urllib.parse

DEFAULTS = {
    "port": 8083, "ws_path": "/mqtt", "mqtt_tls": False,
    "topic_base": "station/player", "volume": 30,
    "music_root": "/mnt/mmcblk0p1/mgwp/audio",
    "cache_dir": "/mnt/mmcblk0p1/mgwp/audio/mira-cache",
    "state_dir": "/mnt/mmcblk0p1/mira-player",
    "max_cache_bytes": 64 * 1024 * 1024, "max_pending": 8, "max_media": 256,
    "amp_gpio": 34, "mute_gpio": 17, "amp_active": 1, "unmute_level": 0,
    "heartbeat": 10, "allowed_ports": [80, 443], "mqtt_watchdog": 300,
}
FIELDS = set(DEFAULTS) | {"device_id", "host", "username", "password", "allowed_hosts",
                          "api_base", "activated_at"}


def load(path, require_password=True):
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ValueError("Config must be an object with documented fields only")
    cfg = dict(DEFAULTS, **raw)
    cfg["password"] = os.environ.get("MIRA_MQTT_PASSWORD", cfg.get("password", ""))
    for key in ("device_id", "host", "ws_path", "topic_base", "api_base",
                "music_root", "cache_dir", "state_dir"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            raise ValueError("Missing/invalid " + key)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", cfg["device_id"]):
        raise ValueError("Invalid device_id")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*", cfg["topic_base"]):
        raise ValueError("Invalid topic_base")
    bounds = {"port": (1, 65535), "volume": (0, 100),
              "max_cache_bytes": (1024, 1024 * 1024 * 1024),
              "max_pending": (1, 32), "max_media": (1, 4096),
              "amp_gpio": (0, 255), "mute_gpio": (0, 255), "heartbeat": (1, 300),
              "mqtt_watchdog": (60, 3600)}
    for key, (low, high) in bounds.items():
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError("Invalid integer range: " + key)
    if cfg["amp_gpio"] == cfg["mute_gpio"]:
        raise ValueError("GPIO pins must be different")
    for key in ("amp_active", "unmute_level"):
        if type(cfg[key]) is not int or cfg[key] not in (0, 1):
            raise ValueError("Invalid GPIO level")
    if type(cfg["mqtt_tls"]) is not bool:
        raise ValueError("mqtt_tls must be boolean")
    hosts = cfg.get("allowed_hosts")
    if (not isinstance(hosts, list) or not hosts or any(
            not isinstance(x, str) or not x or x != x.strip()
            or any(c in x for c in "/:@?#") for x in hosts)):
        raise ValueError("allowed_hosts must be a nonempty hostname list")
    if (not isinstance(cfg["allowed_ports"], list) or not cfg["allowed_ports"] or
            any(type(x) is not int or not 1 <= x <= 65535 for x in cfg["allowed_ports"])):
        raise ValueError("Invalid allowed_ports")
    api = urllib.parse.urlsplit(cfg["api_base"])
    if (api.scheme not in ("http", "https") or api.hostname not in hosts or api.username
            or api.password or api.path not in ("", "/") or api.query or api.fragment
            or api.port not in (None,) + tuple(cfg["allowed_ports"])):
        raise ValueError("api_base must be an http(s) origin listed in allowed_hosts")
    cfg["api_base"] = cfg["api_base"].rstrip("/")
    if require_password and (not isinstance(cfg.get("username"), str) or not cfg["username"]):
        raise ValueError("Missing MQTT username: activate the device first (--activate)")
    if require_password and (not isinstance(cfg["password"], str) or not cfg["password"]):
        raise ValueError("Set MQTT password in local config or MIRA_MQTT_PASSWORD")
    root, cache = Path(cfg["music_root"]).resolve(), Path(cfg["cache_dir"]).resolve()
    cache.relative_to(root)
    if cache == root:
        raise ValueError("cache_dir must be a subdirectory of music_root")
    if cfg["max_media"] < cfg["max_pending"]:
        raise ValueError("max_media must be at least max_pending")
    return cfg
