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

    # Doc thang bang pid_health (OBD_Process dang chay ghi lien tuc) - KHONG tu mo CAN
    # bus rieng nua, tranh xung dot voi OBD_Process dang giu bus that.
    try:
        import sqlite3
        from config import (DATABASE_PATH, PID_RESPONSE_TIMEOUT, SAMPLING_RATE_HZ,
                            HEALTH_MISS_RATE_MAX)
        conn = sqlite3.connect(str(DATABASE_PATH))
        rows = conn.execute(
            "SELECT pid, ewma_latency_ms, ewma_miss_rate, updated_at FROM pid_health").fetchall()
        conn.close()
    except Exception as e:
        log_check("doc pid_health", "FAIL", str(e))
        return

    if not rows:
        log_check("doc pid_health", "WARN",
                   "chưa có số liệu - cần main.py chạy một lúc để OBD_Process gom dữ liệu")
        return

    now = time.time()
    usable_lat = []
    n_usable = 0
    n_stale = 0
    for pid, ewma_lat, ewma_miss, updated_at in rows:
        if updated_at is None or (now - updated_at) > 120:
            n_stale += 1
            continue
        if ewma_lat is None:
            continue
        if ewma_miss is not None and ewma_miss > HEALTH_MISS_RATE_MAX:
            continue
        usable_lat.append(ewma_lat)
        n_usable += 1

    if n_stale:
        log_check("pid_health", "INFO", f"{n_stale} PID số liệu cũ (>2 phút) - bỏ qua")

    if not usable_lat:
        log_check("do PID", "WARN", "chưa đủ số liệu đáng tin - chạy main.py lâu hơn rồi thử lại")
        return

    worst_lat = max(usable_lat)
    # He so an toan cao hon ban CLI cu (1.5x): day la EWMA (trung binh truot),
    # khong phai p95 do truc tiep, can du them de bu cac lan phan hoi cham bat thuong.
    new_timeout = max(round(worst_lat * 2.2 / 1000.0, 3), 0.05)
    safe_cycle = n_usable * new_timeout * 1.2
    new_hz = max(1, int(1.0 / safe_cycle)) if safe_cycle > 0 else 1
    log_check("do PID", "OK", f"{n_usable} PID, EWMA max={worst_lat:.0f}ms (nguồn: pid_health)")
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
        from config import DATABASE_PATH
    except Exception as e:
        log_check("config MPU", "FAIL", str(e))
        return

    # Doc baseline tu bang mpu_baseline (Crash_Process ghi lien tuc neu co MPU tren
    # dung bus da cau hinh) - KHONG tu mo I2C do baseline 50 mau nua, tranh xung dot.
    row = None
    try:
        import sqlite3
        conn = sqlite3.connect(str(DATABASE_PATH))
        row = conn.execute(
            "SELECT g_mean, g_std, sample_count, updated_at FROM mpu_baseline WHERE id=1"
        ).fetchone()
        conn.close()
    except Exception as e:
        log_check("doc mpu_baseline", "FAIL", str(e))

    now = time.time()
    if row and row[3] and (now - row[3]) < 300 and row[2] and row[2] >= 20:
        base_mean, base_std, sample_count, updated_at = row
        noise_peak = base_mean + 5 * base_std
        log_check("MPU baseline", "OK",
                  f"nghỉ={base_mean:.2f}g nhiễu={base_std:.3f}g ({sample_count} mẫu, nguồn: mpu_baseline)")
        if not config.CRASH_DETECTION_ENABLED:
            proposals["CRASH_DETECTION_ENABLED"] = ("False", "True", True)
        suggested_thr = max(round(noise_peak + 2.0, 1), 3.0)
        if abs(suggested_thr - config.CRASH_GFORCE_THRESHOLD) > 0.5:
            proposals["CRASH_GFORCE_THRESHOLD"] = (
                str(config.CRASH_GFORCE_THRESHOLD), str(suggested_thr), suggested_thr)
        return

    # Chua co baseline du tin cay (moi cam / qua it mau / qua cu) -> do nhanh WHO_AM_I
    # tren nhieu bus de biet co MPU khong va co dung bus cau hinh khong. Chi doc 1 byte
    # moi bus, khong giu bus lau nhu vong do baseline cu.
    try:
        from smbus2 import SMBus
    except Exception as e:
        log_check("smbus2", "FAIL", str(e))
        return

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
            bus.close()
            if who in (0x68, 0x70, 0x72, 0x71, 0x69, 0x98):
                found_bus = b
                log_check("tìm MPU", "OK", f"I2C-{b} WHO_AM_I=0x{who:02X}")
                if config.CRASH_MPU_I2C_BUS != b:
                    proposals["CRASH_MPU_I2C_BUS"] = (str(config.CRASH_MPU_I2C_BUS), str(b), b)
                break
        except Exception:
            try:
                bus.close()
            except Exception:
                pass
            continue

    if found_bus is None:
        log_check("tìm MPU", "WARN", "không thấy MPU - phát hiện tai nạn sẽ chỉ dùng OBD")
    else:
        log_check("MPU baseline", "INFO",
                  "chưa đủ mẫu tin cậy - để main.py chạy lâu hơn (xe đứng yên) rồi thử lại")

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
def check_rtc():
    print("\n[7] KIỂM TRA ĐỒNG HỒ (RTC / NTP)")
    rtc_path = "/dev/rtc0" if os.path.exists("/dev/rtc0") else (
        "/dev/rtc" if os.path.exists("/dev/rtc") else None)
    if rtc_path is None:
        log_check("RTC phần cứng", "WARN", "không có /dev/rtc - phụ thuộc hoàn toàn NTP lúc khởi động")
    else:
        try:
            fd = os.open(rtc_path, os.O_RDONLY)
            os.close(fd)
            log_check("RTC phần cứng", "OK", f"{rtc_path} tồn tại và đọc được")
        except PermissionError:
            log_check("RTC phần cứng", "WARN",
                      f"{rtc_path} tồn tại nhưng không đủ quyền đọc - không xác nhận được pin còn tốt hay không")
        except OSError as e:
            log_check("RTC phần cứng", "WARN", f"{rtc_path} tồn tại nhưng lỗi khi đọc ({e}) - nghi pin hỏng")
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=5)
        synced = out.stdout.strip() == "yes"
        log_check("NTP đồng bộ", "OK" if synced else "WARN",
                  "hệ thống đang đồng bộ qua NTP - bù được việc thiếu RTC" if synced
                  else "chưa đồng bộ - kiểm tra mạng/chrony trước khi tin giờ hệ thống")
    except Exception as e:
        log_check("NTP đồng bộ", "WARN", f"không kiểm tra được: {str(e)[:40]}")


