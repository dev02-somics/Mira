import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from mira_player.protocol import download, validate_event, validate_url
from mira_player.commands import parse
from mira_player.store import Store
from mira_player.config import load


class ProtocolTests(unittest.TestCase):
    def test_real_http_verified_download_and_bad_hash_cleanup(self):
        data = b"ID3-test-audio"
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        port = server.server_address[1]
        event = dict(id="local", type="file", delivery="http", fileName="test.mp3",
                     mimeType="audio/mpeg", size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                     downloadUrl="http://127.0.0.1:%d/api/files/local" % port)
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "audio.mp3"
                self.assertEqual(validate_event(json.dumps(event).encode(), ["127.0.0.1"], [port]), event)
                download(event, path, ["127.0.0.1"], threading.Event(), [port])
                self.assertEqual(path.read_bytes(), data)
                path.unlink()
                event["sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    download(event, path, ["127.0.0.1"], threading.Event(), [port])
                self.assertFalse(path.exists())
                self.assertFalse(path.with_suffix(".mp3.part").exists())
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_unapproved_download_urls_rejected(self):
        for url in ("file:///etc/passwd", "http://evil.invalid/api/files/a",
                    "http://files.example.invalid:22/api/files/a",
                    "http://u:p@files.example.invalid/api/files/a",
                    "http://files.example.invalid/etc/passwd"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url, ["files.example.invalid"])

    def test_invalid_commands_rejected(self):
        base = dict(command_id="valid", device_id="mira-01", action="set_volume", volume=40, expires_at=1060)
        for change in (dict(volume=True), dict(volume=101), dict(volume=-1),
                       dict(action="shell"), dict(expires_at=float("inf")),
                       dict(expires_at=1000), dict(expires_at=1400), dict(device_id="other"),
                       dict(unexpected="x"), dict(command_id="../bad"), dict(command_id=True)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse(json.dumps(dict(base, **change)).encode(), "mira-01", 1000)

    def test_zero_volume_and_replay_valid(self):
        for value in (dict(action="set_volume", volume=0), dict(action="replay")):
            cmd = dict(command_id="valid", device_id="mira-01", expires_at=1060, **value)
            self.assertEqual(parse(json.dumps(cmd).encode(), "mira-01", 1000), cmd)

    def test_state_backup_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder, 30)
            store.data["volume"] = 41
            store.save()
            store.path.write_bytes(b"")
            self.assertEqual(Store(folder, 30).data["volume"], 41)

    def test_corrupt_state_and_backup_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder, 30)
            store.save()
            store.path.write_bytes(b"bad")
            Path(str(store.path) + ".bak").write_bytes(b"bad")
            with self.assertRaises(RuntimeError):
                Store(folder, 30)
            self.assertEqual(store.path.read_bytes(), b"bad")

    def test_independent_checkpoint_recovers_both_sd_journals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            checkpoint = root / "flash/recovery.json"
            store = Store(root / "sd", 30, recovery_path=checkpoint)
            store.data["volume"] = 41
            store.save()
            store.path.write_bytes(b"")
            Path(str(store.path) + ".bak").write_bytes(b"")
            recovered = Store(root / "sd", 30, recovery_path=checkpoint)
            self.assertEqual(recovered.data["volume"], 41)
            self.assertEqual(len(list((root / "sd").glob('*.damaged-*'))), 2)
            recovered.save()
            self.assertEqual(Store(root / "sd", 30).data["volume"], 41)

    def test_checkpoint_rate_limit_preserves_flash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            checkpoint = root / "flash/recovery.json"
            store = Store(root / "sd", 30, recovery_path=checkpoint)
            store.save()
            original = checkpoint.read_bytes()
            store.data["volume"] = 60
            store.save()
            self.assertEqual(checkpoint.read_bytes(), original)
            self.assertEqual(Store(root / "sd", 30).data["volume"], 60)

    def test_invalid_config_and_offline_credentials(self):
        root = Path(__file__).resolve().parents[1]
        base = json.loads((root / "config/mira-player.example.json").read_text())
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(base), encoding="utf-8-sig")
            self.assertEqual(load(path, False)["volume"], 30)
            with self.assertRaises(ValueError):
                load(path)
            for change in (dict(volume=True), dict(volume=101), dict(port=0),
                           dict(allowed_hosts=[]), dict(mqtt_tls="yes"), dict(device_id="x/y"),
                           dict(unknown=1), dict(api_base="http://other.invalid"),
                           dict(api_base="ftp://files.example.invalid"), dict(topic_base="a/#"),
                           dict(cache_dir=base.get("music_root", "/mnt/mmcblk0p1/mgwp/audio"))):
                path.write_text(json.dumps(dict(base, **change)), encoding="utf-8")
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load(path, False)


if __name__ == "__main__":
    unittest.main()
