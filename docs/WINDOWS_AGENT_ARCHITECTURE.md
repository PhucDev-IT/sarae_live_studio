# Sarae Windows Agent — Kiến trúc

## 1. Ba ứng dụng độc lập

```text
┌──────────────────────── Máy Windows của khách hàng ────────────────────────┐      ┌──────── VPS ────────┐
│ desktop/  (Tauri 2: Rust + React/TS)                                        │      │ backend/ FastAPI    │
│  ┌──────────────┐  invoke(core_request)  ┌────────────────┐                 │      │                     │
│  │ React UI     │ ─────────────────────▶ │ CoreManager    │                 │      │ /api/identity/*     │
│  │ (không shell)│ ◀── core://event ───── │ (Rust)         │                 │      │ /api/agents/*       │
│  └──────────────┘    core://state        └───────┬────────┘                 │      │ /api/agent-library  │
│                                stdin JSONL ▲     │ ▼ stdout JSONL           │      │ /api/agent-playlists│
│  agent-core/  (Python sidecar, PyInstaller one-file, không UI)              │HTTPS │                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │◀────▶│ SQLite registries   │
│  │ ipc · runtime · activation(DPAPI) · cloud(worker) · media · playlist │   │ chỉ  │ (metadata, lệnh)    │
│  │ obs · monitoring(CAPTCHA) · voice(TTS) · windows(ctypes Win32)       │   │ đi ra└─────────────────────┘
│  └───────┬──────────────────────────┬────────────────────┬─────────────┘   │
│          │ obs-websocket v5         │ chụp vùng cửa sổ   │ đọc metadata   │
│     OBS Studio               TikTok LIVE Studio       Thư mục MP4 local     │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Thành phần | Ngôn ngữ | Trách nhiệm | KHÔNG được làm |
|---|---|---|---|
| `backend/` | Python FastAPI | Identity, license, activation key, hàng đợi lệnh, metadata video, playlist, cảnh báo Telegram | Chứa runtime Windows, import `agent-core`, lưu/stream MP4 |
| `agent-core/` | Python 3.12 | Toàn bộ nghiệp vụ trên máy: Cloud transport, DPAPI, quét MP4, OBS, playlist, CAPTCHA, TTS | Có UI, import `backend/app`, mở cổng HTTP local |
| `desktop/` (Rust) | Rust/Tauri 2 | Khởi chạy/dừng sidecar, chuyển request/response/event, cửa sổ, single-instance | Logic OBS/CAPTCHA/media |
| `desktop/` (React) | TypeScript | Giao diện, chọn thư mục/ảnh qua dialog | Chạy shell, gọi Cloud/OBS trực tiếp, lưu credential |

Ranh giới được giữ bằng **hợp đồng**, không bằng import: `backend/app/agent_contract.py` (kiểu lệnh + payload Cloud),
`docs/WINDOWS_AGENT_IPC.md` + `desktop/src/types/ipc.ts` (IPC GUI ↔ Core).

## 2. Module `agent-core/src/sarae_agent`

| Module | Vai trò chính |
|---|---|
| `__main__.py` | Entrypoint: tách stdout cho IPC, singleton mutex, watchdog tiến trình cha, `--self-check` |
| `config.py` | Đường dẫn `%LOCALAPPDATA%\SaraeAgent`, cấu hình có `version` + migration |
| `runtime.py` | `AgentRuntime`: sở hữu mọi service, state machine Agent, watchdog OBS/rescan |
| `ipc/` | `protocol.py` (khung JSON Lines), `server.py` (reader/writer/thread pool), `methods.py` (bảng method) |
| `activation/` | `dpapi.py` (ctypes CryptProtectData), `secrets.py` (file bí mật), `service.py` (kích hoạt) |
| `cloud/` | `client.py` (httpx + mã lỗi), `worker.py` (heartbeat/poll/ACK/backoff), `commands.py` (kind → hành động) |
| `media/library.py` | Thư mục được phép, quét MP4 nền, index `media_id ↔ path`, kiểm tra quyền phát |
| `playlist/engine.py` | Play/pause/resume/stop/next/previous/loop, tự chuyển bài khi OBS báo hết |
| `obs/controller.py` | Một kết nối obs-websocket dùng chung, tự kết nối lại, đọc lại cấu hình để xác nhận |
| `monitoring/` | `studio.py` (tìm cửa sổ theo tên EXE), `captcha.py` (chụp vùng + OpenCV template matching) |
| `voice/tts.py` | edge-tts có timeout → phát qua Media Source riêng trong OBS |
| `windows/win32.py` | EnumWindows, tên tiến trình, kiểm tra PID, named mutex (ctypes, không pywin32) |
| `diagnostics.py` | Gói zip hỗ trợ (log + cấu hình đã che + trạng thái), không chứa credential |

## 3. Vòng đời

### 3.1 Tiến trình

```text
Mở app ─▶ Rust spawn sidecar (--ipc --parent-pid <GUI>) ─▶ core.ready ─▶ phase=ready
Đóng cửa sổ ─▶ ExitRequested(prevent) ─▶ system.shutdown ─▶ Core dừng worker/monitor/OBS ─▶ exit 0
                                         └─ quá 20 s ─▶ kill (Core con tự thoát nhờ watchdog cha)
