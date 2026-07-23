# BK-AutoBlackBox — Triển khai phần Server (lab / VPS)

Bộ Docker này đóng gói **phần chạy trên server** của hệ thống hộp đen xe.

## Kiến trúc

- **Pi (trên xe)**: giữ toàn bộ phần cứng (camera, CAN/OBD, phát hiện va chạm), chủ động **đẩy** dữ liệu lên server qua MQTT (store-and-forward: mất mạng thì tự dồn, có mạng đẩy bù). Server không cần quyền truy cập ngược vào Pi.
- **Server**: 4 container — `mqtt` (broker, nhận dữ liệu từ Pi), `server` (FastAPI: REST + WebSocket live + nhận zip bằng chứng), `render` (worker render HUD lên video bằng chứng, có ffmpeg), `web` (giao diện).
- Server chỉ lưu **sự kiện + video đã render**. Dữ liệu OBD thô và video gốc nằm ở Pi.

## Yêu cầu server

- Docker + Docker Compose.
- Mở ra ngoài các cổng: **8888** (web), **8080** (API/WS), **9001** (MQTT-WebSocket cho Pi kết nối vào). Cổng 1883 chỉ dùng nội bộ, có thể chặn firewall.

## Checklist — làm trước khi chạy `docker compose up`

Đánh dấu đủ 4 việc này, thiếu 1 trong 4 là hệ thống không chạy đúng :

- [ ] **Mật khẩu MQTT** đã sinh bằng lệnh ở Bước 2 bên dưới (không sinh thì container `mqtt` không khởi động được).
- [ ] **`LAB_PASSWORD`** trong `.env` đã đổi khỏi giá trị mẫu `DOI_MAT_KHAU_WEB`. Để trống = không ai đăng nhập được web (không phải "cho qua mặc định").
- [ ] **`MQTT_USER` / `MQTT_PASS`** trong `.env` (server) khớp đúng với giá trị đã sinh ở Bước 2, và Pi cũng phải export đúng 2 biến này khi chạy (xem mục "Cấu hình phía Pi" bên dưới) — sai 1 bên là Pi đẩy dữ liệu lên sẽ bị từ chối trong im lặng.
- [ ] **`DATABASE_PATH=/data/auth_sessions.db`** có trong `.env`. Thiếu dòng này không làm hệ thống lỗi ngay, nhưng phiên đăng nhập sẽ mất mỗi khi container bị tạo lại (`docker compose down && up`, hoặc `--build`) — lỗi kiểu này khó nhận ra vì mọi thứ vẫn chạy bình thường cho tới lần rebuild kế tiếp.

Khuyến nghị thêm (không bắt buộc để chạy được, nhưng nên làm): `LAB_PASSWORD` nên **khác** `MQTT_PASS` — dùng chung một mật khẩu cho hai mục đích khác nhau thì lộ cái này kéo theo lộ cái kia.

## Triển khai

```bash
# 1. Lấy code
git clone https://github.com/dongthethang2k15kkk/VDR_Telemetry_System.git
cd VDR_Telemetry_System/deploy

# 2. Sinh mật khẩu MQTT (BẮT BUỘC, làm 1 lần — thiếu là container mqtt không khởi động)
docker run --rm -v "$PWD/secrets:/pw" eclipse-mosquitto:2 \
  mosquitto_passwd -c -b /pw/mosquitto_passwd vdr 'MAT_KHAU_MANH'

# 3. Tạo file cấu hình rồi mở ra điền: MQTT_USER, MQTT_PASS, LAB_PASSWORD, DATABASE_PATH
#    LƯU Ý: để trống LAB_PASSWORD -> KHÔNG ai đăng nhập được web (không phải mặc định "cho qua")
#    LƯU Ý: DATABASE_PATH phải trỏ vào /data/... (trong volume) - để trống thì
#    phiên đăng nhập sẽ mất moi khi container bị tạo lại (down && up, hoặc --build)
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

## Kiểm tra sau khi triển khai (bắt buộc — chạy ngay trên server, sau bước 6)

Chạy nguyên khối này, thay `MAT_KHAU_THAT` bằng đúng `LAB_PASSWORD` vừa điền ở `.env`:

```bash
echo "=== khong token: phai 401 ==="
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/api/alerts

echo "=== docs da tat: phai 404 ==="
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/docs

