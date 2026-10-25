@echo off
setlocal

echo ============================================================
echo                  Uninstalling Dew Drop
echo ============================================================
echo.

echo Stopping any running Dew Drop processes...
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq Dew Drop*" /F >nul 2>nul
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq Dewdrop*" /F >nul 2>nul
taskkill /IM dewdrop.exe /F >nul 2>nul

echo Removing application shortcuts...
del /F /Q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Dew Drop.lnk" >nul 2>nul
del /F /Q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\Desktop\Dew Drop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\Desktop\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\OneDrive\Desktop\Dew Drop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\OneDrive\Desktop\Dewdrop.lnk" >nul 2>nul

echo Removing installed application files...
if exist "%LOCALAPPDATA%\Programs\DewDrop" (
    rmdir /S /Q "%LOCALAPPDATA%\Programs\DewDrop" >nul 2>nul
)
if exist "%LOCALAPPDATA%\Programs\Dewdrop" (
    rmdir /S /Q "%LOCALAPPDATA%\Programs\Dewdrop" >nul 2>nul
)

echo.
set /p "DEL_DATA=Delete Dew Drop user settings and cache from %%APPDATA%%? (y/N): "
if /i "%DEL_DATA%"=="y" (
    echo Removing configuration data...
    if exist "%APPDATA%\DewDrop" rmdir /S /Q "%APPDATA%\DewDrop" >nul 2>nul
    if exist "%APPDATA%\Dewdrop" rmdir /S /Q "%APPDATA%\Dewdrop" >nul 2>nul
)

python -c "import ctypes; ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)" >nul 2>nul

echo.
echo ============================================================
echo [SUCCESS] Dew Drop has been completely uninstalled.
echo ============================================================
echo.
pause
