# CHỨC NĂNG: Nghe lén mạng CAN thụ động, xác định xe nổ máy, lọc nhiễu bộ đệm rác
# ==============================================================================
import time
import sys
import can

CHANNEL = '/dev/ttyACM0'
BITRATE = 500000

print("="*60)
print("=== PHASE 1: CHẨN ĐOÁN MẠNG CAN THỤ ĐỘNG ===")
print("="*60)

bus = None
try:
    # FIX: interface= thay vì bustype= (deprecated từ python-can v4.2.0)
    bus = can.interface.Bus(channel=CHANNEL, interface='slcan', bitrate=BITRATE)

    # Drain buffer rác từ phiên trước
    print("[*] Đang drain buffer rác từ phiên trước...")
    drain_deadline = time.monotonic() + 0.3   # FIX: monotonic() thay time() cho đồng bộ với canapp.py
    drained = 0
    while time.monotonic() < drain_deadline:
        stale = bus.recv(timeout=0)
        if stale is None:
            break
        drained += 1
    if drained > 0:
        print(f"[!] Đã loại bỏ {drained} gói rác khỏi buffer.")

    print(f"[+] Đang lắng nghe mạng CAN ({BITRATE} bps). Vui lòng nổ máy xe...")

    start_time = time.monotonic()
    valid_frames = 0
    has_frame = False

    # Lắng nghe thụ động trong tối đa 8 giây
    while time.monotonic() - start_time < 8:
        msg = bus.recv(timeout=0.5)
        if msg is not None:
            print(f"[+] DỮ LIỆU THÔ [ID: {hex(msg.arbitration_id)} | Data: {msg.data.hex()}]")
            valid_frames += 1
            # Đòi hỏi tối thiểu 3 frame sạch để loại trừ xung nhiễu điện khi cắm giắc
            if valid_frames >= 3:
                has_frame = True
                break

    if not has_frame:
        print(f"[-] LỖI [CAN_SILENT_ERR]: Mạng im lặng hoàn toàn hoặc nhiễu nặng (Nhận {valid_frames}/3 frames).")
        sys.exit(1)

    print("[+] PHASE 1 PASS: Mạng CAN có tín hiệu, xe đã nổ máy.")

except can.CanOperationError as e:
    print("[-] LỖI CHÍ MẠNG [CAN_BUS_OFF]: Khối phần cứng CAN controller tự đóng ngắt để bảo vệ.")
    print("    [NGUYÊN NHÂN]: Thường do sai Bitrate cấu hình, lỏng dây CAN-H/CAN-L, hoặc thiếu điện trở đầu cuối 120 Ohm.")
    print(f"    [CHI TIẾT]: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[-] LỖI KHỞI TẠO HOẶC ĐỌC BUS: {e}")
    sys.exit(1)
finally:
    if bus is not None:
        bus.shutdown()
        print("[*] Đã ngắt kết nối Bus an toàn.")