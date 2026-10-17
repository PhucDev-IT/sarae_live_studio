@echo off
setlocal
cd /d "%~dp0"
if not exist "..\backend\.venv\Scripts\python.exe" (echo Create backend Python venv first.& exit /b 1)
"..\backend\.venv\Scripts\python.exe" -m pip install -r "..\backend\requirements.txt" pyinstaller
if errorlevel 1 exit /b 1
pushd "..\backend"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --console --name sarae-agent-core --collect-submodules app --collect-submodules agent agent\ipc.py
if errorlevel 1 (popd & exit /b 1)
popd
for /f "tokens=2" %%t in ('rustc -vV ^| findstr /b "host:"') do set "TARGET=%%t"
if not exist "src-tauri\binaries" mkdir "src-tauri\binaries"
copy /Y "..\backend\dist\sarae-agent-core.exe" "src-tauri\binaries\sarae-agent-core-%TARGET%.exe" >nul
if errorlevel 1 exit /b 1
call npm install
if errorlevel 1 exit /b 1
call npx tauri dev
