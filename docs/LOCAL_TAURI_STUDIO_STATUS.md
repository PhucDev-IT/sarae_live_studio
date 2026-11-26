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

- Sản phẩm có ID ổn định, tên, mô tả và giá tùy chọn. Giá trống khác giá bằng không.
- Nhập video sao chép theo từng khối, tính SHA-256 và kiểm container/video stream bằng FFprobe. File chỉ xuất hiện trong thư viện khi copy, kiểm tra và commit thành công.
- File gốc được giữ nguyên. Xóa video chỉ xóa bản sao trong thư viện; xóa sản phẩm giữ video và bỏ liên kết sản phẩm.
- Resolver chỉ nhận ID, kiểm tra đường dẫn trong thư viện và dung lượng file. File thiếu/thay đổi hiển thị trạng thái thiếu file.
- Preview hỗ trợ HTTP Range và tắt tiếng mặc định. MOV/MKV/HEVC và một số codec có thể nhập được nhưng browser/WebView không giải mã được; UI báo lỗi preview. Chưa có bước chuyển mã tự động.
- Browser gửi file theo luồng qua core local, giới hạn 20 GB, tiến trình gửi và kiểm tra được hiển thị. Native dùng dialog chọn đường dẫn rồi copy trong core; quá trình copy hiển thị trạng thái chờ. Native request hiện có timeout 120 giây; với video lớn nên kiểm tra thư viện sau timeout trước khi nhập lại.
- Browser API chỉ bind loopback, kiểm tra Host/Origin và token ngẫu nhiên. Token kết nối không phải tài khoản hay license. Web dev chỉ cho origin `127.0.0.1:1420` và `localhost:1420`.

Đây là schema local mới; không tự đọc/ghi đè database SaaS hoặc dữ liệu Sarae Agent cũ. Chưa có giao diện migration dữ liệu cũ hoặc backup/restore; không xóa hay di chuyển dữ liệu thật để thử nghiệm.

## Hợp đồng và mã nguồn

| Vị trí | Vai trò |
| --- | --- |
| `desktop/src/services/studio.ts` | Kiểu dữ liệu chung, adapter IPC và HTTP, upload theo luồng, subscription sự kiện. |
| `desktop/src/features/studio/` | Shell, sản phẩm, thư viện video và preview; xử lý mất kết nối. |
| `agent-core/src/sarae_agent/studio/service.py` | Nghiệp vụ local dùng chung, không import Backend/identity/activation. |
| `agent-core/src/sarae_agent/studio/storage.py` | SQLite, migration và transaction. |
| `agent-core/src/sarae_agent/studio/transport.py` | HTTP RPC, upload, SSE và media Range. |
| `agent-core/src/sarae_agent/studio/__main__.py` | Entrypoint local, IPC, khóa data directory và lifecycle. |
| `desktop/scripts/studio-dev.mjs` | Chạy core và Vite cùng vòng đời cho browser dev. |

Methods: `studio.status`, `studio.products.list/create/update/delete`, `studio.media.list/import/delete/url`. Lỗi có `code`, `message`, `details`; mutation phát `studio.changed`. `studio.media.import` nhận đường dẫn chỉ qua native IPC; browser phải dùng endpoint upload. `activation.*` và lệnh cloud không đăng ký trong core local.

## Kiểm thử và phạm vi còn lại

Các lệnh kiểm tra:

```powershell
npm --prefix desktop run build
Set-Location agent-core
.venv/Scripts/python.exe -m pytest tests/test_studio_storage.py tests/test_studio_transport.py tests/test_protocol_config.py -q
```

Kết quả bộ test trên: **101 passed**. Đã kiểm tra SQLite qua lần đóng/mở lại, validation/rollback, copy và xóa giữ file gốc, video giả/audio-only bị từ chối, resolver không nhận đường dẫn tùy ý, HTTP token/origin, upload/Range, IPC khởi động/dừng, khóa controller và dọn upload gián đoạn. Regression shutdown khi đang import xác nhận kết quả đã commit được trả về trước `core.stopped`. Browser được thử bằng MP4 H.264 tạo từ FFmpeg: tạo/sửa sản phẩm tiếng Việt, nhập/gắn video và preview đọc được hình/metadata, tắt tiếng mặc định.

`cargo check --locked` và production build frontend đã qua. Sidecar PyInstaller được build và kiểm tra `--self-check`, IPC local, persistence, single-instance, graceful shutdown và dọn core sau khi bootloader bị đóng cưỡng bức; nhập video vẫn chạy khi FFprobe không có trên PATH nhờ binary trong bundle. Đây là kiểm tra trên máy phát triển, chưa phải chứng nhận bộ cài trên Windows sạch.

Đã mở cửa sổ Tauri debug thật với thư mục dữ liệu thử riêng: core tự kết nối, tạo sản phẩm qua IPC, chọn MP4 bằng dialog Windows, nhập video và preview trong WebView2 hiển thị hình/độ dài 3 giây, tắt tiếng mặc định. Đóng cửa sổ native trả mã thoát 0. Chưa chạy toàn bộ các tổ hợp đóng/mở đồng thời hoặc thử bộ cài trên máy Windows sạch.

Chưa hoàn tất toàn bộ giai đoạn 2 trong thiết kế: người giới thiệu, backup/restore, migration dữ liệu cũ và relink file tham chiếu. Các giai đoạn tiếp theo vẫn là phiên LIVE/playback/OBS, facts/QA và AI reply, TTS, timeline/overlay, ghim/chat và công cụ monitor. Các module cũ trong `backend/`, `web/`, `agent-core/` được giữ để chuyển tiếp; UI mới đánh dấu OBS/AI chưa triển khai. Không coi thành công của local CRUD/preview là đã kiểm chứng phát LIVE hoặc tích hợp OBS/TikTok.

Repo `F:\Coder\sarae-ai-live-studio` chỉ được đọc tham khảo cách tổ chức app/lifecycle; không copy runtime avatar, weights, database, account hoặc secrets sang repo này. Bộ não trả lời và stream sẽ được đối chiếu sâu ở mốc tương ứng trong [thiết kế tổng thể](LOCAL_TAURI_STUDIO_OVERVIEW.md).
