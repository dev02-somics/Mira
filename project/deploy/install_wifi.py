"""Initial installation only, backed up before changing OpenWrt network settings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from mira_wifi.core import Profiles, atomic_json, validate_profile
from mira_wifi.openwrt import batch, run, setline, ubus

CONFIGS = ("wireless", "network", "dhcp", "firewall")
TARGET = Path("/root/mira-wifi")
SETTINGS = Path("/etc/mira-wifi")
INIT = Path("/etc/init.d/mira_wifi")
CLI = Path("/usr/bin/mira-wifi")
PAYLOAD = {"mira_wifi/__init__.py", "mira_wifi/core.py", "mira_wifi/openwrt.py", "mira_wifi/app.py",
           "mira_wifi/static/index.html", "mira_wifi/static/wifi.js", "mira_wifi/static/style.css",
           "start.sh", "mira_wifi.init", "install.py", "mira-wifi", "mira_wifi/cli.py", "mira_wifi/mqtt.py"}


def verify():
    manifest = json.loads((ROOT / "MANIFEST.json").read_text())
    if set(manifest) != PAYLOAD:
        raise RuntimeError("Unexpected manifest")
    for name, digest in manifest.items():
        file = ROOT / name
        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Package verification failed")
    print("Package verified (%d files)." % len(manifest))


def reload_network():
    run(["ubus", "call", "network", "reload"], timeout=30)
    run(["/etc/init.d/firewall", "restart"], timeout=30)
    run(["/etc/init.d/dnsmasq", "restart"], timeout=30)
    run(["wifi", "reload"], timeout=30)


def rollback(path):
    path = Path(path).resolve()
    if path.parent != Path("/root") or not re.fullmatch(r"mira-wifi-backup-\d+", path.name):
        raise RuntimeError("Unexpected backup path")
    if not all((path / name).is_file() for name in CONFIGS):
        raise RuntimeError("Backup incomplete")
    if INIT.exists():
        run([str(INIT), "stop"], timeout=70)
        run([str(INIT), "disable"])
        INIT.unlink()
    if CLI.is_file() and "mira_wifi.cli" in CLI.read_text():
        CLI.unlink()
    for name in CONFIGS:
        batch(["revert " + name])
        shutil.copy2(path / name, Path("/etc/config") / name)
    reload_network()
    print("Original network configuration restored from " + str(path))


def apply():
    import secrets
    if os.name != "posix" or os.geteuid() != 0 or not Path("/etc/openwrt_release").exists():
        raise RuntimeError("Requires root on OpenWrt")
    if TARGET.exists() or INIT.exists() or SETTINGS.exists() or CLI.exists():
        raise RuntimeError("Existing installation found; do not overwrite its saved passwords.")
    if run(["uci", "changes"]).strip():
        raise RuntimeError("Apply or discard existing UCI changes first.")
    wireless = ubus("uci", "get", {"config": "wireless"})["values"]
    if wireless.get("radio0", {}).get("type") != "mac80211":
        raise RuntimeError("Expected mac80211 radio0")
    phy = run(["iw", "phy", "phy0", "info"])
    if "* AP" not in phy or "managed" not in phy:
        raise RuntimeError("Radio must support AP and station modes")
    if any(k.startswith("mira_") for k in wireless):
        raise RuntimeError("Managed wireless section already exists")
    for name in ("network", "dhcp", "firewall"):
        values = ubus("uci", "get", {"config": name})["values"]
        if any(k.startswith("mira_") for k in values):
            raise RuntimeError("Managed UCI section already exists")
    interfaces = ubus("network.interface", "dump").get("interface", [])
    import ipaddress
    setup_net = ipaddress.ip_network("192.168.12.0/24")
    for interface in interfaces:
        for addr in interface.get("ipv4-address", []):
            subnet = ipaddress.ip_network(addr["address"] + "/" + str(addr["mask"]), strict=False)
            if setup_net.overlaps(subnet):
                raise RuntimeError("Setup subnet conflicts with current network")
    ubus("iwinfo", "devices")
    if shutil.disk_usage("/root").free < 350000:
        raise RuntimeError("At least 350 KB writable flash is required")
    # New listener must be available before altering any network.
    import socket
    with socket.socket() as sock:
        sock.bind(("0.0.0.0", 8088))
    backup = Path("/root/mira-wifi-backup-" + str(int(time.time())))
    backup.mkdir(mode=0o700)
    for name in CONFIGS:
        shutil.copy2(Path("/etc/config") / name, backup / name)
        os.chmod(backup / name, 0o600)
    password = secrets.token_urlsafe(12)
    suffix = Path("/sys/class/net/eth0/address").read_text().strip().replace(":", "")[-4:].upper()
    ssid = "MIRA-Setup-" + suffix
    try:
        shutil.copytree(ROOT / "mira_wifi", TARGET / "mira_wifi")
        shutil.copy2(ROOT / "start.sh", TARGET / "start.sh")
        shutil.copy2(ROOT / "install.py", backup / "install.py")
        # Standalone rollback remains usable if the app is later replaced.
        shutil.copytree(ROOT / "mira_wifi", backup / "mira_wifi")
        SETTINGS.mkdir(mode=0o700)
        profiles = Profiles(SETTINGS / "profiles.json")
        lines = []
        for name, value in wireless.items():
            if value.get(".type") == "wifi-iface" and value.get("device") == "radio0":
                if value.get("mode") == "sta" and value.get("encryption") in ("psk2", "psk2+ccmp", "psk-mixed"):
                    try:
                        profiles.save(validate_profile(value["ssid"], "psk2", value["key"]), 0)
                    except (KeyError, ValueError):
                        pass
                lines.append(setline("wireless." + name + ".disabled", "1"))
        settings = dict(setup_ssid=ssid, password_hash=hashlib.sha256(password.encode()).hexdigest(),
                        backup=str(backup))
        atomic_json(SETTINGS / "settings.json", settings)
        access = ("Wi-Fi cai dat: " + ssid + "\nMat khau Wi-Fi va trang quan ly: " + password +
                  "\nTrang cai dat: http://192.168.12.1:8088\nQua LAN: http://192.168.11.102:8088\n")
        (SETTINGS / "access.txt").write_text(access)
        os.chmod(SETTINGS / "access.txt", 0o600)
        values = {
            "wireless.radio0.disabled": "0", "wireless.radio0.channel": "1",
            "wireless.mira_ap": "wifi-iface", "wireless.mira_ap.device": "radio0",
            "wireless.mira_ap.mode": "ap", "wireless.mira_ap.network": "mira_setup",
            "wireless.mira_ap.ssid": ssid, "wireless.mira_ap.encryption": "psk2",
            "wireless.mira_ap.key": password, "wireless.mira_ap.disabled": "0",
            "wireless.mira_sta": "wifi-iface", "wireless.mira_sta.device": "radio0",
            "wireless.mira_sta.mode": "sta", "wireless.mira_sta.network": "mira_uplink",
            "wireless.mira_sta.disabled": "1", "wireless.mira_sta.encryption": "psk2",
            "network.mira_bridge": "device", "network.mira_bridge.name": "br-mira",
            "network.mira_bridge.type": "bridge", "network.mira_bridge.bridge_empty": "1",
            "network.mira_setup": "interface", "network.mira_setup.device": "br-mira",
            "network.mira_setup.proto": "static", "network.mira_setup.ipaddr": "192.168.12.1",
            "network.mira_setup.netmask": "255.255.255.0",
            "network.mira_uplink": "interface", "network.mira_uplink.proto": "dhcp",
            "network.mira_uplink.metric": "5",
            "dhcp.mira_setup": "dhcp", "dhcp.mira_setup.interface": "mira_setup",
            "dhcp.mira_setup.start": "10", "dhcp.mira_setup.limit": "50",
            "dhcp.mira_setup.leasetime": "1h",
        }
        # Prefer Wi-Fi over the modem/default LAN gateway when it has a DHCP lease.
        network = ubus("uci", "get", {"config": "network"})["values"]
        for name, metric in (("wan", "20"), ("lan", "50")):
            if name in network:
                values["network." + name + ".metric"] = metric
        for zone, network_name in (("mira_setup", "mira_setup"), ("mira_wifi", "mira_uplink")):
            base = "firewall." + zone
            values.update({base: "zone", base + ".name": zone, base + ".network": network_name,
                           base + ".input": "REJECT", base + ".output": "ACCEPT", base + ".forward": "REJECT"})
            for tag, proto, ports in (("web", "tcp", "22 8088"), ("dns", "tcp udp", "53"),
                                      ("dhcp", "udp", "67 68"), ("mdns", "udp", "5353")):
                rule = base + "_" + tag
                values.update({rule: "rule", rule + ".name": zone + "-" + tag, rule + ".src": zone,
                               rule + ".proto": proto, rule + ".dest_port": ports, rule + ".target": "ACCEPT"})
        values.update({"firewall.mira_block_wan": "rule", "firewall.mira_block_wan.name": "MIRA-portal-block-WAN",
                       "firewall.mira_block_wan.src": "wan", "firewall.mira_block_wan.proto": "tcp",
                       "firewall.mira_block_wan.dest_port": "8088", "firewall.mira_block_wan.target": "REJECT"})
        batch(lines + [setline(k, v) for k, v in values.items()] + ["commit " + name for name in CONFIGS])
        os.chmod("/etc/config/wireless", 0o600)
        reload_network()
        shutil.copy2(ROOT / "mira-wifi", CLI)
        os.chmod(CLI, 0o755)
        shutil.copy2(ROOT / "mira_wifi.init", INIT)
        os.chmod(INIT, 0o755)
        os.chmod(TARGET / "start.sh", 0o755)
        run([str(INIT), "enable"])
        run([str(INIT), "start"])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urlopen("http://127.0.0.1:8088/", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(2)
        else:
            raise RuntimeError("Portal startup timed out")
        print("Installed. Setup SSID: " + ssid)
        print("Credentials: /etc/mira-wifi/access.txt (root only)")
        print("Backup: " + str(backup))
        print("Portal: http://192.168.12.1:8088 or LAN http://192.168.11.102:8088")
    except Exception:
        rollback(backup)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback")
    args = parser.parse_args()
    os.umask(0o077)
    if args.rollback:
        rollback(args.rollback)
    else:
        verify()
        if args.apply:
            apply()
        else:
            print("Plan: preserve LAN IP, back up 4 UCI configs, import saved Wi-Fi, add protected setup AP")
            print("192.168.12.1/24 + DHCP + portal :8088 + procd. No changes without --apply.")


if __name__ == "__main__":
    main()
