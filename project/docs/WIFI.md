# MIRA Wi-Fi 0.1.0

Đã cài lên MIRA tại 192.168.11.102 ngày 07/10/2026. Đây là dịch vụ riêng với
MIRA Player, dùng Python 3.9 và các công cụ Wi-Fi có sẵn trên OpenWrt.

## Dùng ngay

1. Trên điện thoại/PC, kết nối **MIRA-Setup-5D81**.
2. Xem mật khẩu tại [file truy cập riêng](../config/mira-wifi-access.local.txt).
   Mật khẩu này dùng cho cả Wi-Fi cài đặt và trang quản lý, không phải mật khẩu SSH.
3. Mở **http://192.168.12.1:8088**, đăng nhập, chọn mạng và nhập mật khẩu.
4. Khi trạng thái là “Đã kết nối”, trang hiển thị lệnh SSH với IP do router cấp.
   PC cần cùng mạng với bo để dùng IP đó. Bạn cũng có thể SSH trực tiếp
   `ssh root@192.168.12.1` khi đang nối Wi-Fi cài đặt.

Nếu đang cắm LAN: **http://192.168.11.102:8088** cũng mở được trang.
Điện thoại có thể báo Wi-Fi cài đặt không có Internet; chọn vẫn kết nối.

Địa chỉ LAN 192.168.11.102 được giữ nguyên. Wi-Fi cài đặt dùng dải riêng
192.168.12.0/24, cấp DHCP từ .10 đến .59 và không chuyển tiếp Internet.
Khi nối router khác, IP Wi-Fi do router cấp có thể thay đổi. Tên MIRA.local
có thể dùng trên máy hỗ trợ mDNS, nhưng IP hiện trên trang là cách kiểm tra trực tiếp.

## Hành vi

- Dò mạng 2.4 GHz, gộp điểm phát trùng tên và cùng loại bảo mật theo tín hiệu mạnh nhất.
- Hỗ trợ WPA2-Personal, kể cả mạng WPA/WPA2 có WPA2. WPA3-only, Enterprise,
  WEP, mạng mở, SSID ẩn và captive portal chưa được hỗ trợ ở bản này.
- Chọn mạng đã lưu có thể để trống mật khẩu hoặc nhập mật khẩu mới.
- Chỉ lưu mật khẩu mới khi đã xác nhận liên kết Wi-Fi và có IP DHCP.
  Nhập sai không ghi đè mật khẩu tốt đã lưu.
- Tối đa 16 mạng. Có nút Quên. Mật khẩu không hiển thị lại trên trang.
- Khởi động với Wi-Fi cài đặt; sau 15 giây ưu tiên mạng đã lưu đang nhìn thấy,
  theo lần kết nối thành công gần nhất, rồi theo tín hiệu.
- Đang kết nối tốt thì giữ mạng hiện tại. Khi mất liên kết, chờ 20 giây
  cho driver nối lại, sau đó khôi phục AP và thử mạng quen.
- Mỗi lần kết nối chờ tối đa 50 giây. Mạng thất bại tự thử lại sau ít nhất 5 phút.
- Bo chỉ có một radio, AP và STA phải dùng cùng kênh. Đổi mạng có thể làm Wi-Fi
  cài đặt ngắt tạm thời; nhập sai sẽ trở lại AP trên kênh 1.
- Trạng thái “Đã kết nối” xác nhận liên kết và DHCP, không khẳng định có Internet.
- Nếu router cũng dùng 192.168.12.0/24, cần đổi thiết kế dải AP trước khi sử dụng.
- Trang không tự bật popup captive portal. Mở URL bằng trình duyệt.

## Triển khai hiện tại

- Code: /root/mira-wifi
- Dịch vụ procd: /etc/init.d/mira_wifi, bật tự chạy khi khởi động.
- Hồ sơ: /etc/mira-wifi/profiles.json
- Cấu hình/đăng nhập: /etc/mira-wifi/settings.json và access.txt
- Ba file chứa dữ liệu riêng có quyền 0600; thư mục quyền 0700.
- Wi-Fi A50 cũ đã được nhập vào danh sách lưu; mật khẩu không chép về source.
- AP riêng có WPA2. Trang :8088 yêu cầu đăng nhập, cookie HttpOnly/SameSite,
  CSRF và kiểm tra Host/Origin. Trang dùng HTTP trong mạng cục bộ.
- Firewall cho SSH và trang quản lý trên AP/uplink Wi-Fi, chặn trang từ zone WAN.
  Không thêm chuyển tiếp giữa mạng Wi-Fi cài đặt và LAN/uplink.
- Metric tuyến: Wi-Fi 5, modem WAN 20, LAN 50 để ưu tiên Wi-Fi khi có DHCP.
- Cấu hình STA đang thử chỉ ở UCI tạm trong RAM. Không commit flash mỗi lần thử.
  Base cấu hình lưu trên flash là AP bật/STA tắt, giúp phục hồi sau mất điện.
