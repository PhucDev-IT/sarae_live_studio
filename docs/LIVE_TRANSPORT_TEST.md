# LIVE transport & playlist editing (POC)

## Cập nhật
`git pull origin dev`. Backend `uvicorn --reload`, Web Vite HMR tự nhận thay đổi. Không có migration mới.

## Playlist
- Trong **Playlists**, nhấn biểu tượng bút để sửa tên, thêm/xóa video và dùng mũi tên để thay đổi thứ tự. Nhấn **Lưu thay đổi**.
- Playlist đang chạy không thể sửa. Dừng trước khi sửa để tránh làm sai thứ tự đang phát.
- Dữ liệu lưu SQLite như trước.

## LIVE Control
- Pause / Resume điều khiển Media Source đang hiển thị.
- Previous về video trước, hoặc về video cuối nếu ở đầu playlist và Loop bật.
- Phát video trong playlist: chọn một vị trí để chuyển ngay sang video đó.
- Seek nhập số giây muốn tua. Seek chỉ dùng khi video đang phát và không vượt thời lượng khi có metadata.
- Next / Stop / Loop giữ nguyên.

## Test trên OBS
1. Tạo playlist 3 video, sửa playlist đã tạo và xác nhận thay đổi có lưu.
2. Start rồi Pause và Resume; OBS cần giữ đúng vị trí.
3. Next, Previous, Play Selected đến vị trí bất kỳ.
4. Seek 15 giây (video đủ dài), sau đó Seek quá thời lượng để xem thông báo lỗi.
5. Bật A/B và lặp lại các thao tác.
6. Bật Loop, chờ hết playlist và quan sát auto-next.

## Giới hạn
- Web hiện chỉ nhập Seek bằng giây; thanh kéo tiến độ realtime chưa triển khai.
- Không tự tạo stream hay tự phát TikTok.
- Chưa test chạy trực tiếp trên OBS Windows của người dùng.
