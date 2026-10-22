# Giao thức web SOMICS ↔ MIRA Player 0.3.0

## Topic (theo ACL backend cấp lúc kích hoạt)

| Topic | Chiều | Ghi chú |
| --- | --- | --- |
| `station/player/device/{ID}/command` | Web → bo | Bo chỉ được subscribe topic này |
| `station/player/device/{ID}/status` | Bo → web | Bo chỉ được publish topic này, QoS 1, không retain |

`station/player` là `topic_base` trong config. Mọi tin bo gửi đều lên `status`,
có thêm `kind` và `deviceId`. Backend coi mỗi tin status là heartbeat (online 120 giây),
nên bo không dùng retain/Last Will (tin cũ sẽ làm web tưởng bo còn online).

## HTTP bo gọi tới web (`api_base`)

| Endpoint | Khi nào | Body |
| --- | --- | --- |
| `POST /api/iot/activate` | Kích hoạt 1 lần | `deviceId`, `activationCode`, `firmwareVersion` |
| `POST /api/iot/token` | Chưa có/không refresh được token | `deviceId`, `mqttPassword`, `fileId` |
| `POST /api/iot/token/refresh` | Access token hết hạn (10 phút) | `deviceId`, `refreshToken` |
| `GET /api/files/{id}` | Tải file | Header `Authorization: Bearer <accessToken>` |

Kích hoạt lỗi 502 không làm mất mã. Download bị 401 → bo lấy token mới và thử lại 1 lần.

## Lệnh web → bo

**Tải và phát file** (backend đang gửi):

```json
{"type": "file", "action": "download", "fileId": "uuid", "fileName": "a.mp3",
 "mimeType": "audio/mpeg", "size": 559697, "sha256": "<64 hex>",
 "downloadEndpoint": "/api/files/uuid", "sentAt": "..."}
```

Chỉ MP3/WAV ≤ 20 MiB. File được xếp hàng FIFO, tải xong tự phát, giữ trong cache để replay.

**Danh sách bài trên bo:** theo thứ tự nhận (FIFO), mỗi tên file chỉ một dòng
(không phân biệt hoa/thường). Gửi lại file cùng tên → bản mới thay bản cũ **tại chỗ**,
xoá file cũ (đang phát thì dừng), rồi tải và phát bản mới; bo gửi event `replaced`.
Bài trước/Bài tiếp đi theo thứ tự này. Thứ tự chỉ đổi khi có chức năng chọn bài (chưa làm).

**Nút điều khiển trên web** (`POST /api/iot/devices/{ID}/control` → MQTT):

```json
{"id": "uuid", "type": "control", "action": "set_volume", "volume": 42, "sentAt": "..."}
```

Bo đổi sang lệnh nội bộ (`command_id` = `id`, hết hạn sau 60 giây theo giờ bo).

| action | Tham số | Hành vi |
| --- | --- | --- |
| set_volume | volume 0..100 | Đổi âm lượng ngay, lưu cho lần sau |
| pause | — | Tạm dừng, giữ vị trí |
| resume | — | Tiếp tục bài đang dừng; nếu bài đã hết → phát lại bài gần nhất |
| next / previous | — | Bài kế / bài trước trong các file đã tải (theo thứ tự nhận, vòng lại) |
| set_repeat | repeat: `off` \| `one` | Lặp 1 bài: hết bài thì phát lại bài đó (lưu qua restart) |
| seek | fileId, positionSeconds | Tua bài đang phát/tạm dừng tới vị trí (giây) |
| play | mediaId (= fileId) | Phát file đã cache nếu đang rảnh (chưa có nút) |
| stop / replay | replay: mediaId tuỳ chọn | Dừng + xoá hàng đợi / phát lại từ đầu (chưa có nút) |

Định dạng nội bộ cũng được nhận trực tiếp:
`{"command_id", "device_id", "action", "expires_at", "media_id"?, "volume"?}` —
`expires_at` = UNIX giây, tối đa 300 giây sau hiện tại. Cùng ID gửi lại chỉ nhận lại
kết quả cũ, không chạy lần hai. Tin retained bị bỏ qua.

## Tin bo → web (trên topic status)

| kind | Nội dung chính |
| --- | --- |
| `presence` | `state: online` sau khi subscribe thành công |
| `state` | Cho panel phát nhạc trên web: `state` (IDLE/PLAYING/PAUSED/DONE/ERROR), `fileId`, `fileName`, `positionSeconds`, `durationSeconds`, `repeat`, `volume`. Kèm `playerState` (idle/preparing/playing/paused/error), queued, downloads, error. Mỗi 10 giây và khi đổi trạng thái |
| `result` | `command_id`, `status` (accepted/completed/failed/rejected), `error` |
| `event` | `type`: downloading / downloaded / download_failed / finished / duplicate_file / replaced, `media_id` |

`accepted` chỉ là đã nhận; `completed` của play/replay chỉ gửi khi MPD đã phát.
