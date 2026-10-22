# Triển khai MIRA Player

## Build trên PC

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\dev.ps1 build
```

Tạo `dist/mira-player-<ver>.tar.gz` + `.sha256` (chạy hết test trước, gói tái lập được).

## Cài/cập nhật lên bo

```sh
# Từ PC
scp dist/mira-player-0.3.0.tar.gz deploy/install.py root@<IP-bo>:/tmp/
# Trên bo (config: dùng /root/mira-player/config.json hiện có, hoặc tạo từ config/mira-player.example.json)
cp /root/mira-player/config.json /tmp/mira-config.json
python3 /tmp/install.py --archive /tmp/mira-player-0.3.0.tar.gz --sha256 <SHA256> --config /tmp/mira-config.json --apply
rm /tmp/mira-config.json
```

Installer: verify SHA-256 + manifest → kiểm tra WM8960 và kết nối MQTT → backup
lên `/mnt/mmcblk0p1/backups/mira-install-*` → cài `/root/mira-player` + init
`mira_player` → chờ service sẵn sàng; lỗi thì tự rollback. Thiếu `--apply` chỉ in kế hoạch.

Bo mới chưa có credential: cài với config mẫu (để trống username/password) sẽ không
qua bước kiểm tra MQTT. Kích hoạt trước bằng:

```sh
PYTHONPATH=/tmp/staging python3 -m mira_player.app --config <config> --activate <MÃ> --device-id <ID>
```

hoặc cài xong rồi dùng menu Wi-Fi → Kết nối MQTT.

## Cập nhật menu Wi-Fi

`tools/wifi.py build` tạo `dist/mira-wifi-<ver>.tar.gz`. Cập nhật nhanh chỉ code:
chép `src/mira_wifi/*` vào `/root/mira-wifi/mira_wifi/` rồi
`/etc/init.d/mira_wifi restart` (Wi-Fi ngắt vài giây rồi tự nối lại mạng đã lưu;
chạy restart tách khỏi phiên SSH, ví dụ bằng `start-stop-daemon -S -b`).

## Rollback

Sau khi cài thành công: `/etc/init.d/mira_player stop`, chép lại thư mục từ
`/mnt/mmcblk0p1/backups/mira-install-*/mira-player`, rồi `start`.
Không cần khôi phục firmware/thẻ nhớ để quay lại phiên bản ứng dụng.
