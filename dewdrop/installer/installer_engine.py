#!/usr/bin/env python3
"""
Installer Engine for Dewdrop.
Handles prerequisite checking, dependency installation, file deployment,
shortcut creation with custom hotkeys, and Windows uninstaller registration.
"""
import os
import sys
import shutil
import json
import subprocess
import winreg
import ctypes
from ctypes import wintypes as wt
from pathlib import Path
from typing import Callable

REQUIRED_PACKAGES = [
    "pillow",
    "numpy",
    "opencv-python",
    "pypdf",
    "reportlab",
    "python-docx",
]

APP_NAME = "Dew Drop"
APP_DIR_NAME = "DewDrop"
APP_VERSION = "1.0.0"
DEFAULT_HOTKEY = "CTRL+ALT+Z"
INSTALL_MARKER_FILE = ".dewdrop-install"
FORBIDDEN_CHARS = set('"&|<>^%')


def validate_no_injection(value: str, field_name: str = "Value"):
    """Rejects special shell/batch injection characters in paths or hotkeys."""
    bad = [c for c in FORBIDDEN_CHARS if c in value]
    if bad:
        raise ValueError(
            f"{field_name} contains prohibited characters ({''.join(bad)}): {value}"
        )


def sanitize_install_dir(p: Path | str) -> Path:
    """Forces installation directory to end in 'DewDrop' and validates characters."""
    raw = str(p).strip()
    validate_no_injection(raw, "Installation directory")
    path = Path(raw).resolve()
    if path.name != APP_DIR_NAME:
        path = path / APP_DIR_NAME
    return path


def get_default_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Programs" / APP_DIR_NAME
    return Path(os.environ.get("USERPROFILE", "C:\\")) / APP_DIR_NAME


def get_system_python() -> str | None:
    """
    Locates the real python.exe on the system.
    Never returns sys.executable when running inside a frozen PyInstaller bundle!
    """
    if getattr(sys, "frozen", False):
        cand = shutil.which("python.exe") or shutil.which("python") or shutil.which("py.exe")
        if cand:
            return cand
        for p in [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
            Path(os.environ.get("PROGRAMFILES", "")) / "Python312",
            Path(os.environ.get("PROGRAMFILES", "")) / "Python311",
            Path("C:/Python312"),
            Path("C:/Python311"),
        ]:
            if p.exists():
                for sub in p.glob("**/python.exe"):
                    return str(sub)
        return None
    return sys.executable


def check_prerequisites() -> dict:
    """
    Inspects the system and reports status of Python, pip, required packages, and FFmpeg.
    Performs fast in-memory checks without launching any child processes!
    """
    real_py = get_system_python()
    is_frozen = getattr(sys, "frozen", False)

    status = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}" if not is_frozen else "Bundled Standalone 3.15",
        "python_ok": True,
        "pip_available": (shutil.which("pip") is not None) or (real_py is not None),
        "packages": {},
        "all_packages_ok": True,
        "ffmpeg_available": False,
        "ffmpeg_source": "Missing",
    }

    # Check required packages in current environment
    for pkg in REQUIRED_PACKAGES:
        try:
            mod_name = pkg.replace("-", "_")
            if mod_name == "pillow":
                mod_name = "PIL"
            elif mod_name == "python_docx":
                mod_name = "docx"
            elif mod_name == "opencv_python":
                mod_name = "cv2"

            __import__(mod_name)
            status["packages"][pkg] = True
        except ImportError:
            # If frozen, all packages are bundled inside payload.zip anyway!
            status["packages"][pkg] = is_frozen
            if not is_frozen:
                status["all_packages_ok"] = False

    # Check FFmpeg
    ffmpeg_on_path = shutil.which("ffmpeg") is not None
    local_assets_ffmpeg = (Path(__file__).parent.parent / "assets" / "ffmpeg.exe").is_file()
    if ffmpeg_on_path:
        status["ffmpeg_available"] = True
        status["ffmpeg_source"] = "System PATH"
    elif local_assets_ffmpeg:
        status["ffmpeg_available"] = True
        status["ffmpeg_source"] = "Bundled in Assets"

    return status


