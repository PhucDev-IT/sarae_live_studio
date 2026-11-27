# Studio local — mốc nền 0.3.0

Ngày triển khai: 26/11/2026. Nhánh `feat/local-tauri-studio`.

Đã triển khai luồng đầu tiên trong tài liệu bàn giao: mở studio không cần tài khoản Sarae, CRUD sản phẩm trên SQLite, nhập video thành bản sao được quản lý, gắn với sản phẩm và xem trước. Tauri và browser dùng chung `StudioService`; không khởi động Backend SaaS, activation, cloud worker hoặc upload R2/Drive.

## Chạy nhanh

Bấm đúp `start-studio.cmd` ở thư mục gốc repo. Lần đầu script tự tạo `agent-core/.venv` và cài dependency Python và npm; các lần sau chỉ cài lại khi `pyproject.toml` hoặc `package-lock.json` đổi. Trình duyệt tự mở khi Studio sẵn sàng (đặt `SARAE_NO_BROWSER=1` để tắt). Nếu Studio đang chạy rồi, script chỉ mở lại trình duyệt.

| Lệnh | Việc làm |
| --- | --- |
| `start-studio.cmd` | Studio trong trình duyệt, dữ liệu thật (`%LOCALAPPDATA%\SaraeLiveStudio`). |
| `start-studio.cmd demo` | Như trên nhưng dữ liệu thử ở `.tmp\studio-demo`. |
| `start-studio.cmd native` | Cửa sổ Tauri (cần Rust); `native rebuild` build lại sidecar Python. |
| `start-studio.cmd test` | Chạy test Python của Studio/playback và build giao diện. |
| `start-studio.cmd setup` | Chỉ cài hoặc cập nhật môi trường. |

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
- Thẻ video hiển thị thời lượng, dung lượng, tỷ lệ (9:16, 16:9…) và kích thước như thư viện của nhánh `dev`; cửa sổ xem trước có bảng file gốc, dung lượng, thời lượng, kích thước, tỷ lệ và thời điểm nhập. Migration 6 lưu metadata từ FFprobe, kể cả chiều xoay của video điện thoại. Khi mở thư viện, video cũ được đọc bổ sung từng file; không quét khi migration hoặc tải danh sách. Đọc thông số chạy ngoài khóa dữ liệu, không giữ khóa trong lúc chờ FFprobe. Nếu đọc lỗi, file vẫn được giữ và có thể bấm thử lại; dữ liệu chưa biết hiển thị rõ thay vì giả định bằng 0. Cần khởi động lại core sau cập nhật để áp dụng migration và method `studio.media.inspect`.

Kiểm tra bổ sung cho thông số video: production build đạt; toàn bộ `agent-core/tests/test_studio*.py` đạt **200 test**. Đã thử UI riêng bằng video ngang/dọc thật và video cũ thiếu metadata: thẻ và bảng thông số hiển thị đúng, video cũ tự đọc bổ sung. Bộ test có migration từ schema 5, chiều xoay, thời lượng không xác định, lỗi/thử lại và thao tác đồng thời trong lúc probe. Với native, build lại sidecar bằng `start-studio.cmd native rebuild` để nhận mã đọc metadata mới.

### Ảnh bìa video local (27/11/2026)

Thư viện tự tạo thumbnail JPEG từ khung hình video, ưu tiên khoảng 10% thời lượng và tối đa giây đầu tiên. Ảnh giữ chiều xoay và tỷ lệ, cạnh dài tối đa 640 px, cache ở `thumbnails/media/` theo ID video và SHA-256. Video cũ và video mới được tạo ảnh lần lượt khi mở thư viện; tải danh sách không chạy FFmpeg. Tạo ảnh chạy ngoài khóa dữ liệu, không yêu cầu GPU, không upload lên dịch vụ ngoài và không thay đổi video gốc. Cache còn hợp lệ được dùng lại sau khi mở app; xóa video chỉ xóa ảnh tương ứng. Nếu ảnh lỗi hoặc thiếu FFmpeg, video vẫn xem được và có nút **Tạo lại ảnh bìa**.

`studio.media.thumbnail` tạo ảnh qua cùng service cho IPC và HTTP; endpoint `GET/HEAD /studio/media-thumbnails/<id>` chỉ đọc cache và yêu cầu token local. Bản native đóng gói cả FFmpeg lẫn FFprobe, máy build cần hai executable trên PATH hoặc `SARAE_FFMPEG` / `SARAE_FFPROBE`. Sau cập nhật cần mở lại core; native dùng `start-studio.cmd native rebuild`.

