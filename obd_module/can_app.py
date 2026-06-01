import can
import time
import logging
import signal
from config import (CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE, OBD_REQUEST_ID, OBD_RESPONSE_ID, TELEMETRY_SCHEMA, SAMPLING_RATE_HZ)
from obd_module.db_setup import TelemetryDBWriter

logging.basicConfig(level=logging.INFO, format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s')
logger = logging.getLogger("READER")

# Timeout chờ response mỗi PID (giây)
# 4 PID × 40ms = 160ms/cycle → thực tế ~5Hz max
PID_TIMEOUT_SEC = 0.04

class OBDReader:
    def __init__(self):
        self.running = True
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        self.bus = self._init_can_bus()
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
        self.db_writer.flush()

    def send_request(self, pid: int):
        if not self.bus:
            return
        msg = can.Message(
            arbitration_id=OBD_REQUEST_ID,
            data=[0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00],
            is_extended_id=False
        )
        try:
            self.bus.send(msg)
        except Exception:
            pass

    def decode_response(self, pid, data):
        if len(data) < 4:
            return None
        A = data[3]
        if pid == 0x0D:
            return float(A)
        elif pid == 0x0C:
            B = data[4] if len(data) > 4 else 0
            return ((A * 256) + B) / 4.0
        elif pid == 0x11:
            return (A * 100.0) / 255.0
        elif pid == 0x05:
            return float(A - 40)
        return None

    def read_pid_sequential(self, pid: int) -> float | None:
        """
        Bắn 1 PID → chờ đúng response của PID đó → trả về giá trị.
        Timeout sau PID_TIMEOUT_SEC giây, không block vòng lặp chính.
        """
        self.send_request(pid)
        deadline = time.monotonic() + PID_TIMEOUT_SEC

        while time.monotonic() < deadline:
            msg = self.bus.recv(timeout=0.005)
            if msg is None:
                continue
            if msg.arbitration_id != OBD_RESPONSE_ID:
                continue
            if len(msg.data) < 3:
                continue
            if msg.data[1] != 0x41:
                continue
            if msg.data[2] != pid:          # lọc đúng PID, tránh nhận nhầm
                continue
            return self.decode_response(pid, msg.data)

        logger.warning(f"⏱️ Timeout PID {hex(pid)} — bỏ qua cycle này")
        return None

    def read_and_store(self):
        interval = 1.0 / SAMPLING_RATE_HZ
        print("🚗 Tiến trình đọc CAN Bus đã khởi động (sequential mode).")

        while self.running:
            try:
                # --- Kiểm tra / reconnect phần cứng ---
                if self.bus is None:
                    print("⏳ [OBD] Đang thử kết nối lại cổng phần cứng CAN...")
                    self.bus = self._init_can_bus()
                    if self.bus is None:
                        time.sleep(3)
                        continue
                    print("✅ [OBD] Đã kết nối lại CAN Bus thành công!")

                cycle_start = time.monotonic()

                # --- Bắn tuần tự: 1 PID → nhận xong → PID tiếp theo ---
                for pid in TELEMETRY_SCHEMA.keys():
                    val = self.read_pid_sequential(pid)
                    if val is not None:
                        self.db_writer.enqueue(
                            time.time(),
                            hex(pid),
                            TELEMETRY_SCHEMA[pid]['label'],
                            val,
                            TELEMETRY_SCHEMA[pid]['unit']
                        )

                # --- Điều tiết tần suất ---
                elapsed = time.monotonic() - cycle_start
                wait = interval - elapsed
                if wait > 0:
                    time.sleep(wait)
                else:
                    logger.warning(
                        f"⚡ Cycle vượt interval {interval*1000:.0f}ms "
                        f"(thực tế {elapsed*1000:.0f}ms) — "
                        f"cân nhắc giảm SAMPLING_RATE_HZ"
                    )

            except (can.CanError, OSError) as e:
                if self.running:
                    print(f"⚠️ [OBD ĐỨT KẾT NỐI]: Mất tín hiệu vật lý với phần cứng ({e})")
                    print("⏳ [OBD] Vui lòng cắm lại cáp. Quét lại sau 3s...")
                    try:
                        self.bus.shutdown()
                    except Exception:
                        pass
                    self.bus = None
                    time.sleep(3)

            except Exception as e:
                if self.running:
                    print(f"❌ [OBD LỖI HỆ THỐNG]: {e}")
                    time.sleep(3)