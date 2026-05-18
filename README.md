# VDR Telemetry System: Automotive Data Logging and HUD Architecture

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Orange%20Pi%20%7C%20Linux%20%7C%20Windows-orange.svg)](https://www.armbian.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)

Hệ thống giám sát hành trình, ghi vết dữ liệu Telemetry và đồng bộ Video HUD thời gian thực hiệu năng cao. Hệ thống được thiết kế tối ưu hóa cho các thiết bị nhúng phần cứng như **Orange Pi 4 Pro**, giao tiếp qua mạch dịch mã **CANable Pro 2.0 (slcan)** và thu hình trực tiếp từ **Camera IP (RTSP)**.

---

## 🚀 1. Triết lý Thiết kế & Kiến trúc Phân lớp Module

Hệ thống được tổ chức chặt chẽ theo mô hình hướng thực thể chức năng, phân chia rõ ràng trách nhiệm của từng cấu phần nhằm tối ưu hóa CPU và chống nghẽn khóa I/O.

### 🎧 `can_app.py`
Chịu trách nhiệm lắng nghe trực tiếp lưu lượng luồng dữ liệu trên mạng CAN Bus mạng xe, bóc tách các tín hiệu điện từ cấp thấp thành thông tin số học hữu ích với độ trễ cực thấp (Low Latency).
* **Vòng đời An toàn (Graceful Shutdown Pattern):** Sử dụng cơ chế bẫy tín hiệu (`signal.SIGINT`, `signal.SIGTERM`) từ hệ điều hành để đổi cờ trạng thái điều hướng thay vì dừng đột ngột. Giúp giải phóng bộ đệm RAM và đóng cổng kết nối vật lý an toàn, tránh treo kẹt cổng phần cứng ở lần khởi động sau.
* **Giải mã Vật lý Trực tiếp (Direct J1979 Decoding):** Tự động đóng gói chuỗi Byte truy vấn tiêu chuẩn OBD-II và ép cấu trúc giải mã trực tiếp bằng công thức SAE J1979 ở cấp độ Byte. Bỏ qua các thư viện wrapper cồng kềnh, tiết kiệm đáng kể tài nguyên tính toán cho chip nhúng.
* **Kháng Lỗi Vật Lý Vô Hạn (Auto-Reconnect Pattern) `[Mới ở V3]`:** Toàn bộ logic bọc trong vòng lặp giám sát. Nếu xảy ra đứt gãy kết nối cơ học (lỏng giắc, sập nguồn USB CAN), tiến trình chủ động dọn dẹp cổng lỗi, đưa thực thể về trạng thái an toàn và liên tục quét tái kết nối chu kỳ 3 giây cho đến khi phần cứng được cắm lại.

### 🧠 `db_setup.py`
Đảm bảo tính toàn vẹn dữ liệu, tối ưu hóa tốc độ ghi xuống thiết bị lưu trữ lưu động (Thẻ nhớ MicroSD/SSD) có tốc độ I/O giới hạn.
* **Cấu hình SQLite Chuyên Sâu (WAL Mode):** Kích hoạt chế độ `Write-Ahead Logging` song song với tinh chỉnh kích thước bộ đệm cache (`cache_size=-64000`), cho phép các tiến trình đọc/ghi đồng thời mà không gây khóa chết cơ sở dữ liệu.
* **Cơ chế Đệm Ghi Hàng Loạt (Batch Insert Queue):** Gom dữ liệu thô tạm thời trên RAM thành từng cụm. Thực thi câu lệnh ghi hàng loạt (`executemany`) ngay khi hàng chờ chạm mốc 20 bản ghi, giảm tần suất tương tác vật lý với ổ đĩa xuống 20 lần để kéo dài tuổi thọ phần cứng.
* **Cô Lập Đa Tiến Trình Tuyệt Đối (Isolated Process Connection) `[Mới ở V3]`:** Triệt tiêu hoàn toàn thực thể kết nối SQLite toàn cục. Ép luồng đọc CAN Bus và luồng dọn dẹp ổ cứng tự tạo các kết nối độc lập nằm trọn trong vùng không gian nhớ riêng biệt của tiến trình con, triệt tiêu lỗi xung đột `Database is locked`.
* **Chuẩn Hóa Hệ Quy Chiếu Thời Gian (`timestamp_sec`) `[Mới ở V3]`:** Toàn bộ cấu trúc bảng và index được quy đổi đồng nhất về định dạng Số thực chuẩn Giây (Unix Epoch Seconds). Đảm bảo tính khớp nối toán học 1:1 với cơ chế đặt tên và cắt phân đoạn tự động của hệ thống hình ảnh offline.

### 🧹 `storage_manager.py`
Giám sát dung lượng ổ đĩa ngầm để bảo vệ thiết bị không rơi vào trạng thái tràn bộ nhớ dẫn đến sập hệ điều hành.
* **Dọn dẹp Cuốn chiếu (FIFO Disk Rotation):** Tự động tính toán dung lượng phân vùng. Khi ổ đĩa vượt ngưỡng cảnh báo 80%, luồng chủ động định vị các tệp video `.ts` / `.mp4` cũ nhất theo thời gian sửa đổi để thực hiện xóa cuốn chiếu.
* **Thu hồi Dung lượng Chủ động (Dynamic Vacuuming) `[Mới ở V3]`:** Vận hành lệnh xóa dữ liệu nhật ký cũ quá hạn cấu hình (`RETENTION_DAYS`) thông qua kết nối cô lập, kết hợp kích hoạt lệnh `VACUUM` để tái cấu trúc file CSDL SQLite ngầm, thu hồi không gian đĩa vật lý ngay lập tức.

### 🎛️ `main.py`
Quản lý vòng đời và thiết lập môi trường hoạt động cho toàn bộ hệ thống thông qua trung tâm cấu hình tập trung `config.py` (Single Source of Truth).
* **Vượt rào khóa GIL bằng Đa Tiến Trình (Multiprocessing Orchestration):** Tách biệt luồng đọc dữ liệu CAN Bus, luồng quay video Camera IP và luồng dọn dẹp ổ đĩa sang các tiến trình hệ điều hành độc lập (`multiprocessing.Process`), tận dụng triệt để kiến trúc xử lý đa nhân của chip xử lý ARM Orange Pi.
* **Quản lý Đóng gói Cưỡng bức `[Mới ở V3]`:** Khi luồng chính nhận lệnh kết thúc chương trình, cơ chế điều phối tự động gửi tín hiệu hạ cánh văn minh đến các tiến trình con, ép luồng Camera chốt dữ liệu video `.ts` cuối và ép luồng CAN ghi hết hàng chờ RAM xuống đĩa trước khi giải phóng RAM hoàn toàn.

---

## ⚡ 2. Các Tính Năng Cốt Lõi Vượt Trội

1. **Chế Độ Hoạt Động Kép (Dual-Mode Flexibility):**
   * `PRODUCTION`: Chạy thực tế trên xe. Kết nối phần cứng thông qua cổng giao tiếp vật lý `/dev/ttyACM0` và kéo luồng RTSP trực tiếp từ Camera hành trình IP.
   * `SIMULATION`: Chế độ giả lập thuần túy giúp phát triển ứng dụng (R&D) trên Laptop mà không cần phần cứng. Tự động kích hoạt module giả lập ECU (`ecu_sim.py`) để sinh gói tin CAN ảo chuẩn SAE J1979 phản hồi ngược lại hệ thống.
2. **Kháng Lỗi Chủ Động (Fault-Tolerant System):** Hệ thống đạt độ ổn định công nghiệp. Rút cáp LAN của Camera IP hay rút USB CAN ra hệ thống vẫn tự động in log cảnh báo điềm tĩnh và rơi vào vòng lặp chờ Reconnect, cắm thiết bị lại tự động hút dữ liệu tiếp tục mà không cần can thiệp bằng tay.
3. **An Toàn Đa Luồng (Process-Safety Architecture):** Thiết kế bộ nhớ cô lập hoàn toàn biến hệ thống thành một boong-ke chống đạn, loại bỏ hoàn toàn các lỗi xung đột tài nguyên dùng chung ngầm.

---

## 📂 3. Cấu Trúc Thư Mục Dự Án

```text
VDR_Telemetry_System/
├── main.py                  # Điểm khởi chạy hệ thống (Điều phối đa tiến trình)
├── config.py                # File cấu hình tập trung (Chế độ, cổng kết nối, thông số PID)
├── storage_manager.py       # Quản lý vòng xoay ổ đĩa cuốn chiếu & dọn dẹp CSDL
├── overlay_engine.py        # Engine xử lý offline xuất đồ họa HUD đè lên Video
├── requirements.txt         # Danh sách thư viện phụ thuộc của môi trường ảo
├── .gitignore               # Màng lọc bảo vệ chặn tệp nặng (.venv, data/, video .ts, .mp4)
├── obd_module/              # Phân hệ bóc tách dữ liệu CAN Bus & ECU
│   ├── can_app.py           # Logic đọc, giải mã trực tiếp J1979 & Tự động kết nối lại
│   ├── db_setup.py          # Khởi tạo CSDL SQLite, cơ chế đệm RAM Batch Insert
│   └── ecu_sim.py           # Module giả lập phản hồi ECU phục vụ SIMULATION mode
├── vision_module/           # Phân hệ giám sát hình ảnh ngoại vi
│   └── camera_recorder.py   # Kết nối, quản lý quay cuốn chiếu luồng video RTSP qua FFmpeg
├── diagnostics/             # Các script phục vụ kiểm thử phần cứng độc lập tầng thấp
│   ├── test_p0_hardware.py
│   ├── test_p1_passive.py
│   ├── test_p2_query.py
│   ├── test_p3_database.py
│   └── test_p4_camera.py
└── assets/                  # Tài nguyên đồ họa, font chữ phục vụ Render HUD