Đã kiểm tra thumbnail thật trên UI với video ngang và dọc (JPEG 640×360 / 360×640), tự tạo cho thư viện cũ và giữ các nhãn thông số. Bộ test riêng thư viện (`test_studio_storage`, `test_studio_media_metadata`, `test_studio_media_thumbnails`, `test_studio_presenters`, `test_studio_transport`) đạt **139 test**; production build frontend đạt. Kiểm tra gồm cache qua lần mở lại, lỗi FFmpeg/thử lại, giữ file gốc, xóa ảnh theo video, thao tác đồng thời và endpoint có token cho web/native. Chưa build lại bộ cài native cho thay đổi thumbnail.

Kiểm tra tích hợp cuối sau cập nhật timeline: toàn bộ `test_studio*.py` đạt **210 test** (hai cảnh báo deprecation của dependency TikTokLive/websockets).
- Browser gửi file theo luồng qua core local, giới hạn 20 GB, tiến trình gửi và kiểm tra được hiển thị. Native dùng dialog chọn đường dẫn rồi copy trong core; quá trình copy hiển thị trạng thái chờ. Native request hiện có timeout 120 giây; với video lớn nên kiểm tra thư viện sau timeout trước khi nhập lại.
- Browser API chỉ bind loopback, kiểm tra Host/Origin và token ngẫu nhiên. Token kết nối không phải tài khoản hay license. Web dev chỉ cho origin `127.0.0.1:1420` và `localhost:1420`.

### Đồng bộ sản phẩm TikTok

Sản phẩm chỉ đến từ giỏ hàng TikTok LIVE; không có tạo tay. Migration 3 thêm `tiktok_seller_id`, `tiktok_product_id` (unique theo cặp), giá gốc, tồn kho, danh mục, ảnh, `last_synced_at` và bảng `tiktok_accounts`. Sản phẩm tạo tay ở bản 0.3.0 được giữ, đánh dấu `source = manual` và không bao giờ được ghép theo tên.

Luồng: **Mở Chrome đăng nhập** mở Chrome thường trên hồ sơ `tiktok-chrome-profile` trong thư mục dữ liệu; người dùng tự đăng nhập. **Tiếp tục/Kết nối** đóng cửa sổ đó rồi Playwright mở lại cùng hồ sơ ở LIVE Product Dashboard. **Đồng bộ sản phẩm** tải lại trang, đọc response `account_info/get` và `live_product/list` của chính console (dùng lại `AutoPinService.scan_products`), rồi ghi cả snapshot trong một transaction. Một dòng sai/thiếu mã hoặc trùng mã làm hủy cả lượt; tên, giá, tồn kho, danh mục và ảnh được làm mới, còn mô tả, video và người giới thiệu được giữ; sản phẩm không còn trong giỏ được giữ và chỉ báo số lượng. Cookie/mật khẩu chỉ nằm trong hồ sơ Chrome trên máy; Studio không phát lại URL ký của TikTok.

Ảnh bìa được tải nền sau khi đồng bộ, chỉ từ HTTPS thuộc CDN ảnh TikTok (kiểm tra cả redirect), tối đa 3 MB, nhận diện JPEG/PNG/WebP bằng magic bytes, lưu ở `thumbnails/` và phục vụ qua `/studio/thumbnails/<id>` có token — không cần mở CSP ra internet và vẫn xem được khi offline. Ảnh đổi thì bản cache cũ bị xóa và tải lại.

Người dùng chỉ sửa được mô tả sản phẩm (dữ liệu cho AI). Chưa kiểm tra với tài khoản TikTok thật trong phiên phát triển này: test dùng trình duyệt giả và snapshot giả.

### Tự đọc chat theo phiên

Tùy chọn của bàn trả lời (mặc định bật): phiên bắt đầu thì đọc chat công khai của tài khoản đã xác minh; phiên kết thúc thì dừng — chỉ khi chính phiên đã bật nó, chat bạn tự bấm "Đọc chat" được giữ nguyên. Đã kiểm thử bằng trình đọc chat giả.

### Trả lời khách bằng giọng đọc và bình luận

Theo mô hình `AI_AUTO_REPLY` của nhánh dev, gói trong `studio/replies.py` và hiển thị là "Bàn trả lời khách" trên trang Phiên LIVE.