Core chết bất ngờ ─▶ phase=crashed ─▶ tự khởi động lại (tối đa 3 lần / 5 phút) ─▶ sau đó chờ người dùng
```

| Tình huống | Cơ chế chống orphan / trùng | Đã kiểm thử |
|---|---|---|
| Mở app lần 2 | `tauri-plugin-single-instance` focus cửa sổ cũ | — (thủ công) |
| 2 Core cùng thư mục dữ liệu | Named mutex `Local\SaraeAgentCore.v1.<hash>` → `CORE_ALREADY_RUNNING`, exit 3 | `test_single_core_per_data_folder` |
| GUI chết | Core theo dõi `--parent-pid` + PID cha trực tiếp | `test_parent_exit_stops_core` |
| Kill cứng bootloader PyInstaller | Core con thấy cha trực tiếp mất → tự thoát | `test_hard_kill_of_spawned_process_leaves_no_core` |
| stdin đóng | Reader gặp EOF → shutdown | `test_stdin_close_stops_core` |

### 3.2 Agent (Cloud worker)

```text
 stopped ──start──▶ starting ──heartbeat OK──▶ running ◀──────▶ disconnected (backoff 2s→60s ±20%)
    ▲                    │                        │
    │                    └──────── lỗi fatal ─────┴──▶ error  (AGENT_REVOKED / LICENSE_EXPIRED / AGENT_UNAUTHORIZED)
    └────── stopping ◀── stop ─────────────────────────┘
```

Chỉ tồn tại tối đa **một** `CloudWorker` trong Core (khóa `_lock`), và Core là singleton → không có heartbeat/poll trùng.
Dừng worker **không** dừng playlist đang phát (an toàn LIVE). Khởi động lại máy/ứng dụng **không** tự phát lại playlist cũ
(`needs_manual_resume`).

### 3.3 Lệnh Cloud (đảm bảo không chạy lặp)

```text
OWNER Web ──POST /agents/commands──▶ pending
Agent ──GET .../next──▶ delivered (attempt n) ──ACK accepted──▶ accepted ──ACK running──▶ running ──ACK succeeded/failed──▶ completed/failed
          │ mất phản hồi: sau 15 s gửi lại CÙNG command_id (tối đa 3) — Agent dedupe theo command_id
          │ pending > 120 s ─▶ failed (không chạy lệnh LIVE cũ khi Agent online lại)
          └ accepted/running không tiến độ > 90 s ─▶ failed
