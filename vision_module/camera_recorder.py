import subprocess
import os
import signal
import time
from config import VIDEO_SOURCE, STORAGE_DIR, OPERATION_MODE

class CameraRecorder:
    def __init__(self, segment_time=60):
        """
        segment_time: Độ dài mỗi file .ts (giây). Mặc định 60s/file.
        """
        self.process = None
        self.running = True
        self.segment_time = segment_time
        # Tên file chứa timestamp gốc để dễ đồng bộ sau này
        self.output_pattern = str(STORAGE_DIR / "cam_%Y%m%d_%H%M%S.ts")

        # Đón tín hiệu ngắt an toàn từ Linux
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        if getattr(self, '_is_shutting_down', False):
            return
        self._is_shutting_down = True

        print("\n📷 Nhận tín hiệu tắt máy. Đang đóng gói file .ts cuối cùng...")
        self.running = False
        self.stop()

    def start(self):
        if OPERATION_MODE == "SIMULATION":
            print("🖥️ Chế độ SIMULATION: Bỏ qua ghi luồng Camera IP.")
            return

        print(f"🎥 Bắt đầu tiến trình Camera. (Lưu file .ts mỗi {self.segment_time}s)")
        
        while self.running:
            try:
                print("🎥 [CAMERA] Đang kết nối luồng RTSP...")
                command = [
                    "ffmpeg",
                    "-y",                               # Ghi đè nếu trùng tên
                    "-rtsp_transport", "tcp",           # Ép dùng TCP để tránh rớt gói tin
                    "-i", VIDEO_SOURCE,
                    "-c", "copy",                       # Stream copy trực tiếp (0% CPU Encode)
                    "-f", "segment",                    # Bật chế độ cắt file
                    "-segment_time", str(self.segment_time),
                    "-reset_timestamps", "1",
                    "-strftime", "1",                   # Bật parse time cho tên file
                    self.output_pattern
                ]
                
                # Chạy nền FFmpeg, ẩn log rác để không làm trôi log của CAN Bus
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

                # Giữ luồng sống và giám sát tiến trình FFmpeg
                while self.running and self.process.poll() is None:
                    time.sleep(1)
                
                # Nếu chạy đến đây mà self.running vẫn True, nghĩa là FFmpeg tự crash
                if self.running:
                    print("⚠️ [CAMERA LỖI] Đứt kết nối luồng RTSP (Rút dây LAN / Cam sập).")
                    print("⏳ [CAMERA] Đang thử kết nối lại sau 5 giây...")
                    time.sleep(5)

            except Exception as e:
                if self.running:
                    print(f"❌ [CAMERA ERROR] Lỗi hệ thống bất ngờ: {e}. Thử lại sau 5s...")
                    time.sleep(5)

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            print("✅ Đã ngắt luồng RTSP an toàn.")

def run_camera_recorder():
    """Hàm wrapper để gọi bằng multiprocessing."""
    recorder = CameraRecorder(segment_time=60)
    recorder.start()