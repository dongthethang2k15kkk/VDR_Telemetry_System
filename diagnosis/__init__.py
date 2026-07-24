# diagnosis/__init__.py
#
# Diem vao cong khai cua package - run_diagnosis() la "MOT cho goi" ma
# rule_engine.py se dung (muc 10: "rule_engine.py chi them mot cho goi
# vao diagnosis, khong sua logic cu"). Moi thu phuc tap (bay bang chung,
# hoi chan Dempster-Shafer) nam trong cac module con, package nay chi lap
# rap lai.

import json
import time

from .baseline import learn_from_db
from .plausibility import (
    classify_rpm_zone, evaluate_e1, evaluate_e2, evaluate_e3, evaluate_e4,
    RPM_ZONE_IDLE, RPM_ZONE_2500,
)
from .adapters import evaluate_e5, evaluate_e7
from .dtc_map import evaluate_e6
from .fusion import combine_all, decide


def run_diagnosis(cursor, live_data: dict) -> dict:
    """Chay toan bo pipeline chan doan MOT LAN: thu thap bang chung tu
    nhung nguon co du lieu, hoi chan Dempster-Shafer, tra ve ket qua.

    live_data: dict, TAT CA KEY DEU OPTIONAL - nguon nao thieu du lieu se
    tu dong khong dong gop bang chung (cac ham evaluate_eN da tu tra ve
    Theta khi thieu, xem plausibility.py/adapters.py/dtc_map.py). Cac key
    co the co:
        rpm, maf, iat, ect, speed        - so tuc thoi (dung cho E1)
        ltft_idle, ltft_2500             - hai so do trong CUNG mot phien
                                             kiem tra (dung cho E2, thuong
                                             tu protocol.py cung cap sau
                                             khi da di qua ca hai buoc)
        is_cold_start (bool)             - CO thi moi chay E3 (khong tu doan)
        warmup_series (list[(t,ect)])    - CO thi moi chay E4
        flagged_pids (list[int])         - CO thi moi chay E5
        dtc_codes (list[str])            - CO thi moi chay E6
        trend_alerts (list[dict])        - CO thi moi chay E7 (ket qua
                                             TrendAnalyzer.analyze() truc tiep)

    Tra ve dict: {masses, conflict_k, decision, top_hypothesis, belief,
    plausibility, evidence_count, evidence_sources, evidence_details}.
    Danh sach bang chung rong (khong key nao du dieu kien) van tra ve hop
    le - decision se la KHONG_KET_LUAN, dung nhu thiet ke (muc 4.5)."""
    evidences = []

    rpm = live_data.get("rpm")
    maf = live_data.get("maf")
    iat = live_data.get("iat")
    ect = live_data.get("ect")

    # --- E1: can rpm + maf, VA rpm phai roi dung vao mot trong hai vung
    # cua quy trinh kiem tra (garanti hoac giu 2500 vong). Ngoai hai vung
    # do khong co baseline tuong ung nen khong danh gia (khong bia).
    if rpm is not None and maf is not None:
        zone = classify_rpm_zone(rpm)
        metric = {RPM_ZONE_IDLE: "maf_per_rpm_idle", RPM_ZONE_2500: "maf_per_rpm_2500"}.get(zone)
        if metric is not None:
            baseline = learn_from_db(cursor, metric)
            evidences.append(evaluate_e1(rpm, maf, iat, baseline))

    # --- E2: can CA HAI so do LTFT trong cung phien (garanti va 2500 vong)
    if live_data.get("ltft_idle") is not None and live_data.get("ltft_2500") is not None:
        evidences.append(evaluate_e2(live_data["ltft_idle"], live_data["ltft_2500"], ect))

    # --- E3: CHI chay khi nguoi goi xac dinh ro is_cold_start (khong tu
    # doan qua ECT - cam bay #6, muc 13)
    if "is_cold_start" in live_data:
        evidences.append(evaluate_e3(iat, ect, live_data["is_cold_start"]))

    # --- E4: can chuoi (t, ect) day du tu luc no may
    if live_data.get("warmup_series"):
        evidences.append(evaluate_e4(live_data["warmup_series"], iat_start=live_data.get("iat_start")))

    # --- E5: suc khoe PID (tu HealthMonitor, nguoi goi truyen ket qua vao)
    if "flagged_pids" in live_data:
        evidences.append(evaluate_e5(live_data["flagged_pids"]))

    # --- E6: DTC (co the tao NHIEU Evidence cung luc, xem dtc_map.py)
    if "dtc_codes" in live_data:
        evidences.extend(evaluate_e6(live_data["dtc_codes"]))

    # --- E7: xu huong (tu TrendAnalyzer, nguoi goi truyen ket qua vao)
    if "trend_alerts" in live_data:
        evidences.extend(evaluate_e7(live_data["trend_alerts"]))

    masses, k = combine_all(evidences)
    d = decide(masses)

    return {
        "masses": masses,
        "conflict_k": k,
        "decision": d["decision"],
        "top_hypothesis": d["top_hypothesis"],
        "belief": d["belief"],
        "plausibility": d["plausibility"],
        "evidence_count": len(evidences),
        "evidence_sources": [e.source for e in evidences],
        "evidence_details": [e.detail for e in evidences],
    }


def save_diagnosis_result(cursor, result: dict) -> None:
    """Ghi mot ket qua run_diagnosis() vao bang diagnosis_results (muc 9).
    KHONG tu cursor.connection.commit() - nguoi goi (RuleEngine da co san
    nep tu commit() rieng sau moi thao tac DB) chiu trach nhiem commit,
    giu dung nep cu cua file.

    masses co key la frozenset - khong JSON hoa truc tiep duoc, chuyen ve
    chuoi "HYP1|HYP2" truoc khi luu vao evidence_json."""
    # k la frozenset gia thuyet (khong bao gio rong - Evidence da cam key
    # rong o __post_init__). THETA (toan tap 7 gia thuyet) duoc noi thanh
    # chuoi day du 7 ten, tu no da la bieu dien ro rang cua "khong biet".
    masses_serializable = {"|".join(sorted(k)): v for k, v in result["masses"].items()}
    evidence_json = json.dumps({
        "masses": masses_serializable,
        "evidence_sources": result["evidence_sources"],
        "evidence_details": result["evidence_details"],
    }, ensure_ascii=False)

    now = time.time()
    cursor.execute(
        "INSERT INTO diagnosis_results "
        "(timestamp_sec, top_hypothesis, belief, plausibility, conflict_k, "
        "decision, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (now, result["top_hypothesis"], result["belief"], result["plausibility"],
         result["conflict_k"], result["decision"], evidence_json, now),
    )