```

| Trạng thái Agent báo (`status`) | Lưu ở Cloud (`state`) | Ý nghĩa |
|---|---|---|
| `accepted` | `accepted` | Agent đã nhận, đã kiểm tra trùng |
| `running` | `running` | Đang thực thi (gửi lại mỗi 30 s nếu lâu) |
| `succeeded` | `completed` | Thực thi xong thật sự (OBS đã trả lời) |
| `failed` | `failed` | Có mã lỗi trong `result` |

## 4. Dữ liệu cục bộ — `%LOCALAPPDATA%\SaraeAgent\`

| Đường dẫn | Nội dung | Bảo vệ |
|---|---|---|
| `config.json` | Cloud URL, OBS host/port/tên nguồn, thư mục, monitor (có `version`) | File thường, không có bí mật |
| `secrets\agent_credential.dpapi.json` | `agent_id`, `agent_credential`, Cloud đã kích hoạt | DPAPI theo Windows user + entropy riêng |
| `secrets\obs.dpapi.json` | Mật khẩu OBS WebSocket | DPAPI |
| `media_index.json` | `media_id → path, filename, size, mtime` | — |
| `playback_state.json` | Playlist cuối (chỉ để báo `needs_manual_resume`) | — |
| `monitor\templates\`, `monitor\captures\` | Ảnh mẫu CAPTCHA, ảnh lúc phát hiện (giữ 50) | — |
| `voice\`, `logs\agent.log*`, `diagnostics\` | MP3 TTS (giữ 20), log xoay vòng 5×2 MB, gói zip | Log đã che bí mật |

Nâng cấp từ Agent cũ (nhúng trong `backend/`): lần chạy đầu tự nhập `data\agent_gui_settings.json`, thư mục,
index và **credential DPAPI cũ** → giữ nguyên `agent_id`.

## 5. Bảo mật & multi-tenant

| Rủi ro | Biện pháp |
|---|---|
| Lộ credential thiết bị | DPAPI; không bao giờ trả về GUI/log; credential chỉ gửi tới Cloud đã kích hoạt (`CLOUD_URL_MISMATCH`) |
| GUI bị chèn script | CSP chặt; capability chỉ `dialog:allow-open`, `opener:allow-reveal-item-in-dir`; không có quyền shell; Core chỉ nhận method trong bảng |
| Phát file tuỳ ý | Chỉ nhận `media_id` (sha256 hex); phải nằm trong thư mục được phép, đúng `.mp4`, không theo junction ra ngoài |
| OWNER A điều khiển máy OWNER B | Mọi API OWNER tra `owner_id` từ registry, không tin client; test 2 OWNER |
| SUPER_ADMIN can thiệp LIVE | `queue_command`/playlist write chặn SUPER_ADMIN (403) |
| Agent giả mạo Agent khác | Header `X-Sarae-Agent-Key` phải khớp đúng `agent_id` đã đăng ký (401) |
| Bot token Telegram trên máy khách | Cloud gửi cảnh báo khi heartbeat chuyển sang `captcha_detected` |
| API cũ chưa tách tenant | Không mount trong SaaS mode + middleware 403 |

## 6. Giới hạn đã biết (trung thực)

* Không có API chính thức điều khiển TikTok LIVE Studio: Agent chỉ **phát hiện** cửa sổ/CAPTCHA, không thao tác.
* Nhận diện CAPTCHA là so khớp cạnh theo ảnh mẫu: lệch tỉ lệ ~10 % làm điểm giảm còn ~0,64 (dưới ngưỡng mặc định 0,82)
  → ảnh mẫu phải được cắt ở **đúng mức Display scaling của Windows** mà TikTok LIVE Studio đang dùng (test `test_monitor.py`).
* Duration/codec MP4 chưa được trích xuất (chỉ `filename`, `size_bytes`, `modified_ns`) để tránh mở 10.000 file khi quét.
* TTS cần Internet (dịch vụ Microsoft Edge neural voices).
* Bộ cài chưa ký số (code signing) → SmartScreen có thể cảnh báo lần đầu.
