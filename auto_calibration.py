"""
auto_calibration.py - Tu hieu chinh + tu kiem tra phan cung cho VDR.
Chay 1 lan luc setup hoac khi doi xe/phan cung:
    python auto_calibration.py

Luong:
  1. SELF-CHECK phan cung (CAN/OBD, Camera, MPU, DB, dependencies, internet)
  2. DO de hieu chinh: PID timeout + sampling rate, camera fps/latency, MPU baseline
  3. IN bang de xuat (gia tri cu -> moi)
  4. Hoi [y/N] -> y: backup + ghi thang config.py (verify ast.parse) | N: khong lam gi
"""
import os
import sys
import re
import time
import math
import shutil
import socket
import sqlite3
import subprocess
import statistics

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.py")

# Ket qua hieu chinh se ghi (ten hang so -> gia tri moi). Dien dan trong qua trinh do.
proposals = {}      # {const_name: (old_repr, new_repr, new_value)}
checks = []         # [(ten_muc, trang_thai, ghi_chu)]  trang_thai: OK/WARN/FAIL


def log_check(name, status, note=""):
    checks.append((name, status, note))
    icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌", "INFO": "ℹ️ "}.get(status, "  ")
    print(f"  {icon} {name:32} {note}")


# ══════════════════════════════════════════
# [1] SELF-CHECK DEPENDENCIES
# ══════════════════════════════════════════
def check_dependencies():
    print("\n[1] KIỂM TRA THƯ VIỆN & FILE")
    for mod in ["cv2", "smbus2", "pandas", "numpy", "can", "PIL", "google.auth"]:
        try:
            __import__(mod)
            log_check(f"lib {mod}", "OK")
        except Exception:
            log_check(f"lib {mod}", "FAIL", "thieu -> pip install")
    for tool in ["ffmpeg", "ffprobe"]:
        if shutil.which(tool):
            log_check(f"tool {tool}", "OK")
        else:
            log_check(f"tool {tool}", "FAIL", "khong tim thay")
    for fname in ["firebase_key.json", "config.py", "main.py"]:
        p = os.path.join(PROJECT_ROOT, fname)
        log_check(f"file {fname}", "OK" if os.path.exists(p) else "WARN",
                  "" if os.path.exists(p) else "khong co")


# ══════════════════════════════════════════
# [2] DATABASE
# ══════════════════════════════════════════
def check_database():
    print("\n[2] KIỂM TRA DATABASE")
    try:
        import config
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        need = ["obd_data", "maintenance_schedule", "system_config",
                "device_tokens", "crash_events"]
        for t in need:
            if t in tables:
                log_check(f"bang {t}", "OK")
            else:
                log_check(f"bang {t}", "WARN", "chua co (se tao khi chay main)")
        conn.close()
    except Exception as e:
        log_check("mo DB", "FAIL", str(e))


