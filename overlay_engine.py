"""
overlay_engine.py  –  VDR Telemetry System
Render HUD lên đúng 120 giây video gần nhất từ các file .ts do CameraRecorder ghi.

Luồng hoạt động:
  1. Quét STORAGE_DIR tìm tất cả file cam_*.ts, sắp xếp mới → cũ
  2. Tính tổng thời lượng cần: TARGET_SECONDS (120s)
  3. Gom file từ mới nhất ngược về đến khi đủ 120s
     - File đầu tiên (mới nhất) có thể đang ghi dở → đọc bằng OpenCV trực tiếp
     - Tính offset cắt của file cũ nhất để không lấy dư
  4. Với mỗi frame: tra DB lấy telemetry gần nhất → render HUD bằng Pillow
  5. Pipe frame qua FFmpeg subprocess để mux ra file .ts đầu ra
     (không re-encode bằng GStreamer, camera đã tự nén)
"""

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
    OPERATION_MODE,
    FONT_PATH,
)

# ──────────────────────────────────────────
# HẰNG SỐ
# ──────────────────────────────────────────
TARGET_SECONDS: int = 30           # 30 giây gần nhất
OUTPUT_FILENAME: str = "hud_evidence.mp4"
CAM_GLOB_PATTERN: str = "cam_*.ts"  # Khớp với output_pattern trong CameraRecorder
TIMESTAMP_FMT: str = "%Y%m%d_%H%M%S"  # Khớp cam_%Y%m%d_%H%M%S.ts


# ──────────────────────────────────────────
# 1. FONT
# ──────────────────────────────────────────
def get_fonts():
    try:
        return (
            ImageFont.truetype(FONT_PATH, 40),
            ImageFont.truetype(FONT_PATH, 24),
            ImageFont.truetype(FONT_PATH, 16),
        )
    except IOError:
        print(f"⚠️  Font không tìm thấy tại {FONT_PATH}, dùng font mặc định.")
        default = ImageFont.load_default()
        return default, default, default


# ──────────────────────────────────────────
# 2. TELEMETRY
# ──────────────────────────────────────────
def prefetch_telemetry(window_start_sec: float, window_end_sec: float) -> pd.DataFrame:
    """
    Chỉ tải dữ liệu trong cửa sổ thời gian cần render (tiết kiệm RAM).
    window_start_sec / window_end_sec: Unix timestamp tính bằng GIÂY (khớp schema db_setup.py).
    """
    print("⏳ Đang tải Telemetry từ Database...")
    try:
        conn = sqlite3.connect(str(DATABASE_PATH), check_same_thread=False)
        query = """
            SELECT timestamp_sec, pid_name, value
            FROM obd_data
            WHERE timestamp_sec BETWEEN ? AND ?
            ORDER BY timestamp_sec ASC
        """
        df = pd.read_sql_query(query, conn, params=(window_start_sec, window_end_sec))
        conn.close()

        if df.empty:
            print("⚠️  Không có dữ liệu Telemetry trong khoảng thời gian này.")
            return pd.DataFrame()

        df_pivot = df.pivot_table(index="timestamp_sec", columns="pid_name", values="value")
        df_clean = df_pivot.ffill().bfill()
        print(f"✅ Đã tải {len(df_clean)} mốc Telemetry.")
        return df_clean
    except Exception as e:
        print(f"❌ Lỗi tải Telemetry: {e}")
        return pd.DataFrame()


def lookup_telemetry(df: pd.DataFrame, abs_time_sec: float):
    """Tìm hàng gần nhất trong DataFrame theo thời gian (giây)."""
    if df.empty:
        return 0, 0, 0, 0
    idx = int(np.abs(df.index - abs_time_sec).argmin())
    row = df.iloc[idx]
    def safe(col):
        v = row.get(col, 0)
        return 0 if pd.isna(v) else float(v)
    return safe("Vehicle Speed"), safe("Engine RPM"), safe("Throttle Position"), safe("Coolant Temp")


