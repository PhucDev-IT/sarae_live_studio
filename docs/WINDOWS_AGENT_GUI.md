# Sarae Windows Agent GUI (PySide6) — Developer Preview

The former `run_agent_windows.bat` is terminal-only. The new desktop app is `backend/agent/gui.py`, with an optional PyInstaller `.exe` build.

## Functions

- Dark desktop Qt GUI (no web browser required).
- Configure Cloud API HTTPS URL; local HTTP is allowed only for localhost development.
- Enter one-time activation code issued from Sarae SUPER_ADMIN; activation returns Agent ID and device credential protected using Windows DPAPI.
- Choose one or more Windows directories that contain local MP4 videos. The Cloud receives only metadata; OBS opens Windows-local paths.
- Start/stop Agent worker while UI stays responsive; observe Vietnamese connection events and CAPTCHA status.
- Closing the window minimizes to System Tray when available. Tray menu: show window / quit.
- Optional auto-run of the Agent worker **when the GUI opens**; this does NOT yet register Windows login startup.
- Agent run loop, cloud queue, OBS, TTS and CAPTCHA monitor remain in existing Python modules.

## Run from source (Windows 10/11)

```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-gui.txt
.venv\Scripts\python.exe -m agent.gui
```

Or double click `backend/run_agent_gui.bat`.

For first activation, supply Cloud URL ending in `/api`, paste the activation key, then click **Kích hoạt thiết bị**. Use **Thêm thư mục** for MP4 folders, then **Bắt đầu Agent**. The GUI reads logs, control events and the same Agent account as `python -m agent.main`. **Do not run the terminal Agent and GUI Agent simultaneously** against the same Agent ID or OBS input.

## Build executable on Windows

```text
backend/build_agent_windows.bat
```

Uses PyInstaller `--onedir --windowed` to generate `backend/dist/SaraeAgent/SaraeAgent.exe` together with bundled dependencies. Copy the **entire** `SaraeAgent` folder; `SaraeAgent.exe` on its own is not a standalone installer. The build can be large due to PySide6, OpenCV and NumPy. No signed installer or auto-updater exists yet.

When frozen, runtime files are stored under `%LOCALAPPDATA%\SaraeAgent\data`. For Python source runs, they remain under `backend/data`. Credentials are protected with Windows DPAPI tied to the Windows login. Re-activation is needed for a newly installed fresh data directory unless credentials are migrated securely.

## Known limits / pending production work

- Windows GUI, OBS integration and bundled binary **have not been tested on a real Windows computer yet**. Run the source GUI first, then build/test the executable.
- Workstation GUI does not yet have an automatic Windows startup registry entry, crash recovery supervisor or updater.
- Desktop activation is synchronous with a 15-second request timeout; a slow network may briefly freeze the GUI during activation.
- Manual media scan may briefly freeze GUI with very large directories; scheduled Agent sync remains in the worker.
- Requires interactive Windows user session for desktop capture, not Session 0 Windows Service.
- Commercial SaaS backend legacy modules remain blocked when `SARAE_SAAS_MODE=true` until all resources are tenant scoped.