# ══════════════════════════════════════════
# [3] CAN / OBD  (gop P0 + P2)
# ══════════════════════════════════════════
def check_and_calibrate_obd():
    print("\n[3] KIỂM TRA + HIỆU CHỈNH CAN/OBD")
    try:
        import config
        if config.OPERATION_MODE == "SIMULATION":
            log_check("CAN/OBD", "INFO", "đang SIMULATION - bỏ qua đo PID")
            return
    except Exception:
        pass

    iface = None
    try:
        import config
        iface = config.CAN_INTERFACE
        if not os.path.exists(iface):
            log_check(f"cong {iface}", "FAIL", "không tồn tại - kiểm tra dây USB CAN")
            return
        log_check(f"cong {iface}", "OK")
    except Exception as e:
        log_check("config CAN", "FAIL", str(e))
        return

    # Do PID timeout + sampling (rut gon tu P5: 30 mau/PID cho nhanh)
    try:
        import can
        from config import (CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE,
                            OBD_REQUEST_ID, OBD_RESPONSE_ID, TELEMETRY_SCHEMA,
                            PID_RESPONSE_TIMEOUT, SAMPLING_RATE_HZ)
        bus = can.interface.Bus(channel=CAN_INTERFACE, bustype=CAN_BUS_TYPE, bitrate=CAN_BITRATE)
    except Exception as e:
        log_check("mo CAN bus", "FAIL", str(e))
        return

    SAMPLES = 30
    usable_p95 = []
    n_usable = 0
    for pid in TELEMETRY_SCHEMA:
        lat = []
        for _ in range(SAMPLES):
            while bus.recv(timeout=0) is not None:
                pass
            req = can.Message(arbitration_id=OBD_REQUEST_ID,
                              data=[0x02, 0x01, pid, 0, 0, 0, 0, 0], is_extended_id=False)
            t0 = time.monotonic()
            try:
                bus.send(req)
            except Exception:
                continue
            deadline = t0 + 0.5
            while time.monotonic() < deadline:
                msg = bus.recv(timeout=0.01)
                if msg and msg.arbitration_id == OBD_RESPONSE_ID and len(msg.data) >= 3 \
                        and msg.data[1] == 0x41 and msg.data[2] == pid:
                    lat.append((time.monotonic() - t0) * 1000.0)
                    break
            time.sleep(0.01)
        if len(lat) >= SAMPLES * 0.5:
            n_usable += 1
            ls = sorted(lat)
            usable_p95.append(ls[int(len(ls) * 0.95) - 1] if len(ls) >= 5 else max(ls))
    bus.shutdown()

    if not usable_p95:
        log_check("do PID", "FAIL", "không PID nào trả lời - xe đã nổ máy chưa?")
        return

    worst_p95 = max(usable_p95)
    new_timeout = max(round(worst_p95 * 1.5 / 1000.0, 3), 0.05)
    safe_cycle = n_usable * new_timeout * 1.2
    new_hz = max(1, int(1.0 / safe_cycle)) if safe_cycle > 0 else 1
    log_check("do PID", "OK", f"{n_usable} PID, p95 max={worst_p95:.0f}ms")
    proposals["PID_RESPONSE_TIMEOUT"] = (str(PID_RESPONSE_TIMEOUT), str(new_timeout), new_timeout)
    proposals["SAMPLING_RATE_HZ"] = (str(SAMPLING_RATE_HZ), str(new_hz), new_hz)


# ══════════════════════════════════════════
# [4] CAMERA  (gop P4 + do fps that + latency)
# ══════════════════════════════════════════
def check_and_calibrate_camera():
    print("\n[4] KIỂM TRA + HIỆU CHỈNH CAMERA")
    try:
        import config, cv2
        src = config.VIDEO_SOURCE
    except Exception as e:
        log_check("config camera", "FAIL", str(e))
        return

    # Ping camera neu la RTSP
    if str(src).startswith("rtsp"):
        m = re.search(r"@?(\d+\.\d+\.\d+\.\d+)", src)
        if not m:
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", src)
        if m:
            ip = m.group(1)
            r = subprocess.run(["ping", "-c", "1", "-W", "2", ip],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log_check(f"ping camera {ip}", "OK" if r.returncode == 0 else "FAIL")

    # Mo thu xem co ket noi khong
    cap = cv2.VideoCapture(src)
    opened = cap.isOpened()
    cap.release()
    if not opened:
        log_check("mo luong camera", "FAIL", "không mở được")
        return
    log_check("mo luong camera", "OK")
    # Do fps that bang ffprobe (doc metadata stream, chinh xac hon dem frame)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "0", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
             "-rtsp_transport", "tcp", src],
            capture_output=True, text=True, timeout=15)
        raw = out.stdout.strip()  # vd "12/1"
        if "/" in raw:
            a, b = raw.split("/")
            measured_fps = float(a) / float(b) if float(b) else 0
        else:
            measured_fps = float(raw) if raw else 0
        if measured_fps > 0:
            log_check("camera fps", "OK", f"{measured_fps:.0f} fps (ffprobe metadata)")
        else:
            log_check("camera fps", "WARN", "không đọc được fps từ metadata")
    except Exception as e:
        log_check("camera fps", "WARN", f"ffprobe loi: {str(e)[:30]}")
    # Khong tu sua CAMERA_LATENCY (can do bang phuong phap khac), chi bao
    log_check("camera latency", "INFO", "giữ CAMERA_LATENCY_SEC hiện tại (đo tay khi cần)")


