# CHỨC NĂNG: Chẩn đoán kết nối mạng LAN và test kéo luồng Frame từ Camera IP
# ==============================================================================
import os
import sys
import time
import subprocess
import re
import cv2

# Thêm thư mục gốc vào đường dẫn hệ thống để gọi được file config.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config import VIDEO_SOURCE, OPERATION_MODE
except ImportError:
    print("[-] LỖI: Không tìm thấy file config.py ở thư mục gốc.")
    sys.exit(1)

print("="*60)
print("=== PHASE 4: CHẨN ĐOÁN KẾT NỐI CAMERA IP (RTSP) ===")
print("="*60)

if OPERATION_MODE == "SIMULATION":
    print(f"[*] CẢNH BÁO: Đang ở chế độ SIMULATION. Script sẽ test file video mẫu: {VIDEO_SOURCE}")
else:
    # 1. Bóc tách IP Camera từ đường dẫn RTSP trong config
    match = re.search(r'rtsp://([0-9\.]+):', VIDEO_SOURCE)
    if not match:
        print("[-] LỖI [CAM_ERR_01]: Không tìm thấy IP hợp lệ trong config.VIDEO_SOURCE")
        sys.exit(1)
    
    camera_ip = match.group(1)
    print(f"[*] Đường dẫn RTSP lấy từ config: {VIDEO_SOURCE}")
    print(f"[*] Đã bóc tách IP Camera: {camera_ip}")

    # 2. Ping kiểm tra tầng mạng vật lý (LAN/Wi-Fi)
    print("\n[+] Bước 1: Đang Ping kiểm tra kết nối mạng từ Orange Pi tới Camera...")
    ping_cmd = ["ping", "-c", "3", "-W", "2", camera_ip]
    try:
        # Chạy lệnh ping ẩn, chỉ lấy kết quả
        result = subprocess.run(ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            print(f"[-] LỖI [CAM_ERR_02]: KHÔNG THỂ PING TỚI {camera_ip}.")
            print("    [KHẮC PHỤC]: Kiểm tra lại dây mạng LAN nối từ Cam vào Pi, hoặc xem hai thiết bị đã cùng dải mạng 192.168.1.x chưa.")
            sys.exit(1)
        print("    -> PASS: Ping OK! Orange Pi đã nhìn thấy Camera trên mạng nội bộ.")
    except Exception as e:
        print(f"[-] LỖI PING HỆ THỐNG: {e}")
        sys.exit(1)

# 3. Test mở luồng RTSP bằng thư viện Thị giác máy tính (OpenCV)
print("\n[+] Bước 2: Đang kết nối thử vào cổng RTSP và bóc tách khung hình...")

# Thiết lập timeout cho OpenCV, tránh bị treo vô hạn nếu mạng lag
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;5000"

start_time = time.time()
# Mở luồng video
cap = cv2.VideoCapture(VIDEO_SOURCE, cv2.CAP_FFMPEG)

if not cap.isOpened():
    print("[-] LỖI [CAM_ERR_03]: Mạng thông nhưng Camera từ chối kết nối RTSP.")
    print("    [KHẮC PHỤC]: Kiểm tra lại sai User/Pass, sai port 554, hoặc Camera IP chưa bật chuẩn ONVIF/RTSP trong cài đặt.")
    sys.exit(1)

# 4. Stress-test nhẹ: Đọc thử 15 frames liên tiếp để check độ mượt
print("    -> PASS: Đã mở được luồng RTSP. Đang kéo 15 khung hình test hiệu năng...")
success_frames = 0
height, width = 0, 0

for i in range(15):
    ret, frame = cap.read()
    if ret:
        success_frames += 1
        height, width = frame.shape[:2]

cap.release()
elapsed = time.time() - start_time

# 5. Báo cáo kết quả
if success_frames > 0:
    print(f"\n[==> MILESTONE ĐẠT: CAMERA HOẠT ĐỘNG HOÀN HẢO! <=]")
    print(f"    - Độ phân giải Frame: {width} x {height} pixels")
    print(f"    - Lấy thành công:     {success_frames}/15 frames")
    print(f"    - Thời gian test:     {elapsed:.2f} giây")
else:
    print("[-] LỖI [CAM_ERR_04]: Mở được RTSP nhưng stream bị đen/rỗng. Có thể do nghẽn băng thông phần cứng.")
    sys.exit(1)