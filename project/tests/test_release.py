import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mira_installer", ROOT / "deploy/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ReleaseTests(unittest.TestCase):
    def archive(self, mutation=None):
        payload = {}
        for name in installer.EXPECTED:
            if name.endswith(".py"):
                payload[name] = b"# test module\n"
            else:
                payload[name] = b"test\n"
        payload[installer.MANIFEST] = json.dumps(
            {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}).encode()
        if mutation:
            mutation(payload)
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, data in sorted(payload.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return output.getvalue()

    def verify(self, data, sha=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "release.tar.gz"
            path.write_bytes(data)
            return installer.verify(path, sha or hashlib.sha256(data).hexdigest())

    def test_valid_manifest_and_exact_files(self):
        result = self.verify(self.archive())
        self.assertEqual(set(result), installer.EXPECTED | {installer.MANIFEST})

    def test_outer_archive_checksum_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(self.archive(), "0" * 64)

    def test_modified_payload_rejected_even_with_correct_outer_sha(self):
        def tamper(payload):
            payload["mira-player/start.sh"] = b"modified"
        with self.assertRaises(ValueError):
            self.verify(self.archive(tamper))

    def test_path_traversal_entry_rejected(self):
        def tamper(payload):
            payload["../../outside"] = b"bad"
        with self.assertRaises(ValueError):
            self.verify(self.archive(tamper))

    def test_missing_module_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(self.archive(lambda payload: payload.pop("mira-player/mira_player/app.py")))

    def test_duplicate_and_symlink_entries_rejected(self):
        data = self.archive()
        for link in (False, True):
            output = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as original:
                members = [(m, original.extractfile(m).read()) for m in original]
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                for info, content in members:
                    archive.addfile(info, io.BytesIO(content))
                entry = tarfile.TarInfo("mira-player/start.sh")
                if link:
                    entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
                archive.addfile(entry)
            with self.subTest(link=link), self.assertRaises(ValueError):
                self.verify(output.getvalue())


if __name__ == "__main__":
    unittest.main()
