import asyncio
import json
import time
import threading
import sqlite3
import websockets
from websockets.server import WebSocketServerProtocol

from config import DATABASE_PATH

# ── Cấu hình ──────────────────────────────────────────────────────────────
WS_HOST = "0.0.0.0"
WS_PORT = 8080

# Mapping PID → tên trường trong JSON
_PID_SPEED    = 0x0D
_PID_RPM      = 0x0C
_PID_THROTTLE = 0x11
_PID_COOLANT  = 0x05
_PID_LTFT     = 0x07


class VDRWebSocketServer:
    """
    Server WebSocket chạy trên luồng riêng.
    Broadcast payload JSON đến tất cả client đang kết nối.
    """

    def __init__(self, rule_engine=None, hz: int = 10):
        self._rule_engine  = rule_engine
        self._hz           = hz
        self._clients: set[WebSocketServerProtocol] = set()
        self._loop         = None
        self._current_alert = ""
        self._is_incident   = False
        self._lock          = threading.Lock()

        if rule_engine:
            rule_engine._ws_server = self

    def start(self):
        t = threading.Thread(target=self._run_loop, daemon=True, name="WSServer")
        t.start()
        print(f"🌐 WebSocket Server đang chạy tại ws://{WS_HOST}:{WS_PORT}")

    def notify_alert(self, category: str, item: str, value: str, description: str):
        with self._lock:
            label_map = {
                "speed":    "Overspeed",
                "throttle": "Accel",
                "coolant":  "Engine_Overheat",
                "ltft":     "Fuel_Anomaly",
            }
            self._current_alert = label_map.get(item, description[:20])
            self._is_incident   = True


    def push_telemetry(self, obd_data: dict):
        if self._loop is None:
            return

        with self._lock:
            alert      = self._current_alert
            is_incident = self._is_incident
            self._current_alert = ""
            self._is_incident   = False


        throttle = obd_data.get(_PID_THROTTLE, 0.0)
        speed    = obd_data.get(_PID_SPEED,    0.0)
        rpm      = obd_data.get(_PID_RPM,      0.0)

        brake_pedal = max(0.0, (1.0 - throttle / 100.0) * 30) if speed > 5 else 0.0

        payload = {
            "timestamp": time.time(),
            "telemetry": {
                "speed_kmh":   round(speed, 1),
                "rpm":         round(rpm),
                "brake_pedal": round(brake_pedal, 1),
                "throttle":    round(throttle, 1),
                "coolant_c":   round(obd_data.get(_PID_COOLANT, 0.0), 1),
            },
            "analysis": {
                "current_alert":   alert,
                "is_incident_saved": is_incident,
            }
        }

        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        async with websockets.serve(self._handler, WS_HOST, WS_PORT):
            print(f"✅ WS server listening on port {WS_PORT}")
            await asyncio.Future()  

    async def _handler(self, ws: WebSocketServerProtocol, path: str = "/"):
        self._clients.add(ws)
        client_ip = ws.remote_address[0] if ws.remote_address else "unknown"
        print(f"🔌 Client kết nối: {client_ip} (tổng: {len(self._clients)})")

        try:
            async for raw in ws:
                await self._handle_command(raw, ws)
        except websockets.exceptions.ConnectionClosedOK:
            pass
        except websockets.exceptions.ConnectionClosedError as e:
            print(f"⚠️ Client ngắt đột ngột: {e}")
        finally:
            self._clients.discard(ws)
            print(f"🔌 Client rời: {client_ip} (còn: {len(self._clients)})")

    async def _handle_command(self, raw: str, ws: WebSocketServerProtocol):
        try:
            cmd = json.loads(raw)
        except json.JSONDecodeError:
            return

        command = cmd.get("command")

        if command == "set_hz":
            new_hz = int(cmd.get("value", self._hz))
            self._hz = max(1, min(30, new_hz))
            print(f"⚙️  Cập nhật tần số: {self._hz} Hz")

        elif command == "set_mode":
            mode = cmd.get("value", "live")
            print(f"⚙️  Chế độ đổi sang: {mode}")

        elif command == "seek":
            ts = cmd.get("timestamp")
            print(f"🎬 Seek video tới timestamp: {ts}")

    async def _broadcast(self, payload: dict):
        if not self._clients:
            return
        msg = json.dumps(payload, ensure_ascii=False)
        dead = set()
        for ws in self._clients:
            try:
                await ws.send(msg)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        self._clients -= dead

def patch_rule_engine_send_alert(rule_engine_module, ws_server: VDRWebSocketServer):
    import obd_module.rule_engine as re_mod

    def patched_send_alert(category: str, item: str, value: str, message: str):
        print(f"🚨 [{category.upper()}] {item} ({value}): {message}")
        ws_server.notify_alert(category, item, value, message)

    re_mod.send_alert = patched_send_alert
    print("✅ Đã patch send_alert → WebSocket notify")