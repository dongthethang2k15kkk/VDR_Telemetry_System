# diagnosis/adapters.py
#
# E5 va E7 - boc lai HealthMonitor va TrendAnalyzer SAN CO trong
# obd_module/rule_engine.py thanh Evidence. KHONG sua logic ben trong hai
# class do (muc 3.6: "Khong viet lai HealthMonitor"; muc 3.8: "Giu nguyen
# Mann-Kendall + Sen's slope"). File nay chi la lop vo quy doi ket qua.

from .evidence import (
    Evidence, MAF_DEGRADED, ECT_SENSOR_FAULT, IAT_SENSOR_FAULT,
    BUS_LINK_FAULT, mass_from_deviation,
)

# ---------------------------------------------------------------------
# E5 - Suc khoe PID (muc 3.6), tan dung HealthMonitor san co.
# ---------------------------------------------------------------------

# PID -> gia thuyet TUONG UNG trong khung nhan thuc, CHI cho cac PID la
# CAM BIEN VAT LY that su. 0x07 (LTFT) la gia tri ECU TINH RA, khong phai
# cam bien - khong co gia thuyet "LTFT hong" trong khung. RPM/speed/
# throttle cung vay - mat tin hieu cac PID nay khong anh xa duoc toi gia
# thuyet nao trong bay gia thuyet dang xet.
_PID_TO_HYPOTHESIS = {
    0x10: MAF_DEGRADED,      # MAF
    0x05: ECT_SENSOR_FAULT,  # Coolant/ECT
    0x0F: IAT_SENSOR_FAULT,  # IAT
}


def evaluate_e5(flagged_pids: list) -> Evidence:
    """Bang chung E5 (muc 3.6). flagged_pids: danh sach ma PID (int, vd
    0x10) dang bi HealthMonitor bao mien-rate cao - nguoi goi truy van
    ket qua HealthMonitor.analyze() hoac bang pid_health roi truyen vao,
    KHONG doc DB truc tiep o day (giu ham thuan de test duoc bang du lieu gia).

        >= 2 PID cung luc mat tin hieu -> BUS_LINK_FAULT (giac/cap chung)
        dung 1 PID, PID do co gia thuyet tuong ung -> nghieng gia thuyet do
        dung 1 PID nhung khong co gia thuyet tuong ung (vd LTFT, RPM)
            -> Theta (khung khong co cho de gan)
        0 PID -> Theta

    Do tin cay (d dua vao mass_from_deviation): mat NHIEU PID dong thoi la
    dau hieu ro rang hon (d=0.35 -> m=0.75) so voi mat MOT PID rieng le
    (d=0.20 -> m=0.4, con nhieu nguyen nhan khac co the giai thich)."""
    raw = {"flagged_pids": [hex(p) for p in flagged_pids] if flagged_pids else []}
    n = len(flagged_pids) if flagged_pids else 0

    if n >= 2:
        masses = mass_from_deviation(BUS_LINK_FAULT, 0.35)
        detail = (f"{n} PID cung mat goi/do tre cao dong thoi ({raw['flagged_pids']}) "
                   "- nghi giac/cap chung (BUS_LINK_FAULT).")
        return Evidence(source="E5_pid_health", masses=masses, reliability=1.0, detail=detail, raw=raw)

    if n == 1:
        pid = flagged_pids[0]
        hyp = _PID_TO_HYPOTHESIS.get(pid)
        if hyp is not None:
            masses = mass_from_deviation(hyp, 0.20)
            detail = f"PID {hex(pid)} mat goi/do tre cao rieng le - nghi mach cam bien nay."
            return Evidence(source="E5_pid_health", masses=masses, reliability=1.0, detail=detail, raw=raw)
        detail = f"PID {hex(pid)} mat goi/do tre cao nhung khong co gia thuyet tuong ung trong khung."
        return Evidence(source="E5_pid_health", masses={}, reliability=0.0, detail=detail, raw=raw)

    return Evidence(source="E5_pid_health", masses={}, reliability=0.0,
                     detail="Khong co PID nao dang bi bao suc khoe kem.", raw=raw)


# ---------------------------------------------------------------------
# E7 - Xu huong (muc 3.8), tan dung TrendAnalyzer san co.
# ---------------------------------------------------------------------

# Anh xa cot cua TrendAnalyzer._TARGETS hien tai -> gia thuyet trong khung
# nhan thuc. CHI anh xa duoc ltft_avg (LTFT tang keo dai cung huong voi
# E1 - D-S se tu cong don khi hoi chan). coolant_avg (qua nhiet) KHONG co
# gia thuyet tuong ung trong bay gia thuyet dang xet - alert cua no VAN
# duoc TrendAnalyzer tu ghi vao maintenance_logs nhu cu, chi KHONG dua
# vao hoi chan D-S o day.
_TREND_COL_TO_HYPOTHESIS = {
    "ltft_avg": MAF_DEGRADED,
}


def evaluate_e7(trend_alerts: list) -> list:
    """Bang chung E7 (muc 3.8). trend_alerts: KET QUA TRUC TIEP cua
    TrendAnalyzer().analyze(cursor) - khong doi gi ben trong class do.

    Tra ve DANH SACH Evidence (co the rong neu khong co alert nao anh
    xa duoc), vi mot lan goi analyze() co the tra ve nhieu alert cung luc
    (vd ca ltft_avg lan coolant_avg cung dang tang).

    GHI CHU MO RONG CHUA LAM (muc 3.8 de nghi mo rong _TARGETS de dung
    maf_avg/rpm_avg dang bo phi): TrendAnalyzer hien CHI bat 'xu huong
    TANG co y nghia thong ke toi mot nguong co san (THRESHOLD_LTFT_CRITICAL,
    THRESHOLD_COOLANT_CRITICAL)'. MAF suy giam thi trieu chung chinh la
    MAF LECH khoi baseline (da co E1 lam roi, ham quan diem khac), khong
    han la xu huong tang don dieu theo thoi gian - can thiet ke nguong
    rieng truoc khi nhet vao _TARGETS, khong ep vao khuon cu de tranh
    bia dat con so khong co co so. De lai cho buoc sau neu con thoi gian."""
    out = []
    for alert in (trend_alerts or []):
        col = alert.get("pid_name")
        hyp = _TREND_COL_TO_HYPOTHESIS.get(col)
        if hyp is None:
            continue
        # 'critical' (gan nguong hon, theo TrendAnalyzer.analyze()) tin
        # cay hon 'warning' - vay se lech mass_from_deviation cao hon.
        # d=0.25 -> tang [0.10,0.30) -> m=0.4 (trung binh). d=0.45 -> tang
        # >=0.30 -> m=min(0.85, 0.4+0.45)=0.85 (tran, tin cay cao).
        d = 0.45 if alert.get("severity") == "critical" else 0.25
        masses = mass_from_deviation(hyp, d)
        out.append(Evidence(
            source="E7_trend", masses=masses, reliability=1.0,
            detail=alert.get("description", f"Xu huong tang co y nghia o {col}."),
            raw=dict(alert),
        ))
    return out