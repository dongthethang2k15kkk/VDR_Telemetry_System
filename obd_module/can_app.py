import can
import time
import logging
import signal
from config import (
    CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE,
    OBD_REQUEST_ID, OBD_RESPONSE_ID,
    TELEMETRY_SCHEMA, SAMPLING_RATE_HZ, DATABASE_PATH, PID_RESPONSE_TIMEOUT,
    HEALTH_EWMA_ALPHA_LAT, HEALTH_EWMA_ALPHA_MISS, HEALTH_PERSIST_SEC,
)
from obd_module.db_setup import TelemetryDBWriter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s'
)
logger = logging.getLogger("READER")

# Timeout đợi phản hồi mỗi PID (giây)
# 7 PID × 0.1s = 0.7s/chu kỳ tối đa → SAMPLING_RATE_HZ = 1 là an toàn


# Task2b: 20+ ma loi DTC pho bien (OBD-II standard)
DTC_DESCRIPTIONS = {
    "P0171": "Hệ thống quá loãng (Bank 1)",
    "P0172": "Hệ thống quá giàu (Bank 1)",
    "P0174": "Hệ thống quá loãng (Bank 2)",
    "P0300": "Phát hiện kích nổ ngẫu nhiên / nhiều xi-lanh",
    "P0301": "Kích nổ xi-lanh 1",
    "P0302": "Kích nổ xi-lanh 2",
    "P0303": "Kích nổ xi-lanh 3",
    "P0304": "Kích nổ xi-lanh 4",
    "P0420": "Hiệu suất bộ xúc tác thấp (Bank 1)",
    "P0430": "Hiệu suất bộ xúc tác thấp (Bank 2)",
    "P0101": "Cảm biến MAF sai phạm vi / hiệu suất",
    "P0102": "Cảm biến MAF tín hiệu thấp",
    "P0113": "Cảm biến nhiệt độ khí nạp tín hiệu cao",
    "P0116": "Cảm biến nhiệt độ nước làm mát sai phạm vi",
    "P0117": "Cảm biến nhiệt độ nước làm mát tín hiệu thấp",
    "P0118": "Cảm biến nhiệt độ nước làm mát tín hiệu cao",
    "P0128": "Nhiệt độ nước làm mát dưới ngưỡng điều nhiệt",
    "P0130": "Mạch cảm biến Oxy (Bank 1 Sensor 1)",
    "P0131": "Cảm biến Oxy điện áp thấp (Bank 1 Sensor 1)",
    "P0133": "Cảm biến Oxy phản hồi chậm (Bank 1 Sensor 1)",
    "P0135": "Mạch sấy cảm biến Oxy (Bank 1 Sensor 1)",
    "P0442": "Rò rỉ hệ thống EVAP (nhỏ)",
    "P0455": "Rò rỉ hệ thống EVAP (lớn)",
    "P0506": "Tốc độ không tải thấp hơn mong đợi",
    "P0562": "Điện áp hệ thống thấp",
    "P0563": "Điện áp hệ thống cao",
}


