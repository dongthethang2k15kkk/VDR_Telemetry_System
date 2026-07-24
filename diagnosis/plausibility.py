# diagnosis/plausibility.py
#
# E1 va E2 - hai bang chung vat ly dau tien, doi chieu du lieu do duoc voi
# quy luat vat ly va voi baseline tu hoc cua chinh chiec xe.
# Tham chieu: BANGIAO_CHAN_DOAN_HOP_NHAT.md muc 3.2 (E1), muc 3.3 (E2).
#
# File nay import hang so tu config.py (muc 3.3: "nguong phai dat trong
# config.py, khong hardcode") va dung BaselineState tu baseline.py. Khong
# doc DB truc tiep o day - nguoi goi (adapters.py/protocol.py) chiu trach
# nhiem lay baseline va truyen vao, giu dung ranh gioi "ham thuan de test
# duoc" da thong nhat.

import math

from config import (
    ENGINE_DISPLACEMENT_L, E1_VE_MIN, E1_VE_MAX,
    E1_MAF_SANITY_MIN_RATIO, E1_MAF_SANITY_MAX_RATIO,
    STANDARD_ATM_PRESSURE_PA, AIR_GAS_CONSTANT,
    RPM_IDLE_MIN, RPM_IDLE_MAX, RPM_2500_TARGET, RPM_2500_TOLERANCE,
    E2_DELTA_INTAKE_LEAK_THRESHOLD, E2_DELTA_MAF_THRESHOLD,
    ECT_WARM_THRESHOLD,
    E3_DELTA_NORMAL_MAX, E3_DELTA_FAULT_MIN,
    VN_AMBIENT_TEMP_MIN, VN_AMBIENT_TEMP_MAX,
    WARMUP_T80_MAX_SEC,
)
from .evidence import (
    Evidence, NORMAL, MAF_DEGRADED, INTAKE_LEAK,
    THERMOSTAT_OPEN, ECT_SENSOR_FAULT, IAT_SENSOR_FAULT,
    mass_from_deviation,
)
from .baseline import CHUA_DU, BaselineState, _median

# Ten vung RPM - dung chung giua E1/E2 va sau nay adapters.py/protocol.py
# de chon dung metric baseline va thoi diem ghi mau (muc 5.2, muc 8).
RPM_ZONE_IDLE = "idle"
RPM_ZONE_2500 = "target_2500"
RPM_ZONE_OTHER = None


def classify_rpm_zone(rpm: float):
    """Xac dinh RPM hien tai roi vao vung nao trong quy trinh kiem tra
    10 phut (muc 8): garanti hay giu 2500 vong. Tra ve None neu khong
    thuoc vung nao ca (dang chuyen tiep, ga khong on dinh)."""
    if RPM_IDLE_MIN <= rpm <= RPM_IDLE_MAX:
        return RPM_ZONE_IDLE
    if (RPM_2500_TARGET - RPM_2500_TOLERANCE) <= rpm <= (RPM_2500_TARGET + RPM_2500_TOLERANCE):
        return RPM_ZONE_2500
    return RPM_ZONE_OTHER


# ---------------------------------------------------------------------
# E1 - MAF so voi vong tua (muc 3.2)
# ---------------------------------------------------------------------

def _physics_expected_maf_g_s(rpm: float, ve: float, temp_c: float,
                               displacement_l: float = None,
                               pressure_pa: float = STANDARD_ATM_PRESSURE_PA) -> float:
    """Cong thuc vat ly muc 3.2:
        V_dot = (RPM/2) x V_displacement x VE / 60        [m3/s]
        MAF_ky_vong = V_dot x rho_air                       [kg/s]
        rho_air = P / (R x T)

    CHI dung lam BIEN KIEM TRA TINH HOP LY (sanity bound) trong
    sanity_bound_e1() - KHONG dung ket qua ham nay lam nguong chan doan
    chinh, vi VE khong biet truoc duoc cho tung xe/do mo buom ga (muc 3.2,
    cam bay #4 muc 13)."""
    displacement_l = displacement_l if displacement_l is not None else ENGINE_DISPLACEMENT_L
    t_kelvin = temp_c + 273.15
    rho_air = pressure_pa / (AIR_GAS_CONSTANT * t_kelvin)          # kg/m3
    v_dot = (rpm / 2.0) * (displacement_l / 1000.0) * ve / 60.0     # m3/s
    maf_kg_s = v_dot * rho_air
    return maf_kg_s * 1000.0   # -> g/s


