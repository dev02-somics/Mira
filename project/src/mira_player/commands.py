import json
import math
import re

ACTIONS = {"play", "pause", "resume", "stop", "replay", "set_volume", "next", "previous",
           "set_repeat", "seek"}
REPEAT_MODES = ("off", "one")


def parse(payload, device_id, now):
    if len(payload) > 16384:
        raise ValueError("Command exceeds 16 KiB")
    cmd = json.loads(payload.decode("utf-8"))
    fields = {"command_id", "device_id", "action", "expires_at", "media_id", "volume",
              "repeat", "position"}
    if not isinstance(cmd, dict) or set(cmd) - fields:
        raise ValueError("Invalid command object/fields")
    key = cmd.get("command_id")
    if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", key):
        raise ValueError("Invalid command_id")
    if cmd.get("device_id") != device_id:
        raise ValueError("Wrong device")
    action = cmd.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("Unsupported action")
    expiry = cmd.get("expires_at")
    if (type(expiry) not in (int, float) or not math.isfinite(expiry)
            or expiry <= now or expiry > now + 300):
        raise ValueError("Expired command or expiry more than 300 seconds ahead")
    if action in ("play", "seek") and "media_id" not in cmd:
        raise ValueError(action + " requires media_id")
    if "media_id" in cmd and (action not in ("play", "replay", "seek") or
            not isinstance(cmd["media_id"], str) or not 0 < len(cmd["media_id"]) <= 256):
        raise ValueError("Invalid media_id")
    if action == "set_volume":
        if type(cmd.get("volume")) is not int or not 0 <= cmd["volume"] <= 100:
            raise ValueError("volume must be integer 0..100")
    elif "volume" in cmd:
        raise ValueError("volume applies only to set_volume")
    if action == "set_repeat":
        if cmd.get("repeat") not in REPEAT_MODES:
            raise ValueError("repeat must be off or one")
    elif "repeat" in cmd:
        raise ValueError("repeat applies only to set_repeat")
    if action == "seek":
        position = cmd.get("position")
        if (type(position) not in (int, float) or not math.isfinite(position)
                or not 0 <= position <= 86400):
            raise ValueError("position must be seconds 0..86400")
    elif "position" in cmd:
        raise ValueError("position applies only to seek")
    return cmd
