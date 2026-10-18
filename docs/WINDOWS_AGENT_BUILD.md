# Sarae Windows Agent — Build, kiểm thử & phân phối

## 1. Pipeline

```text
agent-core/ (Python) ──PyInstaller one-file──▶ desktop/src-tauri/binaries/sarae-agent-core-<triple>.exe
                                                     │  (Tauri bundle.externalBin)
desktop/src (React) ──vite build──▶ desktop/dist ────┤
desktop/src-tauri (Rust) ──cargo release──▶ SaraeAgent.exe
                                                     ▼
                                   NSIS ──▶ desktop/release/SaraeAgent-Setup-<version>.exe
```

## 2. Yêu cầu máy build (KHÔNG cần trên máy khách hàng)

| Công cụ | Phiên bản đã dùng | Ghi chú |
|---|---|---|
| Windows 10/11 x64 | 11 (26200) | |
| Python | 3.12 (`py -3.12`) | venv riêng `agent-core/.venv` |
| Node.js / npm | 24 / 11 | |
| Rust | 1.99 (`x86_64-pc-windows-msvc`) | `rustup` |
| Visual Studio Build Tools | VS 2026 (C++ workload) | Linker MSVC |
| NSIS | Tauri tự tải vào `%LOCALAPPDATA%\tauri\NSIS` | |

Máy khách hàng chỉ cần chạy bộ cài: Python, Node, Rust **không** cần. WebView2 được bộ cài tự tải nếu thiếu
(`webviewInstallMode = downloadBootstrapper`).

## 3. Lệnh

| Mục đích | Lệnh (CMD, từ thư mục repo) |
|---|---|
| Build sidecar Python (đúng target triple, có `--self-check`) | `desktop\scripts\build-sidecar.bat` |
| Chạy dev (tạo sidecar nếu chưa có, rồi `tauri dev`) | `desktop\scripts\dev-windows.bat` (thêm `--rebuild-core` khi sửa Python) |
| Build bộ cài | `desktop\scripts\build-windows.bat` |
| Test Agent Core | `cd agent-core && .venv\Scripts\python.exe -m pytest -q` |
| Test Core với Cloud thật | `set SARAE_CLOUD_PYTHON=<venv cloud>\Scripts\python.exe` rồi chạy pytest như trên |
| Test bản sidecar đã đóng gói | `set SARAE_CORE_EXE=agent-core\build\dist\sarae-agent-core.exe` rồi pytest |
| Test Backend (chỉ dependency Cloud) | `cd backend && python -m unittest discover -s tests -p "test_*.py"` |
| Kiểm tra frontend | `cd desktop && npm run build` |

Target triple lấy từ `rustc -vV` (dòng `host:`), mặc định `x86_64-pc-windows-msvc`.

## 4. Tách dependency

| File | Dùng cho | Có Win32/OpenCV/OBS? |
|---|---|---|
| `backend/requirements.txt` | VPS Cloud (Linux/Windows) | **Không** — FastAPI, uvicorn, SQLAlchemy, Alembic, pydantic-settings, multipart, httpx, cryptography |
| `backend/requirements-legacy-local.txt` | (Tuỳ chọn) studio local cũ một người dùng | Có — không cài trên VPS |
| `agent-core/pyproject.toml` | Agent Windows | httpx, pydantic, obsws-python, psutil, mss, numpy, opencv-python-headless, edge-tts (Win32 qua ctypes, không pywin32) |
| `desktop/package.json` | GUI | React 19, Vite 8, TS 6, `@tauri-apps/*`, lucide-react |

Router cũ chỉ nạp OpenCV/mss/pywin32/edge-tts khi được gọi, nên `app.main` khởi động với riêng `requirements.txt`;
trong SaaS mode (`SARAE_SAAS_MODE=true`) middleware chặn các API cũ chưa tách tenant. `backend/app/playback_engine.py` (cần
obsws-python) chỉ còn làm bản gốc cho `agent-core/src/sarae_agent/playback/engine.py`; test của nó tự bỏ qua trên VPS.

## 5. Đường dẫn khi chạy bản cài đặt

| Thứ | Vị trí |
|---|---|
| Ứng dụng (installMode `currentUser`, không cần quyền admin) | `%LOCALAPPDATA%\Sarae Agent\` (`SaraeAgent.exe`, `sarae-agent-core.exe`) |
| Dữ liệu, cấu hình, log, credential DPAPI | `%LOCALAPPDATA%\SaraeAgent\` — không phụ thuộc thư mục làm việc/thư mục EXE |
| Thư viện native của sidecar | Giải nén tạm vào `%TEMP%\_MEI*` mỗi lần chạy (PyInstaller one-file), tự dọn khi Core thoát |

`sarae-agent-core.exe --self-check` in JSON kiểm tra import `cv2`, `numpy`, `mss`, `obsws_python`, `edge_tts`, `httpx`,
`pydantic`, `psutil` và một vòng DPAPI — chạy tự động trong `build-sidecar.bat` và nên chạy lại trên bản đã cài.

## 6. Không commit

`.venv/`, `node_modules/`, `desktop/src-tauri/target/`, `desktop/src-tauri/binaries/*` (trừ README),
`desktop/release/`, `agent-core/build/dist|work/`, `*.egg-info`, dữ liệu `%LOCALAPPDATA%`, credential.

## 7. OBS chuẩn bị trên máy khách

1. OBS 28+ → **Tools → WebSocket Server Settings** → bật, đặt mật khẩu (port mặc định 4455).
2. Tạo **Media Source** tên `Sarae Video` (nguồn video) và `Sarae Voice` (giọng đọc, bật audio trong mixer).
3. Trong Agent: **OBS & Phát** → nhập host/port/mật khẩu → *Lưu & kết nối* → kiểm tra hai nguồn hiện dấu ✓.
