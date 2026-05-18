# 🏎️ VDR Telemetry System (Vehicle Data Recorder)
**An Investigative Evidence Storage & Export System for Vehicles and Smart Traffic Safety (ATGT 2026).**

---

## 🌟 Overview (Tổng quan)
**VDR Telemetry System (V2)** là một hệ thống "Hộp đen" thông minh chuyên dụng được thiết kế cho nền tảng nhúng (Orange Pi 4 Pro / Raspberry Pi). Hệ thống thu thập dữ liệu chẩn đoán xe (OBD-II qua CAN Bus) và đồng bộ hóa với bằng chứng video (IP Camera RTSP). 

Dự án áp dụng kiến trúc **Đa tiến trình (Multiprocessing)** và cơ chế **Bất tử (Fault Tolerance)** để vượt qua giới hạn GIL của Python, đảm bảo hệ thống chạy mượt mà, tự phục hồi sau các sự cố vật lý như đứt cáp, mất mạng mà không làm treo hệ thống.

---

## ✨ Key Features (Tính năng nổi bật)
* **True Multiprocessing Architecture**: Tách biệt hoàn toàn nhân CPU đọc tín hiệu CAN và nhân CPU xử lý luồng Video Camera, đảm bảo tần số lấy mẫu (Sampling Rate) 10Hz ổn định tuyệt đối.
* **Zero-CPU Video Segmenting**: Sử dụng `FFmpeg` (-c copy) cắt luồng RTSP thành các file `.ts` nhỏ liên tục, giải phóng 100% gánh nặng Encode Video cho CPU, đồng thời chống hỏng file video khi xe bị sập nguồn đột ngột.
* **Auto-Reconnect & Graceful Degradation**: Tích hợp cơ chế bảo vệ `try/except` nhiều lớp. Rút dây Camera thì luồng OBD vẫn sống. Cắm cáp lại, hệ thống tự động nhận diện và ghi hình tiếp (Tự phục hồi).
* **Self-Healing Storage (Quản lý tài nguyên 24/7)**: Vận hành ngầm (`Daemon Thread`) theo cơ chế chuẩn công nghiệp. Tự động xoay vòng dung lượng ổ cứng (xóa file `.mp4`, `.ts` cũ) và tối ưu hóa CSDL (Purge & VACUUM SQLite) để hệ thống chạy "bất tử" không bao giờ báo đầy ổ.
* **Glass Morphism HUD Engine**: Render video sau hành trình sử dụng thư viện Pillow và thuật toán lấp đầy dữ liệu trống (`ffill/bfill`) của Pandas, tạo giao diện HUD kính mờ hiện đại, đồng bộ timestamp tuyệt đối.

---

## 🛠️ System Requirements (Yêu cầu hệ thống)
### 1. OS & System Packages
* **OS**: Linux (Debian/Ubuntu/Armbian trên Orange Pi) hoặc Windows.
* **FFmpeg**: Yêu cầu bắt buộc để chia nhỏ luồng RTSP.
  ```bash
  sudo apt-get update
  sudo apt-get install ffmpeg



  VDR_Telemetry_System/
├── 📂 assets/              # Tài nguyên tĩnh: Fonts, Video mẫu
├── 📂 data/                # Dữ liệu động: Database SQLite, Video xuất ra (.ts, .mp4)
├── 📂 diagnostics/         # CÁC SCRIPT KIỂM THỬ PHẦN CỨNG (HIL Test)
│   ├── test_p0_hardware.py # Test cổng Serial OS
│   ├── test_p1_passive.py  # Test bắt gói tin CAN thụ động
│   ├── test_p2_query.py    # Test hỏi/đáp OBD-II
│   ├── test_p3_database.py # Test Stress Database WAL
│   └── test_p4_camera.py   # Test kết nối LAN & luồng RTSP Camera
├── 📂 obd_module/          # MODULE XỬ LÝ DỮ LIỆU XE
│   ├── can_app.py          # Logic đọc CAN Bus với Auto-Reconnect
│   ├── db_setup.py         # Quản lý Database SQLite
│   └── ecu_sim.py          # Giả lập hộp ECU khi không cắm xe thật
├── 📂 vision_module/       # MODULE XỬ LÝ HÌNH ẢNH
│   └── camera_recorder.py  # Ghi luồng RTSP bằng FFmpeg
├── 📄 config.py            # Cấu hình trung tâm (Single Source of Truth)
├── 📄 main.py              # File điều phối đa luồng (Khởi chạy hệ thống)
├── 📄 overlay_engine.py    # Engine render HUD Luxury đè thông số lên Video
├── 📄 storage_manager.py   # Daemon dọn dẹp ổ cứng (Auto-delete)
├── 📄 test_system.py       # Script kiểm tra môi trường Python
└── 📄 requirements.txt     # Danh sách thư viện