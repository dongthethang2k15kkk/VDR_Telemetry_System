import cv2
import sqlite3
import pandas as pd
import numpy as np
import time
import os
from PIL import Image, ImageDraw, ImageFont
from config import DATABASE_PATH, VIDEO_SOURCE, OPERATION_MODE, OUTPUT_VIDEO_PATH, FONT_PATH

def get_fonts():
    """Tải font từ file cấu hình, fallback về mặc định nếu lỗi"""
    try:
        font_large = ImageFont.truetype(FONT_PATH, 40)
        font_medium = ImageFont.truetype(FONT_PATH, 24)
        font_small = ImageFont.truetype(FONT_PATH, 16)
    except IOError:
        print(f"⚠️ Không tìm thấy font tại {FONT_PATH}, dùng font mặc định.")
        font_large = font_medium = font_small = ImageFont.load_default()
    return font_large, font_medium, font_small

def prefetch_telemetry_data():
    """Tải và đồng bộ (Pivot) toàn bộ dữ liệu SQLite lên RAM 1 lần duy nhất"""
    print("⏳ Đang tải dữ liệu Telemetry từ Database...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, uri=True, check_same_thread=False)
        df = pd.read_sql_query("SELECT timestamp_ms, pid_name, value FROM obd_data ORDER BY timestamp_ms ASC", conn)
        conn.close()

        if df.empty:
            print("⚠️ Cảnh báo: Database đang trống!")
            return pd.DataFrame()

        # Pivot bảng và lấp đầy lỗ hổng dữ liệu (ffill/bfill)
        df_pivot = df.pivot_table(index='timestamp_ms', columns='pid_name', values='value')
        df_clean = df_pivot.ffill().bfill()
        print(f"✅ Đã tải xong {len(df_clean)} mốc thời gian.")
        return df_clean
    except Exception as e:
        print(f"❌ Lỗi tải dữ liệu: {e}")
        return pd.DataFrame()

def render_glass_hud(frame, speed, rpm, throttle, temp, fonts):
    """Vẽ giao diện HUD đè lên khung hình bằng Pillow"""
    font_large, font_medium, font_small = fonts
    
    cv2_im_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_im = Image.fromarray(cv2_im_rgb).convert('RGBA')
    
    hud_w, hud_h = 320, 200
    hud_x, hud_y = 30, 30
    
    hud_layer = Image.new('RGBA', pil_im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(hud_layer)
    
    # 1. Vẽ nền kính mờ (Glass Morphism) bo góc 15px
    draw.rounded_rectangle((hud_x, hud_y, hud_x + hud_w, hud_y + hud_h), radius=15, fill=(20, 20, 20, 160))
    
    # 2. Điền text (Ép kiểu int để bỏ phần thập phân thừa)
    draw.text((hud_x + 20, hud_y + 20), f"{int(speed)} km/h", font=font_large, fill=(0, 255, 255, 255))
    draw.text((hud_x + 20, hud_y + 80), f"RPM: {int(rpm)}", font=font_medium, fill=(255, 255, 255, 255))
    draw.text((hud_x + 20, hud_y + 120), f"Thr: {int(throttle)}% | Temp: {int(temp)}°C", font=font_small, fill=(200, 200, 200, 255))
    
    # 3. Thanh cảnh báo vòng tua (Đỏ nếu vượt 6000 RPM)
    bar_x, bar_y = hud_x + 20, hud_y + 155
    bar_w, bar_h = 280, 10
    rpm_ratio = min(rpm / 8000.0, 1.0)
    fill_w = int(bar_w * rpm_ratio)
    
    bar_color = (255, 50, 50, 255) if rpm > 6000 else (50, 255, 50, 255)
    draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline=(100, 100, 100, 255), width=1)
    draw.rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], fill=bar_color)

    # 4. Trộn 2 lớp ảnh và trả về định dạng OpenCV
    pil_im = Image.alpha_composite(pil_im, hud_layer)
    return cv2.cvtColor(np.array(pil_im), cv2.COLOR_RGBA2BGR)

def get_hardware_video_writer(output_path, fps, width, height):
    """Trình xuất video hỗ trợ Hardware Acceleration cho Orange Pi"""
    if OPERATION_MODE == "PRODUCTION":
        print("🚀 Kích hoạt mpph264enc (Hardware VPU) cho Rockchip...")
        gst_str = f"appsrc ! videoconvert ! mpph264enc ! h264parse ! mp4mux ! filesink location={output_path}"
        return cv2.VideoWriter(gst_str, cv2.CAP_GSTREAMER, 0, fps, (width, height))
    else:
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        return cv2.VideoWriter(output_path, fourcc, fps, (width, height))

def main():
    print("🎬 KHỞI ĐỘNG HỆ THỐNG RENDER HUD...")
    
    # 1. Kiểm tra đầu vào Video
    cap = cv2.VideoCapture(VIDEO_SOURCE)
    if not cap.isOpened():
        print(f"❌ LỖI: Không thể mở nguồn video tại: {VIDEO_SOURCE}")
        return

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or pd.isna(fps): fps = 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if width == 0 or height == 0:
        print("❌ LỖI BỘ NHỚ: Video rỗng (Kích thước 0x0). Hãy kiểm tra lại file .mp4!")
        return

    # 2. Tải cấu hình và Dữ liệu
    fonts = get_fonts()
    df_telemetry = prefetch_telemetry_data()
    
    actual_start_time = 0
    if not df_telemetry.empty:
        actual_start_time = df_telemetry.index.min()

    # 3. Thiết lập đầu ra
    os.makedirs(os.path.dirname(OUTPUT_VIDEO_PATH), exist_ok=True)
    out = get_hardware_video_writer(OUTPUT_VIDEO_PATH, fps, width, height)
    print(f"⏺️ Đang xuất video ra: {OUTPUT_VIDEO_PATH}")

    frame_idx = 0

    # 4. Vòng lặp Render Khung hình
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Tính Unix Timestamp của khung hình hiện tại để gióng với Database
        if OPERATION_MODE == "SIMULATION":
            abs_time = actual_start_time + (frame_idx / fps)
        else:
            abs_time = time.time()

        speed, rpm, throttle, temp = 0, 0, 0, 0
        
        # Tìm dữ liệu gần nhất trong DataFrame
        if not df_telemetry.empty:
            closest_idx = (np.abs(df_telemetry.index - abs_time)).argmin()
            row = df_telemetry.iloc[closest_idx]
            speed = row.get('Vehicle Speed', 0)
            rpm = row.get('Engine RPM', 0)
            throttle = row.get('Throttle Position', 0)
            temp = row.get('Coolant Temp', 0)

        # Fix triệt để lỗi giá trị trống (NaN) sinh ra từ pandas làm crash phần text
        speed = 0 if pd.isna(speed) else speed
        rpm = 0 if pd.isna(rpm) else rpm
        throttle = 0 if pd.isna(throttle) else throttle
        temp = 0 if pd.isna(temp) else temp

        # Gọi hàm Render theo bộ xương sườn
        final_frame = render_glass_hud(frame, speed, rpm, throttle, temp, fonts)
        out.write(final_frame)
        
        frame_idx += 1
        
        if total_frames > 0:
            print(f"Render: {(frame_idx/total_frames)*100:5.1f}% ({frame_idx}/{total_frames} frames)    ", end="\r", flush=True)
        else:
            print(f"Đang xử lý Live Stream... {frame_idx} frames    ", end="\r", flush=True)

    cap.release()
    out.release()
    print("\n✅ Hoàn thành Render Video!")

if __name__ == "__main__":
    main()