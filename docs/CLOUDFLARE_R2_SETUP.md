# Cloudflare R2 — Sarae LIVE Studio

## Tạo kho riêng tư

1. Đăng ký Cloudflare, mở **R2 Object Storage**, kích hoạt R2 theo hướng dẫn Cloudflare (tài khoản có thể cần thông tin thanh toán).
2. Chọn **Create bucket**, đặt tên ví dụ `sarae-media`. Không bật Public Development URL (`r2.dev`) hoặc gắn public custom domain. Sarae dùng bucket private.
3. Trong R2, chọn **Manage R2 API Tokens**, tạo token với **Object Read & Write**, chọn **Apply to specific buckets only**, chỉ chọn bucket Sarae.
4. Lưu **Access Key ID** và **Secret Access Key** được cấp cho S3 clients. Đây là cặp khóa S3, không phải chuỗi Cloudflare API bearer token. Secret chỉ hiển thị một lần.
5. Sao chép Account ID và S3 API endpoint. Endpoint thông thường là `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`; bucket có jurisdiction EU dùng endpoint `.eu` do Cloudflare cung cấp.

Tham khảo chính thức: [R2 S3 setup](https://developers.cloudflare.com/r2/get-started/s3/), [multipart upload](https://developers.cloudflare.com/r2/objects/upload-objects/), [R2 limits](https://developers.cloudflare.com/r2/platform/limits/).

## Cấu hình Sarae

Backend cài `backend/requirements.txt` (bao gồm boto3). Chạy schema/Alembic theo hướng dẫn dự án hiện có; R2 tái sử dụng bảng storage credentials, không cần đổi schema.

Đăng nhập SUPER_ADMIN, mở `/#/admin/storage`:

1. Nhập Account ID, Bucket Name, Access Key ID, Secret Access Key.
2. Để Endpoint trống để tự sinh, Region mặc định `auto`.
3. Nhấn **Lưu cấu hình**, sau đó **Kiểm tra kết nối**. Khi thành công trạng thái là CONNECTED.
4. Khách hàng mở Thư viện video và upload như bình thường. MP4, metadata, cover, gắn nhiều video/sản phẩm và thứ tự video giữ API cũ.

Hai khóa không được trả về API. Khi thay cấu hình, nhập lại cả hai. Backend mã hóa cấu hình bằng Fernet hiện có. Production phải cung cấp `SARAE_CREDENTIAL_KEY` ổn định và sao lưu khóa cùng DB; nếu không cấu hình, máy đơn sử dụng `backend/data/credential.key`. Không đưa khóa vào Git hoặc Agent.

**Kiểm tra kết nối** gọi HeadBucket, xác nhận bucket có thể truy cập. Upload/read/delete vẫn phụ thuộc đầy đủ quyền Object Read & Write. Trạng thái kiểm tra cũng được lưu cho cấu hình ENV; các khóa ENV vẫn đọc trực tiếp từ môi trường, không sao chép vào DB.

**Ngắt kết nối** giữ cấu hình đã mã hóa, metadata và file, nhưng khóa truy cập storage qua Sarae. Nhấn kiểm tra để kết nối lại. Hủy/hoàn tất upload trước khi đổi khóa hoặc ngắt kết nối. Không đổi bucket khi còn metadata R2; reset có backup trước nếu đây là môi trường mới không cần giữ media.

## ENV và thứ tự ưu tiên

```dotenv
SARAE_STORAGE_PROVIDER=R2
SARAE_R2_ACCOUNT_ID=
SARAE_R2_BUCKET=
SARAE_R2_ACCESS_KEY_ID=
SARAE_R2_SECRET_ACCESS_KEY=
SARAE_R2_ENDPOINT=
SARAE_R2_REGION=auto
```

ENV là cấu hình mặc định khi chưa có bản ghi R2 trong database. Sau khi lưu từ Admin, **toàn bộ cấu hình DB ưu tiên ENV**, kể cả trạng thái ngắt kết nối. Không trộn từng trường từ hai nguồn, tránh gửi secret tới nhầm endpoint. Endpoint chỉ chấp nhận HTTPS R2 của Account ID tương ứng. Reset xóa bản ghi cấu hình DB, lúc đó ENV có hiệu lực lại.

Provider mặc định cho upload mới là R2. Media cũ tiếp tục dùng provider trên bản ghi; không triển khai hoặc tự chạy migration Google Drive. OAuth Drive vẫn ở Backend để tương thích dữ liệu legacy, không tham gia luồng R2 mới.

## Multipart, checksum và lỗi

Web nhận `chunk_size=8388608` từ API. R2 dùng part 8 MiB cố định, trừ part cuối (tối thiểu S3 là 5 MiB). Chunk Drive 256 KiB không tương thích R2. Upload ID lưu mã hóa; ListParts khôi phục offset sau mất mạng hoặc restart. Complete và status khôi phục được khi phản hồi hoàn tất bị mất. Cancel gọi AbortMultipartUpload.

Object key: `owners/<owner_id>/media/<media_id>/video`. Phân quyền vẫn kiểm tra Owner ở Sarae, không dựa vào prefix. S3 ETag không được dùng như MD5 toàn file. Mỗi part gửi ContentMD5; sau hoàn tất Backend đọc lại object tính SHA-256, kể cả upload được resume sau restart. Việc đọc lại này tăng một lượt tải nội bộ R2 → Backend nhưng bảo đảm Agent luôn nhận checksum đầy đủ. Boto3 retry chuẩn tối đa 3 retries, connect timeout 10 giây/read timeout 120 giây.

Sai khóa, chữ ký, bucket, quyền, timeout được chuyển sang lỗi storage 502/503; không trả 401 khiến Sarae đăng xuất. Range không hợp lệ trả 416, object thiếu trả 404, upload hết hạn trả 410. Nếu ENV thay đổi trong lúc upload, revision của cấu hình không khớp trả `STORAGE_CONFIG_CHANGED`; khôi phục cấu hình cũ để hủy session hoặc dùng lifecycle dọn multipart.

Nên thiết lập lifecycle **Abort incomplete multipart uploads** trong dashboard R2 (ví dụ sau 7 ngày) để dọn upload bị bỏ dở khi trình duyệt đóng hoặc server mất dữ liệu. Sarae không tự tạo bucket, thay lifecycle hoặc thực hiện tác vụ tính phí ngoài upload người dùng yêu cầu.

## Windows Agent

Luồng giữ nguyên: R2 → Backend download có xác thực/Range → Agent Cloud Cache → OBS. Agent kiểm tra checksum, chỉ tải file thiếu/đổi, giữ `.part` để tiếp tục, không tải lại khi LIVE nếu cache hợp lệ. Không sửa OBS hoặc A/B switching; không gửi R2 credentials tới Agent.

Presigned R2 URL có thể giảm băng thông Backend, nhưng là bearer URL cho đến hết hạn và cần xử lý gia hạn lúc resume, redirect và tránh gửi Agent key sang R2. Phiên bản này giữ proxy Backend có phân quyền hiện có. Nếu triển khai trực tiếp sau này, chỉ cấp URL GET theo media sau khi xác thực Owner/Agent, TTL ngắn (60–300 giây), không log query signature, không gửi header Agent key tới R2.

## Reset Cloud Media cho môi trường mới

Không cần reset để cài R2 trên DB trống. Nếu còn metadata Drive/R2 không cần giữ:

1. Dừng Backend và các worker để tránh ghi mới. Xác nhận đúng database (`SARAE_DATA_DIR/sarae_live.db`, mặc định `backend/data/sarae_live.db`).
2. Xem trước số bản ghi:

   ```powershell
   python backend/tools/reset_cloud_media.py backend/data/sarae_live.db
   ```

3. Áp dụng khi chắc chắn không cần giữ Cloud Media:

   ```powershell
   python backend/tools/reset_cloud_media.py backend/data/sarae_live.db --apply
   ```

Công cụ tạo backup SQLite nhất quán cạnh DB (`.cloud-reset-<UTC>.bak`), kiểm tra integrity trước khi xóa và foreign keys trước commit. Chỉ xóa media provider R2/GOOGLE_DRIVE cùng liên kết/thumbnail/presenter/upload/storage settings/OAuth state liên quan. Giữ nguyên sản phẩm, tài khoản, Agent, phiên LIVE, AI, dữ liệu nghiệp vụ và LOCAL_LEGACY. Không xóa file R2, Drive hoặc file local. File cloud cũ cần quản trị viên xử lý riêng nếu muốn; token đã thu hồi có thể khiến không hủy được multipart, hãy dùng lifecycle.

Sau reset, khởi động Backend và nhập cấu hình mới trong Admin. Để rollback, dừng Backend, phục hồi đúng backup DB và giữ nguyên credential key. Backup chứa dữ liệu nghiệp vụ và secrets mã hóa; bảo vệ như database chính.

## Kiểm thử

Không cần bucket thật để chạy tests mock:

```powershell
cd backend
.venv/Scripts/python.exe -m unittest discover -s tests
cd ../web
npm run build
cd ..
$env:PYTHONPATH='agent-core/src'
backend/.venv/Scripts/python.exe -m unittest discover -s agent-core/tests -p test_cloud_media_cache.py
```

Chỉ thử bucket thật khi chủ dự án cung cấp credentials. Không bắt đầu TikTok LIVE thật.
