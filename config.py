import os
from pathlib import Path
from typing import Final

OPERATION_MODE: Final[str] = "PRODUCTION" # Đổi thành "PRODUCTION/SIMULATION" khi chạy thực tế hoặc giả lập
TIME_SOURCE: Final[str] = "INTERNET"   #  " INTERNET/RTC "

import os as _os
BASE_DIR = Path(__file__).resolve().parent
# STORAGE_DIR: uu tien bien moi truong (cho Docker tren server), fallback duong cu (Pi)
STORAGE_DIR = Path(_os.environ.get("VDR_STORAGE_DIR", str(BASE_DIR / "data")))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
# DATABASE_PATH: uu tien env DATABASE_PATH (Docker compose set /data/...), fallback STORAGE_DIR
DATABASE_PATH = Path(_os.environ.get("DATABASE_PATH", str(STORAGE_DIR / "telemetry_v1.db")))

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

# ── MQTT (Pi -> Server, qua WebSocket/WSS) ──────────
# Local de test; deploy thi set bang bien moi truong.
MQTT_HOST   = _os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT   = int(_os.environ.get("MQTT_PORT", "9001"))      # 9001 = listener websockets cua Mosquitto
MQTT_WS_PATH= _os.environ.get("MQTT_WS_PATH", "/mqtt")
MQTT_TLS    = _os.environ.get("MQTT_TLS", "0") == "1"        # 1 = wss qua nginx/Cloudflare
MQTT_TOPIC_PREFIX = _os.environ.get("MQTT_TOPIC_PREFIX", "vdr")
MQTT_DEVICE_ID    = _os.environ.get("MQTT_DEVICE_ID", "pi-01")
MQTT_USER   = _os.environ.get("MQTT_USER", "")             # rong = anonymous (dev)
MQTT_PASS   = _os.environ.get("MQTT_PASS", "")
MQTT_UPLOAD_INTERVAL_SEC = int(_os.environ.get("MQTT_UPLOAD_INTERVAL_SEC", "5"))
MQTT_LIVE_INTERVAL_SEC = int(_os.environ.get("MQTT_LIVE_INTERVAL_SEC", "60"))  # gian push_live rieng, push_events giu nguyen MQTT_UPLOAD_INTERVAL_SEC

# ===== Server nhan bang chung (video tai nan) =====
SERVER_API_URL = _os.environ.get("SERVER_API_URL", "http://localhost:8080/api")  # doi khi len server that
EVIDENCE_UPLOAD_INTERVAL_SEC = int(_os.environ.get("EVIDENCE_UPLOAD_INTERVAL_SEC", "20"))


# ===== OVERLAY ENGINE (video bang chung) =====
CAMERA_LATENCY_SEC: Final[float] = 0.2   # Tre camera (frame chup luc T, ghi file luc T+tre). Do lai khi doi camera.
EVIDENCE_PRE_SEC: Final[int] = 15        # Giay TRUOC moc va cham
EVIDENCE_POST_SEC: Final[int] = 15       # Giay SAU moc va cham


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

# ===== PHAT HIEN TAI NAN (Crash Detection) =====
CRASH_DETECTION_ENABLED: Final[bool] = True   # Bat/tat toan bo phat hien tai nan
# --- MPU-6050 (accelerometer + gyroscope), tu dong do; khong co thi chi dung OBD ---
CRASH_MPU_I2C_BUS: Final[int] = 5             # /dev/i2c-5 (doi neu MPU noi bus khac)
CRASH_MPU_I2C_ADDR: Final[int] = 0x68         # Dia chi mac dinh MPU-6050 (0x69 neu chan AD0=High)
CRASH_SAMPLE_RATE_HZ: Final[int] = 50         # Tan so doc MPU (va cham ~100ms -> can >=50Hz)
# --- Nguong G-force (don vi: g; 1g = trong luc binh thuong) ---
CRASH_GFORCE_THRESHOLD: Final[float] = 4.0    # Tong gia toc > 4g -> nghi va cham (nhe ~5g, vua 20g, nang 40g)
CRASH_GFORCE_SEVERE: Final[float] = 20.0      # > 20g -> tai nan nang
# --- Nguong lat xe (goc nghieng tu gyro/accel, do) ---
CRASH_TILT_THRESHOLD: Final[float] = 60.0     # Goc nghieng > 60 do -> nghi lat xe
# --- Xac nhan cheo bang OBD: toc do sut dot ngot ---
CRASH_SPEED_DROP_KMH: Final[float] = 40.0     # Sut > 40 km/h
CRASH_SPEED_DROP_WINDOW_SEC: Final[float] = 1.5  # Trong vong 1.5 giay -> nghi va cham
# --- Chong bao trung ---
CRASH_COOLDOWN_SEC: Final[int] = 30           # Sau 1 lan phat hien, cho 30s moi bao tiep


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

