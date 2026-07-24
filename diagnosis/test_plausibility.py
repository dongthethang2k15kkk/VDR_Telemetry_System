# diagnosis/test_plausibility.py
#
# Script tu chay, khong can pytest. Verify: python3 diagnosis/test_plausibility.py

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from diagnosis.evidence import (
    THETA, MAF_DEGRADED, INTAKE_LEAK, THERMOSTAT_OPEN,
    ECT_SENSOR_FAULT, IAT_SENSOR_FAULT,
)
from diagnosis.baseline import learn_from_values, CHUA_DU, DANG_HOC, DU
from diagnosis.plausibility import (
    _physics_expected_maf_g_s, sanity_bound_e1, evaluate_e1,
    classify_rpm_zone, evaluate_e2,
    evaluate_e3, compute_warmup_features, evaluate_e4,
    RPM_ZONE_IDLE, RPM_ZONE_2500, RPM_ZONE_OTHER,
)

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
# 1. Cong thuc vat ly E1 - doi chieu DUNG vi du kiem chung trong tai lieu
#    (muc 3.2): dong co 1.5L, 800rpm garanti, VE=0.3, khong khi 20C
#    -> ky vong ~3.6 g/s
# ---------------------------------------------------------------------

maf_vd = _physics_expected_maf_g_s(rpm=800, ve=0.3, temp_c=20.0, displacement_l=1.5)
check(
    f"cong thuc E1: vi du tai lieu (800rpm, 1.5L, VE=0.3, 20C) ~ 3.6 g/s, tinh ra {maf_vd:.3f}",
    approx(maf_vd, 3.6, eps=0.05),
)


# ---------------------------------------------------------------------
# 2. sanity_bound_e1 - bien kiem tra hop ly vat ly
# ---------------------------------------------------------------------

check("sanity_bound_e1: gia tri hop ly (VE~0.3 nam trong [0.2,1.0]) -> True",
      sanity_bound_e1(rpm=800, maf_measured=3.6, iat=20.0))
check("sanity_bound_e1: MAF gan bang 0 (giac tuot/mat tin hieu) -> False",
      not sanity_bound_e1(rpm=800, maf_measured=0.01, iat=20.0))
check("sanity_bound_e1: MAF cao phi ly (100 g/s o garanti) -> False",
      not sanity_bound_e1(rpm=800, maf_measured=100.0, iat=20.0))
check("sanity_bound_e1: rpm=0 (may chua no) -> True (khong danh gia duoc, khong bao nham)",
      sanity_bound_e1(rpm=0, maf_measured=5.0, iat=20.0))


# ---------------------------------------------------------------------
# 3. evaluate_e1 - bon nhanh: sanity fail, rpm=0, baseline chua du, binh thuong
# ---------------------------------------------------------------------

baseline_chua_du = learn_from_values("maf_per_rpm_idle", [])
baseline_du = learn_from_values("maf_per_rpm_idle", [3.6 / 800] * 10)  # 10 mau -> DU

e1_sanity_fail = evaluate_e1(rpm=800, maf_measured=100.0, iat=20.0, baseline=baseline_du)
check("evaluate_e1: sanity fail -> gan het MAF_DEGRADED (0.85), khong can baseline",
      approx(e1_sanity_fail.masses.get(frozenset({MAF_DEGRADED}), 0.0), 0.85))
check("evaluate_e1: sanity fail -> reliability=1.0 (tin ngay, khong phu thuoc baseline)",
      approx(e1_sanity_fail.reliability, 1.0))

e1_rpm0 = evaluate_e1(rpm=0, maf_measured=3.0, iat=20.0, baseline=baseline_du)
check("evaluate_e1: rpm=0 -> hoan toan Theta (khong danh gia)", approx(e1_rpm0.masses[THETA], 1.0))

e1_chua_du = evaluate_e1(rpm=800, maf_measured=3.6, iat=20.0, baseline=baseline_chua_du)
check("evaluate_e1: baseline CHUA_DU -> hoan toan Theta", approx(e1_chua_du.masses[THETA], 1.0))

e1_normal = evaluate_e1(rpm=800, maf_measured=3.6, iat=20.0, baseline=baseline_du)
# baseline hoc tu ty so 3.6/800, maf_measured=3.6 o dung rpm=800 -> khop hoan toan
from diagnosis.evidence import NORMAL as _NORMAL
check("evaluate_e1: khop baseline hoan toan -> m(NORMAL)=0.7",
      approx(e1_normal.masses.get(frozenset({_NORMAL}), 0.0), 0.7))
