"""Single worker owns the radio. HTTP handlers only queue requests."""
import copy
import json
import os
from pathlib import Path
import queue
import threading
import time


def validate_profile(ssid, security, password):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 for c in ssid):
        raise ValueError("Tên Wi-Fi phải dài 1–32 byte, không có ký tự điều khiển.")
    if security != "psk2":
        raise ValueError("Bản này hỗ trợ Wi-Fi WPA2-Personal; chọn mạng có WPA2.")
    if not isinstance(password, str) or not (
        8 <= len(password) <= 63 and all(32 <= ord(c) <= 126 for c in password)
        or len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password)
    ):
        raise ValueError("Mật khẩu cần 8–63 ký tự ASCII hoặc 64 ký tự hex.")
    return dict(ssid=ssid, security=security, password=password)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    if os.name == "posix":
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class Profiles:
    def __init__(self, path):
        self.path = Path(path)
        self.items = []
        errors = []
        for candidate in (self.path, Path(str(self.path) + ".bak")):
            if not candidate.exists():
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                if not isinstance(raw, list) or len(raw) > 16:
                    raise ValueError("Invalid saved Wi-Fi profiles")
                items = []
                for item in raw:
                    profile = validate_profile(item["ssid"], item["security"], item["password"])
                    profile["last_success"] = float(item.get("last_success", 0))
                    if any(p["ssid"] == profile["ssid"] for p in items):
                        raise ValueError("Duplicate Wi-Fi profile")
                    items.append(profile)
                self.items = items
                break
            except (OSError, ValueError, KeyError, TypeError):
                errors.append(candidate)
        else:
            if errors:
                raise ValueError("Saved Wi-Fi profiles and backup unreadable")

    def find(self, ssid):
        return next((dict(p) for p in self.items if p["ssid"] == ssid), None)

    def save(self, profile, now):
        candidate = [dict(p) for p in self.items if p["ssid"] != profile["ssid"]]
        if len(candidate) >= 16:
            raise ValueError("Đã lưu 16 mạng. Hãy quên một mạng trước.")
        candidate.append(dict(profile, last_success=now))
        atomic_json(Path(str(self.path) + ".bak"), candidate)
        atomic_json(self.path, candidate)
        self.items = candidate

    def forget(self, ssid):
        candidate = [p for p in self.items if p["ssid"] != ssid]
        atomic_json(Path(str(self.path) + ".bak"), candidate)
        atomic_json(self.path, candidate)
        self.items = candidate

    def public(self):
        return [dict(ssid=p["ssid"], security=p["security"]) for p in
                sorted(self.items, key=lambda p: p["last_success"], reverse=True)]


def normalize_scan(raw):
    best = {}
    for item in raw:
        ssid = item.get("ssid")
        if not isinstance(ssid, str) or not ssid or len(ssid.encode("utf-8")) > 32:
            continue
        security = item.get("encryption", {})
        supported = 2 in security.get("wpa", []) and "psk" in security.get("authentication", [])
        row = dict(ssid=ssid, signal=int(item.get("signal", -100)),
                   channel=int(item.get("channel", 0)), security="psk2" if supported else "unsupported",
                   supported=supported)
        # Do not merge WPA2 and incompatible/unsafe networks sharing an SSID.
        key = (ssid, row["security"])
        if key not in best or row["signal"] > best[key]["signal"]:
            best[key] = row
    return sorted(best.values(), key=lambda p: p["signal"], reverse=True)[:64]


