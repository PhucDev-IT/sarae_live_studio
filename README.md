# Sarae Live Studio

Ứng dụng Windows cho bán hàng TikTok LIVE: phát video sản phẩm qua OBS, ghim sản phẩm, overlay, AI trả lời bình luận
và đọc bằng giọng clone. Chạy theo mô hình hybrid:

- **Máy khách** chạy Studio: giao diện, OBS, Chrome LIVE Console, video, FFmpeg và dữ liệu SQLite đều nằm trên máy họ.
  AI trả lời bình luận do khách tự cấu hình.
- **PC server** của chủ shop chạy gateway sau Cloudflare Tunnel (`https://api.puns.id.vn`): kích hoạt máy, license,
  giọng clone (sarae-voice-server) và hàng đợi TTS.

## Thư mục

| Thư mục | Nội dung |
|---|---|
| `desktop/` | Ứng dụng Tauri 2 + React (giao diện Studio, trang overlay OBS). Chạy `agent-core` làm sidecar. |
| `agent-core/` | Studio Core bằng Python (không UI): sản phẩm, video, phiên LIVE, OBS, ghim, overlay, AI, giọng, kích hoạt máy. |
| `backend/` | Server ở PC nhà: gateway (`app/gateway_main.py`), trang admin (`app/admin_main.py`), CLI `tools/studio_admin.py`. |
| `scripts/server/` | Tự khởi động, giám sát, sao lưu server PC nhà. |
| `docs/` | Tài liệu. Bắt đầu từ các file bên dưới. |

## Chạy

| Việc | Lệnh | Tài liệu |
|---|---|---|
| Studio trên máy này (trình duyệt, dữ liệu thật) | `start-studio.cmd` | [LOCAL_TAURI_STUDIO_STATUS](docs/LOCAL_TAURI_STUDIO_STATUS.md) |
| Studio cửa sổ Tauri | `start-studio.cmd native` | [WINDOWS_AGENT_BUILD](docs/WINDOWS_AGENT_BUILD.md) |
| Test Python + build giao diện | `start-studio.cmd test` | |
| Gateway trên PC server (`127.0.0.1:8000`) | `start-server.cmd` | [HYBRID_STUDIO_SERVER](docs/HYBRID_STUDIO_SERVER.md) |
| Trang admin: khách hàng, giọng clone (`127.0.0.1:8001`) | `start-admin.cmd` | [HYBRID_STUDIO_SERVER](docs/HYBRID_STUDIO_SERVER.md) |
| Cloudflare Tunnel, tự khởi động, sao lưu | `scripts\server\*.ps1` | [HYBRID_SERVER_OPS](docs/HYBRID_SERVER_OPS.md) |
| Bộ cài gửi khách | `desktop\scripts\build-windows.bat` | [CUSTOMER_INSTALLER](docs/CUSTOMER_INSTALLER.md) |
| Build lại trang overlay OBS | `npm --prefix desktop run build:overlay` | [LIVE_OVERLAYS](docs/LIVE_OVERLAYS.md) |

Cần Node.js 20+ và Python 3.11–3.13 (khuyên dùng 3.12). Build bộ cài cần thêm Rust (MSVC).

Studio trên chính PC server nên gọi thẳng gateway thay vì đi vòng qua Cloudflare:
`setx SARAE_STUDIO_SERVER_URL http://127.0.0.1:8000`, mở cửa sổ mới rồi chạy `start-studio.cmd`.

## Voice server

Giọng clone chạy bằng dự án riêng `..\sarae-voice-server` (GPU, `127.0.0.1:8780`), có `.venv`, `.env` và `data` riêng.
Gateway gọi nó bằng `SARAE_TTS_SERVER_URL` và token trong `backend\.env` (xem `backend\.env.example`).
Không bao giờ đưa voice server hay trang admin ra ngoài qua Cloudflare.

## Ghim sản phẩm TikTok LIVE

Trong phiên LIVE, chọn sản phẩm rồi bấm **Ghim**, hoặc bật **Tự động ghim theo video**. Chrome điều khiển phải mở
đúng LIVE Console đã đăng nhập. Studio dùng TikTok Product ID khi card cho phép xác minh; nếu không, nó đối chiếu
tên, ảnh và giá rồi click nút trên đúng card. Khi nhiều card khớp, người vận hành xác nhận; Studio không tự chọn theo
vị trí. Auto-pin chỉ chạy sau khi video đã chuyển sản phẩm và không bỏ ghim sản phẩm cũ trước khi ghim sản phẩm mới.
Mọi thao tác dùng DOM trong Chrome đang đăng nhập, không gọi API nội bộ của TikTok. Chi tiết:
[AUTO_PIN_WINDOWS_AGENT](docs/AUTO_PIN_WINDOWS_AGENT.md).
