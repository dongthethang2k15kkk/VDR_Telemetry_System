import cv2
import numpy as np
import onnxruntime as ort
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

# --- 1. Cấu hình AI và Mô hình ---
model_path = "data/best.onnx"

# Tối ưu cấu hình luồng cho CPU Orange Pi 4 Pro
opts = ort.SessionOptions()
opts.intra_op_num_threads = 2  # Để 2 luồng để tránh tranh chấp tài nguyên với camera
session = ort.InferenceSession(model_path, sess_options=opts, providers=['CPUExecutionProvider'])

classes = {
    0: 'cam_cac_loai_xe_3_banh', 1: 'cam_do_xe', 2: 'cam_dung_xe_va_do_xe', 3: 'cam_o_to', 
    4: 'cam_o_to_quay_dau_xe', 5: 'cam_o_to_re_phai', 6: 'cam_o_to_re_trai', 7: 'cam_o_to_tai', 
    8: 'cam_o_to_tai_trong_luong_qua_10t', 9: 'cam_o_to_tai_trong_luong_qua_2-5t', 
    10: 'cam_o_to_tai_va_xe_khach', 11: 'cam_quay_dau_xe', 12: 'cam_re_phai', 13: 'cam_re_trai', 
    14: 'cam_re_trai_va_phai', 15: 'cam_su_dung_coi', 16: 'cam_vuot', 
    17: 'cam_xe_2_banh_va_xe_3_banh_co_dong_co', 18: 'cam_xe_di_nguoc_chieu', 19: 'cho', 
    20: 'cho_ngoat_nguy_hiem_ben_phai', 21: 'cho_ngoat_nguy_hiem_ben_trai', 
    22: 'cho_ngoat_nguy_hiem_lien_tiep', 23: 'cho_quay_xe', 24: 'chu_y_nguoi_di_bo_cat_ngang', 
    25: 'chu_y_nguoi_di_xe_dap_cat_ngang', 26: 'chu_y_tre_em', 27: 'cong_truong', 28: 'di_cham', 
    29: 'duong_cho_nguoi_di_bo', 30: 'duong_cho_xe_o_to', 31: 'duong_co_o_ga', 
    32: 'duong_co_song_map_mo_nhan_tao', 33: 'duong_het_uu_tien', 34: 'duong_mot_chieu', 
    35: 'duong_nguoi_di_bo_sang_ngang', 36: 'duong_uu_tien', 37: 'giao_nhau_co_tin_hieu_den', 
    38: 'giao_nhau_voi_duong_khong_uu_tien', 39: 'giao_nhau_voi_duong_uu_tien', 40: 'han_che_chieu_cao', 
    41: 'huong_di_tren_moi_lan_duong_theo_vach_ke_duong', 42: 'huong_phai_di_vuot_chuong_ngai_vat_phai', 
    43: 'huong_phai_di_vuot_chuong_ngai_vat_trai', 44: 'khac', 45: 'khu_vuc_quay_xe', 
    46: 'lan_duong_cho_tung_xe_theo_vach_ke_duong', 47: 'nguy_hiem_khac', 48: 'noi_giao_nhau_chay_theo_vong', 
    49: 'object', 50: 'toc_do_toi_da_40', 51: 'toc_do_toi_da_50', 52: 'toc_do_toi_da_60', 53: 'toc_do_toi_da_80'
}

def postprocess(outputs, img_w, img_h, conf_thres=0.2, nms_thres=0.4):
    predictions = np.squeeze(outputs[0]).T
    boxes, confidences, class_ids = [], [], []
    x_factor, y_factor = img_w / 640, img_h / 640
    for pred in predictions:
        scores = pred[4:]
        class_id = np.argmax(scores)
        confidence = scores[class_id]
        if confidence > conf_thres:
            xc, yc, w, h = pred[0], pred[1], pred[2], pred[3]
            left = int((xc - w/2) * x_factor)
            top = int((yc - h/2) * y_factor)
            boxes.append([left, top, int(w * x_factor), int(h * y_factor)])
            confidences.append(float(confidence))
            class_ids.append(class_id)
    indices = cv2.dnn.NMSBoxes(boxes, confidences, conf_thres, nms_thres)
    return indices, boxes, confidences, class_ids

# --- 2. Biến toàn cục chia sẻ luồng ---
latest_raw_frame = None       
current_detections = []       
ai_fps = 0.0
data_lock = threading.Lock()

