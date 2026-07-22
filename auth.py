"""
auth.py - Quan ly phien dang nhap (session token) cho VDR Telemetry web.
Khong dung JWT (kho thu hoi) - dung session token ngau nhien, chi luu HASH
trong SQLite (giong co che GitHub Personal Access Token). Dung chung cho
ca api_server.py (Pi) va server_app.py (server) - cung mot module.
"""
import time
import secrets
import hashlib
import sqlite3
import threading

from config import DATABASE_PATH, SESSION_TTL_SEC, LOGIN_MAX_FAIL, LOGIN_LOCK_SEC

_lock = threading.Lock()

# ---------- rate limit dang nhap (RAM, khong can ben vung qua restart) ----------
_fail_tracker = {}  # ip -> {"count": int, "locked_until": float}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_conn():
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    conn = _get_conn()
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY,
            created_at REAL,
            expires_at REAL,
            last_seen REAL,
            client_ip TEXT
        )''')
        conn.commit()
    finally:
        conn.close()


_ensure_table()


# ---------- rate limit dang nhap ----------
def check_rate_limit(ip: str):
    """Tra (allowed: bool, wait_sec: float). Khong tang counter o day."""
    entry = _fail_tracker.get(ip)
    if not entry:
        return True, 0
    now = time.time()
    if entry.get("locked_until", 0) > now:
        return False, entry["locked_until"] - now
    return True, 0


def record_login_fail(ip: str):
    now = time.time()
    entry = _fail_tracker.setdefault(ip, {"count": 0, "locked_until": 0})
    entry["count"] += 1
    if entry["count"] >= LOGIN_MAX_FAIL:
        overflow = entry["count"] - LOGIN_MAX_FAIL
        lock_sec = LOGIN_LOCK_SEC * (2 ** min(overflow, 6))
        entry["locked_until"] = now + lock_sec


def record_login_success(ip: str):
    _fail_tracker.pop(ip, None)


# ---------- session ----------
def create_session(client_ip: str) -> dict:
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + SESSION_TTL_SEC
    conn = _get_conn()
    try:
        with _lock:
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, created_at, expires_at, last_seen, client_ip) "
                "VALUES (?,?,?,?,?)",
                (_hash_token(token), now, expires_at, now, client_ip))
            conn.commit()
    finally:
        conn.close()
    return {"token": token, "expires_at": expires_at}


def verify_token(token: str) -> bool:
    if not token:
        return False
    th = _hash_token(token)
    now = time.time()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT expires_at FROM auth_sessions WHERE token_hash = ?", (th,)
        ).fetchone()
        if row is None:
            return False
        if row["expires_at"] < now:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (th,))
            conn.commit()
            return False
        new_expires = now + SESSION_TTL_SEC
        conn.execute(
            "UPDATE auth_sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
            (now, new_expires, th))
        conn.commit()
        return True
    finally:
        conn.close()


def revoke(token: str):
    th = _hash_token(token)
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (th,))
        conn.commit()
    finally:
        conn.close()


def revoke_all():
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM auth_sessions")
        conn.commit()
    finally:
        conn.close()


def cleanup_expired():
    now = time.time()
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
        conn.commit()
    finally:
        conn.close()
