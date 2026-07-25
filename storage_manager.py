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
            if self.get_disk_usage() <= self.threshold - 5:         ####
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
            try:
                cleaner_db.purge_and_vacuum(self.retention_days)
            finally:
                cleaner_db.close()  # Fix#6: dong connection tranh ro ri
            
        except Exception as e:
            print(f"⚠️ Lỗi khi dọn dẹp DB: {e}")

    def _cleanup_evidence_folders(self) -> None:
        """Don cac thu muc evidence_* DA UPLOAD THANH CONG (marker 'UPLOADED:'
        trong crash_events.evidence_path) va cu hon retention_days. Chay moi
        vong lap, KHONG phu thuoc nguong dia day - vi thu muc bang chung
        khong nam trong danh sach quet cua delete_oldest_files() (chi quet
        .mp4/.ts o thu muc goc, khong de quy vao thu muc con).
        TUYET DOI KHONG dong vao thu muc con dang 'PENDING_UPLOAD:' - mat
        bang chung phap ly chua kip gui la khong the chap nhan duoc."""
        from config import DATABASE_PATH
        import sqlite3
        try:
            conn = sqlite3.connect(str(DATABASE_PATH))
            uploaded = {
                row[0].split(":", 1)[1]
                for row in conn.execute(
                    "SELECT evidence_path FROM crash_events "
                    "WHERE evidence_path LIKE 'UPLOADED:%'"
                ).fetchall()
            }
            conn.close()
        except Exception as e:
            print(f"⚠️  Lỗi đọc crash_events để dọn thư mục bằng chứng: {e}")
            return

        cutoff = time.time() - (self.retention_days * 86400)
        try:
            names = os.listdir(STORAGE_DIR)
        except Exception as e:
            print(f"⚠️  Lỗi liệt kê {STORAGE_DIR}: {e}")
            return
        for name in names:
            if not name.startswith("evidence_"):
                continue
            full = os.path.join(STORAGE_DIR, name)
            if not os.path.isdir(full):
                continue
            if name not in uploaded:
                continue  # chua upload xong (hoac khong khop DB) -> giu nguyen, khong dong vao
            try:
                if os.path.getmtime(full) < cutoff:
                    shutil.rmtree(full)
                    print(f"🗑️  Đã xóa thư mục bằng chứng đã upload (quá {self.retention_days} ngày): {name}")
            except Exception as e:
                print(f"⚠️  Lỗi xóa thư mục bằng chứng {name}: {e}")

    def run(self) -> None:
        print(f"🧹 Khởi động Storage Manager (Ngưỡng: {self.threshold}% | Giữ DB: {self.retention_days} ngày)")
        while True:
            self._cleanup_evidence_folders()
            usage = self.get_disk_usage()
            if usage > self.threshold:
                print(f"🚨 CẢNH BÁO: Ổ đĩa đang dùng {usage:.1f}% (> {self.threshold}%). Kích hoạt dọn dẹp...")
                self.delete_oldest_files()
                self._cleanup_database()
                print(f"📊 Dung lượng sau dọn dẹp: {self.get_disk_usage():.1f}%")
            time.sleep(self.interval)