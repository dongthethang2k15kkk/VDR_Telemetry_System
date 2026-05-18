import time
import multiprocessing
import threading
from obd_module.ecu_sim import ECUSimulator
from obd_module.can_app import OBDReader
from obd_module.db_setup import init_db
from config import OPERATION_MODE, RETENTION_DAYS
from storage_manager import DiskRotation
from vision_module.camera_recorder import run_camera_recorder

def run_production_reader():
    """Hàm chạy độc lập trên 1 nhân CPU (Multiprocessing)."""
    reader = OBDReader()
    reader.read_and_store()

def main():
    init_db()

    if OPERATION_MODE == "SIMULATION":
        print("🖥️ KHỞI CHẠY CHẾ ĐỘ GIẢ LẬP (SIMULATION)...")
        ecu = ECUSimulator()
        ecu.start()
        time.sleep(1)

        reader = OBDReader()
        try:
            reader.read_and_store()
        except KeyboardInterrupt:
            print("\n🛑 Nhận lệnh ngắt hệ thống (Ctrl+C)...")
        except Exception as e:
            print(f"\n❌ Lỗi giả lập: {e}")
        finally:
            print("🧹 Đang dọn dẹp hệ thống giả lập...")
            ecu.stop()
            # Đã sửa: Gọi flush từ chính instance của reader
            if hasattr(reader, 'db_writer'):
                reader.db_writer.flush()
            print("✅ Đã ngắt giả lập.")

    else:
        print("🚀 KHỞI CHẠY CHẾ ĐỘ THỰC TẾ (PRODUCTION)...")

        # Bật luồng dọn rác
        disk_cleaner = DiskRotation(
            threshold_percent=80,
            retention_days=RETENTION_DAYS,
        )
        disk_cleaner.start()

        # Bật luồng thu Camera IP
        cam_process = multiprocessing.Process(
            target=run_camera_recorder, daemon=True
        )
        cam_process.start()

        # Bật luồng đọc CAN Bus
        obd_process = multiprocessing.Process(
            target=run_production_reader, daemon=True
        )
        obd_process.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Nhận lệnh ngắt hệ thống (Ctrl+C)...")
        except Exception as e:
            print(f"\n❌ Lỗi hệ thống bất ngờ: {e}")
        finally:
            print("🧹 Đang dọn dẹp và giải phóng tài nguyên phần cứng...")
            
            if obd_process.is_alive():
                obd_process.terminate()
                obd_process.join()
                
            if cam_process.is_alive():
                cam_process.terminate()
                cam_process.join()
            print("✅ Tắt máy an toàn")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()