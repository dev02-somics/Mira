# Phương án chốt — MIRA Player 0.2.0

Ngày 07/10/2026, phạm vi một thiết bị MIRA trên OpenWrt đã có sẵn.

## Thành phần

Một dịch vụ Python 3.9 quản lý kết nối MQTT, bộ điều phối và audio.
Một worker riêng tải file. Callback MQTT chỉ đưa dữ liệu vào inbox có giới hạn.
Bộ điều phối là nơi duy nhất thay đổi trạng thái, hàng đợi và gọi audio.
MPD giữ nhiệm vụ giải mã/phát; ALSA/MRAA điều khiển output WM8960 và amplifier.
procd quản lý boot/respawn.

Web/backend → MQTT command/file event → bộ điều phối → MPD/ALSA → loa.
Download worker → HTTP/HTTPS → kiểm tra hash → cache SD → bộ điều phối.
Thiết bị → MQTT result/state/presence → web/backend.

## Hành vi đã chọn

- File event mới hợp lệ được tải và xếp phát FIFO.
- Play cần media_id đã tải; nếu đang phát/paused/preparing sẽ trả player_busy.
- Pause giữ bài/vị trí/quyền audio; resume tiếp tục vị trí đó.
- Replay dùng media_id chỉ định hoặc bài hiện tại/gần nhất, bắt đầu từ đầu.
- Stop dừng phiên và xóa hàng đợi autoplay. Download đang chạy vẫn có thể hoàn
  tất để lưu cache, nhưng không tự phát sau stop.
- Volume 0..100 điều chỉnh Headphone ALSA khi sở hữu audio. Khi idle chỉ lưu
  âm lượng mong muốn để không thay output của MGWP.
- Sau reboot/restart: idle, thư viện và âm lượng được giữ; command accepted
  chưa kết thúc được đánh failed/service_restarted, không tự thực hiện lại.
- Cache mặc định 64 MiB, 8 mục chờ, metadata 256 media ID. ID cũ được giữ sau
  khi file bị dọn để nhận biết trùng; khi metadata đầy, từ chối file mới.
  Chưa có chức năng xóa thư viện qua MQTT trong bản đầu.
- Chỉ nhận lệnh còn hạn tối đa 300 giây; web phải dùng ID riêng cho mỗi yêu cầu.
  Retained command/file event bị bỏ qua để tránh thực hiện lại lúc reconnect.
- QoS 1 và journal hỗ trợ retry/chống trùng, không bảo đảm exactly-once khi mất
  điện ngay giữa tác động phần cứng và cập nhật journal.

## Phối hợp firmware

Dùng mapping đã có trong source gốc: amplifier MRAA 34 active=1, mute 17 unmute=0,
output Headphone. Không lập trình thanh ghi I2C codec trực tiếp.
Kiểm tra WM8960/ALSA trước khi chạm GPIO. Nếu MPD đang play/pause bởi chương trình
khác, từ chối giành quyền. Trong phiên, MGWP được suspend theo PID + start time;
stop/finish khôi phục đúng tiến trình. Lease /tmp giữ thông tin phục hồi nếu daemon
crash trong cùng lần boot; reboot tạo phiên mới.

Ứng dụng cũ SOMICS phải được dừng trước khi bật dịch vụ mới.
Wrapper MGWP hiện có killall python khi crash; không sửa firmware trong bản này.
Cần kiểm thử thực tế việc phục hồi session/GPIO trước khi vận hành liên tục.

## Phạm vi kiểm chứng

Test PC gồm vòng đời phát, HTTP thật localhost, hash, cache, journal, MQTT callback,
MPD giả, cleanup và release. Cú pháp được kiểm tra theo Python 3.9.
Broker thật, âm lượng thực, SIGSTOP/SIGCONT trên bo, reboot/4G và installer rollback
trên OpenWrt là các bước kiểm chứng triển khai còn lại.

## Nguồn kỹ thuật

- [MPD protocol](https://mpd.readthedocs.io/en/stable/protocol.html)
- [MQTT 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
- [Paho Python](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)
- [OpenWrt procd](https://openwrt.org/docs/techref/procd)
