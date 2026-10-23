#!/usr/bin/env python3
"""
Test deployment verification for Dewdrop-Setup installer engine.
"""
import os
import sys
import tempfile
import json
from pathlib import Path

INSTALLER_DIR = Path(__file__).resolve().parent.parent / "installer"
sys.path.insert(0, str(INSTALLER_DIR))

import installer_engine


def test_installer_deployment():
    print("[TEST] Running installer engine deployment test...")
    with tempfile.TemporaryDirectory(prefix="dewdrop_test_install_") as tmpdir:
        dest = Path(tmpdir) / "Dewdrop"
        logs = []
        def log_cb(msg):
            logs.append(msg)
            print("   [INSTALL LOG]:", msg)

        # 1. Test prerequisites
        prereqs = installer_engine.check_prerequisites()
        assert prereqs["python_ok"], "Python check failed"
        print("[PASS] Prerequisites check succeeded.")

        # 2. Test deployment
        ok = installer_engine.deploy_dewdrop(
            install_dir=dest,
            hotkey="CTRL+ALT+D",
            create_desktop_shortcut=False,  # Don't clutter user desktop during unit test
            create_start_menu_shortcut=False,
            log_callback=log_cb,
        )
        assert ok, "Deployment failed"
        assert dest.exists(), f"Destination directory {dest} does not exist"
        assert (dest / "dewdrop.exe").exists() or (dest / "dewdrop_runtime.py").exists(), "Core executables missing"
        assert (dest / "Uninstall.bat").exists(), "Uninstaller script missing"

        # Check settings
        appdata = Path(os.environ.get("APPDATA", "")) / "Dewdrop" / "settings.json"
        if appdata.exists():
            cfg = json.loads(appdata.read_text(encoding="utf-8"))
            assert cfg.get("hotkey") == "CTRL+ALT+D", f"Expected hotkey CTRL+ALT+D, got {cfg.get('hotkey')}"
            print("[PASS] Configured hotkey verified in settings.json.")

        print(f"[PASS] Successfully deployed to {dest} with {len(list(dest.iterdir()))} root items.")

    print("\nALL INSTALLER TESTS PASSED!")


if __name__ == "__main__":
    test_installer_deployment()
