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

    def run(self):
        while self.running:
            msg = self.bus.recv(timeout=1)
            if msg and msg.arbitration_id == OBD_REQUEST_ID:
                pid = msg.data[2]
                response_data = [0x03, 0x41, pid, 0x00, 0x00, 0x00, 0x00, 0x00]
                
                if pid == 0x0D: # Speed
                    response_data[3] = random.randint(40, 120)
                elif pid == 0x0C: # RPM
                    rpm = random.randint(1500, 5000)
                    val = int(rpm * 4)
                    response_data[3] = (val >> 8) & 0xFF
                    response_data[4] = val & 0xFF
                elif pid == 0x11: # Throttle
                    response_data[3] = random.randint(10, 90)
                elif pid == 0x05: # Coolant Temp
                    # Temp = A - 40 => A = Temp + 40
                    response_data[3] = random.randint(85, 105) + 40
                
                res_msg = can.Message(arbitration_id=OBD_RESPONSE_ID, data=response_data, is_extended_id=False)
                self.bus.send(res_msg)

    def stop(self):
        self.running = False
        self.bus.shutdown()