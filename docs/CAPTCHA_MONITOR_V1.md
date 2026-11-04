# CAPTCHA Monitor — Windows Agent

Monitor phân biệt cảnh báo inactivity, hộp CAPTCHA và giao diện xác minh chưa rõ loại trong cửa sổ TikTok LIVE Studio; không giải, click hoặc kéo thanh xác minh. Agent tìm cửa sổ theo tên executable, chụp phần cửa sổ nằm trong desktop ảo (hỗ trợ nhiều màn hình) và so khớp template bằng OpenCV.

## Chu kỳ

- Mặc định chụp ngay khi bật, sau đó mỗi 240 giây. OWNER có thể đặt 180–240 giây trên trang CAPTCHA Monitor.
- Một lần khớp nghi vấn được chụp lại sau 2,5 giây. Chỉ sau hai lần khớp liên tiếp mới ghi `verification_detected` kèm loại sự kiện.
- Khi cùng giao diện xác minh còn hiện, Agent và Cloud không gửi lại cảnh báo. Hai lần quét không khớp liên tiếp ghi `verification_cleared`; sự kiện này không chứng minh thử thách đã được giải thành công.
- Chu kỳ chụp không thay đổi Cloud heartbeat hoặc polling của web UI.
- Matcher so khớp bản đồ gradient (Sobel) ở 1/2 độ phân giải, quét thô scale 0,6–1,6 ở 1/4 rồi tinh chỉnh quanh vị trí tìm được; vùng màn hình phẳng không bao giờ tính là khớp. Một khung 1920×1080 mất khoảng 1–1,5 giây. Ngưỡng mặc định vẫn là 0,82.

## Templates

Agent ưu tiên custom hợp lệ, sau đó built-in hợp lệ, rồi ảnh được Cloud đồng bộ. Ảnh custom có thể upload và reset trong Advanced Settings của desktop app. Ảnh bị hỏng được bỏ qua. Cloud chỉ đồng bộ các file do Cloud quản lý và không xóa custom/built-in.

Thư mục built-in `agent-core/src/sarae_agent/monitoring/resources/` có **8 template**, mỗi giao diện một template riêng, crop từ ảnh gốc chủ dự án cung cấp (ảnh gốc giữ nguyên, không commit). `templates.json` ghi nguồn, SHA-256, vùng crop, vùng bỏ qua (đồng hồ đếm ngược, hình trong canvas, mã audio…), loại sự kiện và mục đích; README cùng thư mục giải thích lý do chọn từng vùng.

| Loại sự kiện | Template |
| --- | --- |
| `INACTIVITY_WARNING` | `inactivity_modal_top.png` — đầu popup "LIVE will end in": tiêu đề, ô đếm ngược, đoạn văn inactivity |
| `CAPTCHA_CHALLENGE` / SHAPES | `shapes_challenge_popup.png`, `shapes_challenge_popup_wrapped.png` (prompt xuống dòng + spinner), `shapes_challenge_component.png` (canvas + Confirm) |
| `CAPTCHA_CHALLENGE` / PUZZLE | `puzzle_challenge_popup.png`, `puzzle_challenge_component.png` (khung ảnh + thanh kéo), `puzzle_challenge_large.png`, `puzzle_verify_box_edges.png` (hộp "Verify to continue") |

Phân loại: có popup inactivity → `INACTIVITY_WARNING` (kèm dạng CAPTCHA nếu thấy trong cùng khung); không có popup đó nhưng có giao diện thử thách → `CAPTCHA_CHALLENGE`; điểm nằm giữa 0,70 và ngưỡng, shapes/puzzle không phân định được, hoặc ảnh Cloud chưa phân loại → `UNKNOWN_VERIFICATION`. Ảnh mảnh rời (chỉ hình puzzle, canvas trống, một nút) không được coi là xác minh. Ảnh custom cùng kích thước với template built-in dùng lại vùng bỏ qua của template đó; có thể upload mask riêng tên `<template>_mask.png`.

Kiểm thử offline với ảnh gốc: `SARAE_CAPTCHA_FIXTURES=<thư mục images> pytest agent-core/tests/test_monitor.py -s` in PASS/FAIL cho từng ảnh.

Nếu cần mở rộng bộ built-in, hãy chụp/cắt chi tiết UI trên máy vận hành, cho người vận hành xem và xác nhận vùng cắt cùng quyền sử dụng, rồi đặt PNG đã duyệt vào thư mục resources và build lại sidecar. Không dùng vùng chứa dữ liệu cá nhân hay nội dung thử thách ngẫu nhiên.

## Telegram cho OWNER

Trên web, chọn Agent rồi mở CAPTCHA Monitor. Trong phần Telegram, nhập **Bot Token** và **Chat ID**, bật công tắc, bấm **Lưu Telegram**, sau đó bấm **Gửi thông báo thử**. Mỗi Agent có một cặp cấu hình riêng thuộc OWNER của Agent đó. Token được mã hóa trên Cloud bằng cơ chế credential hiện có; API chỉ trả `token_configured`, không trả token. Để trống Bot Token khi lưu sẽ giữ token đã có. Agent không lưu hoặc nhận token/Chat ID.

Cloud nhận trạng thái monitor qua heartbeat và gửi tin nhắn khi chuyển sang `inactivity_warning`, `captcha_challenge`, `unknown_verification` hoặc trở lại `watching`. Tin nhắn có tên máy, thời gian, loại sự kiện, trạng thái và độ tin cậy nếu có. Khi phát hiện, Agent upload ảnh chụp **toàn bộ cửa sổ TikTok LIVE Studio** (JPEG, không crop, cạnh dài tối đa 2560 px) lên `POST /api/agents/verification-capture?agent_id=&at=`; Cloud gửi cảnh báo bằng `sendPhoto` với caption là nội dung tin nhắn để OWNER tự kiểm tra detector báo đúng hay sai. Cloud chờ ảnh của đúng lần phát hiện (khớp theo thời điểm sự kiện) tối đa 20 giây; không có ảnh hoặc gửi ảnh lỗi thì vẫn gửi tin chữ kèm ghi chú. Cloud chỉ giữ ảnh mới nhất của mỗi Agent và xóa ngay sau khi gửi. Tin "đã hết giao diện xác minh" không kèm ảnh. Lỗi Telegram được báo trong kết quả gửi thử hoặc ghi log đã che token; hoạt động LIVE vẫn tiếp tục.

## API OWNER

- `GET /api/verification-monitor/status?agent_id=...`
- `PUT /api/verification-monitor/config?agent_id=...` — chu kỳ và ngưỡng nhận diện
- `PUT /api/verification-monitor/telegram/config?agent_id=...` — bật/tắt, Chat ID, Bot Token ghi một chiều
- `POST /api/verification-monitor/telegram/test?agent_id=...` — gửi bằng cặp đã lưu
- `POST /api/verification-monitor/start?agent_id=...`
- `POST /api/verification-monitor/stop?agent_id=...`

API upload template cũ trong `backend/app/verification_monitor.py` chỉ phục vụ chế độ single-PC legacy; SaaS dùng Windows Agent.
