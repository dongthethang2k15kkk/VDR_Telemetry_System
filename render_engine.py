#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_engine.py  (chay TREN SERVER, trong Docker render)
NAC 2 — render that:
  Input: 1 video da gop san (Pi cat) + obd.json dang [{t,speed,rpm,throttle,temp}]
  Lam:  mo video bang cv2 -> moi frame tinh t=frame_idx/fps -> tra obd gan nhat
        -> ve HUD bang render_glass_hud (tai dung tu overlay_engine) -> xuat mp4 (ffmpeg)
Khong dung DB, khong quet thu muc. Chi xu ly dung goi truyen vao.
"""
import os
import json
import subprocess

import cv2
import numpy as np
from PIL import ImageFont

# Tai dung dung ham ve HUD cua Pi (khong sua overlay_engine)
from overlay_engine import render_glass_hud

FONT_PATH = os.environ.get("FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _get_fonts():
    try:
        return (
            ImageFont.truetype(FONT_PATH, 40),
            ImageFont.truetype(FONT_PATH, 24),
            ImageFont.truetype(FONT_PATH, 16),
        )
    except IOError:
        d = ImageFont.load_default()
        return d, d, d


def _load_obd(obd_path):
    """Doc obd.json dang [{t,speed,rpm,throttle,temp}], sap xep theo t."""
    try:
        with open(obd_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        data.sort(key=lambda r: r.get("t", 0))
        return data
    except Exception as e:
        print(f"⚠️  [ENGINE] Loi doc obd.json: {e}")
        return []


def _lookup(obd, t):
    """Tra ban ghi obd co t gan nhat voi thoi diem frame (giay)."""
    if not obd:
        return 0, 0, 0, 0
    best = min(obd, key=lambda r: abs(r.get("t", 0) - t))
    return (best.get("speed", 0), best.get("rpm", 0),
            best.get("throttle", 0), best.get("temp", 0))


def _open_ffmpeg(output_path, width, height, fps):
    """Pipe frame BGR raw vao ffmpeg -> mp4 (H.264)."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        output_path,
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def render(video_path, obd_path, output_path):
    """Render HUD len video da gop san. Tra True/False."""
    obd = _load_obd(obd_path)
    fonts = _get_fonts()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"⚠️  [ENGINE] Khong mo duoc video: {video_path}")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS) or 12.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width == 0 or height == 0:
        print(f"⚠️  [ENGINE] Video kich thuoc 0, bo qua.")
        cap.release()
        return False

    writer = _open_ffmpeg(output_path, width, height, fps)
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = idx / fps
            speed, rpm, throttle, temp = _lookup(obd, t)
            frame = render_glass_hud(frame, speed, rpm, throttle, temp, fonts, abs_time_sec=0)
            writer.stdin.write(frame.tobytes())
            idx += 1
    finally:
        cap.release()
        if writer.stdin:
            writer.stdin.close()
        writer.wait()

    ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
    print(f"🎬 [ENGINE] Render {idx} frame -> {output_path} ({'OK' if ok else 'RONG'})")
    return ok