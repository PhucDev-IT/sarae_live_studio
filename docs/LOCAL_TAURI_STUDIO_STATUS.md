# Studio local — mốc nền 0.3.0

Ngày triển khai: 26/11/2026. Nhánh `feat/local-tauri-studio`.

Đã triển khai luồng đầu tiên trong tài liệu bàn giao: mở studio không cần tài khoản Sarae, CRUD sản phẩm trên SQLite, nhập video thành bản sao được quản lý, gắn với sản phẩm và xem trước. Tauri và browser dùng chung `StudioService`; không khởi động Backend SaaS, activation, cloud worker hoặc upload R2/Drive.

## Chạy thử bằng browser trên Windows

Cần Node tương thích Vite 8, Python 3.11–3.13 (khuyến nghị 3.12) và FFmpeg/FFprobe trên PATH. Dùng chính frontend trong `desktop/`.

Từ thư mục gốc repo, cài một lần:

```powershell
py -3.12 -m venv agent-core/.venv
agent-core/.venv/Scripts/python.exe -m pip install -e "./agent-core[dev]"
npm --prefix desktop ci
```

Sau đó bấm `start-studio.cmd` hoặc:

```powershell
npm --prefix desktop run studio
```

Mở <http://127.0.0.1:1420>. Giữ terminal chạy, Ctrl+C để dừng UI và core. Launcher chọn cổng loopback trống cho Python, truyền địa chỉ vào Vite proxy và theo dõi tiến trình cha. Không chạy thêm `backend/` hoặc `web/`.

Để thử với dữ liệu riêng:

```powershell
npm --prefix desktop run studio -- --data-dir F:\Coder\sarae_live_studio\.tmp\studio-test
```

`SARAE_STUDIO_DATA_DIR` thay đổi thư mục dữ liệu mặc định; `SARAE_STUDIO_PYTHON` chọn Python trong launcher. Không hard-code đường dẫn repo AI hoặc đường dẫn máy phát triển trong runtime.

## Chạy native Tauri

Cần Rust MSVC và Windows build tools như [hướng dẫn build](WINDOWS_AGENT_BUILD.md). **Phải rebuild sidecar** khi chuyển từ Agent cũ, vì file EXE ignored có thể vẫn là phiên bản cũ:

```powershell
desktop/scripts/dev-windows.bat --rebuild-core
```

Hoặc sau khi build sidecar:

```powershell
npm --prefix desktop run tauri -- dev
```

Tauri tự bật sidecar với `--studio --ipc`, dùng JSON Lines v1 qua stdin/stdout cho nghiệp vụ. HTTP trong bản native chỉ phục vụ video theo ID và token phiên trên `127.0.0.1` ở cổng ngẫu nhiên. Không mở API ghi HTTP trong bản native. Đóng cửa sổ sẽ dừng sidecar; core theo dõi tiến trình cha và khóa thư mục dữ liệu để tránh hai controller cùng chạy.

Spec PyInstaller đóng gói FFprobe và SQLite. Máy build cần `ffprobe` trên PATH hoặc `SARAE_FFPROBE` trỏ tới executable; core frozen tìm FFprobe trong tài nguyên bundle. Bản cài sạch vẫn phải được kiểm thử riêng trước phát hành.

## Dữ liệu và nhập video

Mặc định `%LOCALAPPDATA%\SaraeLiveStudio\studio.sqlite3`, video trong `media/`. SQLite có migration đánh số, foreign keys, WAL và transaction. UI chỉ gọi service, không tự mở SQLite.

- Sản phẩm chỉ đến từ đồng bộ TikTok (xem mục bên dưới); Studio không tạo sản phẩm tay.
- Nhập video sao chép theo từng khối, tính SHA-256 và kiểm container/video stream bằng FFprobe. File chỉ xuất hiện trong thư viện khi copy, kiểm tra và commit thành công.
- File gốc được giữ nguyên. Xóa video chỉ xóa bản sao trong thư viện; xóa sản phẩm giữ video và bỏ liên kết sản phẩm.
- Resolver chỉ nhận ID, kiểm tra đường dẫn trong thư viện và dung lượng file. File thiếu/thay đổi hiển thị trạng thái thiếu file.
- Preview hỗ trợ HTTP Range và tắt tiếng mặc định. MOV/MKV/HEVC và một số codec có thể nhập được nhưng browser/WebView không giải mã được; UI báo lỗi preview. Chưa có bước chuyển mã tự động.
- Browser gửi file theo luồng qua core local, giới hạn 20 GB, tiến trình gửi và kiểm tra được hiển thị. Native dùng dialog chọn đường dẫn rồi copy trong core; quá trình copy hiển thị trạng thái chờ. Native request hiện có timeout 120 giây; với video lớn nên kiểm tra thư viện sau timeout trước khi nhập lại.
- Browser API chỉ bind loopback, kiểm tra Host/Origin và token ngẫu nhiên. Token kết nối không phải tài khoản hay license. Web dev chỉ cho origin `127.0.0.1:1420` và `localhost:1420`.