class OBDReader:
    def __init__(self):
        self.running = True
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        self.bus = self._init_can_bus()
        self.db_writer = TelemetryDBWriter()


        # Snapshot trạng thái xe — tự động khớp với TELEMETRY_SCHEMA
        self.vehicle_snapshot = {pid: None for pid in TELEMETRY_SCHEMA.keys()}
        self._h_lat = {}; self._h_miss = {}; self._h_last_persist = 0.0

    # ──────────────────────────────────────────────
    # PHẦN CỨNG
    # ──────────────────────────────────────────────

    def _init_can_bus(self):
        try:
            return can.interface.Bus(
                channel=CAN_INTERFACE,
                interface=CAN_BUS_TYPE,
                bitrate=CAN_BITRATE,
                # Hardware filter: chi nhan phan hoi OBD (0x7E8-0x7EF), bo qua
                # bus chinh cua xe (~2000 frame/s) ngay o tang CANable -> tranh
                # ngap buffer slcan (loi \r) + het miss tren xe tai cao.
                can_filters=[{"can_id": 0x7E8, "can_mask": 0x7F8}]
            )
        except Exception as e:
            print(f"⚠️  [OBD INIT LỖI]: Không thể mở cổng CAN ({e})")
            return None

    def _handle_shutdown(self, signum, frame):
        # Chốt chặn: Nếu đang dọn dẹp rồi thì return luôn
        if getattr(self, '_is_shutting_down', False):
            return
        self._is_shutting_down = True

        print("\n🔌 Nhận tín hiệu tắt máy. Đang dọn dẹp CAN Bus...")
        self.running = False
        self.db_writer.flush()
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass

    # ──────────────────────────────────────────────
    # GỬI / NHẬN OBD-II
    # ──────────────────────────────────────────────

    def send_request(self, pid: int):
        """Bắn 1 lệnh truy vấn OBD-II Mode 01 cho PID chỉ định."""
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

    def decode_response(self, pid: int, data: bytes):
        """
        Giải mã dữ liệu thô OBD-II Mode 01.
        Trả về float hoặc None nếu không giải mã được.
        """
        if len(data) < 4:
            return None

        A = data[3]

        if pid == 0x0D:                           # Vehicle Speed
            return float(A)

        elif pid == 0x0C:                         # Engine RPM
            B = data[4] if len(data) > 4 else 0
            return ((A * 256) + B) / 4.0

        elif pid == 0x11:                         # Throttle Position
            return (A * 100.0) / 255.0

        elif pid == 0x05:                         # Coolant Temp
            return float(A - 40)

        elif pid == 0x0F:                         # Intake Air Temp
            return float(A - 40)

        elif pid == 0x10:                         # MAF Air Flow Rate
            B = data[4] if len(data) > 4 else 0
            return ((A * 256) + B) / 100.0

        elif pid == 0x07:                         # Fuel Trim Long (LTFT, 1 byte)
            return (A * 100.0) / 128.0 - 100.0

        return None

