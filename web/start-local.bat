@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo PS2 Save Tool Web - Local Start
echo ================================
echo.

set "VENV_PY=%CD%\backend\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [FAIL] backend\.venv is missing. Run setup.bat first.
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo [FAIL] frontend\node_modules is missing. Run setup.bat first.
    exit /b 1
)

call :port_open 6361
if "%PORT_OPEN%"=="1" (
    echo [SKIP] Backend is already running on port 6361.
) else (
    echo [START] Backend on port 6361...
    start "PS2 Save Tool Backend" /D "%CD%\backend" cmd /k ""%VENV_PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 6361"
)

call :port_open 5173
if "%PORT_OPEN%"=="1" (
    echo [SKIP] Frontend is already running on port 5173.
) else (
    echo [START] Frontend on port 5173...
    start "PS2 Save Tool Frontend" /D "%CD%\frontend" cmd /k "npm run dev"
)

echo.
echo Frontend: http://localhost:5173
echo Backend:  http://localhost:6361
echo API health: http://localhost:6361/api/health
echo.
echo For another device on your LAN, open http://SERVER-IP:5173
echo.
exit /b 0

:port_open
set "PORT_OPEN=0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = New-Object System.Net.Sockets.TcpClient; try { $r = $c.BeginConnect('127.0.0.1', %~1, $null, $null); if (-not $r.AsyncWaitHandle.WaitOne(350)) { exit 1 }; $c.EndConnect($r); exit 0 } catch { exit 1 } finally { $c.Close() }" >nul 2>nul
if not errorlevel 1 set "PORT_OPEN=1"
exit /b 0
