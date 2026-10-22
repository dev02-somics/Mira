import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def tests():
    for folder in ("src", "tests", "tools", "deploy"):
        for path in (ROOT / folder).rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 9))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Tests failed")
    return result.testsRun


def build():
    count = tests()
    payload = {}
    for path in (ROOT / "src/mira_player").glob("*.py"):
        payload["mira-player/mira_player/" + path.name] = path.read_bytes().replace(b"\r\n", b"\n")
    for source, target in (("deploy/start.sh", "start.sh"), ("deploy/mira_player.init", "mira_player.init"),
                           ("config/mira-player.example.json", "config.example.json")):
        payload["mira-player/" + target] = (ROOT / source).read_bytes().replace(b"\r\n", b"\n")
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}
    payload["mira-player/MANIFEST.json"] = (json.dumps(hashes, indent=2) + "\n").encode()
    def pack():
        output = io.BytesIO()
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for name, data in sorted(payload.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    entry.mode = 0o755 if name.endswith((".sh", ".init")) else 0o644
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(data))
        return output.getvalue()
    data = pack()
    if data != pack():
        raise RuntimeError("Build not reproducible")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        if set(archive.getnames()) != set(payload):
            raise RuntimeError("Unexpected package entries")
        for name, digest in hashes.items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != digest:
                raise RuntimeError("Package checksum mismatch")
    directory = ROOT / "dist"
    directory.mkdir(exist_ok=True)
    from mira_player import __version__ as version
    for old in directory.glob("mira-player-*.tar.gz*"):
        old.unlink()  # keep only the current release
    target = directory / ("mira-player-%s.tar.gz" % version)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(digest + "  " + target.name + "\n")
    print("PASS: %d tests; package verified and reproducible." % count)
    print(target)
    print("SHA256 " + digest)


def demo():
    from helpers import Fixture
    f = Fixture()
    try:
        f.loaded()
        f.audio.position = 18.5
        f.command("pause")
        print("PAUSED", json.dumps(f.controller.snapshot()))
        f.command("resume")
        f.command("set_volume", volume=40)
        print("RESUMED", json.dumps(f.controller.snapshot()))
        f.command("replay")
        f.tick()
        print("REPLAYED", json.dumps(f.controller.snapshot()))
        f.command("stop")
        print("STOPPED", json.dumps(f.controller.snapshot()))
    finally:
        f.cleanup()
    print("Offline demonstration used simulated audio.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("test", "build", "demo", "validate"))
    args = parser.parse_args()
    if args.command == "test":
        tests()
    elif args.command == "build":
        build()
    elif args.command == "demo":
        demo()
    else:
        from mira_player.app import main as app
        app(["--config", str(ROOT / "config/mira-player.example.json"),
             "--validate-event", str(ROOT / "examples/file-event.json")])


if __name__ == "__main__":
    main()
