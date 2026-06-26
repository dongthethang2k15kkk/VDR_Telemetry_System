import os
from pathlib import Path
from typing import Final

OPERATION_MODE: Final[str] = "PRODUCTION" # Đổi thành "PRODUCTION/SIMULATION" khi chạy thực tế hoặc giả lập
TIME_SOURCE: Final[str] = "INTERNET"   #  " INTERNET/RTC "

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

# 7 LOẠI DỮ LIỆU CỐT LÕI
TELEMETRY_SCHEMA = {
    0x0D: {"label": "Vehicle Speed", "unit": "km/h"},
    0x0C: {"label": "Engine RPM", "unit": "rpm"},
    0x11: {"label": "Throttle Position", "unit": "%"},
    0x05: {"label": "Coolant Temp", "unit": "°C"},
    0x07: {"label": "Fuel Trim Long", "unit": "%"},
    0x10: {"label": "MAF", "unit": "g/s"},
    0x0F: {"label": "Intake Air Temp", "unit": "°C"},
}

SAMPLING_RATE_HZ: Final[int] = 2
# Thoi gian cho toi da 1 PID tra loi (giay). Hieu chuan qua test_p5_calibration.py theo tung xe.
PID_RESPONSE_TIMEOUT: Final[float] = 0.05

# Số ngày giữ lại dữ liệu telemetry trong DB.
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

# ── MODULE CẢNH BÁO & BẢO TRÌ (RULE-BASED) ────────────────
# Nhóm A1 - Cảnh báo kỹ thuật tức thời
THRESHOLD_COOLANT_CRITICAL: Final[float] = 105.0  # °C
THRESHOLD_LTFT_CRITICAL: Final[float] = 25.0      # % (Tuyệt đối: > 25 hoặc < -25)

# Nhóm A2 - Cảnh báo hành vi / An toàn giao thông
BEHAVIOR_SPEED_MAX: Final[float] = 120.0          # km/h
BEHAVIOR_THROTTLE_MAX: Final[float] = 90.0        # %
BEHAVIOR_THROTTLE_DURATION: Final[int] = 5        # Giây liên tục

# Nhóm B - Bảo dưỡng định kỳ
MAINTENANCE_SCHEDULE = {
    "oil_and_filter": {"interval_km": 5000,  "interval_days": 180, "interval_engine_hours": 200},
    "air_filter":     {"interval_km": 15000, "interval_days": 365, "interval_engine_hours": None},
    "spark_plug":     {"interval_km": 30000, "interval_days": 730, "interval_engine_hours": None},
    "gearbox_oil":    {"interval_km": 50000, "interval_days": 730, "interval_engine_hours": None},
    "brake_pad":      {"interval_km": 40000, "interval_days": None, "interval_engine_hours": None},
}

RULE_CHECK_RATE_HZ: Final[int] = 1  # Chạy check logic 1 giây/lần

# ── Telegram Bot ──────────────────────────────────
# Day chi la GIA TRI MAC DINH/FALLBACK. Gia tri that nen nhap qua Web UI
# (luu vao system_config trong DB) de portable - khong can sua file nay.
TELEGRAM_BOT_TOKEN: Final[str] = ""     # Lay tu @BotFather
TELEGRAM_CHAT_ID: Final[str]   = ""     # Lay tu getUpdates API
TELEGRAM_ENABLED: Final[bool]  = False  # True = bat (hoac suy ra tu "co token")

# Cooldown giua cac tin nhan cung loai (giay) - tranh spam
TELEGRAM_COOLDOWN_SEC: Final[int] = 300   # 5 phut cho canh bao thuong
TELEGRAM_MAINT_COOLDOWN_SEC: Final[int] = 3600  # 1 gio cho bao duong

HEALTH_EWMA_ALPHA_LAT: Final[float] = 0.2
HEALTH_EWMA_ALPHA_MISS: Final[float] = 0.1
HEALTH_PERSIST_SEC: Final[float] = 10.0
HEALTH_LATENCY_K: Final[float] = 4.0
HEALTH_MISS_RATE_MAX: Final[float] = 0.05
HEALTH_DEBOUNCE_CYCLES: Final[int] = 3
HEALTH_BASELINE = {}

# ===== Dang nhap Lab (doi mat khau o day) =====
LAB_PASSWORD = "bkauto2010"
