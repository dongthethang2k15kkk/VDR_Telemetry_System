"""
crash_detector.py - Phat hien tai nan cho VDR.
Tu dong do MPU-6050 (accelerometer + gyroscope) qua I2C:
  - Co MPU  -> dung G-force + goc nghieng + xac nhan cheo bang OBD speed-drop
  - Khong   -> chi dung OBD speed-drop (yeu hon, nhung van chay)
Khi phat hien -> luu crash_events + render video bang chung + day FCM.
Chay daemon thread rieng (tan so cao), khong vuong nhip 1Hz cua RuleEngine.
"""
import time
import math
import sqlite3
import threading

from config import (
    DATABASE_PATH,
    CRASH_DETECTION_ENABLED,
    CRASH_MPU_I2C_BUS, CRASH_MPU_I2C_ADDR, CRASH_SAMPLE_RATE_HZ,
    CRASH_GFORCE_THRESHOLD, CRASH_GFORCE_SEVERE, CRASH_TILT_THRESHOLD,
    CRASH_SPEED_DROP_KMH, CRASH_SPEED_DROP_WINDOW_SEC, CRASH_COOLDOWN_SEC,
    MPU_BASELINE_SPEED_MAX_KMH, MPU_BASELINE_EWMA_ALPHA, MPU_BASELINE_PERSIST_SEC,
)

# Thanh ghi MPU-6050
_MPU_WHO_AM_I = 0x75
_MPU_PWR_MGMT_1 = 0x6B
_MPU_ACCEL_XOUT_H = 0x3B
_ACCEL_SCALE = 16384.0  # LSB/g o range +-2g (mac dinh)


