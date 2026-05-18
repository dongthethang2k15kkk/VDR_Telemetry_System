#test_system.py
import sys
import os

def verify_setup():
    print("🔍 ĐANG KIỂM TRA HỆ THỐNG...")
    
    # 1. Check Python version
    print(f"🐍 Python version: {sys.version.split()[0]}")
    
    # 2. Check thư viện
    try:
        import can
        import pandas
        import cv2
        print("✅ Các thư viện quan trọng: OK")
    except ImportError as e:
        print(f"❌ Thiếu thư viện: {e}")
        return

    # 3. Check cấu trúc thư mục
    from config import STORAGE_DIR, BASE_DIR
    if STORAGE_DIR.exists():
        print(f"📁 Thư mục lưu trữ: {STORAGE_DIR} -> OK")
    else:
        print("⚠️ Thư mục data chưa có, sẽ được tạo khi chạy.")

    print("\n🚀 HỆ THỐNG SẴN SÀNG ĐỂ CHẠY SIMULATION!")

if __name__ == "__main__":
    verify_setup()