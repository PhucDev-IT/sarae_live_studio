@echo off
setlocal EnableExtensions
cd /d "%~dp0desktop" || exit /b 1
if not exist "node_modules\vite\bin\vite.js" (
  echo Chua cai dependency giao dien. Chay: npm --prefix desktop ci
  pause
  exit /b 1
)
if not exist "..\agent-core\.venv\Scripts\python.exe" (
  echo Chua tao Python env. Xem docs\LOCAL_TAURI_STUDIO_STATUS.md de cai dat.
  pause
  exit /b 1
)
echo Sarae Live Studio local: http://127.0.0.1:1420
echo Giu cua so nay mo. Nhan Ctrl+C de dung UI va core.
call npm run studio
if errorlevel 1 pause