class CrashDetector:
    def __init__(self):
        self.running = False
        self.bus = None
        self.has_mpu = False
        self._last_crash_time = 0.0
        # speed-drop tracking
        self._speed_history = []  # [(t, speed), ...]
        # Baseline MPU EWMA (dung yen) - cho auto-calibration doc, khong luu list mau
        self._mb_g_ewma = None
        self._mb_g2_ewma = None
        self._mb_sample_count = 0
        self._mb_last_persist = 0.0
        self._mb_current_speed = None
        # DB connection rieng (doc obd_data)
        self.conn = sqlite3.connect(str(DATABASE_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_table()

    # ---------- Setup ----------
    def _ensure_table(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS crash_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp_sec REAL, severity TEXT, gforce REAL, "
            "tilt REAL, speed_before REAL, source TEXT, evidence_path TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS mpu_baseline ("
            "id INTEGER PRIMARY KEY, g_mean REAL, g_std REAL, "
            "sample_count INTEGER, updated_at REAL)"
        )
        self.conn.commit()

    def _detect_mpu(self):
        """Thu mo I2C + doc WHO_AM_I. Tra True neu thay MPU-6050."""
        try:
            from smbus2 import SMBus
            self.bus = SMBus(CRASH_MPU_I2C_BUS)
            who = self.bus.read_byte_data(CRASH_MPU_I2C_ADDR, _MPU_WHO_AM_I)
            # MPU-6050 tra 0x68 (mot so clone tra 0x68/0x70/0x72)
            if who in (0x68, 0x70, 0x72, 0x71, 0x69, 0x98):
                # Danh thuc MPU (clear sleep bit)
                self.bus.write_byte_data(CRASH_MPU_I2C_ADDR, _MPU_PWR_MGMT_1, 0)
                print(f"✅ [CRASH] Phát hiện MPU-6050 tại I2C-{CRASH_MPU_I2C_BUS} (WHO_AM_I=0x{who:02X})")
                return True
            print(f"⚠️  [CRASH] I2C-{CRASH_MPU_I2C_BUS} có thiết bị nhưng WHO_AM_I=0x{who:02X} (không phải MPU)")
            return False
        except Exception as e:
            print(f"ℹ️  [CRASH] Không thấy MPU ({e}). Chuyển chế độ chỉ-OBD.")
            if self.bus:
                try:
                    self.bus.close()
                except Exception:
                    pass
                self.bus = None
            return False

    # ---------- Doc cam bien ----------
    @staticmethod
    def _to_signed(high, low):
        v = (high << 8) | low
        return v - 65536 if v >= 0x8000 else v

    def _read_mpu(self):
        """Tra (g_total, tilt_deg). Loi -> (None, None)."""
        try:
            data = self.bus.read_i2c_block_data(CRASH_MPU_I2C_ADDR, _MPU_ACCEL_XOUT_H, 6)
            ax = self._to_signed(data[0], data[1]) / _ACCEL_SCALE
            ay = self._to_signed(data[2], data[3]) / _ACCEL_SCALE
            az = self._to_signed(data[4], data[5]) / _ACCEL_SCALE
            g_total = math.sqrt(ax * ax + ay * ay + az * az)
            # Goc nghieng so voi phuong thang dung (truc z huong len ~1g khi xe phang)
            tilt = math.degrees(math.acos(max(-1.0, min(1.0, abs(az) / max(g_total, 1e-6)))))
            return g_total, tilt
        except Exception:
            return None, None

    def _get_current_speed(self):
        """Doc toc do moi nhat tu obd_data (PID 0x0D)."""
        try:
            r = self.conn.execute(
                "SELECT value FROM obd_data WHERE pid=? ORDER BY timestamp_sec DESC LIMIT 1",
                ("0xd",)
            ).fetchone()
            if r is None:
                # thu dang hex khac
                r = self.conn.execute(
                    "SELECT value FROM obd_data WHERE pid_name='Vehicle Speed' ORDER BY timestamp_sec DESC LIMIT 1"
                ).fetchone()
            return float(r[0]) if r else None
        except Exception:
            return None

    def _update_mpu_baseline(self, gforce, now):
        """EWMA baseline G luc xe dung yen (loc theo MPU_BASELINE_SPEED_MAX_KMH).
        Khong luu list mau - chi giu EWMA(G) va EWMA(G^2) de suy ra do lech chuan.
        Ghi dinh ky xuong bang mpu_baseline (1 dong co dinh) cho auto-calibration doc."""
        speed = self._mb_current_speed
        if speed is None or speed > MPU_BASELINE_SPEED_MAX_KMH:
            return  # xe dang chay hoac chua ro toc do -> bo qua, tranh lech baseline
        a = MPU_BASELINE_EWMA_ALPHA
        self._mb_g_ewma = gforce if self._mb_g_ewma is None else (1 - a) * self._mb_g_ewma + a * gforce
        g2 = gforce * gforce
        self._mb_g2_ewma = g2 if self._mb_g2_ewma is None else (1 - a) * self._mb_g2_ewma + a * g2
        self._mb_sample_count += 1

        if now - self._mb_last_persist < MPU_BASELINE_PERSIST_SEC:
            return
        self._mb_last_persist = now
        try:
            variance = max(0.0, self._mb_g2_ewma - self._mb_g_ewma ** 2)
            std = variance ** 0.5
            self.conn.execute(
                "INSERT OR REPLACE INTO mpu_baseline (id, g_mean, g_std, sample_count, updated_at) VALUES (1,?,?,?,?)",
                (self._mb_g_ewma, std, self._mb_sample_count, now))
            self.conn.commit()
        except Exception as e:
            print(f"[MPU BASELINE] Lỗi lưu chỉ số: {e}")

    def _check_speed_drop(self, now):
        """Tra speed_before neu toc do sut dot ngot, else None."""
        speed = self._get_current_speed()
        if speed is None:
            return None
        self._speed_history.append((now, speed))
        # giu cua so
        cutoff = now - CRASH_SPEED_DROP_WINDOW_SEC
        self._speed_history = [(t, s) for (t, s) in self._speed_history if t >= cutoff]
        if len(self._speed_history) < 2:
            return None
        speed_max = max(s for (_, s) in self._speed_history)
        if (speed_max - speed) >= CRASH_SPEED_DROP_KMH:
            return speed_max
        return None

    # ---------- Xu ly khi crash ----------
    def _save_evidence_package(self, crash_time):
        """Cat video tho + dump telemetry 30s quanh va cham, luu LOCAL. Khong render."""
        import glob, json, subprocess
        from pathlib import Path
        from config import STORAGE_DIR, EVIDENCE_PRE_SEC, EVIDENCE_POST_SEC, CAMERA_LATENCY_SEC
        t_start = crash_time - EVIDENCE_PRE_SEC
        t_end = crash_time + EVIDENCE_POST_SEC
        # Doi camera ghi du phan sau va cham (UPS giu dien)
        import time as _t
        wait_until = t_end + CAMERA_LATENCY_SEC + 2
        if _t.time() < wait_until:
            _t.sleep(max(0, wait_until - _t.time()))

        out_dir = Path(STORAGE_DIR) / f"evidence_{int(crash_time)}"
        out_dir.mkdir(exist_ok=True)

        # --- 1. Dump telemetry JSON (chot data, khong cat ca DB) ---
        rows = self.conn.execute(
            "SELECT timestamp_sec, pid_name, value FROM obd_data "
            "WHERE timestamp_sec BETWEEN ? AND ? ORDER BY timestamp_sec ASC",
            (t_start, t_end)).fetchall()
        telemetry = [{"t": r["timestamp_sec"], "pid": r["pid_name"], "v": r["value"]} for r in rows]
        meta = {
            "crash_time": crash_time, "pre": EVIDENCE_PRE_SEC, "post": EVIDENCE_POST_SEC,
            "camera_latency": CAMERA_LATENCY_SEC, "telemetry": telemetry,
        }
        with open(out_dir / "telemetry.json", "w") as f:
            json.dump(meta, f)

        # --- 2. Cat video tho (ffmpeg -c copy, KHONG re-encode -> nhe) ---
        cam_files = sorted(glob.glob(str(Path(STORAGE_DIR) / "cam_*.ts")))
        saved = []
        for cf in cam_files:
            name = Path(cf).stem.split("_", 1)
            if len(name) < 2:
                continue
            try:
                import datetime as _dt
                fs = _dt.datetime.strptime(name[1], "%Y%m%d_%H%M%S").timestamp()
            except ValueError:
                continue
            f_end = fs + 65  # segment ~60s
            if f_end >= t_start and fs <= t_end:
                # copy nguyen file .ts lien quan (don gian, chac chan; ghep o server)
                dst = out_dir / Path(cf).name
                subprocess.run(["cp", cf, str(dst)], timeout=10)
                saved.append(Path(cf).name)

        # --- 3. Danh dau san sang upload ---
        self.conn.execute(
            "UPDATE crash_events SET evidence_path=? WHERE timestamp_sec=?",
            (f"PENDING_UPLOAD:{out_dir.name}", crash_time))
        self.conn.commit()
        print(f"✅ [CRASH] Đã chốt gói bằng chứng thô: {out_dir.name} ({len(saved)} video, {len(telemetry)} mẫu telemetry)")

    def _on_crash(self, crash_time, severity, gforce, tilt, speed_before, source):
        if (crash_time - self._last_crash_time) < CRASH_COOLDOWN_SEC:
            return
        self._last_crash_time = crash_time
        print(f"🚨 [CRASH] TAI NẠN! {severity} | G={gforce:.1f} tilt={tilt:.0f}° "
              f"tốc_độ_trước={speed_before} | nguồn={source}")

        # 1. Luu crash_events
        try:
            self.conn.execute(
                "INSERT INTO crash_events (timestamp_sec, severity, gforce, tilt, speed_before, source, evidence_path) "
                "VALUES (?,?,?,?,?,?,?)",
                (crash_time, severity, gforce, tilt, speed_before or 0, source, "PENDING_UPLOAD")
            )
            self.conn.commit()
        except Exception as e:
            print(f"⚠️  [CRASH] Lỗi lưu DB: {e}")

        # 2. CAT VIDEO THO + DUMP TELEMETRY (luu LOCAL truoc, render o server sau)
        #    Pi KHONG render (nang) - chi cat video tho + chot data -> danh dau PENDING_UPLOAD
        def _save_raw():
            try:
                self._save_evidence_package(crash_time)
            except Exception as e:
                print(f"⚠️  [CRASH] Lỗi lưu gói bằng chứng thô: {e}")
        threading.Thread(target=_save_raw, daemon=True).start()

        # 3. Day FCM
        try:
            import fcm_sender
            from api_server import get_all_device_tokens, remove_device_tokens
            tokens = get_all_device_tokens()
            if tokens:
                fcm_sender.send_push_async(
                    tokens,
                    f"🚨 PHAT HIEN TAI NAN ({severity})",
                    f"G-force {gforce:.1f} | Toc do truoc {speed_before or 0:.0f} km/h. Dang luu bang chung.",
                    data={"type": "crash", "time": str(int(crash_time))},
                    on_invalid=remove_device_tokens
                )
        except Exception as e:
            print(f"⚠️  [CRASH] Lỗi đẩy FCM: {e}")

    # ---------- Loop chinh ----------
    def _loop(self):
        period = 1.0 / max(1, CRASH_SAMPLE_RATE_HZ) if self.has_mpu else 0.5
        last_speed_check = 0.0
        while self.running:
            now = time.time()
            gforce, tilt = (None, None)

            if self.has_mpu:
                gforce, tilt = self._read_mpu()

            # speed-drop check (2Hz du, khong can 50Hz)
            speed_before = None
            if (now - last_speed_check) >= 0.5:
                speed_before = self._check_speed_drop(now)
                self._mb_current_speed = self._get_current_speed()
                last_speed_check = now

            # Baseline MPU (chi khi xe dung yen) - cho auto-calibration doc, ghi dinh ky
            if self.has_mpu and gforce is not None:
                self._update_mpu_baseline(gforce, now)

            # ---- Logic quyet dinh ----
            crashed = False
            severity = "NHE"
            src = ""
            g_val = gforce or 0.0
            t_val = tilt or 0.0

            if self.has_mpu and gforce is not None:
                if gforce >= CRASH_GFORCE_SEVERE:
                    crashed, severity, src = True, "NANG", "mpu_gforce"
                elif gforce >= CRASH_GFORCE_THRESHOLD:
                    # G vua phai -> can xac nhan cheo (nghieng hoac speed-drop) de giam bao nham
                    if tilt and tilt >= CRASH_TILT_THRESHOLD:
                        crashed, severity, src = True, "VUA", "mpu_gforce+tilt"
                    elif speed_before is not None:
                        crashed, severity, src = True, "VUA", "mpu_gforce+obd"
                    else:
                        crashed, severity, src = True, "NHE", "mpu_gforce"
                elif tilt and tilt >= CRASH_TILT_THRESHOLD and speed_before is not None:
                    crashed, severity, src = True, "VUA", "tilt+obd"
            else:
                # Khong co MPU: chi dua vao OBD speed-drop
                if speed_before is not None:
                    crashed, severity, src = True, "NGHI_NGO", "obd_only"

            if crashed:
                self._on_crash(now, severity, g_val, t_val, speed_before, src)

            time.sleep(period)

    # ---------- Public ----------
    def start(self):
        if not CRASH_DETECTION_ENABLED:
            print("ℹ️  [CRASH] Đã tắt (CRASH_DETECTION_ENABLED=False)")
            return
        self.has_mpu = self._detect_mpu()
        mode = "MPU+OBD" if self.has_mpu else "chỉ OBD (chưa có MPU)"
        print(f"🛡️  [CRASH] Khởi động phát hiện tai nạn - chế độ: {mode}")
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False
        if self.bus:
            try:
                self.bus.close()
            except Exception:
                pass


def run_crash_detector():
    """Wrapper de goi tu main.py."""
    cd = CrashDetector()
    cd.start()
    return cd


if __name__ == "__main__":
    # Test doc cam bien
    cd = CrashDetector()
    has = cd._detect_mpu()
    print("Co MPU:", has)
    if has:
        for _ in range(5):
            g, t = cd._read_mpu()
            print(f"G={g:.2f}g  tilt={t:.0f}°")
            time.sleep(0.5)
    else:
        print("Test speed-drop tu OBD:")
        print("Speed hien tai:", cd._get_current_speed())