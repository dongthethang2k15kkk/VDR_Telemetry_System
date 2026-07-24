# diagnosis/dtc_map.py
#
# E6 - anh xa ma loi DTC sang Evidence (muc 3.7). Day la bang chung MANH
# NHAT khi co, nhung chi xuat hien khi da hong han. Bang dtc_logs da ton
# tai san trong DB nhung chua module nao doc - file nay lam viec do.

from .evidence import (
    Evidence, MAF_DEGRADED, INTAKE_LEAK, IAT_SENSOR_FAULT,
    ECT_SENSOR_FAULT, THERMOSTAT_OPEN,
)

# "Rat manh" = tran toi da 0.85 (muc 4.2). "Manh, khong phan biet" = 0.60,
# THAP HON vi P0171 tu no khong tach duoc MAF_DEGRADED khoi INTAKE_LEAK -
# viec phan biet la cua E1/E2, D-S se tu cong don khi hoi chan (muc 3.7).
_RAT_MANH = 0.85
_MANH_KHONG_PHAN_BIET = 0.60

# Ma -> (TAP HOP gia thuyet duoc cung co, do manh). Tap hop co the la MOT
# gia thuyet (P0100...) hoac NHIEU gia thuyet cung luc (P0171) - day la
# diem D-S xu ly tu nhien hon xac suat thuong: gan khoi luong cho ca TAP
# {MAF_DEGRADED, INTAKE_LEAK} thay vi phai chia doi cho tung phan tu.
DTC_MAP = {
    "P0100": (frozenset({MAF_DEGRADED}), _RAT_MANH),
    "P0101": (frozenset({MAF_DEGRADED}), _RAT_MANH),
    "P0102": (frozenset({MAF_DEGRADED}), _RAT_MANH),
    "P0103": (frozenset({MAF_DEGRADED}), _RAT_MANH),
    "P0104": (frozenset({MAF_DEGRADED}), _RAT_MANH),
    "P0171": (frozenset({MAF_DEGRADED, INTAKE_LEAK}), _MANH_KHONG_PHAN_BIET),
    "P0111": (frozenset({IAT_SENSOR_FAULT}), _RAT_MANH),
    "P0112": (frozenset({IAT_SENSOR_FAULT}), _RAT_MANH),
    "P0113": (frozenset({IAT_SENSOR_FAULT}), _RAT_MANH),
    "P0116": (frozenset({ECT_SENSOR_FAULT}), _RAT_MANH),
    "P0117": (frozenset({ECT_SENSOR_FAULT}), _RAT_MANH),
    "P0118": (frozenset({ECT_SENSOR_FAULT}), _RAT_MANH),
    "P0128": (frozenset({THERMOSTAT_OPEN}), _RAT_MANH),
}


def evaluate_e6(dtc_codes: list) -> list:
    """Bang chung E6 tu danh sach ma loi dang hoat dong, vd ["P0171","P0128"].

    Tra ve DANH SACH Evidence (khac E1-E5, moi lan CHI tra dung mot
    Evidence). Ly do: nhieu ma loi dong thoi la nhieu NGUON bang chung
    DOC LAP - neu don het vao MOT ham khoi luong duy nhat thi tong khoi
    luong de vuot qua 1.0 (vi du P0100 + P0128 cung luc se la 0.85+0.85
    > 1, vi pham bat bien cua Evidence). Tra ve list de fusion.combine_all()
    tu hoi chan tung cai, dung dung co che D-S da thiet ke, khong tu y
    gop truoc.

    Cac ma cung co CHUNG mot tap gia thuyet duoc GOP lai thanh MOT Evidence
    (lay do manh lon nhat trong so, khong nhan doi vi hai ma cung noi mot
    dieu khong nen duoc tin gap doi).

    Ma khong nam trong DTC_MAP bi bo qua lang le (du an co the co ma khac
    ngoai pham vi bay gia thuyet dang xet). Danh sach rong hoac toan ma la
    -> list chi co mot Evidence rong (Theta)."""
    raw_codes = list(dtc_codes) if dtc_codes else []

    grouped_strength = {}     # hyp_set -> do manh lon nhat
    grouped_codes = {}        # hyp_set -> danh sach ma khop

    for code in raw_codes:
        entry = DTC_MAP.get(code)
        if entry is None:
            continue
        hyp_set, strength = entry
        if strength > grouped_strength.get(hyp_set, 0.0):
            grouped_strength[hyp_set] = strength
        grouped_codes.setdefault(hyp_set, []).append(code)

    if not grouped_strength:
        return [Evidence(
            source="E6_dtc", masses={}, reliability=0.0,
            detail="Khong co ma loi nao trong danh sach anh xa toi bay gia thuyet dang xet.",
            raw={"dtc_codes": raw_codes},
        )]

    out = []
    for hyp_set, strength in grouped_strength.items():
        codes = grouped_codes[hyp_set]
        ten = " hoac ".join(sorted(hyp_set))
        out.append(Evidence(
            source=f"E6_dtc_{'_'.join(codes)}",
            masses={hyp_set: strength},
            reliability=1.0,
            detail=f"Ma loi {codes} cung co {ten} (do manh {strength:.2f}).",
            raw={"dtc_codes": codes, "hypothesis_set": sorted(hyp_set)},
        ))
    return out