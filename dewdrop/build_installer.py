#!/usr/bin/env python3
"""
Automated Build Pipeline for Dewdrop & Dewdrop-Setup.exe.
1. Compiles standalone dewdrop.exe into dist/dewdrop using PyInstaller.
2. Packages dist/dewdrop into installer/payload.zip.
3. Compiles the interactive GUI installer into Dewdrop-Setup.exe.
4. Places Dewdrop-Setup.exe in the root project folder ready for one-click distribution.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent


def run_command(cmd, desc):
    print(f"\n[BUILD STEP] {desc}...")
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), text=True)
    if res.returncode != 0:
        print(f"[ERROR] Step failed with return code {res.returncode}: {desc}")
        sys.exit(res.returncode)
    print(f"[OK] {desc} completed successfully.")


def main():
    print("=" * 65)
    print("      DEWDROP STANDALONE EXECUTABLE & INSTALLER BUILDER      ")
    print("=" * 65)

    # 1. Compile dewdrop.exe
    py_exe = sys.executable
    cmd_dewdrop = [
        py_exe, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", "dewdrop",
        "--icon", "assets/icon.ico",
        "--add-data", "assets;assets",
        "--add-data", "dewdrop_core.py;.",
        "--add-data", "liquid_optics.py;.",
        "--add-data", "tests/self_test.py;tests",
        "--hidden-import", "dewdrop_core",
        "--hidden-import", "liquid_optics",
        "--collect-all", "PIL",
        "--collect-all", "numpy",
        "--collect-all", "cv2",
        "--collect-all", "pypdf",
        "--collect-all", "reportlab",
        "--collect-all", "docx",
        "dewdrop_runtime.py",
    ]
    run_command(cmd_dewdrop, "Compiling standalone dewdrop.exe with PyInstaller")

    # 2. Package into payload.zip
    print("\n[BUILD STEP] Packaging dist/dewdrop into installer/payload.zip...")
    payload_dir = ROOT_DIR / "dist" / "dewdrop"
    zip_target = ROOT_DIR / "installer" / "payload"
    zip_file = ROOT_DIR / "installer" / "payload.zip"
    if zip_file.exists():
        zip_file.unlink()
    shutil.make_archive(str(zip_target), "zip", str(payload_dir))
    zip_mb = zip_file.stat().st_size / (1024 * 1024)
    print(f"[OK] Created payload.zip ({zip_mb:.1f} MB).")

    # 3. Compile Dewdrop-Setup.exe
    cmd_setup = [
        py_exe, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "Dewdrop-Setup",
        "--icon", str((ROOT_DIR / "assets" / "icon.ico").resolve()),
        "--add-data", f"{zip_file.resolve()};.",
        "--add-data", f"{(ROOT_DIR / 'assets').resolve()};assets",
        "--distpath", str((ROOT_DIR / "dist_setup").resolve()),
        str((ROOT_DIR / "installer" / "installer_gui.py").resolve()),
    ]
    run_command(cmd_setup, "Compiling standalone Dewdrop-Setup.exe installer")

    # 4. Copy to target release locations
    final_setup = ROOT_DIR / "dist_setup" / "Dewdrop-Setup.exe"
    dest_dewdrop = ROOT_DIR / "Dewdrop-Setup.exe"
    dest_root = REPO_ROOT / "Dewdrop-Setup.exe"

    shutil.copy2(final_setup, dest_dewdrop)
    shutil.copy2(final_setup, dest_root)

    print("\n" + "=" * 65)
    print("                   BUILD PIPELINE COMPLETE!                  ")
    print("=" * 65)
    print(f"Standalone Installer Executable Ready:")
    print(f" -> {dest_root.resolve()} ({dest_root.stat().st_size / (1024*1024):.1f} MB)")
    print(f" -> {dest_dewdrop.resolve()}")
    print("\nYou can now double-click 'Dewdrop-Setup.exe' to install Dewdrop!")


if __name__ == "__main__":
    main()