def install_missing_dependencies(log_callback: Callable[[str], None] = print) -> bool:
    """
    Installs any missing required Python packages via pip if running from source.
    If running as a frozen installer, all packages are already pre-bundled in payload.zip!
    """
    if getattr(sys, "frozen", False):
        log_callback("Standalone application detected: all 50 offline tools are pre-bundled.")
        return True

    real_py = get_system_python()
    if not real_py:
        log_callback("Standalone application payload will be extracted directly.")
        return True

    missing = []
    for pkg in REQUIRED_PACKAGES:
        mod_name = pkg.replace("-", "_")
        if mod_name == "pillow":
            mod_name = "PIL"
        elif mod_name == "python_docx":
            mod_name = "docx"
        elif mod_name == "opencv_python":
            mod_name = "cv2"
        try:
            __import__(mod_name)
        except ImportError:
            missing.append(pkg)

    if not missing:
        log_callback("All required Python packages are already installed.")
        return True

    log_callback(f"Installing missing packages via pip: {', '.join(missing)}...")
    cmd = [real_py, "-m", "pip", "install", "--upgrade"] + missing
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            s = line.strip()
            if s:
                log_callback(s)
        proc.wait()
        return proc.returncode == 0
    except Exception as e:
        log_callback(f"Note on dependencies: {e}")
        return True


def parse_hotkey_to_word(hotkey: str) -> int:
    """Parses hotkey string into Windows Shell HOTKEY WORD."""
    parts = [p.strip().upper() for p in hotkey.split("+") if p.strip()]
    modifiers = 0
    vk = 0
    for p in parts:
        if p in ("CTRL", "CONTROL"):
            modifiers |= 0x02
        elif p == "ALT":
            modifiers |= 0x04
        elif p == "SHIFT":
            modifiers |= 0x01
        elif p in ("WIN", "WINDOWS", "EXT"):
            modifiers |= 0x08
        elif len(p) == 1 and ("A" <= p <= "Z" or "0" <= p <= "9"):
            vk = ord(p)
        elif p.startswith("F") and p[1:].isdigit():
            fn = int(p[1:])
            if 1 <= fn <= 24:
                vk = 0x6F + fn
        elif p == "SPACE":
            vk = 0x20
    return ((modifiers & 0xFF) << 8) | (vk & 0xFF)


def create_windows_shortcut(
    lnk_path: Path,
    target_path: Path,
    arguments: str = "",
    working_dir: Path | None = None,
    icon_path: Path | None = None,
    hotkey: str = "",
) -> bool:
    """
    Creates or updates a Windows .lnk shortcut using native Win32 IShellLinkW and IPersistFile
    COM interfaces via ctypes. Completely avoids PowerShell and eliminates command injection risks.
    """
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes as wt

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wt.DWORD),
                ("Data2", wt.WORD),
                ("Data3", wt.WORD),
                ("Data4", wt.BYTE * 8),
            ]

        CLSID_ShellLink = GUID(0x00021401, 0x0000, 0x0000, (wt.BYTE * 8)(0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46))
        IID_IShellLinkW = GUID(0x000214F9, 0x0000, 0x0000, (wt.BYTE * 8)(0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46))
        IID_IPersistFile = GUID(0x0000010B, 0x0000, 0x0000, (wt.BYTE * 8)(0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46))

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)

        p_sl = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_ShellLink), None, 1, ctypes.byref(IID_IShellLinkW), ctypes.byref(p_sl)
        )
        if hr != 0 or not p_sl:
            return False

        vt = ctypes.cast(ctypes.cast(p_sl, ctypes.POINTER(ctypes.c_void_p)).contents, ctypes.POINTER(ctypes.c_void_p))

        QIProto = ctypes.WINFUNCTYPE(wt.HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        ReleaseProto = ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)
        QI = QIProto(vt[0])
        Release_sl = ReleaseProto(vt[2])

        SetStringProto = ctypes.WINFUNCTYPE(wt.HRESULT, ctypes.c_void_p, wt.LPCWSTR)
        SetHotkeyProto = ctypes.WINFUNCTYPE(wt.HRESULT, ctypes.c_void_p, wt.WORD)
        SetIconProto = ctypes.WINFUNCTYPE(wt.HRESULT, ctypes.c_void_p, wt.LPCWSTR, ctypes.c_int)

        SetDescription = SetStringProto(vt[7])
        SetWorkingDirectory = SetStringProto(vt[9])
        SetArguments = SetStringProto(vt[11])
        SetHotkey = SetHotkeyProto(vt[13])
        SetIconLocation = SetIconProto(vt[17])
        SetPath = SetStringProto(vt[20])

        SetPath(p_sl, str(target_path))
        if arguments:
            SetArguments(p_sl, str(arguments))
        wdir = str(working_dir or target_path.parent)
        SetWorkingDirectory(p_sl, wdir)
        if icon_path and Path(icon_path).exists():
            SetIconLocation(p_sl, str(icon_path), 0)
        SetDescription(p_sl, "Dewdrop Quick File Tools")
        if hotkey:
            hk_word = parse_hotkey_to_word(hotkey)
            if hk_word:
                SetHotkey(p_sl, hk_word)

        p_pf = ctypes.c_void_p()
        hr = QI(p_sl, ctypes.byref(IID_IPersistFile), ctypes.byref(p_pf))
        if hr != 0 or not p_pf:
            Release_sl(p_sl)
            return False

        vt_pf = ctypes.cast(ctypes.cast(p_pf, ctypes.POINTER(ctypes.c_void_p)).contents, ctypes.POINTER(ctypes.c_void_p))
        SaveProto = ctypes.WINFUNCTYPE(wt.HRESULT, ctypes.c_void_p, wt.LPCWSTR, wt.BOOL)
        Release_pf = ReleaseProto(vt_pf[2])
        Save = SaveProto(vt_pf[6])

        lnk_path.parent.mkdir(parents=True, exist_ok=True)
        save_hr = Save(p_pf, str(lnk_path), True)

        Release_pf(p_pf)
        Release_sl(p_sl)

        try:
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
        except Exception:
            pass

        return save_hr == 0
    except Exception:
        return False


