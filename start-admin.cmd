@echo off
rem Trang quan ly va clone giong cho admin - chi mo tren PC server: http://127.0.0.1:8001
rem Khong dua qua cloudflared (tunnel chi tro toi gateway :8000). Can voice server dang chay (127.0.0.1:8780).
rem Lan dau chay start-server.cmd setup de tao backend\.venv.
setlocal EnableExtensions
set "ROOT=%~dp0"
set "BACK=%ROOT%backend"
set "PY=%BACK%\.venv\Scripts\python.exe"
if not defined SARAE_ADMIN_PORT set "SARAE_ADMIN_PORT=8001"
if not exist "%PY%" (echo [loi] Chua co backend\.venv. Chay "start-server.cmd setup" truoc. & goto :fail)
netstat -ano | findstr /r /c:"127.0.0.1:%SARAE_ADMIN_PORT% .*LISTENING" >nul && (
  echo Trang quan ly giong dang chay roi: http://127.0.0.1:%SARAE_ADMIN_PORT%
  start "" "http://127.0.0.1:%SARAE_ADMIN_PORT%"
  echo Da mo trinh duyet. Cua so nay tu dong sau 5 giay.
  timeout /t 5 >nul
  goto :done
)
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:%SARAE_ADMIN_PORT%/' -TimeoutSec 1 | Out-Null; Start-Process 'http://127.0.0.1:%SARAE_ADMIN_PORT%'; break}catch{Start-Sleep -Milliseconds 700}}"
echo Quan ly giong: http://127.0.0.1:%SARAE_ADMIN_PORT%   (Ctrl+C de dung)
pushd "%BACK%" || goto :fail
"%PY%" -m uvicorn app.admin_main:app --host 127.0.0.1 --port %SARAE_ADMIN_PORT% --workers 1 --no-server-header
set "RESULT=%ERRORLEVEL%"
popd
if not "%RESULT%"=="0" goto :fail
goto :done
:fail
echo.
echo [loi] Trang quan ly dung voi loi. Doc thong bao phia tren.
pause
endlocal & exit /b 1
:done
endlocal & exit /b 0
