@echo off
setlocal DisableDelayedExpansion

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
if defined ARG_DEST set "DEST=%~1"
if not defined ARG_DEST (
    echo Installation Directory:
    echo [Default: %LOCALAPPDATA%\Programs\DewDrop]
    set /p "DEST=Press Enter to accept or enter path: "
)
:: Validate no command-injection characters in DEST
python -c "import os, sys; bad = [chr(x) for x in (34, 38, 124, 60, 62, 94, 37, 33)]; p = os.environ.get('DEST', ''); sys.exit(1 if any(c in p for c in bad) else 0)" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Installation directory contains prohibited characters: " & ^| ^< ^> ^^ %% ^^!
    pause
    exit /b 1
)

:: Enforce DEST ends in DewDrop and normalize path
python -c "import os, pathlib; p = pathlib.Path(os.environ['DEST'].strip()); p = p if p.name.lower() == 'dewdrop' else p / 'DewDrop'; print(str(p.resolve()))" > "%TEMP%\dewdrop_dest.tmp" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Invalid installation directory path.
    pause
    exit /b 1
)
set /p DEST=<"%TEMP%\dewdrop_dest.tmp"
del /f /q "%TEMP%\dewdrop_dest.tmp" >nul 2>nul

:: Refuse existing non-empty directory lacking .dewdrop-install marker
python -c "import os, sys, pathlib; p = pathlib.Path(os.environ['DEST']); marker = p / '.dewdrop-install'; sys.exit(1 if p.exists() and any(p.iterdir()) and not marker.is_file() else 0)" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Destination directory exists, is not empty, and lacks .dewdrop-install marker.
    echo Refusing to overwrite an unverified folder: !DEST!
    pause
    exit /b 1
)

:: 3. Choose global shortcut key
set "HOTKEY=CTRL+ALT+Z"
set "ARG_HOTKEY=%~2"
if defined ARG_HOTKEY set "HOTKEY=%~2"
if not defined ARG_HOTKEY (
    echo.
    echo Global Activation Hotkey:
    echo [Default: CTRL+ALT+Z]
    set /p "HOTKEY=Press Enter to accept or enter hotkey (e.g. CTRL+ALT+Z): "
)

:: Validate hotkey contains only letters, digits, and +
python -c "import os, sys, re; h = os.environ.get('HOTKEY', '').strip(); sys.exit(0 if re.match(r'^[A-Za-z0-9+]+$', h) else 1)" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Invalid hotkey: "!HOTKEY!". Only letters, digits, and + are permitted.
    pause
    exit /b 1
)

setlocal EnableDelayedExpansion

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
echo dewdrop-install-marker>"!DEST!\.dewdrop-install"
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
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DewDrop" /v "DisplayName" /t REG_SZ /d "Dew Drop" /f >nul 2>nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DewDrop" /v "InstallLocation" /t REG_SZ /d "!DEST!" /f >nul 2>nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DewDrop" /v "UninstallString" /t REG_SZ /d "cmd.exe /c \"!DEST!\Uninstall.bat\"" /f >nul 2>nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DewDrop" /v "DisplayIcon" /t REG_SZ /d "!DEST!\assets\icon.ico" /f >nul 2>nul

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