# ══════════════════════════════════════════
# [5] MPU-6050  (do + baseline)
# ══════════════════════════════════════════
def check_and_calibrate_mpu():
    print("\n[5] KIỂM TRA + HIỆU CHỈNH MPU-6050")
    try:
        import config
        from smbus2 import SMBus
    except Exception as e:
        log_check("smbus2", "FAIL", str(e))
        return

    # Quet cac bus I2C tìm MPU (WHO_AM_I = 0x75)
    candidate_buses = [config.CRASH_MPU_I2C_BUS, 5, 7, 8, 9, 1, 13, 15, 20, 0, 2, 3]
    addr = config.CRASH_MPU_I2C_ADDR
    found_bus = None
    seen = set()
    for b in candidate_buses:
        if b in seen:
            continue
        seen.add(b)
        try:
            bus = SMBus(b)
            who = bus.read_byte_data(addr, 0x75)
            if who in (0x68, 0x70, 0x72, 0x71, 0x69, 0x98):
                found_bus = b
                # danh thuc
                bus.write_byte_data(addr, 0x6B, 0)
                time.sleep(0.1)
                log_check("tìm MPU", "OK", f"I2C-{b} WHO_AM_I=0x{who:02X}")
                # Do baseline G luc dung yen (50 mau)
                samples = []
                for _ in range(50):
                    d = bus.read_i2c_block_data(addr, 0x3B, 6)
                    def sgn(h, l):
                        v = (h << 8) | l
                        return v - 65536 if v >= 0x8000 else v
                    ax = sgn(d[0], d[1]) / 16384.0
                    ay = sgn(d[2], d[3]) / 16384.0
                    az = sgn(d[4], d[5]) / 16384.0
                    samples.append(math.sqrt(ax*ax + ay*ay + az*az))
                    time.sleep(0.01)
                base_mean = statistics.mean(samples)
                base_std = statistics.pstdev(samples)
                noise_peak = base_mean + 5 * base_std    # dinh nhieu nen
                log_check("MPU baseline", "OK",
                          f"nghỉ={base_mean:.2f}g nhiễu={base_std:.3f}g")
                bus.close()
                # De xuat: bat crash detection + ghi dung bus
                import config as cfg
                if cfg.CRASH_MPU_I2C_BUS != b:
                    proposals["CRASH_MPU_I2C_BUS"] = (str(cfg.CRASH_MPU_I2C_BUS), str(b), b)
                if not cfg.CRASH_DETECTION_ENABLED:
                    proposals["CRASH_DETECTION_ENABLED"] = ("False", "True", True)
                # Nguong G nhe khong nen thap hon dinh nhieu (tranh bao nham)
                suggested_thr = max(round(noise_peak + 2.0, 1), 3.0)
                if abs(suggested_thr - cfg.CRASH_GFORCE_THRESHOLD) > 0.5:
                    proposals["CRASH_GFORCE_THRESHOLD"] = (
                        str(cfg.CRASH_GFORCE_THRESHOLD), str(suggested_thr), suggested_thr)
                break
            bus.close()
        except Exception:
            try:
                bus.close()
            except Exception:
                pass
            continue

    if found_bus is None:
        log_check("tìm MPU", "WARN", "không thấy MPU - phát hiện tai nạn sẽ chỉ dùng OBD")
        try:
            import config as cfg
            if cfg.CRASH_DETECTION_ENABLED:
                # Van bat (che do OBD-only) - khong ep tat
                log_check("crash mode", "INFO", "chỉ-OBD (chưa gắn MPU)")
        except Exception:
            pass


