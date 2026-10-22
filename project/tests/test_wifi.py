import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from mira_wifi.core import Manager, Profiles, normalize_scan, validate_profile
from mira_wifi.openwrt import OpenWrt, quote
from mira_wifi.app import Server, Sessions


def wifi(ssid="Home", signal=-50, channel=6):
    return dict(ssid=ssid, signal=signal, channel=channel, encryption={"wpa":[2], "authentication":["psk"]})


class Radio:
    def __init__(self):
        self.networks = [wifi(), wifi("Office", -70)]
        self.calls, self.ip, self.scan_error = [], None, False
    def scan(self):
        if self.scan_error:
            raise RuntimeError("scan failed")
        return self.networks
    def connect(self, profile, channel):
        self.calls.append(("connect", profile["ssid"], profile["password"], channel))
        self.ip = None
    def connected_ip(self, ssid):
        return self.ip
    def setup_only(self):
        self.calls.append(("setup",))
        self.ip = None


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "profiles.json"
        self.profiles, self.radio, self.now = Profiles(self.path), Radio(), 1000
        self.manager = Manager(self.radio, self.profiles, "MIRA-Setup-TEST",
                               clock=lambda:self.now, wall=lambda:5000)
        self.manager.step()
    def join(self, ssid="Home", password="secret123"):
        self.manager.submit("connect", dict(ssid=ssid, password=password))
        self.manager.step()
    def test_password_only_saved_after_dhcp(self):
        self.join()
        self.assertEqual(self.manager.snapshot()["state"], "connecting")
        self.assertFalse(self.path.exists())
        self.radio.ip = "192.168.1.9"
        self.manager.step()
        self.assertEqual(self.manager.snapshot()["ip"], self.radio.ip)
        self.assertEqual(Profiles(self.path).find("Home")["password"], "secret123")
        self.assertNotIn("secret123", json.dumps(self.manager.snapshot()))
    def test_failed_password_keeps_existing_and_restores_ap(self):
        self.profiles.save(validate_profile("Home", "psk2", "oldsecret"), 1)
        self.join(password="badsecret")
        self.now += 51
        self.manager.step()
        self.assertEqual(self.manager.snapshot()["state"], "setup")
        self.assertEqual(self.radio.calls[-1], ("setup",))
        self.assertEqual(Profiles(self.path).find("Home")["password"], "oldsecret")
        self.now += 16
        self.manager.step()
        self.assertEqual(self.radio.calls[-1], ("setup",))
    def test_last_success_priority_over_signal(self):
        self.profiles.save(validate_profile("Home", "psk2", "secret123"), 10)
        self.profiles.save(validate_profile("Office", "psk2", "secret456"), 20)
        self.now += 16
        self.manager.step()
        self.assertEqual(self.radio.calls[-1][1], "Office")
    def test_no_switch_while_connected(self):
        self.join()
        self.radio.ip = "192.168.1.9"
        self.manager.step()
        self.profiles.save(validate_profile("Office", "psk2", "secret456"), 6000)
        self.now += 60
        self.manager.step()
        self.assertEqual(len(self.radio.calls), 1)
    def test_loss_grace_then_fallback(self):
        self.join()
        self.radio.ip = "192.168.1.9"
        self.manager.step()
        self.radio.ip = None
        self.manager.step()
        self.assertEqual(self.manager.snapshot()["state"], "reconnecting")
        self.now += 21
        self.manager.step()
        self.assertEqual(self.manager.snapshot()["state"], "setup")
    def test_recovery_within_grace(self):
        self.join()
        self.radio.ip = "192.168.1.9"
        self.manager.step()
        self.radio.ip = None
        self.manager.step()
        self.now += 3
        self.radio.ip = "192.168.1.10"
        self.manager.step()
        self.assertEqual(self.manager.snapshot()["state"], "connected")
        self.assertEqual(len(self.radio.calls), 1)
    def test_scan_failure_does_not_drop_connection(self):
        self.join()
        self.radio.ip = "192.168.1.9"
        self.manager.step()
        self.radio.scan_error = True
        self.now += 50
        self.manager.step()
        self.assertEqual(self.manager.snapshot()["state"], "connected")
        self.assertEqual(len(self.radio.calls), 1)
    def test_saved_password_can_be_reused(self):
        self.profiles.save(validate_profile("Home", "psk2", "secret123"), 0)
        self.join(password=None)
        self.assertEqual(self.radio.calls[-1][2], "secret123")
    def test_forget_active_returns_to_setup(self):
        self.join()
        self.radio.ip = "192.168.1.9"
        self.manager.step()
        self.manager.submit("forget", {"ssid":"Home"})
        self.manager.step()
        self.assertIsNone(Profiles(self.path).find("Home"))
        self.assertEqual(self.manager.snapshot()["state"], "setup")
    def test_missing_wifi_not_attempted(self):
        self.join("Not visible")
        self.assertEqual(self.radio.calls, [])
    def test_pending_cannot_be_overwritten(self):
        self.join()
        self.join("Office")
        self.assertEqual(len(self.radio.calls), 1)
        self.assertEqual(self.manager.snapshot()["ssid"], "Home")
    def test_snapshot_cannot_mutate_controller(self):
        self.manager.snapshot()["networks"].clear()
        self.assertEqual(len(self.manager.snapshot()["networks"]), 2)
    def test_profile_limit_and_corruption(self):
        for i in range(16):
            self.profiles.save(validate_profile("wifi" + str(i), "psk2", "secret123"), i)
        with self.assertRaises(ValueError):
            self.profiles.save(validate_profile("overflow", "psk2", "secret123"), 20)
        self.path.write_text("{broken")
        self.assertEqual(len(Profiles(self.path).items), 16)
        Path(str(self.path) + ".bak").write_text("{broken")
        with self.assertRaises(ValueError):
            Profiles(self.path)
    def test_bounded_queue(self):
        for i in range(8):
            self.manager.submit("scan", {})
        with self.assertRaises(ValueError):
            self.manager.submit("scan", {})

    def test_connected_radio_scans_only_on_explicit_request(self):
        self.join()
        self.radio.ip = "192.168.1.9"
        self.manager.step()
        self.now += 60
        with patch.object(self.radio, "scan", wraps=self.radio.scan) as scan:
            self.manager.step()
            scan.assert_not_called()
            self.manager.submit("scan", {})
            self.manager.step()
            scan.assert_called_once()


