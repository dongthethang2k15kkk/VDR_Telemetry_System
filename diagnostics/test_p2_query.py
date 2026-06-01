# CHỨC NĂNG: Đo latency request→response thực tế của ECU, làm cơ sở adaptive timeout
# ==============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import can
from config import CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE, OBD_REQUEST_ID, OBD_RESPONSE_ID, SAMPLING_RATE_HZ, TELEMETRY_SCHEMA

PROBE_PID        = 0x0C   # RPM — PID ổn định nhất để đo
PROBE_ROUNDS     = 10     # Số lần bắn để lấy trung bình
PROBE_TIMEOUT    = 2.0    # Timeout tối đa mỗi lần (giây)
INTER_PROBE_WAIT = 0.05   # 50ms giữa các lần bắn

print("="*60)
print("=== PHASE 2: ĐO LATENCY ECU (OBD-II PROBE) ===")
print("="*60)

bus = None
try:
    bus = can.interface.Bus(channel=CAN_INTERFACE, interface=CAN_BUS_TYPE, bitrate=CAN_BITRATE)

    drain_deadline = time.monotonic() + 0.3
    while time.monotonic() < drain_deadline:
        if bus.recv(timeout=0) is None:
            break

    req_msg = can.Message(
        arbitration_id=OBD_REQUEST_ID,
        data=[0x02, 0x01, PROBE_PID, 0x00, 0x00, 0x00, 0x00, 0x00],
        is_extended_id=False
    )

    print(f"[+] Bắn {PROBE_ROUNDS} lần PID {hex(PROBE_PID)} để đo latency thực tế ECU...\n")

    latencies = []
    failed    = 0

    for i in range(PROBE_ROUNDS):
        t_send = time.monotonic()
        bus.send(req_msg)

        responded = False
        deadline  = time.monotonic() + PROBE_TIMEOUT
        while time.monotonic() < deadline:
            msg = bus.recv(timeout=deadline - time.monotonic())
            if msg is None:
                break
            if not (OBD_RESPONSE_ID <= msg.arbitration_id <= OBD_RESPONSE_ID + 7):
                continue
            if len(msg.data) < 5 or msg.data[2] != PROBE_PID:
                continue

            latency_ms = (time.monotonic() - t_send) * 1000
            A, B = msg.data[3], msg.data[4]
            rpm  = ((A * 256) + B) / 4.0

            latencies.append(latency_ms)
            print(f"    [{i+1:02d}/{PROBE_ROUNDS}] RPM={rpm:7.1f} | Latency={latency_ms:6.1f}ms")
            responded = True
            break

        if not responded:
            failed += 1
            print(f"    [{i+1:02d}/{PROBE_ROUNDS}] ❌ TIMEOUT — ECU không phản hồi trong {PROBE_TIMEOUT*1000:.0f}ms")

        time.sleep(INTER_PROBE_WAIT)

    print()
    if not latencies:
        print("[-] LỖI [OBD_TIMEOUT]: Không nhận được bất kỳ phản hồi nào từ ECU.")
        sys.exit(1)

    lat_min = min(latencies)
    lat_max = max(latencies)
    lat_avg = sum(latencies) / len(latencies)
    lat_p95 = sorted(latencies)[int(len(latencies) * 0.95)]

    num_pids = len(TELEMETRY_SCHEMA)
    interval_ms = 1000 / SAMPLING_RATE_HZ                  # FIX: lấy từ config, không hardcode
    budget_per_pid = interval_ms / num_pids                 # FIX: 200ms / 4 PIDs = 50ms (đúng với 5Hz sequential)
    recommended_timeout = lat_p95 * 1.5

    print(f"[+] Kết quả đo latency ECU ({len(latencies)}/{PROBE_ROUNDS} thành công):")
    print(f"    Min    : {lat_min:.1f}ms")
    print(f"    Max    : {lat_max:.1f}ms")
    print(f"    Avg    : {lat_avg:.1f}ms")
    print(f"    P95    : {lat_p95:.1f}ms")
    print(f"    Failed : {failed}/{PROBE_ROUNDS}")
    print(f"\n[=] KHUYẾN NGHỊ: PID_TIMEOUT_SEC = {recommended_timeout:.0f}ms cho xe này")
    print(f"[=] Budget mỗi PID @ {SAMPLING_RATE_HZ}Hz / {num_pids} PIDs = {budget_per_pid:.0f}ms")

    # So sánh P95 với PID_TIMEOUT_SEC thực tế trong canapp.py (40ms)
    CANAPP_TIMEOUT_MS = 40
    if lat_p95 < CANAPP_TIMEOUT_MS * 0.8:
        print(f"✅ ECU đủ nhanh → Sequential @ {SAMPLING_RATE_HZ}Hz an toàn (P95 << {CANAPP_TIMEOUT_MS}ms timeout)")
    elif lat_p95 < CANAPP_TIMEOUT_MS:
        print(f"⚠️  ECU sát giới hạn → Cân nhắc tăng PID_TIMEOUT_SEC trong canapp.py lên {recommended_timeout:.0f}ms")
    else:
        print(f"❌ ECU quá chậm (P95={lat_p95:.0f}ms > {CANAPP_TIMEOUT_MS}ms) → Bắt buộc tăng PID_TIMEOUT_SEC hoặc giảm SAMPLING_RATE_HZ")

    print("\n[+] PHASE 2 PASS.")

except can.CanOperationError as e:
    print(f"[-] LỖI [CAN_BUS_OFF]: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[-] LỖI HỆ THỐNG: {e}")
    sys.exit(1)
finally:
    if bus:
        bus.shutdown()
    print("[*] Đã ngắt kết nối Bus an toàn.")