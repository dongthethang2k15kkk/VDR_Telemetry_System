import os
from pathlib import Path
from typing import Final

OPERATION_MODE: Final[str] = "PRODUCTION" # Đổi thành "PRODUCTION/SIMULATION" khi chạy thực tế hoặc giả lập

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "data"
STORAGE_DIR.mkdir(exist_ok=True)
DATABASE_PATH = STORAGE_DIR / "telemetry_v1.db"

# Cấu hình CAN
CAN_INTERFACE: Final[str] = "test_channel" if OPERATION_MODE == "SIMULATION" else "/dev/ttyACM0"
CAN_BUS_TYPE: Final[str] = "virtual" if OPERATION_MODE == "SIMULATION" else "slcan"
CAN_BITRATE: Final[int] = 500000

# OBD-II PIDs
OBD_REQUEST_ID: Final[int] = 0x7DF
OBD_RESPONSE_ID: Final[int] = 0x7E8

# 4 LOẠI DỮ LIỆU CỐT LÕI
TELEMETRY_SCHEMA = {
    0x0D: {"label": "Vehicle Speed", "unit": "km/h"},
    0x0C: {"label": "Engine RPM", "unit": "rpm"},
    0x11: {"label": "Throttle Position", "unit": "%"},
    0x05: {"label": "Coolant Temp", "unit": "°C"},
}

SAMPLING_RATE_HZ: Final[int] = 5

# Số ngày giữ lại dữ liệu telemetry trong DB.
# Bản ghi cũ hơn ngưỡng này sẽ bị xóa khi kích hoạt dọn dẹp.
RETENTION_DAYS: Final[int] = 30

if OPERATION_MODE == "SIMULATION":
    # Dùng video mẫu hoặc Webcam (số 0) khi đang code trên Laptop
    VIDEO_SOURCE: Final[str] = "assets/sample_video.mp4"
else:
    # Link luồng RTSP từ Camera IP
   VIDEO_SOURCE: Final[str] = "rtsp://10.10.10.20:554/user=admin&password=&channel=1&stream=0.sdp"

# Đường dẫn file video xuất ra sau khi Render xong
OUTPUT_VIDEO_PATH: Final[str] = str(STORAGE_DIR / "crash_evidence.ts")

# Đường dẫn Font chữ (Tự động đổi theo môi trường Windows/Linux)
if OPERATION_MODE == "SIMULATION":
    FONT_PATH: Final[str] = "assets/arialbd.ttf"
else:
    FONT_PATH: Final[str] = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"