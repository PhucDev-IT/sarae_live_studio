# CAPTCHA Monitor V1 — Windows

V1 **chỉ phát hiện sự hiện diện của hình ảnh CAPTCHA** qua ảnh chụp màn hình và gửi cảnh báo; **không tự giải, click hoặc kéo thanh**.

## Cài đặt
Tại thư mục `backend`:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Chạy frontend rồi mở `http://localhost:5173/#/verification-monitor`.

## Thử phát hiện

1. Mở TikTok LIVE Studio trên **màn hình chính**, không minimize và không bị ứng dụng khác che.
2. Chuẩn bị **ảnh mẫu cắt nhỏ** lấy từ chi tiết nhận diện CAPTCHA mà bạn có quyền sử dụng; nên chọn chi tiết giao diện ít thay đổi, không chọn vùng thử thách ngẫu nhiên (mảnh ghép/ảnh xoay). Đừng tải ảnh chứa thông tin cá nhân.
3. Upload một hoặc nhiều mẫu trong CAPTCHA Monitor. Mỗi ảnh được lưu vào `backend/data/verification_monitor/templates/` và được quét cùng các ảnh khác. Sau đó bấm **Bật Monitor**.
4. Khi xuất hiện một vùng màn hình đủ giống mẫu qua 2 lượt quét liên tiếp, Monitor ghi sự kiện `captcha_detected` và ảnh snapshot trong `backend/data/verification_monitor`.
5. Khi không còn khớp trong 2 lượt quét, Monitor ghi `captcha_cleared`. Trạng thái này **không có nghĩa CAPTCHA đã giải thành công**.

Bộ thư viện cho phép thêm/xóa nhiều mẫu và có thể tự đặt các file PNG được phép sử dụng vào `backend/data/verification_monitor/templates/` trước khi bật Monitor. Repo tham khảo `gbiz123/tiktok-live-studio-captcha-solver` chưa công bố LICENSE tại thời điểm đối chiếu; vì vậy **ảnh thuộc repo đó chưa được sao chép hoặc đóng gói vào Sarae**. Cần xác minh quyền sử dụng trước khi đưa ảnh của họ vào sản phẩm.

Đây là phép **template matching** đơn giản, không phải nhận diện CAPTCHA tổng quát. Nếu cửa sổ đổi kích thước, DPI hoặc giao diện, cần thay ảnh mẫu. Monitor không hỗ trợ cửa sổ bị che, chạy ẩn, minimization, nhiều màn hình, cũng không thể bảo đảm phát hiện mọi thử thách.

## Telegram

Tạo Telegram bot và lấy bot token, xác định `chat_id` hợp lệ, sau đó đặt trong `backend/.env`:

```env
SARAE_TELEGRAM_BOT_TOKEN=your_telegram_bot_token
```

Khởi động lại Backend; trên giao diện bật **Thông báo Telegram**, nhập chat ID và bấm **Lưu cấu hình**. Token không được nhập trong UI hoặc trả về từ API.

V1 chỉ gửi tin nhắn sự kiện; ảnh snapshot lưu cục bộ trên Backend. Không gửi ảnh hoặc cookie đến Telegram.

## API

- `GET /api/verification-monitor/status`
- `PUT /api/verification-monitor/config`
- `POST /api/verification-monitor/template` (thêm mẫu, không ghi đè)
- `GET /api/verification-monitor/templates`
- `DELETE /api/verification-monitor/templates/{name}`
- `POST /api/verification-monitor/start`
- `POST /api/verification-monitor/stop`

Giới hạn: Bộ nhớ sự kiện chỉ tồn tại trong tiến trình Backend, ảnh snapshot được lưu vào đĩa. Chạy **một Backend worker** trên Windows Desktop. Không dùng trong máy chủ headless hoặc nhiều Uvicorn workers.
