@echo off
setlocal
cd /d "%~dp0"
echo === Building Standalone Dewdrop-Setup.exe Installer ===
python build_installer.py
pause
