# VDR Telemetry System — Kiến trúc & Workflow

> Hộp đen ô tô thông minh (BK-AutoBlackBox). Tài liệu mô tả kiến trúc server, luồng kết nối Pi ↔ Server và workflow xử lý tai nạn.

---

## 1. Nguyên tắc thiết kế

- **Pi = nguồn sự thật gốc.** Toàn bộ `obd_data` thô + video gốc nằm ở Pi.
- **Server chỉ lưu sự kiện + video bằng chứng đã render.** Không giữ `obd_data` thô.
- **2 kênh truyền tách biệt** (dùng đúng công cụ cho đúng việc):
  - **MQTT-over-WebSocket** — sự kiện (crash/alert) + số liệu live. Nhẹ, realtime.
  - **HTTP POST** — gói bằng chứng (video + obd) dạng `.zip`. File nặng.
- **Subdomain riêng** `vdr.bkauto.vn`
- Pi đứng sau NAT/4G nên **Pi luôn chủ động đẩy lên server**, server không gọi ngược xuống Pi.

---

## 2. Connection flow — Pi ↔ Server đi đường nào

```
[VDR — KIẾN TRÚC TỔNG THỂ]
│
├── PI (trên xe) ──────────────► THU THẬP (nguồn sự thật gốc)
│   ├── camera_recorder ───────► ghi video raw
│   ├── obd_module / CAN ──────► telemetry_v1.db
│   ├── crash_detector ───────► MPU + G-force → cắt video + obd
│   └── uploader
│       ├── MQTT-WS ──────────► sự kiện + live (NHẸ, realtime)
│       └── HTTP POST ────────► video raw + obd (NẶNG, khi có mạng)
│
├── ĐƯỜNG TRUYỀN ──────────────► vdr.bkauto.vn (subdomain RIÊNG)
│   └── Cloudflare → nginx
│       ├── /mqtt ────────────► broker (WS)
│       └── /api/upload-evidence ► nhận file video
│
└── SERVER (PC lab, Docker) ───► XỬ LÝ + PHỤC VỤ
    ├── mqtt broker ──────────► nhận sự kiện/live
    ├── server_app ───────────► lưu server_history.db + đẩy WS ra web
    ├── render worker ────────► overlay HUD: video raw → video bằng chứng
    ├── kho video ────────────► video đã render (xem lúc nào cũng được)
    └── web UI ───────────────► dashboard + lịch sử
```

---

## 3. Workflow tai nạn — từ va chạm tới xem video

```
[WORKFLOW TAI NẠN]
│
├── 1. PHÁT HIỆN (Pi) ─────────► MPU: G-force/tilt vượt ngưỡng
│
├── 2. ĐÓNG GÓI (Pi) ─────────► crash_detector
│   ├── cắt video raw + obd snapshot
│   └── ghi crash_events (PENDING)
│
├── 3. BÁO NGAY (Pi→Server) ──► MQTT-WS: đẩy metadata crash
│   └── Server → Web: cảnh báo realtime (thấy liền)
│
├── 4. ĐẨY FILE (khi có mạng) ► HTTP POST: zip (video + obd)
│
├── 5. RENDER (Server) ───────► overlay HUD lên video → mp4 H.264
│   └── lưu vào kho video
│
└── 6. XEM LẠI (Web/App) ─────► lấy video đã render TỪ SERVER
                                 (Pi tắt vẫn xem được)
```

---

## 4. Các thành phần Docker (trên server)

| Service | Vai trò |
|---------|---------|
| `mqtt` | Broker Mosquitto, listener WebSocket :9001 |
| `server` | FastAPI (REST + WebSocket) + MQTT client nền + nhận upload zip |
| `render` | Worker quét pending → overlay HUD → xuất mp4 → cập nhật DB |
| `web` | Giao diện dashboard |

DB server: `server_history.db` (chỉ metadata sự kiện). Kho video: `/data/evidence/rendered/`.

---

## 5. Trạng thái — đã làm gì (tính tới mốc này)

```
[ĐÃ CODE + TEST PASS]
│
├── ✅ MQTT-WS: Pi → broker → server lưu sự kiện
├── ✅ HTTP upload zip bằng chứng (video + obd)
├── ✅ server_app: REST API + WebSocket live + endpoint upload
├── ✅ render worker: overlay HUD thật lên video → mp4 H.264
├── ✅ Logic pending / has_video / active (cảnh báo takeover)
└── ✅ docker-compose 4 service, test end-to-end PASS
```

Toàn bộ đã chạy thật trên môi trường local (broker + server + render + upload), kiểm chứng end-to-end: đẩy crash → upload → render ra video H.264 hợp lệ → API trả `has_video: true`.

---

## 6. Cần cấp để triển khai lên server thật

| # | Cần |
|---|-----|
| 1 | Quyền Docker trên máy `bkauto` (SSH + chạy `docker compose`) | 
| 2 | File `firebase_key.json` (server gửi push notification) |
| 3 | Subdomain `vdr.bkauto.vn` + route nginx (có block mẫu trong `deploy/nginx-vdr.conf.example`) + DNS Cloudflare | 
| 4 | Path ghi data trên máy + dung lượng ổ trống |
