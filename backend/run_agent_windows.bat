@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [LOI] Chua co Python venv. Cai requirements.txt truoc.
  pause
  exit /b 1
)
if not exist "data\agent_activation.json" (
  echo [SARAE] Agent chua kich hoat. Nhap ma tu SUPER_ADMIN:
  ".venv\Scripts\python.exe" -m agent.activate
  if errorlevel 1 (
    echo [LOI] Kich hoat that bai.
    pause
    exit /b 1
  )
)
if not exist "data\agent_media_folders.json" (
  echo [SARAE] Chon thu muc video local:
  ".venv\Scripts\python.exe" -m agent.configure_media
)
echo [SARAE] Dang khoi dong Windows Agent trong phien Desktop...
".venv\Scripts\python.exe" -m agent.main
echo [SARAE] Agent da dung. Ma loi: %ERRORLEVEL%
pause