- Dịch vụ này quản lý radio0 và các section mira_*; dừng dịch vụ trước khi
  chỉnh trực tiếp các section này bằng LuCI/UCI.

Bản sao trước khi cài: **/root/mira-wifi-backup-1791336105**.
Gồm wireless, network, dhcp, firewall gốc và chương trình rollback.

Khôi phục cấu hình mạng cũ từ SSH qua LAN:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 /root/mira-wifi-backup-1791336105/install.py --rollback /root/mira-wifi-backup-1791336105
```

Rollback tắt dịch vụ Wi-Fi mới và trả lại bốn cấu hình mạng cũ.
Code và file thông tin truy cập riêng còn lưu để kiểm tra.
Installer là cài lần đầu, sẽ từ chối ghi đè một cài đặt đã có.
Đã kiểm tra việc tạo backup; chưa thực hiện rollback hoặc reboot toàn bo.

## Phát triển

```powershell
& .\.tools\python\python.exe .\tools\wifi.py test
& .\.tools\python\python.exe .\tools\wifi.py demo
& .\.tools\python\python.exe .\tools\wifi.py build
& .\.tools\python\python.exe .\tools\wifi_browser_check.py
```

Demo tại http://127.0.0.1:8088, mật khẩu demo-wifi; radio mô phỏng.
Kiểm thử trình duyệt dùng Chrome ẩn với hồ sơ tạm, không đọc hồ sơ cá nhân.
Chế độ --device chỉ đăng nhập và đọc danh sách mạng trên bo, không đổi Wi-Fi.

- src/mira_wifi/core.py: danh sách lưu, hàng đợi, chọn mạng và phục hồi.
- src/mira_wifi/openwrt.py: iwinfo/ubus/UCI, không chạy lệnh qua shell.
- src/mira_wifi/app.py và static/: HTTP và giao diện không phụ thuộc Internet.
- deploy/install_wifi.py: xác minh gói, backup, cấu hình lần đầu, rollback.
- tools/wifi.py: test/demo/build riêng; không thay gói Player 0.2.0.

Gói dist/mira-wifi-0.1.0.tar.gz chỉ chứa code và manifest, không chứa mật khẩu.
File config/*.local.txt và config/*.local.json bị loại khỏi Git.

## Kiểm chứng

- 24/24 bài kiểm thử Wi-Fi qua trên PC; HTTP thật localhost, radio mô phỏng.
- Chrome ở 390×844: đăng nhập, danh sách mạng, hộp nhập mật khẩu, IP SSH, lưu mạng,
  đăng xuất và không tràn ngang.
- Trên bo: UCI roundtrip với dấu nháy, dollar, backslash và backtick không bị đổi.
- AP báo Master, WPA2/CCMP, kênh 1; interface setup có IP 192.168.12.1.
- API đăng nhập và dò mạng hoạt động với danh sách mạng thật, không trả mật khẩu.
- Dịch vụ báo running, có liên kết tự khởi động; hồ sơ A50 vẫn còn.
- Kết nối uplink bằng mật khẩu mạng mới chờ người dùng tự nhập trên trang.
  Chưa xác nhận việc điện thoại nhận lease DHCP hay SSH qua Wi-Fi từ một client thật.

Nguồn tham chiếu: [OpenWrt ubus/iwinfo](https://openwrt.org/docs/guide-developer/ubus/iwinfo),
[quản lý Wi-Fi với Travelmate](https://openwrt.org/docs/guide-user/network/wifi/wifiextenders/travelmate).

Kết quả bản cuối: **70/70** kiểm thử toàn project qua (46 Player + 24 Wi-Fi).
Chrome đã kiểm tra trực tiếp trang trên bo qua LAN: đăng nhập, danh sách Wi-Fi thật,
giao diện điện thoại và cookie. Dịch vụ đã được restart và vẫn đọc được hồ sơ A50;
dnsmasq báo DHCP 192.168.12.10–192.168.12.59 hoạt động.

SHA-256 gói Wi-Fi cuối:
`8ea761c5184a7558f0b43fad7f6f1a1eb27f6fdfd76d5b006f1ee1fce527a6ad`.

## Cập nhật 0.1.1: menu terminal

Đã thêm lệnh `mira-wifi`, dùng chung bộ điều khiển và hồ sơ mạng.
Xem [hướng dẫn terminal](WIFI_TERMINAL.md). 85/85 kiểm thử toàn project qua.
Bo đã kết nối SOMICS, có IP 192.168.1.52 tại thời điểm kiểm tra ngày 07/10/2026;
PC đọc được trạng thái qua chính IP Wi-Fi này.