# ──────────────────────────────────────────
# 3. HUD RENDER
# ──────────────────────────────────────────
def render_glass_hud(frame: np.ndarray, speed, rpm, throttle, temp, fonts, abs_time_sec: float = 0) -> np.ndarray:
    """
    Tối ưu tốc độ: chỉ tạo layer nhỏ 320x200 rồi paste lên frame.
    Tránh alpha_composite toàn bộ 1920x1080 → giảm ~80% RAM băng thông Pillow.
    """
    font_large, font_medium, font_small = fonts

    HUD_W, HUD_H = 320, 200
    PASTE_X, PASTE_Y = 30, 30

    hud = Image.new("RGBA", (HUD_W, HUD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(hud)

    from datetime import datetime as _dt
    frame_dt = _dt.fromtimestamp(abs_time_sec) if abs_time_sec > 0 else _dt.now()
    now_str = frame_dt.strftime("%d/%m/%Y  %H:%M:%S")

    draw.rounded_rectangle((0, 0, HUD_W, HUD_H), radius=15, fill=(20, 20, 20, 160))
    draw.text((20, 10),  now_str,                                        font=font_small,  fill=(255, 220, 80, 255))
    draw.text((20, 35),  f"{int(speed)} km/h",                          font=font_large,  fill=(0, 255, 255, 255))
    draw.text((20, 90),  f"RPM: {int(rpm)}",                            font=font_medium, fill=(255, 255, 255, 255))
    draw.text((20, 128), f"Thr: {int(throttle)}%  Temp: {int(temp)}°C", font=font_small,  fill=(200, 200, 200, 255))

    rpm_ratio = min(rpm / 8000.0, 1.0)
    bar_color = (255, 50, 50, 255) if rpm > 6000 else (50, 255, 50, 255)
    draw.rectangle([20, 155, 300, 165], outline=(100, 100, 100, 255), width=1)
    draw.rectangle([20, 155, 20 + int(280 * rpm_ratio), 165], fill=bar_color)

    pil_frame = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    pil_frame.paste(hud, (PASTE_X, PASTE_Y), mask=hud)
    return cv2.cvtColor(np.array(pil_frame), cv2.COLOR_RGB2BGR)


# ──────────────────────────────────────────
# 4. TÌM FILE VÀ TÍNH WINDOW 2 PHÚT
# ──────────────────────────────────────────
def parse_ts_start_time(filepath: str) -> float | None:
    """
    Đọc timestamp từ tên file: cam_YYYYMMDD_HHMMSS.ts
    Trả về Unix timestamp (giây), hoặc None nếu không parse được.
    """
    stem = Path(filepath).stem          # "cam_20260528_153045"
    parts = stem.split("_", 1)          # ["cam", "20260528_153045"]
    if len(parts) < 2:
        return None
    try:
        dt = datetime.strptime(parts[1], TIMESTAMP_FMT)
        return dt.timestamp()
    except ValueError:
        return None


def get_video_duration(filepath: str) -> float:
    """Đọc thời lượng thực tế của file .ts bằng OpenCV (hỗ trợ file đang ghi dở)."""
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        return 0.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    # File đang ghi dở thường báo frame_count = 0 hoặc -1
    if frames <= 0:
        # Fallback: tính từ thời điểm tên file đến NOW
        start = parse_ts_start_time(filepath)
        if start:
            return max(0.0, time.time() - start)
        return 0.0
    return frames / fps


def collect_segments(storage_dir: Path, target_seconds: int) -> list[dict]:
    """
    Trả về danh sách dict đã sắp xếp CŨ → MỚI, mỗi phần tử gồm:
      { path, file_start_unix, duration, trim_start }
    trim_start: bỏ bao nhiêu giây đầu file (chỉ file cũ nhất mới có thể > 0).

    Ví dụ với 3 file 60s mỗi file và target=120s:
      - file3 (mới): toàn bộ 60s
      - file2: toàn bộ 60s
      → đủ 120s, file1 bị bỏ
    Nếu file3 chỉ có 45s (đang ghi):
      - file3: 45s
      - file2: 60s  → cần thêm 15s từ file1 → trim_start = 45s
    """
    pattern = str(storage_dir / CAM_GLOB_PATTERN)
    all_files = sorted(glob.glob(pattern), reverse=True)   # mới → cũ

    if not all_files:
        return []

    # Thu thập từ mới nhất ngược về
    segments = []
    total = 0.0

    for f in all_files:
        dur = get_video_duration(f)
        if dur <= 0:
            continue
        start_unix = parse_ts_start_time(f)
        if start_unix is None:
            continue

        needed = target_seconds - total
        take = min(dur, needed)
        trim_start = dur - take          # Cắt từ cuối → bỏ phần đầu

        segments.insert(0, {             # Chèn vào đầu để giữ thứ tự cũ→mới
            "path": f,
            "file_start_unix": start_unix,
            "duration": dur,
            "trim_start": trim_start,    # Giây bỏ đi ở đầu file này
            "take": take,
        })

        total += take
        if total >= target_seconds:
            break

    print(f"📂 Thu thập {len(segments)} file, tổng ~{total:.1f}s (mục tiêu {target_seconds}s)")
    for s in segments:
        print(f"   {Path(s['path']).name}  dur={s['duration']:.1f}s  trim={s['trim_start']:.1f}s  take={s['take']:.1f}s")

    return segments


# ──────────────────────────────────────────
# 5. FFMPEG PIPE OUTPUT
# ──────────────────────────────────────────
def open_ffmpeg_writer(output_path: str, width: int, height: int, fps: float) -> subprocess.Popen:
    """
    Nhận raw BGR frames từ stdin, mux ra .ts với codec copy-friendly.
    Dùng libx264 nhanh (preset ultrafast) vì ta đang vẽ lên frame mới,
    không thể copy stream gốc nữa.
    """
    tmp_path = output_path.replace(".mp4", "_tmp.ts")
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{width}x{height}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-threads", "2",
        "-crf", "28",
        "-pix_fmt", "yuv420p",  # Windows/mobile compatible (420p thay vì 444p mặc định)
        "-f", "mpegts",
        tmp_path,
    ]
    print(f"🚀 Mở FFmpeg pipe → {tmp_path} (tạm)")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    proc._tmp_path = tmp_path
    proc._final_path = output_path
    return proc


# ──────────────────────────────────────────
# 6. MAIN
# ──────────────────────────────────────────
def main():
    print("=" * 55)
    print("🎬  VDR OVERLAY ENGINE  –  2 phút gần nhất")
    print("=" * 55)

    storage_dir = Path(STORAGE_DIR)
    output_path = str(storage_dir / OUTPUT_FILENAME)

    # ── Bước 1: Thu thập segment ──────────────────────────
    segments = collect_segments(storage_dir, TARGET_SECONDS)
    if not segments:
        print("❌ Không tìm thấy file .ts nào trong", storage_dir)
        return

    # ── Bước 2: Xác định cửa sổ thời gian để tải Telemetry ──
    window_end_sec   = time.time()
    window_start_sec = window_end_sec - TARGET_SECONDS
    df_telemetry = prefetch_telemetry(window_start_sec, window_end_sec)

    # ── Bước 3: Lấy thông số video từ file đầu tiên ────────
    first_cap = cv2.VideoCapture(segments[0]["path"])
    width  = int(first_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(first_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps    = first_cap.get(cv2.CAP_PROP_FPS) or 30.0
    first_cap.release()

    if width == 0 or height == 0:
        print("❌ Không đọc được kích thước frame. Kiểm tra lại file .ts đầu tiên.")
        return

    print(f"📐 Độ phân giải: {width}x{height} @ {fps:.1f}fps")

    fonts = get_fonts()
    ffmpeg_proc = open_ffmpeg_writer(output_path, width, height, fps)

    # ── Bước 4: Render từng segment ────────────────────────
    total_frames_rendered = 0

    for seg in segments:
        path        = seg["path"]
        file_start  = seg["file_start_unix"]   # Unix giây
        trim_start  = seg["trim_start"]         # Giây bỏ đầu
        take        = seg["take"]               # Giây cần lấy

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            print(f"⚠️  Bỏ qua {Path(path).name} (không mở được)")
            continue

        seg_fps = cap.get(cv2.CAP_PROP_FPS) or fps
        skip_frames = int(trim_start * seg_fps)
        take_frames = int(take * seg_fps)

        # Tua đến frame bắt đầu
        if skip_frames > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, skip_frames)

        frames_read = 0
        while frames_read < take_frames:
            ret, frame = cap.read()
            if not ret:
                break

            # Timestamp tuyệt đối của frame này (giây, khớp timestamp_sec trong DB)
            current_frame_pos = skip_frames + frames_read
            abs_time_sec = file_start + current_frame_pos / seg_fps

            speed, rpm, throttle, temp = lookup_telemetry(df_telemetry, abs_time_sec)
            final_frame = render_glass_hud(frame, speed, rpm, throttle, temp, fonts, abs_time_sec)

            try:
                ffmpeg_proc.stdin.write(final_frame.tobytes())
            except BrokenPipeError:
                print("❌ FFmpeg pipe bị đứt.")
                cap.release()
                ffmpeg_proc.wait()
                return

            frames_read += 1
            total_frames_rendered += 1

            pct = (frames_read / take_frames * 100) if take_frames > 0 else 0
            print(f"  [{Path(path).name}] {pct:5.1f}% ({frames_read}/{take_frames})", end="\r", flush=True)

        cap.release()
        print()  # Xuống dòng sau mỗi file

    # ── Bước 5: Đóng FFmpeg pipe ──────────────────────────
    ffmpeg_proc.stdin.close()
    ffmpeg_proc.wait()

    # ── Bước 6: Convert .ts tạm → .mp4 chuẩn (faststart) ─
    tmp_path   = ffmpeg_proc._tmp_path
    final_path = ffmpeg_proc._final_path
    print(f"\n🔄 Convert {tmp_path} → {final_path} ...")
    convert_cmd = [
        "ffmpeg", "-y",
        "-i", tmp_path,
        "-c", "copy",
        "-movflags", "+faststart",
        final_path,
    ]
    subprocess.run(convert_cmd, stderr=subprocess.DEVNULL)
    os.remove(tmp_path)
    print(f"✅ Hoàn thành!  {total_frames_rendered} frames → {final_path}")


if __name__ == "__main__":
    main()