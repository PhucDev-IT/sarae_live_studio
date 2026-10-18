@echo off
setlocal
cd /d "%~dp0"
call "%~dp0build-sidecar.bat"
if errorlevel 1 exit /b 1
call npm install
if errorlevel 1 exit /b 1
call npx tauri dev
