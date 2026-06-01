# CHỨC NĂNG: Kiểm tra tầng vật lý OS, phân quyền và tranh chấp cổng Serial của Pi
# ==============================================================================
import os
import sys

CHANNEL = '/dev/ttyACM0'

print("="*60)
print("=== PHASE 0: CHẨN ĐOÁN LỖI PHẦN CỨNG & HỆ ĐIỀU HÀNH ===")
print("="*60)

# 1. Kiểm tra sự tồn tại vật lý của thiết bị phần cứng cắm qua cổng USB
if not os.path.exists(CHANNEL):
    print("[-] LỖI [OS_ERR_01]: Không tìm thấy cổng vật lý ttyACM0.")
    print("    [KHẮC PHỤC]: Kiểm tra lại dây cáp USB nối từ CANable sang Orange Pi.")
    sys.exit(1)

# 2. Cảnh báo quyền truy cập Linux (Tránh lỗi False Negative khi chạy môi trường đặc biệt)
if not os.access(CHANNEL, os.R_OK) or not os.access(CHANNEL, os.W_OK):
    print("[!] CẢNH BÁO [OS_WARN]: os.access báo thiếu quyền, hệ thống sẽ thử mở cổng trực tiếp.")

# 3. Kiểm tra tranh chấp cổng (Bài test phân quyền và chiếm dụng phần cứng thực tế)
try:
    import serial
    s = serial.Serial(CHANNEL)
    s.close()
    print("[+] Cổng rảnh, phân quyền hệ điều hành OK. Không bị tranh chấp.")
except serial.SerialException as e:
    print(f"[-] LỖI [OS_ERR_03]: Cổng bị chiếm dụng (Device Busy) hoặc lỗi quyền truy cập. Chi tiết: {e}")
    print("    [KHẮC PHỤC]: Chạy lệnh 'sudo chmod a+rw /dev/ttyACM0' HOẶC giải phóng cổng: 'sudo fuser -k /dev/ttyACM0'")
    sys.exit(1)

print("[+] PHASE 0 PASS: Tầng vật lý và OS sạch.")