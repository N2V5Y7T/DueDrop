# Dewdrop — On-Demand Liquid-Glass File Tools for Windows

Dewdrop is an on-demand, zero-background-process Windows utility that presents a per-pixel-transparent liquid-glass circular menu when you press its shortcut key and drag files in File Explorer, on the Desktop, or inside Open/Save dialogs.

## Key Capabilities
- **Zero Resident Footprint**: Nothing runs at Windows startup. Pressing the Start Menu shortcut key (default `Ctrl+Alt+Z`) arms Dewdrop for 2 minutes of inactivity and exits completely when idle.
- **Release-and-Stage Ring Workflow**: Drag files onto one of the 6 glass segments and release to stage them. Rotate the pointer 360° clockwise or counter-clockwise around the centre to flip pages, click the centre to go back, or pick any of the **50 built-in file tools**.
- **Water-Like Spring Morph**: Tools with options open the rounded liquid-glass Options window and collapse upward into the top-centre Dynamic Island progress pill using a continuous damped harmonic oscillator (`response = 0.45s`, `damping = 0.80`).
- **Safe Output Handling**: Converted files are written to a `Dew Drop` subfolder beside the source files using ` (1)`, ` (2)` collision suffixes. Original files are never modified or overwritten.

## Building on Windows 10 / 11 (x64)
1. Ensure the Rust stable toolchain (`x86_64-pc-windows-msvc`), Visual Studio C++ Build Tools, and Inno Setup 6 (`ISCC.exe`) are installed.
2. Optionally place a static `ffmpeg.exe` binary in `assets/ffmpeg.exe` if you want the installer to bundle offline audio/video conversion support.
3. Run the single-command build, test, and installer pipeline from PowerShell:
   ```powershell
   .\tools\build.ps1
   ```
4. The compiled installer will be emitted at `dist\Dewdrop-Setup.exe`.

## Running the Cross-Platform Verification & Visual Comparison Suite
To execute the 50-tool integration test suite, the 10-sample PDF-to-Word acceptance test, the 100-file batch test, the M6 malicious-sample security suite, and the reference screenshot comparison:
```bash
python3 tests/run_cleanroom_verification.py
python3 tools/screenshot-compare.py
```
Results and side-by-side visual comparisons are written to `docs/evidence/`.
