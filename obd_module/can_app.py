import can
import time
import logging
import signal
from config import (CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE, OBD_REQUEST_ID, OBD_RESPONSE_ID, TELEMETRY_SCHEMA, SAMPLING_RATE_HZ)

# SỬA LỖI IMPORT (Import Class thay vì object dùng chung)
from obd_module.db_setup import TelemetryDBWriter

logging.basicConfig(level=logging.INFO, format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s')
logger = logging.getLogger("READER")

class OBDReader:
    def __init__(self):
        self.running = True 
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        
        self.bus = self._init_can_bus()
        
        # SỬA LỖI MULTIPROCESSING: Tự tạo DB Writer riêng nằm trọn trong tiến trình con
        self.db_writer = TelemetryDBWriter()

    def _init_can_bus(self):
        try:
            return can.interface.Bus(channel=CAN_INTERFACE, bustype=CAN_BUS_TYPE, bitrate=CAN_BITRATE)
        except Exception as e:
            print(f"⚠️ [OBD INIT LỖI]: Không thể mở cổng CAN ({e})")
            return None

    def _handle_shutdown(self, signum, frame):
        print("\n🔌 Nhận tín hiệu tắt máy. Đang dọn dẹp CAN Bus...")
        self.running = False
        # Đảm bảo flush data trước khi tắt
        self.db_writer.flush()

    def send_request(self, pid: int):
        if not self.bus:
            return
        msg = can.Message(arbitration_id=OBD_REQUEST_ID, data=[0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00], is_extended_id=False)
        try: self.bus.send(msg)
        except: pass

    def decode_response(self, pid, data):
        if len(data) < 4: return None
        A = data[3]
        if pid == 0x0D: return float(A)
        elif pid == 0x0C:
            B = data[4] if len(data) > 4 else 0
            return ((A * 256) + B) / 4.0
        elif pid == 0x11: return (A * 100.0) / 255.0
        elif pid == 0x05: return float(A - 40)
        return None

    def read_and_store(self):
        interval = 1.0 / SAMPLING_RATE_HZ
        print("🚗 Tiến trình đọc CAN Bus đã khởi động.")
        
        while self.running:
            try:
                if self.bus is None:
                    print("⏳ [OBD] Đang thử kết nối lại cổng phần cứng CAN...")
                    self.bus = self._init_can_bus()
                    if self.bus is None:
                        time.sleep(3)
                        continue
                    else:
                        print("✅ [OBD] Đã kết nối lại CAN Bus thành công!")

                cycle_start = time.monotonic()
                for pid in TELEMETRY_SCHEMA.keys():
                    self.send_request(pid)
                
                deadline = cycle_start + interval
                while time.monotonic() < deadline:
                    msg = self.bus.recv(timeout=0.005)
                    if msg and msg.arbitration_id == OBD_RESPONSE_ID:
                        exact_time = time.time()
                        if msg.data[1] == 0x41:
                            pid = msg.data[2]
                            val = self.decode_response(pid, msg.data)
                            if val is not None:
                                # Dùng self.db_writer thay vì db_writer dùng chung
                                self.db_writer.enqueue(exact_time, hex(pid), TELEMETRY_SCHEMA[pid]['label'], val, TELEMETRY_SCHEMA[pid]['unit'])
                
                wait = interval - (time.monotonic() - cycle_start)
                if wait > 0:
                    time.sleep(wait)

            except (can.CanError, OSError) as e:
                if self.running:
                    print(f"⚠️ [OBD ĐỨT KẾT NỐI]: Mất tín hiệu vật lý với phần cứng ({e})")
                    print("⏳ [OBD] Vui lòng cắm lại cáp. Quét lại sau 3s...")
                    try: self.bus.shutdown()
                    except: pass
                    self.bus = None 
                    time.sleep(3)
            
            except Exception as e:
                if self.running:
                    print(f"❌ [OBD LỖI HỆ THỐNG]: {e}")
                    time.sleep(3)