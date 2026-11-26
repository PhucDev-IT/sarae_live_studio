# Bàn giao nhánh Sarae Live Studio local

Ngày bàn giao: 26/11/2026. Nhánh: `feat/local-tauri-studio`. Repo: `PhucDev-IT/sarae_live_studio`.

**Cập nhật triển khai 26/11/2026:** mốc nền local `0.3.0` đã có mã chạy cho shell, sản phẩm SQLite, nhập và preview video dùng chung service giữa browser/Tauri. Xem [trạng thái và cách chạy hiện tại](LOCAL_TAURI_STUDIO_STATUS.md). Các phần bên dưới ghi lại trạng thái bàn giao ban đầu trước khi triển khai.

## 1. Trạng thái bàn giao

Nhánh được tách từ `dev` tại `33a0e35fce3a9989b6de0af4db6e454397e3969d`. Thay đổi chỉ gồm tài liệu tổng quan, tài liệu bàn giao này và liên kết trong README. Chưa thay đổi mã ứng dụng, cấu hình, dependency hoặc phiên bản; chưa có bản build Tauri local mới.

Đọc [tổng quan kiến trúc và kế hoạch chuyển đổi](LOCAL_TAURI_STUDIO_OVERVIEW.md) trước khi viết mã. Các hợp đồng/path mới trong tài liệu là thiết kế cần triển khai, không phải API đã tồn tại.

## 2. Lấy nhánh trên máy mới

Nếu chưa có repo:

```powershell
git clone --branch feat/local-tauri-studio --single-branch --recurse-submodules https://github.com/PhucDev-IT/sarae_live_studio.git
Set-Location sarae_live_studio
git status
```

Nếu đã có bản clone, lưu/commit thay đổi đang làm trước rồi chạy:

```powershell
git fetch origin
git switch feat/local-tauri-studio
git submodule update --init --recursive
git status
```

Nếu nhánh local chưa tồn tại và Git không tự tạo tracking branch:

```powershell
git switch --track origin/feat/local-tauri-studio
```

`vendor/react-ui-core` là submodule; cần quyền đọc repo này và đúng commit được repo chính ghi lại. Không tự chuyển submodule sang đầu nhánh mới nhất khi chỉ muốn tái lập bản mã nguồn.

Repo AI là nguồn học hỏi tùy lúc cần; không phải submodule/dependency build bắt buộc. Lấy nhánh `dev` của `sarae-ai-live-studio` trên máy mới bằng tài khoản có quyền truy cập. Mốc tham khảo đã khảo sát: `9082e6f`, app `0.1.38`. Đường dẫn local `D:\Android_Programming\sarae-ai-live-studio` chỉ có ý nghĩa trên máy cũ.

## 3. Các quyết định cần giữ khi triển khai

- Một ứng dụng Windows Tauri, một frontend chung để thử bằng browser.
- Không login Sarae, license, kích hoạt hoặc chọn Agent để sử dụng studio.
- Video và dữ liệu nghiệp vụ lưu local; không dùng R2/Drive hay cloud storage Sarae.
- Được phép gọi API AI, với key người dùng cấu hình và được lưu bảo vệ trên máy.
- Tận dụng runtime Python/OBS hiện có; Rust quản lý shell và vòng đời sidecar.
- Domain service local không phụ thuộc OWNER/Agent giả. IPC native và HTTP web dev gọi cùng nghiệp vụ.
- Repo AI chỉ tham khảo bộ não, hàng đợi giọng và cách stream; avatar GPU không phải điều kiện bắt buộc.
- Kết nối TikTok vẫn có thể cần đăng nhập nền tảng cho từng tính năng; việc đó không khóa màn hình studio.

## 4. Công việc đầu tiên trên máy mới

### Kiểm tra nền hiện tại

1. Đọc `AGENTS.md` nếu máy/nhánh có bổ sung quy tắc, rồi đọc hai tài liệu local studio.
2. Kiểm tra `git status`, phiên bản Node/Python/Rust và submodule UI.
3. Chạy các kiểm tra hiện có phù hợp để có mốc lỗi ban đầu. Tham khảo `docs/WINDOWS_AGENT_BUILD.md` cho Windows; không giả định bản hiện tại đã có lệnh chạy studio local.
4. Kiểm kê các màn hình đang dùng và đánh dấu chức năng bắt buộc chuyển, đặc biệt timeline, overlay, ghim sản phẩm, video clip và monitor.

### Thay đổi mã đầu tiên

Triển khai một luồng nhỏ nhưng chạy thật: mở studio không login → tạo sản phẩm lưu SQLite local → nhập một video local → đóng/mở lại vẫn thấy dữ liệu. Cả Tauri và browser dev phải dùng cùng service cho luồng này.

Các đầu việc cụ thể:

