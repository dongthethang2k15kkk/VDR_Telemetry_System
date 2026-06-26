# CHUC NANG: Test TONG HOP - gop 2 phan:
#   PHAN A (tu dong, DB tam, KHONG can xe): bao duong, cooldown, danh dau xong,
#           predictive Mann-Kendall, DTC sim.
#   PHAN B (realtime, CAN xe that): doc song de xac nhan so THAT, khong fake.
#
# Cach dung:  python diagnostics/test_p6_features.py
# Phan A chay truoc, tu dong. Xong hoi co chay phan B (can xe) khong.
import os
import sys
import time
import sqlite3
import tempfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# --- DB tam cho phan A (khong dung DB that) ---
_tmpdir = tempfile.mkdtemp(prefix="vdr_test_")
_TEST_DB = os.path.join(_tmpdir, "test.db")

import config
_REAL_DB = config.DATABASE_PATH      # nho lai DB that cho phan B
config.DATABASE_PATH = _TEST_DB
import obd_module.db_setup as db_setup
db_setup.DATABASE_PATH = _TEST_DB

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [*** FAIL ***] {name}  {detail}")


def fresh_db():
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    db_setup.init_db()
    return sqlite3.connect(_TEST_DB, timeout=5)


# ============================================================
# PHAN A: TEST LOGIC (tu dong, DB tam)
# ============================================================
print("=" * 64)
print("=== PHASE 6A: TEST LOGIC TINH NANG (DB tam, khong can xe) ===")
print("=" * 64)

