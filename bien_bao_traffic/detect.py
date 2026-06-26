import cv2
import numpy as np
import onnxruntime as ort
import time

# 1. Nạp mô hình từ folder data
model_path = "data/best.onnx"
session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])

# 2. Toàn bộ 54 danh mục nhãn chuẩn
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
            width = int(w * x_factor)
            height = int(h * y_factor)
            boxes.append([left, top, width, height])
            confidences.append(float(confidence))
            class_ids.append(class_id)
            
    indices = cv2.dnn.NMSBoxes(boxes, confidences, conf_thres, nms_thres)
    return indices, boxes, confidences, class_ids

# 3. Chạy test nhận diện ảnh
image_path = "data/test.jpg"
image = cv2.imread(image_path)
if image is None:
    print(f"❌ Không tìm thấy ảnh tại: {image_path}. Hãy kiểm tra lại folder data!")
    exit()

h_orig, w_orig, _ = image.shape

# Chuyển đổi hệ màu BGR sang RGB
image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

# Resize và chuẩn hóa dữ liệu đầu vào
blob = cv2.resize(image_rgb, (640, 640)).astype(np.float32) / 255.0
blob = np.transpose(blob, (2, 0, 1))
blob = np.expand_dims(blob, axis=0)

print("🚀 Đang chạy nhận diện biển báo trên CPU...")
start = time.time()
outputs = session.run(None, {session.get_inputs()[0].name: blob})
end = time.time()
print(f"⚡ Xong! Thời gian xử lý: {(end-start)*1000:.2f}ms")

# Xử lý hậu kỳ (ngưỡng tự tin mặc định 0.2)
indices, boxes, confidences, class_ids = postprocess(outputs, w_orig, h_orig, conf_thres=0.2, nms_thres=0.4)

# 4. Vẽ khung hình và nhãn chữ
for i in indices:
    if isinstance(i, np.ndarray): 
        i = i[0]
    box = boxes[i]
    label = classes.get(class_ids[i], f"ID: {class_ids[i]}")
    
    # Vẽ khung vuông màu xanh lá quanh biển báo phát hiện được
    cv2.rectangle(image, (box[0], box[1]), (box[0]+box[2], box[1]+box[3]), (0, 255, 0), 2)
    
    # Tạo chuỗi văn bản hiển thị: Tên_Biển_Báo + Tỉ lệ % tin cậy
    text_display = f"{label} {confidences[i]:.2f}"
    cv2.putText(image, text_display, (box[0], box[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

# 5. Lưu ảnh kết quả
cv2.imwrite("results/result.jpg", image)
print("✅ Hoàn thành! Đã lưu ảnh kết quả tiếng Việt tại: results/result.jpg")