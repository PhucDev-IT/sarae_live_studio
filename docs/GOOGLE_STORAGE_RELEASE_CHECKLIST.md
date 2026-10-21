# Checklist phát hành public — Google OAuth verification & bảo mật storage

> Áp dụng cho kho tập trung dùng Google Drive của chủ hệ thống, scope **`drive.file`**.
> Quy ước: **TÀI LIỆU GOOGLE** = theo tài liệu chính thức; **CẦN KIỂM CHỨNG** = phải xác nhận trong Google Cloud Console của dự án trước khi dựa vào.

## A. Quyết định scope

| Scope | Phân loại (TÀI LIỆU GOOGLE) | Sarae dùng? | Hệ quả verification |
|---|---|---|---|
| `drive.file` | Non-sensitive | ✅ Có | Không cần restricted-scope review và không cần CASA |
| `drive.readonly` | Restricted | ❌ Không | Sẽ cần restricted-scope review + đánh giá bảo mật CASA hằng năm |
| `drive` | Restricted | ❌ Không | Như trên |

- [ ] Xác nhận code chỉ xin `https://www.googleapis.com/auth/drive.file` (`backend/app/google_api.py` → `SCOPES`).
- [ ] Trong *Google Auth Platform → Data access*, chỉ khai báo đúng scope `drive.file`, xóa các scope thử nghiệm cũ (ví dụ `drive.readonly` từ đợt PoC).
- [ ] Nếu sau này cần thêm scope restricted: dừng lại, lập kế hoạch restricted-scope review + CASA trước khi code.

## B. Google Cloud project & OAuth client

- [ ] Dùng project riêng cho production, tách khỏi project PoC/dev.
- [ ] OAuth client loại **Web application**, redirect URI đúng `https://<api-domain>/api/storage-admin/google/callback` và chỉ dùng HTTPS.
- [ ] Bật đúng một API cần dùng: **Google Drive API**. Picker API không cần.
- [ ] Lưu client secret trong secret manager hoặc biến môi trường của máy chủ, không commit vào repo (`SARAE_GOOGLE_CLIENT_SECRET`).
- [ ] Giới hạn người có quyền IAM trên project (owner/editor) ở mức tối thiểu và bật 2FA cho họ.

## C. Consent screen & publishing status

- [ ] **CẦN KIỂM CHỨNG:** khi app ở trạng thái *Testing* với user type *External*, refresh token có thể hết hạn sau 7 ngày → kho sẽ ngừng hoạt động hằng tuần. Trước khi dùng thật, chuyển sang **In production**.
- [ ] Điền đầy đủ: tên app, logo, email hỗ trợ, domain trang chủ, **Privacy Policy** và **Terms of Service** trên domain đã xác minh.
- [ ] Xác minh quyền sở hữu domain trong Google Search Console.
- [ ] **Brand verification**: cần nếu muốn hiển thị tên/logo trên màn hình consent. Chỉ admin của Sarae thấy màn hình này, nhưng nên làm để tránh cảnh báo "unverified app".
- [ ] Privacy Policy phải nói rõ dữ liệu Google được dùng thế nào: chỉ lưu video do khách upload, không đọc file khác (drive.file), không chia sẻ cho bên thứ ba và tuân thủ Google API Services User Data Policy (Limited Use).

## D. Tài khoản Google chứa kho

- [ ] Dùng tài khoản riêng cho kho (không dùng Gmail cá nhân), bật 2FA hoặc passkey và có mã khôi phục được cất an toàn.
- [ ] Theo dõi quota: Gmail miễn phí 15 GB, gói Google One/Workspace lớn hơn. Admin console hiện quota qua nút **Kiểm tra**.
- [ ] Không chia sẻ thư mục `Sarae Media Storage` cho ai và không sửa hoặc xóa file trực tiếp trên Drive.
- [ ] Ghi chú: service account không có dung lượng My Drive riêng, nên muốn dùng thì cần Shared Drive của Workspace. Vì vậy Sarae dùng OAuth của một user (**CẦN KIỂM CHỨNG** nếu đổi sang Workspace).
- [ ] Ghi lại đúng tài khoản đang dùng. Sarae chặn đổi sang tài khoản khác khi đã có video (`STORAGE_ACCOUNT_MISMATCH`).

## E. Bảo mật Backend

| Kiểm tra | Trạng thái trong code | Việc khi phát hành |
|---|---|---|
| Refresh/access token mã hóa Fernet | ✅ `storage_credentials` | [ ] Đặt `SARAE_CREDENTIAL_KEY` cố định, backup riêng; mất khóa thì admin phải kết nối lại |
| Token không trả qua API, không log | ✅ có test | [ ] Review cấu hình log của máy chủ (không log query string của callback) |
| OAuth state hash + dùng 1 lần + 10 phút + PKCE | ✅ có test | — |
| Chỉ SUPER_ADMIN gọi `/api/storage-admin/*` | ✅ có test | [ ] Giới hạn IP/VPN cho admin console nếu có thể |
| Phiên upload resumable (URL có quyền ghi) mã hóa, không gửi cho trình duyệt | ✅ | — |
| URL stream/thumbnail ký HMAC, hết hạn 1 giờ, kiểm owner thật | ✅ có test | [ ] Đặt `SARAE_URL_SIGNING_SECRET` giống nhau trên mọi server |
| Cô lập owner cho media, upload, product, Agent | ✅ có test | [ ] Pentest IDOR trên staging |
| Không trả 401 cho lỗi kho (tránh đăng xuất khách) | ✅ có test | — |
| Giới hạn kích thước, loại file, chữ ký MP4/MOV, quota owner | ✅ có test | [ ] Chọn `SARAE_MEDIA_MAX_UPLOAD_BYTES`, `SARAE_MEDIA_OWNER_QUOTA_BYTES` |
| Kiểm MD5 sau upload, SHA-256 khi migrate | ✅ có test | — |
| HTTPS cho Web và API | ⬜ hạ tầng | [ ] TLS, HSTS; CORS chỉ cho domain Web |
| Rate limit upload/stream | ⬜ chưa có | [ ] Đặt ở reverse proxy (giới hạn số request và băng thông theo IP/owner) |
| Body size ở reverse proxy | ⬜ hạ tầng | [ ] Cho phép tối đa ~33 MB mỗi request `/chunk`, timeout đủ dài |
| Audit log thao tác admin | ⚠️ chỉ có log ứng dụng | [ ] Lưu log kết nối/ngắt/migrate vào kho log tập trung |
| Backup DB | ⬜ | [ ] Backup định kỳ và trước mỗi lần migrate/downgrade |

## F. Vận hành

- [ ] Có runbook khi `STORAGE_TOKEN_REVOKED`: admin vào **Kho lưu trữ** → **Kết nối lại** đúng tài khoản cũ.
- [ ] Cảnh báo khi quota Drive vượt 80%.
- [ ] Theo dõi lỗi `STORAGE_RATE_LIMITED` (Drive API quota per user).
- [ ] Trước khi vượt giới hạn Drive (dung lượng, băng thông, số request): lên kế hoạch chuyển sang S3/R2 theo `docs/CENTRALIZED_MEDIA_STORAGE.md` mục 5.
- [ ] Chạy thử end-to-end trên staging với Google thật: kết nối → upload video lớn (> 1 GB) → mất mạng giữa chừng → resume → xem trước có seek → Agent tải về → revoke → kết nối lại.

## G. Tham khảo

- [Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Manage uploads (resumable)](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
- Google API Services User Data Policy, OAuth app verification help (tra cứu bản mới nhất trước khi nộp)
