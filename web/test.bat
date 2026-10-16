@echo off
setlocal EnableExtensions
cd /d "%~dp0backend"

if not exist .venv\Scripts\python.exe (
  echo [FAIL] Web dependencies are not set up yet. Run setup.bat first.
  exit /b 1
)

set "PYTHONPATH=core"
.venv\Scripts\python.exe -m unittest discover -s tests -v
exit /b %ERRORLEVEL%