### Đồng bộ sản phẩm TikTok

Sản phẩm chỉ đến từ giỏ hàng TikTok LIVE; không có tạo tay. Migration 3 thêm `tiktok_seller_id`, `tiktok_product_id` (unique theo cặp), giá gốc, tồn kho, danh mục, ảnh, `last_synced_at` và bảng `tiktok_accounts`. Sản phẩm tạo tay ở bản 0.3.0 được giữ, đánh dấu `source = manual` và không bao giờ được ghép theo tên.

Luồng: **Mở Chrome đăng nhập** mở Chrome thường trên hồ sơ `tiktok-chrome-profile` trong thư mục dữ liệu; người dùng tự đăng nhập. **Tiếp tục/Kết nối** đóng cửa sổ đó rồi Playwright mở lại cùng hồ sơ ở LIVE Product Dashboard. **Đồng bộ sản phẩm** tải lại trang, đọc response `account_info/get` và `live_product/list` của chính console (dùng lại `AutoPinService.scan_products`), rồi ghi cả snapshot trong một transaction. Một dòng sai/thiếu mã hoặc trùng mã làm hủy cả lượt; tên, giá, tồn kho, danh mục và ảnh được làm mới, còn mô tả, video và người giới thiệu được giữ; sản phẩm không còn trong giỏ được giữ và chỉ báo số lượng. Cookie/mật khẩu chỉ nằm trong hồ sơ Chrome trên máy; Studio không phát lại URL ký của TikTok.

Ảnh bìa được tải nền sau khi đồng bộ, chỉ từ HTTPS thuộc CDN ảnh TikTok (kiểm tra cả redirect), tối đa 3 MB, nhận diện JPEG/PNG/WebP bằng magic bytes, lưu ở `thumbnails/` và phục vụ qua `/studio/thumbnails/<id>` có token — không cần mở CSP ra internet và vẫn xem được khi offline. Ảnh đổi thì bản cache cũ bị xóa và tải lại.

Người dùng chỉ sửa được mô tả sản phẩm (dữ liệu cho AI). Chưa kiểm tra với tài khoản TikTok thật trong phiên phát triển này: test dùng trình duyệt giả và snapshot giả.

### Người giới thiệu

Migration số 2 thêm bảng `presenters` (tên, ghi chú, trạng thái hoạt động/tạm nghỉ) và bảng liên kết nhiều–nhiều `media_presenters`. Một video có thể gắn với nhiều người giới thiệu. Xóa người giới thiệu hoặc video chỉ xóa liên kết, không xóa video. Người đang tạm nghỉ vẫn giữ các liên kết cũ, nhưng không hiện trong danh sách chọn khi gắn video mới. Ứng dụng chưa có ảnh đại diện; avatar hiển thị chữ cái đầu của tên.

Trong thư viện video có nút gắn để đổi sản phẩm và người giới thiệu của video đã nhập, qua `studio.media.update`. Thư viện có thêm bộ lọc theo người giới thiệu. Trên trang người giới thiệu, bấm số video sẽ mở thư viện đã lọc sẵn theo người đó.

Đây là schema local mới; không tự đọc/ghi đè database SaaS hoặc dữ liệu Sarae Agent cũ. Chưa có giao diện migration dữ liệu cũ hoặc backup/restore; không xóa hay di chuyển dữ liệu thật để thử nghiệm.

## Hợp đồng và mã nguồn

