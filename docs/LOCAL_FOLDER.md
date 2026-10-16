# Media Library: Upload MP4 hoặc Link Local Folder

Có hai cách đưa video vào thư viện:

1. **Upload MP4:** Chọn file từ browser, FastAPI lưu bản sao trong `backend/data/media`; xóa bản ghi sẽ xóa file đã upload.
2. **Link Local Folder:** Nhập đường dẫn folder **trên máy đang chạy FastAPI/OBS**, ví dụ `D:\\Sarae\\Videos`; Backend quét MP4 và chỉ lưu đường dẫn/metadata, **không copy**. Xóa video khỏi thư viện **không xóa file gốc**.

## Update

```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Web đang chạy Vite sẽ tự cập nhật source. Nếu không, chạy `npm.cmd run dev` trong web/.

## Kiểm thử

- Thư viện video → **Link Local Folder** → nhập đường dẫn thực tế trên máy Backend → Quét và liên kết.
- Sau khi quét, video có nhãn **Local**. Có thể Preview, chỉnh sửa product/presenter/tags, đưa vào playlist và phát OBS.
- Quét lại folder sẽ không thêm trùng cùng một đường dẫn.
- Xóa bản ghi Local sẽ không đụng đến file MP4 gốc.
- Test cả upload như trước để xác nhận không bị ảnh hưởng.
- Đổi tên/di chuyển file local sẽ làm Preview/OBS báo file không tồn tại; quét lại folder để thêm đường dẫn mới.

## Giới hạn phase này

- Chỉ MP4; tối đa 2000 video trên một lượt quét; hỗ trợ quét thư mục con.
- Đường dẫn phải tồn tại trên máy Backend. Browser không được trực tiếp cấp quyền đọc ổ đĩa của máy khác.
- Để phục vụ OBS, Backend và OBS cần chạy trên cùng máy hoặc nhìn thấy cùng đường dẫn qua filesystem.
- API quét folder hiện chỉ thích hợp cho môi trường **localhost đáng tin cậy**, do chưa có xác thực/quản lý danh sách đường dẫn được phép; không expose Backend ra Internet.
- Chưa có watcher tự quét file mới/xóa, đồng bộ đổi tên, hoặc đồng bộ folder theo lịch.
- Chưa kiểm tra build trên máy Windows của người dùng.
