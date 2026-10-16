# LIVE Watchdog & Recovery POC

Các thay đổi dùng OBS WebSocket media cursor (millisecond) để phát hiện video đang ở trạng thái PLAYING nhưng thời gian phát không tiến.

## Cơ chế
- Poll OBS mỗi 1 giây khi playlist đang chạy.
- Nếu OBS trả `media_cursor` và vẫn báo PLAYING nhưng cursor không tiến ít nhất 20 giây: thử RESTART Media Source.
- Tối đa 2 lần restart khi không thấy cursor tiến trở lại; sau đó dừng monitor để tránh loop restart vô hạn.
- Nếu mất OBS WebSocket: retry 5 lần theo backoff hiện tại; nếu kết nối hồi phục, theo dõi tiếp nguồn đang phát.
- Ghi thống kê phiên vào RAM: thời gian bắt đầu, số lần chuyển video, số lỗi WebSocket, số lần thực hiện recovery. Bảng hiển thị ở LIVE Control.
- Thống kê sẽ reset khi bắt đầu một playlist mới hoặc khởi động lại Backend. Playlist lưu SQLite không thay đổi.
- Video từ Link Local Folder vẫn đọc trực tiếp đường dẫn gốc khi OBS play.

## Kiểm thử
1. `git pull origin dev`, để FastAPI --reload và Vite HMR cập nhật.
2. LIVE Control: chạy playlist 3 video có Loop, quan sát tăng bộ đếm chuyển video và thời gian phiên.
3. Tắt OBS WebSocket Server trong thời gian ngắn rồi bật lại, quan sát Retry và Recovery.
4. Thử một video bị treo thực sự: chỉ khi OBS trả media_cursor thì watchdog mới kích hoạt; không dùng Pause làm cách giả lập treo.
5. Dừng bằng Stop, đợi trên 20 giây: watchdog không được tự phát lại.
6. Thử video local và uploaded.

## Ranh giới
- Đây là watchdog kiểm tra cursor, **không** phân tích khung hình, âm thanh, hay GPU; một video phát ra khung hình tĩnh nhưng cursor vẫn tăng sẽ không bị coi là treo.
- Trạng thái PAUSED/STOPPED không bị tự restart; tránh phá thao tác tạm dừng thủ công.
- OBS bị tắt hẳn, scene bị xóa, stream bị ngắt lâu, hoặc mạng lỗi kéo dài vẫn cần xử lý thủ công. Không tự StartStream hoặc can thiệp TikTok.
- Watchdog chỉ phù hợp chạy trên một tiến trình FastAPI (không --workers), localhost đáng tin cậy.
- Code đã push nhưng chưa được kiểm chứng bằng OBS Windows, vì vậy đây vẫn là bản POC.
