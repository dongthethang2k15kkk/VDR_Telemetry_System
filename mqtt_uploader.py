#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mqtt_uploader.py  (chay TREN PI)
Hai nhiem vu:
  1) LIVE  : doc snapshot OBD moi nhat -> publish realtime (khong luu)
  2) EVENT : day cac bang su kien (crash/alert/dtc/trip) len server theo last_id
             -> server luu lam lich su. Mat mang thi last_id khong nhich,
                co mang lai tu day bu (store-and-forward tu nhien).
Khong dung obd_data tho, khong day video. Video van nam o Pi.
"""
import json
import time
import sqlite3
import threading

import paho.mqtt.client as mqtt

import config

# ── Cac bang su kien can day len server (ten_bang -> cot moc) ──
EVENT_TABLES = {
    "crash_events": "id",
    "alert_logs":   "id",
    "dtc_logs":     "id",
    "trip_logs":    "id",
}

# Cot dung de lam "live snapshot" lay tu obd_data (ban ghi moi nhat moi pid)
_TOPIC = lambda leaf: f"{config.MQTT_TOPIC_PREFIX}/{config.MQTT_DEVICE_ID}/{leaf}"

_CONFIG_KEY = lambda tbl: f"mqtt_last_id_{tbl}"


def _connect():
    cid = f"vdr-uploader-{config.MQTT_DEVICE_ID}"
    cli = mqtt.Client(client_id=cid, transport="websockets")
    if getattr(config, "MQTT_USER", ""):
        cli.username_pw_set(config.MQTT_USER, getattr(config, "MQTT_PASS", ""))
    cli.ws_set_options(path=config.MQTT_WS_PATH)
    if config.MQTT_TLS:
        cli.tls_set()
    cli.reconnect_delay_set(min_delay=1, max_delay=30)
    cli.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
    cli.loop_start()
    return cli


def _db():
    conn = sqlite3.connect(str(config.DATABASE_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _get_last_id(conn, tbl):
    row = conn.execute(
        "SELECT value FROM system_config WHERE key=?", (_CONFIG_KEY(tbl),)
    ).fetchone()
    return int(row["value"]) if row else 0


def _set_last_id(conn, tbl, val):
    conn.execute(
        "INSERT INTO system_config(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=?",
        (_CONFIG_KEY(tbl), float(val), float(val)),
    )
    conn.commit()


def push_events(cli, conn):
    """Day cac row su kien moi (id > last_id) len server, QoS 1 (it nhat 1 lan)."""
    for tbl, key_col in EVENT_TABLES.items():
        try:
            last = _get_last_id(conn, tbl)
            rows = conn.execute(
                f"SELECT * FROM {tbl} WHERE {key_col} > ? ORDER BY {key_col} ASC LIMIT 200",
                (last,),
            ).fetchall()
        except sqlite3.OperationalError:
            continue  # bang chua ton tai tren Pi -> bo qua
        if not rows:
            continue
        max_id = last
        for r in rows:
            payload = {"table": tbl, "row": dict(r)}
            info = cli.publish(_TOPIC("event"), json.dumps(payload, default=str), qos=1)
            # Chi nhich last_id khi day thanh cong (da gui vao buffer client)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                break
            max_id = max(max_id, int(r[key_col]))
        if max_id > last:
            _set_last_id(conn, tbl, max_id)
            print(f"📤 [UPLOADER] {tbl}: day {len(rows)} ban ghi, last_id -> {max_id}")


def push_live(cli, conn):
    """Snapshot gia tri OBD moi nhat cua moi pid -> publish realtime (khong luu)."""
    try:
        rows = conn.execute(
            "SELECT pid, pid_name, value, unit, MAX(timestamp_sec) AS ts "
            "FROM obd_data GROUP BY pid"
        ).fetchall()
    except sqlite3.OperationalError:
        return
    snap = {r["pid_name"] or r["pid"]: r["value"] for r in rows}
    if snap:
        cli.publish(_TOPIC("live"), json.dumps({"ts": time.time(), "data": snap}), qos=0)


def main():
    print(f"🚀 [UPLOADER] Ket noi MQTT-WS {config.MQTT_HOST}:{config.MQTT_PORT}{config.MQTT_WS_PATH}")
    cli = _connect()
    conn = _db()
    interval = config.MQTT_UPLOAD_INTERVAL_SEC
    live_interval = getattr(config, "MQTT_LIVE_INTERVAL_SEC", 60)
    last_live = 0.0
    try:
        while True:
            if cli.is_connected():
                now = time.time()
                if now - last_live >= live_interval:
                    push_live(cli, conn)      # realtime, gian ra theo MQTT_LIVE_INTERVAL_SEC
                    last_live = now
                push_events(cli, conn)    # su kien, giu nguyen tan suat (nhe, quan trong)
            else:
                print("⏳ [UPLOADER] Chua co ket noi, cho retry...")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n👋 [UPLOADER] Dung.")
    finally:
        cli.loop_stop()
        conn.close()


if __name__ == "__main__":
    main()