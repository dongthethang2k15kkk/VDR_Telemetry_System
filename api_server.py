import os
import subprocess
import datetime
import cv2
from fastapi.responses import StreamingResponse
import sqlite3
import asyncio
import time
import threading
import auto_calibration
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Import chuẩn từ config
from config import DATABASE_PATH, SAMPLING_RATE_HZ, LAB_PASSWORD, MPU_BASELINE_SPEED_MAX_KMH

app = FastAPI(title="BK-AutoBlackBox API")


@app.post("/api/login")
def lab_login(payload: dict):
    """Kiem tra mat khau Lab (so voi config.py)."""
    pw = (payload or {}).get("password", "")
    if LAB_PASSWORD and pw == LAB_PASSWORD:
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Sai mat khau")

# Mở CORS để Web UI gọi API thoải mái
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# KHỐI 1: KHỞI TẠO & DB HELPER
# ==========================================
def get_db():
    """Tạo connection read-only độc lập"""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ==========================================
# KHỐI 2: REST ENDPOINTS (Xử lý Alert)
# ==========================================
@app.get("/api/alerts")
def get_unresolved_alerts():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, timestamp_sec, alert_type, description 
            FROM maintenance_logs 
            WHERE is_resolved = 0 
            ORDER BY timestamp_sec DESC
        ''')
        alerts = [dict(row) for row in cursor.fetchall()]
        return {"status": "success", "data": alerts}
    finally:
        conn.close() # Đảm bảo luôn đóng connection dù có lỗi hay không

@app.put("/api/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int):
    conn = get_db()
    try:
        cursor = conn.cursor()
        now = time.time()
        
        cursor.execute('''
            UPDATE maintenance_logs 
            SET is_resolved = 1, resolved_at = ? 
            WHERE id = ? AND is_resolved = 0
        ''', (now, alert_id))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Alert not found or already resolved")
            
        conn.commit()
        return {"status": "success", "message": f"Alert {alert_id} resolved"}
    finally:
        conn.close() # Đảm bảo luôn đóng connection


# ==========================================
# KHỐI 3: WEBSOCKET ENDPOINT (Real-time)
# ==========================================
@app.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        while True:
            # Lấy data Telemetry mới nhất (4 PIDs)
            cursor.execute('''
                SELECT timestamp_sec, pid_name, value, unit 
                FROM obd_data 
                ORDER BY timestamp_sec DESC 
                LIMIT 4
            ''')
            telemetry_raw = cursor.fetchall()
            
            telemetry_data = {}
            latest_ts = 0
            for row in telemetry_raw:
                telemetry_data[row['pid_name']] = row['value']
                if row['timestamp_sec'] > latest_ts:
                    latest_ts = row['timestamp_sec']

            # Lấy alert mới nhất chưa đọc
            cursor.execute('''
                SELECT id, alert_type, description 
                FROM maintenance_logs 
                WHERE is_resolved = 0 
                ORDER BY timestamp_sec DESC 
                LIMIT 1
            ''')
            latest_alert = cursor.fetchone()

            # Đóng gói JSON
            payload = {
                "timestamp": latest_ts,
                "telemetry": telemetry_data,
                "latest_alert": dict(latest_alert) if latest_alert else None
            }

            # Bắn lên Client
            await websocket.send_json(payload)
            
            # Ngủ 100ms (10Hz)
            await asyncio.sleep(1.0 / SAMPLING_RATE_HZ)
            
    except WebSocketDisconnect:
        print("🔌 Web UI đã ngắt kết nối WebSocket.")
    except Exception as e:
        print(f"⚠️ Lỗi WebSocket: {e}")
    finally:
        conn.close() # Đã có sẵn block dọn dẹp an toàn khi đứt kết nối

def _gen_frames():
    from config import VIDEO_SOURCE
    cap = cv2.VideoCapture(VIDEO_SOURCE, cv2.CAP_FFMPEG)
    while True:
        ret, frame = cap.read()
        if not ret:
            cap.release()
            cap = cv2.VideoCapture(VIDEO_SOURCE, cv2.CAP_FFMPEG)
            continue
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, now_str, (frame.shape[1]-320, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,0), 3)
        cv2.putText(frame, now_str, (frame.shape[1]-320, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,220,80), 1)
        _, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
               + jpg.tobytes() + b'\r\n')

# ==========================================
# KHOI CRASH: Lich su tai nan + video bang chung
# ==========================================
@app.get("/api/crash-events")
def get_crash_events():
    """Danh sach su co tai nan (moi nhat truoc)."""
    conn = get_db()
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
                "id": r["id"],
                "timestamp": r["timestamp_sec"],
                "severity": r["severity"],
                "gforce": round(r["gforce"] or 0, 1),
                "tilt": round(r["tilt"] or 0, 0),
                "speed_before": round(r["speed_before"] or 0, 0),
                "source": r["source"],
                "evidence": fname,
                "has_video": bool(fname),
                "pending": is_pending,
                "acknowledged": bool(r["acknowledged"]),
            })
        return {"events": out, "count": len(out)}
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}
    finally:
        conn.close()


@app.get("/api/crash-events/{event_id}/obd")
def get_crash_obd(event_id: int):
    """OBD timeline quanh thoi diem va cham (15s truoc -> 15s sau)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT timestamp_sec FROM crash_events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Khong tim thay su co")
        t = row["timestamp_sec"]
        data = conn.execute(
            "SELECT timestamp_sec, pid_name, value FROM obd_data "
            "WHERE timestamp_sec BETWEEN ? AND ? ORDER BY timestamp_sec ASC",
            (t - 15, t + 15)
        ).fetchall()
        return {"crash_time": t, "data": [dict(d) for d in data]}
    finally:
        conn.close()


@app.get("/api/evidence/{filename}")
def get_evidence_video(filename: str):
    """Serve file video bang chung (chong path traversal)."""
    import os
    from fastapi.responses import FileResponse
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Ten file khong hop le")
    from config import STORAGE_DIR
    path = os.path.join(str(STORAGE_DIR), filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Khong co video")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/crash-events/active")
def get_active_crash():
    """Su co NANG/VUA chua xem (acknowledged=0), moi nhat. De hien canh bao takeover."""
    conn = get_db()
    try:
        r = conn.execute(
            "SELECT id, timestamp_sec, severity, gforce, tilt, speed_before, evidence_path "
            "FROM crash_events WHERE acknowledged=0 AND severity IN ('NANG','VUA') "
            "ORDER BY timestamp_sec DESC LIMIT 1"
        ).fetchone()
        if not r:
            return {"active": False}
        ev = r["evidence_path"] or ""
        return {
            "active": True,
            "id": r["id"], "timestamp": r["timestamp_sec"], "severity": r["severity"],
            "gforce": round(r["gforce"] or 0, 1), "tilt": round(r["tilt"] or 0, 0),
            "speed_before": round(r["speed_before"] or 0, 0),
            "evidence": ev.split("/")[-1] if ev else "", "has_video": bool(ev),
        }
    finally:
        conn.close()


@app.put("/api/crash-events/{event_id}/ack")
def ack_crash(event_id: int):
    """Danh dau da xem (tat takeover cho su co nay)."""
    conn = get_db()
    try:
        conn.execute("UPDATE crash_events SET acknowledged=1 WHERE id=?", (event_id,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@app.get("/stream/camera")
def camera_stream():
    return StreamingResponse(
        _gen_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
# ==========================================
# KHỐI 4: ENTRY POINT
# ==========================================

@app.get("/api/maintenance/history")
def get_maintenance_history():
    conn = get_db()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, timestamp_sec, item, km_at_service, note FROM maintenance_history ORDER BY timestamp_sec DESC LIMIT 100"
        ).fetchall()
        return {"status": "success", "data": [dict(r) for r in rows]}
    finally:
        conn.close()

def run_fastapi_server():
    print("🌐 Đang khởi động API Server (FastAPI) tại port 8080...")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning", reload=False, workers=1, timeout_graceful_shutdown=1)

# ==========================================
# KHỐI 5: MAINTENANCE / BẢO DƯỠNG (REST)
# ==========================================
def _current_odo(cursor) -> float:
    """ODO hiện tại = base_odo người dùng nhập + tổng km các chuyến đã lưu."""
    row = cursor.execute("SELECT value FROM system_config WHERE key='base_odo'").fetchone()
    base_odo = row["value"] if row else 0.0
    trip = cursor.execute("SELECT COALESCE(SUM(total_km), 0) AS s FROM trip_logs").fetchone()
    # Fix#2: cong them km chuyen dang chay (RuleEngine persist xuong system_config) -> 1 nguon su that
    _live = cursor.execute("SELECT value FROM system_config WHERE key='live_trip_km'").fetchone()
    live_km = float(_live["value"]) if _live and _live["value"] else 0.0
    return float(base_odo) + float(trip["s"] if trip else 0.0) + live_km


@app.get("/api/maintenance")
def get_maintenance():
    conn = get_db()
    try:
        cur = conn.cursor()
        current_odo = _current_odo(cur)
        # Task1f: tong gio no may tu trip_logs
        _eh_row = cur.execute("SELECT COALESCE(SUM(engine_hours), 0) AS s FROM trip_logs").fetchone()
        _live_eh = cur.execute("SELECT value FROM system_config WHERE key='live_trip_eh'").fetchone()
        _live_eh_v = float(_live_eh["value"]) if _live_eh and _live_eh["value"] else 0.0
        total_engine_hours = float(_eh_row["s"] if _eh_row else 0.0) + _live_eh_v  # Fix#2
        items = []
        rows = cur.execute(
            "SELECT item, interval_km, interval_days, last_km, last_date, status, last_engine_hours, interval_engine_hours FROM maintenance_schedule"
        ).fetchall()
        for r in rows:
            km_used = current_odo - (r["last_km"] or 0)
            km_ratio = (km_used / r["interval_km"] * 100) if r["interval_km"] else 0
            days_used = (time.time() - r["last_date"]) / 86400.0 if r["last_date"] else 0
            days_ratio = (days_used / r["interval_days"] * 100) if r["interval_days"] else 0
            engine_hours_used = total_engine_hours - (r["last_engine_hours"] or 0)
            interval_eh = r["interval_engine_hours"]
            engine_hours_ratio = (engine_hours_used / interval_eh * 100) if interval_eh else 0
            ratio = max(km_ratio, days_ratio, engine_hours_ratio)
            km_left = round((r["interval_km"] or 0) - km_used, 1)
            days_left = round(r["interval_days"] - days_used) if r["interval_days"] else None
            engine_hours_left = round(interval_eh - engine_hours_used, 1) if interval_eh else None
            severity = "critical" if ratio >= 100 else "warning" if ratio >= 90 else "ok"
            # Xac dinh moc QUYET DINH (cai co ratio cao nhat) de hien thi nhat quan voi severity
            driver = "km"
            if days_ratio >= km_ratio and days_ratio >= engine_hours_ratio:
                driver = "days"
            elif engine_hours_ratio >= km_ratio and engine_hours_ratio >= days_ratio:
                driver = "engine_hours"
            # Mo ta tinh trang theo moc quyet dinh (am = qua han)
            if driver == "km":
                overdue = km_used - (r["interval_km"] or 0)
                status_text = (f"Quá hạn {abs(round(overdue))} km" if overdue >= 0
                               else f"Còn {round(km_left)} km")
            elif driver == "days":
                overdue = days_used - (r["interval_days"] or 0)
                status_text = (f"Quá hạn {abs(round(overdue))} ngày" if overdue >= 0
                               else f"Còn {round(days_left)} ngày")
            else:
                overdue = engine_hours_used - (interval_eh or 0)
                status_text = (f"Quá hạn {abs(round(overdue))} giờ máy" if overdue >= 0
                               else f"Còn {round(engine_hours_left)} giờ máy")
            items.append({
                "item": r["item"],
                "interval_km": r["interval_km"],
                "interval_days": r["interval_days"],
                "last_km": r["last_km"],
                "km_used": round(km_used, 1),
                "km_left": km_left,
                "days_left": days_left,
                "engine_hours_used": round(engine_hours_used, 1),
                "engine_hours_left": engine_hours_left,
                "interval_engine_hours": interval_eh,
                "ratio": round(ratio, 1),
                "severity": severity,  # Task1f
                "driver": driver,
                "status_text": status_text,
            })
        return {"status": "success", "current_odo": round(current_odo, 1), "data": items}
    finally:
        conn.close()


@app.put("/api/maintenance/odometer")
def set_odometer(payload: dict):
    km = payload.get("km")
    if km is None:
        raise HTTPException(status_code=400, detail="Thiếu trường 'km'")
    try:
        km = float(km)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="'km' phải là số")
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('base_odo', ?)", (km,))
        conn.commit()
        return {"status": "success", "base_odo": km}
    finally:
        conn.close()


@app.put("/api/maintenance/{item}/done")
def mark_maintenance_done(item: str, payload: dict = None):
    # Task5b: nhan note (tuy chon) tu body
    note = (payload or {}).get("note", "") if isinstance(payload, dict) else ""
    conn = get_db()
    try:
        cur = conn.cursor()
        current_odo = _current_odo(cur)
        # Task5b: tong gio no may hien tai de reset chu ky (dong bo voi rule_engine.mark_maintained)
        _eh = cur.execute("SELECT COALESCE(SUM(engine_hours), 0) FROM trip_logs").fetchone()
        current_eh = float(_eh[0] if _eh else 0.0)
        res = cur.execute(
            "UPDATE maintenance_schedule SET last_km=?, last_date=?, status='🟢 Normal' WHERE item=?",
            (current_odo, time.time(), item),
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Không có hạng mục bảo dưỡng này")
        # Task5b: reset last_engine_hours (va lo hong: web bam done truoc day khong reset gio may)
        cur.execute("UPDATE maintenance_schedule SET last_engine_hours=? WHERE item=?", (current_eh, item))
        cur.execute(
            "INSERT INTO maintenance_history (timestamp_sec, item, km_at_service, note) VALUES (?, ?, ?, ?)",
            (time.time(), item, current_odo, note)
        )
        conn.commit()
        return {"status": "success", "item": item, "reset_at_km": round(current_odo, 1)}
    finally:
        conn.close()


# ==========================================
# KHOI 7: DTC - MA LOI CHAN DOAN (Task2c)
# ==========================================
@app.post("/api/dtc/scan")
def dtc_scan():
    """Set co yeu cau quet -> reader chinh (giu bus) quet -> poll ket qua moi."""
    import time as _t
    conn = get_db()
    try:
        # Moc thoi gian truoc khi quet, de loc DTC moi
        t0 = _t.time()
        conn.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('dtc_scan_request', 1)")
        conn.commit()
        # Doi reader xu ly (poll toi da ~4s)
        found = []
        for _ in range(40):
            _t.sleep(0.1)
            rows = conn.execute(
                "SELECT dtc_code, description FROM dtc_logs WHERE timestamp_sec >= ? ORDER BY id DESC",
                (t0,),
            ).fetchall()
            if rows:
                # gom unique
                seen = set()
                found = []
                for r in rows:
                    if r["dtc_code"] in seen:
                        continue
                    seen.add(r["dtc_code"])
                    found.append({"code": r["dtc_code"], "description": r["description"]})
                break
            # neu co da bi xoa (reader da xu ly) ma khong co DTC -> thoat s-som
            flag = conn.execute("SELECT value FROM system_config WHERE key='dtc_scan_request'").fetchone()
            if flag and float(flag["value"] or 0) == 0 and _ > 2:
                break
        return {"status": "success", "count": len(found), "data": found}
    finally:
        conn.close()


@app.get("/api/dtc/history")
def dtc_history():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, timestamp_sec, dtc_code, description, is_cleared "
            "FROM dtc_logs ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return {"status": "success", "data": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.put("/api/dtc/{dtc_id}/clear")
def dtc_clear(dtc_id: int):
    conn = get_db()
    try:
        res = conn.execute("UPDATE dtc_logs SET is_cleared=1 WHERE id=?", (dtc_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Khong tim thay DTC")
        conn.commit()
        return {"status": "success", "id": dtc_id, "cleared": True}
    finally:
        conn.close()


# ==========================================
# KHOI 8: PREDICTIVE (Task3e) - tinh live
# ==========================================
@app.get("/api/maintenance/prediction")
def get_prediction():
    """Tinh du bao live moi lan goi (khong luu bang)."""
    from obd_module.rule_engine import TrendAnalyzer
    conn = get_db()
    try:
        results = TrendAnalyzer().analyze(conn.cursor())
        conn.commit()  # vi analyze co the ghi maintenance_logs
        return {"status": "success", "count": len(results), "data": results}
    except Exception as e:
        return {"status": "error", "error": str(e), "data": []}
    finally:
        conn.close()


# ==========================================
# KHOI 9: FCM - DEVICE TOKENS (thong bao day)
# ==========================================
def _ensure_token_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_tokens ("
        "token TEXT PRIMARY KEY, "
        "created_at REAL, "
        "last_seen REAL)"
    )


def get_all_device_tokens():
    """Tra ve list token da dang ky (cho fcm_sender dung)."""
    conn = get_db()
    try:
        _ensure_token_table(conn)
        rows = conn.execute("SELECT token FROM device_tokens").fetchall()
        return [r["token"] for r in rows]
    finally:
        conn.close()


def remove_device_tokens(tokens):
    """Xoa token khong con hop le (callback tu fcm_sender)."""
    if not tokens:
        return
    conn = get_db()
    try:
        _ensure_token_table(conn)
        conn.executemany("DELETE FROM device_tokens WHERE token=?", [(t,) for t in tokens])
        conn.commit()
    finally:
        conn.close()


@app.post("/api/register-token")
def register_token(payload: dict):
    """App gui FCM token len -> luu DB (de backend gui push)."""
    import time as _t
    token = (payload or {}).get("token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Thieu token")
    conn = get_db()
    try:
        _ensure_token_table(conn)
        now = _t.time()
        conn.execute(
            "INSERT INTO device_tokens (token, created_at, last_seen) VALUES (?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET last_seen=?",
            (token, now, now, now)
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM device_tokens").fetchone()[0]
        return {"status": "success", "total_devices": n}
    finally:
        conn.close()


@app.post("/api/unregister-token")
def unregister_token(payload: dict):
    """Huy dang ky token (app go thong bao / dang xuat)."""
    token = (payload or {}).get("token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Thieu token")
    conn = get_db()
    try:
        _ensure_token_table(conn)
        conn.execute("DELETE FROM device_tokens WHERE token=?", (token,))
        conn.commit()
        return {"status": "success"}
    finally:
        conn.close()


# ==========================================
# KHỐI: THIẾT BỊ (capabilities + health) — nền tảng để web ẩn/hiện panel
# ==========================================
@app.get("/api/device/capabilities")
def device_capabilities():
    """Bao cho web biet dang noi vao Pi va co nhung tinh nang thiet bi nao.
    server_app.py (chay tren server lab) KHONG co route nay -> web fetch loi/404
    la hieu dang xem ban server, tu an cac panel thiet bi."""
    return {"device": "pi", "features": ["health"]}


@app.get("/api/system/health")
def system_health():
    """Suc khoe phan cung Pi: CPU%, nhiet do, RAM, uptime. Doc thang /proc va /sys, khong can psutil."""

    def _cpu_percent(interval=0.15):
        def _read():
            with open("/proc/stat") as f:
                parts = f.readline().split()[1:8]
            vals = list(map(int, parts))
            idle = vals[3] + vals[4]
            total = sum(vals)
            return idle, total
        try:
            idle1, total1 = _read()
            time.sleep(interval)
            idle2, total2 = _read()
            d_idle = idle2 - idle1
            d_total = total2 - total1
            if d_total <= 0:
                return None
            return round((1 - d_idle / d_total) * 100, 1)
        except Exception:
            return None

    def _temp_c():
        for path in ("/sys/class/thermal/thermal_zone0/temp",
                     "/sys/class/thermal/thermal_zone1/temp"):
            try:
                with open(path) as f:
                    raw = int(f.read().strip())
                return round(raw / 1000, 1)
            except Exception:
                continue
        return None

    def _mem():
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.strip().split()[0])
            total_mb = info.get("MemTotal", 0) / 1024
            avail_mb = info.get("MemAvailable", 0) / 1024
            used_pct = round((1 - avail_mb / total_mb) * 100, 1) if total_mb else None
            return {"total_mb": round(total_mb), "used_percent": used_pct}
        except Exception:
            return {"total_mb": None, "used_percent": None}

    def _uptime_sec():
        try:
            with open("/proc/uptime") as f:
                return round(float(f.read().split()[0]))
        except Exception:
            return None

    def _git_hash():
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                capture_output=True, text=True, timeout=2,
            )
            return out.stdout.strip() or None
        except Exception:
            return None

    return {
        "cpu_percent": _cpu_percent(),
        "temp_c": _temp_c(),
        "mem": _mem(),
        "uptime_sec": _uptime_sec(),
        "version": _git_hash(),
    }


# ==========================================
# KHỐI: AUTO-CALIBRATION (chay nen, khong mo phan cung rieng - doc pid_health/mpu_baseline)
# ==========================================
_calib_lock = threading.Lock()
_calib_running = False


def _get_current_speed_kmh():
    """Doc toc do moi nhat tu obd_data, dung de chan calibrate luc xe dang chay."""
    try:
        conn = get_db()
        r = conn.execute(
            "SELECT value FROM obd_data WHERE pid=? ORDER BY timestamp_sec DESC LIMIT 1",
            ("0xd",)
        ).fetchone()
        if r is None:
            r = conn.execute(
                "SELECT value FROM obd_data WHERE pid_name='Vehicle Speed' ORDER BY timestamp_sec DESC LIMIT 1"
            ).fetchone()
        conn.close()
        return float(r[0]) if r else None
    except Exception:
        return None


@app.post("/api/calibration/start")
def calibration_start():
    global _calib_running
    speed = _get_current_speed_kmh()
    if speed is not None and speed > MPU_BASELINE_SPEED_MAX_KMH:
        raise HTTPException(status_code=423,
                            detail=f"Xe đang di chuyển ({speed:.0f} km/h) - dừng xe trước khi hiệu chỉnh")
    with _calib_lock:
        if _calib_running:
            raise HTTPException(status_code=409, detail="Đang có 1 lần hiệu chỉnh chạy rồi")
        _calib_running = True

    def _run():
        global _calib_running
        try:
            auto_calibration.run_checks_and_measure()
        finally:
            _calib_running = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/api/calibration/status")
def calibration_status():
    return {
        "running": _calib_running,
        "checks": [{"name": n, "status": st, "note": note} for (n, st, note) in auto_calibration.checks],
        "proposals": [{"name": name, "old": old, "new": new}
                      for name, (old, new, _) in auto_calibration.proposals.items()],
    }


@app.post("/api/calibration/apply")
def calibration_apply():
    if _calib_running:
        raise HTTPException(status_code=409, detail="Đang chạy hiệu chỉnh, đợi xong rồi mới áp dụng")
    speed = _get_current_speed_kmh()
    if speed is not None and speed > MPU_BASELINE_SPEED_MAX_KMH:
        raise HTTPException(status_code=423,
                            detail=f"Xe đang di chuyển ({speed:.0f} km/h) - dừng xe trước khi áp dụng")
    return auto_calibration.apply_proposals()


@app.get("/api/calibration/last")
def calibration_last():
    last = auto_calibration.get_last_run()
    if last is None:
        raise HTTPException(status_code=404, detail="Chưa từng chạy auto-calibration")
    return last


# ==========================================
# KHỐI: STORAGE MANAGER (% đĩa, dọn theo chính sách)
# ==========================================
_storage_cleanup_lock = threading.Lock()
_storage_cleanup_running = False


@app.get("/api/storage/status")
def storage_status():
    import shutil as _shutil
    from config import STORAGE_DIR
    from storage_manager import DiskRotation

    dr = DiskRotation()
    total, used, free = _shutil.disk_usage(STORAGE_DIR)
    video_files = [f for f in os.listdir(STORAGE_DIR) if f.endswith(".mp4") or f.endswith(".ts")]
    video_paths = [os.path.join(STORAGE_DIR, f) for f in video_files]
    video_total_bytes = sum(os.path.getsize(p) for p in video_paths if os.path.exists(p))
    oldest_ts = None
    if video_paths:
        oldest_ts = min(os.path.getmtime(p) for p in video_paths if os.path.exists(p))

    return {
        "usage_percent": round((used / total) * 100, 1),
        "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1),
        "free_gb": round(free / (1024**3), 1),
        "threshold_percent": dr.threshold,
        "retention_days": dr.retention_days,
        "video_file_count": len(video_files),
        "video_total_mb": round(video_total_bytes / (1024**2), 1),
        "oldest_video_timestamp": oldest_ts,
    }


@app.post("/api/storage/cleanup")
def storage_cleanup():
    global _storage_cleanup_running
    with _storage_cleanup_lock:
        if _storage_cleanup_running:
            raise HTTPException(status_code=409, detail="Đang dọn dẹp rồi, đợi xong")
        _storage_cleanup_running = True

    try:
        from storage_manager import DiskRotation
        dr = DiskRotation()
        before = dr.get_disk_usage()
        dr.delete_oldest_files()
        dr._cleanup_database()
        after = dr.get_disk_usage()
        return {"before_percent": round(before, 1), "after_percent": round(after, 1), "cleaned": True}
    finally:
        _storage_cleanup_running = False