# ══════════════════════════════════════════
def apply_proposals():
    """Ghi cac de xuat trong proposals[] xuong config.py. KHONG hoi xac nhan -
    goi ham nay nghia la da duoc xac nhan roi (CLI hoi truoc khi goi trong main(),
    web thi nguoi dung bam nut Ap dung sau khi xem bang de xuat).
    Tra ve dict: {"applied": bool, "detail": [str,...], "error": str|None}."""
    detail = []
    if not proposals:
        detail.append("Không có thông số nào cần chỉnh.")
        return {"applied": False, "detail": detail, "error": None}

    src = open(CONFIG_PATH, encoding="utf-8").read()
    bak = CONFIG_PATH + ".bak.autocal"
    if not os.path.exists(bak):
        shutil.copy(CONFIG_PATH, bak)
        detail.append(f"Backup: {bak}")

    for name, (old, new, _) in proposals.items():
        pattern = rf"({name}\s*:\s*Final\[\w+\]\s*=\s*)([^\s#]+)"
        new_src = re.sub(pattern, rf"\g<1>{new}", src, count=1)
        if new_src != src:
            detail.append(f"{name} -> {new}")
            src = new_src
        else:
            detail.append(f"Không tìm thấy {name} - bỏ qua")

    try:
        import ast
        ast.parse(src)
    except SyntaxError as e:
        err = f"Config mới sai cú pháp ({e}). KHÔNG ghi. Backup vẫn còn."
        detail.append(err)
        return {"applied": False, "detail": detail, "error": err}

    open(CONFIG_PATH, "w", encoding="utf-8").write(src)
    detail.append("Đã ghi config.py. Cần khởi động lại main.py để áp dụng (không tự restart).")

    last = get_last_run()
    if last is not None:
        last["applied"] = True
        _save_last_run(last)

    return {"applied": True, "detail": detail, "error": None}


LAST_RUN_PATH = os.path.join(PROJECT_ROOT, "auto_calibration_last_run.json")


def _save_last_run(result):
    try:
        import json
        with open(LAST_RUN_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Không lưu được kết quả lần chạy gần nhất: {e}")


def get_last_run():
    """Đọc kết quả lần chạy gần nhất từ file (cho route /api/calibration/last)."""
    try:
        import json
        with open(LAST_RUN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def run_checks_and_measure():
    """Chạy toàn bộ self-check + đo lường, KHÔNG hỏi input(), KHÔNG ghi config.
    Reset checks[]/proposals{} trước khi chạy (gọi lại nhiều lần qua web an toàn).
    Trả về dict kết quả, đồng thời lưu xuống file cho get_last_run()."""
    checks.clear()
    proposals.clear()

    print("=" * 64)
    print("=== VDR AUTO-CALIBRATION - TỰ KIỂM TRA & HIỆU CHỈNH ===")
    print("=" * 64)
    check_dependencies()
    check_database()
    check_and_calibrate_obd()
    check_and_calibrate_camera()
    check_and_calibrate_mpu()
    check_connectivity()
    check_rtc()

    n_fail = sum(1 for _, s, _ in checks if s == "FAIL")
    n_warn = sum(1 for _, s, _ in checks if s == "WARN")
    n_ok = sum(1 for _, s, _ in checks if s == "OK")
    print(f"\nSelf-check: {n_ok} OK | {n_warn} WARN | {n_fail} FAIL")

    result = {
        "timestamp": time.time(),
        "checks": [{"name": n, "status": st, "note": note} for (n, st, note) in checks],
        "proposals": [{"name": name, "old": old, "new": new} for name, (old, new, _) in proposals.items()],
        "applied": False,
    }
    _save_last_run(result)
    return result


def main():
    run_checks_and_measure()
    print("\n" + "=" * 64)
    print("=== BẢNG ĐỀ XUẤT HIỆU CHỈNH ===")
    print("=" * 64)
    if not proposals:
        print("\n[*] Không có thông số nào cần chỉnh (mọi thứ đã tối ưu hoặc thiếu phần cứng để đo).")
        print("\n=== HOÀN TẤT AUTO-CALIBRATION ===")
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
        print("\n=== HOÀN TẤT AUTO-CALIBRATION ===")
        return

    apply_result = apply_proposals()
    for line in apply_result["detail"]:
        print(f"[+] {line}")
    print("\n=== HOÀN TẤT AUTO-CALIBRATION ===")


if __name__ == "__main__":
    main()
