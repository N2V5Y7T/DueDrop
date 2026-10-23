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
python -c "import PIL, pypdf, docx, reportlab, numpy" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Installing required lightweight Python packages...
    python -m pip install --user -r requirements.txt
)
echo Launching Dewdrop overlay (drag files onto a segment, click a tool, or press Esc / centre hole to close)...
start "" pythonw dewdrop_runtime.py