check("evaluate_e1: reliability = alpha cua baseline (DU -> 1.0)", approx(e1_normal.reliability, 1.0))

baseline_dang_hoc = learn_from_values("maf_per_rpm_idle", [3.6 / 800] * 5)  # 5 mau -> DANG_HOC, alpha=0.5
e1_dang_hoc = evaluate_e1(rpm=800, maf_measured=3.6, iat=20.0, baseline=baseline_dang_hoc)
check("evaluate_e1: baseline DANG_HOC -> reliability=alpha=0.5", approx(e1_dang_hoc.reliability, 0.5))


# ---------------------------------------------------------------------
# 4. classify_rpm_zone
# ---------------------------------------------------------------------

check("classify_rpm_zone: 800rpm -> idle", classify_rpm_zone(800) == RPM_ZONE_IDLE)
check("classify_rpm_zone: 2500rpm -> target_2500", classify_rpm_zone(2500) == RPM_ZONE_2500)
check("classify_rpm_zone: 2650rpm (trong dung sai 200) -> target_2500", classify_rpm_zone(2650) == RPM_ZONE_2500)
check("classify_rpm_zone: 1500rpm (giua hai vung) -> None", classify_rpm_zone(1500) == RPM_ZONE_OTHER)
check("classify_rpm_zone: 0rpm -> None", classify_rpm_zone(0) == RPM_ZONE_OTHER)


# ---------------------------------------------------------------------
# 5. evaluate_e2 - phan biet INTAKE_LEAK / MAF_DEGRADED / khong ro
# ---------------------------------------------------------------------

e2_lanh = evaluate_e2(ltft_idle=15.0, ltft_2500=2.0, ect=50.0)
check("evaluate_e2: may chua am (ECT<80) -> hoan toan Theta, khong doc LTFT",
      approx(e2_lanh.masses[THETA], 1.0))

# delta = 15-2 = 13 > 8 -> nghieng INTAKE_LEAK
e2_ho_nap = evaluate_e2(ltft_idle=15.0, ltft_2500=2.0, ect=85.0)
check("evaluate_e2: delta=13% (>8%) -> nghieng INTAKE_LEAK",
      frozenset({INTAKE_LEAK}) in e2_ho_nap.masses)
check("evaluate_e2: khong dong thoi gan MAF_DEGRADED", frozenset({MAF_DEGRADED}) not in e2_ho_nap.masses)

# delta = 6-5 = 1, |1|<=3, ca hai duong -> nghieng MAF_DEGRADED
e2_maf = evaluate_e2(ltft_idle=6.0, ltft_2500=5.0, ect=85.0)
check("evaluate_e2: delta=1% (<=3%, ca hai duong) -> nghieng MAF_DEGRADED",
      frozenset({MAF_DEGRADED}) in e2_maf.masses)

# delta = 10-5 = 5, nam giua 3 va 8 -> khong ro, Theta
e2_khong_ro = evaluate_e2(ltft_idle=10.0, ltft_2500=5.0, ect=85.0)
check("evaluate_e2: delta=5% (giua 3% va 8%) -> khong ket luan (Theta)",
      approx(e2_khong_ro.masses[THETA], 1.0))

# ca hai duong nhung delta nho, mot cai am -> khong thoa dieu kien 'ca hai duong'
e2_mot_am = evaluate_e2(ltft_idle=1.0, ltft_2500=-1.0, ect=85.0)
check("evaluate_e2: delta nho nhung mot gia tri am -> khong thoa dieu kien MAF_DEGRADED, ve Theta",
      approx(e2_mot_am.masses[THETA], 1.0))


# ---------------------------------------------------------------------
# 6. evaluate_e3 - IAT vs ECT luc khoi dong nguoi
# ---------------------------------------------------------------------

e3_khong_nguoi = evaluate_e3(iat=25.0, ect=45.0, is_cold_start=False)
check("evaluate_e3: khong phai khoi dong nguoi -> Theta (khong ap dung)",
      approx(e3_khong_nguoi.masses[THETA], 1.0))

e3_binh_thuong = evaluate_e3(iat=26.0, ect=28.0, is_cold_start=True)  # delta=2
check("evaluate_e3: delta=2C (<=5C) -> nghieng NORMAL", approx(e3_binh_thuong.masses.get(frozenset({_NORMAL}), 0), 0.7))

