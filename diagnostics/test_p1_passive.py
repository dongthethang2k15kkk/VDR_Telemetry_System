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
    # Khởi tạo kết nối slcan
    bus = can.interface.Bus(bustype='slcan', channel=CHANNEL, bitrate=BITRATE)
    
    # Xử lý an toàn Private API để xóa bộ đệm rác từ phiên làm việc trước
    try:
        bus._serial_port.flushInput()
        bus._serial_port.flushOutput()
    except AttributeError:
        print("[!] CẢNH BÁO: Bộ thư viện python-can đã nâng cấp, bỏ qua bước flush bộ đệm.")
        
    print(f"[+] Đang lắng nghe mạng CAN ({BITRATE} bps). Vui lòng nổ máy xe...")
    
    start_time = time.time()
    valid_frames = 0
    has_frame = False

    # Lắng nghe thụ động trong tối đa 8 giây
    while time.time() - start_time < 8:
        msg = bus.recv(timeout=0.5)
        if msg is not None:
            print(f"[+] DỮ LIỆU THÔ [ID: {hex(msg.arbitration_id)} | Data: {msg.data.hex()}]")
            valid_frames += 1
            # Đòi hỏi tối thiểu 3 frame sạch để loại trừ các xung nhiễu điện khi cắm giắc
            if valid_frames >= 3:
                has_frame = True
                break

    if not has_frame:
        print(f"[-] LỖI [CAN_SILENT_ERR]: Mạng im lặng hoàn toàn hoặc nhiễu nặng (Nhận {valid_frames}/3 frames).")
        sys.exit(1)

except can.CanOperationError as e:
    # Bẫy lỗi phần cứng tự ngắt khi gặp sự cố chập mạch, sai bitrate
    print("[-] LỖI CHÍ MẠNG [CAN_BUS_OFF]: Khối phần cứng CAN controller tự đóng ngắt để bảo vệ.")
    print("    [NGUYÊN NHÂN]: Thường do sai Bitrate cấu hình, lỏng dây CAN-H/CAN-L, hoặc thiếu điện trở đầu cuối 120 Ohm.")
    print(f"    [CHI TIẾT]: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[-] LỖI KHỞI TẠO HOẶC ĐỌC BUS: {e}")
    sys.exit(1)
finally:
    # Đảm bảo tài nguyên phần cứng luôn được giải phóng an toàn kể cả khi chương trình crash
    if bus is not None:
        bus.shutdown()
        print("[*] Đã ngắt kết nối Bus an toàn.")