TOKEN=$(curl -s -X POST http://localhost:8080/api/login \
  -H "Content-Type: application/json" -d '{"password":"MAT_KHAU_THAT"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo "=== co token: phai 200 ==="
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/api/alerts

echo "=== ky duong dan NGOAI danh sach cho phep: phai 400 (chan dung) ==="
curl -s -w " [%{http_code}]\n" -X POST http://localhost:8080/api/media/sign \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"path":"/api/upload-evidence"}'
```

Kỳ vọng đúng thứ tự: `401`, `404`, rồi `200`, và dòng cuối phải có `[400]`. Nếu dòng cuối ra `[200]` — **dừng lại ngay, đây là lỗ hổng nghiêm trọng**, không tiếp tục dùng hệ thống cho tới khi sửa (nghĩa là có ai đó đã mở rộng danh sách đường dẫn được phép ký chữ ký, xem `SIGNABLE_PREFIXES` trong `auth.py`).

Kiểm tra thêm bằng mắt: mở `http://<IP_SERVER>:8888`, đăng nhập bằng `LAB_PASSWORD` thật, xác nhận vào được dashboard. Nếu Pi đã trỏ đúng MQTT về server này thì sau vài giây sẽ thấy dữ liệu realtime.

## Cấu hình phía Pi (trỏ về server)

Trên Pi, trước khi chạy hệ thống:

```bash
export MQTT_HOST=<IP_SERVER> MQTT_PORT=9001 MQTT_USER=vdr MQTT_PASS='MAT_KHAU_MANH'
python main.py
```

## Lưu ý đã biết

- **Ô camera live** trên web chỉ có hình khi mở web trong cùng mạng với Pi (luồng camera phát trực tiếp từ Pi, server không có camera). Xem **lịch sử, cảnh báo, video bằng chứng** thì truy cập từ mọi nơi.
- Muốn gắn subdomain + HTTPS: tham khảo `nginx-vdr.conf.example` (reverse proxy 8888/8080/9001 về sau nginx, cấp cert).
- Toàn bộ route `/api/*` yêu cầu đăng nhập (Bearer token). Video bằng chứng (`/api/evidence/{file}`) dùng signed URL hạn 10 phút, xin qua `/api/media/sign` — web không tự gán link vào `<video src>`.
- `/docs` và `/openapi.json` bị tắt cố ý (`docs_url=None`) để không lộ danh sách route ra Internet.

## Xử lý sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách sửa |
|---|---|---|
| Đăng nhập đúng `LAB_PASSWORD` vẫn báo sai | `.env` sửa sau khi container đã build, chưa rebuild | `docker compose up -d --build` |
| `docker compose logs server` có dòng `⚠️ [AUTH] LAB_PASSWORD trong` | `.env` không có dòng `LAB_PASSWORD`, hoặc container không đọc được `.env` | Kiểm tra `.env` có đúng dòng `LAB_PASSWORD=...`, không để trống |
| Đăng nhập được, nhưng bị đá ra ngay sau lần `up --build` kế tiếp (không phải do hết hạn 7 ngày) | Thiếu `DATABASE_PATH=/data/auth_sessions.db` trong `.env`, session bị ghi ra ngoài volume | Thêm dòng đó vào `.env`, `docker compose up -d --build` lại |
| Container `mqtt` không khởi động (`docker compose ps` không thấy Up) | Chưa chạy lệnh sinh `secrets/mosquitto_passwd` (Bước 2) | Chạy lại lệnh `mosquitto_passwd` ở Bước 2 rồi `docker compose up -d` lại |
| Pi chạy bình thường nhưng server không nhận được dữ liệu | `MQTT_USER`/`MQTT_PASS` trên Pi không khớp với server, hoặc Pi export sai biến | Đối chiếu lại giá trị trên Pi và trong `.env` server, phải giống hệt nhau |
| Video bằng chứng không phát được trên web (ô video trống) | Chữ ký signed URL hết hạn (10 phút) hoặc `/api/media/sign` trả lỗi | Mở tab Network trên trình duyệt, xem request tới `/api/media/sign` trả gì |
| `/docs` vẫn truy cập được | Đang chạy image cũ, build từ trước khi tắt `docs_url` | `docker compose up -d --build` để build lại image mới |

## Trạng thái

| Phần | Trạng thái |
|------|-----------|
| Compose + Dockerfile 4 service | Xong |
| Pi → server đẩy dữ liệu (MQTT store-and-forward) | Xong (`mqtt_uploader.py`) |
| Render video bằng chứng tự động | Xong (`render_worker.py`, quét pending → rendered → cập nhật DB) |
| Web phát video bằng chứng | Xong (`GET /api/evidence/{filename}`) |
| Broker MQTT có xác thực user/pass | Xong (tắt anonymous) |
| Xác thực web (Bearer token + signed URL + WebSocket) | Xong |
| HTTPS + subdomain qua nginx | Chưa gắn (đã có file conf mẫu) |
