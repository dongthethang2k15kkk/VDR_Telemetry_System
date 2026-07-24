# diagnosis/test_adapters.py
#
# Script tu chay, khong can pytest. Verify: python3 diagnosis/test_adapters.py

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from diagnosis.evidence import THETA, MAF_DEGRADED, ECT_SENSOR_FAULT, BUS_LINK_FAULT
from diagnosis.adapters import evaluate_e5, evaluate_e7

_failures = []


def check(name, condition):
    if condition:
        print(f"[+] {name}")
    else:
        print(f"[-] THAT BAI: {name}")
        _failures.append(name)


def approx(a, b, eps=1e-6):
    return abs(a - b) <= eps


# ---------------------------------------------------------------------
# E5 - suc khoe PID
# ---------------------------------------------------------------------

e5_khong_co = evaluate_e5([])
check("evaluate_e5: khong PID nao bi bao -> Theta", approx(e5_khong_co.masses[THETA], 1.0))

e5_mot_pid_maf = evaluate_e5([0x10])
check("evaluate_e5: 1 PID (MAF) mat tin hieu -> nghieng MAF_DEGRADED",
      frozenset({MAF_DEGRADED}) in e5_mot_pid_maf.masses)
check("evaluate_e5: 1 PID rieng le -> d=0.20 -> m=0.4 (tang trung binh cua mass_from_deviation)",
      approx(e5_mot_pid_maf.masses[frozenset({MAF_DEGRADED})], 0.4))

e5_mot_pid_khong_map = evaluate_e5([0x07])  # LTFT - khong co gia thuyet tuong ung
check("evaluate_e5: 1 PID khong anh xa duoc (LTFT) -> Theta",
      approx(e5_mot_pid_khong_map.masses[THETA], 1.0))

e5_hai_pid = evaluate_e5([0x10, 0x05])
check("evaluate_e5: 2 PID cung mat tin hieu -> nghieng BUS_LINK_FAULT",
      frozenset({BUS_LINK_FAULT}) in e5_hai_pid.masses)
check("evaluate_e5: 2 PID -> d=0.35 -> m=0.75 (tin cay hon truong hop 1 PID)",
      approx(e5_hai_pid.masses[frozenset({BUS_LINK_FAULT})], 0.75))
check("evaluate_e5: BUS_LINK_FAULT tin cay hon nghi mot cam bien don le",
      e5_hai_pid.masses[frozenset({BUS_LINK_FAULT})] > e5_mot_pid_maf.masses[frozenset({MAF_DEGRADED})])

e5_ba_pid = evaluate_e5([0x10, 0x05, 0x0F])
check("evaluate_e5: >=2 PID (ke ca 3) van la BUS_LINK_FAULT",
      frozenset({BUS_LINK_FAULT}) in e5_ba_pid.masses)


# ---------------------------------------------------------------------
# E7 - xu huong (boc TrendAnalyzer.analyze() ket qua)
# ---------------------------------------------------------------------

check("evaluate_e7: danh sach rong -> list rong", evaluate_e7([]) == [])
check("evaluate_e7: None -> list rong (an toan)", evaluate_e7(None) == [])

alert_ltft_warning = {
    "pid_name": "ltft_avg", "current_avg": 18.0, "slope": 0.5,
    "trips_to_threshold": 5, "severity": "warning", "description": "LTFT dang tang...",
}
r7_warning = evaluate_e7([alert_ltft_warning])
check("evaluate_e7: alert ltft_avg -> co 1 Evidence", len(r7_warning) == 1)
check("evaluate_e7: alert ltft_avg -> nghieng MAF_DEGRADED",
      frozenset({MAF_DEGRADED}) in r7_warning[0].masses)
check("evaluate_e7: severity=warning -> d=0.25 -> m=0.4",
      approx(r7_warning[0].masses[frozenset({MAF_DEGRADED})], 0.4))

alert_ltft_critical = dict(alert_ltft_warning, severity="critical")
r7_critical = evaluate_e7([alert_ltft_critical])
check("evaluate_e7: severity=critical -> d=0.45 -> m=0.85 (tran, tin cay hon warning)",
      approx(r7_critical[0].masses[frozenset({MAF_DEGRADED})], 0.85))
check(
    "evaluate_e7: critical tin cay hon warning",
    r7_critical[0].masses[frozenset({MAF_DEGRADED})] > r7_warning[0].masses[frozenset({MAF_DEGRADED})],
)

alert_coolant = {
    "pid_name": "coolant_avg", "current_avg": 100.0, "slope": 0.8,
    "trips_to_threshold": 3, "severity": "critical", "description": "Nhiet do nuoc dang tang...",
}
check("evaluate_e7: alert coolant_avg khong anh xa duoc gia thuyet nao -> bi loc bo",
      evaluate_e7([alert_coolant]) == [])

r7_tron_lan = evaluate_e7([alert_ltft_warning, alert_coolant])
check("evaluate_e7: tron alert ltft+coolant -> chi giu lai alert anh xa duoc (ltft)",
      len(r7_tron_lan) == 1 and r7_tron_lan[0].raw["pid_name"] == "ltft_avg")


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