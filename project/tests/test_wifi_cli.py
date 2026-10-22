import getpass
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import warnings

from mira_wifi.app import Server, Sessions
from mira_wifi.cli import Menu, MenuError, Portal, access_password, endpoint, hidden_password, main, safe
from mira_wifi.core import Manager, Profiles
from test_wifi import Radio


class FakePortal:
    def __init__(self, saved=False, supported=True):
        self.calls = []
        self.state = dict(state="setup", ssid=None, ip=None, message="Chọn mạng.", setup_ssid="MIRA-Test",
                          setup_url="http://192.168.12.1:8088",
                          networks=[dict(ssid="Nhà", supported=supported, saved=saved, signal=-50)],
                          saved=[dict(ssid="Nhà")] if saved else [])
    def status(self):
        return self.state
    def mqtt_status(self):
        return dict(installed=True, activated=False, device_id=None, activated_at=None, link="unknown")
    def mqtt_activate(self, device_id, code):
        self.calls.append(("mqtt", (device_id, code)))
        return {"message": "Activated " + device_id}
    def send(self, action, data=None):
        self.calls.append((action, data))
        if action == "connect":
            self.state = dict(self.state, state="connected", ssid=data["ssid"], ip="192.168.1.9", message="Đã kết nối.")
        elif action == "forget":
            self.state = dict(self.state, saved=[])
        return {"accepted":True}


