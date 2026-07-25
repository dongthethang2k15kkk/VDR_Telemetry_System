import http.server
import socketserver
import time
import multiprocessing
import os
import signal
from obd_module.ecu_sim import ECUSimulator
from obd_module.can_app import OBDReader
from obd_module.db_setup import init_db
from config import OPERATION_MODE, RETENTION_DAYS, BASE_DIR
from storage_manager import DiskRotation
from vision_module.camera_recorder import run_camera_recorder
from api_server import run_fastapi_server

def run_production_reader():
    """Hàm chạy độc lập trên 1 nhân CPU (Multiprocessing)."""
    try:
        os.setpgrp()  # Tách khỏi process group của cha để không nhận SIGINT trực tiếp
    except AttributeError:
        pass  # Dự phòng nếu chạy trên môi trường không hỗ trợ POSIX
    
    reader = OBDReader()
    from obd_module.rule_engine import RuleEngine
    RuleEngine().start()
    reader.read_and_store()

def run_camera_isolated():
    """Hàm chạy độc lập thu luồng Camera (Multiprocessing)."""
    try:
        os.setpgrp()
    except AttributeError:
        pass
    run_camera_recorder()

def run_api_isolated():
    """Hàm chạy độc lập API Server FastAPI (Multiprocessing)."""
    try:
        os.setpgrp()
    except AttributeError:
        pass
    run_fastapi_server()

def run_web_ui_server():
    try:
        os.setpgrp()
    except AttributeError:
        pass
    os.chdir(BASE_DIR / "web_ui")
    handler = http.server.SimpleHTTPRequestHandler
    handler.log_message = lambda *args: None
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", 8888), handler) as httpd:
        print("🌐 Web UI tại http://0.0.0.0:8888")
        httpd.serve_forever()

def run_crash_isolated():
    """Chay phat hien tai nan trong process rieng (tu do MPU + OBD speed)."""
    try:
        os.setpgrp()
    except AttributeError:
        pass
    from crash_detector import CrashDetector
    import time as _t
    cd = CrashDetector()
    cd.start()
    try:
        while True:
            _t.sleep(1)
    except KeyboardInterrupt:
        pass  # Da nhan Ctrl+C, thoat em diu, khong in traceback


def run_mqtt_uploader_isolated():
    """Chay day su kien (crash/alert/dtc/trip + live snapshot) len server qua MQTT, doc lap tien trinh."""
    try:
        os.setpgrp()
    except AttributeError:
        pass
    import mqtt_uploader
    mqtt_uploader.main()


def run_evidence_uploader_isolated():
    """Chay quet + nen + upload video bang chung (crash) len server, doc lap tien trinh."""
    try:
        os.setpgrp()
    except AttributeError:
        pass
    import evidence_uploader
    evidence_uploader.main()


