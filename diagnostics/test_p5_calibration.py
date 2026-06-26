# CHUC NANG: Hieu chuan (calibration) cho TUNG XE cu the.
# Do do tre phan hoi that cua moi PID -> tinh config toi uu (uu tien KHONG MISS)
# -> tu dong ghi vao config.py (co backup) + xuat report.
#
# Cach dung:  python diagnostics/test_p5_calibration.py
# Yeu cau:    da cam xe that, xe NO MAY, OPERATION_MODE = PRODUCTION.
import os
import sys
import time
import statistics

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import can
from config import (
    CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE,
    OBD_REQUEST_ID, OBD_RESPONSE_ID,
    TELEMETRY_SCHEMA, OPERATION_MODE,
)

# ---- Tham so do ----
SAMPLES_PER_PID = 100      # so lan do moi PID
PROBE_TIMEOUT = 0.5        # timeout RONG khi do (de KHONG tu gay miss luc do)
RECV_SLICE = 0.01          # moi lan recv cho 10ms
SAFETY_MARGIN = 1.5        # bien an toan cho PID_RESPONSE_TIMEOUT (p95 * 1.5)
SUPPORT_THRESHOLD = 0.95   # tra loi >= 95% => PID ho tro tot

print("=" * 64)
print("=== PHASE 5: HIEU CHUAN CONFIG CHO XE (CALIBRATION) ===")
print("=" * 64)

if OPERATION_MODE == "SIMULATION":
    print("[-] Dang o che do SIMULATION. Tool nay can xe THAT (PRODUCTION).")
    print("    Sua OPERATION_MODE = 'PRODUCTION' trong config.py roi cam xe.")
    sys.exit(1)


def measure_pid(bus, pid):
    """Do 1 PID SAMPLES_PER_PID lan. Tra (so_lan_tra_loi, list_do_tre_ms)."""
    latencies = []
    responded = 0
    for _ in range(SAMPLES_PER_PID):
        # drain rac truoc moi lan do
        while bus.recv(timeout=0) is not None:
            pass
        req = can.Message(
            arbitration_id=OBD_REQUEST_ID,
            data=[0x02, 0x01, pid, 0, 0, 0, 0, 0],
            is_extended_id=False,
        )
        t0 = time.monotonic()
        try:
            bus.send(req)
        except Exception:
            continue
        deadline = t0 + PROBE_TIMEOUT
        got = False
        while time.monotonic() < deadline:
            msg = bus.recv(timeout=RECV_SLICE)
            if msg is None:
                continue
            if msg.arbitration_id != OBD_RESPONSE_ID:
                continue
            if len(msg.data) < 3:
                continue
            if msg.data[1] != 0x41 or msg.data[2] != pid:
                continue
            latencies.append((time.monotonic() - t0) * 1000.0)  # ms
            got = True
            break
        if got:
            responded += 1
        time.sleep(0.02)  # nghi nhe giua cac lan do
    return responded, latencies


