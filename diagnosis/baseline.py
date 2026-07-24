# diagnosis/baseline.py
#
# Hoc dac tinh rieng cua CHIEC XE NAY cho tung dai luong (metric), dung
# trung vi (median) va MAD thay vi trung binh/do lech chuan - ben voi
# ngoai le (mot lan do loi giac la du lam hong trung binh). Muc 5.2-5.4.
#
# Bang baseline_samples (da tao san trong DB, xem BANGIAO_CHAN_DOAN_HOP_NHAT.md
# muc 9) KHONG co cot vehicle_id - vi rang buoc thi nghiem la MOT xe co dinh
# (xem muc 14 cau hoi 1, da chot). Khi phat lai dataset nhieu xe (buoc 13),
# dung mot file .db rieng cho moi xe, khong nhet vehicle_id vao day.

import time

CHUA_DU = "CHUA_DU"
DANG_HOC = "DANG_HOC"
DU = "DU"

N_TOI_THIEU = 3     # duoi nguong nay: khong dua ra bang chung nao, alpha=0
N_DU = 10           # tu nguong nay: coi la du, alpha=1.0 (muc 4.3, 5.4)

# He so quy MAD ve thang tuong duong do lech chuan cho phan phoi chuan (muc 5.3)
MAD_TO_STD = 1.4826


def _median(vals):
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _mad(vals, med):
    """Median Absolute Deviation - trung vi cua |x - trung vi|."""
    return _median([abs(v - med) for v in vals])


def _status_and_alpha(n: int):
    """Trang thai hoc va alpha chiet khau (muc 5.4). alpha o day la con so
    se duoc gan cho Evidence.reliability khi mot module E1..E4 dung baseline
    nay de tao bang chung."""
    if n < N_TOI_THIEU:
        return CHUA_DU, 0.0
    if n < N_DU:
        return DANG_HOC, n / float(N_DU)
    return DU, 1.0


class BaselineState:
    """Ket qua hoc baseline cho MOT metric (vd 'maf_per_rpm_idle').
    Object bat bien - moi lan hoc lai tao object moi, khong sua tai cho."""

    __slots__ = ("metric", "n", "median", "mad", "status", "alpha")

    def __init__(self, metric, n, median, mad, status, alpha):
        self.metric = metric
        self.n = n
        self.median = median
        self.mad = mad
        self.status = status
        self.alpha = alpha

    def __repr__(self):
        return (f"BaselineState(metric={self.metric!r}, n={self.n}, "
                f"median={self.median}, mad={self.mad}, status={self.status}, "
                f"alpha={self.alpha:.2f})")

    def deviation_sigma(self, x: float) -> float:
        """nguong_lech = |x - median| / (1.4826 x MAD) - so 'sigma' tuong
        duong, dung de so sanh voi nguong dang do lech chuan quen thuoc.

        MAD=0 (moi mau giong het nhau, hay gap khi it mau) khong chia duoc -
        quy uoc: x trung khop median -> lech 0; x khac di, du chi mot chut,
        -> lech vo cuc (bao hieu ro rang la bat thuong, khong lam tron thanh
        mot con so nho gay hieu lam la 'binh thuong')."""
        if self.n == 0 or self.median is None:
            raise ValueError(f"Chua co mau nao cho metric '{self.metric}'")
        if self.mad <= 0:
            return 0.0 if x == self.median else float("inf")
        return abs(x - self.median) / (MAD_TO_STD * self.mad)

    def deviation_ratio(self, x: float) -> float:
        """Do lech TY LE so voi median: |x-median|/|median|, dung lam dau
        vao cho evidence.mass_from_deviation() (thang 0..1+), khac voi
        deviation_sigma() la so lan MAD. Modules E1..E4 chon cai nao hop ly
        hon theo tung boi canh (mass_from_deviation can thang [0,1])."""
        if self.n == 0 or self.median is None:
            raise ValueError(f"Chua co mau nao cho metric '{self.metric}'")
        if self.median == 0:
            return 0.0 if x == 0 else float("inf")
        return abs(x - self.median) / abs(self.median)


def learn_from_values(metric: str, values: list) -> BaselineState:
    """Ham thuan: tinh median/MAD/trang thai tu danh sach gia tri tho,
    KHONG dung DB. Day la ham duoc unit-test truc tiep bang mau gia
    (muc 12 buoc 4: 'Bom mau gia, kiem tra trung vi/MAD')."""
    n = len(values)
    status, alpha = _status_and_alpha(n)
    if n == 0:
        return BaselineState(metric, 0, None, None, status, alpha)
    med = _median(values)
    mad = _mad(values, med)
    return BaselineState(metric, n, med, mad, status, alpha)


def record_sample(cursor, metric: str, value: float,
                   ect: float = None, iat: float = None,
                   created_at: float = None) -> None:
    """Ghi mot mau baseline tho vao bang baseline_samples (muc 5.3).
    KHONG tinh median/MAD o day - viec do thuoc ve learn_from_db(), goi
    lazily khi can chan doan, tranh tinh lai moi lan ghi (ghi thuong xuyen
    hon doc rat nhieu).

    Nguoi goi (E1..E4 trong plausibility.py) chiu trach nhiem CHI goi ham
    nay khi da thoa dieu kien thu mau (vd may am, ECT>80 - muc 5.2) - ham
    nay khong tu loc gi ca, ghi thang nhung gi duoc dua vao."""
    ts = created_at if created_at is not None else time.time()
    cursor.execute(
        "INSERT INTO baseline_samples "
        "(metric, value, ect_at_sample, iat_at_sample, created_at) "
        "VALUES (?,?,?,?,?)",
        (metric, value, ect, iat, ts),
    )


def _fetch_values(cursor, metric: str) -> list:
    rows = cursor.execute(
        "SELECT value FROM baseline_samples WHERE metric = ? AND value IS NOT NULL",
        (metric,),
    ).fetchall()
    return [r[0] for r in rows]


def learn_from_db(cursor, metric: str) -> BaselineState:
    """Doc toan bo mau tho cua metric tu baseline_samples roi hoc.
    O(n log n) moi lan goi (sap xep lai tu dau) - voi vai chuc mau (muc 5.3
    noi ro 'voi vai chuc mau thi chi phi khong dang ke') thi khong can toi
    uu incremental, danh doi lay code don gian de kiem chung."""
    values = _fetch_values(cursor, metric)
    return learn_from_values(metric, values)