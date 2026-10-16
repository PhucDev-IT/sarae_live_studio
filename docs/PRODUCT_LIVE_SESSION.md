# Product Video Queue & LIVE Session — POC

## Mô hình
- **Manual Playlist** vẫn dùng được, như trước.
- **Product Session** lấy video từ Media Library đã liên kết qua `product_id`. Một sản phẩm có nhiều video; chỉ lấy file còn tồn tại (Upload hoặc Link Local).
- Trong Product Session, Loop luôn bật theo sản phẩm hiện tại.
- **Chuyển ngay**: yêu cầu chuyển sang video đầu tiên của sản phẩm mới.
- **Chuyển sau video hiện tại**: lưu một `pending_product_id`, chỉ thực hiện khi OBS báo `OBS_MEDIA_STATE_ENDED`. Thay lựa chọn lần nữa sẽ ghi đè yêu cầu chờ; có nút Hủy chuyển chờ.
- Khi video kết thúc và không có yêu cầu chuyển sản phẩm, engine truy vấn lại Media Library: video mới liên kết với sản phẩm sẽ xuất hiện từ lượt kế tiếp, video bị gỡ sẽ bị loại khỏi queue.
- Pause, Resume, Seek, Next/Previous, Play Selected và Watchdog vẫn dùng cùng playback engine; Next khi có yêu cầu chuyển sẽ thực hiện chuyển sản phẩm chờ.
- State của phiên đang giữ trong một tiến trình FastAPI, không persist qua Backend restart. Chưa tự bật TikTok LIVE/OBS streaming.

## Test
1. Vào Media Library: gán ít nhất **2 video cho sản phẩm A**, **2 video cho sản phẩm B** (có thể gồm video upload và local).
2. `git pull origin dev`; để uvicorn `--reload` và Vite HMR nhận bản mới.
3. OBS kết nối bình thường. Trong LIVE Control → **LIVE theo sản phẩm** → chọn A → **Bắt đầu phiên**.
4. Kiểm tra A phát video theo vòng, không sang B nếu không chọn.
5. Chọn B → **Chuyển ngay**: OBS cần phát video đầu B; session không kết thúc.
6. Chuyển về A; chọn B → **Chuyển sau video**: video A đang phát tiếp, khi kết thúc sẽ sang B. Thử **Hủy chuyển chờ**.
7. Thử nút Next khi đang có chuyển chờ: chuyển sang sản phẩm chờ.
8. Thêm/gỡ một video khỏi A tại Media Library, đợi đến ranh giới video: queue sẽ cập nhật. Nút Select đọc queue hiện tại.
9. Stop phiên, thử Playlist thủ công cũ để kiểm tra tương thích.

## Lưu ý / giới hạn
- Khi chuyển sản phẩm bằng A/B có thể nháy: deferred tới phase tối ưu.
- Dùng 1 tiến trình FastAPI; reload backend sẽ mất session đang chạy.
- Không có lịch sử Session lưu DB, không có Product Queue order tùy chỉnh; hiện thứ tự video theo Media ID.
- Không tự khởi động streaming. POC chỉ điều khiển OBS nguồn video.
- **Chưa được chạy kiểm thử OBS/Windows thực tế từ môi trường phát triển này.**
