# diagnosis/test_diagnosis.py
#
# Script tu chay, khong can pytest. Verify: python3 diagnosis/test_diagnosis.py

import os
import sqlite3
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from diagnosis import run_diagnosis, save_diagnosis_result
from diagnosis.baseline import record_sample
from diagnosis.fusion import KET_LUAN, NGHI_NGO, KHONG_KET_LUAN

_failures = []


def check(name, condition):
    if condition:
        print(f"[+] {name}")
    else:
        print(f"[-] THAT BAI: {name}")
        _failures.append(name)


def approx(a, b, eps=1e-6):
    return abs(a - b) <= eps


def _new_db():
    """Tao DB in-memory dung DUNG schema muc 9 (khong dung file that)."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute('''CREATE TABLE baseline_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        metric TEXT, value REAL, ect_at_sample REAL, iat_at_sample REAL, created_at REAL)''')
    cur.execute('''CREATE TABLE diagnosis_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_sec REAL,
        top_hypothesis TEXT, belief REAL, plausibility REAL, conflict_k REAL,
        decision TEXT, evidence_json TEXT, created_at REAL)''')
    conn.commit()
    return conn, cur


# ---------------------------------------------------------------------
# 1. live_data rong -> khong co bang chung nao, van tra ve hop le
# ---------------------------------------------------------------------

conn, cur = _new_db()
r_rong = run_diagnosis(cur, {})
check("run_diagnosis: live_data rong -> evidence_count=0", r_rong["evidence_count"] == 0)
check("run_diagnosis: khong bang chung -> decision=KHONG_KET_LUAN", r_rong["decision"] == KHONG_KET_LUAN)
conn.close()


# ---------------------------------------------------------------------
# 2. Chi co DTC manh (P0100) -> KET_LUAN
# ---------------------------------------------------------------------

conn, cur = _new_db()
r_dtc = run_diagnosis(cur, {"dtc_codes": ["P0100"]})
check("run_diagnosis: DTC P0100 rieng le -> evidence_count=1", r_dtc["evidence_count"] == 1)
check("run_diagnosis: DTC manh -> decision=KET_LUAN", r_dtc["decision"] == KET_LUAN)
check("run_diagnosis: top_hypothesis dung MAF_DEGRADED", r_dtc["top_hypothesis"] == "MAF_DEGRADED")
conn.close()


# ---------------------------------------------------------------------
# 3. RPM ngoai hai vung (idle/2500) -> E1 khong chay du co du rpm+maf
# ---------------------------------------------------------------------

conn, cur = _new_db()
r_ngoai_vung = run_diagnosis(cur, {"rpm": 1500, "maf": 5.0})  # 1500 khong thuoc vung nao
check("run_diagnosis: rpm ngoai vung idle/2500 -> E1 khong dong gop, evidence_count=0",
      r_ngoai_vung["evidence_count"] == 0)
conn.close()


# ---------------------------------------------------------------------
# 4. RPM trong vung idle nhung baseline CHUA_DU -> E1 van chay nhung
#    tra Theta (khong dong gop khoi luong, van tinh 1 evidence)
# ---------------------------------------------------------------------

conn, cur = _new_db()
r_baseline_rong = run_diagnosis(cur, {"rpm": 800, "maf": 3.6, "iat": 20.0})
check("run_diagnosis: rpm trong vung idle -> E1 CO chay (evidence_count=1)",
      r_baseline_rong["evidence_count"] == 1)
check("run_diagnosis: baseline rong -> decision van la KHONG_KET_LUAN",
      r_baseline_rong["decision"] == KHONG_KET_LUAN)
conn.close()


# ---------------------------------------------------------------------
# 5. RPM trong vung idle, baseline DU va khop -> nghieng NORMAL, ket hop
#    voi E5 (mot PID mat tin hieu khong lien quan) - vi cac gia thuyet
#    khac nhau (NORMAL vs BUS_LINK_FAULT khong map vi chi 1 PID) nen it
#    xung dot.
# ---------------------------------------------------------------------

conn, cur = _new_db()
for _ in range(10):
    record_sample(cur, "maf_per_rpm_idle", 3.6 / 800.0, ect=85.0, iat=25.0)
conn.commit()
r_normal = run_diagnosis(cur, {"rpm": 800, "maf": 3.6, "iat": 20.0})
check("run_diagnosis: baseline DU + khop hoan toan -> nghieng NORMAL",
      r_normal["top_hypothesis"] == "NORMAL")
conn.close()


# ---------------------------------------------------------------------
# 6. save_diagnosis_result: ghi that vao DB, doc lai kiem tra
# ---------------------------------------------------------------------

conn, cur = _new_db()
result = run_diagnosis(cur, {"dtc_codes": ["P0128"]})
save_diagnosis_result(cur, result)
conn.commit()

row = cur.execute(
    "SELECT top_hypothesis, belief, decision, evidence_json FROM diagnosis_results"
).fetchone()
check("save_diagnosis_result: co dung 1 dong duoc ghi", row is not None)
check("save_diagnosis_result: top_hypothesis ghi dung", row[0] == "THERMOSTAT_OPEN")
check("save_diagnosis_result: belief ghi dung", approx(row[1], result["belief"]))
check("save_diagnosis_result: decision ghi dung", row[2] == result["decision"])

import json
parsed = json.loads(row[3])
check("save_diagnosis_result: evidence_json parse lai duoc (JSON hop le)", "masses" in parsed)
check("save_diagnosis_result: masses trong JSON co key dang chuoi (khong con frozenset)",
      all(isinstance(k, str) for k in parsed["masses"].keys()))
conn.close()


# ---------------------------------------------------------------------
# 7. Nhieu nguon xung dot -> conflict_k cao, decision than trong hon
# ---------------------------------------------------------------------

conn, cur = _new_db()
# E6 noi MAF_DEGRADED manh, E5 noi BUS_LINK_FAULT (PID khac) - hai gia
# thuyet khac nhau tu hai nguon doc lap -> co xung dot nhung khong cuc doan
r_hai_nguon = run_diagnosis(cur, {"dtc_codes": ["P0100"], "flagged_pids": [0x05, 0x0F]})
check("run_diagnosis: hai nguon (DTC + PID health) -> evidence_count=2", r_hai_nguon["evidence_count"] == 2)
check("run_diagnosis: co ket qua hop le (masses tong = 1)", approx(sum(r_hai_nguon["masses"].values()), 1.0))


# ---------------------------------------------------------------------
print("=" * 60)
if _failures:
    print(f"THAT BAI {len(_failures)} TEST:")
    for name in _failures:
        print(f"  - {name}")
    sys.exit(1)
else:
    print("TAT CA TEST PASS.")
    sys.exit(0)