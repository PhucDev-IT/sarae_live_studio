@echo off
rem Customer release build: FFmpeg (pinned, SHA256) -> Python sidecar (customer profile)
rem -> React bundle -> Tauri NSIS installer (per-user).
rem Output: desktop\release\SaraeLiveStudio-Setup-<version>.exe (+ .sha256)
rem Doc: docs\CUSTOMER_INSTALLER.md
rem
rem Optional environment:
rem   SARAE_BUILD_SERVER_URL  account/license server baked into the build (default https://api.puns.id.vn)
rem   SARAE_SIGN_CONFIG       path to a Tauri config fragment with bundle.windows.signCommand
rem                           (see src-tauri\tauri.signing.example.json); unset = unsigned build
setlocal EnableExtensions
set "DESKTOP=%~dp0.."
pushd "%DESKTOP%" || exit /b 1

rem The customer build never carries developer switches, whatever this shell has set.
set "SARAE_BUILD_CHANNEL=customer"
if not defined SARAE_BUILD_SERVER_URL set "SARAE_BUILD_SERVER_URL=https://api.puns.id.vn"
set "SARAE_STUDIO_DEV_UNLICENSED="
echo [build] Server: %SARAE_BUILD_SERVER_URL%

echo [build] FFmpeg (pinned SHA256)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch-ffmpeg.ps1" || goto :fail

call "%~dp0build-sidecar.bat" --no-pause || goto :fail

set "TRIPLE="
for /f "tokens=2" %%t in ('rustc -vV ^| findstr /b "host:"') do set "TRIPLE=%%t"
if "%TRIPLE%"=="" set "TRIPLE=x86_64-pc-windows-msvc"
set "SIDECAR=src-tauri\binaries\sarae-agent-core-%TRIPLE%.exe"
echo [build] Checking the sidecar build profile
powershell -NoProfile -Command "$r = (& '%SIDECAR%' --self-check | Out-String | ConvertFrom-Json); if ($r.build_profile.channel -ne 'customer' -or $r.build_profile.server_url -ne $env:SARAE_BUILD_SERVER_URL.TrimEnd('/')) { Write-Host ('[build] Sai build profile: ' + ($r.build_profile | ConvertTo-Json -Compress)); exit 1 }" || goto :fail

echo [build] npm ci
if exist "package-lock.json" (call npm ci --no-audit --no-fund) else (call npm install --no-audit --no-fund)
if errorlevel 1 goto :fail

set "SIGN_ARGS="
if defined SARAE_SIGN_CONFIG (
  if not exist "%SARAE_SIGN_CONFIG%" (echo [build] Khong thay SARAE_SIGN_CONFIG: %SARAE_SIGN_CONFIG% & goto :fail)
  echo [build] Ky ma bang %SARAE_SIGN_CONFIG%
  set "SIGN_ARGS=--config "%SARAE_SIGN_CONFIG%""
) else (
  echo [build] Khong ky ma ^(SARAE_SIGN_CONFIG chua dat^): Windows SmartScreen se canh bao khi cai.
)

echo [build] tauri build (NSIS)
call npx tauri build --bundles nsis %SIGN_ARGS% || goto :fail

set "VERSION="
for /f "usebackq delims=" %%v in (`node -p "require('./src-tauri/tauri.conf.json').version"`) do set "VERSION=%%v"
set "BUNDLE=src-tauri\target\release\bundle\nsis"
set "OUTPUT=release\SaraeLiveStudio-Setup-%VERSION%.exe"
if not exist "release" mkdir "release"
if exist "%OUTPUT%" del /q "%OUTPUT%"
for %%f in ("%BUNDLE%\*_%VERSION%_x64-setup.exe") do copy /y "%%f" "%OUTPUT%" >nul
if not exist "%OUTPUT%" (echo [build] Khong tim thay installer & goto :fail)
powershell -NoProfile -Command "$h = (Get-FileHash -Algorithm SHA256 -LiteralPath '%OUTPUT%').Hash.ToLowerInvariant(); Set-Content -LiteralPath '%OUTPUT%.sha256' -Value ($h + '  SaraeLiveStudio-Setup-%VERSION%.exe') -Encoding ASCII; Write-Host ('[build] SHA256 ' + $h)" || goto :fail
echo [build] OK: %DESKTOP%\%OUTPUT%
popd
exit /b 0

:fail
echo [build] FAILED
popd
if /i not "%~1"=="--no-pause" pause
exit /b 1
