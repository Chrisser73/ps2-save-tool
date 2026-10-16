@echo off
setlocal
cd /d "%~dp0"

pyw -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    start "" pyw -3 ps2_save_tool.py %*
    exit /b 0
)

pythonw -c "import sys" >nul 2>nul
if not errorlevel 1 (
    start "" pythonw ps2_save_tool.py %*
    exit /b 0
)

py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    py -3 ps2_save_tool.py %*
    exit /b %errorlevel%
)

python -c "import sys" >nul 2>nul
if not errorlevel 1 (
    python ps2_save_tool.py %*
    exit /b %errorlevel%
)

echo Python 3 was not found.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
pause
exit /b 1