# ---- 1. BAO DUONG: ODO tang -> % cap nhat -> canh bao ----
print("\n[1] BAO DUONG: ODO tang -> % cap nhat -> qua han thi canh bao")
re_inst = None
try:
    from obd_module.rule_engine import RuleEngine
    fresh_db()
    # Tao RuleEngine TRUOC -> no chay migration them cot last_alert_at/engine_hours
    re_inst = RuleEngine()
    re_inst.last_maint_check = 0
    re_inst.trip_km = 0
    re_inst.trip_engine_hours = 0
    # Gio schema day du -> bom du lieu qua cursor cua RuleEngine
    re_inst.cursor.execute("INSERT OR REPLACE INTO maintenance_schedule "
                "(item, interval_km, interval_days, last_km, last_date, last_engine_hours, interval_engine_hours, last_alert_at) "
                "VALUES ('oil', 5000, 365, 0, ?, 0, NULL, 0)", (time.time(),))
    re_inst.cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('base_odo', 0)")
    re_inst.cursor.execute("INSERT INTO trip_logs (start_time, end_time, total_km, engine_hours) VALUES (?,?,4800,80)",
                (time.time()-3600, time.time()))
    re_inst.conn.commit()
    re_inst._check_maintenance_schedule()
    row = re_inst.cursor.execute(
        "SELECT severity FROM alert_logs WHERE category='maintenance' AND item='oil' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    check("Bao duong 96% -> sinh canh bao", row is not None, f"row={row}")
    if row:
        check("Severity = warning (90-100%)", row[0] == "warning", f"got {row[0]}")

    re_inst.cursor.execute("INSERT INTO trip_logs (start_time, end_time, total_km, engine_hours) VALUES (?,?,300,5)",
                           (time.time()-100, time.time()))
    re_inst.cursor.execute("UPDATE maintenance_schedule SET last_alert_at=0 WHERE item='oil'")
    re_inst.conn.commit()
    re_inst.last_maint_check = 0
    re_inst._check_maintenance_schedule()
    row2 = re_inst.cursor.execute(
        "SELECT severity FROM alert_logs WHERE category='maintenance' AND item='oil' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    check("Bao duong 102% -> critical", row2 is not None and row2[0] == "critical", f"row2={row2}")
except Exception as e:
    import traceback
    check("Test bao duong chay duoc", False, f"EXC: {e}")
    traceback.print_exc()

# ---- 2. COOLDOWN ----
print("\n[2] ALERT COOLDOWN: da bao thi khong bao lai trong 24h")
try:
    n_before = re_inst.cursor.execute(
        "SELECT COUNT(*) FROM alert_logs WHERE category='maintenance' AND item='oil'").fetchone()[0]
    re_inst.last_maint_check = 0
    re_inst._check_maintenance_schedule()
    n_after = re_inst.cursor.execute(
        "SELECT COUNT(*) FROM alert_logs WHERE category='maintenance' AND item='oil'").fetchone()[0]
    check("Goi lai ngay -> KHONG canh bao trung", n_after == n_before, f"truoc={n_before} sau={n_after}")
except Exception as e:
    check("Test cooldown chay duoc", False, f"EXC: {e}")

# ---- 3. DANH DAU XONG ----
print("\n[3] DANH DAU XONG (mark_maintained): reset last_km ve ODO hien tai")
try:
    re_inst.mark_maintained("oil")
    last_km = re_inst.cursor.execute(
        "SELECT last_km FROM maintenance_schedule WHERE item='oil'").fetchone()[0]
    global_km = re_inst._get_global_km()
    check("Sau danh dau: last_km ~ ODO hien tai", abs(last_km - global_km) < 1.0,
          f"last_km={last_km} odo={global_km}")
except Exception as e:
    check("Test danh dau xong chay duoc", False, f"EXC: {e}")

# ---- 4. PREDICTIVE Mann-Kendall ----
print("\n[4] PREDICTIVE: LTFT tang -> bao | LTFT phang -> khong bao")
try:
    from obd_module.rule_engine import TrendAnalyzer
    conn2 = fresh_db()
    cur2 = conn2.cursor()
    for v in [12.0, 13.4, 14.1, 15.6, 16.4, 18.1, 19.0, 20.4]:
        cur2.execute("INSERT INTO trip_averages (ltft_avg) VALUES (?)", (v,))
    conn2.commit()
    res_rise = TrendAnalyzer().analyze(cur2)
    check("LTFT tang co y nghia -> predictive bao",
          any(r["pid_name"] == "ltft_avg" for r in res_rise), f"res={res_rise}")

    conn3 = fresh_db()
    cur3 = conn3.cursor()
    for v in [16, 15.8, 16.2, 15.9, 16.1, 16.0, 15.7, 16.3]:
        cur3.execute("INSERT INTO trip_averages (ltft_avg) VALUES (?)", (v,))
    conn3.commit()
    res_flat = TrendAnalyzer().analyze(cur3)
    check("LTFT phang -> KHONG bao (chong bao gia)",
          not any(r["pid_name"] == "ltft_avg" for r in res_flat), f"res={res_flat}")
except Exception as e:
    import traceback
    check("Test predictive chay duoc", False, f"EXC: {e}")
    traceback.print_exc()

# ---- 5. DTC scan (sim) ----
print("\n[5] DTC: scan che do sim -> ghi dtc_logs")
try:
    import obd_module.can_app as can_app_mod
    _old = can_app_mod.CAN_BUS_TYPE
    can_app_mod.CAN_BUS_TYPE = "virtual"
    fresh_db()
    reader = can_app_mod.OBDReader()
    found = reader.scan_dtc()
    check("scan_dtc (sim) tra >=1 ma loi", isinstance(found, list) and len(found) >= 1, f"found={found}")
    c = sqlite3.connect(_TEST_DB, timeout=5)
    n_dtc = c.execute("SELECT COUNT(*) FROM dtc_logs").fetchone()[0]
    c.close()
    check("dtc_logs co ban ghi", n_dtc >= 1, f"n_dtc={n_dtc}")
    can_app_mod.CAN_BUS_TYPE = _old
except Exception as e:
    import traceback
    check("Test DTC chay duoc", False, f"EXC: {e}")
    traceback.print_exc()

print("\n" + "=" * 64)
print(f"=== PHAN A: {PASS} PASS / {FAIL} FAIL ===")
print("=" * 64)

import shutil
shutil.rmtree(_tmpdir, ignore_errors=True)
config.DATABASE_PATH = _REAL_DB


# ============================================================
# PHAN B: VERIFY REALTIME (can xe that)
# ============================================================
print("\n")
ans = input(">>> Chay PHAN B (doc realtime, CAN XE that dang no may)? [y/N]: ").strip().lower()
if ans != "y":
    print("[*] Bo qua phan B. Ket thuc.")
    sys.exit(0 if FAIL == 0 else 1)

if config.OPERATION_MODE == "SIMULATION":
    print("[-] Dang SIMULATION - phan B can PRODUCTION + xe that. Dung.")
    sys.exit(1)

import can
from config import (CAN_INTERFACE, CAN_BUS_TYPE, CAN_BITRATE,
                    OBD_REQUEST_ID, OBD_RESPONSE_ID, PID_RESPONSE_TIMEOUT)

PROBES = [
    (0x0C, "RPM",      lambda d: ((d[3] * 256) + d[4]) / 4.0,   "v/p",  (0, 8000)),
    (0x0D, "Toc do",   lambda d: float(d[3]),                   "km/h", (0, 255)),
    (0x05, "Nuoc",     lambda d: float(d[3] - 40),              "do C", (-40, 130)),
    (0x11, "Buom ga",  lambda d: (d[3] * 100.0) / 255.0,        "%",    (0, 100)),
    (0x0F, "Khi nap",  lambda d: float(d[3] - 40),              "do C", (-40, 100)),
    (0x10, "MAF",      lambda d: ((d[3] * 256) + d[4]) / 100.0, "g/s",  (0, 700)),
    (0x07, "LTFT",     lambda d: (d[3] * 100.0) / 128.0 - 100.0, "%",   (-100, 100)),
]
print("\n=== PHASE 6B: XAC NHAN SO THAT (REALTIME) ===")
print("Tac dong len xe (dap ga / de may chay / chay xe) -> xem so doi dung vat ly.")
print("Bam Ctrl+C de ra bang tong ket.\n")

bus = can.interface.Bus(channel=CAN_INTERFACE, interface=CAN_BUS_TYPE, bitrate=CAN_BITRATE,
                        can_filters=[{"can_id": 0x7E8, "can_mask": 0x7F8}])
stats = {pid: {"min": None, "max": None, "n": 0, "miss": 0} for pid, *_ in PROBES}


def query(pid, decode):
    bus.send(can.Message(arbitration_id=OBD_REQUEST_ID,
                         data=[0x02, 0x01, pid, 0, 0, 0, 0, 0], is_extended_id=False))
    t0 = time.monotonic()
    while time.monotonic() < t0 + PID_RESPONSE_TIMEOUT:
        try:
            m = bus.recv(timeout=0.005)
        except (ValueError, IndexError):
            continue
        if m is None or m.arbitration_id != OBD_RESPONSE_ID:
            continue
        d = m.data
        if len(d) >= 3 and d[1] == 0x41 and d[2] == pid:
            try:
                return decode(d)
            except (IndexError, ValueError):
                return None
    return None


try:
    while True:
        line = []
        for pid, name, decode, unit, (lo, hi) in PROBES:
            val = query(pid, decode)
            s = stats[pid]
            if val is None:
                s["miss"] += 1
                line.append(f"{name}=MISS")
            else:
                s["n"] += 1
                s["min"] = val if s["min"] is None else min(s["min"], val)
                s["max"] = val if s["max"] is None else max(s["max"], val)
                flag = "!" if not (lo <= val <= hi) else ""
                line.append(f"{name}={val:.1f}{unit}{flag}")
        print("  " + " | ".join(line))
        time.sleep(0.5)
except KeyboardInterrupt:
    print("\n\n" + "=" * 64)
    print("=== PHAN B TONG KET (kiem chung bien thien) ===")
    print("=" * 64)
    print(f"{'PID':10} {'mau':>5} {'miss':>5} {'min':>8} {'max':>8}  {'bien thien?':>12}")
    for pid, name, decode, unit, rng in PROBES:
        s = stats[pid]
        if s["n"] == 0:
            print(f"{name:10} {s['n']:>5} {s['miss']:>5} {'--':>8} {'--':>8}  {'KHONG DOC':>12}")
        else:
            varied = "CO" if (s["max"] - s["min"]) > 0.01 else "DUNG IM"
            print(f"{name:10} {s['n']:>5} {s['miss']:>5} {s['min']:>8.1f} {s['max']:>8.1f}  {varied:>12}")
    print("\n  'CO' = so THAT (phan ung tac dong). 'DUNG IM' = doi chieu taplo.")
    bus.shutdown()