"""SOMICS web backend: activation, download tokens and web command mapping.

Every HTTP call the device makes to the backend lives here, so a new backend
endpoint only needs a function in this module.
"""
import json
import logging
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request

from .protocol import atomic_bytes, download

LOG = logging.getLogger(__name__)


class ApiError(RuntimeError):
    """Backend answered with a non-retryable HTTP error (bad code, bad credential...)."""


def post(api_base, path, body, timeout=15):
    """POST JSON and return the JSON reply.

    5xx/408/429 propagate as urllib HTTPError so callers can treat them as
    transient; other 4xx become ApiError carrying the backend's message.
    """
    request = urllib.request.Request(api_base + path, method="POST",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(65536).decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (408, 429) or 500 <= error.code <= 599:
            raise
        try:
            detail = json.loads(error.read(4096).decode("utf-8")).get("error")
        except Exception:
            detail = None
        raise ApiError("%s HTTP %s: %s" % (path, error.code, detail or error.reason))


def topics(cfg):
    base = "%s/device/%s" % (cfg["topic_base"], cfg["device_id"])
    return base + "/command", base + "/status"


# ---------------------------------------------------------------------------
# Activation: device ID + one-time code -> MQTT credential saved in config
# ---------------------------------------------------------------------------

def activate(config_path, cfg, device_id, code):
    try:
        data = post(cfg["api_base"], "/api/iot/activate",
                    {"deviceId": device_id, "activationCode": code,
                     "firmwareVersion": "mira-player"}, timeout=30)
    except urllib.error.HTTPError as error:
        # 502 = backend could not create the broker user; the code is NOT consumed.
        raise ApiError("/api/iot/activate HTTP %s: %s (code not consumed, retry later)"
                       % (error.code, error.reason))
    mqtt = data.get("mqtt") or {}
    if not data.get("ok") or not mqtt.get("username") or not mqtt.get("password"):
        raise ApiError("Activation response missing MQTT credential")
    path = Path(config_path)
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    raw.update(device_id=device_id, username=mqtt["username"], password=mqtt["password"],
               activated_at=(data.get("device") or {}).get("activatedAt"))
    # host/port stay as configured: the server may advertise a closed TLS port.
    atomic_bytes(path, (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    path.chmod(0o600)
    return mqtt


# ---------------------------------------------------------------------------
# Download tokens: MQTT password -> access/refresh pair -> Bearer download
# ---------------------------------------------------------------------------

class Tokens:
    def __init__(self, cfg, clock=time.monotonic, call=post):
        self.cfg, self.clock, self.call = cfg, clock, call
        self.lock = threading.Lock()
        self.access, self.access_until, self.refresh = None, 0.0, None

    def _store(self, data):
        if not data.get("ok") or not data.get("accessToken"):
            raise ApiError("Token response without accessToken")
        self.access = data["accessToken"]
        # Renew early so a long download never starts with a dying token.
        self.access_until = self.clock() + max(int(data.get("expiresIn", 600)) - 60, 30)
        self.refresh = data.get("refreshToken") or self.refresh

    def get(self, file_id=None):
        with self.lock:
            if self.access and self.clock() < self.access_until:
                return self.access
            if self.refresh:
                try:
                    self._store(self.call(self.cfg["api_base"], "/api/iot/token/refresh",
                                          {"deviceId": self.cfg["device_id"],
                                           "refreshToken": self.refresh}))
                    return self.access
                except ApiError as error:
                    # Refresh tokens rotate and die on re-provision; fall back to the password.
                    LOG.warning("Token refresh rejected (%s); using MQTT credential", error)
                    self.refresh = None
            body = {"deviceId": self.cfg["device_id"], "mqttPassword": self.cfg["password"]}
            if file_id:
                body["fileId"] = file_id
            self._store(self.call(self.cfg["api_base"], "/api/iot/token", body))
            LOG.info("Access token issued for %s", self.cfg["device_id"])
            return self.access

    def invalidate(self):
        with self.lock:
            self.access, self.access_until = None, 0.0


def authorized_fetch(tokens, fetch=download):
    """Controller fetch(): download with a Bearer token, renewing it once on 401."""
    def run(event, destination, allowed_hosts, stop, allowed_ports):
        for attempt in (1, 2):
            try:
                return fetch(event, destination, allowed_hosts, stop, allowed_ports,
                             token=tokens.get(event["id"]))
            except urllib.error.HTTPError as error:
                if error.code != 401 or attempt == 2:
                    raise
                tokens.invalidate()
    return run


# ---------------------------------------------------------------------------
# Web command mapping: backend payload on .../command -> controller input
# ---------------------------------------------------------------------------

def control_to_command(message, device_id, now):
    """Web control {id, type: control, action, volume?} -> internal command."""
    command_id = message.get("id")
    if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", command_id):
        raise ValueError("Missing/invalid control id")
    command = {"command_id": command_id, "device_id": device_id,
               "action": message.get("action"), "expires_at": int(now) + 60}
    if "volume" in message:
        command["volume"] = message["volume"]
    if "repeat" in message:
        command["repeat"] = message["repeat"]
    if "positionSeconds" in message:
        command["position"] = message["positionSeconds"]
    media_id = message.get("fileId", message.get("mediaId"))
    if media_id is not None:
        command["media_id"] = media_id
    return json.dumps(command).encode("utf-8")


def classify(payload, cfg, now=None):
    """Return ("file", event_bytes), ("command", command_bytes) or (None, None).

    The backend publishes file downloads as {type: file, action: download, ...}
    and player buttons as {type: control, action, volume?}; the documented
    internal command format (docs/MQTT_CONTRACT.md) is accepted as-is.
    """
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError):
        return "command", payload  # let the command parser report the error
    if not isinstance(message, dict):
        return "command", payload
    if message.get("type") == "control":
        return "command", control_to_command(message, cfg["device_id"],
                                             time.time() if now is None else now)
    if message.get("type") != "file":
        return "command", payload
    api_base = cfg["api_base"]
    if message.get("action") != "download":
        return None, None
    file_id = message.get("fileId")
    if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", file_id):
        raise ValueError("Missing/invalid fileId")
    endpoint = message.get("downloadEndpoint") or "/api/files/" + file_id
    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise ValueError("downloadEndpoint must be a server path")
    event = {"id": file_id, "type": "file", "delivery": "http", "downloadUrl": api_base + endpoint}
    for field in ("fileName", "mimeType", "size", "sha256"):
        event[field] = message.get(field)
    duration = message.get("durationSeconds")
    if type(duration) in (int, float) and 0 < duration < 86400:
        event["durationSeconds"] = duration
    return "file", json.dumps(event).encode("utf-8")
