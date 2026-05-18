# CHỨC NĂNG: Stress test đa luồng PID, bảo vệ eMMC/Thẻ nhớ, xử lý lệch nhịp phản hồi ECU
# ==============================================================================
import os
import time
import sqlite3
import sys
import can

CHANNEL = '/dev/ttyACM0'
BITRATE = 500000
DB_PATH = 'obd_module/obd_data.db'

print("="*60)
print("=== PHASE 3: STRESS TEST & DATABASE TRUY VẤN CHU KỲ ===")
print("="*60)

# Xác thực thư mục lưu trữ DB an toàn
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    print(f"[-] LỖI [DB_DIR]: Thư mục '{db_dir}' không tồn tại trên bộ nhớ. Vui lòng tạo thư mục trước.")
    sys.exit(1)

conn = None
bus = None
try:
    conn = sqlite3.connect(DB_PATH)
    conn_cursor = conn.cursor()
    
    # Kích hoạt chế độ WAL (Write-Ahead Logging) tăng tốc độ ghi và bảo vệ cấu trúc file khi sập nguồn
    conn_cursor.execute("PRAGMA journal_mode=WAL")
    wal_status = conn_cursor.fetchone()
    if wal_status and wal_status[0].lower() != 'wal':
        print(f"[!] CẢNH BÁO [DB_WAL_WARN]: File hệ thống không hỗ trợ WAL mode (Hiện tại: {wal_status[0]}). Tốc độ ghi có thể bị giảm.")
    else:
        print("[+] Kích hoạt thành công chế độ WAL bảo vệ lưu trữ dữ liệu.")

    bus = can.interface.Bus(bustype='slcan', channel=CHANNEL, bitrate=BITRATE)
    
    try:
        bus._serial_port.flushInput()
    except AttributeError:
        pass

    # Đã nới rộng thời gian lên 10 giây để đảm bảo kích thước mẫu thử (Sample Size) đủ độ tin cậy thống kê
    print("[+] Bắt đầu quét đa thông số (Speed, RPM, Throttle) chu kỳ liên tục trong 10 giây...")
    pids = {0x0D: 'speed', 0x0C: 'rpm', 0x11: 'throttle'}
    start_time = time.time()
    rows_inserted = 0
    total_iterations = 0

    while time.time() - start_time < 10:
        current_data = {'speed': 0, 'rpm': 0, 'throttle': 0, 'brake': 0, 'raw_hex': ''}
        total_iterations += 1
        successful_pids = 0
        
        for pid in pids.keys():
            req = can.Message(arbitration_id=0x7DF, data=[2, 1, pid, 0, 0, 0, 0, 0], is_extended_id=False)
            bus.send(req)
            
            # Đặt timeout 0.3s để bù trừ độ trễ chuyển đổi qua cầu USB-Serial
            res = bus.recv(timeout=0.3)
            
            if res and 0x7E8 <= res.arbitration_id <= 0x7EF and len(res.data) >= 5:
                # Bắt và chẩn đoán lỗi lệch nhịp dữ liệu (Asynchronous Response)
                if res.data[2] == pid:
                    current_data['raw_hex'] += res.data.hex() + "|"
                    successful_pids += 1
                    
                    if pid == 0x0D:
                        current_data['speed'] = res.data[3]
                    elif pid == 0x0C:
                        current_data['rpm'] = ((res.data[3] * 256) + res.data[4]) / 4
                    elif pid == 0x11:
                        current_data['throttle'] = (res.data[3] * 100) / 255
                else:
                    print(f"    [!] CẢNH BÁO [ASYNC_ECU]: Đang đợi phản hồi PID {hex(pid)} nhưng nhận được kết quả muộn của PID {hex(res.data[2])}. Bỏ qua gói lỗi nhịp.")
            
            # Cung cấp 10ms khoảng nghỉ (Inter-query delay) để các ECU đời cũ không bị quá tải bộ nhớ
            time.sleep(0.01)
                    
        # Chỉ tiến hành ghi và commit dữ liệu thực tế vào DB nếu chu kỳ đó thu thập được ít nhất một biến sạch từ xe
        if successful_pids > 0:
            conn_cursor.execute("INSERT INTO car_logs (timestamp, speed, rpm, throttle, brake, raw_hex) VALUES (?, ?, ?, ?, ?, ?)",
                           (time.time(), current_data['speed'], current_data['rpm'], current_data['throttle'], current_data['brake'], current_data['raw_hex']))
            # Ghi trực tiếp ngay trong vòng lặp để tránh hiện tượng mất trắng dữ liệu khi xe rung lắc làm sập nguồn Pi đột ngột
            conn.commit()
            rows_inserted += 1

    # Đánh giá hiệu năng dựa trên chất lượng phản hồi thực tế của ECU
    success_rate = (rows_inserted / total_iterations) * 100 if total_iterations > 0 else 0
    print(f"[+] Quét hoàn tất. Tổng số chu kỳ thử nghiệm: {total_iterations}, Ghi nhận dữ liệu thực: {rows_inserted}")
    print(f"    -> Tỉ lệ thành công đạt: {success_rate:.1f}%")

    if success_rate >= 70.0:
        print("\n[==> MILESTONE ĐẠT: CHUỖI TOÀN BỘ QUY TRÌNH XE THẬT HOÀN HẢO! <=]")
    else:
        print("[-] LỖI HIỆU NĂNG [PERF_LOW_ERR]: Tỉ lệ phản hồi dưới 70%. Cần xem lại kết nối cơ học hoặc tăng thời gian trễ.")

except can.CanOperationError as e:
    print("[-] LỖI QUÉT DỮ LIỆU [CAN_BUS_OFF]: Thiết bị ngắt đột ngột (Bus-off) trong quá trình đo chu kỳ.")
    print(f"    [CHI TIẾT]: {e}")
except sqlite3.Error as e:
    print(f"[-] LỖI TRUY XUẤT SQLITE: {e}")
except Exception as e:
    print(f"[-] LỖI HỆ THỐNG KHÔNG XÁC ĐỊNH: {e}")
finally:
    # Cơ chế đóng cưỡng bức an toàn mọi kết nối độc lập trước khi thoát tiến trình
    if conn is not None:
        conn.close()
    if bus is not None:
        bus.shutdown()
    print("[*] Đã đóng và khóa an toàn toàn bộ kết nối cơ sở dữ liệu và CAN Bus.")