- **Nguồn bình luận**: chat công khai của tài khoản TikTok đã xác minh khi đồng bộ (TikTokLive 7.0.1, chỉ đọc, không đăng nhập), hoặc ô "bình luận thử" của người vận hành.
- **Lọc trước AI**: tin của chính tài khoản đang LIVE, tài khoản trong danh sách bỏ qua, sự kiện trùng `msg_id`, cùng người cùng nội dung trong 20 giây, emoji/ký tự lặp và câu không phải hỏi bán hàng (có thể bật "gửi mọi bình luận cho AI"). AI tắt thì không xếp hàng.
- **AI**: một lần gọi cho mỗi câu được chọn, ngữ cảnh là sản phẩm đang phát trong OBS; câu hỏi quá `max_age_seconds` khi tới lượt bị bỏ.
- **Hai nhánh độc lập**: giọng đọc (Edge TTS qua `VoiceEngine` của Agent, phát vào nguồn giọng của OBS, gọi tên khách, giảm âm video khi đọc) và bình luận (ChatSender của Agent trên Chrome của Studio: trả lời trực tiếp → nhắc tên → tin thường, kiểm tra đúng tài khoản, chống trùng, giãn nhịp, hạn sử dụng). Nhánh này lỗi không ảnh hưởng nhánh kia.
- **Tự động có kiểm soát**: tự đọc / tự gửi bật riêng; chờ `confirm_seconds` để bạn hủy; câu "cần duyệt" và bình luận thử (nhánh bình luận) không bao giờ tự gửi. Người vận hành sửa câu trả lời, bấm Đọc / Gửi bình luận / Bỏ qua cho từng câu.
- Trạng thái bàn trả lời nằm trong bộ nhớ của Core; mỗi câu đã gửi được ghi vào nhật ký phiên LIVE.

Đã thử tự động với AI, giọng, chat và Chrome giả; giao diện chạy với core thật, OBS/giọng/Seller Brain giả. **Chưa thử đọc chat, đọc giọng hay gửi bình luận trên tài khoản TikTok thật.** Đồng bộ sản phẩm TikTok thật đã chạy được (14 sản phẩm, đủ ảnh bìa) trong thư mục dữ liệu thử.

Đóng gói: spec PyInstaller thêm `TikTokLive`/`TikTokLiveProto`; chưa build lại sidecar để xác nhận.

### Phiên LIVE: Single Video Timeline như nhánh dev

Studio không tự livestream bằng avatar và không tự đổi sản phẩm theo số video/phút (không khớp nội dung video — đã bỏ chế độ phát theo sản phẩm). Như dev (`docs/SINGLE_VIDEO_TIMELINE.md`): phiên là danh sách video phát nối tiếp, sản phẩm được ghim theo **mốc thời gian của từng video**; vị trí phát OBS là đồng hồ, nên ghim luôn khớp đoạn video đang giới thiệu.

Khác dev để thao tác nhanh: mốc ghim **gắn với video trong thư viện** (đặt một lần, mọi phiên dùng lại), tự lưu, không có bước nháp → duyệt → kích hoạt. Migration 7 thêm `media_markers` (video, `at_ms`, PIN_PRODUCT/UNPIN_PRODUCT, sản phẩm), cột `video_ids`/`video_order`/`video_loop` của `live_sessions`, và xóa `live_session_products`, `sales_scripts` của bản trước.

- **Đặt mốc** (Thư viện video → "Đặt mốc ghim"): xem video, chọn sản phẩm, bấm "Ghim từ mm:ss" (phím P) hoặc "Bỏ ghim từ mm:ss" (phím U). Thanh thời gian tô màu đoạn ghim từng sản phẩm; sửa thời điểm, đổi sản phẩm, xóa từng mốc; tự lưu. Chỉ sản phẩm đồng bộ từ TikTok mới ghim được; xóa sản phẩm xóa các mốc của nó.
- **Phiên**: tên, các video theo thứ tự, phát lần lượt/ngẫu nhiên/xáo trộn, lặp lại hay một lượt. Bấm "Phát" là chạy. Khi đang phát chỉ đổi được tên và lặp lại.
- **Ghim theo mốc**: dùng nguyên `TimelineScheduler` của Agent (chỉ ghim khi khác với cái đang ghim, tua không phát lại mốc đã qua, qua video kế giữ ghim cũ tới mốc đầu tiên, thử lại có giới hạn rồi `PIN_FAILED`, đọc lại Console định kỳ). Sửa mốc khi đang phát được áp ngay từ vị trí hiện tại.
- **Chạy thử / ghim thật** (công tắc, mặc định chạy thử như dry-run của dev): chạy thử chỉ ghi lại sẽ ghim gì; ghim thật bấm trên TikTok LIVE Console qua Chrome của Studio (`ConsolePinExecutor`) và chỉ khi Console xác nhận đang LIVE (`ConsoleLiveGate`, fail closed). Nhãn nhận diện LIVE của Console thừa hưởng từ dev, chưa kiểm chứng trên Console thật.
- Engine phát, OBS và mật khẩu: `PlaybackEngine.video_play`, `ObsController`, `ObsSettingsStore` của Agent, dữ liệu trong `<dữ liệu studio>/engine` (`SARAE_AGENT_HOME`). Scheduler và model chính sách nhận UUID của Studio, vẫn giữ ID số của cloud.
- Một phiên phát tại một thời điểm; video thiếu file chặn bắt đầu. Core khởi động lại không tự phát tiếp (`core_restart`); đóng Studio dừng video (`core_stopped`).
- Phòng điều khiển: video đang phát, thanh tiến độ có chấm mốc (bấm để tua), mốc kế tiếp, trạng thái ghim (sẽ ghim / đang ghim / chờ LIVE / lỗi), danh sách video, nhật ký.
- Bàn trả lời lấy ngữ cảnh AI từ sản phẩm timeline đang ghim.

