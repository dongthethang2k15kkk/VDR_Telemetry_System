# diagnosis/evidence.py
#
# Khung nhan thuc (frame of discernment) va cau truc Evidence dung chung cho
# toan bo he thong hoi chan Dempster-Shafer.
# Tham chieu: BANGIAO_CHAN_DOAN_HOP_NHAT.md muc 3.1 (khung nhan thuc),
# muc 4.1 (Evidence), muc 4.2 (do lech -> khoi luong), muc 4.3 (chiet khau).
#
# File nay CHUA lam gi ve vat ly cam bien hay Dempster combine - do la viec
# cua plausibility.py va fusion.py (buoc 2 va 5 trong bang trien khai).

from dataclasses import dataclass, field

# ---- Khung nhan thuc - bay gia thuyet, khong mo rong them (muc 3.1) ----
NORMAL = "NORMAL"
MAF_DEGRADED = "MAF_DEGRADED"
INTAKE_LEAK = "INTAKE_LEAK"
THERMOSTAT_OPEN = "THERMOSTAT_OPEN"
ECT_SENSOR_FAULT = "ECT_SENSOR_FAULT"
IAT_SENSOR_FAULT = "IAT_SENSOR_FAULT"
BUS_LINK_FAULT = "BUS_LINK_FAULT"

HYPOTHESES = (
    NORMAL,
    MAF_DEGRADED,
    INTAKE_LEAK,
    THERMOSTAT_OPEN,
    ECT_SENSOR_FAULT,
    IAT_SENSOR_FAULT,
    BUS_LINK_FAULT,
)

# Theta = toan tap = "khong biet". Day la khoa dung moi khi can gan khoi
# luong con lai trong Dempster-Shafer. La mot frozenset nen dung duoc lam
# key trong dict masses giong moi tap con khac.
THETA = frozenset(HYPOTHESES)

_MASS_EPS = 1e-9

# Tran khoi luong cho mot gia thuyet don le - khong nguon nao duoc chac
# chan tuyet doi (muc 4.2, chu thich "Tran 0.85 la co y").
MASS_CAP_SINGLE_SOURCE = 0.85


def _normalize_masses(masses: dict) -> dict:
    """Cuong che bat bien: tong masses.values() == 1.0 (sai so _MASS_EPS).
    Phan con thieu (neu tong < 1) duoc don vao THETA - dung nguyen tac
    "khong bao gio gan het khoi luong cho mot gia thuyet" (muc 4.2, cam bay #1).

    Khong sua doi dict truyen vao, tra ve dict moi. Bo qua cac muc co
    khoi luong <= 0 (khong dong gop gi, giu dict gon).

    Nem ValueError neu tong < 0 hoac tong > 1 (vuot qua nghia la loi logic
    o noi goi ham, khong phai thu de "chuan hoa lai" bang cach chia ty le -
    che dau loi o day se an mat bug that su).
    """
    total = sum(masses.values())

    if total < -_MASS_EPS:
        raise ValueError(f"Tong khoi luong am ({total}): {masses}")

    if total > 1.0 + _MASS_EPS:
        raise ValueError(f"Tong khoi luong vuot qua 1.0 ({total}): {masses}")

    out = {k: v for k, v in masses.items() if v > _MASS_EPS}

    if abs(total - 1.0) <= _MASS_EPS:
        return out

    missing = 1.0 - total
    out[THETA] = out.get(THETA, 0.0) + missing
    return out