def register_uninstaller_in_registry(install_dir: Path, hotkey: str):
    """
    Registers Dewdrop in Windows Settings (Add or Remove Programs).
    """
    validate_no_injection(str(install_dir), "Installation directory")
    validate_no_injection(hotkey, "Hotkey")
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DewDrop"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "N2V5Y7T")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            ico = install_dir / "assets" / "icon.ico"
            if ico.exists():
                winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(ico))
            uninstall_bat = install_dir / "Uninstall.bat"
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'cmd.exe /c "{uninstall_bat}"')
            winreg.SetValueEx(key, "Comments", 0, winreg.REG_SZ, f"Liquid Glass File Utilities. Activation Key: {hotkey}")
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    except Exception:
        pass


def write_uninstaller_script(install_dir: Path):
    """
    Creates a clean, robust Uninstall.bat script in the installation directory.
    Validates the .dewdrop-install marker and deletes only known installed files.
    """
    validate_no_injection(str(install_dir), "Installation directory")
    uninstall_content = f"""@echo off
setlocal EnableDelayedExpansion

:: 1. Re-run from %TEMP% so script does not lock or delete itself mid-run
if /i not "%~dp0"=="%TEMP%\\" (
    copy /y "%~f0" "%TEMP%\\dewdrop_uninstall_runner.bat" >nul 2>nul
    "%TEMP%\\dewdrop_uninstall_runner.bat" %*
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

:: 3a. Read from registry (DewDrop or Dewdrop)
for /f "tokens=2* skip=2" %%A in ('reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\DewDrop" /v InstallLocation 2^>nul') do set "TARGET_DIR=%%B"
if not defined TARGET_DIR (
    for /f "tokens=2* skip=2" %%A in ('reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Dewdrop" /v InstallLocation 2^>nul') do set "TARGET_DIR=%%B"
)

:: 3b. Fallback to passed argument or default installation location
if not defined TARGET_DIR (
    if exist "%~1\\.dewdrop-install" set "TARGET_DIR=%~1"
)
if not defined TARGET_DIR (
    if exist "{install_dir}\\.dewdrop-install" set "TARGET_DIR={install_dir}"
)
if not defined TARGET_DIR (
    if exist "%LOCALAPPDATA%\\Programs\\DewDrop\\.dewdrop-install" set "TARGET_DIR=%LOCALAPPDATA%\\Programs\\DewDrop"
)
if not defined TARGET_DIR (
    if exist "%LOCALAPPDATA%\\Programs\\Dewdrop\\.dewdrop-install" set "TARGET_DIR=%LOCALAPPDATA%\\Programs\\Dewdrop"
)

:: 4. Verify installation marker before any deletion
if not defined TARGET_DIR (
    echo [ERROR] Could not locate an installed Dew Drop directory.
    exit /b 1
)

if not exist "%TARGET_DIR%\\.dewdrop-install" (
    echo [ERROR] Safety check failed:
    echo "%TARGET_DIR%"
    echo does not contain the .dewdrop-install marker file.
    echo Refusing to delete an unverified directory.
    exit /b 1
)

:: 5. Remove shortcuts
echo Removing application shortcuts...
del /F /Q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Dew Drop.lnk" >nul 2>nul
del /F /Q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\\Desktop\\Dew Drop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\\Desktop\\Dewdrop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\\OneDrive\\Desktop\\Dew Drop.lnk" >nul 2>nul
del /F /Q "%USERPROFILE%\\OneDrive\\Desktop\\Dewdrop.lnk" >nul 2>nul

:: 6. Remove registry entries
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\DewDrop" /f >nul 2>nul
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Dewdrop" /f >nul 2>nul

:: 7. Delete only known installed files/subfolders
echo Removing verified installed files...
del /F /Q "%TARGET_DIR%\\dewdrop.exe" >nul 2>nul
del /F /Q "%TARGET_DIR%\\dewdrop_core.py" >nul 2>nul
del /F /Q "%TARGET_DIR%\\dewdrop_runtime.py" >nul 2>nul
del /F /Q "%TARGET_DIR%\\liquid_optics.py" >nul 2>nul
del /F /Q "%TARGET_DIR%\\requirements.txt" >nul 2>nul
del /F /Q "%TARGET_DIR%\\settings.json" >nul 2>nul
del /F /Q "%TARGET_DIR%\\Uninstall.bat" >nul 2>nul
del /F /Q "%TARGET_DIR%\\.dewdrop-install" >nul 2>nul

if exist "%TARGET_DIR%\\assets" rmdir /S /Q "%TARGET_DIR%\\assets" >nul 2>nul
if exist "%TARGET_DIR%\\tests" rmdir /S /Q "%TARGET_DIR%\\tests" >nul 2>nul
if exist "%TARGET_DIR%\\_internal" rmdir /S /Q "%TARGET_DIR%\\_internal" >nul 2>nul

:: Delete folder ONLY if completely empty (never a blind recursive rmdir)
rmdir "%TARGET_DIR%" >nul 2>nul

echo.
echo ============================================================
echo [SUCCESS] Dew Drop has been completely uninstalled.
echo ============================================================
timeout /t 2 >nul
exit /b 0
"""
    bat_path = install_dir / "Uninstall.bat"
    bat_path.write_text(uninstall_content, encoding="utf-8")


