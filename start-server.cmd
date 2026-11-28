@echo off
rem Sarae Studio gateway - server PC nha (docs\HYBRID_STUDIO_SERVER.md, muc "Van hanh server").
rem   start-server.cmd          Chay gateway tai http://127.0.0.1:8000 (cloudflared dua ra https://api.puns.id.vn)
rem   start-server.cmd setup    Chi tao/cap nhat Python env backend\.venv
rem Thu tu: voice server (127.0.0.1:8780) -> start-server.cmd -> cloudflared.
rem Cau hinh trong backend\.env (SARAE_TTS_SERVER_URL, SARAE_TTS_SERVER_TOKEN...). Du lieu: SARAE_DATA_DIR hoac backend\data.
setlocal EnableExtensions
set "ROOT=%~dp0"
set "BACK=%ROOT%backend"
set "PY=%BACK%\.venv\Scripts\python.exe"
set "REQ=%BACK%\requirements-gateway.txt"
set "MODE=%~1"

if exist "%PY%" goto :deps
set "BASEPY="
for %%V in (3.12 3.13 3.11) do if not defined BASEPY (py -%%V -c "import sys" >nul 2>&1 && set "BASEPY=py -%%V")
if not defined BASEPY (python -c "import sys; raise SystemExit(not (3,11) <= sys.version_info[:2] <= (3,13))" >nul 2>&1 && set "BASEPY=python")
if not defined BASEPY (echo [setup] Can Python 3.11-3.13 ^(khuyen nghi 3.12^): https://www.python.org & goto :fail)
echo [setup] Tao Python env backend\.venv bang %BASEPY% ...
%BASEPY% -m venv "%BACK%\.venv" || goto :fail

:deps
fc /b "%REQ%" "%BACK%\.venv\.gateway-requirements" >nul 2>&1 && goto :ready
rem Env da co du goi (vd. tao bang uv, khong co pip): khong cai gi them.
"%PY%" -c "import fastapi, uvicorn, httpx, pydantic_settings" >nul 2>&1 && goto :stamp
echo [setup] Cai dependency gateway ...
"%PY%" -m pip --version >nul 2>&1 && goto :pip
where uv >nul 2>&1 && goto :uv
echo [setup] Env chua co pip, thu ensurepip ...
"%PY%" -m ensurepip --upgrade >nul 2>&1 || (echo [setup] Khong co pip va khong co uv. Cai uv: winget install astral-sh.uv & goto :fail)
:pip
"%PY%" -m pip install --disable-pip-version-check -q -r "%REQ%" || goto :fail
goto :stamp
:uv
uv pip install --python "%PY%" -q -r "%REQ%" || goto :fail
:stamp
copy /y "%REQ%" "%BACK%\.venv\.gateway-requirements" >nul

:ready
if /i "%MODE%"=="setup" (echo [setup] Moi truong da san sang. & goto :done)
if not exist "%BACK%\.env" echo [canh bao] Chua co backend\.env: TTS se bao TTS_UNAVAILABLE cho toi khi dat SARAE_TTS_SERVER_URL va SARAE_TTS_SERVER_TOKEN.
netstat -ano | findstr /r /c:"127.0.0.1:8000 .*LISTENING" >nul && (
  echo Cong 8000 dang duoc dung. Gateway co the dang chay roi.
  goto :fail
)
echo.
echo Sarae Studio gateway: http://127.0.0.1:8000/api/health
echo Giu cua so nay mo va khong cho may ngu. Nhan Ctrl+C de dung.
echo.
pushd "%BACK%" || goto :fail
rem Mot worker duy nhat: hang doi TTS va gioi han request nam trong bo nho cua tien trinh.
"%PY%" -m uvicorn app.gateway_main:app --host 127.0.0.1 --port 8000 --workers 1 --no-server-header
set "RESULT=%ERRORLEVEL%"
popd
if not "%RESULT%"=="0" goto :fail
goto :done

:fail
echo.
echo [loi] Gateway dung voi loi. Doc thong bao phia tren.
pause
endlocal & exit /b 1

:done
endlocal & exit /b 0
