# diagnosis/test_evidence.py
#
# Script tu chay (khong can pytest - du an khong co pytest trong
# requirements.txt, giu dung nep diagnostics/test_p0_hardware.py).
# Verify: python3 diagnosis/test_evidence.py
#
# Tang A - dung dan thuat toan (muc 11 ban giao). Bat buoc co trong file nay:
#   - tong khoi luong luon bang 1
#   - chiet khau alpha=0 lam moi khoi luong don ve THETA
# (hai test con lai cua Tang A - Dempster vi du kinh dien, nghich ly Zadeh -
#  thuoc ve fusion.py, buoc 2, chua lam o day.)

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from diagnosis.evidence import (
    Evidence, THETA, HYPOTHESES, NORMAL, MAF_DEGRADED, INTAKE_LEAK,
    MASS_CAP_SINGLE_SOURCE, _normalize_masses, mass_from_deviation,
)

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
# 1. _normalize_masses: bat bien tong = 1
# ---------------------------------------------------------------------

m = _normalize_masses({frozenset({MAF_DEGRADED}): 0.6})
check("normalize: tong = 1 sau khi them THETA", approx(sum(m.values()), 1.0))
check("normalize: phan thieu don dung vao THETA", approx(m[THETA], 0.4))
check("normalize: khoi luong goc giu nguyen", approx(m[frozenset({MAF_DEGRADED})], 0.6))

m2 = _normalize_masses({
    frozenset({MAF_DEGRADED}): 0.5,
    frozenset({INTAKE_LEAK}): 0.5,
})
check("normalize: tong da du 1 thi khong dong THETA", THETA not in m2)
check("normalize: tong = 1 (truong hop du san)", approx(sum(m2.values()), 1.0))

m3 = _normalize_masses({frozenset(HYPOTHESES): 1.0})
check("normalize: gan het cho THETA van hop le (Bel(Theta)=1)", approx(m3[THETA], 1.0))

try:
    _normalize_masses({frozenset({MAF_DEGRADED}): 1.5})
    check("normalize: tong > 1 phai nem ValueError", False)
except ValueError:
    check("normalize: tong > 1 phai nem ValueError", True)

try:
    _normalize_masses({frozenset({MAF_DEGRADED}): -0.5})
    check("normalize: tong am phai nem ValueError", False)
except ValueError:
    check("normalize: tong am phai nem ValueError", True)

m_empty = _normalize_masses({})
check("normalize: dict rong -> toan bo ve THETA", approx(m_empty[THETA], 1.0))


# ---------------------------------------------------------------------
# 2. Evidence.__post_init__: validate + tu chuan hoa
# ---------------------------------------------------------------------

e = Evidence(source="TEST", masses={frozenset({MAF_DEGRADED}): 0.6}, reliability=1.0)
check("Evidence: tu chuan hoa khi khoi tao", approx(sum(e.masses.values()), 1.0))

try:
    Evidence(source="TEST", masses={"MAF_DEGRADED": 0.6})  # str, khong phai frozenset
    check("Evidence: key khong phai frozenset phai nem TypeError", False)
except TypeError:
    check("Evidence: key khong phai frozenset phai nem TypeError", True)

try:
    Evidence(source="TEST", masses={frozenset({"KHONG_TON_TAI"}): 0.5})
    check("Evidence: gia thuyet ngoai THETA phai nem ValueError", False)
except ValueError:
    check("Evidence: gia thuyet ngoai THETA phai nem ValueError", True)

try:
    Evidence(source="TEST", masses={frozenset({MAF_DEGRADED}): 0.5}, reliability=1.5)
    check("Evidence: reliability ngoai [0,1] phai nem ValueError", False)
except ValueError:
    check("Evidence: reliability ngoai [0,1] phai nem ValueError", True)

try:
    Evidence(source="TEST", masses={frozenset(): 0.5})
    check("Evidence: tap rong lam key phai nem ValueError", False)
except ValueError:
    check("Evidence: tap rong lam key phai nem ValueError", True)


# ---------------------------------------------------------------------
# 3. discounted(): chiet khau Shafer - alpha=0 don het ve THETA (muc 11)
# ---------------------------------------------------------------------

e_full = Evidence(
    source="TEST", reliability=0.0,
    masses={frozenset({MAF_DEGRADED}): 0.6, frozenset({INTAKE_LEAK}): 0.2},
)
d0 = e_full.discounted()
check("discounted: alpha=0 -> THETA nhan het khoi luong", approx(d0[THETA], 1.0))
check(
    "discounted: alpha=0 -> moi gia thuyet khac deu ve 0",
    all(approx(v, 0.0) for k, v in d0.items() if k != THETA),
)

e_alpha1 = Evidence(
    source="TEST", reliability=1.0,
    masses={frozenset({MAF_DEGRADED}): 0.6},
)
d1 = e_alpha1.discounted()
check(
    "discounted: alpha=1 -> giu nguyen masses goc",
    approx(d1[frozenset({MAF_DEGRADED})], 0.6) and approx(d1[THETA], 0.4),
)

e_half = Evidence(
    source="TEST", reliability=0.5,
    masses={frozenset({MAF_DEGRADED}): 0.6},
)
dh = e_half.discounted()
check("discounted: alpha=0.5 -> m(H) giam theo ty le", approx(dh[frozenset({MAF_DEGRADED})], 0.3))
check("discounted: tong sau chiet khau van bang 1", approx(sum(dh.values()), 1.0))


# ---------------------------------------------------------------------
# 4. mass_from_deviation: ba khoang cua ham doc tuyen tinh (muc 4.2)
# ---------------------------------------------------------------------

low = mass_from_deviation(MAF_DEGRADED, 0.05)
check("mass_from_deviation: d<0.10 -> nghieng NORMAL", frozenset({NORMAL}) in low)
check("mass_from_deviation: d<0.10 -> m(NORMAL)=0.7", approx(low[frozenset({NORMAL})], 0.7))

mid = mass_from_deviation(MAF_DEGRADED, 0.20)
check("mass_from_deviation: 0.10<=d<0.30 -> m(H)=0.4", approx(mid[frozenset({MAF_DEGRADED})], 0.4))

high = mass_from_deviation(MAF_DEGRADED, 0.3)
check(
    "mass_from_deviation: d>=0.30 -> m(H)=0.4+d (chua cham tran)",
    approx(high[frozenset({MAF_DEGRADED})], 0.7),
)

extreme = mass_from_deviation(MAF_DEGRADED, 5.0)
check(
    f"mass_from_deviation: d lon -> tran o {MASS_CAP_SINGLE_SOURCE} (khong bao gio 1.0 tuyet doi)",
    approx(extreme[frozenset({MAF_DEGRADED})], MASS_CAP_SINGLE_SOURCE),
)

check(
    "mass_from_deviation: bien duoc kep [0,1], d am xu ly nhu 0",
    frozenset({NORMAL}) in mass_from_deviation(MAF_DEGRADED, -1.0),
)

try:
    mass_from_deviation("KHONG_TON_TAI", 0.5)
    check("mass_from_deviation: hypothesis la vong phai nem ValueError", False)
except ValueError:
    check("mass_from_deviation: hypothesis la vong phai nem ValueError", True)


# ---------------------------------------------------------------------
print("=" * 60)
if _failures:
    print(f"THAT BAI {len(_failures)} TEST:")
    for name in _failures:
        print(f"  - {name}")
    sys.exit(1)
else:
    print(f"TAT CA TEST PASS.")
    sys.exit(0)
