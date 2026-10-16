@echo off
setlocal EnableExtensions
cd /d "%~dp0"

python -m unittest discover -s tests -v
exit /b %ERRORLEVEL%