@dataclass
class Evidence:
    """Mot mau bang chung tu mot nguon (E1..E7). Xem muc 4.1.

    source:       ten nguon, vd "E1_maf_rpm", "E6_dtc" - dung de truy vet
                   trong evidence_json luc luu diagnosis_results.
    masses:        dict frozenset(tap con HYPOTHESES) -> khoi luong.
                   Vd frozenset({MAF_DEGRADED}): 0.6,
                      frozenset({MAF_DEGRADED, INTAKE_LEAK}): 0.2  (nhu P0171,
                      muc 3.7 - mot ma loi cung co hai gia thuyet cung luc).
                   Duoc _normalize_masses() cuong che tong = 1 sau __post_init__.
    reliability:   alpha trong [0,1] - do tin cay nguon, dung khi chiet khau
                   (muc 4.3). Mac dinh 1.0 (khong chiet khau) cho nguon nao
                   chua gan voi trang thai baseline (vd E6 DTC luon tin cay).
    detail:        cau giai thich tieng Viet cho nguoi doc / UI.
    raw:           so lieu tho (vd {"maf_deviation": 0.42, "n_samples": 7})
                   de hien thi debug / UI, khong dung trong tinh toan combine.
    """

    source: str
    masses: dict
    reliability: float = 1.0
    detail: str = ""
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        for key in self.masses:
            if not isinstance(key, frozenset):
                raise TypeError(
                    f"Khoa masses phai la frozenset, gap {type(key).__name__}: {key!r}"
                )
            if not key or not key.issubset(THETA):
                raise ValueError(
                    f"Gia thuyet {key} rong hoac khong nam trong THETA={THETA}"
                )
        if not (0.0 <= self.reliability <= 1.0):
            raise ValueError(f"reliability phai trong [0,1], gap {self.reliability}")
        # Luu ban chuan hoa - dam bao bat bien dung trong suot vong doi object
        self.masses = _normalize_masses(self.masses)

    def discounted(self) -> dict:
        """Ap dung chiet khau Shafer theo do tin cay nguon (muc 4.3):

            m'(A)     = alpha * m(A)                voi moi A != THETA
            m'(THETA) = alpha * m(THETA) + (1 - alpha)

        alpha = 0  -> toan bo khoi luong don ve THETA (nguon khong dang tin
                      chut nao, tuong duong "khong noi gi ca"). Day la mot
                      trong bon test bat buoc cua Tang A (muc 11).
        alpha = 1  -> giu nguyen masses, khong chiet khau.

        Tra ve dict moi (khong sua self.masses).
        """
        alpha = self.reliability
        out = {}
        theta_mass = self.masses.get(THETA, 0.0)
        for hyp, m in self.masses.items():
            if hyp == THETA:
                continue
            out[hyp] = alpha * m
        out[THETA] = alpha * theta_mass + (1.0 - alpha)
        return out


def mass_from_deviation(hypothesis: str, deviation: float) -> dict:
    """Ham doc tuyen tinh chuyen do lech chuan hoa (0..1) thanh khoi luong
    cho MOT gia thuyet don, theo muc 4.2. Luon chua khoi luong con lai cho
    THETA (qua _normalize_masses o Evidence.__post_init__, ham nay chi tra
    ve phan m(H), khong tu them THETA vao).

        d < 0.10          -> nghieng ve NORMAL: m(NORMAL) = 0.7
        0.10 <= d < 0.30  -> m(H) = 0.4
        d >= 0.30         -> m(H) = min(0.85, 0.4 + d)

    hypothesis: mot trong cac hang so HYPOTHESES (vd MAF_DEGRADED).
    deviation:  gia tri thuc te, se duoc kep vao [0, 1] truoc khi tra loi -
                nguoi goi (E1..E4) chiu trach nhiem tinh do lech chuan hoa
                dung don vi cua tin hieu do.
    """
    if hypothesis not in HYPOTHESES:
        raise ValueError(f"hypothesis '{hypothesis}' khong nam trong HYPOTHESES")

    d = max(0.0, min(1.0, deviation))

    if d < 0.10:
        return {frozenset({NORMAL}): 0.7}
    elif d < 0.30:
        return {frozenset({hypothesis}): 0.4}
    else:
        m = min(MASS_CAP_SINGLE_SOURCE, 0.4 + d)
        return {frozenset({hypothesis}): m}
