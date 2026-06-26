 # CHỨC NĂNG: Nghe lén mạng CAN thụ động, xác định xe nổ máy, lọc nhiễu bộ đệm rác
import time
import os
import sys
import can
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config import CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE

print("="*60)
print("=== PHASE 1: CHẨN ĐOÁN MẠNG CAN THỤ ĐỘNG ===")
print("="*60)

bus = None
try:
    bus = can.interface.Bus(channel=CAN_INTERFACE, interface=CAN_BUS_TYPE, bitrate=CAN_BITRATE)

    print("[*] Đang drain buffer rác từ phiên trước...")
    drain_deadline = time.time() + 0.3
    drained = 0
    while time.time() < drain_deadline:
        if bus.recv(timeout=0) is None: break
        drained += 1
    if drained > 0: print(f"[!] Đã loại bỏ {drained} gói rác.")

    print(f"[+] Đang lắng nghe mạng CAN trên {CAN_INTERFACE} ({CAN_BITRATE} bps). Vui lòng nổ máy xe...")

    start_time = time.time()
    valid_frames = 0
    has_frame = False

    while time.time() - start_time < 8:
        msg = bus.recv(timeout=0.5)
        if msg is not None:
            print(f"[+] DỮ LIỆU THÔ [ID: {hex(msg.arbitration_id)} | Data: {msg.data.hex()}]")
            valid_frames += 1
            if valid_frames >= 3:
                has_frame = True
                break

    if not has_frame:
        print(f"[-] LỖI [CAN_SILENT_ERR]: Mạng im lặng hoàn toàn hoặc nhiễu nặng.")
        sys.exit(1)

    print("[+] PHASE 1 PASS: Mạng CAN có tín hiệu, xe đã nổ máy.")

except can.CanOperationError as e:
    print("[-] LỖI CHÍ MẠNG [CAN_BUS_OFF]: Khối phần cứng CAN controller tự đóng ngắt.")
    print(f"    [CHI TIẾT]: {e}")
    sys.exit(1)
finally:
    if bus is not None:
        bus.shutdown()
        print("[*] Đã ngắt kết nối Bus an toàn.")