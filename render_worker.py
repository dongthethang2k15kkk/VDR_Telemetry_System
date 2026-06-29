#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_worker.py  (chay TREN SERVER, trong Docker)
NAC 1 — KHUNG (chua render that bang overlay_engine):
  - Quet EVIDENCE_DIR/pending/*.zip
  - Giai nen, doc obd.json (kiem tra goi hop le)
  - TAM: tao file .mp4 placeholder trong rendered/ (nac 2 se thay bang render that)
  - Cap nhat crash_events.evidence_path trong server_history.db -> web thay has_video=true
  - Xoa zip khoi pending
Vong lap moi POLL_SEC giay.
"""
import os
import time
import json
import zipfile
import sqlite3
import shutil

SERVER_DB    = os.environ.get("SERVER_DB_PATH", "/data/server_history.db")
EVIDENCE_DIR = os.environ.get("EVIDENCE_DIR", "/data/evidence")
POLL_SEC     = int(os.environ.get("RENDER_POLL_SEC", "5"))

PENDING_DIR  = os.path.join(EVIDENCE_DIR, "pending")
RENDERED_DIR = os.path.join(EVIDENCE_DIR, "rendered")
WORK_DIR     = os.path.join(EVIDENCE_DIR, "work")


def _ensure_dirs():
    for d in (PENDING_DIR, RENDERED_DIR, WORK_DIR):
        os.makedirs(d, exist_ok=True)


def _parse_name(fname):
    # "<device>_<crash_id>.zip" -> (device, crash_id)
    base = fname[:-4] if fname.endswith(".zip") else fname
    parts = base.rsplit("_", 1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


def _update_db(crash_id, video_path):
    try:
        conn = sqlite3.connect(SERVER_DB)
        conn.execute(
            "UPDATE crash_events SET evidence_path=? WHERE id=?",
            (video_path, int(crash_id)),
        )
        conn.commit()
        n = conn.total_changes
        conn.close()
        return n
    except Exception as e:
        print(f"⚠️  [RENDER] Loi cap nhat DB: {e}")
        return 0


def process_one(zip_path):
    fname = os.path.basename(zip_path)
    device, crash_id = _parse_name(fname)
    if crash_id is None:
        print(f"⚠️  [RENDER] Ten file la, bo qua: {fname}")
        return

    work = os.path.join(WORK_DIR, fname[:-4])
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(work)
    except Exception as e:
        print(f"⚠️  [RENDER] Zip hong {fname}: {e}")
        return

    files = os.listdir(work)
    has_video = any(f.endswith(".ts") or f.endswith(".mp4") for f in files)
    obd_ok = "obd.json" in files
    print(f"🔧 [RENDER] {fname}: files={files} video={has_video} obd={obd_ok}")

    # ── NAC 1: tao placeholder thay vi render that ──
    # NAC 2 se thay block nay bang goi overlay_engine.render_window(...)
    out_name = f"evidence_{device}_{crash_id}.mp4"
    out_path = os.path.join(RENDERED_DIR, out_name)
    with open(out_path, "wb") as fo:
        fo.write(b"PLACEHOLDER_RENDERED_VIDEO")

    n = _update_db(crash_id, out_path)
    if n > 0:
        print(f"✅ [RENDER] crash_id={crash_id} -> {out_name} (DB cap nhat)")
    else:
        print(f"⚠️  [RENDER] crash_id={crash_id}: khong tim thay dong trong DB (van luu video)")

    # Dọn: xoa zip pending + thu muc work
    os.remove(zip_path)
    shutil.rmtree(work, ignore_errors=True)


def main():
    _ensure_dirs()
    print(f"🚀 [RENDER] Worker chay. Quet {PENDING_DIR} moi {POLL_SEC}s")
    while True:
        try:
            zips = [f for f in os.listdir(PENDING_DIR) if f.endswith(".zip")]
            for z in zips:
                process_one(os.path.join(PENDING_DIR, z))
        except Exception as e:
            print(f"⚠️  [RENDER] Loi vong lap: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()