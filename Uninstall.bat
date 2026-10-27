@echo off
setlocal EnableDelayedExpansion

:: 1. Re-run from %TEMP% so script does not lock or delete itself mid-run
if /i not "%~dp0"=="%TEMP%\" (
    set "CALLER_DIR=%~dp0"
    if "!CALLER_DIR:~-1!"=="\" set "CALLER_DIR=!CALLER_DIR:~0,-1!"
    copy /y "%~f0" "%TEMP%\dewdrop_uninstall_runner.bat" >nul 2>nul
    "%TEMP%\dewdrop_uninstall_runner.bat" "!CALLER_DIR!" %*
    exit /b %ERRORLEVEL%
)

echo ============================================================
echo                  Uninstalling Dew Drop
echo ============================================================
echo.

:: 2. Terminate running processes
echo Stopping running Dew Drop processes...
taskkill /IM dewdrop.exe /F >nul 2>nul
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq Dew Drop*" /F >nul 2>nul
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq Dewdrop*" /F >nul 2>nul

:: 3. Resolve Installation Location
set "TARGET_DIR="

:: 3a. Check command line arguments first
if exist "%~1\.dewdrop-install" set "TARGET_DIR=%~1"
if not defined TARGET_DIR (
    if exist "%~2\.dewdrop-install" set "TARGET_DIR=%~2"
)

:: 3b. Read from registry (DewDrop or Dewdrop), only if valid marker exists
if not defined TARGET_DIR (
    for /f "tokens=2* skip=2" %%A in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DewDrop" /v InstallLocation 2^>nul') do (
        if exist "%%~B\.dewdrop-install" set "TARGET_DIR=%%~B"
    )
)
if not defined TARGET_DIR (
    for /f "tokens=2* skip=2" %%A in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Dewdrop" /v InstallLocation 2^>nul') do (
        if exist "%%~B\.dewdrop-install" set "TARGET_DIR=%%~B"
    )
)
if not defined TARGET_DIR (
    if exist "%LOCALAPPDATA%\Programs\DewDrop\.dewdrop-install" set "TARGET_DIR=%LOCALAPPDATA%\Programs\DewDrop"
)
if not defined TARGET_DIR (
    if exist "%LOCALAPPDATA%\Programs\Dewdrop\.dewdrop-install" set "TARGET_DIR=%LOCALAPPDATA%\Programs\Dewdrop"
)

:: 4. Verify installation marker before any deletion
if not defined TARGET_DIR (
    echo [ERROR] Could not locate an installed Dew Drop directory.
    echo Please make sure Dew Drop is installed or pass the path as an argument.
    pause
    exit /b 1
)

if not exist "%TARGET_DIR%\.dewdrop-install" (
    echo [ERROR] Safety check failed:
    echo "%TARGET_DIR%"
    echo does not contain the .dewdrop-install marker file.
    echo Refusing to delete an unverified directory.
    pause
    exit /b 1
)

:: 5. Remove shortcuts
echo Removing application shortcuts...
del /F /Q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Dew Drop.lnk" >nul 2>nul
del /F /Q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\Desktop\Dew Drop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\Desktop\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\OneDrive\Desktop\Dew Drop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\OneDrive\Desktop\Dewdrop.lnk" >nul 2>nul

:: 6. Remove registry entries
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DewDrop" /f >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Dewdrop" /f >nul 2>nul

:: 7. Delete only known installed files/subfolders
echo Removing verified installed files from "%TARGET_DIR%"...
del /F /Q "%TARGET_DIR%\dewdrop.exe" >nul 2>nul
del /F /Q "%TARGET_DIR%\dewdrop_core.py" >nul 2>nul
del /F /Q "%TARGET_DIR%\dewdrop_runtime.py" >nul 2>nul
del /F /Q "%TARGET_DIR%\liquid_optics.py" >nul 2>nul
del /F /Q "%TARGET_DIR%\requirements.txt" >nul 2>nul
del /F /Q "%TARGET_DIR%\settings.json" >nul 2>nul
del /F /Q "%TARGET_DIR%\Uninstall.bat" >nul 2>nul
del /F /Q "%TARGET_DIR%\.dewdrop-install" >nul 2>nul

if exist "%TARGET_DIR%\assets" rmdir /S /Q "%TARGET_DIR%\assets" >nul 2>nul
if exist "%TARGET_DIR%\tests" rmdir /S /Q "%TARGET_DIR%\tests" >nul 2>nul
if exist "%TARGET_DIR%\_internal" rmdir /S /Q "%TARGET_DIR%\_internal" >nul 2>nul

:: Delete folder ONLY if completely empty (never a blind recursive rmdir)
rmdir "%TARGET_DIR%" >nul 2>nul

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
