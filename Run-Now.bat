@echo off
setlocal
cd /d "%~dp0"
echo === Starting Dewdrop Liquid-Glass Ring ===
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python 3 is not installed or not on PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)
set "TARGET=dewdrop_runtime.py"
if exist "dewdrop\dewdrop_runtime.py" set "TARGET=dewdrop\dewdrop_runtime.py"
echo Launching Dewdrop overlay...
start "" pythonw "%TARGET%"
