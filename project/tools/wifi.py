"""Wi-Fi test/build/demo entry point; Python 3.9+, no dependencies."""
import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def tests():
    for path in list((ROOT / "src/mira_wifi").glob("*.py")) + [ROOT / "deploy/install_wifi.py"] + list((ROOT / "tests").glob("test_wifi*.py")):
        ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 9))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT / "tests"), "test_wifi*.py"))
    if not result.wasSuccessful():
        raise RuntimeError("Wi-Fi tests failed")
    return result.testsRun


def build():
    count = tests()
    payload = {}
    for path in (ROOT / "src/mira_wifi").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            payload["mira_wifi/" + path.relative_to(ROOT / "src/mira_wifi").as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    for source, target in (("deploy/install_wifi.py", "install.py"), ("deploy/mira_wifi.init", "mira_wifi.init"),
                           ("deploy/wifi-start.sh", "start.sh"), ("deploy/mira-wifi", "mira-wifi")):
        payload[target] = (ROOT / source).read_bytes().replace(b"\r\n", b"\n")
    payload["MANIFEST.json"] = json.dumps({n:hashlib.sha256(b).hexdigest() for n,b in sorted(payload.items())}, indent=2).encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for name, data in sorted(payload.items()):
                entry = tarfile.TarInfo("mira-wifi/" + name)
                entry.size, entry.mode = len(data), 0o755 if name.endswith((".sh", ".init")) or name == "mira-wifi" else 0o644
                archive.addfile(entry, io.BytesIO(data))
    from mira_wifi import __version__
    path = ROOT / ("dist/mira-wifi-" + __version__ + ".tar.gz")
    path.parent.mkdir(exist_ok=True)
    for old in path.parent.glob("mira-wifi-*.tar.gz*"):
        old.unlink()  # keep only the current release
    path.write_bytes(buffer.getvalue())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".gz.sha256").write_text(digest + "  " + path.name + "\n")
    print("PASS %d Wi-Fi tests. %s\nSHA256 %s" % (count, path, digest))


def demo():
    from mira_wifi.core import Profiles, Manager
    from mira_wifi.app import serve
    class Demo:
        def __init__(self):
            self.ssid = None
        def scan(self):
            return [dict(ssid=name, signal=level, channel=channel,
                         encryption={"wpa":[2], "authentication":["psk"]})
                    for name,level,channel in (("SOMICS",-48,1), ("Nha cua toi",-64,6), ("Phong khach",-78,11))]
        def setup_only(self):
            self.ssid = None
        def connect(self, profile, channel):
            self.ssid = profile["ssid"]
        def connected_ip(self, ssid):
            return "192.168.1.120" if ssid == self.ssid else None
    with tempfile.TemporaryDirectory() as folder:
        manager = Manager(Demo(), Profiles(Path(folder) / "profiles.json"), "MIRA-Setup-DEMO")
        print("Demo only: http://127.0.0.1:8088 ; password: demo-wifi", flush=True)
        serve(manager, hashlib.sha256(b"demo-wifi").hexdigest(), "127.0.0.1", 8088)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("test", "build", "demo"))
    args = parser.parse_args()
    {"test": tests, "build": build, "demo": demo}[args.command]()