1. Chốt hợp đồng `StudioClient` và vị trí service local trong `agent-core`; định nghĩa capability native/web.
2. Tạo entrypoint local tách khỏi activation/cloud worker; không bật Backend SaaS cùng lúc.
3. Khởi tạo thư mục `%LOCALAPPDATA%\SaraeLiveStudio`, SQLite và migration đầu tiên.
4. Chuyển shell/navigation và màn hình sản phẩm từ `web/` sang frontend chung trong `desktop/`; giữ giao diện hiện có khi phù hợp.
5. Tạo adapter Tauri IPC và adapter HTTP loopback cho web dev, kèm stream sự kiện và xử lý lỗi giống nhau.
6. Thay cloud media resolver bằng repository local, thêm import/preview có progress và kiểm tra file.
7. Kiểm tra luồng trên, ghi lại lệnh chạy/build thực tế và cập nhật tài liệu trước khi chuyển sang OBS/AI.

Không bắt đầu bằng cách xóa hàng loạt `backend/`/`web/` hoặc rewrite toàn bộ playback engine. Các màn hình và service cũ là nguồn để giữ nghiệp vụ trong thời gian chuyển đổi.

## 5. Những điểm cần rà soát kỹ

| Khu vực | Rủi ro cần xử lý |
| --- | --- |
| Bỏ login | Nhiều service còn yêu cầu owner/agent; bỏ UI auth chưa đủ. |
| Media | Resolver đang dựa vào cloud manifest/cache; đổi đúng nơi để OBS nhận file local. |
| Runtime | `AgentRuntime` còn gọi `_cloud()` cho voice, overlay, timeline và nhiều luồng phụ. |
| UI dùng chung | Alias và script chuẩn bị `@sarae/ui` hiện gắn với `web/`; sửa đường dẫn khi di chuyển frontend. |
| Database | Chỉ một service sở hữu thao tác ghi; không để backend cũ và core mới cùng điều khiển dữ liệu/phiên. |
| AI | Chụp ngữ cảnh lúc nhận comment, chống trùng và hủy stale reply khi sản phẩm/phiên đổi. |
| TTS | Tạo được file không có nghĩa đã đọc; ghi nhận theo playback thực và tránh audio bị phát hai lần. |
| OBS | Virtual Camera không tự chuyển audio; phải kiểm tra cả đường hình lẫn tiếng trên cấu hình thực. |
| Chuyển máy | Đường dẫn file tham chiếu và secrets DPAPI không portable; có restore/relink và nhập key lại. |
| Build | Installer phải chứa core và tài nguyên cần thiết; không phụ thuộc đường dẫn source/Python của máy phát triển. |

## 6. Kiểm tra tối thiểu cho các mốc sau

- **Local shell:** mở khi không có Cloud, không cần tài khoản; core thoát theo app, mở hai lần không có hai controller.
- **Lưu trữ:** transaction/migration, phục hồi sau lỗi import, file thiếu, ổ đầy, backup/restore và đường dẫn tiếng Việt.
- **Playback:** OBS thật, A/B, next/seek/pause, mất kết nối, checkpoint và không tự LIVE lại sau restart.
- **AI:** facts đúng/sai/thiếu, câu nhiều ý, hỏi mẫu cũ, viewer hỏi tiếp, duplicate event, timeout, giới hạn request và không lộ key.
- **TTS/chat:** queue ưu tiên, stop/cancel, một lần phát/gửi, phân biệt đang chờ với hoàn tất và không làm playback video bị treo.
- **Tích hợp:** thử TikTok/OBS trên môi trường hoặc phiên được phép; không coi mock thành công là đã xác minh stream thực tế.
- **Đóng gói:** cài trên Windows sạch, GUI/core cùng phiên bản, dữ liệu và secrets nằm đúng nơi.

Không dùng API key thật, video riêng hay dữ liệu khách hàng trong fixture được commit. Kiểm thử có gọi AI trả phí hoặc tác động phiên LIVE phải tách khỏi bộ test mặc định.

## 7. Prompt tiếp tục gợi ý

> Tiếp tục trên nhánh `feat/local-tauri-studio` của `PhucDev-IT/sarae_live_studio`. Đọc `docs/LOCAL_TAURI_STUDIO_OVERVIEW.md` và `docs/LOCAL_TAURI_STUDIO_HANDOFF.md`. Mục tiêu là một Windows app Tauri có cùng frontend để chạy thử trên web, bỏ login/license/activation và cloud storage. Video/dữ liệu lưu local, AI được dùng API ngoài. `sarae-ai-live-studio/dev` là repo tham khảo về bộ não và stream, không cần bê toàn bộ avatar R&D. Bắt đầu bằng mốc nền local và luồng sản phẩm/video chạy thật, tái sử dụng runtime Python/OBS hiện có, không xóa nghiệp vụ cũ trước khi chuyển xong. Báo rõ phần đã kiểm thử và phần chưa triển khai.
