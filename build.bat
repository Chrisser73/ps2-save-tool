@echo off
setlocal EnableExtensions EnableDelayedExpansion

title PS2 Save Tool - Builder
cd /d "%~dp0"

set "ROOT=%~dp0"
set "SOURCE_FILE=%ROOT%ps2_save_tool.py"
set "ICO_FILE=%ROOT%app.ico"
set "PNG_FILE=%ROOT%app.png"
set "DIST_DIR=%ROOT%_build"
set "INTERNAL_NAME=PS2SaveTool"

cls
echo.
echo ============================================================
echo  PS2 Save Tool - Production Builder
echo ============================================================
echo.

if not exist "%SOURCE_FILE%" (
    echo [ERROR] ps2_save_tool.py was not found.
    echo         Keep build.bat in the project root.
    goto :failed
)
echo [OK] Source file found.

if not exist "%ICO_FILE%" (
    echo.
    echo [ERROR] app.ico was not found.
    echo         Windows needs app.ico for the baked EXE icon and title-bar icon.
    echo         Put app.ico beside build.bat and run the builder again.
    goto :failed
)
echo [OK] app.ico found - it will be baked into the EXE.

if exist "%PNG_FILE%" (
    echo [OK] app.png found - it will be bundled into the EXE for the in-app logo.
) else (
    echo [INFO] app.png was not found. The application will use the bundled app.ico instead.
)
echo.

rem ------------------------------------------------------------
rem Find Python 3.10+
rem ------------------------------------------------------------
set "PYTHON_CMD="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python 3.10 or newer was not found.
    echo         Install Python 3 and enable "Add Python to PATH".
    echo         https://www.python.org/downloads/
    goto :failed
)

echo [OK] Python:
%PYTHON_CMD% --version
echo.

rem ------------------------------------------------------------
rem Dependency checks
rem ------------------------------------------------------------
%PYTHON_CMD% -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] pip was not found.
    echo         Try: %PYTHON_CMD% -m ensurepip --upgrade
    goto :failed
)
echo [OK] pip found.

%PYTHON_CMD% -c "import tkinter, _tkinter; print('Tk', tkinter.TkVersion, '- Tcl', tkinter.TclVersion)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Tkinter / Tcl-Tk is missing.
    echo         Install the standard Python build from python.org and enable Tcl/Tk and IDLE.
    goto :failed
)
echo [OK] Tkinter / Tcl-Tk found.

%PYTHON_CMD% -c "from PIL import Image; print(Image.__version__)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Pillow is not installed.
    echo         Install runtime requirements with:
    echo         %PYTHON_CMD% -m pip install -r requirements.txt
    goto :failed
)
echo [OK] Pillow found.

%PYTHON_CMD% -c "import cryptography; print(cryptography.__version__)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] cryptography is not installed.
    echo         Install runtime requirements with:
    echo         %PYTHON_CMD% -m pip install -r requirements.txt
    goto :failed
)
echo [OK] cryptography found.

%PYTHON_CMD% -c "import mymcplusplus" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] mymcplusplus is not installed.
    echo         PS2 memory-card, MAX and legacy-save operations need it.
    echo         Install runtime requirements with:
    echo         %PYTHON_CMD% -m pip install -r requirements.txt
    goto :failed
)
echo [OK] mymcplusplus found.

%PYTHON_CMD% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller is not installed.
    echo         Install build requirements with:
    echo         %PYTHON_CMD% -m pip install -r requirements-build.txt
    goto :failed
)
echo [OK] PyInstaller:
%PYTHON_CMD% -m PyInstaller --version
echo.

rem ------------------------------------------------------------
rem Read app version
rem ------------------------------------------------------------
set "VERSION="
for /f "tokens=2 delims==" %%V in ('findstr /b /c:"APP_VERSION =" "%SOURCE_FILE%"') do (
    if not defined VERSION set "VERSION=%%V"
)
set "VERSION=%VERSION: =%"
set "VERSION=%VERSION:"=%"
if not defined VERSION (
    echo [ERROR] APP_VERSION could not be read from ps2_save_tool.py.
    goto :failed
)

set "FINAL_EXE=PS2 Save Tool v%VERSION%.exe"
echo Application version: %VERSION%
echo Output filename: %FINAL_EXE%
echo.

rem ------------------------------------------------------------
rem Run source tests before touching output
rem ------------------------------------------------------------
echo ============================================================
echo  Running tests
echo ============================================================
echo.
%PYTHON_CMD% -m unittest discover -s tests -v
if errorlevel 1 (
    echo.
    echo [ERROR] Unit tests failed. Build stopped.
    goto :failed
)