def sanity_bound_e1(rpm: float, maf_measured: float, iat: float = None) -> bool:
    """Kiem tra MAF do duoc co nam trong dai vat ly hop ly khong, voi VE
    gia dinh trong [E1_VE_MIN, E1_VE_MAX] va bien noi long them
    [E1_MAF_SANITY_MIN_RATIO, E1_MAF_SANITY_MAX_RATIO] (muc 3.2).

    Tra ve True = HOP LY (khong ket luan gi them tu day, con phai xem baseline).
    Tra ve False = CHAC CHAN SAI - nam ngoai moi kha nang vat ly, khong can
    cho baseline hoc du de bao."""
    if rpm <= 0:
        return True   # khong danh gia duoc khi may chua no, tranh bao nham

    temp_c = iat if iat is not None else 20.0
    maf_lo = _physics_expected_maf_g_s(rpm, E1_VE_MIN, temp_c)
    maf_hi = _physics_expected_maf_g_s(rpm, E1_VE_MAX, temp_c)

    lo_bound = maf_lo * E1_MAF_SANITY_MIN_RATIO
    hi_bound = maf_hi * E1_MAF_SANITY_MAX_RATIO
    return lo_bound <= maf_measured <= hi_bound


def evaluate_e1(rpm: float, maf_measured: float, iat: float,
                 baseline: BaselineState) -> Evidence:
    """Bang chung E1 (muc 3.2). baseline phai la BaselineState DUNG VUNG
    RPM hien tai ('maf_per_rpm_idle' hoac 'maf_per_rpm_2500') - nguoi goi
    chon truoc bang classify_rpm_zone(), ham nay khong tu doan.

    Thu tu uu tien:
      1) Sai vat ly ro rang (sanity_bound_e1=False) -> tin cay ngay, khong
         can cho baseline (muc 3.2: 'chac chan cam bien sai, khong can baseline').
      2) Baseline CHUA_DU mau -> khong dua bang chung nao (Theta = 1.0).
      3) Binh thuong -> so lech ty le voi baseline, chiet khau theo alpha.
    """
    raw = {"rpm": rpm, "maf_measured": maf_measured, "iat": iat}

    if not sanity_bound_e1(rpm, maf_measured, iat):
        return Evidence(
            source="E1_maf_rpm_sanity",
            masses={frozenset({MAF_DEGRADED}): 0.85},
            reliability=1.0,
            detail=(f"MAF do duoc ({maf_measured:.2f} g/s) nam ngoai dai vat ly "
                    f"hop ly o {rpm:.0f} vong/phut (gia dinh VE {E1_VE_MIN}-{E1_VE_MAX})."),
            raw=raw,
        )

    if rpm <= 0:
        return Evidence(
            source="E1_maf_rpm", masses={}, reliability=0.0,
            detail="RPM = 0, may chua no, khong danh gia duoc E1.", raw=raw,
        )

    if baseline.status == CHUA_DU:
        return Evidence(
            source="E1_maf_rpm", masses={}, reliability=0.0,
            detail=f"Baseline '{baseline.metric}' chua du mau ({baseline.n}/3) de danh gia.",
            raw=raw,
        )

    current_ratio = maf_measured / rpm
    deviation = baseline.deviation_ratio(current_ratio)
    masses = mass_from_deviation(MAF_DEGRADED, deviation)

    return Evidence(
        source="E1_maf_rpm",
        masses=masses,
        reliability=baseline.alpha,
        detail=(f"MAF/RPM hien tai lech {deviation * 100:.0f}% so voi baseline "
                f"'{baseline.metric}' ({baseline.n} mau, trang thai {baseline.status})."),
        raw=raw,
    )


