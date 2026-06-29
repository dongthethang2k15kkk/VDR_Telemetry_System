#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server_app.py  (chay TREN SERVER, trong Docker)
Gop lam mot process:
  - FastAPI: phuc vu web (REST lich su + WebSocket live)
  - MQTT client nen: nhan event tu Pi -> ghi server_history.db
                     nhan live tu Pi  -> bo vao bo nho -> day ra WebSocket
Server CHI luu su kien (metadata). Video o Pi, render rieng (cum sau).
Route tra DUNG shape web cu dang doi -> web khoi sua.
"""
import json
import os
import sqlite3
import threading
import asyncio

import paho.mqtt.client as mqtt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ── Config tu env ──
MQTT_HOST    = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT    = int(os.environ.get("MQTT_PORT", "9001"))
MQTT_WS_PATH = os.environ.get("MQTT_WS_PATH", "/mqtt")
MQTT_TLS     = os.environ.get("MQTT_TLS", "0") == "1"
TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "vdr")
SERVER_DB    = os.environ.get("SERVER_DB_PATH", "/data/server_history.db")
EVIDENCE_DIR = os.environ.get("EVIDENCE_DIR", "/data/evidence")  # noi luu zip upload + video render
LAB_PASSWORD = os.environ.get("LAB_PASSWORD", "bkauto2010")

# ── Schema lich su tren server (khop cot Pi day len) ──
_SCHEMA = {
    "crash_events": "id INTEGER PRIMARY KEY, device TEXT, timestamp_sec REAL, severity TEXT, gforce REAL, tilt REAL, speed_before REAL, source TEXT, evidence_path TEXT, acknowledged INTEGER",
    "alert_logs":   "id INTEGER PRIMARY KEY, device TEXT, timestamp_sec REAL, category TEXT, source TEXT, item TEXT, value TEXT, severity TEXT, description TEXT",
    "dtc_logs":     "id INTEGER PRIMARY KEY, device TEXT, timestamp_sec REAL, dtc_code TEXT, description TEXT, is_cleared INTEGER",
    "trip_logs":    "id INTEGER PRIMARY KEY, device TEXT, start_time REAL, end_time REAL, total_km REAL, engine_hours REAL",
}

_db_lock = threading.Lock()
# Bo nho giu live moi nhat (Pi day len, web doc ra)
_live_state = {"timestamp": 0, "telemetry": {}, "latest_alert": None}


def _db():
    conn = sqlite3.connect(SERVER_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    os.makedirs(os.path.dirname(SERVER_DB), exist_ok=True)
    conn = _db()
    for tbl, cols in _SCHEMA.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl} ({cols})")
    conn.commit()
    conn.close()
    print(f"🗄️  [SRV] DB lich su san sang: {SERVER_DB}")


# ==========================================
# MQTT NEN (nhan tu Pi)
# ==========================================
def _save_event(device, table, row):
    if table not in _SCHEMA:
        return
    cols = [c.split()[0] for c in _SCHEMA[table].split(",")]
    data = {"device": device}
    for c in cols:
        if c in row:
            data[c] = row[c]
    fields = ",".join(data.keys())
    marks  = ",".join("?" for _ in data)
    with _db_lock:
        conn = _db()
        conn.execute(f"INSERT OR IGNORE INTO {table} ({fields}) VALUES ({marks})", tuple(data.values()))
        conn.commit()
        conn.close()
    print(f"💾 [SRV] {device}/{table} id={row.get('id')} -> luu")


def _on_connect(cli, userdata, flags, rc):
    print(f"✅ [SRV] MQTT rc={rc}, subscribe {TOPIC_PREFIX}/+/#")
    cli.subscribe(f"{TOPIC_PREFIX}/+/event", qos=1)
    cli.subscribe(f"{TOPIC_PREFIX}/+/live",  qos=0)


def _on_message(cli, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception as e:
        print(f"⚠️  [SRV] payload loi: {e}")
        return
    device = msg.topic.split("/")[1] if len(msg.topic.split("/")) >= 2 else "unknown"
    if msg.topic.endswith("/event"):
        _save_event(device, payload.get("table"), payload.get("row", {}))
    elif msg.topic.endswith("/live"):
        data = payload.get("data", {})
        _live_state["timestamp"] = payload.get("ts", 0)
        _live_state["telemetry"] = data


def _mqtt_thread():
    cli = mqtt.Client(client_id="vdr-server-app", transport="websockets")
    cli.ws_set_options(path=MQTT_WS_PATH)
    if MQTT_TLS:
        cli.tls_set()
    cli.on_connect = _on_connect
    cli.on_message = _on_message
    cli.reconnect_delay_set(min_delay=1, max_delay=30)
    print(f"🚀 [SRV] Ket noi MQTT-WS {MQTT_HOST}:{MQTT_PORT}{MQTT_WS_PATH}")
    while True:
        try:
            cli.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            cli.loop_forever()
        except Exception as e:
            print(f"⏳ [SRV] MQTT retry sau loi: {e}")
            import time as _t; _t.sleep(5)


# ==========================================
# FASTAPI (phuc vu web)
# ==========================================
app = FastAPI(title="VDR Server App")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    _init_db()
    threading.Thread(target=_mqtt_thread, daemon=True).start()


@app.post("/api/login")
def lab_login(payload: dict):
    if (payload or {}).get("password", "") == LAB_PASSWORD:
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Sai mat khau")


@app.get("/api/alerts")
def get_alerts():
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, timestamp_sec, severity AS alert_type, description "
            "FROM alert_logs ORDER BY timestamp_sec DESC LIMIT 100"
        ).fetchall()
        return {"status": "success", "data": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/crash-events")
def get_crash_events():
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, timestamp_sec, severity, gforce, tilt, speed_before, source, evidence_path, acknowledged "
            "FROM crash_events ORDER BY timestamp_sec DESC LIMIT 100"
        ).fetchall()
        out = []
        for r in rows:
            ev = r["evidence_path"] or ""
            is_pending = ev.startswith("PENDING")
            fname = "" if is_pending else (ev.split("/")[-1] if ev else "")
            out.append({
                "id": r["id"], "timestamp": r["timestamp_sec"], "severity": r["severity"],
                "gforce": round(r["gforce"] or 0, 1), "tilt": round(r["tilt"] or 0, 0),
                "speed_before": round(r["speed_before"] or 0, 0), "source": r["source"],
                "evidence": fname, "has_video": bool(fname), "pending": is_pending,
                "acknowledged": bool(r["acknowledged"]),
            })
        return {"events": out, "count": len(out)}
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}
    finally:
        conn.close()


@app.get("/api/crash-events/active")
def get_active_crash():
    conn = _db()
    try:
        r = conn.execute(
            "SELECT id, timestamp_sec, severity, gforce, tilt, speed_before, evidence_path "
            "FROM crash_events WHERE acknowledged=0 AND severity IN ('NANG','VUA') "
            "ORDER BY timestamp_sec DESC LIMIT 1"
        ).fetchone()
        if not r:
            return {"active": False}
        ev = r["evidence_path"] or ""
        is_pending = ev.startswith("PENDING")
        fname = "" if is_pending else (ev.split("/")[-1] if ev else "")
        return {
            "active": True, "id": r["id"], "timestamp": r["timestamp_sec"], "severity": r["severity"],
            "gforce": round(r["gforce"] or 0, 1), "tilt": round(r["tilt"] or 0, 0),
            "speed_before": round(r["speed_before"] or 0, 0),
            "evidence": fname, "has_video": bool(fname), "pending": is_pending,
        }
    finally:
        conn.close()


@app.put("/api/crash-events/{event_id}/ack")
def ack_crash(event_id: int):
    conn = _db()
    try:
        conn.execute("UPDATE crash_events SET acknowledged=1 WHERE id=?", (event_id,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@app.get("/api/dtc/history")
def dtc_history():
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, timestamp_sec, dtc_code, description, is_cleared "
            "FROM dtc_logs ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return {"status": "success", "data": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(_live_state)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        print("🔌 [SRV] Web ngat WebSocket.")
    except Exception as e:
        print(f"⚠️  [SRV] WS loi: {e}")


@app.post("/api/upload-evidence")
async def upload_evidence(device: str = Form("pi-01"), crash_id: int = Form(...), file: UploadFile = File(...)):
    """Pi POST zip (video raw + obd) cho 1 su co -> luu vao pending de render worker xu ly."""
    pend = os.path.join(EVIDENCE_DIR, "pending")
    os.makedirs(pend, exist_ok=True)
    dest = os.path.join(pend, f"{device}_{crash_id}.zip")
    data = await file.read()
    with open(dest, "wb") as fo:
        fo.write(data)
    print(f"📦 [SRV] Nhan goi bang chung: {dest} ({len(data)} bytes)")
    return {"status": "received", "crash_id": crash_id, "bytes": len(data)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)