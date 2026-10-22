"""OpenWrt 21.x adapter; UCI passwords travel over stdin, never through a shell."""
import json
import os
from pathlib import Path
import subprocess


def run(args, data=None, timeout=20):
    try:
        result = subprocess.run(args, input=data, text=True, encoding="utf-8",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("OpenWrt command unavailable/timed out") from None
    if result.returncode:
        raise RuntimeError("OpenWrt command failed: " + args[0])
    return result.stdout


def quote(value):
    text = str(value)
    if any(ord(c) < 32 for c in text):
        raise ValueError("UCI control character")
    return "'" + text.replace("'", "'\\''") + "'"


def batch(lines):
    run(["uci", "-q", "batch"], "\n".join(lines) + "\n")


def setline(key, value):
    return "set " + key + "=" + quote(value)


def ubus(obj, method, data=None):
    return json.loads(run(["ubus", "call", obj, method, json.dumps(data or {})], timeout=20))


class OpenWrt:
    def wireless(self):
        return ubus("network.wireless", "status").get("radio0", {})

    def device(self, section):
        for interface in self.wireless().get("interfaces", []):
            if interface.get("section") == section:
                return interface.get("ifname")
        return None

    def scan(self):
        dev = self.device("mira_ap") or self.device("mira_sta")
        if not dev:
            raise RuntimeError("Radio not ready")
        return ubus("iwinfo", "scan", {"device": dev}).get("results", [])

    def connected_ip(self, ssid):
        try:
            status = ubus("network.interface.mira_uplink", "status")
            if not status.get("up") or not status.get("ipv4-address"):
                return None
            dev = self.device("mira_sta")
            if not dev:
                return None
            info = ubus("iwinfo", "info", {"device": dev})
            if info.get("ssid") != ssid:
                return None
            # DHCP can outlive a broken association. Require an actual kernel link.
            link = run(["iw", "dev", dev, "link"])
            if "Connected to " not in link:
                return None
            return status["ipv4-address"][0]["address"]
        except RuntimeError:
            return None

    def apply(self, values):
        # Base config is AP-only. Candidate station credentials stay in RAM.
        # Never commit wireless here: avoids flash writes for every retry.
        batch(["revert wireless.mira_sta", "revert wireless.radio0.channel"] +
              [setline(k, v) for k, v in values.items()])
        path = Path("/tmp/.uci/wireless")
        if path.exists():
            os.chmod(path, 0o600)
        run(["wifi", "reload", "radio0"], timeout=30)
        # reload preserves netifd's administrative-down flag. Explicitly raise
        # the managed radio so recovery also works after a radio shutdown.
        # This ubus action succeeds with an empty response on OpenWrt 21.x.
        run(["ubus", "call", "network.wireless", "up", '{"device":"radio0"}'])

    def setup_only(self):
        self.apply({"wireless.mira_sta.disabled": "1", "wireless.radio0.channel": "1"})

    def connect(self, profile, channel):
        if not isinstance(channel, int) or not 1 <= channel <= 13:
            raise ValueError("Kênh Wi-Fi không được hỗ trợ.")
        self.apply({"wireless.mira_sta.disabled": "0",
                    "wireless.mira_sta.ssid": profile["ssid"],
                    "wireless.mira_sta.key": profile["password"],
                    "wireless.mira_sta.encryption": "psk2",
                    "wireless.radio0.channel": str(channel)})