# ---------------------------------------------------------------------
# E2 - LTFT garanti so voi 2500 vong (muc 3.3) - phan biet MAF_DEGRADED
# voi INTAKE_LEAK, hai benh moi tin hieu khac deu nham lan.
# ---------------------------------------------------------------------

def evaluate_e2(ltft_idle: float, ltft_2500: float, ect: float) -> Evidence:
    """Bang chung E2 (muc 3.3).

        delta = LTFT_idle - LTFT_2500
        delta > E2_DELTA_INTAKE_LEAK_THRESHOLD (8%)         -> nghieng INTAKE_LEAK
        |delta| <= E2_DELTA_MAF_THRESHOLD (3%) va ca hai duong -> nghieng MAF_DEGRADED
        con lai                                              -> Theta (khong ro)

    Cam bay #3 (muc 13): LTFT luc may lanh vo nghia (ECU chay vong ho) -
    ham nay CHU DONG kiem tra ect >= ECT_WARM_THRESHOLD, khong tin tuong
    nguoi goi da loc san.

    reliability=1.0 la lua chon co y: day la phep doi chieu TUC THOI trong
    cung mot phien do (khong phu thuoc lich su baseline), khac E1. Neu sau
    nay muon giam tin cay theo do on dinh cua baseline ltft_idle/ltft_2500
    (hoc rieng theo muc 5.2), day la cho de cam them, chua lam trong buoc nay."""
    raw = {"ltft_idle": ltft_idle, "ltft_2500": ltft_2500, "ect": ect}

    if ect is None or ect < ECT_WARM_THRESHOLD:
        return Evidence(
            source="E2_ltft_idle_vs_2500", masses={}, reliability=0.0,
            detail=f"May chua du am (ECT={ect}, can >={ECT_WARM_THRESHOLD:.0f}C) - LTFT vo nghia.",
            raw=raw,
        )

    delta = ltft_idle - ltft_2500
    raw["delta"] = delta

    if delta > E2_DELTA_INTAKE_LEAK_THRESHOLD:
        # d=0.20 nam trong khoang [0.10,0.30) cua mass_from_deviation -> m=0.4
        # (tin cay vua, vi day la nguong khoi tao chua hieu chinh - muc 3.3)
        masses = mass_from_deviation(INTAKE_LEAK, 0.20)
        detail = (f"LTFT chenh garanti-2500 = {delta:.1f}% "
                   f"(> nguong {E2_DELTA_INTAKE_LEAK_THRESHOLD:.0f}%) - nghieng ve ho duong nap.")
    elif abs(delta) <= E2_DELTA_MAF_THRESHOLD and ltft_idle > 0 and ltft_2500 > 0:
        masses = mass_from_deviation(MAF_DEGRADED, 0.20)
        detail = (f"LTFT chenh garanti-2500 chi {delta:.1f}% (ca hai duong, "
                   f"<= nguong {E2_DELTA_MAF_THRESHOLD:.0f}%) - nghieng ve MAF suy giam.")
    else:
        masses = {}
        detail = f"LTFT chenh {delta:.1f}% - vung khong ro rang giua hai gia thuyet, khong ket luan."

    return Evidence(
        source="E2_ltft_idle_vs_2500", masses=masses, reliability=1.0,
        detail=detail, raw=raw,
    )


# ---------------------------------------------------------------------
# E3 - IAT so voi ECT luc khoi dong nguoi (muc 3.4) - "ground truth mien
# phi", moi sang no may la mot diem du lieu, khong ton nhien lieu.
# ---------------------------------------------------------------------