# --- 3. Luồng đọc dữ liệu từ Camera IP ---
def camera_reader_thread(rtsp_url):
    global latest_raw_frame
    print("🔄 Đang cố gắng kết nối tới luồng RTSP...")
    cap = cv2.VideoCapture(rtsp_url)
    
    while True:
        ret, frame = cap.read()
        if not ret:
            print("⚠️ Mất tín hiệu RTSP, đang kết nối lại...")
            time.sleep(2)
            cap.open(rtsp_url)
            continue
        with data_lock:
            latest_raw_frame = frame.copy()
        time.sleep(0.01)  # Nghỉ cực ngắn để ổn định luồng

# --- 4. Web Server Livestream ---
class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global latest_raw_frame, current_detections, ai_fps
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            try:
                while True:
                    # Lấy dữ liệu an toàn tránh xung đột luồng
                    with data_lock:
                        if latest_raw_frame is None:
                            frame_to_show = None
                        else:
                            frame_to_show = latest_raw_frame.copy()
                            detections = list(current_detections)
                            current_fps = ai_fps

                    # Nếu chưa có ảnh từ camera, ngủ 100ms rồi kiểm tra lại (Sửa lỗi nghẽn mạch)
                    if frame_to_show is None:
                        time.sleep(0.1)
                        continue

                    # Vẽ kết quả AI đè lên khung hình mượt
                    for (box, label, conf) in detections:
                        cv2.rectangle(frame_to_show, (box[0], box[1]), (box[0]+box[2], box[1]+box[3]), (0, 255, 0), 2)
                        cv2.putText(frame_to_show, f"{label} {conf:.2f}", (box[0], box[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

                    cv2.putText(frame_to_show, f"AI Process: {current_fps:.1f} FPS", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    cv2.putText(frame_to_show, "Stream: LIVE (Smooth)", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                    # Encode JPG gửi lên web
                    ret, jpeg = cv2.imencode('.jpg', frame_to_show)
                    if not ret: 
                        continue
                    
                    self.wfile.write(b'--frame\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', str(len(jpeg.tobytes())))
                    self.end_headers()
                    self.wfile.write(jpeg.tobytes())
                    self.wfile.write(b'\r\n')
                    time.sleep(0.03)  # Giữ luồng hiển thị mượt ~30 FPS
            except Exception as e:
                pass

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer): 
    pass

def start_web_server():
    server = ThreadedHTTPServer(('0.0.0.0', 5000), StreamingHandler)
    print("🌐 Web Server đã sẵn sàng tại cổng :5000")
    server.serve_forever()

# --- 5. Luồng xử lý AI chạy ngầm ---
def ai_inference_loop():
    global latest_raw_frame, current_detections, ai_fps
    print("🚀 Luồng AI đã bắt đầu kích hoạt...")
    
    while True:
        with data_lock:
            if latest_raw_frame is None:
                frame_to_process = None
            else:
                frame_to_process = latest_raw_frame.copy()

        # Nếu chưa có ảnh từ camera, ngủ 20ms rồi kiểm tra lại (Sửa lỗi nghẽn mạch)
        if frame_to_process is None:
            time.sleep(0.02)
            continue

        h_orig, w_orig, _ = frame_to_process.shape
        frame_rgb = cv2.cvtColor(frame_to_process, cv2.COLOR_BGR2RGB)
        blob = cv2.resize(frame_rgb, (640, 640)).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))
        blob = np.expand_dims(blob, axis=0)

        start_time = time.time()
        outputs = session.run(None, {session.get_inputs()[0].name: blob})
        local_fps = 1.0 / (time.time() - start_time)

        indices, boxes, confidences, class_ids = postprocess(outputs, w_orig, h_orig, conf_thres=0.2, nms_thres=0.4)

        new_detections = []
        for i in indices:
            if isinstance(i, np.ndarray): i = i[0]
            box = boxes[i]
            label = classes.get(class_ids[i], f"ID: {class_ids[i]}")
            new_detections.append((box, label, confidences[i]))

        with data_lock:
            current_detections = new_detections
            ai_fps = local_fps
        
        time.sleep(0.01)  # Giảm tải nhiệt độ CPU

if __name__ == '__main__':
    camera_rtsp_url = "rtsp://10.10.10.20:554/user=admin&password=&channel=1&stream=0.sdp"
    
    # Kích hoạt luồng đọc cam và luồng web server
    threading.Thread(target=camera_reader_thread, args=(camera_rtsp_url,), daemon=True).start()
    threading.Thread(target=start_web_server, daemon=True).start()
    
    try:
        ai_inference_loop()
    except KeyboardInterrupt:
        print("\n🛑 Đã tắt hệ thống gọn gàng!")