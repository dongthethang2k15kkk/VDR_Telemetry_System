import can
import time
import threading

CAN_INTERFACE = '/dev/ttyACM0'
CAN_BUS_TYPE = 'slcan'
CAN_BITRATE = 500000

PID_ENGINE_RPM = 0x0C
PID_VEHICLE_SPEED = 0x0D
PID_THROTTLE_POS = 0x11
PID_COOLANT_TEMP = 0x05

class FullCarScanner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self.bus = None

        try:
            self.bus = can.interface.Bus(channel=CAN_INTERFACE, bustype=CAN_BUS_TYPE, bitrate=CAN_BITRATE)
            print(f"✅ Đã kết nối cáp slcan qua cổng {CAN_INTERFACE}")
        except Exception as e:
            print(f"❌ Lỗi kết nối phần cứng: {e}")
            self.running = False

    def request_pid(self, pid):
        """Gửi yêu cầu dữ liệu lên mạng CAN"""
        request_data = [0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00]
        msg = can.Message(arbitration_id=0x7DF, data=request_data, is_extended_id=False)
        try:
            self.bus.send(msg)
        except can.CanError:
            pass

    def run(self):
        if not self.bus:
            return

        print("🚗 Bắt đầu đọc dữ liệu từ xe... (Nhấn Ctrl+C để dừng)\n")
        print("-" * 75)
        
        while self.running:
            # Tạo một dictionary để lưu dữ liệu tạm thời trong chu kỳ này
            car_data = {"rpm": "N/A", "speed": "N/A", "throttle": "N/A", "temp": "N/A"}

            # 1.RPM
            self.request_pid(PID_ENGINE_RPM)
            res = self.bus.recv(timeout=0.2)
            if res and 0x7E8 <= res.arbitration_id <= 0x7EF and len(res.data) >= 5 and res.data[2] == PID_ENGINE_RPM:
                rpm = ((res.data[3] * 256) + res.data[4]) / 4
                car_data["rpm"] = f"{rpm:.0f}"

            # 2.SPEED
            self.request_pid(PID_VEHICLE_SPEED)
            res = self.bus.recv(timeout=0.2)
            if res and 0x7E8 <= res.arbitration_id <= 0x7EF and len(res.data) >= 4 and res.data[2] == PID_VEHICLE_SPEED:
                speed = res.data[3]
                car_data["speed"] = f"{speed}"

            # 3.THROTTLE
            self.request_pid(PID_THROTTLE_POS)
            res = self.bus.recv(timeout=0.2)
            if res and 0x7E8 <= res.arbitration_id <= 0x7EF and len(res.data) >= 4 and res.data[2] == PID_THROTTLE_POS:
                throttle = (res.data[3] * 100) / 255
                car_data["throttle"] = f"{throttle:.1f}"

            # 4.COOLANT TEMP
            self.request_pid(PID_COOLANT_TEMP)
            res = self.bus.recv(timeout=0.2)
            if res and 0x7E8 <= res.arbitration_id <= 0x7EF and len(res.data) >= 4 and res.data[2] == PID_COOLANT_TEMP:
                temp = res.data[3] - 40
                car_data["temp"] = f"{temp}"

            print(f"\r🔄 RPM: {car_data['rpm']:>4} vòng/phút  |  🚀 Speed: {car_data['speed']:>3} km/h  |  🎛️ Throttle: {car_data['throttle']:>4} %  |  🌡️ Temp: {car_data['temp']:>3} °C", end="")

            time.sleep(0.1)

    def stop(self):
        self.running = False
        if self.bus:
            time.sleep(0.5)
            self.bus.shutdown()
            print("\n" + "-" * 75)
            print("🛑 Đã ngắt kết nối an toàn.")

if __name__ == "__main__":
    scanner = FullCarScanner()
    
    if scanner.running:
        scanner.start()
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            scanner.stop()