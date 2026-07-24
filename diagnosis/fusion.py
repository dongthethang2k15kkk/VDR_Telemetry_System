# diagnosis/fusion.py
#
# Ket hop nhieu Evidence thanh mot chan doan xep hang. Muc 4.4-4.5 ban giao.
#
# Chi lam viec voi "masses" (dict frozenset -> float) da duoc chuan hoa va
# chiet khau boi Evidence - file nay khong biet gi ve cam bien hay vat ly,
# dung nguyen tac ranh gioi cua muc 4.1.

from .evidence import THETA, HYPOTHESES

# He so xung dot K >= nguong nay -> chuyen tu Dempster sang Yager (muc 4.4).
CONFLICT_K_THRESHOLD = 0.75

_MASS_EPS = 1e-9

KET_LUAN = "KET_LUAN"
NGHI_NGO = "NGHI_NGO"
KHONG_KET_LUAN = "KHONG_KET_LUAN"


def combine_two(m1: dict, m2: dict):
    """Ket hop hai ham khoi luong bang quy tac Dempster hoac Yager (muc 4.4).

    m1, m2: dict frozenset -> float, moi dict tong = 1.0 (da chiet khau tu
            Evidence.discounted() truoc khi goi ham nay).

    Tra ve (masses_moi, K) voi K la he so xung dot CUA LAN COMBINE NAY
    (tong tich khoi luong ma giao nhau = tap rong).

        K < CONFLICT_K_THRESHOLD  -> Dempster: chuan hoa chia cho (1-K)
        K >= CONFLICT_K_THRESHOLD -> Yager: khong chuan hoa, don K vao THETA
                                     (day la cach xu ly nghich ly Zadeh, muc 2.3)

    Truong hop K ~= 1.0 (xung dot tuyet doi, khong con phan giao nao) khong
    the chuan hoa duoc du la Dempster hay Yager kieu thong thuong - tra ve
    toan bo ve THETA, coi nhu "hai nguon noi hai chuyen khac han nhau,
    khong ket luan duoc gi".
    """
    combined = {}
    conflict = 0.0

    for a, ma in m1.items():
        for b, mb in m2.items():
            prod = ma * mb
            inter = a & b
            if not inter:
                conflict += prod
            else:
                combined[inter] = combined.get(inter, 0.0) + prod

    # Kep K vao [0,1] de trach sai so dau phay dong gay K=1.0000000002
    conflict = max(0.0, min(1.0, conflict))

    if conflict >= 1.0 - _MASS_EPS:
        return {THETA: 1.0}, conflict

    if conflict < CONFLICT_K_THRESHOLD:
        norm = 1.0 - conflict
        out = {k: v / norm for k, v in combined.items()}
    else:
        out = dict(combined)
        out[THETA] = out.get(THETA, 0.0) + conflict

    return out, conflict


def combine_all(evidences: list):
    """Ket hop tuan tu danh sach doi tuong Evidence (goi .discounted() cho
    tung cai). Tra ve (masses_cuoi_cung, K_cuoi_cung).

    Danh sach rong  -> ({THETA: 1.0}, 0.0)   (khong co gi de noi -> khong biet)
    Mot phan tu      -> tra dung masses da chiet khau cua no, K=0.0
                        (chi co mot nguon thi khong co gi de xung dot).
    """
    if not evidences:
        return {THETA: 1.0}, 0.0

    acc = evidences[0].discounted()
    if len(evidences) == 1:
        return acc, 0.0

    k = 0.0
    for e in evidences[1:]:
        acc, k = combine_two(acc, e.discounted())
    return acc, k


def belief(masses: dict, hypothesis) -> float:
    """Bel(H) = tong khoi luong cua moi tap con cua H (muc 4.5).
    hypothesis: mot ma gia thuyet (str) hoac frozenset san co."""
    h = frozenset({hypothesis}) if isinstance(hypothesis, str) else hypothesis
    return sum(m for a, m in masses.items() if a.issubset(h))


def plausibility(masses: dict, hypothesis) -> float:
    """Pl(H) = tong khoi luong cua moi tap giao voi H (muc 4.5).
    Luon co Bel(H) <= Pl(H) - day la bat bien toan hoc cua ly thuyet."""
    h = frozenset({hypothesis}) if isinstance(hypothesis, str) else hypothesis
    return sum(m for a, m in masses.items() if a & h)


def rank_hypotheses(masses: dict) -> list:
    """Xep hang tung gia thuyet DON (khong phai tap hop) theo Bel giam dan.
    Tra ve list[(hypothesis, bel, pl)], luon co du HYPOTHESES phan tu
    (kha nang Bel=Pl=0 neu gia thuyet do khong duoc bang chung nao nhac toi)."""
    ranked = [(h, belief(masses, h), plausibility(masses, h)) for h in HYPOTHESES]
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def decide(masses: dict) -> dict:
    """Quy tac cong bo (muc 4.5):

        Bel(top) >= 0.60 va Bel(top) - Bel(thu_hai) >= 0.20  -> KET_LUAN
        Bel(top) >= 0.35                                      -> NGHI_NGO
        nguoc lai                                             -> KHONG_KET_LUAN

    Tra ve dict day du de UI/log dung truc tiep, khong can tinh lai."""
    ranked = rank_hypotheses(masses)
    top_h, top_bel, top_pl = ranked[0]
    second_h, second_bel, _ = ranked[1] if len(ranked) > 1 else (None, 0.0, 0.0)

    if top_bel >= 0.60 and (top_bel - second_bel) >= 0.20:
        decision = KET_LUAN
    elif top_bel >= 0.35:
        decision = NGHI_NGO
    else:
        decision = KHONG_KET_LUAN

    return {
        "decision": decision,
        "top_hypothesis": top_h,
        "belief": top_bel,
        "plausibility": top_pl,
        "second_hypothesis": second_h,
        "second_belief": second_bel,
    }
