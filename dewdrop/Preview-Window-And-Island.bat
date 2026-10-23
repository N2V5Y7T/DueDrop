@echo off
setlocal
cd /d "%~dp0"
echo === Launching Dewdrop Options Window ^& Dynamic Island Preview ===
echo - Click "Start" (or press Enter / Spacebar) to see the watery spring morph into the Top-Centre Dynamic Island!
echo - Click anywhere on the Dynamic Island Pill at the top of your screen to expand it back into the Window!
echo - Click "-" (Minimize) or click outside the Window to collapse it back into the Dynamic Island!
echo - Press keys 1 (Ring), 2 (Options Window), 3 (Dynamic Island), or Spacebar (Morph toggle) anytime.
python -c "import PIL, pypdf, docx, reportlab, numpy" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    python -m pip install --user -r requirements.txt
)
start "" pythonw dewdrop_runtime.py --preview-window
