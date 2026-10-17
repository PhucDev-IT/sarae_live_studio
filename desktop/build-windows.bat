@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where rustc >nul 2>nul
if errorlevel 1 (echo [ERROR] Install Rust MSVC toolchain and Visual Studio C++ Build Tools.& exit /b 1)
where npm >nul 2>nul
if errorlevel 1 (echo [ERROR] Install Node.js 20+ first.& exit /b 1)
if not exist "..\backend\.venv\Scripts\python.exe" (echo [ERROR] Missing backend Python venv.& exit /b 1)
set "PY=..\backend\.venv\Scripts\python.exe"
%PY% -m pip install -r "..\backend\requirements.txt" pyinstaller
if errorlevel 1 exit /b 1
if not exist "src-tauri\binaries" mkdir "src-tauri\binaries"
pushd "..\backend"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --console --name sarae-agent-core --collect-submodules app --collect-submodules agent agent\ipc.py
if errorlevel 1 (popd & exit /b 1)
popd
for /f %%t in ('rustc -vV ^| findstr "host:"') do set "TARGET=%%t"
rem rustc -vV prints "host: x86_64-pc-windows-msvc"; extract exact triple instead
for /f "tokens=2" %%t in ('rustc -vV ^| findstr /b "host:"') do set "TARGET=%%t"
if not defined TARGET (echo [ERROR] Cannot determine Rust target.& exit /b 1)
copy /Y "..\backend\dist\sarae-agent-core.exe" "src-tauri\binaries\sarae-agent-core-%TARGET%.exe" >nul
if errorlevel 1 exit /b 1
call npm install
if errorlevel 1 exit /b 1
call npx tauri build
if errorlevel 1 exit /b 1
echo [DONE] Installer output: desktop\src-tauri\target\release\bundle\nsis
