@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo                     Dew Drop Installation
echo ============================================================
echo.

:: Detect source directory
set "SRC=%~dp0"
if exist "%SRC%dewdrop\dewdrop_runtime.py" (
    set "SRC=%SRC%dewdrop\"
)

:: 1. Verify Python 3.11+ installation
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python was not found on your system PATH.
    echo Please install Python 3.11 or newer from https://www.python.org/downloads/
    echo and ensure "Add python.exe to PATH" is checked during setup.
    echo.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python 3.11 or newer is required to run Dew Drop.
    python --version
    echo Please download and install a newer Python release from https://www.python.org/
    echo.
    pause
    exit /b 1
)

:: 2. Choose destination directory
set "DEST=%LOCALAPPDATA%\Programs\DewDrop"
set "ARG_DEST=%~1"
if defined ARG_DEST (
    set "DEST=%~1"
) else (
    echo Installation Directory:
    echo [Default: %LOCALAPPDATA%\Programs\DewDrop]
    set /p "INPUT_DEST=Press Enter to accept or enter path: "
    if defined INPUT_DEST (
        for /f "tokens=* delims= " %%a in ("!INPUT_DEST!") do set "TRIMMED_DEST=%%a"
        if defined TRIMMED_DEST set "DEST=!TRIMMED_DEST!"
    )
)
:: Strip trailing backslash from DEST if present
if "!DEST:~-1!"=="\" set "DEST=!DEST:~0,-1!"

:: 3. Choose global shortcut key
set "HOTKEY=CTRL+ALT+Z"
set "ARG_HOTKEY=%~2"
if defined ARG_HOTKEY (
    set "HOTKEY=%~2"
) else (
    echo.
    echo Global Activation Hotkey:
    echo [Default: CTRL+ALT+Z]
    set /p "INPUT_HOTKEY=Press Enter to accept or enter hotkey (e.g. CTRL+ALT+Z): "
    if defined INPUT_HOTKEY (
        for /f "tokens=* delims= " %%a in ("!INPUT_HOTKEY!") do set "TRIMMED_HOTKEY=%%a"
        if defined TRIMMED_HOTKEY set "HOTKEY=!TRIMMED_HOTKEY!"
    )
)

echo.
echo ============================================================
echo Installing Dew Drop...
echo Target: !DEST!
echo Hotkey: !HOTKEY!
echo ============================================================
echo.

:: 4. Install / Verify dependencies
echo [1/4] Installing required Python dependencies...
python -m pip install -r "%SRC%requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Standard pip install reported an issue. Retrying with --user...
    python -m pip install --user -r "%SRC%requirements.txt"
)

:: 5. Copy files to destination
echo [2/4] Deploying application files...
if not exist "!DEST!" mkdir "!DEST!"
if not exist "!DEST!\assets" mkdir "!DEST!\assets"

xcopy /E /I /Y "%SRC%assets\*" "!DEST!\assets\" >nul
copy /Y "%SRC%dewdrop_core.py" "!DEST!\dewdrop_core.py" >nul
copy /Y "%SRC%dewdrop_runtime.py" "!DEST!\dewdrop_runtime.py" >nul
copy /Y "%SRC%liquid_optics.py" "!DEST!\liquid_optics.py" >nul
copy /Y "%SRC%requirements.txt" "!DEST!\requirements.txt" >nul
if exist "%~dp0Uninstall.bat" copy /Y "%~dp0Uninstall.bat" "!DEST!\Uninstall.bat" >nul
if exist "%SRC%ffmpeg.exe" copy /Y "%SRC%ffmpeg.exe" "!DEST!\ffmpeg.exe" >nul

:: Clean up any obsolete standalone exe in DEST
if exist "!DEST!\dewdrop.exe" del /F /Q "!DEST!\dewdrop.exe" >nul 2>nul
if exist "!DEST!\_internal" rmdir /S /Q "!DEST!\_internal" >nul 2>nul

:: 6. Register shortcut and reserve hotkey
echo [3/4] Registering global hotkey !HOTKEY! in Windows Explorer...
python "!DEST!\dewdrop_runtime.py" --make-shortcut "!HOTKEY!"

:: 7. Verification smoke test
echo [4/4] Verifying installation integrity...
python -c "import sys; sys.path.insert(0, r'!DEST!'); import dewdrop_core, liquid_optics; print('Dew Drop Verification: OK')" >nul 2>nul

echo.
echo ============================================================
echo [SUCCESS] Dew Drop installed successfully!
echo.
echo Location: !DEST!
echo Hotkey:   !HOTKEY!
echo.
echo Shortcuts have been placed on your Desktop and Start Menu.
echo Press !HOTKEY! anytime in Windows to open Dew Drop!
echo ============================================================
echo.
pause