def main():
    # Tu don tan du lan chay truoc: kill process main.py cu (tru chinh no) + port
    import subprocess, os
    _self = os.getpid()
    subprocess.run(
        f"for p in $(pgrep -f 'python.*main.py'); do [ $p -ne {_self} ] && kill -9 $p 2>/dev/null; done; "
        "fuser -k 8080/tcp 8888/tcp 2>/dev/null || true",
        shell=True,
    )
    import time as _t; _t.sleep(1)  # cho port nha ra
    from clock import sync_clock
    sync_clock()          # set đồng hồ TRƯỚC init_db và trước khi bật process con
    init_db()

    if OPERATION_MODE == "SIMULATION":
        print("🖥️ KHỞI CHẠY CHẾ ĐỘ GIẢ LẬP (SIMULATION)...")
        
        # Bật API Server trong chế độ giả lập để test Web UI
        api_process = multiprocessing.Process(
            target=run_api_isolated, name="API_Process_Sim", daemon=True
        )
        api_process.start()

        web_process = multiprocessing.Process(target=run_web_ui_server, name="WebUI_Process_Sim", daemon=True)
        web_process.start()

        ecu = ECUSimulator()
        ecu.start()
        time.sleep(1)

        reader = OBDReader()
        from obd_module.rule_engine import RuleEngine
        RuleEngine().start()
        try:
            reader.read_and_store()
        except KeyboardInterrupt:
            print("\n🛑 Nhận lệnh ngắt hệ thống (Ctrl+C)...")
        except Exception as e:
            print(f"\n❌ Lỗi giả lập: {e}")
        finally:
            print("🧹 Đang dọn dẹp hệ thống giả lập...")
            ecu.stop()
            if hasattr(reader, 'db_writer'):
                reader.db_writer.flush()
            
            # Tắt luồng API an toàn với cơ chế bảo vệ timeout
            if api_process.is_alive():
                try:
                    api_process.terminate()
                    api_process.join(timeout=5)
                    if api_process.is_alive():
                        api_process.kill()
                except Exception:
                    pass
                
            print("✅ Đã ngắt giả lập.")

    else:
        print("🚀 KHỞI CHẠY CHẾ ĐỘ THỰC TẾ (PRODUCTION)...")

        # Bật luồng dọn rác (Thread chạy nền trong tiến trình cha)
        disk_cleaner = DiskRotation(
            threshold_percent=80,
            retention_days=RETENTION_DAYS,
        )
        disk_cleaner.start()

        # Bật luồng thu Camera IP (Đã cô lập nhóm tiến trình)
        cam_process = multiprocessing.Process(
            target=run_camera_isolated, name="Cam_Process", daemon=True
        )
        cam_process.start()

        # Bật luồng đọc CAN Bus (Đã cô lập nhóm tiến trình)
        obd_process = multiprocessing.Process(
            target=run_production_reader, name="OBD_Process", daemon=True
        )
        obd_process.start()
        crash_process = multiprocessing.Process(
            target=run_crash_isolated, name="Crash_Process", daemon=True
        )
        crash_process.start()

        # Bật luồng đẩy sự kiện MQTT + upload video bằng chứng lên server (tự động, không cần chạy tay)
        mqtt_up_process = multiprocessing.Process(
            target=run_mqtt_uploader_isolated, name="MQTTUploader_Process", daemon=True
        )
        mqtt_up_process.start()

        evidence_up_process = multiprocessing.Process(
            target=run_evidence_uploader_isolated, name="EvidenceUploader_Process", daemon=True
        )
        evidence_up_process.start()

        # Bật luồng API Server FastAPI (Đã cô lập nhóm tiến trình)
        api_process = multiprocessing.Process(
            target=run_api_isolated, name="API_Process", daemon=True
        )
        api_process.start()
        web_process = multiprocessing.Process(
            target=run_web_ui_server, name="WebUI_Process", daemon=True
        )
        web_process.start()

        def _handle_sigterm(signum, frame):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, _handle_sigterm)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Nhận lệnh ngắt hệ thống (Ctrl+C)...")
        except Exception as e:
            print(f"\n❌ Lỗi hệ thống bất ngờ: {e}")
        finally:
            print("🧹 Đang dọn dẹp và giải phóng tài nguyên phần cứng...")
            
            # Quản lý chu trình tắt máy tuần tự và dứt điểm cho toàn bộ tiến trình con
            for p in [obd_process, cam_process, api_process, web_process,
                      mqtt_up_process, evidence_up_process]:
                try:
                    if p and p.is_alive():
                        p.terminate()
                        p.join(timeout=5)  # Chờ tối đa 5 giây để tiến trình hoàn tất flush ghi file .ts / DB
                        if p.is_alive():
                            print(f"⚠️ Tiến trình {p.name} không phản hồi dọn dẹp, cưỡng chế kill!")
                            p.kill()  # Force ngắt nếu vẫn bị kẹt deadlock lock nội bộ
                except Exception as e:
                    print(f"⚠️ Lỗi khi tắt tiến trình {p.name if p else 'Unknown'}: {e}")

            # crash_process rieng: co the dang trong ~15-20s cho camera ghi du
            # phan sau va cham (EVIDENCE_POST_SEC + CAMERA_LATENCY_SEC) truoc
            # khi chot goi bang chung - can nhieu thoi gian hon 4 tien trinh tren,
            # neu khong se mat bang chung cua vu tai nan vua xay ra dung luc tat may.
            try:
                if crash_process and crash_process.is_alive():
                    crash_process.terminate()
                    crash_process.join(timeout=20)
                    if crash_process.is_alive():
                        print(f"⚠️ Tiến trình {crash_process.name} không phản hồi dọn dẹp, cưỡng chế kill!")
                        crash_process.kill()
            except Exception as e:
                print(f"⚠️ Lỗi khi tắt tiến trình crash: {e}")
                
            print("✅ Tắt máy an toàn")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()