rem ------------------------------------------------------------
rem Build completely in TEMP so _build contains ONLY the final EXE
rem ------------------------------------------------------------
set "TEMP_ROOT=%TEMP%\PS2SaveTool_Build_%RANDOM%_%RANDOM%"
set "WORK_DIR=%TEMP_ROOT%\work"
set "SPEC_DIR=%TEMP_ROOT%\spec"
set "TEMP_DIST=%TEMP_ROOT%\dist"

mkdir "%WORK_DIR%" >nul 2>nul
mkdir "%SPEC_DIR%" >nul 2>nul
mkdir "%TEMP_DIST%" >nul 2>nul
if not exist "%TEMP_DIST%" (
    echo [ERROR] Temporary build folders could not be created.
    goto :failed
)

if exist "%DIST_DIR%" (
    echo Removing previous _build folder...
    call :remove_directory "%DIST_DIR%"
)
if exist "%DIST_DIR%" (
    echo [ERROR] The previous _build folder could not be removed.
    echo         Close the old EXE and Explorer windows using that folder.
    call :remove_directory "%TEMP_ROOT%"
    goto :failed
)
mkdir "%DIST_DIR%" >nul 2>nul


echo.
echo ============================================================
echo  Building one-file Windows EXE
echo ============================================================
echo.

if exist "%PNG_FILE%" (
    %PYTHON_CMD% -m PyInstaller ^
        --noconfirm ^
        --clean ^
        --onefile ^
        --windowed ^
        --name "%INTERNAL_NAME%" ^
        --icon "%ICO_FILE%" ^
        --add-data "%ICO_FILE%;." ^
        --add-data "%PNG_FILE%;." ^
        --collect-all mymcplusplus ^
        --distpath "%TEMP_DIST%" ^
        --workpath "%WORK_DIR%" ^
        --specpath "%SPEC_DIR%" ^
        "%SOURCE_FILE%"
) else (
    %PYTHON_CMD% -m PyInstaller ^
        --noconfirm ^
        --clean ^
        --onefile ^
        --windowed ^
        --name "%INTERNAL_NAME%" ^
        --icon "%ICO_FILE%" ^
        --add-data "%ICO_FILE%;." ^
        --collect-all mymcplusplus ^
        --distpath "%TEMP_DIST%" ^
        --workpath "%WORK_DIR%" ^
        --specpath "%SPEC_DIR%" ^
        "%SOURCE_FILE%"
)

set "BUILD_RESULT=%ERRORLEVEL%"
if not "%BUILD_RESULT%"=="0" (
    echo.
    echo [ERROR] PyInstaller build failed with error code %BUILD_RESULT%.
    call :remove_directory "%TEMP_ROOT%"
    goto :failed
)

if not exist "%TEMP_DIST%\%INTERNAL_NAME%.exe" (
    echo [ERROR] PyInstaller finished but the generated EXE was not found.
    call :remove_directory "%TEMP_ROOT%"
    goto :failed
)

move /y "%TEMP_DIST%\%INTERNAL_NAME%.exe" "%DIST_DIR%\%FINAL_EXE%" >nul
if errorlevel 1 (
    echo [ERROR] The final EXE could not be moved into _build.
    call :remove_directory "%TEMP_ROOT%"
    goto :failed
)

call :remove_directory "%TEMP_ROOT%"

rem Sanity check: _build should contain only one EXE.
for /f %%C in ('dir /b /a-d "%DIST_DIR%" ^| find /c /v ""') do set "OUTPUT_COUNT=%%C"
if not "%OUTPUT_COUNT%"=="1" (
    echo [WARNING] _build contains %OUTPUT_COUNT% files instead of exactly one.
    echo           Only the final EXE is intended to remain there.
)

echo.
echo ============================================================
echo  BUILD SUCCESSFUL
echo ============================================================
echo.
echo Created:
echo "%DIST_DIR%\%FINAL_EXE%"
echo.
echo app.ico and app.png are bundled into the EXE. They are NOT required beside it.
echo.
pause
exit /b 0

:remove_directory
set "CLEAN_TARGET=%~1"
if not exist "%CLEAN_TARGET%" exit /b 0
for /L %%R in (1,1,5) do (
    rmdir /s /q "%CLEAN_TARGET%" >nul 2>nul
    if not exist "%CLEAN_TARGET%" exit /b 0
    timeout /t 1 /nobreak >nul
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -LiteralPath '%CLEAN_TARGET%' -Recurse -Force -ErrorAction SilentlyContinue" >nul 2>nul
exit /b 0

:failed
echo.
echo ============================================================
echo  BUILD FAILED
echo ============================================================
echo.
pause
exit /b 1
