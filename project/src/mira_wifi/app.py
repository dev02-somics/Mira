"""Local Wi-Fi portal; no external packages or Internet assets."""
import argparse
import hashlib
import hmac
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import time
from urllib.parse import urlsplit

from .core import Manager, Profiles
from .mqtt import MqttSetup
from .openwrt import OpenWrt

STATIC = Path(__file__).with_name("static")


class Sessions:
    def __init__(self, password_hash, clock=time.monotonic):
        self.password_hash, self.clock = password_hash, clock
        self.sessions, self.failures = {}, []
        self.lock = threading.Lock()

    def login(self, password):
        with self.lock:
            now = self.clock()
            self.failures = [t for t in self.failures if now - t < 60]
            if len(self.failures) >= 5:
                raise ValueError("Thử quá nhiều lần. Vui lòng đợi một phút.")
            if not isinstance(password, str) or not hmac.compare_digest(
                    hashlib.sha256(password.encode()).hexdigest(), self.password_hash):
                self.failures.append(now)
                raise ValueError("Mật khẩu quản lý chưa đúng.")
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            if len(self.sessions) >= 16:
                del self.sessions[next(iter(self.sessions))]
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self.sessions[token] = (csrf, now + 3600)
            return token, csrf

    def lookup(self, cookie):
        try:
            parsed = cookies.SimpleCookie()
            parsed.load(cookie or "")
            token = parsed["mira_session"].value
            with self.lock:
                csrf, expiry = self.sessions[token]
            return (token, csrf) if expiry > self.clock() else (None, None)
        except (KeyError, cookies.CookieError):
            return None, None

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, manager, sessions, mqtt=None):
        self.manager, self.sessions, self.mqtt = manager, sessions, mqtt
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "MIRA-WiFi"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *args):
        pass

    def reply(self, code, value, content_type="application/json; charset=utf-8", cookie=None):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def same_origin(self):
        host = self.headers.get("Host", "")
        try:
            target = urlsplit("http://" + host)
            # Accept the local address used for this request, plus the fixed mDNS name.
            if target.hostname not in (self.connection.getsockname()[0], "mira.local", "localhost"):
                return False
            if target.port != self.server.server_address[1] or target.path or target.username:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin == "http://" + host
        except ValueError:
            return False

    def do_GET(self):
        if not self.same_origin():
            self.reply(403, {"error": "Địa chỉ truy cập không hợp lệ."})
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/wifi.js": ("wifi.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path in assets:
            name, kind = assets[self.path]
            self.reply(200, (STATIC / name).read_bytes(), kind)
            return
        _, csrf = self.server.sessions.lookup(self.headers.get("Cookie"))
        if not csrf:
            self.reply(401, {"error": "Vui lòng đăng nhập."})
        elif self.path == "/api/session":
            self.reply(200, {"csrf": csrf})
        elif self.path == "/api/status":
            self.reply(200, self.server.manager.snapshot())
        elif self.path == "/api/mqtt" and self.server.mqtt:
            self.reply(200, self.server.mqtt.status())
        else:
            self.reply(404, {"error": "Không tìm thấy."})

    def do_POST(self):
        if not self.same_origin():
            self.reply(403, {"error": "Yêu cầu khác nguồn bị từ chối."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Yêu cầu quá lớn hoặc thiếu độ dài.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Cần dữ liệu JSON.")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Yêu cầu chưa đầy đủ.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Dữ liệu không hợp lệ.")
            if self.path == "/api/login":
                try:
                    token, csrf = self.server.sessions.login(data.get("password"))
                except ValueError as exc:
                    self.reply(400, {"error": str(exc)})
                    return
                self.reply(200, {"csrf": csrf}, cookie="mira_session=" + token +
                           "; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600")
                return
            token, csrf = self.server.sessions.lookup(self.headers.get("Cookie"))
            if not csrf:
                self.reply(401, {"error": "Phiên đã hết hạn. Vui lòng đăng nhập lại."})
                return
            if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), csrf):
                self.reply(403, {"error": "Phiên không hợp lệ."})
                return
            if self.path == "/api/logout":
                self.server.sessions.logout(token)
                self.reply(200, {}, cookie="mira_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
                return
            if self.path == "/api/mqtt/activate" and self.server.mqtt:
                try:
                    result = self.server.mqtt.activate(data.get("device_id"), data.get("code"))
                except ValueError as exc:
                    self.reply(400, {"error": str(exc)})
                    return
                self.reply(200, result)
                return
            action = {"/api/scan": "scan", "/api/connect": "connect", "/api/forget": "forget"}.get(self.path)
            if action is None:
                self.reply(404, {"error": "Không tìm thấy."})
                return
            try:
                self.server.manager.submit(action, data)
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
                return
            self.reply(202, {"accepted": True})
        except (ValueError, UnicodeError):
            # Only controlled validation messages are useful; malformed JSON can contain a password.
            self.reply(400, {"error": "Kiểm tra tên mạng và mật khẩu (8–63 ký tự), rồi thử lại."})
        except (OSError, TimeoutError):
            self.close_connection = True


def serve(manager, password_hash, host="0.0.0.0", port=8088, mqtt=None, health_path=None):
    server = Server((host, port), manager, Sessions(password_hash), mqtt)
    worker = threading.Thread(target=manager.run, args=(health_path,), daemon=True)
    worker.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        manager.halt.set()
        worker.join(timeout=35)
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/mira-wifi/settings.json")
    args = parser.parse_args()
    import fcntl
    os.umask(0o077)
    with open("/var/run/mira-wifi.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(Path(args.config).read_text())
        adapter = OpenWrt()
        # Always restore AP-first boot state, including after an interrupted attempt.
        adapter.setup_only()
        manager = Manager(adapter, Profiles("/etc/mira-wifi/profiles.json"), config["setup_ssid"])
        def stop(*unused):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            serve(manager, config["password_hash"], mqtt=MqttSetup(),
                  health_path="/tmp/mira-wifi-health.json")
        except KeyboardInterrupt:
            pass
        finally:
            adapter.setup_only()


if __name__ == "__main__":
    main()
