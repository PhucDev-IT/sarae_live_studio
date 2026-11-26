@echo off
rem Sarae Live Studio - chay nhanh tren Windows.
rem   start-studio.cmd              Mo Studio trong trinh duyet (du lieu that)
rem   start-studio.cmd demo         Mo Studio voi du lieu thu trong .tmp\studio-demo
rem   start-studio.cmd native       Mo cua so Tauri (can Rust); them "rebuild" de build lai sidecar
rem   start-studio.cmd test         Chay test Python + build giao dien
rem   start-studio.cmd setup        Chi cai/cap nhat moi truong
rem Lan dau tu tao Python env va cai dependency; tu cai lai khi pyproject.toml / package-lock.json doi.
setlocal EnableExtensions
set "ROOT=%~dp0"
set "CORE=%ROOT%agent-core"
set "DESK=%ROOT%desktop"
set "PY=%CORE%\.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:1420"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=web"

if /i "%MODE%"=="help" goto :help
if /i "%MODE%"=="-h" goto :help
if /i "%MODE%"=="/?" goto :help
for %%M in (web demo native test setup) do if /i "%MODE%"=="%%M" goto :known
echo Khong hieu che do "%MODE%".
goto :help

:known
call :setup || goto :fail
if /i "%MODE%"=="setup" (echo [setup] Moi truong da san sang. & goto :done)
if /i "%MODE%"=="test" goto :test
if /i "%MODE%"=="native" goto :native
goto :web

rem ---------------------------------------------------------------------------
:setup
where npm >nul 2>&1 || (echo [setup] Can cai Node.js 20+: https://nodejs.org & exit /b 1)
if exist "%PY%" goto :python_deps
set "BASEPY="
for %%V in (3.12 3.13 3.11) do if not defined BASEPY (py -%%V -c "import sys" >nul 2>&1 && set "BASEPY=py -%%V")
if not defined BASEPY (python -c "import sys; raise SystemExit(not (3,11) <= sys.version_info[:2] <= (3,13))" >nul 2>&1 && set "BASEPY=python")
if not defined BASEPY (echo [setup] Can Python 3.11-3.13 ^(khuyen nghi 3.12^): https://www.python.org & exit /b 1)
echo [setup] Tao Python env bang %BASEPY% ...
%BASEPY% -m venv "%CORE%\.venv" || exit /b 1
:python_deps
fc /b "%CORE%\pyproject.toml" "%CORE%\.venv\.studio-pyproject" >nul 2>&1 && goto :node_deps
echo [setup] Cai dependency Python (agent-core) ...
"%PY%" -m pip install --disable-pip-version-check -q -e "%CORE%[dev]" || exit /b 1
copy /y "%CORE%\pyproject.toml" "%CORE%\.venv\.studio-pyproject" >nul
:node_deps
fc /b "%DESK%\package-lock.json" "%DESK%\node_modules\.studio-lock" >nul 2>&1 && goto :tools
echo [setup] Cai dependency giao dien (npm ci) ...
call npm --prefix "%DESK%" ci --no-audit --no-fund || exit /b 1
copy /y "%DESK%\package-lock.json" "%DESK%\node_modules\.studio-lock" >nul
:tools
where ffprobe >nul 2>&1 || echo [canh bao] Chua co FFmpeg/FFprobe tren PATH: nhap video se bao loi.
exit /b 0

rem ---------------------------------------------------------------------------
:web
netstat -ano | findstr /r /c:":1420 .*LISTENING" >nul && (
  echo Studio dang chay roi. Mo %URL%
  start "" "%URL%"
  goto :done
)
rem Mo trinh duyet khi giao dien san sang (dat SARAE_NO_BROWSER=1 de tat).
if not "%SARAE_NO_BROWSER%"=="1" start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest -UseBasicParsing '%URL%' -TimeoutSec 1 | Out-Null; Start-Process '%URL%'; break}catch{Start-Sleep -Milliseconds 700}}"
echo.
echo Sarae Live Studio: %URL%
echo Giu cua so nay mo. Nhan Ctrl+C de dung giao dien va core.
echo.
if /i "%MODE%"=="demo" (
  echo [demo] Du lieu thu: %ROOT%.tmp\studio-demo
  call npm --prefix "%DESK%" run studio -- --data-dir "%ROOT%.tmp\studio-demo"
) else (
  call npm --prefix "%DESK%" run studio
)
if errorlevel 1 goto :fail
goto :done

rem ---------------------------------------------------------------------------
:native
set "REBUILD="
if /i "%~2"=="rebuild" set "REBUILD=--rebuild-core"
call "%DESK%\scripts\dev-windows.bat" %REBUILD%
if errorlevel 1 goto :fail
goto :done

rem ---------------------------------------------------------------------------
:test
pushd "%CORE%" || goto :fail
"%PY%" -m pytest -q -p no:warnings tests\test_studio_replies.py tests\test_studio_live.py tests\test_studio_ai.py ^
  tests\test_studio_tiktok.py tests\test_studio_presenters.py tests\test_studio_storage.py tests\test_studio_transport.py ^
  tests\test_protocol_config.py tests\test_playback_engine.py tests\test_playback_policy.py tests\test_media_playback.py ^
  tests\test_obs_controller.py
set "RESULT=%ERRORLEVEL%"
popd
if not "%RESULT%"=="0" goto :fail
call npm --prefix "%DESK%" run build || goto :fail
echo [test] Tat ca deu qua.
goto :done

rem ---------------------------------------------------------------------------
:help
echo.
echo Cach dung: start-studio.cmd [web^|demo^|native [rebuild]^|test^|setup]
echo   web     Mo Studio trong trinh duyet voi du lieu that (mac dinh)
echo   demo    Nhu web nhung dung du lieu thu .tmp\studio-demo
echo   native  Mo cua so Tauri; "native rebuild" build lai sidecar Python
echo   test    Chay test Python va build giao dien
echo   setup   Chi cai hoac cap nhat moi truong
exit /b 0

:fail
echo.
echo [loi] Khong chay duoc. Xem thong bao phia tren.
pause
exit /b 1

:done
exit /b 0
