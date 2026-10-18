@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where rustc >nul 2>nul
if errorlevel 1 (echo [ERROR] Install Rust MSVC toolchain and Visual Studio C++ Build Tools.& exit /b 1)
where npm >nul 2>nul
if errorlevel 1 (echo [ERROR] Install Node.js 20+ first.& exit /b 1)
call "%~dp0build-sidecar.bat" --clean
if errorlevel 1 exit /b 1
call npm install
if errorlevel 1 exit /b 1
call npx tauri build
if errorlevel 1 exit /b 1
echo [DONE] Installer output: desktop\src-tauri\target\release\bundle\nsis