class Manager:
    def __init__(self, adapter, profiles, setup_ssid, clock=time.monotonic, wall=time.time):
        self.adapter, self.profiles, self.clock, self.wall = adapter, profiles, clock, wall
        self.inbox = queue.Queue(maxsize=8)
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.scan_due = 0
        self.scan_requested = False
        self.auto_after = clock() + 15
        self.failed_until = {}
        self.pending = None
        self.connected = None
        self.lost_since = None
        self.state = dict(state="setup", ssid=None, ip=None, message="Chọn Wi-Fi để kết nối.",
                          setup_ssid=setup_ssid, setup_url="http://192.168.12.1:8088",
                          networks=[], saved=profiles.public(), scanning=False)
        self.public_state = copy.deepcopy(self.state)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.public_state)

    def publish(self):
        self.state["saved"] = self.profiles.public()
        for row in self.state["networks"]:
            row["saved"] = self.profiles.find(row["ssid"]) is not None
        with self.lock:
            self.public_state = copy.deepcopy(self.state)

    def submit(self, action, data):
        if action not in ("scan", "connect", "forget"):
            raise ValueError("Lệnh không hợp lệ.")
        if not isinstance(data, dict):
            raise ValueError("Dữ liệu không hợp lệ.")
        # Validate lengths before bounded enqueue; credentials never enter public state.
        if action != "scan":
            ssid = data.get("ssid")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
                raise ValueError("Tên Wi-Fi không hợp lệ.")
        if action == "connect" and data.get("password") is not None:
            validate_profile(data["ssid"], data.get("security", "psk2"), data["password"])
        try:
            self.inbox.put_nowait((action, dict(data)))
        except queue.Full:
            raise ValueError("Thiết bị đang bận, vui lòng thử lại.")

    def fallback(self, message):
        self.adapter.setup_only()
        self.pending = self.connected = self.lost_since = None
        self.state.update(state="setup", ssid=None, ip=None, message=message)

    def start_connect(self, profile, channel):
        self.adapter.connect(profile, channel)
        self.pending = (profile, self.clock() + 50)
        self.connected = None
        self.state.update(state="connecting", ssid=profile["ssid"], ip=None,
                          message="Đang kết nối, tối đa 50 giây. Wi-Fi cài đặt có thể ngắt tạm thời.")

    def manual(self, action, data, now):
        if action == "scan":
            self.scan_due = min(self.scan_due, now)
            self.scan_requested = True
        elif action == "forget":
            ssid = data["ssid"]
            self.profiles.forget(ssid)
            self.failed_until.pop(ssid, None)
            if self.state["ssid"] == ssid:
                self.fallback("Đã quên mạng. Hãy chọn Wi-Fi khác.")
                self.auto_after = now + 15
        else:
            if self.pending:
                raise ValueError("Đang thử kết nối. Vui lòng chờ kết quả.")
            profile = (validate_profile(data["ssid"], data.get("security", "psk2"), data["password"])
                       if data.get("password") is not None else self.profiles.find(data["ssid"]))
            if profile is None:
                raise ValueError("Mạng chưa lưu mật khẩu.")
            if not self.profiles.find(profile["ssid"]) and len(self.profiles.items) >= 16:
                raise ValueError("Đã lưu 16 mạng. Hãy quên một mạng trước.")
            row = next((r for r in self.state["networks"]
                        if r["ssid"] == profile["ssid"] and r["supported"]), None)
            if row is None:
                raise ValueError("Chưa thấy Wi-Fi WPA2 này. Hãy dò lại mạng.")
            self.failed_until.pop(profile["ssid"], None)
            self.start_connect(profile, row["channel"])

    def step(self):
        now = self.clock()
        try:
            action, data = self.inbox.get_nowait()
        except queue.Empty:
            pass
        else:
            try:
                self.manual(action, data, now)
            except ValueError as exc:
                self.state["message"] = str(exc)
        if self.pending:
            profile, deadline = self.pending
            ip = self.adapter.connected_ip(profile["ssid"])
            if ip:
                # Save only after association AND DHCP, without leaking candidate passwords.
                self.profiles.save(profile, self.wall())
                self.connected, self.pending = profile["ssid"], None
                self.state.update(state="connected", ssid=self.connected, ip=ip,
                                  message="Đã kết nối Wi-Fi. Bạn có thể SSH qua IP bên dưới.")
            elif now >= deadline:
                self.failed_until[profile["ssid"]] = now + 300
                self.fallback("Không kết nối được. Kiểm tra mật khẩu, tín hiệu và DHCP của router.")
                self.auto_after = now + 15
        elif self.connected:
            ip = self.adapter.connected_ip(self.connected)
            if ip:
                self.lost_since = None
                self.state.update(state="connected", ip=ip, message="Đã kết nối Wi-Fi. Bạn có thể SSH qua IP bên dưới.")
            else:
                self.lost_since = self.lost_since if self.lost_since is not None else now
                self.state.update(state="reconnecting", ip=None, message="Mất Wi-Fi, đang thử kết nối lại.")
                if now - self.lost_since >= 20:
                    self.failed_until[self.connected] = now + 30
                    self.fallback("Mất Wi-Fi. Đang tìm mạng đã lưu.")
                    self.scan_due = 0
        if now >= self.scan_due and not self.pending and (not self.connected or self.scan_requested):
            self.scan_requested = False
            self.state["scanning"] = True
            self.publish()
            try:
                self.state["networks"] = normalize_scan(self.adapter.scan())
            except RuntimeError:
                self.state["message"] = "Chưa dò được mạng. Thiết bị sẽ thử lại."
            finally:
                self.state["scanning"] = False
                self.scan_due = now + 45
        if not self.pending and not self.connected and now >= self.auto_after:
            visible = {r["ssid"]: r for r in self.state["networks"] if r["supported"]}
            candidates = [p for p in self.profiles.items if p["ssid"] in visible
                          and self.failed_until.get(p["ssid"], 0) <= now]
            candidates.sort(key=lambda p: (p["last_success"], visible[p["ssid"]]["signal"]), reverse=True)
            if candidates:
                chosen = candidates[0]
                self.start_connect(chosen, visible[chosen["ssid"]]["channel"])
        self.publish()

    def run(self, health_path=None):
        health_due = 0
        while not self.halt.is_set():
            try:
                self.step()
            except Exception:
                # Do not log subprocess diagnostics/config, which may contain credentials.
                self.state["message"] = "Lỗi thao tác Wi-Fi. Đang khôi phục mạng cài đặt."
                try:
                    self.fallback(self.state["message"])
                except Exception:
                    self.state["state"] = "error"
                self.auto_after = self.clock() + 60
                self.publish()
            if health_path and self.clock() >= health_due:
                atomic_json(health_path, dict(pid=os.getpid(), monotonic=self.clock(),
                                             state=self.state["state"]))
                health_due = self.clock() + 10
            self.halt.wait(2)
