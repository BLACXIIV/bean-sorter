@echo off
rem Dev run on a laptop: webcam + simulated valve + separate dev database.
rem Double-click or run from a terminal. Close this window (or press a key) to stop.
setlocal
cd /d "%~dp0"
set BEAN_DB=data\dev.db
if not exist logs mkdir logs
if not exist var\sessions mkdir var\sessions

echo [dev] starting engine (webcam + simulated valve) ...
for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "(Start-Process -PassThru -WindowStyle Hidden -FilePath '.\.venv\Scripts\python.exe' -ArgumentList 'engine.py','--camera','webcam','--valve','sim' -WorkingDirectory '%CD%' -RedirectStandardOutput 'logs\dev-engine.log' -RedirectStandardError 'logs\dev-engine.err').Id"`) do set ENG_PID=%%P

echo [dev] starting PHP server ...
for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "(Start-Process -PassThru -WindowStyle Hidden -FilePath 'php' -ArgumentList '-S','127.0.0.1:8080','-t','web' -WorkingDirectory '%CD%' -RedirectStandardOutput 'logs\dev-php.log' -RedirectStandardError 'logs\dev-php.err').Id"`) do set PHP_PID=%%P

echo [dev] waiting for engine + PHP (up to 25 s) ...
set READY=0
for /l %%I in (1,1,50) do (
    curl -sf http://127.0.0.1:5000/api/status >nul 2>&1 && curl -sf -o nul http://127.0.0.1:8080/ >nul 2>&1 && set READY=1 && goto :ready
    timeout /t 1 /nobreak >nul
)
:ready
if "%READY%"=="0" (
    echo [dev] FAILED - see logs\dev-engine.log and logs\dev-php.log
    goto :stop
)

echo [dev] opening http://127.0.0.1:8080/ ...
start "" http://127.0.0.1:8080/
echo.
echo Dev servers running. Press any key or close this window to stop them.
pause >nul

:stop
echo [dev] stopping engine + PHP ...
if defined ENG_PID taskkill /PID %ENG_PID% /F >nul 2>&1
if defined PHP_PID taskkill /PID %PHP_PID% /F >nul 2>&1
endlocal
