# VDR Telemetry - Hướng dẫn triển khai lên Server (VPS)

Bộ Docker này đóng gói phần **chạy trên server** của hệ thống hộp đen xe BK-AutoBlackBox.

## Kiến trúc
- **Pi**: giữ phần phần cứng (CAN/camera), chủ động ĐẨY data lên server (bảo mật: server không cần đọc Pi).
- **Server**: chạy 3 container, phục vụ người dùng truy cập từ mọi nơi qua IP công khai.

## Yêu cầu server
- Docker + Docker Compose
- Mở cổng 8080 (API) và 8888 (web) ra ngoài (hoặc đặt sau reverse proxy / nginx)

## Cách chạy

```bash
# Tại thư mục gốc project (không phải trong deploy/)
docker compose -f deploy/docker-compose.yml up -d --build

# Xem log
docker compose -f deploy/docker-compose.yml logs -f

# Dừng
docker compose -f deploy/docker-compose.yml down
```

Sau khi chạy: truy cập `http://<IP_SERVER>:8888` (giao diện), API ở `http://<IP_SERVER>:8080`.

## CẦN CHỈNH KHI DEPLOY THẬT (đánh dấu để không quên)

1. **Đường data**: `config.py` cần đọc `DATABASE_PATH` từ biến môi trường (compose đã set `/data/telemetry_v1.db`). Kiểm tra config.py ưu tiên env này.
2. **Cơ chế Pi đẩy data lên server**: phần này CHƯA hoàn chỉnh - dự kiến dùng MQTT (store-and-forward) hoặc HTTP push. Cần thêm endpoint nhận data ở api + uploader ở Pi.
3. **Render worker**: hiện `render` container mới là khung chờ - cần thêm vòng lặp "quét crash PENDING_UPLOAD → render → cập nhật DB".
4. **app.js của web_ui**: đổi `CONFIG.API_URL` trỏ về IP server (hiện trỏ IP Pi local).
5. **Bảo mật**: thêm HTTPS (nginx + cert), giới hạn CORS, đổi mật khẩu Lab.

## Trạng thái

| Phần | Trạng thái |
|------|-----------|
| Dockerfile 3 service | Xong (bản nháp) |
| docker-compose | Xong (bản nháp) |
| Cơ chế Pi→server đẩy data | Chưa (cần MQTT/HTTP) |
| Render worker tự động | Chưa (cần vòng lặp quét PENDING) |

> Đây là bản nháp đóng gói sẵn. Khi có VPS, deploy thử + chỉnh các mục "CẦN CHỈNH" ở trên.
