# Sarae Live Studio

Monorepo for an internal live-commerce production workspace.

- `web/` — React, TypeScript, Vite and Tailwind CSS 4. SaaS Web for OWNER / SUPER_ADMIN.
- `backend/` — Sarae Cloud API (FastAPI). Cloud-only dependencies; no Windows runtime.
- `agent-core/` — Windows Agent Core (Python, no UI): activation/DPAPI, Cloud transport, MP4 library, OBS playlist, CAPTCHA monitor, TTS.
- `desktop/` — Windows Agent desktop app (Tauri 2 + React) hosting `agent-core` as a sidecar.
- `docs/` — development documents. Windows Agent: `WINDOWS_AGENT_ARCHITECTURE.md`, `WINDOWS_AGENT_IPC.md`, `WINDOWS_AGENT_BUILD.md`.

## Local prerequisites

Node.js (compatible with Vite 8) and Python 3.12. The Windows Agent build also needs Rust (MSVC) — see `docs/WINDOWS_AGENT_BUILD.md`.

## Start Backend (Windows CMD)

```cmd
cd backend
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

API health: http://127.0.0.1:8000/api/health
Swagger: http://127.0.0.1:8000/docs

## Start Web (second terminal)

```cmd
cd web
npm install
npm run dev
```

Web UI: http://localhost:5173

Run `npm run build` to validate frontend types and production bundle.
Run `npm run lint` for static checks.

## Scope of Foundation

Completed in this phase:
- Responsive shell, compact sidebar, light/dark mode, dashboard and module navigation.
- Actual API health monitoring; unavailable modules are clearly identified as pending.
- FastAPI settings, CORS, SQLite connection and baseline Alembic infrastructure.

Not implemented: product CRUD, video imports, cloud storage, thumbnail generation, playlist execution, OBS or AI. Dashboard shows em dashes rather than fake numbers.

## Storage and security

Development database is `backend/data/sarae_live.db` (ignored by Git). Use `backend/.env.example` as the basis for local configuration. Before exposing the service beyond localhost, implement authentication and suitable access controls. Desktop-local video folders are not accessible to remote browsers without an authorized server/agent.

# TikTok LIVE product pin (Windows Agent)

Trong **LIVE Control**, chọn sản phẩm rồi bấm **Ghim**, hoặc bật **Tự động ghim theo video**.
Windows Agent phải đang online, phiên LIVE phải gắn TikTok Account đã xác minh, và Chrome điều khiển
phải mở đúng LIVE Console đã đăng nhập. Dùng chức năng đồng bộ LIVE Console hiện có trước khi ghim để
Agent xác nhận tài khoản. Toggle tự động ghim mặc định **tắt** mỗi lần Agent khởi động.

Sarae dùng TikTok Product ID đã lưu khi card cho phép xác minh ID trực tiếp. Nếu card không lộ ID,
Agent đối chiếu tên, ảnh và giá với sản phẩm trong catalog rồi click nút DOM trên đúng card. Khi có
nhiều card khớp, LIVE Control hiện từng card để người vận hành xác nhận; Agent không tự chọn theo vị trí
hoặc tên gần giống. Nếu card không còn tồn tại khi xác nhận, lệnh thất bại và cần xem lại.

Auto-pin chỉ chạy sau khi Agent xác nhận playback đã chuyển sản phẩm. Lệnh cũ bị hủy nếu video đã chuyển
tiếp; Agent không bỏ ghim sản phẩm cũ trước khi ghim sản phẩm mới. **Bỏ ghim** là thao tác thủ công.
Thời gian hiển thị card 30 giây của TikTok không kích hoạt unpin hoặc repin. Các thao tác dùng DOM trong
Chrome đang đăng nhập, không gửi request đến API nội bộ TikTok.

**Đã kiểm thử:** mapping ID và descriptor, trường hợp trùng card cần xác nhận, pin/unpin và đọc lại DOM,
loại lệnh auto-pin cũ bằng mock DOM; build Web. Người vận hành báo đã dùng được ghim/bỏ ghim trên Console.
**Cần xác minh thêm trên LIVE Console thực tế:** auto-pin khi chuyển video liên tiếp, card trùng thông tin,
và các biến thể DOM/ảnh/giá của TikTok. Không tự động thao tác trên một phiên LIVE thật khi chưa được phép.
