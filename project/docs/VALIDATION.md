# Kết quả kiểm chứng — 07/10/2026

Bản ứng dụng: MIRA Player 0.2.0. Workspace:
C:/Users/phong/Documents/project.

- 46/46 kiểm thử qua trên Python portable 3.13.12 / Windows.
- Cú pháp source được kiểm tra theo Python 3.9.
- HTTP thật trên localhost: tải đúng hash thành công; sai hash bị từ chối và dọn .part.
- Audio/MQTT/MPD trong test dùng mô phỏng, chưa kiểm chứng phần cứng hay broker thật.
- Demo: pause tại 18.5 giây → resume vẫn 18.5 giây, volume 40 → replay từ 0 → stop về idle.
- 75 file đối chiếu thành công với SD-MANIFEST.json có trong bản sao gốc.
  Một tài liệu phụ bị thiếu lúc copy đã được khôi phục từ bản trùng tại thư mục gốc,
  chỉ sau khi SHA-256 khớp đúng giá trị manifest.
- Gói cài có 14 file payload; manifest và checksum từng file được xác minh.
  Hai lần đóng gói từ cùng nội dung cho ra bytes giống nhau.
- Installer đã chạy trên chính release ở chế độ plan; chưa chạy --apply.
- Thử kết nối TCP SSH tới 192.168.11.102 trong phiên hoàn tất bị timeout.
  Chưa kiểm tra ACL broker, loa vật lý, reboot hoặc rollback thực tế trên bo.

Release: dist/mira-player-0.2.0.tar.gz

SHA-256:
```text
6eb2efc89a5eaccce823b70dbcd65a698c3c6369a6a640d612383faff1b1db70
```

Bước tiếp theo: xem DECISION.md / MQTT_CONTRACT.md / DEPLOYMENT.md,
kết nối lại bo và duyệt triển khai theo kế hoạch sao lưu/kiểm tra/rollback.

## Bổ sung Wi-Fi — cùng ngày 07/10/2026

SSH đã kết nối lại được. MIRA Wi-Fi 0.1.0 đã được cài và kiểm tra trên bo;
Player 0.2.0 chưa được triển khai. 70/70 kiểm thử toàn project qua.
Xem [kết quả và giới hạn kiểm chứng Wi-Fi](WIFI.md).

## Menu terminal Wi-Fi 0.1.1 — 07/10/2026

85/85 kiểm thử qua (46 Player + 39 Wi-Fi). Đã cài lệnh mira-wifi;
PC đọc được trạng thái thật qua IP LAN và IP Wi-Fi 192.168.1.52.
Xem [hướng dẫn và giới hạn kiểm thử terminal](WIFI_TERMINAL.md).

## English terminal menu 0.1.2 — 07/10/2026

39/39 Wi-Fi tests passed. English status output and the interactive menu were
verified over SSH on the device. SOMICS remains connected at 192.168.1.52;
saved profiles are unchanged. See [terminal release notes](WIFI_TERMINAL.md).

## Player 0.3.0 + Wi-Fi 0.2.0 trên bo — 07/10/2026

- 105/105 test trên PC (Python portable 3.13; cú pháp kiểm theo 3.9).
- Cài bằng `deploy/install.py --apply`: WM8960 nhận đủ control, MQTT_READY,
  INSTALLED_AND_READY. Service cũ `somics_mqtt` đã gỡ.
- Tài khoản `SOMICS-000001` (kích hoạt qua `/api/iot/activate`): subscribe
  `station/player/device/SOMICS-000001/command` thành công, web báo online.
- Upload WAV trên web → "Access token issued" → file `ready` (SHA-256 khớp) →
  MPD log `played "mira-cache/…wav"`.
- Menu Wi-Fi: trạng thái MQTT `connected`; mã kích hoạt sai → báo đúng lỗi, không restart player.
- Chưa kiểm: kích hoạt thành công qua menu (cần mã mới), lệnh play/pause/stop từ web
  (web chưa có nút), reboot bo.
