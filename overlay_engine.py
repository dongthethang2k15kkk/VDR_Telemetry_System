import cv2
import sqlite3
import pandas as pd
import numpy as np
import subprocess
import os
import time
import glob
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from config import (
    DATABASE_PATH,
    STORAGE_DIR,
    FONT_PATH,
    CAMERA_LATENCY_SEC,
    EVIDENCE_PRE_SEC,
    EVIDENCE_POST_SEC,
)

CAM_GLOB_PATTERN: str = "cam_*.ts"
TIMESTAMP_FMT: str = "%Y%m%d_%H%M%S"


# ── FONT ──────────────────────────────────
def get_fonts():
    try:
        return (
            ImageFont.truetype(FONT_PATH, 40),
            ImageFont.truetype(FONT_PATH, 24),
            ImageFont.truetype(FONT_PATH, 16),
        )
    except IOError:
        print(f"⚠️  Font không tìm thấy tại {FONT_PATH}, dùng mặc định.")
        d = ImageFont.load_default()
        return d, d, d


# ── TELEMETRY ─────────────────────────────
def prefetch_telemetry(t_start: float, t_end: float) -> pd.DataFrame:
    print("⏳ Tải Telemetry từ DB...")
    try:
        conn = sqlite3.connect(str(DATABASE_PATH), check_same_thread=False)
        df = pd.read_sql_query(
            "SELECT timestamp_sec, pid_name, value FROM obd_data "
            "WHERE timestamp_sec BETWEEN ? AND ? ORDER BY timestamp_sec ASC",
            conn, params=(t_start, t_end))
        conn.close()
        if df.empty:
            print("⚠️  Không có Telemetry trong khoảng này.")
            return pd.DataFrame()
        df_pivot = df.pivot_table(index="timestamp_sec", columns="pid_name", values="value")
        print(f"✅ Tải {len(df_pivot)} mốc Telemetry.")
        return df_pivot.ffill().bfill()
    except Exception as e:
        print(f"❌ Lỗi tải Telemetry: {e}")
        return pd.DataFrame()


def lookup_telemetry(df: pd.DataFrame, abs_time_sec: float):
    if df.empty:
        return 0, 0, 0, 0
    idx = int(np.abs(df.index - abs_time_sec).argmin())
    row = df.iloc[idx]
    def safe(col):
        v = row.get(col, 0)
        return 0 if pd.isna(v) else float(v)
    return safe("Vehicle Speed"), safe("Engine RPM"), safe("Throttle Position"), safe("Coolant Temp")