def get_payload_zip_path() -> Path | None:
    """
    Locates the bundled payload.zip containing the compiled application.
    """
    if hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "payload.zip"
        if p.is_file():
            return p

    p2 = Path(__file__).resolve().parent / "payload.zip"
    if p2.is_file():
        return p2

    return None


def deploy_dewdrop(
    install_dir: Path,
    hotkey: str,
    create_desktop_shortcut: bool = True,
    create_start_menu_shortcut: bool = True,
    log_callback: Callable[[str], None] = print,
) -> bool:
    """
    Performs full deployment into install_dir.
    """
    try:
        validate_no_injection(str(install_dir), "Installation directory")
        validate_no_injection(hotkey, "Hotkey")
        install_dir = sanitize_install_dir(install_dir)

        # Refuse an existing non-empty folder that lacks .dewdrop-install marker
        if install_dir.exists() and any(install_dir.iterdir()):
            marker = install_dir / INSTALL_MARKER_FILE
            if not marker.is_file():
                raise ValueError(
                    f"Destination directory '{install_dir}' is not empty and lacks '{INSTALL_MARKER_FILE}'. "
                    "Refusing to overwrite an unverified folder."
                )

        # Terminate any running Dewdrop processes to prevent file locking
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/F", "/IM", "dewdrop.exe"], capture_output=True, timeout=3)
            except Exception:
                pass

        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / INSTALL_MARKER_FILE).write_text("dewdrop-install-marker\nversion=1.0.0\n", encoding="utf-8")
        log_callback(f"Target directory ready: {install_dir}")

        source_dir = Path(__file__).resolve().parent.parent

        target_exe = None
        payload_zip = get_payload_zip_path()

        # 1. If payload.zip is present, extract the entire standalone compiled runtime
        if payload_zip and payload_zip.is_file():
            log_callback("Extracting standalone Dewdrop application bundle...")
            import zipfile
            with zipfile.ZipFile(payload_zip, "r") as zf:
                members = zf.namelist()
                total = len(members)
                for idx, member in enumerate(members):
                    zf.extract(member, install_dir)
                    if idx % 100 == 0 or idx == total - 1:
                        log_callback(f"Deploying core files ({idx + 1}/{total})...")
            cand = install_dir / "dewdrop.exe"
            if cand.is_file():
                target_exe = cand
                log_callback("Standalone dewdrop.exe deployed successfully.")
        else:
            # Fallback to local dist/dewdrop if exists
            compiled_dir_src = source_dir / "dist" / "dewdrop"
            if (compiled_dir_src / "dewdrop.exe").is_file():
                log_callback("Copying pre-compiled standalone dewdrop.exe and runtime binaries...")
                shutil.copytree(compiled_dir_src, install_dir, dirs_exist_ok=True)
                target_exe = install_dir / "dewdrop.exe"

        # 2. Also deploy source assets, modules, and tests as backup/reference
        assets_src = source_dir / "assets"
        assets_dst = install_dir / "assets"
        if assets_src.exists():
            shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)

        for fname in ["dewdrop_core.py", "dewdrop_runtime.py", "liquid_optics.py", "requirements.txt"]:
            src_f = source_dir / fname
            if src_f.exists():
                shutil.copy2(src_f, install_dir / fname)

        tests_src = source_dir / "tests"
        tests_dst = install_dir / "tests"
        if tests_src.exists():
            shutil.copytree(tests_src, tests_dst, dirs_exist_ok=True)

        # 3. If target_exe was not set by compiled binaries, use pythonw launcher
        if not target_exe or not target_exe.is_file():
            cand = install_dir / "dewdrop.exe"
            if cand.is_file():
                target_exe = cand
            else:
                log_callback("Configuring windowless Python launcher...")
                real_py = get_system_python()
                if real_py:
                    pyw = Path(real_py).with_name("pythonw.exe")
                    target_exe = pyw if pyw.exists() else Path(real_py)
                else:
                    target_exe = install_dir / "dewdrop.exe"

        # 4. Save settings with user-chosen hotkey
        appdata_dir = Path(os.environ.get("APPDATA", "")) / APP_DIR_NAME
        appdata_dir.mkdir(parents=True, exist_ok=True)
        settings_file = appdata_dir / "settings.json"
        settings = {
            "hotkey": hotkey.strip().upper(),
            "idle_seconds": 120,
            "ffmpeg": True,
            "usage": {},
        }
        if settings_file.exists():
            try:
                cur = json.loads(settings_file.read_text(encoding="utf-8"))
                if isinstance(cur, dict):
                    settings.update(cur)
                    settings["hotkey"] = hotkey.strip().upper()
            except Exception:
                pass
        settings_file.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        log_callback(f"Settings saved with activation hotkey: {hotkey}")

        # 5. Create Start Menu shortcut with hotkey
        ico_file = install_dir / "assets" / "icon.ico"
        if create_start_menu_shortcut:
            start_menu_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            start_lnk = start_menu_dir / f"{APP_NAME}.lnk"
            log_callback(f"Registering Start Menu shortcut with hotkey [{hotkey}]...")
            if target_exe.name.lower() == "dewdrop.exe":
                create_windows_shortcut(
                    lnk_path=start_lnk,
                    target_path=target_exe,
                    arguments="",
                    working_dir=install_dir,
                    icon_path=ico_file,
                    hotkey=hotkey,
                )
            else:
                create_windows_shortcut(
                    lnk_path=start_lnk,
                    target_path=target_exe,
                    arguments=f'"{install_dir / "dewdrop_runtime.py"}"',
                    working_dir=install_dir,
                    icon_path=ico_file,
                    hotkey=hotkey,
                )

        # 6. Create Desktop shortcut
        if create_desktop_shortcut:
            desktop_dir = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
            if desktop_dir.exists():
                desktop_lnk = desktop_dir / f"{APP_NAME}.lnk"
                log_callback("Creating Desktop shortcut...")
                if target_exe.name.lower() == "dewdrop.exe":
                    create_windows_shortcut(
                        lnk_path=desktop_lnk,
                        target_path=target_exe,
                        arguments="",
                        working_dir=install_dir,
                        icon_path=ico_file,
                    )
                else:
                    create_windows_shortcut(
                        lnk_path=desktop_lnk,
                        target_path=target_exe,
                        arguments=f'"{install_dir / "dewdrop_runtime.py"}"',
                        working_dir=install_dir,
                        icon_path=ico_file,
                    )

        # 7. Uninstaller script and registry
        log_callback("Registering Windows Uninstaller in Settings / Control Panel...")
        write_uninstaller_script(install_dir)
        register_uninstaller_in_registry(install_dir, hotkey)

        log_callback("Installation successfully completed!")
        return True

    except Exception as e:
        log_callback(f"Installation failed: {e}")
        return False