def evaluate_e3(iat: float, ect: float, is_cold_start: bool) -> Evidence:
    """Bang chung E3 (muc 3.4).

        |IAT-ECT| <= E3_DELTA_NORMAL_MAX (5C)   -> ca hai binh thuong
        |IAT-ECT| > E3_DELTA_FAULT_MIN (10C)     -> mot trong hai loi;
            xac dinh cai nao bang cach so voi dai moi truong VN hop ly
            [VN_AMBIENT_TEMP_MIN, VN_AMBIENT_TEMP_MAX]. Neu ca hai deu
            trong dai (hoac ca hai deu ngoai dai) -> khong phan biet duoc,
            Theta (day la diem manh cua D-S: duoc phep noi 'khong biet').
        khoang giua (5C < delta <= 10C)          -> vung mo, Theta.

    is_cold_start: CO BAT BUOC do NGUOI GOI xac dinh truoc (protocol.py/
    adapters.py), bang cach so sanh time.time() voi lan cuoi ghi du lieu
    trong DB (>6 gio -> coi la nguoi). Ham nay KHONG tu xac dinh, vi cam
    bay #6 (muc 13): 'khoi dong nguoi phai xac dinh bang khoang cach thoi
    gian, KHONG bang ECT' - ngay nang o VN, xe dau ngoai troi co the ECT=
    45C ma van la nguoi hoan toan. Neu ham nay tu doan qua ECT se roi dung
    vao chinh cai bay tai lieu canh bao."""
    raw = {"iat": iat, "ect": ect, "is_cold_start": is_cold_start}

    if not is_cold_start:
        return Evidence(
            source="E3_iat_ect_coldstart", masses={}, reliability=0.0,
            detail="Khong phai thoi diem khoi dong nguoi - E3 khong ap dung luc nay.",
            raw=raw,
        )
    if iat is None or ect is None:
        return Evidence(
            source="E3_iat_ect_coldstart", masses={}, reliability=0.0,
            detail="Thieu IAT hoac ECT, khong danh gia duoc E3.", raw=raw,
        )

    delta = abs(iat - ect)
    raw["delta"] = delta
    iat_in_range = VN_AMBIENT_TEMP_MIN <= iat <= VN_AMBIENT_TEMP_MAX
    ect_in_range = VN_AMBIENT_TEMP_MIN <= ect <= VN_AMBIENT_TEMP_MAX

    if delta <= E3_DELTA_NORMAL_MAX:
        masses = {frozenset({NORMAL}): 0.7}
        detail = (f"|IAT-ECT|={delta:.1f}C luc khoi dong nguoi (<= "
                   f"{E3_DELTA_NORMAL_MAX:.0f}C) - hai cam bien binh thuong.")
    elif delta > E3_DELTA_FAULT_MIN:
        if iat_in_range and not ect_in_range:
            masses = mass_from_deviation(ECT_SENSOR_FAULT, 0.20)
            detail = (f"|IAT-ECT|={delta:.1f}C, IAT={iat:.1f}C hop ly nhung "
                       f"ECT={ect:.1f}C ngoai dai moi truong VN "
                       f"({VN_AMBIENT_TEMP_MIN:.0f}-{VN_AMBIENT_TEMP_MAX:.0f}C) - nghi ECT loi.")
        elif ect_in_range and not iat_in_range:
            masses = mass_from_deviation(IAT_SENSOR_FAULT, 0.20)
            detail = (f"|IAT-ECT|={delta:.1f}C, ECT={ect:.1f}C hop ly nhung "
                       f"IAT={iat:.1f}C ngoai dai moi truong VN "
                       f"({VN_AMBIENT_TEMP_MIN:.0f}-{VN_AMBIENT_TEMP_MAX:.0f}C) - nghi IAT loi.")
        else:
            masses = {}
            _tinh_trang = "ca hai deu trong dai" if iat_in_range else "ca hai deu ngoai dai"
            detail = (f"|IAT-ECT|={delta:.1f}C lon nhung {_tinh_trang} moi truong hop ly - "
                       "khong xac dinh duoc cam bien nao loi, khong ket luan.")
    else:
        masses = {}
        detail = f"|IAT-ECT|={delta:.1f}C - vung giua ({E3_DELTA_NORMAL_MAX:.0f}-{E3_DELTA_FAULT_MIN:.0f}C), khong du ro."

    return Evidence(source="E3_iat_ect_coldstart", masses=masses, reliability=1.0, detail=detail, raw=raw)