class MenuTests(unittest.TestCase):
    def menu(self, portal, answers, password="test-secret"):
        output, prompts = [], []
        replies = iter(answers)
        def secret(prompt):
            prompts.append(prompt)
            return password
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        menu = Menu(portal, read=lambda prompt:next(replies), secret=secret, write=output.append,
                    clock=lambda:clock[0], sleep=sleep)
        return menu, output, prompts

    def test_menu_connects_new_network_without_echoing_secret(self):
        portal = FakePortal()
        menu, output, prompts = self.menu(portal, ["1", "q"])
        menu.run()
        self.assertEqual(portal.calls, [("connect", {"ssid":"Nhà", "security":"psk2", "password":"test-secret"})])
        self.assertIn("SSH: ssh root@192.168.1.9", output)
        self.assertNotIn("test-secret", "\n".join(output+prompts))

    def test_mqtt_menu_sends_device_id_and_code(self):
        portal = FakePortal()
        menu, output, _ = self.menu(portal, ["m", "somics-000001", " abcd-efgh-jklm ", "q"])
        menu.run()
        self.assertEqual(portal.calls, [("mqtt", ("SOMICS-000001", "ABCD-EFGH-JKLM"))])
        self.assertIn("Activated SOMICS-000001", output)
        self.assertIn("MQTT: not activated (press M)", output)

    def test_mqtt_menu_blank_device_id_goes_back(self):
        portal = FakePortal()
        menu, _, _ = self.menu(portal, ["m", "", "q"])
        menu.run()
        self.assertEqual(portal.calls, [])

    def test_empty_input_reuses_saved_password(self):
        portal = FakePortal(saved=True)
        menu, output, prompts = self.menu(portal, ["1","q"], "")
        menu.run()
        self.assertIsNone(portal.calls[0][1]["password"])

    def test_empty_new_password_cancels_without_request(self):
        portal = FakePortal()
        menu, _, _ = self.menu(portal, ["1","q"], "")
        menu.run()
        self.assertEqual(portal.calls, [])

    def test_unsupported_wifi_never_prompts_password(self):
        portal = FakePortal(supported=False)
        menu, _, prompts = self.menu(portal, ["1","q"])
        menu.run()
        self.assertEqual(prompts, [])
        self.assertEqual(portal.calls, [])

    def test_forget_requires_explicit_confirmation(self):
        portal = FakePortal(saved=True)
        menu, _, _ = self.menu(portal, ["s","1","","q"])
        menu.run()
        self.assertEqual(portal.calls, [])
        menu, _, _ = self.menu(portal, ["s","1","YES","q"])
        menu.run()
        self.assertEqual(portal.calls, [("forget", {"ssid":"Nhà"})])

    def test_invalid_choice_then_scan(self):
        portal = FakePortal()
        menu, _, _ = self.menu(portal, ["100","r","q"])
        menu.run()
        self.assertEqual(portal.calls, [("scan", None)])

    def test_invalid_password_not_sent(self):
        portal = FakePortal()
        menu, output, _ = self.menu(portal, ["1","q"], "short")
        menu.run()
        self.assertEqual(portal.calls, [])
        self.assertNotIn("short", "\n".join(output))

    def test_terminal_controls_and_bidi_in_ssid_removed(self):
        malicious = "\x1b[2Jname\r\n\x9b31m\u202eevil\u2028end"
        portal = FakePortal()
        portal.state["networks"][0]["ssid"] = malicious
        menu, output, _ = self.menu(portal, ["q"])
        menu.run()
        text = "\n".join(output)
        for c in ("\x1b", "\r", "\x9b", "\u202e", "\u2028"):
            self.assertNotIn(c, text)
        self.assertIn("Nhà", safe("Nhà"))

    def test_hidden_input_refuses_echo_fallback(self):
        def fallback(prompt):
            warnings.warn("cannot hide password", getpass.GetPassWarning)
            self.fail("Must stop before an echoed read")
        with patch("mira_wifi.cli.getpass.getpass", fallback), self.assertRaises(MenuError):
            hidden_password("Password: ")

    def test_wait_failure_shows_current_state_without_resubmitting(self):
        portal = FakePortal()
        initial = dict(portal.state)
        states = iter([dict(initial,state="connecting",ssid="Nhà",message="Đang kết nối."),
                       dict(initial,message="Không kết nối được.")])
        portal.status = lambda:next(states)
        menu, output, _ = self.menu(portal, [])
        menu.watch("Nhà", initial)
        self.assertEqual(portal.calls, [])
        self.assertTrue(any("not confirmed" in text for text in output))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.access = self.root / "access.txt"
        self.access.write_text("Wi-Fi cai dat: MIRA-Test\nMat khau Wi-Fi va trang quan ly: portal-test\n", encoding="utf-8")
        self.manager = Manager(Radio(), Profiles(self.root / "profiles.json"), "MIRA-Test")
        self.manager.step()
        self.server = Server(("127.0.0.1", 0), self.manager, Sessions(hashlib.sha256(b"portal-test").hexdigest()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_real_http_login_csrf_command_and_logout(self):
        client = Portal(self.url, self.access)
        client.login()
        self.assertEqual(client.status()["state"], "setup")
        client.send("connect", {"ssid":"Home","security":"psk2","password":"secret123"})
        self.manager.step()
        self.assertEqual(client.status()["state"], "connecting")
        client.close()
        with self.assertRaises(MenuError):
            client.status()

    def test_status_command_is_read_only_and_does_not_leak_credentials(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            code = main(["--url", self.url, "--access-file", str(self.access), "--status"])
        self.assertEqual(code, 0)
        self.assertIn("MIRA-Test", output.getvalue())
        self.assertIn("Status: Not connected", output.getvalue())
        self.assertIn("Select a Wi-Fi network to connect.", output.getvalue())
        self.assertNotIn("portal-test", output.getvalue())
        self.assertTrue(self.manager.inbox.empty())

    def test_missing_credentials_is_clear_without_traceback(self):
        output = io.StringIO()
        with patch("sys.stderr", output):
            code = main(["--url", self.url, "--access-file", str(self.root/"missing"), "--status"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", output.getvalue())

    def test_server_validation_error_is_readable(self):
        client = Portal(self.url, self.access)
        client.login()
        try:
            with self.assertRaises(MenuError) as error:
                client.send("connect", {"ssid":"Home", "password":"short"})
            self.assertIn("8-63", str(error.exception))
            self.assertNotIn("short", str(error.exception))
        finally:
            client.close()

    def test_url_rejects_remote_hosts_and_embedded_credentials(self):
        for url in ("http://example.com:8088", "http://8.8.8.8:8088", "http://user:pass@192.168.1.1:8088",
                    "http://192.168.1.1:8088/else", "http://192.168.1.1:8088?x=1"):
            with self.subTest(url=url), self.assertRaises(MenuError):
                endpoint(url)
        self.assertEqual(endpoint(self.url+"/"), self.url)
        self.assertEqual(access_password(self.access), "portal-test")


if __name__ == "__main__":
    unittest.main()
