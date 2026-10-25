@echo off
setlocal
cd /d "%~dp0"
echo === Starting Dew Drop Liquid-Glass Overlay ===
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not on system PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/ and check "Add python.exe to PATH".
    pause
    exit /b 1
)
set "TARGET=dewdrop_runtime.py"
if exist "dewdrop\dewdrop_runtime.py" set "TARGET=dewdrop\dewdrop_runtime.py"
echo Launching Dew Drop...
start "" pythonw "%TARGET%" %*
