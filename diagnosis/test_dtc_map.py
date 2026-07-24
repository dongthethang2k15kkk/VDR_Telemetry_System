# diagnosis/test_dtc_map.py
#
# Script tu chay, khong can pytest. Verify: python3 diagnosis/test_dtc_map.py

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from diagnosis.evidence import THETA, MAF_DEGRADED, INTAKE_LEAK, THERMOSTAT_OPEN
from diagnosis.dtc_map import evaluate_e6

_failures = []


def check(name, condition):
    if condition:
        print(f"[+] {name}")
    else:
        print(f"[-] THAT BAI: {name}")
        _failures.append(name)


def approx(a, b, eps=1e-9):
    return abs(a - b) <= eps


# ---------------------------------------------------------------------

r_rong = evaluate_e6([])
check("evaluate_e6: danh sach rong -> list 1 phan tu", len(r_rong) == 1)
check("evaluate_e6: danh sach rong -> Theta", approx(r_rong[0].masses[THETA], 1.0))

r_khong_khop = evaluate_e6(["P9999_KHONG_TON_TAI"])
check("evaluate_e6: ma khong co trong DTC_MAP -> Theta", approx(r_khong_khop[0].masses[THETA], 1.0))

r_p0100 = evaluate_e6(["P0100"])
check("evaluate_e6: P0100 -> 1 Evidence", len(r_p0100) == 1)
check("evaluate_e6: P0100 -> m(MAF_DEGRADED)=0.85",
      approx(r_p0100[0].masses.get(frozenset({MAF_DEGRADED}), 0.0), 0.85))

r_p0171 = evaluate_e6(["P0171"])
check("evaluate_e6: P0171 -> gan cho TAP {MAF_DEGRADED, INTAKE_LEAK}, khong tach rieng",
      frozenset({MAF_DEGRADED, INTAKE_LEAK}) in r_p0171[0].masses)
check("evaluate_e6: P0171 -> do manh 0.60 (thap hon P0100 vi khong phan biet duoc)",
      approx(r_p0171[0].masses[frozenset({MAF_DEGRADED, INTAKE_LEAK})], 0.60))

# Hai ma khac nhom -> HAI Evidence rieng, khong gop vao mot dict (tranh vuot 1.0)
r_hai_nhom = evaluate_e6(["P0100", "P0128"])
check("evaluate_e6: hai ma khac nhom gia thuyet -> tra ve 2 Evidence", len(r_hai_nhom) == 2)
check(
    "evaluate_e6: moi Evidence rieng van tong = 1 (khong vi pham bat bien)",
    all(approx(sum(e.masses.values()), 1.0) for e in r_hai_nhom),
)
_hyps_tra_ve = {frozenset(e.raw["hypothesis_set"]) for e in r_hai_nhom}
check(
    "evaluate_e6: dung hai nhom la MAF_DEGRADED va THERMOSTAT_OPEN",
    frozenset({MAF_DEGRADED}) in _hyps_tra_ve and frozenset({THERMOSTAT_OPEN}) in _hyps_tra_ve,
)

# Hai ma CUNG mot nhom (P0100, P0101 deu -> MAF_DEGRADED) -> gop thanh MOT Evidence
r_cung_nhom = evaluate_e6(["P0100", "P0101"])
check("evaluate_e6: hai ma cung nhom gia thuyet -> GOP thanh 1 Evidence (khong nhan doi)",
      len(r_cung_nhom) == 1)
check("evaluate_e6: gop nhom -> ghi lai ca hai ma trong raw",
      set(r_cung_nhom[0].raw["dtc_codes"]) == {"P0100", "P0101"})
check("evaluate_e6: gop nhom -> do manh la max, khong cong don (van 0.85, khong phai 1.7)",
      approx(r_cung_nhom[0].masses.get(frozenset({MAF_DEGRADED}), 0.0), 0.85))

# Ma khop + ma khong khop tron lan -> bo qua ma la, chi giu ma khop
r_tron_lan = evaluate_e6(["P0100", "P9999_LA"])
check("evaluate_e6: ma la bi bo qua lang le, chi giu ma khop", len(r_tron_lan) == 1)
check("evaluate_e6: raw chi ghi ma THAT SU khop", r_tron_lan[0].raw["dtc_codes"] == ["P0100"])


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