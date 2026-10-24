@echo off
setlocal
echo ============================================================
echo           Uninstalling Dewdrop Liquid Glass
echo ============================================================
echo.
echo Stopping any running Dewdrop processes...
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq Dewdrop*" /F >nul 2>nul
taskkill /IM dewdrop.exe /F >nul 2>nul
taskkill /IM dewdrop-worker.exe /F >nul 2>nul

echo Removing shortcuts...
del /F /Q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\Desktop\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\OneDrive\Desktop\Dewdrop.lnk" >nul 2>nul

:: Detect current installation directory dynamically
set "DEST=%~dp0"
if "%DEST:~-1%"=="\" set "DEST=%DEST:~0,-1%"
if not exist "%DEST%\dewdrop_runtime.py" (
    if exist "D:\program files\dewdrop_runtime.py" (
        set "DEST=D:\program files"
    ) else if exist "d:\program files\dewdrop_runtime.py" (
        set "DEST=d:\program files"
    ) else (
        set "DEST=%LOCALAPPDATA%\Programs\Dewdrop"
    )
)
if exist "%DEST%" (
    echo Removing Dewdrop files from "%DEST%"...
    rmdir /S /Q "%DEST%" >nul 2>nul
)

python -c "import ctypes; ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)" >nul 2>nul

echo.
echo ============================================================
echo [SUCCESS] Dewdrop has been completely uninstalled.
echo ============================================================
echo.
pause
