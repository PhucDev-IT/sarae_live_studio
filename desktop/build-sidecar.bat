@echo off
rem Builds the Python Agent Core with PyInstaller and copies it to src-tauri\binaries with the
rem Rust target-triple suffix Tauri expects (sarae-agent-core-x86_64-pc-windows-msvc.exe).
rem Usage: build-sidecar.bat [--clean]
setlocal EnableExtensions
cd /d "%~dp0"
set "BACKEND=%~dp0..\backend"
set "PY=%BACKEND%\.venv\Scripts\python.exe"
if not exist "%PY%" (echo [ERROR] Missing backend venv: create it with "py -3.12 -m venv backend\.venv".& exit /b 1)

set "TARGET="
for /f "tokens=2" %%t in ('rustc -vV 2^>nul ^| findstr /b "host:"') do set "TARGET=%%t"
if not defined TARGET (echo [ERROR] rustc not found. Install Rust MSVC toolchain and reopen the terminal.& exit /b 1)

"%PY%" -m pip install --disable-pip-version-check -q -r "%BACKEND%\requirements.txt" pyinstaller
if errorlevel 1 exit /b 1

set "CLEAN="
if /i "%~1"=="--clean" set "CLEAN=--clean"
pushd "%BACKEND%"
"%PY%" -m PyInstaller --noconfirm %CLEAN% --log-level WARN sarae-agent-core.spec
if errorlevel 1 (popd & exit /b 1)
popd

if not exist "src-tauri\binaries" mkdir "src-tauri\binaries"
copy /Y "%BACKEND%\dist\sarae-agent-core.exe" "src-tauri\binaries\sarae-agent-core-%TARGET%.exe" >nul
if errorlevel 1 (echo [ERROR] Cannot copy sidecar. Close Sarae Agent if it is running.& exit /b 1)
echo [OK] Sidecar: src-tauri\binaries\sarae-agent-core-%TARGET%.exe
