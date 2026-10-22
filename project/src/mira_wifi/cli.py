"""Numbered Wi-Fi menu over the existing controller's authenticated API."""
import argparse
import getpass
import http.cookiejar
import ipaddress
import json
from pathlib import Path
import sys
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import warnings

from .core import validate_profile


class MenuError(Exception):
    pass


def safe(value):
    """SSID and server messages must not send escape/control sequences to a terminal."""
    return "".join(c if not unicodedata.category(c).startswith("C") and c not in "\u2028\u2029"
                   else "?" for c in str(value))


MESSAGES_EN = {'Tên Wi-Fi phải dài 1–32 byte, không có ký tự điều khiển.': 'The Wi-Fi name must be 1-32 bytes with no control characters.', 'Bản này hỗ trợ Wi-Fi WPA2-Personal; chọn mạng có WPA2.': 'This version supports WPA2-Personal. Select a WPA2 network.', 'Mật khẩu cần 8–63 ký tự ASCII hoặc 64 ký tự hex.': 'The password must contain 8-63 ASCII characters or 64 hexadecimal characters.', 'Đã lưu 16 mạng. Hãy quên một mạng trước.': 'The limit is 16 saved networks. Forget a network first.', 'Chọn Wi-Fi để kết nối.': 'Select a Wi-Fi network to connect.', 'Lệnh không hợp lệ.': 'Invalid command.', 'Dữ liệu không hợp lệ.': 'Invalid request data.', 'Tên Wi-Fi không hợp lệ.': 'Invalid Wi-Fi name.', 'Thiết bị đang bận, vui lòng thử lại.': 'The device is busy. Please try again.', 'Đang kết nối, tối đa 50 giây. Wi-Fi cài đặt có thể ngắt tạm thời.': 'Connecting, up to 50 seconds. Setup Wi-Fi may disconnect temporarily.', 'Đã quên mạng. Hãy chọn Wi-Fi khác.': 'Network forgotten. Select another Wi-Fi network.', 'Đang thử kết nối. Vui lòng chờ kết quả.': 'A connection attempt is in progress. Please wait for the result.', 'Mạng chưa lưu mật khẩu.': 'No password is saved for this network.', 'Chưa thấy Wi-Fi WPA2 này. Hãy dò lại mạng.': 'This WPA2 network was not found. Scan again.', 'Đã kết nối Wi-Fi. Bạn có thể SSH qua IP bên dưới.': 'Wi-Fi connected. Use the IP address shown to connect over SSH.', 'Không kết nối được. Kiểm tra mật khẩu, tín hiệu và DHCP của router.': 'Connection failed. Check the password, signal and router DHCP settings.', 'Mất Wi-Fi, đang thử kết nối lại.': 'Wi-Fi disconnected. Reconnecting.', 'Mất Wi-Fi. Đang tìm mạng đã lưu.': 'Wi-Fi disconnected. Looking for saved networks.', 'Chưa dò được mạng. Thiết bị sẽ thử lại.': 'Could not scan for networks. The device will retry.', 'Lỗi thao tác Wi-Fi. Đang khôi phục mạng cài đặt.': 'Wi-Fi operation failed. Restoring the setup network.', 'Kênh Wi-Fi không được hỗ trợ.': 'This Wi-Fi channel is not supported.', 'Thử quá nhiều lần. Vui lòng đợi một phút.': 'Too many attempts. Please wait one minute.', 'Mật khẩu quản lý chưa đúng.': 'Incorrect management password.', 'Địa chỉ truy cập không hợp lệ.': 'Invalid access address.', 'Vui lòng đăng nhập.': 'Please log in.', 'Không tìm thấy.': 'Not found.', 'Yêu cầu khác nguồn bị từ chối.': 'Cross-origin request rejected.', 'Yêu cầu quá lớn hoặc thiếu độ dài.': 'Request too large or missing its length.', 'Cần dữ liệu JSON.': 'JSON data is required.', 'Yêu cầu chưa đầy đủ.': 'Incomplete request.', 'Phiên đã hết hạn. Vui lòng đăng nhập lại.': 'Session expired. Please log in again.', 'Phiên không hợp lệ.': 'Invalid session.', 'Kiểm tra tên mạng và mật khẩu (8–63 ký tự), rồi thử lại.': 'Check the network name and password (8-63 characters), then try again.'}


