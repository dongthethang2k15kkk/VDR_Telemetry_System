import threading
import time
import sqlite3

from config import (
    DATABASE_PATH,
    THRESHOLD_COOLANT_CRITICAL, THRESHOLD_LTFT_CRITICAL,
    BEHAVIOR_SPEED_MAX, BEHAVIOR_THROTTLE_MAX, BEHAVIOR_THROTTLE_DURATION,
    MAINTENANCE_SCHEDULE, RULE_CHECK_RATE_HZ,
    HEALTH_BASELINE, HEALTH_LATENCY_K, HEALTH_MISS_RATE_MAX, HEALTH_DEBOUNCE_CYCLES,
)

ALERT_EMOJI = {
    "technical": "\U0001f534", "behavior": "\U0001f7e1",
    "maintenance": "\U0001f527", "predictive": "\U0001f52e",
}
ALERT_LABEL = {
    "coolant": "Nhiet do dong co", "ltft": "He so nhiện lieu (LTFT)",
    "speed": "Toc do xe", "throttle": "Ga keo dai",
    "oil_and_filter": "Dau + Loc dau", "air_filter": "Loc gio",
    "spark_plug": "Bugi", "brake_pad": "Ma phanh", "gearbox_oil": "Dau hop so",
}


def send_alert(category: str, item: str, value: str, message: str):
    """Gửi cảnh báo ra console + ghi log."""
    print(f"\U0001f6a8 [{category.upper()}] {item} ({value}): {message}")