# Baseline MPU (dung yen) - EWMA rieng, cham hon HEALTH_EWMA de tranh nhay loan theo rung tam thoi
MPU_BASELINE_SPEED_MAX_KMH: Final[float] = 1.0   # duoi nguong nay coi la xe dung yen (loc nhieu cam bien toc do)
MPU_BASELINE_EWMA_ALPHA: Final[float] = 0.02      # cham hon HEALTH_EWMA_ALPHA_LAT (0.2) - baseline can on dinh
MPU_BASELINE_PERSIST_SEC: Final[float] = 30.0     # ghi DB moi 30s (MPU doc toi 50Hz, khong ghi moi lan doc)

# ── GIAI DOAN CHAN DOAN HOP NHAT (Dempster-Shafer) ──────────────────────
# Nguong KHOI TAO, CAN HIEU CHINH LAI sau khi co du lieu xe that
# (xem BANGIAO_CHAN_DOAN_HOP_NHAT.md muc 3.2-3.3).

# E1 - MAF vs RPM: dung tich dong co THAT SU cua xe thi nghiem (lit).
ENGINE_DISPLACEMENT_L: Final[float] = 1.5   # SUA DUNG THEO XE THAT DANG DUNG

# E1 - bien kiem tra hop ly vat ly (sanity bound), KHONG dung lam nguong
# chan doan chinh - baseline tu hoc moi la nguon chinh (muc 3.2).
E1_VE_MIN: Final[float] = 0.2
E1_VE_MAX: Final[float] = 1.0
E1_MAF_SANITY_MIN_RATIO: Final[float] = 0.2   # MAF do < 0.2x bien duoi -> chac chan sai
E1_MAF_SANITY_MAX_RATIO: Final[float] = 3.0   # MAF do > 3x bien tren -> chac chan sai
STANDARD_ATM_PRESSURE_PA: Final[float] = 101325.0
AIR_GAS_CONSTANT: Final[float] = 287.0        # J/(kg.K)

# Vung RPM dung cho quy trinh kiem tra 10 phut (muc 8) va chon baseline
# zone cho E1/E2 (garanti vs 2500 vong).
RPM_IDLE_MIN: Final[float] = 600.0
RPM_IDLE_MAX: Final[float] = 1000.0
RPM_2500_TARGET: Final[float] = 2500.0
RPM_2500_TOLERANCE: Final[float] = 200.0

# E2 - LTFT garanti vs 2500 vong: nguong phan biet MAF_DEGRADED / INTAKE_LEAK
# (muc 3.3). GIA TRI KHOI TAO, phai hieu chinh sau khi co du lieu xe that.
E2_DELTA_INTAKE_LEAK_THRESHOLD: Final[float] = 8.0   # %
E2_DELTA_MAF_THRESHOLD: Final[float] = 3.0            # %

# May da am - moi bang chung dua tren LTFT phai cho qua nguong nay
# (cam bay #3 muc 13: LTFT luc may lanh vo nghia vi ECU chay vong ho).
ECT_WARM_THRESHOLD: Final[float] = 80.0   # C

# E3 - IAT vs ECT luc khoi dong nguoi (muc 3.4)
E3_DELTA_NORMAL_MAX: Final[float] = 5.0    # C, duoi muc nay -> ca hai binh thuong
E3_DELTA_FAULT_MIN: Final[float] = 10.0    # C, tren muc nay -> mot trong hai loi
VN_AMBIENT_TEMP_MIN: Final[float] = 15.0   # C, dai nhiet do moi truong hop ly o VN
VN_AMBIENT_TEMP_MAX: Final[float] = 42.0   # C

# E4 - Duong cong ham nong (muc 3.5). 12 phut = nguong OEM lien quan P0128.
WARMUP_T80_MAX_SEC: Final[float] = 720.0   # qua moc nay ma chua dat 80C -> nghi thermostat

# ===== Dang nhap Lab: uu tien bien moi truong LAB_PASSWORD, khong co gia tri mac dinh =====
LAB_PASSWORD = _os.environ.get("LAB_PASSWORD", "")

# ===== Phien dang nhap (session token) - xem auth.py =====
SESSION_TTL_SEC = int(_os.environ.get("SESSION_TTL_SEC", str(7 * 24 * 3600)))  # 7 ngay
LOGIN_MAX_FAIL = int(_os.environ.get("LOGIN_MAX_FAIL", "5"))
LOGIN_LOCK_SEC = int(_os.environ.get("LOGIN_LOCK_SEC", "60"))
DEVICE_API_KEY = _os.environ.get("DEVICE_API_KEY", "")  # cho may-voi-may (evidence_uploader -> server)