# ---------------------------------------------------------------------
# E4 - Duong cong ham nong (muc 3.5) - van hang nhiet ket mo lam duong
# cong phang ra, lien quan ma OEM P0128 nhung ta phat hien som hon.
# ---------------------------------------------------------------------

def compute_warmup_features(t_ect_series: list) -> dict:
    """Tinh cac dac trung tu chuoi (t_sec, ect_c) ghi tu luc no may (t=0),
    da sap xep tang dan theo t_sec (muc 3.5):

        t_80        - thoi diem noi suy tuyen tinh dat 80C (None neu chua dat)
        slope_max   - do doc lon nhat (C/s) trong giai doan tang
        ect_plateau - trung vi ECT cua cac mau sau WARMUP_T80_MAX_SEC
                       (None neu chuoi chua du dai)

    Ham thuan, khong dung DB - de test bang chuoi gia (muc 12 buoc 6)."""
    if len(t_ect_series) < 2:
        return {"t_80": None, "slope_max": 0.0, "ect_plateau": None}

    t_80 = None
    slope_max = 0.0
    for i in range(len(t_ect_series) - 1):
        t0, e0 = t_ect_series[i]
        t1, e1 = t_ect_series[i + 1]
        if t_80 is None and e0 < 80.0 <= e1 and t1 > t0:
            frac = (80.0 - e0) / (e1 - e0)
            t_80 = t0 + frac * (t1 - t0)
        dt = t1 - t0
        if dt > 0:
            slope = (e1 - e0) / dt
            if slope > slope_max:
                slope_max = slope

    plateau_samples = [e for t, e in t_ect_series if t >= WARMUP_T80_MAX_SEC]
    ect_plateau = _median(plateau_samples) if plateau_samples else None

    return {"t_80": t_80, "slope_max": slope_max, "ect_plateau": ect_plateau}


def evaluate_e4(t_ect_series: list, iat_start: float = None) -> Evidence:
    """Bang chung E4 (muc 3.5). t_ect_series: list[(t_sec, ect_c)] tu luc
    no may, sap xep tang dan theo t_sec.

    iat_start CHUA duoc dung de so sanh gio - cam bay #7 (muc 13: 'chi so
    sanh cac lan co IAT chenh nhau duoi 5C') la trach nhiem cua noi HOC
    baseline warmup_t80 (chi dua vao baseline_samples nhung mau co
    iat_at_sample gan giong iat_start hien tai) - khong phai viec cua ham
    danh gia mot duong cong don le nay. iat_start duoc giu lai trong `raw`
    de ghi log/hien thi, chua dung de tinh toan o buoc nay."""
    features = compute_warmup_features(t_ect_series)
    raw = {"iat_start": iat_start, "n_samples": len(t_ect_series), **features}

    if len(t_ect_series) == 0:
        return Evidence(
            source="E4_warmup_curve", masses={}, reliability=0.0,
            detail="Chua co du lieu duong cong ham nong.", raw=raw,
        )

    t_80 = features["t_80"]
    last_t = t_ect_series[-1][0]

    if t_80 is not None and t_80 <= WARMUP_T80_MAX_SEC:
        masses = {frozenset({NORMAL}): 0.7}
        detail = (f"Dat 80C sau {t_80:.0f}s (<= {WARMUP_T80_MAX_SEC:.0f}s) "
                   "- duong cong ham nong binh thuong.")
    elif t_80 is None and last_t >= WARMUP_T80_MAX_SEC:
        masses = mass_from_deviation(THERMOSTAT_OPEN, 0.35)
        detail = (f"Sau {last_t:.0f}s van chua dat 80C (nguong "
                   f"{WARMUP_T80_MAX_SEC:.0f}s) - nghi van hang nhiet ket mo.")
    else:
        masses = {}
        detail = (f"Moi thu duoc {last_t:.0f}s du lieu, chua dat 80C va chua qua "
                   f"{WARMUP_T80_MAX_SEC:.0f}s - can them du lieu de ket luan.")

    return Evidence(source="E4_warmup_curve", masses=masses, reliability=1.0, detail=detail, raw=raw)