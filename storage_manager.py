import os
import time
import shutil
import threading
from config import STORAGE_DIR, RETENTION_DAYS

class DiskRotation(threading.Thread):
    def __init__(self, threshold_percent: int = 80,
                 check_interval: int = 600,
                 retention_days: int = RETENTION_DAYS):
        super().__init__(daemon=True)
        self.threshold = threshold_percent
        self.interval = check_interval
        self.retention_days = retention_days

    def get_disk_usage(self) -> float:
        total, used, _ = shutil.disk_usage(STORAGE_DIR)
        return (used / total) * 100

    def delete_oldest_files(self) -> None:
        files = [
            os.path.join(STORAGE_DIR, f)
            for f in os.listdir(STORAGE_DIR)
            if f.endswith('.mp4') or f.endswith('.ts')
        ]
        if not files:
            return

        files.sort(key=os.path.getmtime)

        for file in files:
            if self.get_disk_usage() <= self.threshold - 5:
                break
            try:
                os.remove(file)
                print(f"🗑️  Đã xóa file cũ để giải phóng dung lượng: {file}")
            except Exception as e:
                print(f"⚠️  Lỗi xóa file: {e}")

    def _cleanup_database(self) -> None:
        """Gọi purge_and_vacuum bằng một connection độc lập."""
        try:
            from obd_module.db_setup import TelemetryDBWriter
            
            print("🗄️ Đang kết nối Database để dọn dẹp...")
            cleaner_db = TelemetryDBWriter()
            cleaner_db.purge_and_vacuum(self.retention_days)
            
        except Exception as e:
            print(f"⚠️ Lỗi khi dọn dẹp DB: {e}")

    def run(self) -> None:
        print(f"🧹 Khởi động Storage Manager (Ngưỡng: {self.threshold}% | Giữ DB: {self.retention_days} ngày)")
        while True:
            usage = self.get_disk_usage()
            if usage > self.threshold:
                print(f"🚨 CẢNH BÁO: Ổ đĩa đang dùng {usage:.1f}% (> {self.threshold}%). Kích hoạt dọn dẹp...")
                self.delete_oldest_files()
                self._cleanup_database()
                print(f"📊 Dung lượng sau dọn dẹp: {self.get_disk_usage():.1f}%")
            time.sleep(self.interval)