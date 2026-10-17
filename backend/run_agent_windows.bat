@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [LOI] Chua co moi truong Python. Hay tao venv va cai requirements.txt.
  pause
  exit /b 1
)
echo [SARAE] Dang khoi dong Windows Agent trong phien Desktop hien tai...
".venv\Scripts\python.exe" -m agent.main
echo [SARAE] Agent da dung. Ma loi: %ERRORLEVEL%
pause
