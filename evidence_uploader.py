#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_uploader.py  (chay TREN PI, doc lap voi main.py — giong mqtt_uploader.py)
Quet crash_events co evidence_path bat dau bang "PENDING_UPLOAD:",
nen thu muc bang chung (video .ts + telemetry.json) thanh zip trong bo nho,
POST multipart len server (/api/upload-evidence).

AN TOAN DU LIEU PHAP LY: KHONG BAO GIO xoa thu muc/video goc tren Pi
sau khi upload thanh cong — chi doi storage_manager.py tu don theo
chinh sach xoay vong khi day dia. Upload that bai (mat mang, server down)
-> giu nguyen PENDING_UPLOAD, tu thu lai vong quet sau (store-and-forward).
"""
import io
import os
import time
import sqlite3
import zipfile
from pathlib import Path

import requests

import config

SCAN_INTERVAL_SEC = getattr(config, "EVIDENCE_UPLOAD_INTERVAL_SEC", 20)
SERVER_API_URL = getattr(config, "SERVER_API_URL", "http://localhost:8080/api")
DEVICE_ID = getattr(config, "MQTT_DEVICE_ID", "pi-01")


def _db():
    conn = sqlite3.connect(str(config.DATABASE_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _zip_folder(folder: Path) -> bytes:
    """Nen toan bo thu muc bang chung (telemetry.json + cam_*.ts) vao bo nho, khong ghi file zip tam ra dia."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.iterdir()):
            if f.is_file():
                zf.write(f, arcname=f.name)
    buf.seek(0)
    return buf.read()


def _upload_one(conn, row):
    crash_id = row["id"]
    marker = row["evidence_path"] or ""
    if not marker.startswith("PENDING_UPLOAD:"):
        return  # "PENDING_UPLOAD" tran (chua co video, cho _save_evidence_package chot xong)

    folder_name = marker.split(":", 1)[1]
    folder = Path(config.STORAGE_DIR) / folder_name
    if not folder.is_dir():
        print(f"⚠️  [EVUP] Thieu thu muc bang chung: {folder} (crash_id={crash_id})")
        return

    try:
        zip_bytes = _zip_folder(folder)
    except Exception as e:
        print(f"⚠️  [EVUP] Loi nen zip crash_id={crash_id}: {e}")
        return

    try:
        resp = requests.post(
            f"{SERVER_API_URL}/upload-evidence",
            data={"device": DEVICE_ID, "crash_id": crash_id},
            files={"file": (f"{folder_name}.zip", zip_bytes, "application/zip")},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        # Mat mang / server down -> KHONG dong den file goc, thu lai vong sau
        print(f"⏳ [EVUP] Chua gui duoc crash_id={crash_id} ({e}), se thu lai")
        return

    # Thanh cong: chi doi marker trong DB, video goc van nguyen tren Pi
    conn.execute(
        "UPDATE crash_events SET evidence_path=? WHERE id=?",
        (f"UPLOADED:{folder_name}", crash_id),
    )
    conn.commit()
    print(f"📤 [EVUP] Da gui bang chung crash_id={crash_id} -> server ({len(zip_bytes)} bytes)")


def main():
    print(f"🚀 [EVUP] Uploader bang chung: {SERVER_API_URL}, quet moi {SCAN_INTERVAL_SEC}s")
    conn = _db()
    try:
        while True:
            rows = conn.execute(
                "SELECT id, evidence_path FROM crash_events "
                "WHERE evidence_path LIKE 'PENDING_UPLOAD:%'"
            ).fetchall()
            for row in rows:
                _upload_one(conn, row)
            time.sleep(SCAN_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\n👋 [EVUP] Dung.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()