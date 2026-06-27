"""
fcm_sender.py — Gui thong bao day (push) qua Firebase Cloud Messaging HTTP v1 API.
Doc service-account key, lay OAuth token, POST message toi tung device token.
"""
import json
import time
import threading

import requests
import google.auth.transport.requests
from google.oauth2 import service_account

# ===== Cau hinh =====
_KEY_PATH = "firebase_key.json"
_PROJECT_ID = "bk-autoblackbox"
_FCM_URL = f"https://fcm.googleapis.com/v1/projects/{_PROJECT_ID}/messages:send"
_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

# Cache OAuth credentials (tu refresh khi het han)
_credentials = None
_cred_lock = threading.Lock()


def _get_access_token():
    """Lay OAuth access token (tu refresh). Tra None neu loi."""
    global _credentials
    try:
        with _cred_lock:
            if _credentials is None:
                _credentials = service_account.Credentials.from_service_account_file(
                    _KEY_PATH, scopes=_SCOPES
                )
            if not _credentials.valid:
                _credentials.refresh(google.auth.transport.requests.Request())
            return _credentials.token
    except Exception as e:
        print(f"⚠️  [FCM] Lỗi lấy access token: {e}")
        return None


def _send_one(token: str, title: str, body: str, data: dict = None):
    """Gui push toi 1 device token. Tra (ok, status_code)."""
    access_token = _get_access_token()
    if not access_token:
        return False, 0

    message = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "android": {
                "priority": "high",
                "notification": {"channel_id": "bk_alerts", "sound": "default"},
            },
        }
    }
    if data:
        # data values phai la string
        message["message"]["data"] = {k: str(v) for k, v in data.items()}

    try:
        resp = requests.post(
            _FCM_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; UTF-8",
            },
            json=message,
            timeout=8,
        )
        return resp.ok, resp.status_code
    except Exception as e:
        print(f"⚠️  [FCM] Lỗi gửi push: {e}")
        return False, 0


def send_push(tokens, title: str, body: str, data: dict = None):
    """
    Gui push toi nhieu device token (background thread, non-blocking).
    tokens: list[str] hoac 1 str.
    Token loi 404/400 (app go cai / token het han) -> tra ve de caller xoa khoi DB.
    Tra ve list token CAN XOA (invalid).
    """
    if isinstance(tokens, str):
        tokens = [tokens]
    if not tokens:
        return []

    invalid = []
    for tk in tokens:
        ok, code = _send_one(tk, title, body, data)
        if not ok and code in (400, 404):
            # token khong con hop le -> danh dau xoa
            invalid.append(tk)
        time.sleep(0.05)  # tranh spam API
    return invalid


def send_push_async(tokens, title: str, body: str, data: dict = None, on_invalid=None):
    """Goi send_push trong thread rieng (khong chan luong chinh).
    on_invalid: callback(list_token_loi) de caller xoa khoi DB."""
    def _run():
        invalid = send_push(tokens, title, body, data)
        if invalid and on_invalid:
            try:
                on_invalid(invalid)
            except Exception as e:
                print(f"⚠️  [FCM] Lỗi callback on_invalid: {e}")

    threading.Thread(target=_run, daemon=True).start()


# Test nhanh: python3 fcm_sender.py <device_token>
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Dung: python3 fcm_sender.py <device_token>")
        sys.exit(1)
    tk = sys.argv[1]
    print("Gui test push...")
    inv = send_push(tk, "BK-AutoBlackBox", "Test thong bao day thanh cong! 🚗")
    print("Token không hợp lệ:", inv if inv else "(không có)")
    print("Xong. Kiểm tra điện thoại.")