| Vị trí | Vai trò |
| --- | --- |
| `desktop/src/services/studio.ts` | Kiểu dữ liệu chung, adapter IPC và HTTP, upload theo luồng, subscription sự kiện. |
| `desktop/src/features/studio/` | Shell, sản phẩm, thư viện video và preview; xử lý mất kết nối. |
| `agent-core/src/sarae_agent/studio/service.py` | Nghiệp vụ local dùng chung, không import Backend/identity/activation. |
| `agent-core/src/sarae_agent/studio/storage.py` | SQLite, migration và transaction. |
| `agent-core/src/sarae_agent/studio/tiktok.py` | Kết nối Chrome TikTok, đồng bộ snapshot, cache ảnh bìa. |
| `agent-core/src/sarae_agent/studio/transport.py` | HTTP RPC, upload, SSE và media Range. |
| `agent-core/src/sarae_agent/studio/__main__.py` | Entrypoint local, IPC, khóa data directory và lifecycle. |
| `desktop/scripts/studio-dev.mjs` | Chạy core và Vite cùng vòng đời cho browser dev. |

Methods: `studio.status`, `studio.products.list/update/delete` (update chỉ nhận `description`), `studio.tiktok.status/login/connect/sync`, `studio.presenters.list/create/update/delete`, `studio.media.list/import/update/delete/url`. `studio.media.list` nhận `product_id` và `presenter_id` để lọc; `studio.media.import` và endpoint upload (`presenter_id` lặp lại trong query) nhận danh sách người giới thiệu. Lỗi có `code`, `message`, `details`; mutation phát `studio.changed`. `studio.media.import` nhận đường dẫn chỉ qua native IPC; browser phải dùng endpoint upload. `activation.*` và lệnh cloud không đăng ký trong core local.

## Kiểm thử và phạm vi còn lại

Các lệnh kiểm tra:

```powershell
npm --prefix desktop run build
Set-Location agent-core
.venv/Scripts/python.exe -m pytest tests/test_studio_tiktok.py tests/test_studio_presenters.py tests/test_studio_storage.py tests/test_studio_transport.py tests/test_protocol_config.py -q
```

Kết quả bộ test trên: **118 passed** (26/11/2026, sau đồng bộ TikTok; các test CRUD sản phẩm tay được thay bằng test snapshot). Đã kiểm tra SQLite qua lần đóng/mở lại, validation/rollback, copy và xóa giữ file gốc, video giả/audio-only bị từ chối, resolver không nhận đường dẫn tùy ý, HTTP token/origin, upload/Range, IPC khởi động/dừng, khóa controller và dọn upload gián đoạn. Regression shutdown khi đang import xác nhận kết quả đã commit được trả về trước `core.stopped`. Browser được thử bằng MP4 H.264 tạo từ FFmpeg: tạo/sửa sản phẩm tiếng Việt, nhập/gắn video và preview đọc được hình/metadata, tắt tiếng mặc định.

`cargo check --locked` và production build frontend đã qua. Sidecar PyInstaller được build và kiểm tra `--self-check`, IPC local, persistence, single-instance, graceful shutdown và dọn core sau khi bootloader bị đóng cưỡng bức; nhập video vẫn chạy khi FFprobe không có trên PATH nhờ binary trong bundle. Đây là kiểm tra trên máy phát triển, chưa phải chứng nhận bộ cài trên Windows sạch.

Đã mở cửa sổ Tauri debug thật với thư mục dữ liệu thử riêng: core tự kết nối, tạo sản phẩm qua IPC, chọn MP4 bằng dialog Windows, nhập video và preview trong WebView2 hiển thị hình/độ dài 3 giây, tắt tiếng mặc định. Đóng cửa sổ native trả mã thoát 0. Chưa chạy toàn bộ các tổ hợp đóng/mở đồng thời hoặc thử bộ cài trên máy Windows sạch.

Giai đoạn 2 còn thiếu backup/restore và relink file tham chiếu. Không làm migration dữ liệu cũ: studio local là app mới với dữ liệu sạch. Các giai đoạn tiếp theo vẫn là phiên LIVE/playback/OBS, facts/QA và AI reply, TTS, timeline/overlay, ghim/chat và công cụ monitor. Các module cũ trong `backend/`, `web/`, `agent-core/` được giữ để chuyển tiếp; UI mới đánh dấu OBS/AI chưa triển khai. Không coi thành công của local CRUD/preview là đã kiểm chứng phát LIVE hoặc tích hợp OBS/TikTok.

Repo `F:\Coder\sarae-ai-live-studio` chỉ được đọc tham khảo cách tổ chức app/lifecycle; không copy runtime avatar, weights, database, account hoặc secrets sang repo này. Bộ não trả lời và stream sẽ được đối chiếu sâu ở mốc tương ứng trong [thiết kế tổng thể](LOCAL_TAURI_STUDIO_OVERVIEW.md).
