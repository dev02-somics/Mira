# Menu Wi-Fi trong terminal — MIRA Wi-Fi 0.1.2

Đã cài lệnh `mira-wifi` lên bo ngày 07/10/2026. Menu dùng chung danh sách mạng,
mật khẩu và bộ điều khiển với trang Wi-Fi. Không cần mở trình duyệt.

## Trên bo qua SSH

```sh
mira-wifi
```

Hoặc mở menu ngay khi SSH từ PC, dùng IP hiện tại của bo:

```sh
ssh -t root@192.168.1.52 mira-wifi
```

IP 192.168.1.52 là địa chỉ quan sát lúc kiểm tra trên mạng SOMICS. DHCP có thể đổi IP.
Qua LAN vẫn dùng 192.168.11.102; khi nối trực tiếp MIRA-Setup-5D81 dùng 192.168.12.1.

| Phím | Chức năng |
| --- | --- |
| Số thứ tự mạng | Chọn Wi-Fi để kết nối |
| Enter ở bước mật khẩu đã lưu | Dùng lại mật khẩu cũ |
| R | Yêu cầu dò mạng và cập nhật danh sách |
| S | Xem mạng đã lưu; chọn mạng để quên, cần gõ YES xác nhận |
| Enter ở menu chính | Cập nhật trạng thái và IP |
| Q | Thoát menu |

Mật khẩu mới được nhập ẩn, không hiện ký tự khi gõ. Để trống mật khẩu mạng chưa lưu
để quay lại. Chỉ lưu sau khi kết nối và nhận IP thành công.
Mạng đang sử dụng hiện “In use”; mạng đã lưu được ưu tiên trong danh sách.

Đổi Wi-Fi đang dùng cho SSH có thể ngắt phiên SSH. Yêu cầu đã gửi vẫn được dịch vụ
trên bo xử lý. Có thể nối lại qua Wi-Fi cài đặt sau khi quá trình thử kết nối kết thúc.

Lệnh chỉ đọc trạng thái hoặc dò rồi thoát:

```sh
mira-wifi --status
mira-wifi --scan
```

Đăng nhập SSH bằng root là đủ; menu tự đọc file đăng nhập nội bộ quyền 0600.
Không truyền mật khẩu vào dòng lệnh.

## Mở menu ngay trên PC

Trong PowerShell tại project:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\wifi-menu.ps1 -DeviceUrl http://192.168.1.52:8088
```

Không truyền DeviceUrl thì dùng địa chỉ LAN 192.168.11.102:8088.
Menu trên PC đọc file riêng config/mira-wifi-access.local.txt đã loại khỏi Git.
Nó gọi API cục bộ trên bo; không phụ thuộc một cửa sổ trình duyệt.
PC vẫn cần có đường mạng tới bo. Mở terminal không thay cho bước nối Wi-Fi hoặc LAN.

## Kiểm chứng và triển khai

- 85/85 bài kiểm thử toàn project qua, gồm 39 bài Wi-Fi (15 bài mới cho terminal).
- HTTP thật localhost: đăng nhập, CSRF, gửi lệnh và đăng xuất.
- Menu: chọn mạng, dùng mật khẩu đã lưu, nhập ẩn, hủy, quên có xác nhận,
  xử lý lỗi và loại ký tự điều khiển terminal khỏi tên Wi-Fi.
- Đã đọc trạng thái thật từ PC qua cả LAN 192.168.11.102 và Wi-Fi 192.168.1.52.
- Kết quả tại thời điểm kiểm tra: bo đã nối SOMICS và có IP 192.168.1.52.
- Nâng cấp chỉ thêm cli.py, cập nhật __init__.py và cài /usr/bin/mira-wifi;
  không restart dịch vụ hoặc radio, không đổi mật khẩu/hồ sơ mạng.
- Bản sao các file trước nâng cấp: /root/mira-wifi-cli-backup-1791344320.
- Chưa dùng menu để đổi mạng thật sang một SSID khác; luồng gửi lệnh được kiểm thử
  với radio mô phỏng để giữ nguyên kết nối đang hoạt động.

Gói: dist/mira-wifi-0.1.1.tar.gz

SHA-256: `a9c9168c0cb9d079debb3140610e528bee7da9c170d058d87c4f18515b2e783b`.

Source chính: src/mira_wifi/cli.py. Trình chạy PC: tools/wifi_menu.py và wifi-menu.ps1.
Installer đã bao gồm lệnh terminal cho các lần cài mới. Gói 0.1.0 cũ vẫn giữ trong dist.

## English terminal menu 0.1.2 — 07/10/2026

All terminal labels, prompts, help, status messages and errors are now in English.
Network names are unchanged. Forgetting a network requires typing YES.

- 39/39 Wi-Fi tests passed, including the English status-message check.
- Verified both status output and the interactive menu over SSH on the device.
- Device remains connected to SOMICS at 192.168.1.52.
- Updated only cli.py and __init__.py, without restarting the service or radio.
- Saved profiles were checked byte-for-byte and remain unchanged.
- Previous files: /root/mira-wifi-english-backup-1791346143.

Package: dist/mira-wifi-0.1.2.tar.gz

SHA-256: 166e84a1069f6e2b56355fe6fb463940652d571978189820d0ea3b9c4a5d5c66.
