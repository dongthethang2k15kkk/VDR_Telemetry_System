import sqlite3
import time
from config import DATABASE_PATH

# Hàm chạy 1 lần ở main.py để khởi tạo bảng chuẩn
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()
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
    conn.commit()
    conn.close()
    print("✅ Đã khởi tạo Database với chuẩn [timestamp_sec]")


class TelemetryDBWriter:
    def __init__(self):
        # Mỗi luồng (Process) sẽ tự tạo 1 Connection riêng khi gọi Class này
        self.conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA cache_size=-64000;")
        self.cursor = self.conn.cursor()
        self.queue = []

    def enqueue(self, ts, pid, name, val, unit):
        self.queue.append((ts, pid, name, val, unit))
        if len(self.queue) >= 20:
            self.flush()

    def flush(self):
        if not self.queue:
            return
        # Đã đổi timestamp_ms thành timestamp_sec ở đây
        self.cursor.executemany(
            "INSERT INTO obd_data (timestamp_sec, pid, pid_name, value, unit) VALUES (?,?,?,?,?)",
            self.queue
        )
        self.conn.commit()
        self.queue.clear()

    def vacuum(self) -> None:
        print("🗜️  Đang chạy VACUUM để thu hồi dung lượng DB...")
        self.flush()
        self.conn.isolation_level = None
        try:
            self.conn.execute("VACUUM")
            print("✅ VACUUM hoàn tất.")
        finally:
            self.conn.isolation_level = ""

    def purge_and_vacuum(self, retention_days: int) -> None:
        cutoff_sec = time.time() - (retention_days * 86400)
        self.cursor.execute("DELETE FROM obd_data WHERE timestamp_sec < ?", (cutoff_sec,))
        self.conn.commit()
        deleted = self.cursor.rowcount
        if deleted:
            print(f"🗄️  Đã xóa {deleted:,} bản ghi cũ hơn {retention_days} ngày khỏi DB.")
            self.vacuum()