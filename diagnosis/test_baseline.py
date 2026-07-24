# diagnosis/test_baseline.py
#
# Script tu chay, khong can pytest. Verify: python3 diagnosis/test_baseline.py

import os
import sqlite3
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from diagnosis.baseline import (
    learn_from_values, learn_from_db, record_sample,
    CHUA_DU, DANG_HOC, DU, N_TOI_THIEU, N_DU, MAD_TO_STD,
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
# 1. Trung vi / MAD tinh tay doc lap - ben voi ngoai le
#
#   values = [10, 11, 12, 100]
#   sorted = [10, 11, 12, 100], n=4 chan -> median = (11+12)/2 = 11.5
#   |x-median| = [1.5, 0.5, 0.5, 88.5] -> sorted = [0.5, 0.5, 1.5, 88.5]
#   MAD = (0.5+1.5)/2 = 1.0
# ---------------------------------------------------------------------

st = learn_from_values("test_metric", [10, 11, 12, 100])
check("baseline: median dung voi ngoai le (11.5)", approx(st.median, 11.5))
check("baseline: MAD ben voi ngoai le, khong bi 100 keo bung (MAD=1.0)", approx(st.mad, 1.0))
check("baseline: n dung (4)", st.n == 4)

# Doi chieu: trung binh/do lech chuan (KHONG dung trong code that) se bi
# ngoai le keo lech han - chung minh vi sao chon median/MAD (muc 5.3).
_mean = sum([10, 11, 12, 100]) / 4
check(
    "baseline: (doi chieu) trung binh bi ngoai le keo lech xa median that",
    abs(_mean - st.median) > 20,
)


# ---------------------------------------------------------------------
# 2. Trang thai hoc theo so mau (muc 5.4)
# ---------------------------------------------------------------------

s0 = learn_from_values("m", [])
check("trang thai: 0 mau -> CHUA_DU", s0.status == CHUA_DU)
check("trang thai: 0 mau -> alpha=0", approx(s0.alpha, 0.0))
check("trang thai: 0 mau -> median=None (khong bia so)", s0.median is None)

s2 = learn_from_values("m", [1.0, 2.0])
check(f"trang thai: 2 mau (<{N_TOI_THIEU}) -> CHUA_DU", s2.status == CHUA_DU)
check("trang thai: 2 mau -> alpha=0 (khong dua bang chung nao)", approx(s2.alpha, 0.0))

s5 = learn_from_values("m", [1.0, 2.0, 3.0, 4.0, 5.0])
check(f"trang thai: 5 mau ({N_TOI_THIEU}<=n<{N_DU}) -> DANG_HOC", s5.status == DANG_HOC)
check("trang thai: 5 mau -> alpha=5/10=0.5", approx(s5.alpha, 0.5))

s10 = learn_from_values("m", list(range(10)))
check(f"trang thai: 10 mau (n>={N_DU}) -> DU", s10.status == DU)
check("trang thai: 10 mau -> alpha=1.0", approx(s10.alpha, 1.0))

s20 = learn_from_values("m", list(range(20)))
check("trang thai: 20 mau van la DU (khong vuot qua 1.0)", s20.status == DU and approx(s20.alpha, 1.0))

# Bien vua dung nguong
s3 = learn_from_values("m", [1.0, 2.0, 3.0])
check(f"trang thai: dung {N_TOI_THIEU} mau -> DANG_HOC (khong con CHUA_DU)", s3.status == DANG_HOC)


# ---------------------------------------------------------------------
# 3. deviation_sigma / deviation_ratio
# ---------------------------------------------------------------------

st2 = learn_from_values("m", [10.0, 12.0, 14.0])  # median=12, mad=median(|2,0,2|)=2
check("deviation_sigma: dung tai median -> 0", approx(st2.deviation_sigma(12.0), 0.0))
check(
    "deviation_sigma: cong thuc |x-median|/(1.4826*MAD)",
    approx(st2.deviation_sigma(20.0), abs(20.0 - 12.0) / (MAD_TO_STD * 2.0)),
)

st_flat = learn_from_values("m", [5.0, 5.0, 5.0, 5.0])  # MAD=0
check("deviation_sigma: MAD=0 va x=median -> 0 (khong chia 0)", approx(st_flat.deviation_sigma(5.0), 0.0))
check("deviation_sigma: MAD=0 va x!=median -> vo cuc", st_flat.deviation_sigma(5.1) == float("inf"))

check("deviation_ratio: dung tai median -> 0", approx(st2.deviation_ratio(12.0), 0.0))
check("deviation_ratio: ty le dung |x-median|/median", approx(st2.deviation_ratio(18.0), 0.5))

try:
    s0.deviation_sigma(1.0)
    check("deviation_sigma: chua co mau nao phai nem ValueError", False)
except ValueError:
    check("deviation_sigma: chua co mau nao phai nem ValueError", True)


# ---------------------------------------------------------------------
# 4. Tich hop voi DB that (SQLite in-memory, dung dung schema muc 9)
# ---------------------------------------------------------------------

conn = sqlite3.connect(":memory:")
cur = conn.cursor()
cur.execute('''CREATE TABLE baseline_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT,
    value REAL,
    ect_at_sample REAL,
    iat_at_sample REAL,
    created_at REAL)''')
conn.commit()

for v in [10.0, 11.0, 12.0, 100.0]:
    record_sample(cur, "maf_per_rpm_idle", v, ect=85.0, iat=30.0, created_at=1000.0)
conn.commit()

st_db = learn_from_db(cur, "maf_per_rpm_idle")
check("record_sample + learn_from_db: khop voi learn_from_values truc tiep",
      approx(st_db.median, st.median) and approx(st_db.mad, st.mad))
check("record_sample + learn_from_db: dung du n=4 mau da ghi", st_db.n == 4)

st_other = learn_from_db(cur, "metric_khong_ton_tai")
check("learn_from_db: metric chua co mau nao -> CHUA_DU, n=0", st_other.status == CHUA_DU and st_other.n == 0)

# Ghi them mau cho metric khac, xac nhan khong lam nhieu metric ban dau
record_sample(cur, "ltft_idle", 3.5, ect=85.0, iat=30.0)
conn.commit()
st_maf_again = learn_from_db(cur, "maf_per_rpm_idle")
check("learn_from_db: them mau metric khac khong anh huong metric cu", st_maf_again.n == 4)

conn.close()


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