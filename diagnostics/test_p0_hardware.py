# CHỨC NĂNG: Kiểm tra tầng vật lý OS, phân quyền và tranh chấp cổng Serial
import os
import sys
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config import CAN_INTERFACE, OPERATION_MODE

print("="*60)
print("=== PHASE 0: CHẨN ĐOÁN LỖI PHẦN CỨNG & HỆ ĐIỀU HÀNH ===")
print("="*60)

if OPERATION_MODE == "SIMULATION":
    print(f"[+] Chế độ {OPERATION_MODE}: Bỏ qua kiểm tra phần cứng vật lý.")
    sys.exit(0)

# 1. Kiểm tra sự tồn tại vật lý của thiết bị phần cứng cắm qua cổng USB
if not os.path.exists(CAN_INTERFACE):
    print(f"[-] LỖI [OS_ERR_01]: Không tìm thấy cổng vật lý {CAN_INTERFACE}.")
    print("    [KHẮC PHỤC]: Kiểm tra lại dây cáp USB nối từ module CAN sang thiết bị.")
    sys.exit(1)

# 2. Cảnh báo quyền truy cập Linux
if not os.access(CAN_INTERFACE, os.R_OK) or not os.access(CAN_INTERFACE, os.W_OK):
    print(f"[!] CẢNH BÁO [OS_WARN]: Thiếu quyền truy cập vào {CAN_INTERFACE}. Hệ thống sẽ thử mở trực tiếp.")

# 3. Kiểm tra tranh chấp cổng
try:
    import serial
    s = serial.Serial(CAN_INTERFACE)
    s.close()
    print(f"[+] Cổng {CAN_INTERFACE} rảnh, phân quyền OS OK. Không bị tranh chấp.")
except serial.SerialException as e:
    print(f"[-] LỖI [OS_ERR_03]: Cổng bị chiếm dụng (Device Busy) hoặc lỗi quyền. Chi tiết: {e}")
    print(f"    [KHẮC PHỤC]: sudo chmod a+rw {CAN_INTERFACE} HOẶC sudo fuser -k {CAN_INTERFACE}")
    sys.exit(1)

print("[+] PHASE 0 PASS: Tầng vật lý và OS sạch.")