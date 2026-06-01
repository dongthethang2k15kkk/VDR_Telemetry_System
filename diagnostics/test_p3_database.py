# CHỨC NĂNG: Stress test DB + CAN Bus — mirror đúng pattern canapp.py (Sequential)
# Mục tiêu: đảm bảo pipeline ghi dữ liệu hoạt động chuẩn trước khi chạy main.py
# ==============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import can
from config import (
    CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE,
    OBD_REQUEST_ID, OBD_RESPONSE_ID,
    TELEMETRY_SCHEMA, SAMPLING_RATE_HZ
)
from obd_module.db_setup import init_db, TelemetryDBWriter

# FIX: Mirror đúng hằng số trong canapp.py — phải đồng bộ thủ công nếu canapp.py thay đổi
PID_TIMEOUT_SEC   = 0.04   # 40ms/PID — giống hệt canapp.py
INTERVAL          = 1.0 / SAMPLING_RATE_HZ
TEST_DURATION_SEC = 10

print("=" * 60)
print(f"=== PHASE 3: STRESS TEST DATABASE @ {SAMPLING_RATE_HZ}Hz (SEQUENTIAL) ===")
print(f"    {len(TELEMETRY_SCHEMA)} PIDs/cycle | Interval={INTERVAL*1000:.0f}ms | Timeout/PID={PID_TIMEOUT_SEC*1000:.0f}ms")
print("=" * 60)

def decode_response(pid: int, data: bytes):
    if len(data) < 4:
        return None
    A = data[3]
    if pid == 0x0D: return float(A)
    elif pid == 0x0C:
        B = data[4] if len(data) > 4 else 0
        return ((A * 256) + B) / 4.0
    elif pid == 0x11: return (A * 100.0) / 255.0
    elif pid == 0x05: return float(A - 40)
    return None

def read_pid_sequential(bus, pid: int) -> float | None:
    """
    Mirror chính xác hàm read_pid_sequential() trong canapp.py.
    Bắn 1 PID → lọc đúng response → timeout 40ms → trả None nếu miss.
    """
    msg = can.Message(
        arbitration_id=OBD_REQUEST_ID,
        data=[0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00],
        is_extended_id=False
    )
    try:
        bus.send(msg)
    except Exception:
        return None

    deadline = time.monotonic() + PID_TIMEOUT_SEC
    while time.monotonic() < deadline:
        rx = bus.recv(timeout=0.005)
        if rx is None:
            continue
        if rx.arbitration_id != OBD_RESPONSE_ID:
            continue
        if len(rx.data) < 3:
            continue
        if rx.data[1] != 0x41:
            continue
        if rx.data[2] != pid:
            continue
        return decode_response(pid, rx.data)

    return None   # timeout — miss PID này cycle này

init_db()
db_writer = TelemetryDBWriter()
bus       = None
pids      = list(TELEMETRY_SCHEMA.keys())
pid_hit_count = {pid: 0 for pid in pids}

try:
    bus = can.interface.Bus(channel=CAN_INTERFACE, interface=CAN_BUS_TYPE, bitrate=CAN_BITRATE)

    drain_deadline = time.monotonic() + 0.3
    while time.monotonic() < drain_deadline:
        if bus.recv(timeout=0) is None:
            break

    print(f"[+] Bắt đầu stress test {TEST_DURATION_SEC}s...\n")

    start_time     = time.monotonic()
    total_cycles   = 0
    success_cycles = 0
    total_values   = 0

    while time.monotonic() - start_time < TEST_DURATION_SEC:
        cycle_start  = time.monotonic()
        total_cycles += 1
        cycle_hits   = 0

        # Sequential: bắn từng PID, chờ nhận xong rồi mới bắn tiếp
        for pid in pids:
            val = read_pid_sequential(bus, pid)
            if val is not None:
                db_writer.enqueue(
                    time.time(),
                    hex(pid),
                    TELEMETRY_SCHEMA[pid]['label'],
                    val,
                    TELEMETRY_SCHEMA[pid]['unit']
                )
                pid_hit_count[pid] += 1
                cycle_hits += 1

        if cycle_hits > 0:
            success_cycles += 1
            total_values   += cycle_hits

        # Điều tiết tần suất — anchor từ cycle_start tránh drift
        elapsed = time.monotonic() - cycle_start
        wait    = INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        elif wait < -0.010:
            print(f"    [!] CẢNH BÁO [CYCLE_OVERRUN]: Trễ {abs(wait)*1000:.1f}ms — cân nhắc giảm SAMPLING_RATE_HZ hoặc tăng PID_TIMEOUT_SEC")

    db_writer.flush()

    success_rate  = (success_cycles / total_cycles * 100) if total_cycles > 0 else 0
    avg_per_cycle = (total_values / success_cycles) if success_cycles > 0 else 0

    print(f"[+] Kết quả stress test:")
    print(f"    Tổng chu kỳ        : {total_cycles}")
    print(f"    Chu kỳ có dữ liệu  : {success_cycles} ({success_rate:.1f}%)")
    print(f"    Tổng giá trị ghi   : {total_values}")
    print(f"    TB giá trị/chu kỳ  : {avg_per_cycle:.1f} / {len(pids)} PIDs")
    print(f"\n    Hit rate từng PID:")
    for pid, count in pid_hit_count.items():
        rate  = count / total_cycles * 100
        label = TELEMETRY_SCHEMA[pid]['label']
        flag  = "✅" if rate >= 70 else "❌"
        print(f"    {flag} {label:<20} : {count}/{total_cycles} ({rate:.1f}%)")

    if success_rate >= 70.0:
        print("\n[==> MILESTONE ĐẠT: STRESS TEST DB CHUẨN MAIN.PY! <==]")
    else:
        print("\n[-] LỖI HIỆU NĂNG [PERF_LOW_ERR]: Tỉ lệ < 70% — kiểm tra lại ECU hoặc tăng PID_TIMEOUT_SEC.")

except can.CanOperationError as e:
    print(f"[-] LỖI [CAN_BUS_OFF]: {e}")
except Exception as e:
    print(f"[-] LỖI HỆ THỐNG: {e}")
finally:
    db_writer.flush()
    if bus:
        bus.shutdown()
    print("[*] Đóng kết nối CAN Bus và DB an toàn.")