Kiểm thử tự động với obs-websocket giả, MP4 thật và executor chạy thử; giao diện chạy với core thật, OBS giả lập. **Chưa thử OBS thật và chưa ghim thật trên TikTok.** Overlay theo mốc (dev `LIVE_OVERLAYS.md`) là mốc kế tiếp; truyền sang điện thoại tạm hoãn.

### Trợ lý AI và thông tin sản phẩm

Migration 4 thêm `products.facts` (JSON) và bảng `settings` không chứa bí mật (bảng kịch bản đã bị xóa ở migration 7).

- **Thông tin sản phẩm cho AI**: ngoài mô tả, mỗi sản phẩm có bảng size (cân nặng/chiều cao), màu, chất liệu, giao hàng/đổi trả, lưu ý và các dữ kiện thêm dạng khóa–giá trị. Core kiểm tra và chuẩn hóa; đồng bộ TikTok không ghi đè phần này.
- **Provider**: `gemini` (cloud, key), `openai_compatible` (cloud HTTPS, key, đổi được `base_url` — OpenAI, OpenRouter…), `seller_brain` (llama.cpp của sarae-ai-live-studio, mặc định `http://127.0.0.1:8830/v1`, chỉ nhận địa chỉ loopback, gửi `json_schema` và `enable_thinking: false`). Seller Brain dùng chung GPU với OBS/TTS; UI cảnh báo trước khi chọn.
- **API key** chỉ ghi, được mã hóa DPAPI với entropy riêng của Studio trong `secrets/ai-<provider>.dpapi.json`, UI chỉ thấy 4 ký tự cuối. Lỗi của provider được che key trước khi trả về. Key không nằm trong SQLite, settings hay log.
- **Trả lời bình luận** (`studio.ai.reply`): ngữ cảnh chỉ gồm tên, giá, giá gốc, tồn kho, danh mục, mô tả và facts của sản phẩm được chọn; bình luận được coi là dữ liệu. Kết quả gồm intent, câu trả lời ngắn, `needs_review` và lý do. Trang Trợ lý AI có khung thử với bình luận mẫu; **chưa đọc chat TikTok, chưa gửi chat, chưa đọc TTS** — `action_executed` luôn `false`.
- Gọi provider chạy ngoài khóa của service; bộ đếm lượt gọi/token/lỗi chỉ tồn tại trong phiên Core.

Đã thử giao diện với một server giả lập Seller Brain trên máy (trả lời giá từ ngữ cảnh sản phẩm, đánh dấu cần duyệt); **chưa gọi Gemini/OpenAI hay Seller Brain thật**. Test tự động dùng provider giả, không gọi mạng; một lần kiểm tra thủ công xác nhận DPAPI thật mã hóa và đọc lại được key.

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
| `agent-core/src/sarae_agent/studio/replies.py` | Bàn trả lời: đọc chat, lọc, AI, giọng đọc và bình luận. |
| `agent-core/src/sarae_agent/studio/live.py` | Phiên LIVE theo timeline: phát video qua PlaybackEngine, ghim theo mốc qua TimelineScheduler của Agent. |
| `agent-core/src/sarae_agent/studio/ai.py` | Provider AI, key DPAPI, trả lời bình luận. |
| `agent-core/src/sarae_agent/studio/tiktok.py` | Kết nối Chrome TikTok, đồng bộ snapshot, cache ảnh bìa. |
| `agent-core/src/sarae_agent/studio/transport.py` | HTTP RPC, upload, SSE và media Range. |
| `agent-core/src/sarae_agent/studio/__main__.py` | Entrypoint local, IPC, khóa data directory và lifecycle. |
| `desktop/scripts/studio-dev.mjs` | Chạy core và Vite cùng vòng đời cho browser dev. |

