import can
import time
import random
import threading
from config import CAN_INTERFACE, CAN_BUS_TYPE, OBD_REQUEST_ID, OBD_RESPONSE_ID

class ECUSimulator(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.bus = can.interface.Bus(channel=CAN_INTERFACE, bustype=CAN_BUS_TYPE)
        self.running = True
        
        # 1. Biến trạng thái vật lý của xe
        self.state = 'IDLE'       # Trạng thái lái: IDLE, ACCEL, CRUISE, BRAKE
        self.speed = 0.0          # km/h
        self.rpm = 800.0          # Vòng/phút (Galanti)
        self.throttle = 0.0       # %
        self.coolant_temp = 35.0  # Bắt đầu ở nhiệt độ môi trường
        self.maf = 3.0            # g/s
        self.iat = 40.0           # Nhiệt độ khí nạp (°C)
        self.ltft = 0.0           # Cân bằng nhiên liệu (%)
        
        self.gear_ratio = 30.0    # Tỉ số truyền ảo (RPM = speed * gear_ratio)
        self.state_timer = time.time()
        self._demo_t0 = time.time()  # mốc chu kỳ demo lái ẩu
        
        # 2. Khởi chạy luồng tính toán vật lý (Cập nhật 10 lần/giây)
        self.physics_thread = threading.Thread(target=self._update_physics, daemon=True)
        self.physics_thread.start()

    def _update_physics(self):
        """Mô phỏng động học xe và các quy luật vật lý tuyến tính."""
        while self.running:
            now = time.time()
            dt = 0.1 # Chu kỳ 10Hz
            
            # --- CHUYỂN TRẠNG THÁI LÁI ---
            if self.state == 'IDLE':
                self.throttle = 0.0
                self.speed = 0.0
                self.rpm = 800.0 + random.uniform(-15, 15)
                if now - self.state_timer > random.uniform(3, 8):
                    self.state = 'ACCEL'
                    self.state_timer = now
            
            elif self.state == 'ACCEL':
                self.throttle = random.uniform(40.0, 75.0)
                self.speed += (self.throttle * 0.06) * dt
                self.rpm = 1000 + (self.speed * self.gear_ratio)
                
                # Sang số ảo (Giảm vòng tua khi xe chạy nhanh)
                if self.rpm > 3500 and self.gear_ratio > 10:
                    self.gear_ratio -= 4  
                    self.rpm = 1000 + (self.speed * self.gear_ratio)

                if self.speed > random.uniform(50, 80):
                    self.state = 'CRUISE'
                    self.state_timer = now
                    
            elif self.state == 'CRUISE':
                self.throttle = random.uniform(15.0, 25.0) # Giữ nhẹ ga
                self.speed += random.uniform(-1, 1) * dt
                self.rpm = 1000 + (self.speed * self.gear_ratio) + random.uniform(-30, 30)
                
                if now - self.state_timer > random.uniform(10, 20):
                    self.state = random.choice(['ACCEL', 'BRAKE'])
                    self.state_timer = now
                    
            elif self.state == 'BRAKE':
                self.throttle = 0.0
                self.speed -= random.uniform(15.0, 25.0) * dt # Phanh gấp/nhẹ
                if self.speed <= 0:
                    self.speed = 0.0
                    self.gear_ratio = 30.0
                    self.state = 'IDLE'
                    self.state_timer = now
                else:
                    self.rpm = 1000 + (self.speed * self.gear_ratio)

            # --- GIỚI HẠN VÀ TƯƠNG QUAN VẬT LÝ ---
            self.rpm = max(800.0, min(6500.0, self.rpm))
            
            # Nước làm mát nóng dần lên mức nhiệt độ hoạt động (~90°C)
            if self.coolant_temp < 90.0:
                self.coolant_temp += 0.2 * dt
            else:
                self.coolant_temp = 90.0 + random.uniform(-1, 1)
            
            # Cảm biến lượng khí nạp (MAF) tỉ lệ thuận với vòng tua và độ mở bướm ga
            self.maf = (self.rpm * max(5.0, self.throttle) * 0.001) / 8.0
            
            # Cân bằng nhiên liệu dài hạn (thường xoay quanh 0%)
            self.ltft = random.uniform(-1.5, 1.5)

            # ── DEMO: ép lái ẩu theo chu kỳ 40s để cảnh báo ATGT bắn ra ──
            cyc = (now - self._demo_t0) % 40.0
            if 8.0 <= cyc < 15.0:        # 7s: vượt tốc 130 + thốc ga 95%
                self.speed = 130.0
                self.throttle = 95.0
                self.rpm = min(6500.0, 1000 + self.speed * self.gear_ratio)
            elif 18.0 <= cyc < 23.0:     # 5s: quá nhiệt 110C
                self.coolant_temp = 110.0
            elif 26.0 <= cyc < 31.0:     # 5s: LTFT bất thường 30%
                self.ltft = 30.0

            time.sleep(dt)

    def run(self):
        """Luồng chính: Lắng nghe request và encode chuẩn OBD-II."""
        print("🖥️ [SIMULATOR] Physics Engine Active. Chờ OBD Scanner...")
        while self.running:
            try:
                msg = self.bus.recv(timeout=0.1)
                # Lọc đúng yêu cầu OBD-II chuẩn (0x7DF)
                if msg and msg.arbitration_id == OBD_REQUEST_ID:
                    if len(msg.data) >= 3 and msg.data[1] == 0x01:
                        self._handle_request(msg.data[2])
            except Exception:
                pass

    def _handle_request(self, pid):
        # Header chuẩn OBD Response: [Độ dài data, 0x41, PID, A, B, C, D, E]
        res = [0x00, 0x41, pid, 0x00, 0x00, 0x00, 0x00, 0x00]
        
        if pid == 0x0D:   # Speed (A km/h)
            res[0], res[3] = 3, int(self.speed) & 0xFF
            
        elif pid == 0x0C: # RPM ((A*256 + B)/4) -> A*256+B = RPM*4
            val = int(self.rpm * 4)
            res[0], res[3], res[4] = 4, (val >> 8) & 0xFF, val & 0xFF
            
        elif pid == 0x11: # Throttle (A*100/255) -> A = Throttle*255/100
            res[0], res[3] = 3, int(self.throttle * 255.0 / 100.0) & 0xFF
            
        elif pid == 0x05: # Coolant Temp (A - 40) -> A = Temp + 40
            res[0], res[3] = 3, int(self.coolant_temp + 40) & 0xFF
            
        elif pid == 0x10: # MAF ((A*256 + B)/100) -> A*256+B = MAF*100
            val = int(self.maf * 100)
            res[0], res[3], res[4] = 4, (val >> 8) & 0xFF, val & 0xFF
            
        elif pid == 0x0F: # IAT (A - 40)
            res[0], res[3] = 3, int(self.iat + 40) & 0xFF
            
        elif pid == 0x07: # LTFT ((A*100/128) - 100)
            res[0], res[3] = 3, int((self.ltft + 100.0) * 128.0 / 100.0) & 0xFF
            
        else:
            return # Bỏ qua các PID không khai báo

        res_msg = can.Message(arbitration_id=OBD_RESPONSE_ID, data=res, is_extended_id=False)
        self.bus.send(res_msg)

    def stop(self):
        self.running = False
        if self.bus:
            self.bus.shutdown()