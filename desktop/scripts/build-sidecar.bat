@echo off
rem Build the Python Agent Core into a single exe and place it where Tauri expects the sidecar:
rem   desktop\src-tauri\binaries\sarae-agent-core-<rust target triple>.exe
setlocal EnableExtensions
set "DESKTOP=%~dp0.."
set "CORE=%~dp0..\..\agent-core"
pushd "%CORE%" || exit /b 1

if not exist ".venv\Scripts\python.exe" (
  echo [sidecar] Creating agent-core virtualenv
  py -3.12 -m venv .venv || goto :fail
)
echo [sidecar] Installing agent-core dependencies
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e ".[dev]" || goto :fail

set "TRIPLE="
for /f "tokens=2" %%t in ('rustc -vV ^| findstr /b "host:"') do set "TRIPLE=%%t"
if "%TRIPLE%"=="" set "TRIPLE=x86_64-pc-windows-msvc"
echo [sidecar] Target triple: %TRIPLE%

echo [sidecar] Running PyInstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --log-level WARN ^
  --distpath build\dist --workpath build\work build\sarae-agent-core.spec || goto :fail

echo [sidecar] Verifying bundled native dependencies
"build\dist\sarae-agent-core.exe" --self-check || goto :fail

if not exist "%DESKTOP%\src-tauri\binaries" mkdir "%DESKTOP%\src-tauri\binaries"
copy /y "build\dist\sarae-agent-core.exe" "%DESKTOP%\src-tauri\binaries\sarae-agent-core-%TRIPLE%.exe" >nul || goto :fail
echo [sidecar] OK: src-tauri\binaries\sarae-agent-core-%TRIPLE%.exe
popd
exit /b 0

:fail
echo [sidecar] FAILED
popd
exit /b 1
