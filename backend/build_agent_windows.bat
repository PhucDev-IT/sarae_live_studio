@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo [LOI] Tao Python venv trong backend truoc.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-gui.txt
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name SaraeAgent --collect-all PySide6 --collect-submodules app --collect-submodules agent agent\gui.py
if errorlevel 1 (
 echo [LOI] Dong goi that bai.
 pause
 exit /b 1
)
echo [THANH CONG] Xem dist\SaraeAgent\SaraeAgent.exe
pause
