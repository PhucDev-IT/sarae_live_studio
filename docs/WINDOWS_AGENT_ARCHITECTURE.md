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
│  │ ipc · runtime · activation(DPAPI) · cloud(worker) · media · playback │   │ chỉ  │ (metadata, lệnh)    │
│  │ obs · monitoring(CAPTCHA) · voice(TTS) · windows(ctypes Win32)       │   │ đi ra└─────────────────────┘
│  └───────┬──────────────────────────┬────────────────────┬─────────────┘   │
│          │ obs-websocket v5         │ chụp vùng cửa sổ   │ đọc metadata   │
│     OBS Studio               TikTok LIVE Studio       Thư mục MP4 local     │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Thành phần | Ngôn ngữ | Trách nhiệm | KHÔNG được làm |
|---|---|---|---|
| `backend/` | Python FastAPI | Identity, license, activation key, hàng đợi lệnh, metadata video, playlist, cảnh báo Telegram | Chứa runtime Windows, import `agent-core`, lưu/stream MP4 |
| `agent-core/` | Python 3.12 | Toàn bộ nghiệp vụ trên máy: Cloud transport, DPAPI, quét MP4, OBS (PlaybackEngine), CAPTCHA, TTS | Có UI, import `backend/app`, mở cổng HTTP local |
| `desktop/` (Rust) | Rust/Tauri 2 | Khởi chạy/dừng sidecar, chuyển request/response/event, cửa sổ, single-instance | Logic OBS/CAPTCHA/media |
| `desktop/` (React) | TypeScript | Giao diện, chọn thư mục/ảnh qua dialog | Chạy shell, gọi Cloud/OBS trực tiếp, lưu credential |

Ranh giới được giữ bằng **hợp đồng**, không bằng import: `backend/app/cloud_agents.py` là nguồn chuẩn của giao thức
lệnh Cloud (`COMMAND_KINDS`, `validate_payload`, vòng đời lệnh), `agent-core/src/sarae_agent/cloud/commands.py` kiểm tra lại
cùng payload; `docs/WINDOWS_AGENT_IPC.md` + `desktop/src/types/ipc.ts` (IPC GUI ↔ Core).

## 2. Module `agent-core/src/sarae_agent`

| Module | Vai trò chính |
|---|---|
| `__main__.py` | Entrypoint: tách stdout cho IPC, singleton mutex, watchdog tiến trình cha, `--self-check` |
| `config.py` | Đường dẫn `%LOCALAPPDATA%\SaraeAgent`, cấu hình có `version` + migration |
| `runtime.py` | `AgentRuntime`: sở hữu mọi service, state machine Agent, watchdog OBS/rescan |
| `ipc/` | `protocol.py` (khung JSON Lines), `server.py` (reader/writer/thread pool), `methods.py` (bảng method) |
| `activation/` | `dpapi.py` (ctypes CryptProtectData), `secrets.py` (file bí mật), `service.py` (kích hoạt) |
| `cloud/` | `client.py` (httpx + mã lỗi), `worker.py` (heartbeat, long-poll, lane, ACK, sự kiện, đồng bộ thư viện), `commands.py` (kind → hành động), `journal.py` (journal lệnh, khóa một tiến trình, outbox sự kiện) |
| `media/library.py` | Thư mục được phép, quét MP4 nền, index `media_id ↔ path`, thời lượng + thumbnail JPEG (OpenCV), kiểm tra quyền phát |
| `playback/engine.py` | `PlaybackEngine` port từ `backend/app/playback_engine.py`: playlist, phiên sản phẩm (`product_play` immediate/after_current), dual A/B, watchdog chống đứng hình |
| `playback/service.py` | Góc nhìn `PlaybackStatus` cho GUI + đánh dấu `needs_manual_resume` |
| `obs/controller.py` | Một kết nối obs-websocket dùng chung (cả `PlaybackEngine` qua `ObsRequests`), tự kết nối lại, mật khẩu DPAPI |
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

Chỉ tồn tại tối đa **một** `CloudWorker` trong Core (khóa `_lock`), Core là singleton theo thư mục dữ liệu, và worker giữ
khóa file `data\agent_commands.lock` (cùng file Agent cũ trong `backend/agent` đã dùng) → không có hai tiến trình cùng nhận lệnh
(`AGENT_ALREADY_RUNNING`). Dừng worker **không** dừng video đang phát (watchdog của `PlaybackEngine` vẫn chạy; sự kiện chờ trong
outbox). Khởi động lại máy/ứng dụng **không** tự phát lại phiên cũ (`needs_manual_resume`).

Luồng (chỉ kết nối ra ngoài): heartbeat 2 s khi đang phát (rảnh: `cloud.heartbeat_seconds`, mặc định 5 s; Cloud coi offline sau 35 s)
gửi `playback` = `PlaybackEngine.status()`; long-poll `GET /agents/commands/{agent_id}/next?wait=25`; một executor cho mỗi lane
(`live`, `background`) nên TTS dài không chặn chuyển sản phẩm; luồng media dò video mới (≤ 20 s mỗi lượt) và đồng bộ thư viện.