# ══════════════════════════════════════════
# [6] INTERNET / FIREBASE
# ══════════════════════════════════════════
def check_connectivity():
    print("\n[6] KIỂM TRA KẾT NỐI")
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        log_check("internet", "OK")
    except Exception:
        log_check("internet", "WARN", "không có mạng - FCM/NTP sẽ lỗi")
        return
    # Thu Firebase auth
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gtr
        key = os.path.join(PROJECT_ROOT, "firebase_key.json")
        if os.path.exists(key):
            cred = service_account.Credentials.from_service_account_file(
                key, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
            cred.refresh(gtr.Request())
            log_check("Firebase auth", "OK" if cred.token else "WARN")
        else:
            log_check("Firebase auth", "WARN", "không có firebase_key.json")
    except Exception as e:
        log_check("Firebase auth", "WARN", str(e)[:40])


# ══════════════════════════════════════════
# GHI CONFIG (style P5: hoi y/N, backup, verify)
# ══════════════════════════════════════════
def write_config():
    print("\n" + "=" * 64)
    print("=== BẢNG ĐỀ XUẤT HIỆU CHỈNH ===")
    print("=" * 64)

    # Tong ket self-check
    n_fail = sum(1 for _, s, _ in checks if s == "FAIL")
    n_warn = sum(1 for _, s, _ in checks if s == "WARN")
    n_ok = sum(1 for _, s, _ in checks if s == "OK")
    print(f"Self-check: {n_ok} OK | {n_warn} WARN | {n_fail} FAIL")

    if not proposals:
        print("\n[*] Không có thông số nào cần chỉnh (mọi thứ đã tối ưu hoặc thiếu phần cứng để đo).")
        return

    print("\nThông số sẽ ghi vào config.py:")
    for name, (old, new, _) in proposals.items():
        print(f"  {name:28} {old:>8}  ->  {new}")

    try:
        answer = input("\n>>> Xác nhận ghi đè config.py? [y/N]: ").strip().lower()
    except EOFError:
        answer = "n"
    if answer != "y":
        print("[*] Bỏ qua. Không sửa gì.")
        return

    # Backup
    src = open(CONFIG_PATH, encoding="utf-8").read()
    bak = CONFIG_PATH + ".bak.autocal"
    if not os.path.exists(bak):
        shutil.copy(CONFIG_PATH, bak)
        print(f"[+] Backup: {bak}")

    # Thay tung hang so (giu nguyen comment phia sau neu co)
    for name, (old, new, _) in proposals.items():
        # khop:  NAME: Final[...] = <gia tri>   (khong dung phan comment)
        pattern = rf"({name}\s*:\s*Final\[\w+\]\s*=\s*)([^\s#]+)"
        new_src = re.sub(pattern, rf"\g<1>{new}", src, count=1)
        if new_src != src:
            print(f"[+] {name} -> {new}")
            src = new_src
        else:
            print(f"[!] Không tìm thấy {name} - bỏ qua")

    # Verify cu phap truoc khi ghi
    try:
        import ast
        ast.parse(src)
    except SyntaxError as e:
        print(f"[-] LỖI: config mới sai cú pháp ({e}). KHÔNG ghi. Backup vẫn còn.")
        return

    open(CONFIG_PATH, "w", encoding="utf-8").write(src)
    print("[+] Đã ghi config.py. Khởi động lại main.py để áp dụng.")


def main():
    print("=" * 64)
    print("=== VDR AUTO-CALIBRATION - TỰ KIỂM TRA & HIỆU CHỈNH ===")
    print("=" * 64)
    check_dependencies()
    check_database()
    check_and_calibrate_obd()
    check_and_calibrate_camera()
    check_and_calibrate_mpu()
    check_connectivity()
    write_config()
    print("\n=== HOÀN TẤT AUTO-CALIBRATION ===")


if __name__ == "__main__":
    main()