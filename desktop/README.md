# Sarae Windows Agent GUI — Tauri + React + Python Core

**New direction (Oct 2026):** Tauri 2 + React GUI and Python sidecar. Legacy Flutter Windows sources are being removed; the Tauri app uses `desktop/src`, `desktop/src-tauri` and `desktop/package.json`. Previous Flutter instructions are no longer the primary Agent GUI.

## Architecture

- React/TypeScript UI handles activation form, folder picker, start/stop, logs.
- Tauri runs a packaged Python sidecar (`sarae-agent-core.exe`) as a child process, communicating over JSON lines on stdin/stdout.
- Python handles Cloud HTTPS requests, DPAPI credentials, local files, OBS websocket and TikTok CAPTCHA/window monitoring.
- Cloud FastAPI deployment is separate: **no Tauri, Node.js, PySide6 or Windows GUI dependencies on VPS**.

## Windows development

Requirements: Rust MSVC toolchain, Visual Studio C++ Build Tools, Node.js/npm, Python backend virtual environment, WebView2.

```bat
cd desktop
prepare-dev.bat
```

This builds the Python sidecar for the installed Rust target and runs `npx tauri dev`.

## Windows installer

```bat
cd desktop
build-windows.bat
```

Output (if Windows build succeeds): `desktop/src-tauri/target/release/bundle/nsis`.

The customer uses one Windows installer/GUI, though it internally contains the Tauri executable and packaged Python sidecar. Customer does not need to install Python.

## Setup

1. SUPER_ADMIN issues activation code in Sarae Web.
2. Enter Cloud URL ending in `/api`, save, paste activation code, activate.
3. Select local MP4 folders, start Agent.
4. GUI sends allowlisted IPC commands to Python; Agent polls Cloud and controls OBS. Videos stay on Windows.

Packaged Agent writes settings and DPAPI-encrypted credential to `%LOCALAPPDATA%\SaraeAgent\data`.

## Important caveats

This source implementation **has not been built or tested on a real Windows machine here**. Complete a Tauri/NSIS/sidecar/OBS smoke test before commercial distribution. No signed installer, system tray, automatic Windows login startup or updater is implemented yet.

The deprecated PySide6 GUI launcher, PySide6 requirements and old build script have been removed. Neither PySide6 nor Flutter is required for the Tauri desktop or Cloud VPS.

The shared single-tenant modules remain blocked under `SARAE_SAAS_MODE=true` until migrated by OWNER.

## Cleaning legacy Flutter files

`git pull origin dev` updates your local checkout. To inspect remaining tracked Flutter files, run `powershell -ExecutionPolicy Bypass -File scripts/remove-legacy-flutter.ps1` from the repository root. To remove them after review, run the same script with `-Apply`, then review `git diff --cached --stat` and commit the removal. Do not remove the `desktop/src` or `desktop/src-tauri` folders, or the local Python Agent Core.