# ── HUD RENDER ────────────────────────────
def render_glass_hud(frame, speed, rpm, throttle, temp, fonts, abs_time_sec=0):
    font_large, font_medium, font_small = fonts
    HUD_W, HUD_H = 320, 200
    PASTE_X, PASTE_Y = 30, 30
    hud = Image.new("RGBA", (HUD_W, HUD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(hud)
    frame_dt = datetime.fromtimestamp(abs_time_sec) if abs_time_sec > 0 else datetime.now()
    now_str = frame_dt.strftime("%d/%m/%Y  %H:%M:%S")
    draw.rounded_rectangle((0, 0, HUD_W, HUD_H), radius=15, fill=(20, 20, 20, 160))
    draw.text((20, 10), now_str, font=font_small, fill=(255, 220, 80, 255))
    draw.text((20, 35), f"{int(speed)} km/h", font=font_large, fill=(0, 255, 255, 255))
    draw.text((20, 90), f"RPM: {int(rpm)}", font=font_medium, fill=(255, 255, 255, 255))
    draw.text((20, 128), f"Thr: {int(throttle)}%  Temp: {int(temp)}°C", font=font_small, fill=(200, 200, 200, 255))
    rpm_ratio = min(rpm / 8000.0, 1.0)
    bar_color = (255, 50, 50, 255) if rpm > 6000 else (50, 255, 50, 255)
    draw.rectangle([20, 155, 300, 165], outline=(100, 100, 100, 255), width=1)
    draw.rectangle([20, 155, 20 + int(280 * rpm_ratio), 165], fill=bar_color)
    pil_frame = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    pil_frame.paste(hud, (PASTE_X, PASTE_Y), mask=hud)
    return cv2.cvtColor(np.array(pil_frame), cv2.COLOR_RGB2BGR)


# ── PARSE THỜI GIAN FILE ──────────────────
def parse_ts_start_time(filepath: str):
    parts = Path(filepath).stem.split("_", 1)
    if len(parts) < 2:
        return None
    try:
        return datetime.strptime(parts[1], TIMESTAMP_FMT).timestamp()
    except ValueError:
        return None


def get_video_duration(filepath: str) -> float:
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        return 0.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 12.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if frames <= 0:
        start = parse_ts_start_time(filepath)
        return max(0.0, time.time() - start) if start else 0.0
    return frames / fps


# ── FFMPEG WRITER ─────────────────────────
def open_ffmpeg_writer(output_path: str, width: int, height: int, fps: float):
    tmp_path = output_path.replace(".mp4", "_tmp.ts")
    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{width}x{height}", "-pix_fmt", "bgr24", "-r", str(fps),
        "-i", "pipe:0", "-c:v", "libx264", "-preset", "ultrafast",
        "-threads", "2", "-crf", "28", "-pix_fmt", "yuv420p",
        "-f", "mpegts", tmp_path,
    ]
    print(f"🚀 FFmpeg pipe → {tmp_path}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    proc._tmp_path = tmp_path
    proc._final_path = output_path
    return proc


# ── HÀM LÕI: render cửa sổ [t_start, t_end] tuyệt đối ──
def render_window(t_start_abs: float, t_end_abs: float, output_path: str) -> bool:
    """
    Render HUD cho mọi frame có thời gian thật nằm trong [t_start_abs, t_end_abs].
    Thời gian thật của frame = file_start + POS_MSEC/1000 - CAMERA_LATENCY_SEC.
    Trả về True nếu xuất được file.
    """
    storage_dir = Path(STORAGE_DIR)
    all_files = sorted(glob.glob(str(storage_dir / CAM_GLOB_PATTERN)))  # cũ → mới
    if not all_files:
        print("❌ Không có file cam_*.ts")
        return False

    # Lọc file có khả năng chứa frame trong cửa sổ
    candidates = []
    for f in all_files:
        fs = parse_ts_start_time(f)
        if fs is None:
            continue
        dur = get_video_duration(f)
        f_end = fs + dur
        # file chồng lấn cửa sổ? (đã trừ latency nên nới biên 1s cho an toàn)
        if f_end >= (t_start_abs - 1) and fs <= (t_end_abs + 1):
            candidates.append((f, fs))
    if not candidates:
        print("❌ Không có frame nào trong cửa sổ thời gian yêu cầu.")
        return False

    df = prefetch_telemetry(t_start_abs, t_end_abs)

    # Kích thước từ file đầu
    c0 = cv2.VideoCapture(candidates[0][0])
    width = int(c0.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(c0.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = c0.get(cv2.CAP_PROP_FPS) or 12.0
    c0.release()
    if width == 0 or height == 0:
        print("❌ Không đọc được kích thước frame.")
        return False
    print(f"📐 {width}x{height} @ {fps:.1f}fps")

    fonts = get_fonts()
    proc = open_ffmpeg_writer(output_path, width, height, fps)
    total = 0

    for path, file_start in candidates:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            continue
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            # Thời gian thật của frame (POS_MSEC = thời điểm trong file, chính xác bất kể fps)
            pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            abs_time = file_start + pos_msec / 1000.0 - CAMERA_LATENCY_SEC
            if abs_time < t_start_abs:
                continue
            if abs_time > t_end_abs:
                break
            speed, rpm, throttle, temp = lookup_telemetry(df, abs_time)
            out = render_glass_hud(frame, speed, rpm, throttle, temp, fonts, abs_time)
            try:
                proc.stdin.write(out.tobytes())
            except BrokenPipeError:
                print("❌ FFmpeg pipe đứt.")
                cap.release()
                proc.wait()
                return False
            total += 1
        cap.release()

    proc.stdin.close()
    proc.wait()

    if total == 0:
        print("❌ Không render được frame nào.")
        if os.path.exists(proc._tmp_path):
            os.remove(proc._tmp_path)
        return False

    # Convert .ts tạm → .mp4 faststart
    print(f"🔄 Convert → {output_path}")
    subprocess.run(["ffmpeg", "-y", "-i", proc._tmp_path, "-c", "copy",
                    "-movflags", "+faststart", output_path], stderr=subprocess.DEVNULL)
    if os.path.exists(proc._tmp_path):
        os.remove(proc._tmp_path)
    print(f"✅ Xong! {total} frames → {output_path}")
    return True


# ── API cho crash_detector gọi ────────────
def render_evidence(crash_time: float, pre_sec: int = None, post_sec: int = None) -> str:
    """
    Tạo video bằng chứng quanh thời điểm va chạm.
    crash_time: Unix timestamp (giây) lúc phát hiện va chạm.
    Trả về đường dẫn file, hoặc "" nếu thất bại.
    """
    pre = pre_sec if pre_sec is not None else EVIDENCE_PRE_SEC
    post = post_sec if post_sec is not None else EVIDENCE_POST_SEC
    t_start = crash_time - pre
    t_end = crash_time + post
    output = str(Path(STORAGE_DIR) / f"evidence_{int(crash_time)}.mp4")
    print("=" * 55)
    print(f"🎬 VIDEO BẰNG CHỨNG quanh {datetime.fromtimestamp(crash_time):%d/%m/%Y %H:%M:%S}")
    print(f"   Cửa sổ: -{pre}s → +{post}s")
    print("=" * 55)

    # Nếu va chạm vừa xảy ra, frame "post" có thể CHƯA ghi xong -> đợi
    wait_until = t_end + CAMERA_LATENCY_SEC + 2
    now = time.time()
    if now < wait_until:
        delay = wait_until - now
        print(f"⏳ Đợi {delay:.1f}s để camera ghi đủ phần sau va chạm...")
        time.sleep(delay)

    ok = render_window(t_start, t_end, output)
    return output if ok else ""


# ── CHẠY TAY (test): render 30s gần nhất ──
def main():
    print("🎬 OVERLAY ENGINE — test 30s gần nhất")
    now = time.time()
    render_window(now - 30, now, str(Path(STORAGE_DIR) / "hud_test.mp4"))


if __name__ == "__main__":
    main()