MESSAGES_EN.update({
    'Mã thiết bị không hợp lệ (vd SOMICS-000001).': 'Invalid device ID (e.g. SOMICS-000001).',
    'Mã kích hoạt không hợp lệ (vd ABCD-EFGH-JKLM).': 'Invalid activation code (e.g. ABCD-EFGH-JKLM).',
    'Đang kích hoạt, vui lòng chờ.': 'Activation already in progress. Please wait.',
    'Không chạy được chương trình MQTT. Kiểm tra mạng rồi thử lại.': 'Could not run the MQTT program. Check the network and try again.',
    'Mã thiết bị hoặc mã kích hoạt không đúng (hoặc đã bị thu hồi).': 'Wrong device ID or activation code (or the code was revoked).',
    'Mã kích hoạt đã được dùng. Xin mã mới trên web.': 'This activation code was already used. Get a new one on the web page.',
    'Máy chủ chưa cấp được tài khoản MQTT. Mã chưa bị dùng, thử lại sau.': 'The server could not create the MQTT account. The code was not used; try again later.',
    'Không gọi được máy chủ. Kiểm tra Internet rồi thử lại.': 'Cannot reach the server. Check the Internet connection and try again.',
})


def english_message(value):
    text = str(value)
    translated = MESSAGES_EN.get(text)
    if translated is not None:
        return translated
    if text.isascii():
        return safe(text)
    return "The device reported an unexpected message. Refresh the status or try again."


def endpoint(value):
    try:
        url = urlsplit(value)
        if url.scheme != "http" or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError()
        if not url.hostname or not url.port:
            raise ValueError()
        if url.hostname not in ("localhost", "mira.local"):
            address = ipaddress.ip_address(url.hostname)
            allowed = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            if not any(address in ipaddress.ip_network(net) for net in allowed):
                raise ValueError()
        return value.rstrip("/")
    except ValueError:
        raise MenuError("Use a local HTTP URL with a port, for example http://192.168.11.102:8088.") from None


def access_password(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
        if len(text) > 4096:
            raise ValueError()
        prefix = "Mat khau Wi-Fi va trang quan ly: "
        password = next(line[len(prefix):] for line in text.splitlines() if line.startswith(prefix))
        if not password or len(password) > 128:
            raise ValueError()
        return password
    except (OSError, ValueError, StopIteration):
        raise MenuError("Cannot read the access file. On the device, run as root; "
                        "on the PC, use --access-file config/mira-wifi-access.local.txt.") from None


def hidden_password(prompt):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except getpass.GetPassWarning:
        raise MenuError("This terminal cannot hide password input. Open an interactive SSH session or use ssh -t.") from None


class Portal:
    def __init__(self, url, access_file):
        self.url, self.access_file = endpoint(url), access_file
        self.csrf = ""
        # Never send local credentials through an environment-configured HTTP proxy.
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, data=None, timeout=5):
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
                          headers={"Content-Type": "application/json", "X-CSRF-Token": self.csrf})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                result = json.loads(response.read(65536))
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except HTTPError as exc:
            try:
                message = json.loads(exc.read(4096)).get("error", "Request rejected.")
            except (ValueError, AttributeError):
                message = "Request rejected."
            raise MenuError(english_message(message)) from None
        except (URLError, OSError, ValueError):
            raise MenuError("Cannot reach the Wi-Fi service at " + self.url +
                            ". Check the device connection and the mira_wifi service.") from None

    def login(self):
        self.csrf = self.request("/api/login", {"password": access_password(self.access_file)})["csrf"]

    def status(self):
        return self.request("/api/status")

    def send(self, action, data=None):
        return self.request("/api/" + action, data or {})

    def mqtt_status(self):
        return self.request("/api/mqtt")

    def mqtt_activate(self, device_id, code):
        # The device calls the SOMICS backend and restarts the player: allow 90 s.
        return self.request("/api/mqtt/activate", {"device_id": device_id, "code": code}, timeout=90)

    def close(self):
        if self.csrf:
            try:
                self.send("logout")
            except MenuError:
                pass
            self.csrf = ""