# IAT hop ly (25, trong 15-42), ECT ngoai dai (50) -> nghi ECT loi
e3_ect_loi = evaluate_e3(iat=25.0, ect=50.0, is_cold_start=True)  # delta=25
check("evaluate_e3: IAT hop ly, ECT ngoai dai VN -> nghi ECT_SENSOR_FAULT",
      frozenset({ECT_SENSOR_FAULT}) in e3_ect_loi.masses)

# ECT hop ly (25), IAT ngoai dai (60) -> nghi IAT loi
e3_iat_loi = evaluate_e3(iat=60.0, ect=25.0, is_cold_start=True)  # delta=35
check("evaluate_e3: ECT hop ly, IAT ngoai dai VN -> nghi IAT_SENSOR_FAULT",
      frozenset({IAT_SENSOR_FAULT}) in e3_iat_loi.masses)

# Ca hai deu trong dai hop ly nhung van lech >10 -> khong phan biet duoc
e3_khong_phan_biet = evaluate_e3(iat=16.0, ect=40.0, is_cold_start=True)  # delta=24, ca hai trong [15,42]
check("evaluate_e3: ca hai deu trong dai hop ly nhung lech lon -> Theta (khong phan biet duoc)",
      approx(e3_khong_phan_biet.masses[THETA], 1.0))

e3_vung_giua = evaluate_e3(iat=25.0, ect=32.0, is_cold_start=True)  # delta=7, giua 5 va 10
check("evaluate_e3: delta=7C (vung giua 5-10C) -> Theta", approx(e3_vung_giua.masses[THETA], 1.0))

e3_thieu_du_lieu = evaluate_e3(iat=None, ect=30.0, is_cold_start=True)
check("evaluate_e3: thieu IAT -> Theta", approx(e3_thieu_du_lieu.masses[THETA], 1.0))


# ---------------------------------------------------------------------
# 7. compute_warmup_features + evaluate_e4
# ---------------------------------------------------------------------

# Chuoi tinh tay doc lap: dat 80C giua mau t=120 (75C) va t=180 (85C)
# noi suy: frac=(80-75)/(85-75)=0.5 -> t_80=120+0.5*60=150
series_binh_thuong = [(0, 25.0), (60, 50.0), (120, 75.0), (180, 85.0), (300, 88.0)]
feat = compute_warmup_features(series_binh_thuong)
check("compute_warmup_features: t_80 noi suy dung (150s)", approx(feat["t_80"], 150.0))
check("compute_warmup_features: slope_max dung (25/60 giua hai doan dau)", approx(feat["slope_max"], 25.0 / 60.0, eps=1e-4))
check("compute_warmup_features: chuoi ngan hon 720s -> ect_plateau=None", feat["ect_plateau"] is None)

feat_it_mau = compute_warmup_features([(0, 25.0)])
check("compute_warmup_features: 1 mau -> tra ve None/0.0 an toan, khong loi",
      feat_it_mau["t_80"] is None and approx(feat_it_mau["slope_max"], 0.0))

feat_rong = compute_warmup_features([])
check("compute_warmup_features: chuoi rong -> khong loi", feat_rong["t_80"] is None)

e4_binh_thuong = evaluate_e4(series_binh_thuong, iat_start=28.0)
check("evaluate_e4: dat 80C dung han -> nghieng NORMAL",
      approx(e4_binh_thuong.masses.get(frozenset({_NORMAL}), 0), 0.7))

series_thermostat = [(0, 25.0), (400, 60.0), (800, 65.0)]  # qua 720s ma chua toi 80C
e4_thermostat = evaluate_e4(series_thermostat, iat_start=28.0)
check("evaluate_e4: qua 720s chua dat 80C -> nghi THERMOSTAT_OPEN",
      frozenset({THERMOSTAT_OPEN}) in e4_thermostat.masses)

series_chua_du = [(0, 25.0), (100, 50.0)]  # moi 100s, chua dat 80C, chua qua 720s
e4_chua_du = evaluate_e4(series_chua_du, iat_start=28.0)
check("evaluate_e4: chua dat 80C va chua qua 720s -> Theta (can them du lieu)",
      approx(e4_chua_du.masses[THETA], 1.0))

e4_rong = evaluate_e4([], iat_start=28.0)
check("evaluate_e4: chuoi rong -> Theta", approx(e4_rong.masses[THETA], 1.0))


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