### 3.3 Lệnh Cloud (đảm bảo không chạy lặp)

Vòng đời do Cloud sở hữu (`backend/app/cloud_agents.py`):

```text
OWNER Web ──POST /agents/{agent_id}/commands (Idempotency-Key)──▶ PENDING ──hết hạn──▶ TIMEOUT / hủy ──▶ CANCELLED
Agent ──GET .../next?wait=25 (claim, claim_token mới)──▶ RUNNING ──POST /agents/commands/ack {claim_token, success, result}──▶ SUCCESS | FAILED
          └ hết lease mà chưa ACK: kind idempotent & còn lượt ─▶ PENDING (giao lại, token mới); còn lại ─▶ TIMEOUT
```

| Phía Agent | Cách làm |
|---|---|
| Thực thi tối đa một lần | Kết quả ghi vào `command_journal.json` **trước** khi ACK; lệnh giao lại (mất ACK, giao lại do hết lease, Core khởi động lại) chỉ được ACK lại kết quả đã lưu với claim token mới |
| Giao lại khi còn đang chạy | Không chạy lần hai; ACK cuối dùng claim token mới nhất |
| Mất mạng khi ACK | Thử lại 1, 2, 4, 8, 15 s (Cloud vẫn nhận ACK trễ, đánh dấu `late_ack`); 404/409 → dừng thử |
| Kết quả | Thành công: JSON (≤ 60000 ký tự); thất bại: thông điệp lỗi dạng chữ (Web hiển thị nguyên văn) |
| Sự kiện phát | `PRODUCT_SWITCHED` / `PLAYBACK_ENDED` / `ERROR` → `POST /agents/events`, `seq` bắt đầu từ epoch ms, gửi lại an toàn |

## 4. Dữ liệu cục bộ — `%LOCALAPPDATA%\SaraeAgent\`

| Đường dẫn | Nội dung | Bảo vệ |
|---|---|---|
| `config.json` | Cloud URL, OBS host/port/tên nguồn/dual A-B, thư mục, monitor (có `version`) | File thường, không có bí mật |
| `secrets\agent_credential.dpapi.json` | `agent_id`, `agent_credential`, Cloud đã kích hoạt | DPAPI theo Windows user + entropy riêng |
| `secrets\obs.dpapi.json` | Mật khẩu OBS WebSocket | DPAPI |
| `media_index.json` | `media_id → path, filename, size, mtime` | — |
| `media_meta.json`, `thumbs\` | Thời lượng + thumbnail JPEG ≤ 40 KB theo (size, mtime) | Chỉ gửi metadata, không gửi đường dẫn |
| `command_journal.json` | 500 lệnh Cloud gần nhất đã thực thi và kết quả | — |
| `data\agent_commands.lock` | Khóa một tiến trình nhận lệnh | Khóa OS, tự nhả khi tiến trình thoát |
| `playback_state.json` | Phiên phát cuối (chỉ để báo `needs_manual_resume`) | — |
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
| SUPER_ADMIN can thiệp LIVE | API lệnh OWNER chặn SUPER_ADMIN (403) |
| Agent giả mạo Agent khác | Header `X-Sarae-Agent-Key` phải khớp đúng `agent_id` đã đăng ký; ACK phải mang claim token Cloud đã cấp cho lệnh đó |
| Lộ đường dẫn máy khách | Heartbeat/thư viện chỉ có tên file, `media_id` (sha256) và tên EXE; `exe_path` bị bỏ trước khi gửi |
| API cũ chưa tách tenant | Không mount trong SaaS mode + middleware 403 |

## 6. Giới hạn đã biết (trung thực)

* Không có API chính thức điều khiển TikTok LIVE Studio: Agent chỉ **phát hiện** cửa sổ/CAPTCHA, không thao tác.
* Nhận diện CAPTCHA là so khớp cạnh theo ảnh mẫu: lệch tỉ lệ ~10 % làm điểm giảm còn ~0,64 (dưới ngưỡng mặc định 0,82)
  → ảnh mẫu phải được cắt ở **đúng mức Display scaling của Windows** mà TikTok LIVE Studio đang dùng (test `test_monitor.py`).
* Thời lượng + thumbnail được dò dần (≤ 20 s mỗi lượt đồng bộ) sau khi quét; codec chưa được trích xuất.
* Cảnh báo Telegram khi phát hiện CAPTCHA: Agent Core không tự gửi (không giữ bot token trên máy khách) và Cloud hiện chưa
  gửi khi heartbeat chuyển sang `captcha_detected` → cần bổ sung phía Cloud.
* Cloud chưa gửi `X-Sarae-Error`: Agent bị thu hồi hiện báo `LICENSE_EXPIRED` (cùng HTTP 403), mã kích hoạt đã dùng báo
  `ACTIVATION_KEY_INVALID`.
* TTS cần Internet (dịch vụ Microsoft Edge neural voices).
* Bộ cài chưa ký số (code signing) → SmartScreen có thể cảnh báo lần đầu.