Methods: `studio.status`, `studio.products.list/update/delete` (update chỉ nhận `description` và `facts`), `studio.tiktok.status/login/connect/sync`, `studio.ai.config/configure/set_key/delete_key/test/reply`, `studio.obs.status/configure/connect/disconnect`, `studio.live.sessions.list/create/update/delete`, `studio.live.status/start/pause/resume/next/previous/seek/stop/pins`, `studio.replies.status/configure/chat_start/chat_stop/simulate/speak/send_comment/cancel/dismiss/stop_voice`, `studio.presenters.list/create/update/delete`, `studio.media.list/import/update/delete/url/markers.list/markers.set`. `studio.media.list` nhận `product_id` và `presenter_id` để lọc; `studio.media.import` và endpoint upload (`presenter_id` lặp lại trong query) nhận danh sách người giới thiệu. Lỗi có `code`, `message`, `details`; mutation phát `studio.changed`. `studio.media.import` nhận đường dẫn chỉ qua native IPC; browser phải dùng endpoint upload. `activation.*` và lệnh cloud không đăng ký trong core local.

## Kiểm thử và phạm vi còn lại

Các lệnh kiểm tra:

```powershell
npm --prefix desktop run build
Set-Location agent-core
.venv/Scripts/python.exe -m pytest tests/test_studio_replies.py tests/test_studio_live.py tests/test_studio_ai.py tests/test_studio_tiktok.py tests/test_studio_presenters.py tests/test_studio_storage.py tests/test_studio_transport.py tests/test_protocol_config.py tests/test_playback_engine.py tests/test_playback_policy.py tests/test_media_playback.py tests/test_obs_controller.py -q
```

Kết quả bộ test trên: **254 passed** (26/11/2026, sau bàn trả lời; gồm cả test playback/OBS của Agent). Đã kiểm tra SQLite qua lần đóng/mở lại, validation/rollback, copy và xóa giữ file gốc, video giả/audio-only bị từ chối, resolver không nhận đường dẫn tùy ý, HTTP token/origin, upload/Range, IPC khởi động/dừng, khóa controller và dọn upload gián đoạn. Regression shutdown khi đang import xác nhận kết quả đã commit được trả về trước `core.stopped`. Browser được thử bằng MP4 H.264 tạo từ FFmpeg: tạo/sửa sản phẩm tiếng Việt, nhập/gắn video và preview đọc được hình/metadata, tắt tiếng mặc định.

`cargo check --locked` và production build frontend đã qua. Sidecar PyInstaller được build và kiểm tra `--self-check`, IPC local, persistence, single-instance, graceful shutdown và dọn core sau khi bootloader bị đóng cưỡng bức; nhập video vẫn chạy khi FFprobe không có trên PATH nhờ binary trong bundle. Đây là kiểm tra trên máy phát triển, chưa phải chứng nhận bộ cài trên Windows sạch.

Đã mở cửa sổ Tauri debug thật với thư mục dữ liệu thử riêng: core tự kết nối, tạo sản phẩm qua IPC, chọn MP4 bằng dialog Windows, nhập video và preview trong WebView2 hiển thị hình/độ dài 3 giây, tắt tiếng mặc định. Đóng cửa sổ native trả mã thoát 0. Chưa chạy toàn bộ các tổ hợp đóng/mở đồng thời hoặc thử bộ cài trên máy Windows sạch.

Giai đoạn 2 còn thiếu backup/restore và relink file tham chiếu. Không làm migration dữ liệu cũ: studio local là app mới với dữ liệu sạch. Các giai đoạn tiếp theo vẫn là phiên LIVE/playback/OBS, facts/QA và AI reply, TTS, timeline/overlay, ghim/chat và công cụ monitor. Các module cũ trong `backend/`, `web/`, `agent-core/` được giữ để chuyển tiếp; UI mới đánh dấu OBS/AI chưa triển khai. Không coi thành công của local CRUD/preview là đã kiểm chứng phát LIVE hoặc tích hợp OBS/TikTok.

Repo `F:\Coder\sarae-ai-live-studio` chỉ được đọc tham khảo cách tổ chức app/lifecycle; không copy runtime avatar, weights, database, account hoặc secrets sang repo này. Bộ não trả lời và stream sẽ được đối chiếu sâu ở mốc tương ứng trong [thiết kế tổng thể](LOCAL_TAURI_STUDIO_OVERVIEW.md).
