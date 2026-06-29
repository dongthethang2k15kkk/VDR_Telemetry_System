#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mqtt_subscriber.py  (chay TREN SERVER, trong Docker)
- Subscribe vdr/+/event  -> ghi vao DB server (lich su, web doc tu day)
- Subscribe vdr/+/live   -> hien realtime (tam thoi in ra; noi WebSocket sau)
Server CHI luu su kien (metadata). Khong luu obd_data tho, khong luu video.
"""
import json
import os
import sqlite3
import threading

import paho.mqtt.client as mqtt

MQTT_HOST   = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "9001"))
MQTT_WS_PATH= os.environ.get("MQTT_WS_PATH", "/mqtt")
MQTT_TLS    = os.environ.get("MQTT_TLS", "0") == "1"
TOPIC_PREFIX= os.environ.get("MQTT_TOPIC_PREFIX", "vdr")
SERVER_DB   = os.environ.get("SERVER_DB_PATH", "/data/server_history.db")

# Schema toi thieu cho lich su tren server (khop cot Pi day len)
_SCHEMA = {
    "crash_events": "id INTEGER PRIMARY KEY, device TEXT, timestamp_sec REAL, severity TEXT, gforce REAL, tilt REAL, speed_before REAL, source TEXT, evidence_path TEXT, acknowledged INTEGER",
    "alert_logs":   "id INTEGER PRIMARY KEY, device TEXT, timestamp_sec REAL, category TEXT, source TEXT, item TEXT, value TEXT, severity TEXT, description TEXT",
    "dtc_logs":     "id INTEGER PRIMARY KEY, device TEXT, timestamp_sec REAL, dtc_code TEXT, description TEXT, is_cleared INTEGER",
    "trip_logs":    "id INTEGER PRIMARY KEY, device TEXT, start_time REAL, end_time REAL, total_km REAL, engine_hours REAL",
}

_lock = threading.Lock()


def _db():
    conn = sqlite3.connect(SERVER_DB, check_same_thread=False)
    return conn


def _init_db():
    os.makedirs(os.path.dirname(SERVER_DB), exist_ok=True)
    conn = _db()
    for tbl, cols in _SCHEMA.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl} ({cols})")
    conn.commit()
    conn.close()
    print(f"🗄️  [SUB] DB lich su san sang: {SERVER_DB}")


def _device_from_topic(topic):
    # vdr/<device>/event  -> <device>
    parts = topic.split("/")
    return parts[1] if len(parts) >= 2 else "unknown"


def _save_event(device, table, row):
    if table not in _SCHEMA:
        return
    cols = [c.split()[0] for c in _SCHEMA[table].split(",")]
    data = {"device": device}
    for c in cols:
        if c in row:
            data[c] = row[c]
        elif c == "device":
            continue
    fields = ",".join(data.keys())
    marks  = ",".join("?" for _ in data)
    with _lock:
        conn = _db()
        # id la PRIMARY KEY -> tu chong trung neu Pi gui lai (INSERT OR IGNORE)
        conn.execute(
            f"INSERT OR IGNORE INTO {table} ({fields}) VALUES ({marks})",
            tuple(data.values()),
        )
        conn.commit()
        conn.close()
    print(f"💾 [SUB] {device}/{table} id={row.get('id')} -> luu")


def on_connect(cli, userdata, flags, rc):
    print(f"✅ [SUB] Ket noi broker rc={rc}, subscribe {TOPIC_PREFIX}/+/#")
    cli.subscribe(f"{TOPIC_PREFIX}/+/event", qos=1)
    cli.subscribe(f"{TOPIC_PREFIX}/+/live",  qos=0)


def on_message(cli, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception as e:
        print(f"⚠️  [SUB] Payload loi: {e}")
        return
    device = _device_from_topic(msg.topic)
    if msg.topic.endswith("/event"):
        _save_event(device, payload.get("table"), payload.get("row", {}))
    elif msg.topic.endswith("/live"):
        # TODO: forward ra WebSocket cho web. Tam in gon.
        data = payload.get("data", {})
        print(f"📡 [SUB] live {device}: {list(data.items())[:3]}...")


def main():
    _init_db()
    cli = mqtt.Client(client_id="vdr-server-sub", transport="websockets")
    cli.ws_set_options(path=MQTT_WS_PATH)
    if MQTT_TLS:
        cli.tls_set()
    cli.on_connect = on_connect
    cli.on_message = on_message
    cli.reconnect_delay_set(min_delay=1, max_delay=30)
    print(f"🚀 [SUB] Ket noi MQTT-WS {MQTT_HOST}:{MQTT_PORT}{MQTT_WS_PATH}")
    cli.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    cli.loop_forever()


if __name__ == "__main__":
    main()