# ----------------------------------------------
    # TASK 2b: DOC MA LOI DTC (Mode 03)
    # ----------------------------------------------
    def _decode_dtc_bytes(self, raw):
        """Parse danh sach byte (da bo PCI) thanh list ma DTC dang 'P0171'.
        Moi DTC = 2 byte. byte[0] bits[7:6] = loai (00=P,01=C,10=B,11=U),
        bits[5:4] = chu so dau, phan con lai = 3 hex digit."""
        codes = []
        for i in range(0, len(raw) - 1, 2):
            b1, b2 = raw[i], raw[i + 1]
            if b1 == 0 and b2 == 0:
                continue  # 0x0000 = khong co loi / padding
            letter = "PCBU"[(b1 >> 6) & 0x03]
            d1 = (b1 >> 4) & 0x03
            d2 = b1 & 0x0F
            d3 = (b2 >> 4) & 0x0F
            d4 = b2 & 0x0F
            codes.append(f"{letter}{d1}{d2:X}{d3:X}{d4:X}")
        return codes

    def scan_dtc(self):
        """
        Quet ma loi chan doan (Mode 03). Tra ve list[dict]: {code, description}.
        - SIMULATION: tra ve 2-3 ma gia ngau nhien (de test UI/luong).
        - PRODUCTION: gui Mode 03, doc ISO-TP (single + multi-frame),
          gom nhieu ECU (0x7E8..0x7EF). Ghi dtc_logs + maintenance_logs + send_alert.

        *** LUU Y: phan multi-frame PRODUCTION chua kiem chung tren xe that
            (telemetry toàn single-frame nên đường này chưa chạy bao giờ).
            Cần test khi có xe bật đèn Check Engine với >=3 mã lỗi. ***
        """
        import time as _time

        # --- Nhanh SIMULATION: tra ma gia ---
        if str(CAN_BUS_TYPE).lower() == "virtual":
            import random
            sample = random.sample(list(DTC_DESCRIPTIONS.items()),
                                   k=min(3, len(DTC_DESCRIPTIONS)))
            found = [{"code": c, "description": d} for c, d in sample[:random.randint(2, 3)]]
            self._persist_dtc(found)
            return found

        # --- Nhanh PRODUCTION: doc that ---
        if not self.bus:
            return []

        # Gui yeu cau Mode 03
        req = can.Message(
            arbitration_id=OBD_REQUEST_ID,
            data=[0x01, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            is_extended_id=False,
        )
        try:
            self.bus.send(req)
        except Exception:
            return []

        # Gom frame tu cac ECU (0x7E8..0x7EF). Moi ECU co the multi-frame.
        ecu_buffers = {}     # arb_id -> bytearray payload da gom
        ecu_expected = {}    # arb_id -> tong so byte mong doi (multi-frame)
        deadline = _time.monotonic() + 2.0  # timeout 2s

        while _time.monotonic() < deadline:
            msg = self.bus.recv(timeout=0.05)
            if msg is None:
                continue
            arb = msg.arbitration_id
            if not (0x7E8 <= arb <= 0x7EF):
                continue
            data = bytes(msg.data)
            if len(data) < 1:
                continue
            pci_type = (data[0] >> 4) & 0x0F  # nibble dau = loai frame ISO-TP

            if pci_type == 0x0:
                # Single Frame: data[0] low nibble = do dai, payload tu data[1]
                length = data[0] & 0x0F
                payload = data[1:1 + length]
                # payload[0] phai la 0x43 (positive response Mode 03), payload[1]=so DTC
                if payload and payload[0] == 0x43:
                    ecu_buffers[arb] = bytearray(payload[2:])  # bo 0x43 + count
                    ecu_expected[arb] = len(payload[2:])

            elif pci_type == 0x1:
                # First Frame: 12 bit do dai = ((data[0]&0x0F)<<8)|data[1]
                total_len = ((data[0] & 0x0F) << 8) | data[1]
                # payload bat dau tu data[2]; data[2] nen la 0x43
                first_payload = data[2:]
                if first_payload and first_payload[0] == 0x43:
                    ecu_buffers[arb] = bytearray(first_payload[2:])
                    ecu_expected[arb] = total_len - 2  # tru 0x43 + count byte
                else:
                    ecu_buffers[arb] = bytearray(first_payload)
                    ecu_expected[arb] = total_len
                # Gui Flow Control: cho phep ECU gui tiep (0x30, BS=0, ST=0)
                fc = can.Message(
                    arbitration_id=0x7E0 + (arb - 0x7E8),  # request ID tuong ung ECU
                    data=[0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
                    is_extended_id=False,
                )
                try:
                    self.bus.send(fc)
                except Exception:
                    pass

            elif pci_type == 0x2:
                # Consecutive Frame: payload tu data[1]
                if arb in ecu_buffers:
                    ecu_buffers[arb].extend(data[1:])

            # Dung som neu tat ca ECU da du byte
            done = ecu_buffers and all(
                len(ecu_buffers[a]) >= ecu_expected.get(a, 0) for a in ecu_buffers
            )
            if done and ecu_expected:
                break

        # Parse tat ca buffer thanh ma DTC
        all_codes = []
        for arb, buf in ecu_buffers.items():
            # Fix#9: cat buffer ve dung so byte mong doi truoc khi parse (bo padding 0xAA -> tranh ma rac)
            _exp = ecu_expected.get(arb)
            if _exp and _exp > 0:
                buf = buf[:_exp]
            all_codes.extend(self._decode_dtc_bytes(bytes(buf)))

        found = []
        seen = set()
        for code in all_codes:
            if code in seen:
                continue
            seen.add(code)
            found.append({"code": code, "description": DTC_DESCRIPTIONS.get(code, "Mã lỗi không xác định")})

        self._persist_dtc(found)
        return found

    def _persist_dtc(self, found):
        """Ghi DTC vao dtc_logs + maintenance_logs (UI) + send_alert (Telegram)."""
        import time as _time
        import sqlite3 as _sqlite
        ts = _time.time()
        # Ghi dtc_logs (connection rieng - thread-safe)
        try:
            conn = _sqlite.connect(DATABASE_PATH, timeout=5)
            for d in found:
                conn.execute(
                    "INSERT INTO dtc_logs (timestamp_sec, dtc_code, description, is_cleared) VALUES (?, ?, ?, 0)",
                    (ts, d["code"], d["description"]),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[MÃ LỖI] Lỗi lưu mã chẩn đoán: {e}")
        # Log vao maintenance_logs de UI hien alert + Telegram
        for d in found:
            desc = f"Ma loi {d['code']}: {d['description']}"
            try:
                self.db_writer.log_maintenance_alert(ts, "DTC", desc)
            except Exception:
                pass
            try:
                from obd_module.rule_engine import send_alert
                send_alert("technical", d["code"], d["code"], desc)
            except Exception:
                pass

    def _query_single_pid(self, pid: int, cycle_ts: float = None):
        """MODULE A: ban request 1 PID -> phan loai ket cuc theo ISO 15765-4.
        - OK   : positive response (0x41), decode duoc -> ghi DB + snapshot.
        - BUSY : NRC 0x78 (Response Pending) -> ECU ban; KHONG tinh miss,
                 KHONG block doi P2* (yield ngay de bao ve nhip lay mau).
        - MISS : het timeout khong co phan hoi hop le (nghi loi lop vat ly).
        Tra (outcome, latency_ms). latency_ms chi co gia tri khi OK.
        cycle_ts: moc thoi gian chung cua ca chu ky (Module C) -> timestamp deu.
        Chi ghi DB khi OK -> mau thieu la 1 KHE TRONG that su, khong bfill so gia.
        """
        if cycle_ts is None:
            cycle_ts = time.time()
        t0 = time.monotonic()
        self.send_request(pid)

        deadline = t0 + PID_RESPONSE_TIMEOUT
        while time.monotonic() < deadline:
            try:
                msg = self.bus.recv(timeout=0.005)
            except (ValueError, IndexError):
                continue
            if msg is None:
                continue
            if msg.arbitration_id != OBD_RESPONSE_ID:
                continue
            d = msg.data
            if len(d) < 3:
                continue

            # --- NRC Response Pending (0x78): ECU dang ban, yield ngay ---
            # Layout NRC: [PCI, 0x7F, SIDRQ(=0x01 Mode01), NRC]. PID KHONG co mat.
            if d[1] == 0x7F and len(d) >= 4 and d[2] == 0x01 and d[3] == 0x78:
                # ECU da nhan request -> KHONG phai network miss. Bo qua chu ky nay.
                return ("BUSY", None)

            # --- Positive response dung PID vua hoi ---
            if d[1] == 0x41 and d[2] == pid:
                val = self.decode_response(pid, d)
                if val is None:
                    return ("MISS", None)        # nhan duoc nhung khong decode -> coi nhu thieu
                latency_ms = (time.monotonic() - t0) * 1000.0
                self.db_writer.enqueue(
                    cycle_ts,                    # MODULE C: dong dau bang moc chung cua chu ky
                    hex(pid),
                    TELEMETRY_SCHEMA[pid]['label'],
                    val,
                    TELEMETRY_SCHEMA[pid]['unit']
                )
                self.vehicle_snapshot[pid] = val
                return ("OK", latency_ms)

            # frame khac (vd phan hoi cua PID khac) -> bo qua, doc tiep
        return ("MISS", None)                    # het timeout, khong phan hoi hop le

    def _check_dtc_request(self):
        """Task2c: poll co 'dtc_scan_request' trong system_config.
        Neu =1 -> reader chinh (dang giu bus) quet DTC roi xoa co.
        Cach nay dam bao chi 1 bus duy nhat, khong tranh chap /dev/ttyACM0."""
        import sqlite3 as _sq
        try:
            conn = _sq.connect(DATABASE_PATH, timeout=2)
            row = conn.execute("SELECT value FROM system_config WHERE key='dtc_scan_request'").fetchone()
            if row and row[0] and float(row[0]) >= 1:
                conn.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('dtc_scan_request', 0)")
                conn.commit()
                conn.close()
                print("[OBD] Nhận yêu cầu quét DTC -> đang quét...")
                self.scan_dtc()
            else:
                conn.close()
        except Exception as e:
            print(f"[OBD] _check_dtc_request lỗi: {e}")

    def _update_and_persist_health(self, cycle_outcomes: dict):
        """MODULE B (phia can_app): cap nhat EWMA latency + miss-rate moi PID tu
        ket cuc 1 chu ky, dinh ky ghi xuong bang pid_health cho RuleEngine doc.
        QUY UOC: MISS=1, OK/BUSY=0 (BUSY la ECU ban, KHONG phai loi mang)."""
        for pid, (outcome, lat) in cycle_outcomes.items():
            # miss-rate EWMA
            miss_ind = 1.0 if outcome == "MISS" else 0.0
            prev_m = self._h_miss.get(pid, 0.0)
            self._h_miss[pid] = (1 - HEALTH_EWMA_ALPHA_MISS) * prev_m + HEALTH_EWMA_ALPHA_MISS * miss_ind
            # latency EWMA: chi cap nhat khi OK (moi co so do)
            if outcome == "OK" and lat is not None:
                prev_l = self._h_lat.get(pid)
                self._h_lat[pid] = lat if prev_l is None else \
                    (1 - HEALTH_EWMA_ALPHA_LAT) * prev_l + HEALTH_EWMA_ALPHA_LAT * lat

        # persist dinh ky (tranh ghi DB moi chu ky)
        now = time.time()
        if now - self._h_last_persist < HEALTH_PERSIST_SEC:
            return
        self._h_last_persist = now
        import sqlite3 as _sq
        try:
            conn = _sq.connect(DATABASE_PATH, timeout=2)
            conn.execute('''CREATE TABLE IF NOT EXISTS pid_health (
                pid INTEGER PRIMARY KEY, ewma_latency_ms REAL,
                ewma_miss_rate REAL, updated_at REAL)''')
            for pid in cycle_outcomes:
                conn.execute(
                    "INSERT OR REPLACE INTO pid_health (pid, ewma_latency_ms, ewma_miss_rate, updated_at) VALUES (?,?,?,?)",
                    (pid, self._h_lat.get(pid), self._h_miss.get(pid, 0.0), now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SỨC KHỎE] Lỗi lưu chỉ số cảm biến: {e}")

    def read_and_store(self):
        interval = 1.0 / SAMPLING_RATE_HZ
        print("🚗 Tiến trình đọc CAN Bus đã khởi động.")

        while self.running:
            try:
                # Tự kết nối lại nếu mất phần cứng
                if self.bus is None:
                    print("⏳ [OBD] Đang thử kết nối lại cổng phần cứng CAN...")
                    self.bus = self._init_can_bus()
                    if self.bus is None:
                        time.sleep(3)
                        continue
                    print("✅ [OBD] Đã kết nối lại CAN Bus thành công!")

                cycle_start = time.monotonic()
                cycle_ts = time.time()
                cycle_outcomes = {}

                # Quét tuần tự: bắn 1 PID → đợi phản hồi → bắn PID tiếp theo
                for pid in TELEMETRY_SCHEMA.keys():
                    if not self.running:
                        break
                    cycle_outcomes[pid] = self._query_single_pid(pid, cycle_ts)

                self._check_dtc_request()  # Task2c: quet DTC khi co co tu API/bot
                self._update_and_persist_health(cycle_outcomes)


                # Ngủ phần còn lại của interval (nếu quét xong sớm)
                wait = interval - (time.monotonic() - cycle_start)
                if wait > 0:
                    time.sleep(wait)
                else:
                    print(f'[OBD] Chu kỳ quét trễ {(-wait)*1000:.0f}ms')

            except (can.CanError, OSError) as e:
                if self.running:
                    print(f"⚠️  [OBD ĐỨT KẾT NỐI]: Mất tín hiệu vật lý với phần cứng ({e})")
                    print("⏳ [OBD] Vui lòng cắm lại cáp. Quét lại sau 3s...")
                    try:
                        self.bus.shutdown()
                    except Exception:
                        pass
                    self.bus = None
                    time.sleep(3)

            except Exception as e:
                if self.running:
                    import traceback
                    print(f"❌ [OBD LỖI HỆ THỐNG]: {type(e).__name__}: {e}")
                    print(traceback.format_exc())
                    time.sleep(3)