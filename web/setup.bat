@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo PS2 Save Tool Web - Local Setup
echo ================================
echo.

if not exist "backend\requirements.txt" (
    echo [FAIL] backend\requirements.txt was not found.
    echo        Keep setup.bat in the web project root.
    exit /b 1
)
if not exist "frontend\package.json" (
    echo [FAIL] frontend\package.json was not found.
    echo        Keep setup.bat in the web project root.
    exit /b 1
)

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=python"
    )
)
if not defined PYTHON_CMD (
    echo [FAIL] Python 3.10 or newer is required.
    exit /b 1
)
for /f "delims=" %%V in ('%PYTHON_CMD% --version 2^>^&1') do echo [ OK ] %%V

where node >nul 2>nul
if errorlevel 1 (
    echo [FAIL] Node.js is required. Node.js 22 LTS is recommended.
    exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [FAIL] npm is required. Install Node.js/npm and try again.
    exit /b 1
)
node -e "const [a,b]=process.versions.node.split('.').map(Number); process.exit(a>20 || (a===20 ^&^& b>=19) ? 0 : 1)" >nul 2>nul
if errorlevel 1 (
    for /f "delims=" %%V in ('node --version') do echo [FAIL] Node.js 20.19+ is required; Node.js 22 LTS is recommended. Found %%V.
    exit /b 1
)
for /f "delims=" %%V in ('node --version') do set "NODE_VER=%%V"
for /f "delims=" %%V in ('npm --version') do set "NPM_VER=%%V"
echo [ OK ] Node %NODE_VER% / npm %NPM_VER%

if not exist "backend\.venv\Scripts\python.exe" (
    echo [INFO] Creating backend Python virtual environment...
    %PYTHON_CMD% -m venv "backend\.venv"
    if errorlevel 1 (
        echo [FAIL] Could not create backend\.venv.
        exit /b 1
    )
) else (
    echo [ OK ] Backend virtual environment already exists.
)

set "VENV_PY=%CD%\backend\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [FAIL] Backend virtual environment is incomplete.
    exit /b 1
)

echo [INFO] Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

echo [INFO] Installing backend requirements...
"%VENV_PY%" -m pip install -r "backend\requirements.txt"
if errorlevel 1 exit /b 1
echo [ OK ] Backend dependencies installed.

echo [INFO] Installing frontend dependencies...
pushd frontend
call npm ci
set "NPM_RESULT=%ERRORLEVEL%"
popd
if not "%NPM_RESULT%"=="0" (
    echo [FAIL] npm ci failed.
    exit /b %NPM_RESULT%
)
echo [ OK ] Frontend dependencies installed.

echo.
echo Setup completed successfully.
echo Nothing was started automatically.
echo.
echo Start both local services with:
echo   start-local.bat
echo.
echo Frontend: http://localhost:5173
echo Backend:  http://localhost:6361
echo.
exit /b 0