LABELS = {"setup": "Not connected", "connecting": "Connecting", "connected": "Connected",
          "reconnecting": "Reconnecting", "error": "Needs attention"}


class Menu:
    def __init__(self, client, read=input, secret=hidden_password, write=print,
                 clock=time.monotonic, sleep=time.sleep):
        self.client, self.read, self.secret, self.write = client, read, secret, write
        self.clock, self.sleep = clock, sleep

    def summary(self, state):
        self.write("\n=== MIRA WI-FI ===")
        self.write("Status: " + safe(LABELS.get(state.get("state"), state.get("state", "?"))))
        if state.get("ssid"):
            self.write("Network: " + safe(state["ssid"]))
        if state.get("ip"):
            self.write("SSH: ssh root@" + safe(state["ip"]))
            self.write("Web page: http://" + safe(state["ip"]) + ":8088")
        self.write(english_message(state.get("message", "")))
        self.write("Setup Wi-Fi: " + safe(state.get("setup_ssid", "")))
        self.write("Setup page: " + safe(state.get("setup_url", "")))

    def networks(self, state):
        rows = sorted(state.get("networks", []), key=lambda p: (not p.get("saved", False), -p["signal"]))
        self.write("\n  #  Wi-Fi network                      Signal     Status")
        for number, row in enumerate(rows, 1):
            label = "Unsupported" if not row.get("supported") else (
                "In use" if state.get("state") == "connected" and state.get("ssid") == row["ssid"]
                else "Saved" if row.get("saved") else "Password required")
            name = safe(row["ssid"])
            if len(name) > 34:
                name = name[:31] + "..."
            self.write("%3d  %-34s %4d dBm   %s" % (number, name, row["signal"], label))
        if not rows:
            self.write("No networks found. Press R to scan again.")
        return rows

    def scan(self):
        self.client.send("scan")
        self.write("Scan requested...")
        deadline = self.clock() + 12
        saw_scanning = False
        start = self.clock()
        while self.clock() < deadline:
            self.sleep(1)
            state = self.client.status()
            saw_scanning = saw_scanning or state.get("scanning", False)
            if not state.get("scanning") and (saw_scanning or self.clock() - start >= 4):
                return state
        self.write("The device is still processing. The list below shows the latest available status.")
        return self.client.status()

    def watch(self, ssid, before):
        deadline, started, last = self.clock() + 90, False, None
        self.write("Request sent. Switching Wi-Fi may disconnect SSH; the device will continue processing.")
        try:
            while self.clock() < deadline:
                self.sleep(2)
                state = self.client.status()
                marker = (state.get("state"), state.get("ssid"), state.get("ip"), state.get("message"))
                if marker != last:
                    self.write("  " + safe(LABELS.get(state.get("state"), "?")) + ": " +
                               english_message(state.get("message", "")))
                    last = marker
                if state.get("state") == "connecting" and state.get("ssid") == ssid:
                    started = True
                if state.get("state") == "connected" and state.get("ssid") == ssid:
                    self.write("SSH: ssh root@" + safe(state.get("ip", "")))
                    return
                if state.get("state") not in ("connecting", "reconnecting"):
                    if started or state.get("message") != before.get("message"):
                        self.write("Check the current status in the menu; the requested connection is not confirmed.")
                        return
            self.write("Result not confirmed. Press Enter to refresh; the password will not be submitted again automatically.")
        except KeyboardInterrupt:
            self.write("\nStopped waiting. The device will continue processing the request.")

    def join(self, row, state):
        if not row.get("supported"):
            self.write("This version supports 2.4 GHz WPA2-Personal networks.")
            return
        if state.get("state") == "connecting":
            self.write("A connection attempt is in progress. Please wait.")
            return
        if state.get("state") == "connected" and state.get("ssid") == row["ssid"]:
            self.write("Already connected to this network.")
            return
        self.write("\nConnect to: " + safe(row["ssid"]))
        if row.get("saved"):
            self.write("A password is saved. Press Enter to reuse it, or enter a new password.")
        else:
            self.write("Password input is hidden. Leave it blank to go back.")
        password = self.secret("Wi-Fi password: ")
        if not password and not row.get("saved"):
            return
        if password:
            try:
                validate_profile(row["ssid"], "psk2", password)
            except ValueError as exc:
                raise MenuError(english_message(str(exc))) from None
        self.client.send("connect", {"ssid": row["ssid"], "security": "psk2", "password": password or None})
        password = None
        self.watch(row["ssid"], state)

    def saved(self):
        state = self.client.status()
        rows = state.get("saved", [])
        self.write("\nSAVED NETWORKS")
        for number, row in enumerate(rows, 1):
            self.write("%d. %s" % (number, safe(row["ssid"])))
        if not rows:
            self.write("No saved networks.")
            return
        answer = self.read("Enter a network number to forget, or press Enter to go back: ").strip()
        if not answer:
            return
        if not answer.isdecimal() or not 1 <= int(answer) <= len(rows):
            self.write("Invalid network number.")
            return
        ssid = rows[int(answer) - 1]["ssid"]
        self.write("Forget network " + safe(ssid) + ". Forgetting the active network may disconnect SSH.")
        if self.read("Type YES to confirm: ").strip().upper() == "YES":
            self.client.send("forget", {"ssid": ssid})
            self.write("Forget request sent.")

    def mqtt_line(self):
        try:
            info = self.client.mqtt_status()
        except MenuError:
            return "MQTT: status unavailable"
        if not info.get("installed"):
            return "MQTT: player not installed"
        if not info.get("activated"):
            return "MQTT: not activated (press M)"
        return "MQTT: activated as %s, link %s" % (safe(info.get("device_id")), safe(info.get("link")))

    def mqtt(self):
        self.write("\nMQTT ACTIVATION")
        self.write(self.mqtt_line())
        self.write("Enter the device ID and activation code from the SOMICS web page. Leave blank to go back.")
        device_id = self.read("Device ID (e.g. SOMICS-000001): ").strip().upper()
        if not device_id:
            return
        code = self.read("Activation code (e.g. ABCD-EFGH-JKLM): ").strip().upper()
        if not code:
            return
        self.write("Activating, up to 1 minute...")
        try:
            result = self.client.mqtt_activate(device_id, code)
        except MenuError as exc:
            # Server messages are Vietnamese; print them as-is (safe() strips control codes).
            self.write("Activation failed: " + safe(exc))
            return
        self.write(safe(result.get("message", "Activated.")))

    def run(self):
        while True:
            try:
                state = self.client.status()
                self.summary(state)
                self.write(self.mqtt_line())
                rows = self.networks(state)
                self.write("\nNumber: connect | R: scan | S: saved networks | M: MQTT | Enter: refresh | Q: quit")
                choice = self.read("Select: ").strip().lower()
                if choice == "q":
                    return
                if not choice:
                    continue
                if choice == "r":
                    self.scan()
                elif choice == "s":
                    self.saved()
                elif choice == "m":
                    self.mqtt()
                elif choice.isdecimal() and 1 <= int(choice) <= len(rows):
                    self.join(rows[int(choice) - 1], state)
                else:
                    self.write("Choose a listed number, R, S, M or Q.")
            except MenuError as exc:
                self.write("Error: " + safe(exc))
                if self.read("Enter to retry, Q to quit: ").strip().lower() == "q":
                    return


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="MIRA Wi-Fi menu for terminal/SSH.")
    parser.add_argument("--url", default="http://127.0.0.1:8088", help="Wi-Fi service URL")
    parser.add_argument("--access-file", default="/etc/mira-wifi/access.txt", help="Private access file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="Print status and exit")
    mode.add_argument("--scan", action="store_true", help="Scan, print networks and exit")
    args = parser.parse_args(argv)
    client = None
    try:
        client = Portal(args.url, args.access_file)
        client.login()
        menu = Menu(client)
        if args.status:
            menu.summary(client.status())
        elif args.scan:
            state = menu.scan()
            menu.summary(state)
            menu.networks(state)
        else:
            menu.run()
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nWi-Fi menu closed.")
        return 0
    except MenuError as exc:
        print("Error: " + safe(exc), file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
