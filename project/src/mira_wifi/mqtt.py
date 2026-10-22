"""MQTT activation for MIRA Player: device ID + activation code -> credential.

mira_player owns the backend call and the config write
(python3 -m mira_player.app --activate); this module only validates input,
runs it, restarts the player service and reports status without the password.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

DEVICE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,63}$")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,63}$")
STALE_SECONDS = 30


class MqttSetup:
    def __init__(self, home="/root/mira-player", service="/etc/init.d/mira_player",
                 status="/tmp/mira-player-status.json", run=subprocess.run, clock=time.time):
        self.home, self.config = Path(home), Path(home) / "config.json"
        self.service, self.status_file = service, Path(status)
        self.run, self.clock = run, clock
        self.lock = threading.Lock()

    def link_state(self):
        """connected | connecting | stopped, from the player's local status file."""
        try:
            data = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "stopped"
        if self.clock() - data.get("timestamp", 0) > STALE_SECONDS:
            return "stopped"
        return "connected" if data.get("mqtt_ready") else "connecting"

    def status(self):
        try:
            config = json.loads(self.config.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {"installed": False, "activated": False, "device_id": None,
                    "activated_at": None, "link": "stopped"}
        return {"installed": True, "activated": bool(config.get("activated_at")),
                "device_id": config.get("device_id"), "activated_at": config.get("activated_at"),
                "link": self.link_state()}

    def activate(self, device_id, code):
        device_id = device_id.strip().upper() if isinstance(device_id, str) else ""
        code = code.strip().upper() if isinstance(code, str) else ""
        if not DEVICE_RE.match(device_id):
            raise ValueError("Mã thiết bị không hợp lệ (vd SOMICS-000001).")
        if not CODE_RE.match(code):
            raise ValueError("Mã kích hoạt không hợp lệ (vd ABCD-EFGH-JKLM).")
        if not self.lock.acquire(blocking=False):
            raise ValueError("Đang kích hoạt, vui lòng chờ.")
        try:
            try:
                result = self.run(["/usr/bin/python3", "-m", "mira_player.app", "--config", str(self.config),
                                   "--activate", code, "--device-id", device_id],
                                  env=dict(os.environ, PYTHONPATH=str(self.home)),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Không chạy được chương trình MQTT. Kiểm tra mạng rồi thử lại.")
            if result.returncode != 0:
                raise ValueError(self.error_message(result.stderr))
            # The old credential is revoked on the broker; restart with the new one.
            self.run([self.service, "restart"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, timeout=30)
            return {"message": "Kích hoạt thành công " + device_id + ". Thiết bị đang kết nối MQTT."}
        finally:
            self.lock.release()

    @staticmethod
    def error_message(stderr):
        text = (stderr or b"").decode("utf-8", "replace")
        line = next((l for l in reversed(text.splitlines()) if " ERROR " in l), "")
        detail = line.split(" ERROR ", 1)[1].strip() if line else ""
        if "HTTP 403" in detail:
            return "Mã thiết bị hoặc mã kích hoạt không đúng (hoặc đã bị thu hồi)."
        if "HTTP 409" in detail:
            return "Mã kích hoạt đã được dùng. Xin mã mới trên web."
        if "HTTP 502" in detail:
            return "Máy chủ chưa cấp được tài khoản MQTT. Mã chưa bị dùng, thử lại sau."
        if "urlopen error" in detail or "timed out" in detail:
            return "Không gọi được máy chủ. Kiểm tra Internet rồi thử lại."
        return "Kích hoạt thất bại" + (": " + detail[:200] if detail else ".")
