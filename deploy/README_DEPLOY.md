# BK-AutoBlackBox — Triển khai phần Server (lab / VPS)

Bộ Docker này đóng gói **phần chạy trên server** của hệ thống hộp đen xe.

## Kiến trúc

- **Pi (trên xe)**: giữ toàn bộ phần cứng (camera, CAN/OBD, phát hiện va chạm), chủ động **đẩy** dữ liệu lên server qua MQTT (store-and-forward: mất mạng thì tự dồn, có mạng đẩy bù). Server không cần quyền truy cập ngược vào Pi.
- **Server**: 4 container — `mqtt` (broker, nhận dữ liệu từ Pi), `server` (FastAPI: REST + WebSocket live + nhận zip bằng chứng), `render` (worker render HUD lên video bằng chứng, có ffmpeg), `web` (giao diện).
- Server chỉ lưu **sự kiện + video đã render**. Dữ liệu OBD thô và video gốc nằm ở Pi.

## Yêu cầu server

- Docker + Docker Compose.
- Mở ra ngoài các cổng: **8888** (web), **8080** (API/WS), **9001** (MQTT-WebSocket cho Pi kết nối vào). Cổng 1883 chỉ dùng nội bộ, có thể chặn firewall.

## Triển khai từ A đến Z

```bash
# 1. Lấy code
git clone https://github.com/dongthethang2k15kkk/VDR_Telemetry_System.git
cd VDR_Telemetry_System/deploy

# 2. Sinh mật khẩu MQTT (BẮT BUỘC, làm 1 lần — thiếu là container mqtt không khởi động)
docker run --rm -v "$PWD/secrets:/pw" eclipse-mosquitto:2 \
  mosquitto_passwd -c -b /pw/mosquitto_passwd vdr 'MAT_KHAU_MANH'

# 3. Tạo file cấu hình rồi mở ra điền: MQTT_USER=vdr, MQTT_PASS, LAB_PASSWORD
cp .env.example .env

# 4. (Tùy chọn) Bật thông báo đẩy Firebase — thiếu file này hệ thống vẫn chạy, chỉ tắt push
#    Chép firebase_key.json vào deploy/secrets/  (file này KHÔNG được commit lên git)

# 5. Chạy
docker compose up -d --build

# 6. Kiểm tra
docker compose ps            # 4 container Up
docker compose logs -f server
```

Truy cập: giao diện `http://<IP_SERVER>:8888` — API `http://<IP_SERVER>:8080/api/...`

## Cấu hình phía Pi (trỏ về server)

Trên Pi, trước khi chạy hệ thống:

```bash
export MQTT_HOST=<IP_SERVER> MQTT_PORT=9001 MQTT_USER=vdr MQTT_PASS='MAT_KHAU_MANH'
python main.py
```

## Lưu ý đã biết

- **Ô camera live** trên web chỉ có hình khi mở web trong cùng mạng với Pi (luồng camera phát trực tiếp từ Pi, server không có camera). Xem **lịch sử, cảnh báo, video bằng chứng** thì truy cập từ mọi nơi.
- Muốn gắn subdomain + HTTPS: tham khảo `nginx-vdr.conf.example` (reverse proxy 8888/8080/9001 về sau nginx, cấp cert).

## Trạng thái

| Phần | Trạng thái |
|------|-----------|
| Compose + Dockerfile 4 service | Xong |
| Pi → server đẩy dữ liệu (MQTT store-and-forward) | Xong (`mqtt_uploader.py`) |
| Render video bằng chứng tự động | Xong (`render_worker.py`, quét pending → rendered → cập nhật DB) |
| Web phát video bằng chứng | Xong (`GET /api/evidence/{filename}`) |
| Broker MQTT có xác thực user/pass | Xong (tắt anonymous) |
| HTTPS + subdomain qua nginx | Chưa gắn (đã có file conf mẫu) |
