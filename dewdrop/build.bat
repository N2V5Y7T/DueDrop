@echo off
setlocal
cd /d "%~dp0"
echo === Dewdrop Clean-Room Build & Self-Test ===
where cargo >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Rust toolchain detected; running tools\build.ps1...
    powershell -NoProfile -ExecutionPolicy Bypass -File "tools\build.ps1"
    pause
    exit /b 0
)
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Running Python dependency install and 50-tool verification suite...
    python -m pip install --user -r requirements.txt
    python dewdrop_runtime.py --self-test
    echo.
    echo Verification passed! Double-click Install.bat to install or Run-Now.bat to launch immediately.
    pause
    exit /b 0
)
echo Neither Rust (cargo) nor Python was found on PATH.
pause