class TrendAnalyzer:
    """Task3c (nang cap): du bao som bang Mann-Kendall + Sen's slope (Theil-Sen).
    Khac ban cu (OLS): chi canh bao khi xu huong TANG co Y NGHIA THONG KE
    (Mann-Kendall p < P_VALUE_MAX), va do doc bang trung vi cac cap diem (Sen)
    -> ben voi nhieu/ngoai le, het bao dong gia.

    Tru x hien dung chi so chuyen (trip index). Khi cot engine_hours/total_km
    co trong trip_averages thi doi `_x_series()` sang cot do (1 dong) -> don vi
    du bao thanh "sau ~X gio may / km".
    """
    MIN_TRIPS = 6          # MK can du diem moi co Y nghia (>=6 an toan hon 5)
    FORECAST_TRIPS = 10    # canh bao neu du bao cham nguong trong 10 chuyen toi
    P_VALUE_MAX = 0.05     # nguong y nghia thong ke cho Mann-Kendall

    _TARGETS = [
        ("ltft_avg", THRESHOLD_LTFT_CRITICAL, "LTFT", "bugi/kim phun"),
        ("coolant_avg", THRESHOLD_COOLANT_CRITICAL, "Nhiet do nuoc", "he thong lam mat"),
    ]

    # ---- Toan thuan Python (khong can numpy/scipy) ----
    @staticmethod
    def _normal_cdf(z):
        import math
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    @classmethod
    def _mann_kendall(cls, ys):
        """Tra (trend, S, z, p). trend: 'increasing'/'decreasing'/'no trend'.
        Co hieu chinh ties trong phuong sai."""
        import math
        n = len(ys)
        S = 0
        for i in range(n - 1):
            for j in range(i + 1, n):
                d = ys[j] - ys[i]
                S += (d > 0) - (d < 0)   # sign(d)
        # phuong sai co hieu chinh ties
        counts = {}
        for v in ys:
            counts[v] = counts.get(v, 0) + 1
        tie_term = sum(t * (t - 1) * (2 * t + 5) for t in counts.values() if t > 1)
        var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
        if var_s <= 0:
            return ("no trend", S, 0.0, 1.0)
        if S > 0:
            z = (S - 1) / math.sqrt(var_s)
        elif S < 0:
            z = (S + 1) / math.sqrt(var_s)
        else:
            z = 0.0
        p = 2.0 * (1.0 - cls._normal_cdf(abs(z)))   # 2 phia
        if p < cls.P_VALUE_MAX and S > 0:
            trend = "increasing"
        elif p < cls.P_VALUE_MAX and S < 0:
            trend = "decreasing"
        else:
            trend = "no trend"
        return (trend, S, z, p)

    @staticmethod
    def _sen_slope(xs, ys):
        """Sen's slope = trung vi do doc cua moi cap diem (ben voi ngoai le)."""
        slopes = []
        n = len(ys)
        for i in range(n - 1):
            for j in range(i + 1, n):
                dx = xs[j] - xs[i]
                if dx != 0:
                    slopes.append((ys[j] - ys[i]) / dx)
        if not slopes:
            return 0.0
        slopes.sort()
        m = len(slopes)
        mid = m // 2
        return slopes[mid] if m % 2 else (slopes[mid - 1] + slopes[mid]) / 2.0

    @staticmethod
    def _median(vals):
        s = sorted(vals)
        m = len(s)
        mid = m // 2
        return s[mid] if m % 2 else (s[mid - 1] + s[mid]) / 2.0

    def _x_series(self, n):
        """Tru x. Hien tai = chi so chuyen [0..n-1].
        Doi sang engine_hours/total_km tich luy o day khi co cot do."""
        return list(range(n))

    def analyze(self, cursor) -> list:
        """Tra list canh bao predictive. Giu nguyen cau truc dict cu de UI
        khong vo: {pid_name, current_avg, slope, trips_to_threshold, severity, description}."""
        results = []
        for col, threshold, label, advice in self._TARGETS:
            rows = cursor.execute(
                f"SELECT {col} FROM trip_averages WHERE {col} IS NOT NULL "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()
            ys = [r[0] for r in rows][::-1]   # dao lai -> thoi gian tang dan
            n = len(ys)
            if n < self.MIN_TRIPS:
                continue

            # 1) Co xu huong TANG co y nghia khong? (thay cho slope>0.01 cu)
            trend, S, z, p = self._mann_kendall(ys)
            if trend != "increasing":
                continue

            # 2) Do doc ben (Sen) + intercept ben (Kendall-Theil = median(y - slope*x))
            xs = self._x_series(n)
            slope = self._sen_slope(xs, ys)
            if slope <= 0:
                continue
            intercept = self._median([ys[k] - slope * xs[k] for k in range(n)])

            current = ys[-1]
            if current >= threshold:
                continue   # da vuot roi -> viec cua rule tuc thoi, khong phai du bao

            # 3) Du bao diem cham nguong
            x_cross = (threshold - intercept) / slope
            crossing = round(x_cross - (n - 1))
            if not (0 < crossing <= self.FORECAST_TRIPS):
                continue

            sev = "warning" if crossing > 3 else "critical"
            desc = (f"{label} dang tang +{slope:.2f}/chuyen co y nghia "
                    f"(MK p={p:.3f}, hien {current:.1f}) -> du bao cham nguong "
                    f"{threshold:.0f} sau ~{crossing} chuyen. Nen kiem tra {advice}.")
            results.append({
                "pid_name": col, "current_avg": round(current, 2),
                "slope": round(slope, 3), "trips_to_threshold": crossing,
                "severity": sev, "description": desc,
            })

        for r in results:
            try:
                cursor.execute(
                    "INSERT INTO maintenance_logs (timestamp_sec, alert_type, description, is_resolved) VALUES (?,?,?,0)",
                    (time.time(), "PREDICTIVE", r["description"]))
                send_alert("predictive", r["pid_name"], f"{r['current_avg']}", r["description"])
            except Exception:
                pass
        return results


class HealthMonitor:
    """MODULE B: chan doan suc khoe phan cung, doc tu bang pid_health (can_app ghi).
    Hai tang, DO uu tien hon VANG:
      - DO (vat ly):  EWMA miss-rate > HEALTH_MISS_RATE_MAX -> nghi giac cam/cap nhieu.
      - VANG (latency): EWMA latency > mean_ms + K*jitter_ms -> nghi tai bus/ECU ban.
    Debounce HEALTH_DEBOUNCE_CYCLES chu ky de chong bao gia (1 burst thoang qua khong trip).
    Hysteresis: da bao thi khong bao lai cho toi khi ve binh thuong.
    Khong quy ket 'giac long' cho latency (latency yeu, de nham tai bus)."""

    def __init__(self):
        self._lat_strike = {}     # pid -> so chu ky lien tiep vuot nguong latency
        self._miss_strike = {}    # pid -> so chu ky lien tiep vuot nguong miss-rate
        self._alerted_lat = set()
        self._alerted_miss = set()

    def analyze(self, cursor) -> list:
        results = []
        try:
            rows = cursor.execute(
                "SELECT pid, ewma_latency_ms, ewma_miss_rate FROM pid_health"
            ).fetchall()
        except Exception:
            return results   # chua co bang pid_health -> bo qua nhe nhang
        cur = {r[0]: (r[1], r[2]) for r in rows}

        for pid, base in HEALTH_BASELINE.items():
            if pid not in cur:
                continue
            lat, miss = cur[pid]
            label = base.get("label", hex(pid))

            # --- DO: miss-rate (uu tien) ---
            if miss is not None and miss > HEALTH_MISS_RATE_MAX:
                self._miss_strike[pid] = self._miss_strike.get(pid, 0) + 1
                if self._miss_strike[pid] >= HEALTH_DEBOUNCE_CYCLES and pid not in self._alerted_miss:
                    self._alerted_miss.add(pid)
                    desc = (f"Rot goi lien tuc o {label} (miss-rate {miss*100:.1f}%). "
                            f"Canh bao lop vat ly: kiem tra giac cam/cap nhieu.")
                    results.append({"pid": pid, "severity": "critical",
                                    "kind": "physical", "description": desc})
            else:
                self._miss_strike[pid] = 0
                self._alerted_miss.discard(pid)

            # --- VANG: latency drift (chi xet khi PID nay khong dang dinh DO) ---
            thr = base["mean_ms"] + HEALTH_LATENCY_K * base["jitter_ms"]
            if pid not in self._alerted_miss and lat is not None and lat > thr:
                self._lat_strike[pid] = self._lat_strike.get(pid, 0) + 1
                if self._lat_strike[pid] >= HEALTH_DEBOUNCE_CYCLES and pid not in self._alerted_lat:
                    self._alerted_lat.add(pid)
                    desc = (f"Do tre giao tiep {label} tang ({lat:.0f}ms > nguong {thr:.0f}ms). "
                            f"Kiem tra tai CAN bus / ECU ban.")
                    results.append({"pid": pid, "severity": "warning",
                                    "kind": "latency", "description": desc})
            else:
                self._lat_strike[pid] = 0
                self._alerted_lat.discard(pid)

        for r in results:
            try:
                cursor.execute(
                    "INSERT INTO maintenance_logs (timestamp_sec, alert_type, description, is_resolved) VALUES (?,?,?,0)",
                    (time.time(), "HEALTH", r["description"]))
                send_alert("health", hex(r["pid"]), r["severity"], r["description"])
            except Exception:
                pass
        return results


class RuleEngine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        
        self.conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.cursor = self.conn.cursor()
        
        self.obd_data_lock = threading.Lock()
        self.current_data = {
            0x0D: 0.0, 0x05: 0.0, 0x11: 0.0, 0x07: 0.0, 0x0C: 0.0
        }
        
        self.throttle_high_start = None
        self.last_calc_time = time.monotonic()
        self.last_maint_check = time.monotonic()
        
        # --- Cảnh báo tức thời (Reset theo chuyến) ---
        self.alerted_this_trip = set()
        
        # --- Cảnh báo bảo dưỡng (Vĩnh viễn cho đến khi mark_maintained) ---
        self.alerted_this_maintenance = set()
        
        self.trip_start_time = time.time()
        self.trip_km = 0.0
        self.trip_engine_hours = 0.0
        # Fix#1: trang thai vong doi chuyen di
        self._idle_since = None      # moc thoi gian xe bat dau dung yen
        self._last_persist = 0.0     # lan cuoi persist trip xuong DB
        self._trip_ended = False     # da flush cho lan dung nay chua
        
        self._init_schedule()
        self.health_monitor = HealthMonitor()

    def _init_schedule(self):
        # ── Tự tạo các bảng RuleEngine cần (trước thiếu → crash) ──
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS alert_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_sec REAL,
            category TEXT, source TEXT, item TEXT, value TEXT, severity TEXT, description TEXT)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance_schedule (
            item TEXT PRIMARY KEY, interval_km REAL, interval_days REAL,
            last_km REAL DEFAULT 0, last_date REAL, status TEXT DEFAULT '🟢 Normal')''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS trip_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL, end_time REAL,
            total_km REAL, engine_hours REAL)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value REAL)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS pid_health (pid INTEGER PRIMARY KEY, ewma_latency_ms REAL, ewma_miss_rate REAL, updated_at REAL)''')
        self.cursor.execute("INSERT OR IGNORE INTO system_config (key, value) VALUES ('base_odo', 0)")
        self.conn.commit()
        # -- Migration Task1 RE: engine_hours (idempotent) --
        for _ddl in (
            "ALTER TABLE maintenance_schedule ADD COLUMN interval_engine_hours REAL DEFAULT NULL",
            "ALTER TABLE maintenance_schedule ADD COLUMN last_engine_hours REAL DEFAULT 0",
            "ALTER TABLE maintenance_schedule ADD COLUMN last_alert_at REAL DEFAULT 0",
        ):
            try:
                self.cursor.execute(_ddl)
            except sqlite3.OperationalError as _e:
                if "duplicate column" not in str(_e).lower():
                    raise
        self.conn.commit()
        for item, cfg in MAINTENANCE_SCHEDULE.items():
            self.cursor.execute(
                "INSERT OR IGNORE INTO maintenance_schedule (item, interval_km, interval_days, interval_engine_hours, last_date) VALUES (?, ?, ?, ?, ?)",
                (item, cfg["interval_km"], cfg["interval_days"], cfg.get("interval_engine_hours"), time.time())
            )
        self.conn.commit()

    def _get_global_km(self) -> float:
        """Lấy tổng ODO xe = ODO gốc + Tổng lịch sử Trip + Trip hiện tại"""
        res_odo = self.cursor.execute("SELECT value FROM system_config WHERE key='base_odo'").fetchone()
        base_odo = res_odo[0] if res_odo else 0.0
        
        res_history = self.cursor.execute("SELECT COALESCE(SUM(total_km), 0) FROM trip_logs").fetchone()
        history_km = res_history[0] if res_history else 0.0
        
        return base_odo + history_km + self.trip_km

    def update_base_odo(self, new_odo_km: float):
        """Dùng cho User Setup (Nhập số km trên đồng hồ thực tế của xe)"""
        self.cursor.execute("UPDATE system_config SET value=? WHERE key='base_odo'", (new_odo_km,))
        self.conn.commit()

    def mark_maintained(self, item: str):
        """Gọi khi user xác nhận đã bảo dưỡng xong"""
        current_global_km = self._get_global_km()
        # Task1e: tong gio no may hiện tai (de reset chu ky engine_hours)
        _res_eh = self.cursor.execute("SELECT COALESCE(SUM(engine_hours), 0) FROM trip_logs").fetchone()
        current_engine_hours = (_res_eh[0] if _res_eh else 0.0) + self.trip_engine_hours
        
        self.cursor.execute(
            "UPDATE maintenance_schedule SET last_km=?, last_date=?, status='🟢 Normal' WHERE item=?",
            (current_global_km, time.time(), item)
        )
        self.cursor.execute(
            "UPDATE maintenance_schedule SET last_engine_hours=? WHERE item=?",
            (current_engine_hours, item)
        )
        self.conn.commit()
        self.alerted_this_maintenance.discard(item)
        print(f"🔧 Đã ghi nhận bảo dưỡng [{item}] tại mốc {current_global_km:.1f} km.")

    IDLE_END_TRIP_SEC = 180   # xe dung yen 3 phut -> ket thuc chuyen
    PERSIST_EVERY_SEC = 60    # persist trip xuong DB moi 60s

    def _manage_trip_lifecycle(self, speed, rpm):
        """Fix#1: ket thuc chuyen khi xe dung lau + persist trip dinh ky (song qua reboot)."""
        now = time.monotonic()
        moving = (speed or 0) > 1 or (rpm or 0) > 300

        # 1) Persist trip dang chay xuong system_config (de API/UI doc + chong mat khi tat may)
        if now - self._last_persist >= self.PERSIST_EVERY_SEC:
            self._last_persist = now
            try:
                self.cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('live_trip_km', ?)", (self.trip_km,))
                self.cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('live_trip_eh', ?)", (self.trip_engine_hours,))
                self.conn.commit()
            except Exception as e:
                print(f"[HÀNH TRÌNH] Lỗi lưu tạm dữ liệu chuyến: {e}")

        # 2) Auto-end-trip: xe dung yen qua IDLE_END_TRIP_SEC -> flush
        if moving:
            self._idle_since = None
            self._trip_ended = False
        else:
            if self._idle_since is None:
                self._idle_since = now
            elif (now - self._idle_since >= self.IDLE_END_TRIP_SEC
                  and not self._trip_ended
                  and (self.trip_km > 0.1 or self.trip_engine_hours > 0.01)):
                print("[HÀNH TRÌNH] Xe dừng lâu → tự kết thúc chuyến đi, lưu dữ liệu.")
                self.reset_trip()
                self._trip_ended = True
                # reset live counter sau khi flush
                try:
                    self.cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('live_trip_km', 0)")
                    self.cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('live_trip_eh', 0)")
                    self.conn.commit()
                except Exception:
                    pass

    def reset_trip(self):
        self.flush_trip()
        self.alerted_this_trip.clear()
        self.trip_start_time = time.time()
        self.trip_km = 0.0
        self.trip_engine_hours = 0.0
        self.last_calc_time = time.monotonic()

    def update_pid(self, pid: int, value: float):
        with self.obd_data_lock:
            if pid in self.current_data:
                self.current_data[pid] = value

    def _log_alert_trip(self, category: str, item: str, value: str, severity: str, desc: str):
        """Ghi log cho các cảnh báo reset theo chuyến (Kỹ thuật/Hành vi)"""
        alert_key = f"{category}_{item}"
        if alert_key not in self.alerted_this_trip:
            self.cursor.execute(
                "INSERT INTO alert_logs (timestamp_sec, category, source, item, value, severity, description) VALUES (?,?,?,?,?,?,?)",
                (time.time(), category, "Rule_Engine", item, value, severity, desc)
            )
            self.conn.commit()
            self.alerted_this_trip.add(alert_key)
            self._push_ui_alert(f"{category}:{item}", f"{desc} ({value})")
            self._push_fcm(desc, f"{item}: {value}")
            send_alert(category, item, value, desc)

    def _check_group_a_rules(self, speed, coolant, throttle, ltft):
        if coolant > THRESHOLD_COOLANT_CRITICAL:
            self._log_alert_trip("technical", "coolant", f"{coolant:.1f}°C", "critical", "Động cơ quá nhiệt")
        if abs(ltft) > THRESHOLD_LTFT_CRITICAL:
            self._log_alert_trip("technical", "ltft", f"{ltft:.1f}%", "warning", "Nhiên liệu bất thường (kích hoạt MIL)")
        if speed > BEHAVIOR_SPEED_MAX:
            self._log_alert_trip("behavior", "speed", f"{speed:.1f} km/h", "warning", "Vượt quá 120km/h")

        if throttle > BEHAVIOR_THROTTLE_MAX:
            if self.throttle_high_start is None:
                self.throttle_high_start = time.monotonic()
            elif (time.monotonic() - self.throttle_high_start) >= BEHAVIOR_THROTTLE_DURATION:
                self._log_alert_trip("behavior", "throttle", f"{throttle:.1f}%", "warning", "Thốc ga mạnh kéo dài >5s")
        else:
            self.throttle_high_start = None

    def _accumulate_group_b(self, speed, rpm):
        now = time.monotonic()
        dt_hours = (now - self.last_calc_time) / 3600.0
        self.last_calc_time = now

        if speed > 0:
            self.trip_km += speed * dt_hours
        if rpm > 0:
            self.trip_engine_hours += dt_hours

    def _check_maintenance_schedule(self):
        now_mono = time.monotonic()
        if now_mono - self.last_maint_check < 60.0:
            return
        self.last_maint_check = now_mono

        global_km = self._get_global_km()
        current_time = time.time()

        # Task1d: tong gio no may = lich su trip_logs + chuyen hiện tai
        res_eh = self.cursor.execute("SELECT COALESCE(SUM(engine_hours), 0) FROM trip_logs").fetchone()
        total_engine_hours = (res_eh[0] if res_eh else 0.0) + self.trip_engine_hours
        rows = self.cursor.execute(
            "SELECT item, last_km, interval_km, last_date, interval_days, last_engine_hours, interval_engine_hours, last_alert_at FROM maintenance_schedule"
        ).fetchall()

        for item, last_km, interval_km, last_date, interval_days, last_engine_hours, interval_engine_hours, last_alert_at in rows:
            km_used = global_km - last_km
            km_ratio = (km_used / interval_km) * 100 if interval_km > 0 else 0

            days_used = (current_time - last_date) / 86400.0 if last_date else 0
            days_ratio = (days_used / interval_days) * 100 if interval_days else 0
            engine_hours_used = total_engine_hours - (last_engine_hours or 0)
            engine_hours_ratio = (engine_hours_used / interval_engine_hours) * 100 if interval_engine_hours else 0

            max_ratio = max(km_ratio, days_ratio, engine_hours_ratio)

            if max_ratio >= 100:
                severity = "critical"
                desc = f"Đã quá hạn bảo dưỡng {item}!"
            elif max_ratio >= 90:
                severity = "warning"
                desc = f"Sắp đến hạn bảo dưỡng {item}"
            else:
                continue

            # Sử dụng Set riêng biệt cho cảnh báo bảo dưỡng
            # Task1d: override desc voi context so lieu + neu ro trigger
            _status = "Đã quá hạn" if max_ratio >= 100 else "Sắp đến hạn"
            _ratios = {"số km": km_ratio, "số ngày": days_ratio, "giờ nổ máy": engine_hours_ratio}
            _trigger = max(_ratios, key=_ratios.get)
            _ctx = [f"{km_used:.0f}km"]
            if interval_engine_hours:
                _ctx.append(f"{engine_hours_used:.0f}h nổ máy")
            if interval_days:
                _ctx.append(f"{days_used:.0f} ngày")
            desc = f"{item}: {' | '.join(_ctx)} -> {_status} (do {_trigger})"
            # Fix#3: chong re-spam bang DB (khong dung set RAM). Bao lai neu lan cuoi > 24h.
            if current_time - (last_alert_at or 0) > 86400:
                self.cursor.execute(
                    "INSERT INTO alert_logs (timestamp_sec, category, source, item, value, severity, description) VALUES (?,?,?,?,?,?,?)",
                    (current_time, "maintenance", "Rule_Engine", item, f"{max_ratio:.1f}%", severity, desc)
                )
                self.cursor.execute("UPDATE maintenance_schedule SET last_alert_at=? WHERE item=?", (current_time, item))
                self.conn.commit()
                self._push_ui_alert(f"maintenance:{item}", desc)
                self._push_fcm("Nhac bao duong", desc)
                send_alert("maintenance", item, f"{max_ratio:.1f}%", desc)

    def _reload_thresholds(self):
        """Task4d: doc nguong override tu system_config (nguoi dung set qua bot).
        Tu throttle 60s. Rebind bien GLOBAL trong namespace rule_engine -
        co tac dung vi _check_group_a_rules doc bien module-level (1Hz).
        CANH BAO: neu sau nay doi sang 'config.X' thi rebind nay vo tac dung."""
        now = time.monotonic()
        if now - getattr(self, "_last_thr_reload", 0) < 60.0:
            return
        self._last_thr_reload = now
        try:
            rows = self.cursor.execute(
                "SELECT key, value FROM system_config WHERE key IN ('speed_threshold','coolant_threshold')"
            ).fetchall()
            cfg = {k: v for k, v in rows}
            global BEHAVIOR_SPEED_MAX, THRESHOLD_COOLANT_CRITICAL
            if 'speed_threshold' in cfg and cfg['speed_threshold']:
                BEHAVIOR_SPEED_MAX = cfg['speed_threshold']
            if 'coolant_threshold' in cfg and cfg['coolant_threshold']:
                THRESHOLD_COOLANT_CRITICAL = cfg['coolant_threshold']
        except Exception:
            pass

    def run(self):
        print(f"⚙️  Rule Engine khởi động ({RULE_CHECK_RATE_HZ}Hz)")
        while self.running:
            start_time = time.monotonic()
            self._refresh_from_db()
            
            with self.obd_data_lock:
                speed = self.current_data.get(0x0D, 0)
                coolant = self.current_data.get(0x05, 0)
                throttle = self.current_data.get(0x11, 0)
                ltft = self.current_data.get(0x07, 0)
                rpm = self.current_data.get(0x0C, 0)

            self._check_group_a_rules(speed, coolant, throttle, ltft)
            self._accumulate_group_b(speed, rpm)
            self._manage_trip_lifecycle(speed, rpm)  # Fix#1: ket thuc chuyen + persist
            self._check_maintenance_schedule()
            self._reload_thresholds()  # Task4d: nhan lenh /setspeed /settemp tu bot
            self.health_monitor.analyze(self.cursor)

            elapsed = time.monotonic() - start_time
            sleep_time = max(0, (1.0 / RULE_CHECK_RATE_HZ) - elapsed)
            time.sleep(sleep_time)

    def flush_trip(self):
        if self.trip_km > 0.1 or self.trip_engine_hours > 0.01:
            _end_time = time.time()
            self.cursor.execute(
                "INSERT INTO trip_logs (start_time, end_time, total_km, engine_hours) VALUES (?,?,?,?)",
                (self.trip_start_time, _end_time, self.trip_km, self.trip_engine_hours)
            )
            self.conn.commit()
            # Task3b: tính trung bình PID của chuyến vừa kết thúc -> trip_averages
            _trip_id = self.cursor.lastrowid
            try:
                def _avg(pid_hex):
                    r = self.cursor.execute(
                        "SELECT AVG(value) FROM obd_data WHERE pid=? AND timestamp_sec BETWEEN ? AND ?",
                        (pid_hex, self.trip_start_time, _end_time)
                    ).fetchone()
                    return r[0] if r and r[0] is not None else None
                self.cursor.execute(
                    "INSERT INTO trip_averages (trip_id, ltft_avg, coolant_avg, rpm_avg, maf_avg, created_at) VALUES (?,?,?,?,?,?)",
                    (_trip_id, _avg("0x7"), _avg("0x5"), _avg("0xc"), _avg("0x10"), _end_time)
                )
                self.conn.commit()
                # Task3d: chạy phân tích xu hướng sau mỗi chuyến
                try:
                    TrendAnalyzer().analyze(self.cursor)
                except Exception as _te:
                    print(f"[DỰ BÁO] Lỗi phân tích xu hướng: {_te}")
            except Exception as _e:
                print(f"[DỰ BÁO] Lỗi tính trung bình chuyến đi: {_e}")
            print(f"💾 Đã lưu hành trình: {self.trip_km:.2f} km | {self.trip_engine_hours:.2f} giờ máy.")
    def _push_ui_alert(self, alert_type: str, description: str):
        """Đẩy cảnh báo vào maintenance_logs để Web UI hiện (UI đọc bảng này)."""
        try:
            self.cursor.execute(
                "INSERT INTO maintenance_logs (timestamp_sec, alert_type, description, is_resolved) VALUES (?,?,?,0)",
                (time.time(), alert_type, description))
            self.conn.commit()
        except Exception as e:
            print(f"⚠️ Lỗi đẩy cảnh báo lên UI: {e}")

    def _push_fcm(self, title, body):
        """Day push (FCM) toi cac thiet bi da dang ky. Non-blocking, silent-fail."""
        try:
            import fcm_sender
            from api_server import get_all_device_tokens, remove_device_tokens
            tokens = get_all_device_tokens()
            if tokens:
                fcm_sender.send_push_async(
                    tokens, title, body,
                    data={"source": "rule_engine"},
                    on_invalid=remove_device_tokens
                )
        except Exception as e:
            print(f"[FCM] Lỗi đẩy push: {e}")

    def _refresh_from_db(self):
        """Đọc giá trị PID mới nhất từ obd_data (do OBDReader ghi) → nạp current_data."""
        # Fix#7b: query tung PID dung index idx_obd_pid_ts (tranh full table scan moi 1Hz)
        try:
            with self.obd_data_lock:
                for pid_int in list(self.current_data.keys()):
                    r = self.cursor.execute(
                        "SELECT value FROM obd_data WHERE pid=? ORDER BY timestamp_sec DESC LIMIT 1",
                        (hex(pid_int),)
                    ).fetchone()
                    if r is not None:
                        self.current_data[pid_int] = r[0]
        except Exception:
            pass
