import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from helpers import Fixture
from mira_player import backend
from mira_player.backend import ApiError, Tokens, authorized_fetch, classify


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b'{"error":"nope"}'))


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now, self.calls, self.replies = 0.0, [], []
        def call(api, path, body):
            self.calls.append((path, body))
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        self.tokens = Tokens(self.f.cfg, clock=lambda: self.now, call=call)
    def tearDown(self):
        self.f.cleanup()

    def pair(self, n):
        return dict(ok=True, accessToken="a%d" % n, refreshToken="r%d" % n, expiresIn=600)

    def test_password_exchange_then_cache(self):
        self.replies = [self.pair(1)]
        self.assertEqual(self.tokens.get("file-1"), "a1")
        self.assertEqual(self.tokens.get("file-2"), "a1")
        self.assertEqual(self.calls, [("/api/iot/token", {"deviceId": "mira-01",
                                                          "mqttPassword": "test-only", "fileId": "file-1"})])

    def test_expired_access_uses_refresh_token(self):
        self.replies = [self.pair(1), self.pair(2)]
        self.tokens.get()
        self.now += 600
        self.assertEqual(self.tokens.get(), "a2")
        self.assertEqual(self.calls[1], ("/api/iot/token/refresh", {"deviceId": "mira-01", "refreshToken": "r1"}))

    def test_rejected_refresh_falls_back_to_password(self):
        self.replies = [self.pair(1), ApiError("revoked"), self.pair(3)]
        self.tokens.get()
        self.now += 600
        self.assertEqual(self.tokens.get(), "a3")
        self.assertEqual(self.calls[2][0], "/api/iot/token")

    def test_401_download_renews_token_once(self):
        self.replies = [self.pair(1), self.pair(2)]
        seen = []
        def fetch(event, path, hosts, stop, ports, token):
            seen.append(token)
            if len(seen) == 1:
                raise http_error(401)
        authorized_fetch(self.tokens, fetch)(dict(id="m"), Path("x"), [], None, [80])
        self.assertEqual(seen, ["a1", "a2"])

    def test_other_http_errors_not_retried(self):
        self.replies = [self.pair(1)]
        def fetch(*args, **kwargs):
            raise http_error(403)
        with self.assertRaises(urllib.error.HTTPError):
            authorized_fetch(self.tokens, fetch)(dict(id="m"), Path("x"), [], None, [80])
        self.assertEqual(len(self.calls), 1)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.path = Path(self.f.tmp.name) / "config.json"
        self.path.write_text(json.dumps({"device_id": "OLD", "host": "h", "username": "", "password": ""}))
    def tearDown(self):
        self.f.cleanup()

    def test_activation_saves_credential_keeps_endpoint(self):
        reply = dict(ok=True, device=dict(activatedAt="2026-10-07T05:39:50Z"),
                     mqtt=dict(username="SOMICS-1", password="pw", mqttHost="", mqttPort=8883))
        with patch.object(backend, "post", return_value=reply) as post:
            backend.activate(self.path, self.f.cfg, "SOMICS-1", "ABCD-EFGH-JKLM")
        self.assertEqual(post.call_args[0][2]["activationCode"], "ABCD-EFGH-JKLM")
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved, {"device_id": "SOMICS-1", "host": "h", "username": "SOMICS-1",
                                 "password": "pw", "activated_at": "2026-10-07T05:39:50Z"})

    def test_backend_failure_leaves_config_untouched(self):
        before = self.path.read_bytes()
        with patch.object(backend, "post", side_effect=http_error(502)):
            with self.assertRaisesRegex(ApiError, "code not consumed"):
                backend.activate(self.path, self.f.cfg, "SOMICS-1", "ABCD")
        self.assertEqual(self.path.read_bytes(), before)


CFG = {"device_id": "mira-01", "api_base": "http://h"}


class ClassifyTests(unittest.TestCase):
    def test_control_command_passes_through(self):
        payload = b'{"command_id":"c1","action":"stop"}'
        self.assertEqual(classify(payload, CFG), ("command", payload))

    def test_bad_file_id_rejected(self):
        with self.assertRaises(ValueError):
            classify(b'{"type":"file","action":"download","fileId":"../x"}', CFG)


if __name__ == "__main__":
    unittest.main()
