# MIRA IoT Player 0.3.0 + MIRA Wi-Fi 0.2.0

Hai dịch vụ Python chạy trên bo MIRA (OpenWrt, Python 3.9, paho 1.5.1):

- **mira_player**: nhận lệnh MQTT từ web SOMICS, tải file bằng access token,
  kiểm tra SHA-256 và phát qua MPD/WM8960. Có hàng đợi, cache, play/pause/resume/
  stop/replay/âm lượng.
- **mira_wifi**: trang `http://<IP-bo>:8088` và lệnh `mira-wifi` để chọn Wi-Fi và
  **kích hoạt MQTT** bằng 2 thông số: mã thiết bị + mã kích hoạt.

Đã kiểm chứng trên bo ngày 07/10/2026: kích hoạt → kết nối MQTT → web thấy online
→ upload file trên web → bo lấy token, tải, phát ra loa. Xem [VALIDATION](docs/VALIDATION.md).

## Luồng với web SOMICS

1. Web tạo thiết bị + mã kích hoạt (mỗi mã gắn với 1 thiết bị, dùng 1 lần).
2. Trên bo: menu Wi-Fi → **Kết nối MQTT** → nhập mã thiết bị + mã kích hoạt.
   Bo gọi `POST /api/iot/activate`, lưu username/password MQTT vào config, khởi động lại player.
3. Player subscribe `station/player/device/<ID>/command`, gửi trạng thái lên `.../status`.
4. Web upload file → lệnh `{type: file, action: download, fileId…}` → bo đổi MQTT
   password lấy token (`/api/iot/token`, tự refresh) → tải `/api/files/<id>` → phát.

Chi tiết topic/payload: [MQTT_CONTRACT](docs/MQTT_CONTRACT.md).

## Cấu trúc

```text
src/mira_player/      dịch vụ phát nhạc
  app.py              khởi động, --activate, --check-connection, --check-hardware
  config.py           đọc/kiểm tra config.json
  backend.py          mọi HTTP tới web: activate, token, chuyển lệnh web → nội bộ
  broker.py           MQTT: topic thiết bị, gửi state/result/event lên status
  commands.py         lệnh điều khiển (play/pause/stop/volume…)
  controller.py       vòng điều phối: hàng đợi, tải, cache, phát
  protocol.py         kiểm tra file event, tải có xác minh, ghi file an toàn
  audio.py, legacy_audio.py   MPD/ALSA/GPIO WM8960
  store.py            lưu trạng thái trên thẻ SD
src/mira_wifi/        Wi-Fi + menu kích hoạt MQTT
  app.py              web server cổng 8088
  cli.py              menu terminal (lệnh mira-wifi)
  core.py, openwrt.py quản lý Wi-Fi
  mqtt.py             kích hoạt MQTT qua mira_player
  static/             giao diện web
tests/                kiểm thử (chạy trên PC, mô phỏng phần cứng/broker)
deploy/               installer, init script procd, start script
config/               config mẫu; *.local.* là file riêng, không đưa vào Git
tools/                dev.py (test/build), wifi.py, menu Wi-Fi từ PC
docs/                 giao thức, triển khai, kiểm chứng, Wi-Fi
demo-somics/          source backend/web SOMICS (repo riêng, để tham khảo API)
```

**Thêm tính năng khi web có API mới:** gọi HTTP mới → thêm hàm trong
`backend.py`; lệnh MQTT mới → thêm action trong `commands.py` và xử lý trong
`controller.py`; dạng lệnh web mới → thêm nhánh trong `backend.classify()`.
Mỗi phần có test tương ứng trong `tests/`.

## Lệnh trên PC

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\dev.ps1 test    # toàn bộ test
powershell -ExecutionPolicy Bypass -File .\tools\dev.ps1 build   # dist/mira-player-<ver>.tar.gz
.\.tools\python\python.exe tools\wifi.py build                    # dist/mira-wifi-<ver>.tar.gz
```

Python portable có sẵn ở `.tools/python/python.exe`. `dist/` chỉ giữ bản mới nhất.

## Trên bo

| Thứ | Vị trí |
| --- | --- |
| Player | `/root/mira-player`, service `/etc/init.d/mira_player` |
| Config + credential MQTT | `/root/mira-player/config.json` (chmod 600) |
| Dữ liệu/cache | `/mnt/mmcblk0p1/mira-player`, `/mnt/mmcblk0p1/mgwp/audio/mira-cache` |
| Wi-Fi/menu | `/root/mira-wifi`, `/etc/mira-wifi`, service `mira_wifi`, lệnh `mira-wifi` |
| Log | `logread \| grep -E 'sh\[\|mira'` |
| Trạng thái nhanh | `cat /tmp/mira-player-status.json` |

Triển khai/rollback: [DEPLOYMENT](docs/DEPLOYMENT.md). Wi-Fi: [WIFI](docs/WIFI.md),
[menu terminal](docs/WIFI_TERMINAL.md).