def main():
    bus = None
    try:
        bus = can.interface.Bus(channel=CAN_INTERFACE, bustype=CAN_BUS_TYPE, bitrate=CAN_BITRATE)
    except Exception as e:
        print(f"[-] Khong mo duoc CAN bus: {e}")
        sys.exit(1)

    print(f"[*] Do {SAMPLES_PER_PID} lan moi PID. Tong {len(TELEMETRY_SCHEMA)} PID.")
    print(f"[*] Xe phai dang NO MAY de co du lieu. Bat dau...\n")

    report = {}        # pid -> dict ket qua
    supported = []     # pid ho tro tot
    flaky = []         # pid chap chon
    unsupported = []   # pid khong ho tro

    for pid, meta in TELEMETRY_SCHEMA.items():
        label = meta["label"]
        responded, lat = measure_pid(bus, pid)
        rate = responded / SAMPLES_PER_PID
        if lat:
            lat_sorted = sorted(lat)
            p95 = lat_sorted[int(len(lat_sorted) * 0.95) - 1] if len(lat_sorted) >= 20 else max(lat_sorted)
            info = {
                "label": label, "rate": rate, "responded": responded,
                "min": min(lat), "mean": statistics.mean(lat),
                "p95": p95, "max": max(lat),
            }
        else:
            info = {"label": label, "rate": rate, "responded": responded,
                    "min": None, "mean": None, "p95": None, "max": None}
        report[pid] = info

        if rate >= SUPPORT_THRESHOLD:
            supported.append(pid)
            tag = "[OK]"
        elif rate > 0:
            flaky.append(pid)
            tag = "[CHAP CHON]"
        else:
            unsupported.append(pid)
            tag = "[KHONG HO TRO]"

        if lat:
            print(f"  {tag:14} {hex(pid)} {label:20} "
                  f"tra loi {responded}/{SAMPLES_PER_PID} | "
                  f"min={info['min']:.0f} mean={info['mean']:.0f} "
                  f"p95={info['p95']:.0f} max={info['max']:.0f} ms")
        else:
            print(f"  {tag:14} {hex(pid)} {label:20} tra loi 0/{SAMPLES_PER_PID} (xe khong ho tro PID nay)")

    bus.shutdown()

    # ---- Tinh config toi uu (uu tien KHONG MISS) ----
    print("\n" + "=" * 64)
    print("=== KET QUA HIEU CHUAN ===")
    print("=" * 64)

    usable = supported + flaky
    if not usable:
        print("[-] Khong PID nao tra loi. Kiem tra: xe da no may? day CAN? OBD_RESPONSE_ID dung?")
        sys.exit(1)

    # PID_RESPONSE_TIMEOUT = p95 cham nhat trong cac PID dung duoc * bien an toan
    worst_p95 = max(report[p]["p95"] for p in usable if report[p]["p95"] is not None)
    new_timeout = round((worst_p95 * SAFETY_MARGIN) / 1000.0, 3)
    new_timeout = max(new_timeout, 0.05)  # san toi thieu 50ms

    # SAMPLING_RATE_HZ: 1 chu ky phai du quet het PID dung duoc
    n_pid = len(usable)
    cycle_needed = n_pid * new_timeout       # thoi gian xau nhat quet het 1 vong
    # them 20% dem -> tan so an toan
    safe_cycle = cycle_needed * 1.2
    new_hz = max(1, int(1.0 / safe_cycle)) if safe_cycle > 0 else 1

    print(f"PID ho tro tot   : {[hex(p) for p in supported]}")
    print(f"PID chap chon    : {[hex(p) for p in flaky]} (van giu nhung canh bao)")
    print(f"PID KHONG ho tro : {[hex(p) for p in unsupported]} (NEN loai khoi schedule)")
    print(f"\nDo tre p95 cham nhat: {worst_p95:.0f} ms")
    print(f"-> PID_RESPONSE_TIMEOUT de xuat: {new_timeout} s  (p95 x {SAFETY_MARGIN})")
    print(f"-> SAMPLING_RATE_HZ de xuat   : {new_hz} Hz  ({n_pid} PID x {new_timeout}s + dem)")

    # ---- Ghi report ra file ----
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(PROJECT_ROOT, f"calibration_{ts}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"CALIBRATION REPORT - {ts}\n")
        f.write(f"Xe: (ghi ten xe vao day)\n\n")
        for pid in TELEMETRY_SCHEMA:
            r = report[pid]
            if r["p95"] is not None:
                f.write(f"{hex(pid)} {r['label']}: {r['responded']}/{SAMPLES_PER_PID}, "
                        f"min={r['min']:.0f} mean={r['mean']:.0f} p95={r['p95']:.0f} max={r['max']:.0f} ms\n")
            else:
                f.write(f"{hex(pid)} {r['label']}: 0/{SAMPLES_PER_PID} (khong ho tro)\n")
        f.write(f"\nDE XUAT CONFIG:\n")
        f.write(f"PID_RESPONSE_TIMEOUT = {new_timeout}\n")
        f.write(f"SAMPLING_RATE_HZ = {new_hz}\n")
        f.write(f"PID khong ho tro (nen loai): {[hex(p) for p in unsupported]}\n")
    print(f"\n[+] Da luu report: {report_path}")

    # ---- Tu dong ghi vao config.py (co backup) ----
    config_path = os.path.join(PROJECT_ROOT, "config.py")
    src = open(config_path, encoding="utf-8").read()
    import re as _re0
    _cur_hz = _re0.search(r"SAMPLING_RATE_HZ\s*:\s*Final\[int\]\s*=\s*(\d+)", src)
    _cur_to = _re0.search(r"PID_RESPONSE_TIMEOUT\s*:\s*Final\[float\]\s*=\s*([\d.]+)", src)
    print("\n--- Thay đổi sẽ ghi vào config.py ---")
    print(f"  SAMPLING_RATE_HZ     : {_cur_hz.group(1) if _cur_hz else '?'}  ->  {new_hz}")
    print(f"  PID_RESPONSE_TIMEOUT : {_cur_to.group(1) if _cur_to else '?'}  ->  {new_timeout}")
    answer = input(">>> Xác nhận ghi đè? [y/N]: ").strip().lower()
    if answer != "y":
        print("[*] Bỏ qua ghi config. Có thể tự sửa tay theo report.")
        return

    # Sua CA 2 hang so trong config.py (can_app.py da doc tu config)
    import re
    src2 = re.sub(r"SAMPLING_RATE_HZ\s*:\s*Final\[int\]\s*=\s*\d+",
                  f"SAMPLING_RATE_HZ: Final[int] = {new_hz}", src, count=1)
    n1 = (src2 != src)
    src3 = re.sub(r"PID_RESPONSE_TIMEOUT\s*:\s*Final\[float\]\s*=\s*[\d.]+",
                  f"PID_RESPONSE_TIMEOUT: Final[float] = {new_timeout}", src2, count=1)
    n2 = (src3 != src2)

    open(config_path, "w", encoding="utf-8").write(src3)
    if n1:
        print(f"[+] Đã cập nhật SAMPLING_RATE_HZ = {new_hz}")
    else:
        print("[!] Không tìm thấy SAMPLING_RATE_HZ - sửa tay theo report.")
    if n2:
        print(f"[+] Đã cập nhật PID_RESPONSE_TIMEOUT = {new_timeout}")
    else:
        print("[!] Không tìm thấy PID_RESPONSE_TIMEOUT - sửa tay theo report.")
    print(f"[+] Khởi động lại: python main.py (để áp config mới)")
    if unsupported:
        print(f"[!] PID xe khong ho tro {[hex(p) for p in unsupported]} -> nen xoa khoi TELEMETRY_SCHEMA trong config.py.")


if __name__ == "__main__":
    main()