class ValidationTests(unittest.TestCase):
    def test_ssid_utf8_and_credential_rules(self):
        for ssid, mode, password in [("", "psk2", "secret123"), ("é"*17, "psk2", "secret123"),
                                     ("bad\nssid", "psk2", "secret123"), ("Home", "none", ""),
                                     ("Home", "psk2", "short"), ("Home", "psk2", "a"*65)]:
            with self.subTest(ssid=ssid, mode=mode), self.assertRaises(ValueError):
                validate_profile(ssid, mode, password)
        validate_profile("Nhà", "psk2", "ab'cd$()\\123"+chr(96))
        validate_profile("Home", "psk2", "a"*64)
    def test_scan_deduplicates_strongest_without_security_downgrade(self):
        insecure = wifi()
        insecure["encryption"] = {"enabled":False}
        rows = normalize_scan([wifi(signal=-80), wifi(signal=-40), insecure])
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(r["signal"] for r in rows if r["supported"]), -40)
    def test_key_goes_to_stdin_not_shell_or_argv(self):
        calls = []
        password = "ab'cd$()\\123"+chr(96)
        def run(args, data=None, timeout=20):
            calls.append((args, data))
            return ""
        with patch("mira_wifi.openwrt.run", run):
            OpenWrt().connect(validate_profile("Home", "psk2", password), 6)
        self.assertEqual(calls[-1][0], ["ubus", "call", "network.wireless", "up", '{"device":"radio0"}'])
        self.assertNotIn("ab", str([c[0] for c in calls]))
        self.assertIn(quote(password), calls[0][1])
        self.assertFalse(any("commit" in (data or "") for _,data in calls))
        with self.assertRaises(ValueError):
            quote("a\nb")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.manager = Manager(Radio(), Profiles(Path(cls.tmp.name) / "profiles.json"), "MIRA-Setup-TEST")
        cls.manager.step()
        cls.server = Server(("127.0.0.1", 0), cls.manager, Sessions(hashlib.sha256(b"portal-secret").hexdigest()))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()
    def request(self, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            hdr = {"Content-Type":"application/json"}
            hdr.update(headers or {})
            conn.request("GET" if data is None else "POST", path, body=None if data is None else json.dumps(data), headers=hdr)
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            conn.close()
    def login(self):
        status, headers, raw = self.request("/api/login", {"password":"portal-secret"})
        self.assertEqual(status, 200)
        return {"Cookie":headers["Set-Cookie"].split(";")[0], "X-CSRF-Token":json.loads(raw)["csrf"]}
    def test_assets_are_local_and_protected_against_framing(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"wifi.js", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
    def test_auth_required_and_passwords_not_returned(self):
        self.assertEqual(self.request("/api/status")[0], 401)
        status, _, body = self.request("/api/status", headers=self.login())
        self.assertEqual(status, 200)
        self.assertNotIn(b"password", body)
    def test_csrf_required(self):
        headers = self.login()
        headers.pop("X-CSRF-Token")
        self.assertEqual(self.request("/api/scan", {}, headers)[0], 403)
    def test_cross_origin_and_rebinding_rejected(self):
        headers = self.login()
        self.assertEqual(self.request("/api/scan", {}, dict(headers, Origin="http://evil.test"))[0], 403)
        self.assertEqual(self.request("/", headers={"Host":"evil.test:8088"})[0], 403)
    def test_session_logout_and_command_accepted(self):
        headers = self.login()
        self.assertEqual(self.request("/api/scan", {}, headers)[0], 202)
        self.assertEqual(self.request("/api/logout", {}, headers)[0], 200)
        self.assertEqual(self.request("/api/status", headers=headers)[0], 401)
    def test_bad_request_does_not_echo_secret(self):
        status, _, body = self.request("/api/connect", {"ssid":"Home", "password":"x"}, self.login())
        self.assertEqual(status, 400)
        self.assertNotIn(b'"password"', body)
    def test_rate_limit_and_session_expiry(self):
        now = [0]
        sessions = Sessions(hashlib.sha256(b"right").hexdigest(), lambda:now[0])
        for i in range(5):
            with self.assertRaises(ValueError):
                sessions.login("wrong")
        with self.assertRaises(ValueError):
            sessions.login("right")
        now[0] = 61
        token, csrf = sessions.login("right")
        self.assertEqual(sessions.lookup("mira_session="+token)[1], csrf)
        now[0] += 3601
        self.assertIsNone(sessions.lookup("mira_session="+token)[1])


if __name__ == "__main__":
    unittest.main()
