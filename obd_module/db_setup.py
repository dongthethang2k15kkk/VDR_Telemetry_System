import sqlite3
import time
import threading
from config import DATABASE_PATH

# Hàm chạy 1 lần ở main.py để khởi tạo bảng chuẩn
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")    # luồng đọc và ghi chạy song song
    cursor = conn.cursor()
    
    # Bảng Telemetry gốc
    cursor.execute('''CREATE TABLE IF NOT EXISTS obd_data (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_sec REAL,
        pid           TEXT,
        pid_name      TEXT,
        value         REAL,
        unit          TEXT
    )''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_obd_timestamp
        ON obd_data (timestamp_sec)
    ''')
    # Fix#7: composite index cho query MAX(id) GROUP BY pid (chong full table scan)
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_obd_pid_ts
        ON obd_data (pid, timestamp_sec)
    ''')
    
    # Bảng mới lưu log bảo trì, bảo dưỡng
    cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance_logs (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_sec REAL,
        alert_type    TEXT,
        description   TEXT,
        is_resolved   INTEGER DEFAULT 0,
        resolved_at   REAL
    )''')
    

    # Bảng RuleEngine
    cursor.execute('''CREATE TABLE IF NOT EXISTS alert_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_sec REAL,
        category TEXT, source TEXT, item TEXT, value TEXT, severity TEXT, description TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance_schedule (
        item TEXT PRIMARY KEY, interval_km REAL, interval_days REAL,
        last_km REAL DEFAULT 0, last_date REAL, status TEXT DEFAULT '🟢 Normal')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS trip_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL, end_time REAL,
        total_km REAL, engine_hours REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value REAL)''')
    cursor.execute("INSERT OR IGNORE INTO system_config (key, value) VALUES ('base_odo', 0)")
    cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance_history (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_sec REAL,
        item         TEXT,
        km_at_service REAL,
        note         TEXT DEFAULT '')'''  )

    # -- Migration Task1: engine_hours cho maintenance_schedule (idempotent) --
    for _ddl in (
        "ALTER TABLE maintenance_schedule ADD COLUMN interval_engine_hours REAL DEFAULT NULL",
        "ALTER TABLE maintenance_schedule ADD COLUMN last_engine_hours REAL DEFAULT 0",
    ):
        try:
            cursor.execute(_ddl)
        except sqlite3.OperationalError as _e:
            if "duplicate column" not in str(_e).lower():
                raise
    # Task2a: bang luu ma loi DTC
    cursor.execute('''CREATE TABLE IF NOT EXISTS dtc_logs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_sec REAL,
        dtc_code     TEXT,
        description  TEXT,
        is_cleared   INTEGER DEFAULT 0)''')
    # Task3a: trung binh moi chuyen (cho predictive)
    cursor.execute('''CREATE TABLE IF NOT EXISTS trip_averages (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        trip_id       INTEGER,
        ltft_avg      REAL,
        coolant_avg   REAL,
        rpm_avg       REAL,
        maf_avg       REAL,
        created_at    REAL)''')
    conn.commit()
    conn.close()
    print("✅ Đã khởi tạo Database: obd_data, maintenance_logs, alert_logs, maintenance_schedule, trip_logs, system_config")

class TelemetryDBWriter:
    def __init__(self):
        # Mỗi luồng (Process) sẽ tự tạo 1 Connection riêng khi gọi Class này
        self.conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA cache_size=-64000;")
        self.cursor = self.conn.cursor()
        
        self.queue = []
        self.alert_queue = []
        self._lock = threading.Lock()

    def enqueue(self, ts, pid, name, val, unit):
        with self._lock:
            self.queue.append((ts, pid, name, val, unit))
            if len(self.queue) >= 20:
                self._flush_unsafe()

    def log_maintenance_alert(self, ts, alert_type, description):
        with self._lock:
            self.alert_queue.append((ts, alert_type, description, 0))

    def flush(self):
        with self._lock:
            self._flush_unsafe()

    def _flush_unsafe(self):
        """
        CẢNH BÁO DEADLOCK (DÀNH CHO NGƯỜI MAINTAIN):
        Hàm này mặc định RẰNG TRƯỚC ĐÓ ĐÃ GỌI `with self._lock`.
        Tuyệt đối KHÔNG gọi ngược lại các hàm có chứa `with self._lock` 
        (như enqueue, flush, log_maintenance_alert) từ bên trong hàm này.
        """
        if self.queue:
            self.cursor.executemany(
                "INSERT INTO obd_data (timestamp_sec, pid, pid_name, value, unit) VALUES (?,?,?,?,?)",
                self.queue
            )
            self.queue.clear()
            
        if self.alert_queue:
            self.cursor.executemany(
                "INSERT INTO maintenance_logs (timestamp_sec, alert_type, description, is_resolved) VALUES (?, ?, ?, ?)",
                self.alert_queue
            )
            self.alert_queue.clear()

        # CHỈ COMMIT 1 LẦN DUY NHẤT CHO CẢ 2 BẢNG
        self.conn.commit()

    def vacuum(self) -> None:
        # Fix#6: dung wal_checkpoint(TRUNCATE) thay VACUUM -> khong can exclusive lock,
        # khong tranh chap voi OBD dang ghi 4 lan/giay (tranh "database is locked").
        print("🗜️  Đang checkpoint WAL để thu hồi dung lượng DB...")
        self.flush()
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.execute("PRAGMA incremental_vacuum")
            self.conn.commit()
            print("✅ Checkpoint WAL hoàn tất.")
        except Exception as e:
            print(f"⚠️  Checkpoint lỗi (bỏ qua): {e}")

    def purge_and_vacuum(self, retention_days: int) -> None:
        cutoff_sec = time.time() - (retention_days * 86400)
        self.cursor.execute("DELETE FROM obd_data WHERE timestamp_sec < ?", (cutoff_sec,))
        self.conn.commit()
        deleted = self.cursor.rowcount
        if deleted:
            print(f"🗄️  Đã xóa {deleted:,} bản ghi cũ hơn {retention_days} ngày khỏi DB.")
            self.vacuum()

    def close(self) -> None:
        # Fix#6: dong connection de tranh ro ri (storage_manager goi sau khi don dep)
        try:
            self.flush()
            self.conn.close()
        except Exception:
            pass
