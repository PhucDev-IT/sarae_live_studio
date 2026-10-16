# Thử chuyển video A/B trên OBS (Double Buffering)

Đây là cơ chế thử nghiệm giảm nháy khi chuyển MP4. Giữ nguyên chế độ 1 source cũ để so sánh. **OBS và Backend chạy trên cùng máy**.

1. OBS → Sources → tạo **2 Media Sources** trong **cùng scene đang hiển thị**: `Sarae Video A` và `Sarae Video B` (chính xác dấu cách và chữ hoa).
2. Trong Properties của **cả hai nguồn**: chọn MP4 local bất kỳ để khởi tạo; **tắt Loop**; nếu có mục "Close file when inactive", tắt mục đó. Để nguồn hiển thị ban đầu thế nào cũng được; backend quản lý visibility khi bắt đầu phát.
3. Cập nhật repo, khởi động lại Backend và Web.
4. LIVE Control → giữ tên cơ sở **Sarae Video** → bật checkbox **Phát A/B** → Kết nối / Kiểm tra.
5. Chọn playlist có từ 3 MP4 trở lên, nhấn Phát thử, Next, Next và chờ tự chuyển. So sánh độ nháy với chế độ A/B tắt.
6. Test âm thanh: chỉ một nguồn được unmute tại một thời điểm, nhưng vẫn cần nghe thực tế xem có tiếng đè hoặc ngắt không.

Khi A/B bật, backend phát nguồn đang hiển thị, chuẩn bị MP4 kế tiếp ở nguồn ẩn, khi Next sẽ hiển thị nguồn được chuẩn bị trước rồi ẩn nguồn cũ. Cả hai Media Sources phải ở scene hiện tại; nếu không có đủ, nút Phát thử báo lỗi 422.

**Giới hạn:** đây là thử nghiệm scene-item switching, không phải OBS transition/fade chuẩn. Video đầu tiên vẫn có thể cần thời gian giải mã. Chưa đảm bảo không nháy trong mọi trường hợp hoặc đồng bộ âm thanh hoàn hảo. Nếu thấy khung đen, hãy gửi video màn hình OBS Preview và log Backend để kiểm tra. Chưa áp dụng phát streaming lên TikTok.
