# diagnosis/test_fusion.py
#
# Script tu chay, khong can pytest. Verify: python3 diagnosis/test_fusion.py
#
# Tang A (muc 11) - hai test bat buoc con lai cho fusion:
#   - Quy tac Dempster tren vi du kinh dien cho ket qua da biet
#   - Nghich ly Zadeh: dung dung vi du xung dot cao, xac nhan he thong
#     chuyen sang Yager thay vi cho ket qua vo ly

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from diagnosis.evidence import (
    Evidence, THETA, NORMAL, MAF_DEGRADED, INTAKE_LEAK, BUS_LINK_FAULT,
)
from diagnosis.fusion import (
    combine_two, combine_all, belief, plausibility, rank_hypotheses, decide,
    KET_LUAN, NGHI_NGO, KHONG_KET_LUAN, CONFLICT_K_THRESHOLD,
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


A = frozenset({MAF_DEGRADED})
B = frozenset({INTAKE_LEAK})
C = frozenset({BUS_LINK_FAULT})


# ---------------------------------------------------------------------
# 1. Vi du kinh dien (tinh tay doc lap) - hai nguon it xung dot
#
#   m1(A)=0.6, m1(Theta)=0.4
#   m2(A)=0.3, m2(B)=0.5, m2(Theta)=0.2
#
#   Cap co giao rong:  A-B (0.6*0.5=0.30)  ->  K = 0.30
#   Cac cap con lai:
#     A&A=A:      0.6*0.3=0.18
#     A&Theta=A:  0.6*0.2=0.12
#     Theta&A=A:  0.4*0.3=0.12
#     Theta&B=B:  0.4*0.5=0.20
#     Theta&Theta=Theta: 0.4*0.2=0.08
#   Chuan hoa chia (1-K)=0.7:
#     m(A) = (0.18+0.12+0.12)/0.7 = 0.42/0.7 = 0.6
#     m(B) = 0.20/0.7 = 0.285714...
#     m(Theta) = 0.08/0.7 = 0.114286...
# ---------------------------------------------------------------------

m1 = {A: 0.6, THETA: 0.4}
m2 = {A: 0.3, B: 0.5, THETA: 0.2}
result, k = combine_two(m1, m2)

check("Dempster kinh dien: K = 0.30", approx(k, 0.30))
check("Dempster kinh dien: m(A) = 0.6", approx(result[A], 0.6))
check("Dempster kinh dien: m(B) = 0.285714", approx(result[B], 0.285714, eps=1e-5))
check("Dempster kinh dien: m(Theta) = 0.114286", approx(result[THETA], 0.114286, eps=1e-5))
check("Dempster kinh dien: tong = 1", approx(sum(result.values()), 1.0))
check("Dempster kinh dien: K < nguong -> khong chuyen Yager", k < CONFLICT_K_THRESHOLD)


# ---------------------------------------------------------------------
# 2. Nghich ly Zadeh (muc 2.3 + 4.4) - hai nhan chung gan nhu doi lap
#
#   Nhan chung 1: m1(A)=0.99, m1(C)=0.01
#   Nhan chung 2: m2(B)=0.99, m2(C)=0.01
#
#   Dempster GOC (khong xu ly) se cho m(C)=1.0 - phi ly, vi ca hai nhan
#   chung deu gan cho C khoi luong RAT NHO (1%), the ma C lai thanh ket
#   luan CHAC CHAN TUYET DOI. He thong cua ta phai tranh duoc dieu nay.
# ---------------------------------------------------------------------

zw1 = {A: 0.99, C: 0.01}
zw2 = {B: 0.99, C: 0.01}
zresult, zk = combine_two(zw1, zw2)

check("Zadeh: K rat cao (>= 0.75)", zk >= CONFLICT_K_THRESHOLD)
check("Zadeh: he thong chuyen sang Yager (khong chuan hoa)", approx(sum(zresult.values()), 1.0))
check(
    "Zadeh: KHONG roi vao nghich ly - m(C) van rat nho, khong bi day len ~1.0",
    zresult.get(C, 0.0) < 0.05,
)
check(
    "Zadeh: phan lon khoi luong don ve THETA (he thong tu nhan 'khong biet')",
    zresult[THETA] > 0.9,
)
# Doi chieu: neu KHONG chuyen Yager ma cu dung Dempster chuan hoa binh
# thuong thi se rai dung vao nghich ly - xac nhan dieu nay that su xay ra
# neu bo qua buoc kiem tra K (de chung minh vi sao buoc kiem tra la bat buoc).
_naive_norm = 1.0 - zk
_naive_mC = 0.01 * 0.01 / _naive_norm
check(
    "Zadeh: (doi chieu) Dempster khong xu ly se cho m(C) gan 1.0 - dung la nghich ly",
    _naive_mC > 0.9,
)


# ---------------------------------------------------------------------
# 3. combine_all: danh sach rong / mot phan tu / nhieu phan tu
# ---------------------------------------------------------------------

masses_empty, k_empty = combine_all([])
check("combine_all: danh sach rong -> toan bo THETA", approx(masses_empty[THETA], 1.0))
check("combine_all: danh sach rong -> K=0", approx(k_empty, 0.0))

e_single = Evidence(source="E1", masses={A: 0.6}, reliability=1.0)
masses_single, k_single = combine_all([e_single])
check(
    "combine_all: mot phan tu -> giu nguyen masses da chiet khau",
    approx(masses_single[A], 0.6) and approx(masses_single[THETA], 0.4),
)
check("combine_all: mot phan tu -> K=0", approx(k_single, 0.0))

e_a = Evidence(source="E1", masses={A: 0.6}, reliability=1.0)
e_b = Evidence(source="E2", masses={B: 0.5, A: 0.3}, reliability=1.0)
masses_ab, k_ab = combine_all([e_a, e_b])
check(
    "combine_all: hai phan tu khop voi combine_two goi truc tiep",
    approx(masses_ab[A], result[A]) and approx(masses_ab[THETA], result[THETA]),
)


# ---------------------------------------------------------------------
# 4. Bel/Pl: bat bien Bel(H) <= Pl(H), va truong hop bang chung ro rang
# ---------------------------------------------------------------------

masses_clear = {A: 0.7, THETA: 0.3}
bel_a = belief(masses_clear, MAF_DEGRADED)
pl_a = plausibility(masses_clear, MAF_DEGRADED)
check("Bel/Pl: Bel(A) = 0.7 (chi co A moi la tap con cua A)", approx(bel_a, 0.7))
check("Bel/Pl: Pl(A) = 1.0 (A va Theta deu giao voi A)", approx(pl_a, 1.0))
check("Bel/Pl: bat bien Bel <= Pl", bel_a <= pl_a + 1e-9)

bel_b = belief(masses_clear, INTAKE_LEAK)
pl_b = plausibility(masses_clear, INTAKE_LEAK)
check("Bel/Pl: gia thuyet khong duoc nhac toi -> Bel=0", approx(bel_b, 0.0))
check("Bel/Pl: nhung Pl > 0 vi Theta van giao voi no", pl_b > 0.0)

ranked = rank_hypotheses(masses_clear)
check("rank_hypotheses: du 7 gia thuyet", len(ranked) == 7)
check("rank_hypotheses: dung dau la MAF_DEGRADED", ranked[0][0] == MAF_DEGRADED)
check(
    "rank_hypotheses: sap giam dan theo Bel",
    all(ranked[i][1] >= ranked[i + 1][1] for i in range(len(ranked) - 1)),
)


# ---------------------------------------------------------------------
# 5. decide(): ba nhanh cua quy tac cong bo
# ---------------------------------------------------------------------

d1 = decide({A: 0.85, THETA: 0.15})
check("decide: Bel cao + cach biet lon -> KET_LUAN", d1["decision"] == KET_LUAN)
check("decide: KET_LUAN tra dung top_hypothesis", d1["top_hypothesis"] == MAF_DEGRADED)

d2 = decide({A: 0.45, B: 0.20, THETA: 0.35})
check("decide: Bel vua, cach biet khong du -> NGHI_NGO", d2["decision"] == NGHI_NGO)

d3 = decide({A: 0.20, B: 0.15, THETA: 0.65})
check("decide: Bel thap -> KHONG_KET_LUAN", d3["decision"] == KHONG_KET_LUAN)

# Bat bien toan hoc: voi phan phoi hop le (tong khoi luong = 1, khong am),
# Bel(top) >= 0.60 LUON keo theo khoang cach >= 0.20 voi hang hai - vi phan
# con lai cho moi gia thuyet khac toi da chi con 0.40. Dieu kien thu hai
# trong decide() la luoi an toan cho truong hop khong chuan (vd sau Yager
# hoac input tu ben ngoai khong qua Evidence), khong bao gio la nut that
# that su voi masses hop le. Test o bien 0.60 dung dan xac nhan dieu do.
d4 = decide({A: 0.61, B: 0.39})
check(
    "decide: bat bien - Bel(top)=0.61 voi phan phoi hop le tu dong dat KET_LUAN",
    d4["decision"] == KET_LUAN,
)

d5 = decide({THETA: 1.0})
check("decide: hoan toan khong biet -> KHONG_KET_LUAN", d5["decision"] == KHONG_KET_LUAN)


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
