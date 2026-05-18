# CHỨC NĂNG: Gửi lệnh yêu cầu OBD-II chủ động, quét dải phản hồi chuẩn của các ECU
# ==============================================================================
import time
import sys
import can

CHANNEL = '/dev/ttyACM0'
BITRATE = 500000

print("="*60)
print("=== PHASE 2: CHẨN ĐOÁN HỎI - ĐÁP OBD-II ===")
print("="*60)

bus = None
try:
    bus = can.interface.Bus(bustype='slcan', channel=CHANNEL, bitrate=BITRATE)
    
    try:
        bus._serial_port.flushInput()
    except AttributeError:
        pass
        
    # Gửi gói tin quảng bá yêu cầu cung cấp RPM (PID 0x0C) tới ID Loa Phường 0x7DF
    req_msg = can.Message(arbitration_id=0x7DF, data=[2, 1, 0x0C, 0, 0, 0, 0, 0], is_extended_id=False)
    print("[+] Đang bắn lệnh truy vấn vòng tua máy RPM (PID 0x0C)...")
    bus.send(req_msg)
    
    ecu_responded = False
    start_time = time.time()

    # Chờ phản hồi chủ động từ xe trong tối đa 3 giây
    while time.time() - start_time < 3:
        res_msg = bus.recv(timeout=0.25)
        if res_msg is None:
            continue
            
        # Quét bộ lọc dải ID chuẩn của ECU Động cơ (từ 0x7E8 đến 0x7EF)
        if 0x7E8 <= res_msg.arbitration_id <= 0x7EF:
            if len(res_msg.data) >= 5 and res_msg.data[2] == 0x0C:
                A, B = res_msg.data[3], res_msg.data[4]
                rpm = ((A * 256) + B) / 4
                print(f"[+] PASS: Nhận phản hồi từ ECU {hex(res_msg.arbitration_id)} -> Vòng tua hiện tại = {rpm} RPM")
                ecu_responded = True
                break
                
    if not ecu_responded:
        print("[-] LỖI [OBD_TIMEOUT]: Xe không phản hồi. Có thể cổng OBD-II bị bảo mật bởi Security Gateway.")
        sys.exit(1)

except can.CanOperationError as e:
    print("[-] LỖI GỬI LỆNH [CAN_BUS_OFF]: Phần cứng ngắt đường truyền khi cố gắng ghi gói tin.")
    print(f"    [CHI TIẾT]: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[-] LỖI HỆ THỐNG: {e}")
    sys.exit(1)
finally:
    if bus is not None:
        bus.shutdown()
        print("[*] Đã ngắt kết nối Bus an toàn.")