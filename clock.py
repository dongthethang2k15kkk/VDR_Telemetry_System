import os
import subprocess
from datetime import datetime
try:
    from config import TIME_SOURCE
except ImportError:
    TIME_SOURCE = "INTERNET"
_SUDO = [] if os.geteuid() == 0 else ["sudo", "-n"]  # user thuong: tu them sudo


def read_rtc() -> datetime:
    """Đọc RTC on-board (HYM8563) qua hwclock -> datetime giờ địa phương. Cần root."""
    out = subprocess.run(
        [*_SUDO, "hwclock", "-r"], check=True, capture_output=True, text=True
    ).stdout.strip()
    # hwclock -r trả ISO kèm offset, vd: 2026-06-17 16:04:54.536538+07:00
    return datetime.fromisoformat(out).replace(tzinfo=None)


def _set_ntp(enable: bool) -> None:
    subprocess.run(
        [*_SUDO, "timedatectl", "set-ntp", "true" if enable else "false"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def sync_clock() -> None:
    """Set đồng hồ OS theo TIME_SOURCE + tự bật/tắt NTP. Cần root. Fail-safe."""
    if TIME_SOURCE == "RTC":
        _set_ntp(False)                 # tắt NTP kẻo mạng kéo giờ internet đè lên RTC
        try:
            dt = read_rtc()
            subprocess.run(
                [*_SUDO, "date", "-s", dt.strftime("%Y-%m-%d %H:%M:%S")],
                check=True, stdout=subprocess.DEVNULL
            )
            print(f"🕒 [CLOCK] - RTC: {dt:%Y-%m-%d %H:%M:%S}")
        except Exception as e:
            print(f"⚠️  [CLOCK] Đọc RTC thất bại ({e}) → giữ nguyên giờ OS.")
    else:
        _set_ntp(True)                  # bật lại NTP để hệ thống tự đồng bộ qua internet
        print("🕒 [CLOCK] - NTP")


# Test tay: python3 clock.py — chỉ ĐỌC, không đổi giờ máy, không đụng NTP.
if __name__ == "__main__":
    print("🔍 Thử đọc RTC...\n")
    try:
        dt = read_rtc()
        print(f"✅ RTC trả về      : {dt:%Y-%m-%d %H:%M:%S}")
        print(f"   Giờ hệ thống    : {datetime.now():%Y-%m-%d %H:%M:%S}")
        print("\n   → Lệch nhiều = RTC chưa set đúng. Set giờ OS đúng rồi: sudo hwclock -w")
    except Exception as e:
        print(f"❌ Không đọc được RTC: {e}")
        print(" Chạy bằng sudo chưa? sudo python3 clock.py")
