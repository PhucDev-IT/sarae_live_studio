@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Vui long tao venv truoc va cai requirements.txt.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-gui.txt
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m agent.gui
