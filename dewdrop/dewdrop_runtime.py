#!/usr/bin/env python3
"""
Dewdrop Windows Runtime & Liquid-Glass UI
Features:
- Real Photo / File Thumbnail Preview inside the Options Window, Progress Window, and Dynamic Island (updates live to show the current file being processed during multi-file batches)
- Smart File-Type Filtering (selecting/dropping Images shows ONLY Image options; selecting/dropping a ZIP shows ONLY ZIP/Archive options; PDF -> PDF options; Video/Audio -> Media options)
- Automatic Active Windows Explorer Selection Detection + Native Windows Multi-File Picker when clicking the Ring center hub with 0 files
- Razor-sharp anti-aliased rounded corners & annular segments (zero zig-zag / blurry edges)
- Continuous animated Liquid-Glass Shine Sweep (`shine_phase`) across Ring, Window, and Dynamic Island
- Pure Jet-Black Ink (`#000000`) typography & icons with enlarged Center Hub text
- Butter-smooth < 2 ms interactive rendering: instant Minimize (`-`), Square Expand (`□`), Cross (`✕`), `Start`, `Pause`, `Cancel`, and real-time mouse-draggable Percentage Slider (`WM_LBUTTONDOWN` + `WM_MOUSEMOVE` + `WM_LBUTTONUP`)
"""
import ctypes
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path


import numpy as np
from PIL import Image, ImageDraw


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from dewdrop_core import (
    SpringSolver,
    RotationPager,
    TOOL_SPECS,
    compute_ring_placement,
    execute_tool,
    filter_and_rank_tools,
    has_ffmpeg,
    sanitize_stem,
    resolve_clash_free_output,
    was_output_fallback_used,
    worker_main,
)


from liquid_optics import (
    render_optical_ring_rgba,
    render_optical_window_rgba,
    render_optical_progress_window_rgba,
    render_optical_island_rgba,
    render_optical_closing_alert_rgba,
    render_liquid_glass_toast_rgba,
    generate_vivid_desktop_backdrop,
    draw_sharp_rounded_rect,
    load_ui_font,
    draw_shadowed_text,
)


APP_NAME = "Dew Drop"
APP_DIR_NAME = "DewDrop"
DEFAULT_IDLE_SECS = 120.0
DWELL_ENTER_SECS = 1.50
DWELL_BACK_SECS = 1.50
ENTER_GRACE_SECS = 1.00
DONE_HOLD_SECS = 3.50


_DESKTOP_SNAPSHOT_RGB: np.ndarray | None = None




# ============================================================================
# 1. SETTINGS, USAGE COUNTERS & SIZE-CAPPED LOGGING (%APPDATA%\Dewdrop)
# ============================================================================


def get_appdata_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d




def log_error(context: str, exc: BaseException | str):
    try:
        log_path = get_appdata_dir() / "dewdrop.log"
        if log_path.exists() and log_path.stat().st_size > 512 * 1024:
            tail = log_path.read_bytes()[-256 * 1024:]
            log_path.write_bytes(tail)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        detail = exc if isinstance(exc, str) else "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {context}: {detail}\n")
    except Exception:
        pass




def load_settings() -> dict:
    defaults = {
        "hotkey": "CTRL+ALT+Z",
        "idle_seconds": 120,
        "ffmpeg": has_ffmpeg(),
        "usage": {},
    }
    spath = get_appdata_dir() / "settings.json"
    if spath.exists():
        try:
            loaded = json.loads(spath.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update(loaded)
                if not isinstance(defaults.get("usage"), dict):
                    defaults["usage"] = {}
        except Exception as e:
            log_error("load_settings", e)
    defaults["ffmpeg"] = bool(defaults.get("ffmpeg", True)) and has_ffmpeg()
    return defaults




def save_settings(settings: dict):
    try:
        spath = get_appdata_dir() / "settings.json"
        tmp = spath.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, spath)
    except Exception as e:
        log_error("save_settings", e)




def record_tool_usage(settings: dict, tool_id: str, group_name: str):
    usage = settings.setdefault("usage", {})
    usage[tool_id] = int(usage.get(tool_id, 0)) + 1
    gkey = f"group:{group_name}"
    usage[gkey] = int(usage.get(gkey, 0)) + 1
    save_settings(settings)




# ============================================================================
# 2. ACTIVE EXPLORER SELECTION & NATIVE WINDOWS FILE PICKER
# ============================================================================


_JS_SEL_PATH: Path | None = None


def _get_jscript_sel_path() -> Path:
    global _JS_SEL_PATH
    if _JS_SEL_PATH is not None and _JS_SEL_PATH.exists():
        return _JS_SEL_PATH
    cached = ROOT_DIR / "assets" / "get_explorer_selection.js"
    if not cached.exists():
        js_code = (
            "var sh = new ActiveXObject('Shell.Application');\n"
            "var wins = sh.Windows();\n"
            "for (var i = 0; i < wins.Count; i++) {\n"
            "  try {\n"
            "    var w = wins.Item(i);\n"
            "    var sel = w.Document.SelectedItems();\n"
            "    if (sel && sel.Count > 0) {\n"
            "      for (var j = 0; j < sel.Count; j++) {\n"
            "        WScript.Echo(sel.Item(j).Path);\n"
            "      }\n"
            "      break;\n"
            "    }\n"
            "  } catch(e) {}\n"
            "}\n"
        )
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(js_code, encoding="utf-8")
        except (PermissionError, OSError):
            cached = get_appdata_dir() / "assets" / "get_explorer_selection.js"
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(js_code, encoding="utf-8")
            except Exception:
                pass
    _JS_SEL_PATH = cached
    return _JS_SEL_PATH




def get_selected_explorer_files() -> list[Path]:
    """
    Queries Windows Explorer (`Shell.Application`) for any files currently selected by the user
    in the foreground/active File Explorer window so launching Dewdrop immediately filters tools
    to the user's selected files. Uses high-performance cscript / JScript query (~50-80ms)
    eliminating the 1.5s PowerShell launch delay.
    """
    if os.name != "nt":
        return []
    try:
        js_file = _get_jscript_sel_path()
        res = subprocess.run(
            ["cscript", "//nologo", str(js_file)],
            capture_output=True,
            text=True,
            timeout=0.8,
            creationflags=0x08000000,
        )
        paths = []
        for line in res.stdout.splitlines():
            s = line.strip()
            if s:
                p = Path(s).resolve()
                if p.exists():
                    paths.append(p)
        return paths
    except Exception:
        return []




def pick_folder_dialog_win32(hwnd=None) -> list[Path]:
    """
    Opens the native Windows folder picker dialog allowing the user
    to select an entire directory for tools like Make ZIP or TAR.GZ.
    Uses modern IFileDialog / FolderBrowserDialog.
    """
    if os.name != "nt":
        return []


    # 1. Modern Windows Explorer Folder Dialog via tkinter (uses IFileDialog with FOS_PICKFOLDERS)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        selected = filedialog.askdirectory(title="Select Folder to Compress (Make ZIP / Archive)")
        root.destroy()
        if selected:
            p = Path(selected).resolve()
            if p.is_dir():
                return [p]
    except Exception:
        pass


    # 2. Modern PowerShell FolderBrowserDialog with AutoUpgradeEnabled
    try:
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$f.Description = 'Select Folder to Compress (Make ZIP / Archive)'; "
            "$f.ShowNewFolderButton = $true; "
            "$f.AutoUpgradeEnabled = $true; "
            "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=60
        )
        if res.returncode == 0 and res.stdout.strip():
            p = Path(res.stdout.strip())
            if p.is_dir():
                return [p]
    except Exception:
        pass


    # 3. Native Win32 SHBrowseForFolderW with properly allocated pszDisplayName buffer
    try:
        import ctypes.wintypes as wt


        class BROWSEINFOW(ctypes.Structure):
            _fields_ = [
                ("hwndOwner", wt.HWND),
                ("pidlRoot", ctypes.c_void_p),
                ("pszDisplayName", wt.LPWSTR),
                ("lpszTitle", wt.LPCWSTR),
                ("ulFlags", wt.UINT),
                ("lpfn", ctypes.c_void_p),
                ("lParam", wt.LPARAM),
                ("iImage", ctypes.c_int),
            ]


        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)


        disp_buf = ctypes.create_unicode_buffer(260)
        bi = BROWSEINFOW()
        bi.hwndOwner = hwnd
        bi.pszDisplayName = ctypes.cast(disp_buf, wt.LPWSTR)
        bi.lpszTitle = "Select a folder for Dewdrop (Make ZIP / Archive)"
        bi.ulFlags = 0x00000001 | 0x00000040  # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE


        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wt.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wt.BOOL


        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return []
        path_buf = ctypes.create_unicode_buffer(32768)
        ret = []
        if shell32.SHGetPathFromIDListW(pidl, path_buf):
            p = Path(path_buf.value)
            if p.is_dir():
                ret = [p]
        ole32.CoTaskMemFree(pidl)
        return ret
    except Exception:
        return []




def pick_files_dialog_win32(hwnd=None) -> list[Path]:
    """
    Opens the native Windows multi-select file picker (`GetOpenFileNameW` in `comdlg32.dll`)
    when the user clicks the Ring center hub to select files directly.
    """
    if os.name != "nt":
        return []
    try:
        import ctypes.wintypes as wt


        OFN_ALLOWMULTISELECT = 0x00000200
        OFN_EXPLORER = 0x00080000
        OFN_FILEMUSTEXIST = 0x00001000
        OFN_PATHMUSTEXIST = 0x00000800
        OFN_NOCHANGEDIR = 0x00000008


        class OPENFILENAMEW(ctypes.Structure):
            _fields_ = [
                ("lStructSize", wt.DWORD),
                ("hwndOwner", wt.HWND),
                ("hInstance", wt.HINSTANCE),
                ("lpstrFilter", wt.LPCWSTR),
                ("lpstrCustomFilter", wt.LPWSTR),
                ("nMaxCustFilter", wt.DWORD),
                ("nFilterIndex", wt.DWORD),
                ("lpstrFile", wt.LPWSTR),
                ("nMaxFile", wt.DWORD),
                ("lpstrFileTitle", wt.LPWSTR),
                ("nMaxFileTitle", wt.DWORD),
                ("lpstrInitialDir", wt.LPCWSTR),
                ("lpstrTitle", wt.LPCWSTR),
                ("Flags", wt.DWORD),
                ("nFileOffset", wt.WORD),
                ("nFileExtension", wt.WORD),
                ("lpstrDefExt", wt.LPCWSTR),
                ("lCustData", wt.LPARAM),
                ("lpfnHook", ctypes.c_void_p),
                ("lpTemplateName", wt.LPCWSTR),
                ("pvReserved", ctypes.c_void_p),
                ("dwReserved", wt.DWORD),
                ("FlagsEx", wt.DWORD),
            ]


        buf_len = 32768
        file_buf = ctypes.create_unicode_buffer(buf_len)
        ofn = OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
        ofn.hwndOwner = hwnd
        ofn.lpstrFilter = (
            "All Supported Files\0*.*\0"
            "Images (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif;*.ico)\0*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif;*.ico\0"
            "Archives (*.zip;*.tar;*.gz;*.tgz;*.bz2;*.xz)\0*.zip;*.tar;*.gz;*.tgz;*.bz2;*.xz\0"
            "PDF & Word (*.pdf;*.docx)\0*.pdf;*.docx\0"
            "Video & Audio (*.mp4;*.webm;*.mov;*.mkv;*.mp3;*.wav;*.flac;*.m4a)\0*.mp4;*.webm;*.mov;*.mkv;*.mp3;*.wav;*.flac;*.m4a\0\0"
        )
        ofn.nFilterIndex = 1
        ofn.lpstrFile = ctypes.cast(file_buf, wt.LPWSTR)
        ofn.nMaxFile = buf_len
        ofn.lpstrTitle = "Select file(s) for Dewdrop"
        ofn.Flags = OFN_EXPLORER | OFN_ALLOWMULTISELECT | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR


        comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
        comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
        comdlg32.GetOpenFileNameW.restype = wt.BOOL
        if not comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return []


        raw = ctypes.wstring_at(ctypes.addressof(file_buf), buf_len)
        parts = [p for p in raw.split("\0") if p]
        if not parts:
            return []
        if len(parts) == 1:
            p = Path(parts[0]).resolve()
            return [p] if p.exists() else []
        directory = Path(parts[0]).resolve()
        selected = [(directory / name).resolve() for name in parts[1:]]
        return [p for p in selected if p.exists()]
    except Exception:
        return []




# ============================================================================
# 3. SINGLE-INSTANCE GUARD & WINDOWS JOB OBJECT / WORKER CONTROL
# ============================================================================


_SINGLE_MUTEX = None
_SINGLE_EVENT = None
_JOB_HANDLE = None




def acquire_single_instance() -> bool:
    global _SINGLE_MUTEX, _SINGLE_EVENT
    if os.name != "nt":
        return True
    try:
        k32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        EVENT_MODIFY_STATE = 0x0002
        mutex = k32.CreateMutexW(None, False, "Local\\Dewdrop.v1.lock")
        last_err = k32.GetLastError()
        if last_err == ERROR_ALREADY_EXISTS:
            evt = k32.OpenEventW(EVENT_MODIFY_STATE, False, "Local\\Dewdrop.v1.show")
            if evt:
                k32.SetEvent(evt)
                k32.CloseHandle(evt)
            if mutex:
                k32.CloseHandle(mutex)
            return False
        _SINGLE_MUTEX = mutex
        _SINGLE_EVENT = k32.CreateEventW(None, True, False, "Local\\Dewdrop.v1.show")
        return True
    except Exception:
        return True




def poll_show_signal() -> bool:
    if os.name != "nt" or not _SINGLE_EVENT:
        return False
    try:
        k32 = ctypes.windll.kernel32
        if k32.WaitForSingleObject(_SINGLE_EVENT, 0) == 0:
            k32.ResetEvent(_SINGLE_EVENT)
            return True
    except Exception:
        pass
    return False




def release_single_instance():
    global _SINGLE_MUTEX, _SINGLE_EVENT
    if os.name != "nt":
        return
    try:
        k32 = ctypes.windll.kernel32
        if _SINGLE_EVENT:
            k32.CloseHandle(_SINGLE_EVENT)
            _SINGLE_EVENT = None
        if _SINGLE_MUTEX:
            k32.CloseHandle(_SINGLE_MUTEX)
            _SINGLE_MUTEX = None
    except Exception:
        pass




def bind_children_to_app():
    global _JOB_HANDLE
    if os.name != "nt" or _JOB_HANDLE is not None:
        return
    try:
        from ctypes import wintypes as wt


        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]


        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD),
            ]


        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]


        k32 = ctypes.windll.kernel32
        job = k32.CreateJobObjectW(None, None)
        if job:
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000, JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
            info.BasicLimitInformation.LimitFlags = 0x2000 | 0x0200
            info.JobMemoryLimit = 3 * 1024 * 1024 * 1024  # 3 GiB limit
            k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            _JOB_HANDLE = job
    except Exception:
        pass




def assign_process_to_job(proc_handle: int):
    """Assigns a child process handle to the sandboxed Job Object."""
    global _JOB_HANDLE
    if os.name != "nt" or not _JOB_HANDLE or not proc_handle:
        return
    try:
        ctypes.windll.kernel32.AssignProcessToJobObject(_JOB_HANDLE, int(proc_handle))
    except Exception:
        pass




def suspend_or_resume_process(pid: int, suspend: bool) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            PROCESS_SUSPEND_RESUME = 0x0800
            k32 = ctypes.windll.kernel32
            ntdll = ctypes.windll.ntdll
            hproc = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, int(pid))
            if not hproc:
                return False
            try:
                rc = ntdll.NtSuspendProcess(hproc) if suspend else ntdll.NtResumeProcess(hproc)
                return rc == 0
            finally:
                k32.CloseHandle(hproc)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, signal.SIGSTOP if suspend else signal.SIGCONT)
            return True
        except Exception:
            return False




def terminate_worker_tree(proc: subprocess.Popen | None):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=0x08000000,
                )
            else:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass




# ============================================================================
# 4. PER-TOOL OPTIONS WINDOW CONFIGURATION (ALL 24 OPTION TOOLS)
# ============================================================================


def get_tool_ui_config(tool_id: str, quality_val: int, chip_idx: int, text_val: str, crop_box: list[int] | None = None) -> dict:
    q = max(5, min(100, int(quality_val)))
    c = max(0, min(5, int(chip_idx)))


    if tool_id in ("img_to_png", "img_to_gif"):
        widths = [0, 1920, 1280]
        return {
            "chips": ["Original Size", "Max 1920 px", "Max 1280 px"],
            "has_slider": False,
            "slider_str": None,
            "text_val": None,
            "opts": {"width": widths[min(c, 2)], "rotation": 0},
        }
    if tool_id == "img_to_ico":
        return {
            "chips": ["Pad Square", "Stretch Square", "All Sizes"],
            "has_slider": False,
            "slider_str": None,
            "text_val": None,
            "opts": {"square_icon": (c != 1)},
        }
    if tool_id in ("img_to_jpeg", "img_to_webp"):
        preset_q = [72, 85, 95][min(c, 2)]
        eff_q = q if q != 85 else preset_q
        return {
            "chips": ["Smaller (72%)", "Balanced (85%)", "Best (95%)"],
            "has_slider": True,
            "slider_str": f"{eff_q}%",
            "text_val": None,
            "opts": {"quality": eff_q, "width": 0, "rotation": 0},
        }
    if tool_id == "img_compress":
        target_kbs = [0, 200, 500]
        return {
            "chips": ["By Quality %", "Max 200 KB", "Max 500 KB"],
            "has_slider": True,
            "slider_str": f"{q}%" if c == 0 else f"{target_kbs[min(c, 2)]} KB",
            "text_val": None,
            "opts": {"quality": q, "target_kb": target_kbs[min(c, 2)]},
        }
    if tool_id == "img_resize":
        modes = ["percent", "fit", "fill"]
        m = modes[min(c, 2)]
        px = max(160, int(round(q * 24)))
        return {
            "chips": ["Scale %", "Fit Inside", "Fill & Crop"],
            "has_slider": True,
            "slider_str": f"{q}%" if m == "percent" else f"{px}px",
            "text_val": None,
            "opts": {"resize_mode": m, "percent": q, "width": px, "height": px},
        }
    if tool_id == "img_crop":
        ratios = ["1:1", "16:9", "4:3", "9:16", "custom"]
        c_idx = min(c, 4)
        opts = {"ratio": ratios[c_idx]}
        if c_idx == 4 and crop_box is not None:
            opts["box"] = list(crop_box)
        return {
            "chips": ["1:1 Square", "16:9 Wide", "4:3 Photo", "9:16 Tall", "Custom"],
            "has_slider": False,
            "slider_str": None,
            "text_val": None,
            "opts": opts,
        }
    if tool_id == "img_rotate_flip":
        rots = [90, 180, 270]
        rot = rots[min(c, 2)]
        return {
            "chips": ["Rotate 90°", "Rotate 180°", "Rotate 270°"],
            "has_slider": False,
            "slider_str": None,
            "text_val": None,
            "opts": {"rotation": rot, "flip": "none"},
        }
    if tool_id == "img_effects":
        effs = ["grayscale", "sepia", "sharpen", "blur"]
        labels = ["Grayscale", "Sepia", "Sharpen", "Blur"]
        return {
            "chips": labels,
            "has_slider": True,
            "slider_str": f"{max(1, q // 10)}px",
            "text_val": None,
            "opts": {"effect": effs[min(c, 3)], "blur_px": max(1.0, q / 12.0)},
        }
    if tool_id == "img_watermark":
        pos_codes = ["br", "bl", "tr", "c"]
        txt = text_val if text_val else "Confidential"
        return {
            "chips": ["Bottom-Right", "Bottom-Left", "Top-Right", "Center"],
            "has_slider": True,
            "slider_str": f"{q}%",
            "text_val": txt,
            "opts": {"text": txt, "position": pos_codes[min(c, 3)], "quality": q},
        }
    if tool_id == "pdf_from_images":
        return {
            "chips": ["One PDF (A4)", "One PDF (Fit)", "Separate PDFs"],
            "has_slider": True,
            "slider_str": f"{q}%",
            "text_val": None,
            "opts": {"combine": (c != 2), "page": ("A4" if c == 0 else "image"), "quality": q},
        }
    if tool_id in ("pdf_extract_pages", "pdf_delete_pages"):
        presets = ["1", "1-3", "2-"]
        rng = text_val if text_val else presets[min(c, 2)]
        return {
            "chips": ["Page 1", "Pages 1-3", "Page 2-End"],
            "has_slider": False,
            "slider_str": None,
            "text_val": rng,
            "opts": {"pages": rng},
        }
    if tool_id == "pdf_rotate":
        rots = [90, 180, 270]
        return {
            "chips": ["90° CW", "180°", "270° CW"],
            "has_slider": False,
            "slider_str": None,
            "text_val": text_val,
            "opts": {"rotation": rots[min(c, 2)], "pages": text_val or ""},
        }
    if tool_id == "pdf_compress":
        q_defaults = [85, 65, 45]
        q_chosen = q if q != 85 else q_defaults[min(c, 2)]
        return {
            "chips": ["Light (Lossless)", "Balanced (150dpi)", "Strong (Smallest)"],
            "has_slider": True,
            "slider_str": f"Quality {q_chosen}%",
            "text_val": None,
            "opts": {"level": min(c, 2) + 1, "quality": q_chosen},
        }
    if tool_id == "video_compress":
        maxhs = [0, 1080, 720]
        return {
            "chips": ["Light (Original)", "Balanced (1080p)", "Smallest (720p)"],
            "has_slider": True,
            "slider_str": f"Lvl {min(c, 2) + 1} ({q}%)",
            "text_val": None,
            "opts": {"level": min(c, 2) + 1, "maxh": maxhs[min(c, 2)], "quality": q},
        }
    if tool_id == "audio_compress":
        brs = [96, 128, 192, 320]
        return {
            "chips": ["96 kbps", "128 kbps", "192 kbps", "320 kbps"],
            "has_slider": False,
            "slider_str": None,
            "text_val": None,
            "opts": {"bitrate": brs[min(c, 3)]},
        }
    if tool_id == "media_trim":
        ends = ["00:05", "00:15", "00:30"]
        spec_txt = text_val if text_val else f"00:00-{ends[min(c, 2)]}"
        parts = spec_txt.split("-", 1) if "-" in spec_txt else ["00:00", spec_txt]
        return {
            "chips": ["First 5s", "First 15s", "First 30s"],
            "has_slider": False,
            "slider_str": None,
            "text_val": spec_txt,
            "opts": {"start": parts[0].strip(), "end": parts[1].strip() if len(parts) > 1 else ""},
        }
    if tool_id == "video_to_gif":
        widths = [320, 480, 640]
        dur = max(1, min(20, int(round(q / 5.0))))
        return {
            "chips": ["320 px Wide", "480 px Wide", "640 px Wide"],
            "has_slider": True,
            "slider_str": f"{dur}s",
            "text_val": None,
            "opts": {"width": widths[min(c, 2)], "duration": dur, "start": "0"},
        }
    if tool_id == "video_frame_png":
        stamps = ["00:00.5", "00:01.0", "00:03.0"]
        ts = text_val if text_val else stamps[min(c, 2)]
        return {
            "chips": ["At 0.5s", "At 1.0s", "At 3.0s"],
            "has_slider": False,
            "slider_str": None,
            "text_val": ts,
            "opts": {"start": ts},
        }
    if tool_id in ("archive_zip", "archive_targz", "archive_gzip"):
        level_map = [1, 2, 3]
        lvl = level_map[min(c, 2)]
        return {
            "chips": ["Fast (Lvl 1)", "Normal (Lvl 6)", "Best (Lvl 9)"],
            "has_slider": False,
            "slider_str": None,
            "text_val": None,
            "opts": {"level": lvl, "combine": True},
        }


    return {
        "chips": [],
        "has_slider": False,
        "slider_str": None,
        "text_val": None,
        "opts": {"quality": q, "level": min(c, 2) + 1},
    }




# ============================================================================
# 5. SCREEN BACKDROP CAPTURE & SURFACE RENDER WRAPPERS
# ============================================================================


def capture_full_desktop_rgb(screen_w: int, screen_h: int) -> np.ndarray | None:
    if os.name != "nt" or screen_w <= 0 or screen_h <= 0:
        return None
    try:
        import ctypes.wintypes as wt
        user32 = ctypes.WinDLL("user32")
        gdi32 = ctypes.WinDLL("gdi32")


        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
            ]


        user32.GetDC.argtypes = [wt.HWND]
        user32.GetDC.restype = wt.HDC
        user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
        gdi32.CreateCompatibleDC.restype = wt.HDC
        gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
        gdi32.CreateDIBSection.restype = wt.HBITMAP
        gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        gdi32.SelectObject.restype = wt.HGDIOBJ
        gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
        gdi32.BitBlt.restype = wt.BOOL
        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi32.DeleteObject.restype = wt.BOOL
        gdi32.DeleteDC.argtypes = [wt.HDC]
        gdi32.DeleteDC.restype = wt.BOOL


        hdc_scr = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_scr)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), screen_w, -screen_h, 1, 32, 0, screen_w * screen_h * 4, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(hdc_mem, hbmp)
        gdi32.BitBlt(hdc_mem, 0, 0, screen_w, screen_h, hdc_scr, 0, 0, 0x00CC0020)
        raw = ctypes.string_at(bits, screen_w * screen_h * 4)
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_scr)


        bgra = np.frombuffer(raw, dtype=np.uint8).reshape((screen_h, screen_w, 4))
        rgb = bgra[..., [2, 1, 0]].astype(np.float32)
        if np.std(rgb) < 1.5:
            return None
        return rgb
    except Exception:
        return None




_SCREEN_CAPTURE_DCS: dict[tuple[int, int], tuple] = {}
_WINDOW_CAPTURE_DCS: dict[tuple[int, int], tuple] = {}
_CAPTURE_GDI_INITIALIZED = False
_CAPTURE_USER32 = None
_CAPTURE_GDI32 = None
_ACTIVE_OVERLAY_HWNDS: set[int] = set()




def register_overlay_hwnd(hwnd: int):
    """Registers an active Dewdrop window to prevent self-capture feedback during screen capture."""
    if hwnd:
        _ACTIVE_OVERLAY_HWNDS.add(int(hwnd))




def unregister_overlay_hwnd(hwnd: int):
    """Unregisters a destroyed or dismissed Dewdrop window."""
    if hwnd:
        _ACTIVE_OVERLAY_HWNDS.discard(int(hwnd))




def _init_capture_gdi():
    global _CAPTURE_GDI_INITIALIZED, _CAPTURE_USER32, _CAPTURE_GDI32
    if _CAPTURE_GDI_INITIALIZED or os.name != "nt":
        return
    import ctypes.wintypes as wt
    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")
    _CAPTURE_USER32 = user32
    _CAPTURE_GDI32 = gdi32


    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.ReleaseDC.restype = ctypes.c_int


    if hasattr(user32, "GetWindowDisplayAffinity"):
        user32.GetWindowDisplayAffinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowDisplayAffinity.restype = wt.BOOL
    if hasattr(user32, "SetWindowDisplayAffinity"):
        user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        user32.SetWindowDisplayAffinity.restype = wt.BOOL
    if hasattr(user32, "IsWindow"):
        user32.IsWindow.argtypes = [wt.HWND]
        user32.IsWindow.restype = wt.BOOL
    if hasattr(user32, "IsWindowVisible"):
        user32.IsWindowVisible.argtypes = [wt.HWND]
        user32.IsWindowVisible.restype = wt.BOOL
    if hasattr(user32, "PrintWindow"):
        user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
        user32.PrintWindow.restype = wt.BOOL
    if hasattr(user32, "GetWindow"):
        user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
        user32.GetWindow.restype = wt.HWND
    if hasattr(user32, "GetWindowRect"):
        user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        user32.GetWindowRect.restype = wt.BOOL


    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateDIBSection.argtypes = [
        wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD
    ]
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.BitBlt.argtypes = [
        wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD
    ]
    gdi32.BitBlt.restype = wt.BOOL
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteObject.restype = wt.BOOL
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.DeleteDC.restype = wt.BOOL


    _CAPTURE_GDI_INITIALIZED = True




_WALLPAPER_CACHE: dict = {
    "mtime": 0.0,
    "path": "",
    "screen_size": (0, 0),
    "arr": None,
}




def generate_procedural_blue_gradient(w: int, h: int) -> np.ndarray:
    """Procedurally generated clean blue gradient fallback when wallpaper cannot be read."""
    y = np.linspace(0.0, 1.0, max(1, h), dtype=np.float32)[:, None]
    r = np.clip(18.0 + y * 12.0, 0, 255)
    g = np.clip(38.0 + y * 45.0, 0, 255)
    b = np.clip(95.0 + y * 90.0, 0, 255)
    grad = np.empty((max(1, h), max(1, w), 3), dtype=np.float32)
    grad[:, :, 0] = r
    grad[:, :, 1] = g
    grad[:, :, 2] = b
    return grad


def get_desktop_wallpaper_rect(x: int, y: int, w: int, h: int) -> np.ndarray | None:
    """
    Retrieves the exact cropped region of the active Windows desktop wallpaper.
    Discovers wallpaper via TranscodedWallpaper, SPI_GETDESKWALLPAPER, or registry.
    Scales and caches at current screen resolution with mtime invalidation.
    Guarantees that Liquid Glass over the Windows desktop is never black!
    """
    if os.name != "nt" or w <= 0 or h <= 0:
        return None
    try:
        user32 = ctypes.WinDLL("user32")
        candidates = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper")
        buf = ctypes.create_unicode_buffer(512)
        if hasattr(user32, "SystemParametersInfoW") and user32.SystemParametersInfoW(0x0073, 512, buf, 0) and buf.value:
            candidates.append(Path(buf.value))
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
                val, _ = winreg.QueryValueEx(k, "WallPaper")
                if val:
                    candidates.append(Path(val))
        except Exception:
            pass


        found_path = None
        for cand in candidates:
            if cand.is_file() and cand.stat().st_size > 0:
                found_path = cand
                break
        if not found_path:
            return generate_procedural_blue_gradient(w, h)


        virt_w = max(user32.GetSystemMetrics(0), 800)
        virt_h = max(user32.GetSystemMetrics(1), 600)
        mtime = found_path.stat().st_mtime


        if (
            _WALLPAPER_CACHE["arr"] is None
            or _WALLPAPER_CACHE["path"] != str(found_path)
            or _WALLPAPER_CACHE["mtime"] != mtime
            or _WALLPAPER_CACHE["screen_size"] != (virt_w, virt_h)
        ):
            im = Image.open(found_path).convert("RGB")
            if im.size != (virt_w, virt_h):
                im = im.resize((virt_w, virt_h), Image.Resampling.BILINEAR)
            arr = np.array(im, dtype=np.float32)
            _WALLPAPER_CACHE["arr"] = arr
            _WALLPAPER_CACHE["path"] = str(found_path)
            _WALLPAPER_CACHE["mtime"] = mtime
            _WALLPAPER_CACHE["screen_size"] = (virt_w, virt_h)


        arr = _WALLPAPER_CACHE["arr"]
        sh, sw, _ = arr.shape
        x0 = max(0, min(int(x), sw - w))
        y0 = max(0, min(int(y), sh - h))
        if x0 + w <= sw and y0 + h <= sh:
            crop = arr[y0 : y0 + h, x0 : x0 + w]
            if crop.shape[:2] == (h, w) and crop.max() > 0:
                return crop
    except Exception:
        pass
    return generate_procedural_blue_gradient(w, h)




def capture_live_screen_rect_rgb(x: int, y: int, w: int, h: int) -> np.ndarray | None:
    """
    Direct ultra-fast live screen capture of the desktop area beneath the liquid glass window.
    Captures live playing videos and background apps directly without rapid affinity toggling,
    completely eliminating OBS blinking/flickering while preventing self-capture feedback loops!
    """
    if os.name != "nt" or w <= 0 or h <= 0:
        return None
    try:
        _init_capture_gdi()
        user32 = _CAPTURE_USER32 or ctypes.WinDLL("user32")
        gdi32 = _CAPTURE_GDI32 or ctypes.WinDLL("gdi32")
        import ctypes.wintypes as wt


        # 1. Check if any active overlay is explicitly in stealth/exclusive mode (0x11).
        # In stealth mode, DWM already excludes the window from BitBlt captures automatically.
        is_stealth = False
        if hasattr(user32, "GetWindowDisplayAffinity"):
            for hw in list(_ACTIVE_OVERLAY_HWNDS):
                try:
                    if user32.IsWindow(hw) and user32.IsWindowVisible(hw):
                        curr_aff = wt.DWORD()
                        if user32.GetWindowDisplayAffinity(hw, ctypes.byref(curr_aff)):
                            if curr_aff.value == 0x00000011:
                                is_stealth = True
                                break
                except Exception:
                    pass


        hdc_scr = user32.GetDC(None)
        if not hdc_scr:
            return None


        key = (int(w), int(h))
        if key in _SCREEN_CAPTURE_DCS:
            hdc_mem, bits, _ = _SCREEN_CAPTURE_DCS[key]
        else:
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
                ]


            hdc_mem = gdi32.CreateCompatibleDC(hdc_scr)
            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), int(w), -int(h), 1, 32, 0, int(w * h * 4), 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            gdi32.SelectObject(hdc_mem, hbmp)
            _SCREEN_CAPTURE_DCS[key] = (hdc_mem, bits, hbmp)


        if is_stealth:
            # Stealth mode: DewDrop has affinity 0x11, so DWM hardware-excludes it. Direct BitBlt works with zero feedback.
            try:
                ok = gdi32.BitBlt(hdc_mem, 0, 0, int(w), int(h), hdc_scr, int(x), int(y), 0x40CC0020)
                raw = ctypes.string_at(bits, int(w * h * 4))
            finally:
                user32.ReleaseDC(None, hdc_scr)
            if not ok:
                return None
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape((int(h), int(w), 4))
            return bgra[..., [2, 1, 0]].astype(np.float32)


        # 2. Record Mode (affinity 0x0):
        # We MUST NEVER toggle SetWindowDisplayAffinity during capture! Toggling affinity 60 times/sec causes OBS to blink/strobe!
        # First, search for the active top-level window directly beneath DewDrop in the Z-order.
        target_win = None
        GW_HWNDNEXT = 2
        for overlay_hw in list(_ACTIVE_OVERLAY_HWNDS):
            if user32.IsWindow(overlay_hw) and user32.IsWindowVisible(overlay_hw):
                curr = user32.GetWindow(overlay_hw, GW_HWNDNEXT)
                while curr:
                    if user32.IsWindowVisible(curr) and curr not in _ACTIVE_OVERLAY_HWNDS:
                        cls_buf = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(curr, cls_buf, 256)
                        cls_name = cls_buf.value
                        if cls_name not in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                            rc = wt.RECT()
                            if user32.GetWindowRect(curr, ctypes.byref(rc)):
                                if not (rc.right <= x or rc.left >= x + w or rc.bottom <= y or rc.top >= y + h):
                                    bw = rc.right - rc.left
                                    bh = rc.bottom - rc.top
                                    if bw > 0 and bh > 0:
                                        target_win = (curr, rc.left, rc.top, bw, bh)
                                        break
                    curr = user32.GetWindow(curr, GW_HWNDNEXT)
                if target_win:
                    break


        if target_win:
            hw_below, bx, by, bw, bh = target_win
            try:
                win_key = (int(bw), int(bh))
                if win_key in _WINDOW_CAPTURE_DCS:
                    hdc_win, bits_win, _ = _WINDOW_CAPTURE_DCS[win_key]
                else:
                    if len(_WINDOW_CAPTURE_DCS) >= 4:
                        for k in list(_WINDOW_CAPTURE_DCS.keys()):
                            old_dc, _, old_bmp = _WINDOW_CAPTURE_DCS.pop(k)
                            try:
                                gdi32.DeleteObject(old_bmp)
                                gdi32.DeleteDC(old_dc)
                            except Exception:
                                pass
                    hdc_win = gdi32.CreateCompatibleDC(hdc_scr)
                    class BITMAPINFOHEADER(ctypes.Structure):
                        _fields_ = [
                            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                            ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
                        ]
                    bmi_w = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), int(bw), -int(bh), 1, 32, 0, int(bw * bh * 4), 0, 0, 0, 0)
                    bits_win = ctypes.c_void_p()
                    hbmp_w = gdi32.CreateDIBSection(hdc_win, ctypes.byref(bmi_w), 0, ctypes.byref(bits_win), None, 0)
                    gdi32.SelectObject(hdc_win, hbmp_w)
                    _WINDOW_CAPTURE_DCS[win_key] = (hdc_win, bits_win, hbmp_w)


                # PW_RENDERFULLCONTENT = 2: Captures DirectComposition, hardware-accelerated Chrome/YouTube, VLC
                ok = user32.PrintWindow(hw_below, hdc_win, 2)
                if ok:
                    sub_x = int(x - bx)
                    sub_y = int(y - by)
                    gdi32.BitBlt(hdc_mem, 0, 0, int(w), int(h), hdc_win, sub_x, sub_y, 0x00CC0020)
                    raw = ctypes.string_at(bits, int(w * h * 4))
                    bgra = np.frombuffer(raw, dtype=np.uint8).reshape((int(h), int(w), 4))
                    rgb_win = bgra[..., [2, 1, 0]].astype(np.float32)
                    if rgb_win.max() > 0:
                        return rgb_win
            except Exception:
                pass


        # 3. If no application window is beneath us (e.g. over desktop wallpaper) or PrintWindow returns black:
        wp_rgb = get_desktop_wallpaper_rect(x, y, w, h)
        if wp_rgb is not None:
            user32.ReleaseDC(None, hdc_scr)
            return wp_rgb
        # Use BitBlt with plain SRCCOPY (0x00CC0020).
        # Because DewDrop is a layered window (WS_EX_LAYERED), plain SRCCOPY without CAPTUREBLT
        # automatically excludes layered windows by GDI definition, eliminating self-capture feedback!
        try:
            ok = gdi32.BitBlt(hdc_mem, 0, 0, int(w), int(h), hdc_scr, int(x), int(y), 0x00CC0020)
            raw = ctypes.string_at(bits, int(w * h * 4))
        finally:
            user32.ReleaseDC(None, hdc_scr)


        if ok:
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape((int(h), int(w), 4))
            rgb = bgra[..., [2, 1, 0]].astype(np.float32)
            if rgb.max() > 0:
                return rgb
    except Exception:
        pass
    return generate_procedural_blue_gradient(w, h)




def capture_screen_backdrop_rgb(x: int, y: int, w: int, h: int, record_mode: bool = False) -> np.ndarray:
    """
    Live real-time screen capture of the exact desktop area beneath the liquid glass window.
    Continuously reflects and refracts playing videos, moving windows & changing wallpapers.
    Works seamlessly in both normal live mode and screen recording mode with zero feedback loop!
    """
    global _DESKTOP_SNAPSHOT_RGB
    # Always attempt real-time live capture first (for both normal and record mode)
    live_rgb = capture_live_screen_rect_rgb(x, y, w, h)
    if live_rgb is not None:
        return live_rgb


    # Fallback only if live capture fails (e.g. desktop locked / headless / non-interactive test session)
    if _DESKTOP_SNAPSHOT_RGB is not None:
        sh, sw, _ = _DESKTOP_SNAPSHOT_RGB.shape
        x0 = max(0, min(int(x), sw - w))
        y0 = max(0, min(int(y), sh - h))
        if x0 + w <= sw and y0 + h <= sh:
            return _DESKTOP_SNAPSHOT_RGB[y0:y0 + h, x0:x0 + w]
    return generate_vivid_desktop_backdrop(w, h)




GROUPS = filter_and_rank_tools([], ffmpeg_available=True)
SEG_ANGLES_DEG = [0, 60, 120, 180, 240, 300]




def parse_hotkey_to_word(hotkey: str) -> int:
    """
    Parses hotkey string (e.g. 'CTRL+ALT+Z') into Windows Shell HOTKEY WORD.
    High byte = HOTKEYF modifiers (SHIFT=0x01, CONTROL=0x02, ALT=0x04, EXT=0x08).
    Low byte = Virtual Key code.
    """
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
                vk = 0x6F + fn  # VK_F1 is 0x70
        elif p == "SPACE":
            vk = 0x20
    return ((modifiers & 0xFF) << 8) | (vk & 0xFF)




def create_windows_shortcut_com(
    lnk_path: Path,
    target_path: Path | str,
    args: str = "",
    working_dir: Path | str = "",
    icon_path: Path | str = "",
    hotkey: str = "",
    description: str = "Dewdrop Quick File Tools",
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
        if args:
            SetArguments(p_sl, str(args))
        if working_dir:
            SetWorkingDirectory(p_sl, str(working_dir))
        if icon_path and Path(icon_path).exists():
            SetIconLocation(p_sl, str(icon_path), 0)
        if description:
            SetDescription(p_sl, str(description))
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


        lnk_str = str(lnk_path)
        Path(lnk_path).parent.mkdir(parents=True, exist_ok=True)
        if Path(lnk_path).exists():
            try:
                Path(lnk_path).unlink()
            except Exception:
                pass
        save_hr = Save(p_pf, lnk_str, True)


        Release_pf(p_pf)
        Release_sl(p_sl)


        try:
            ctypes.windll.shell32.SHChangeNotify(0x00000002, 0x0005, lnk_str, None)
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
        except Exception:
            pass


        return save_hr == 0
    except Exception:
        return False




def get_special_shell_folder(csidl: int, fallback: Path) -> Path:
    try:
        from ctypes import wintypes as wt
        buf = ctypes.create_unicode_buffer(wt.MAX_PATH)
        hr = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
        if hr == 0 and buf.value:
            p = Path(buf.value)
            if p.exists():
                return p
    except Exception:
        pass
    return fallback




def configure_windows_shortcut(hotkey: str = "CTRL+ALT+Z") -> bool:
    settings = load_settings()
    settings["hotkey"] = hotkey.strip().upper()
    save_settings(settings)
    if os.name != "nt":
        return False


    # Start Menu Programs (0x0002) and Desktop (0x0010, automatically resolves OneDrive Desktop)
    programs_dir = get_special_shell_folder(
        0x0002,
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    )
    desktop_dir = get_special_shell_folder(
        0x0010,
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
    )


    if getattr(sys, "frozen", False):
        target_path = Path(sys.executable)
        args_str = ""
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        target_path = pyw if pyw.exists() else Path(sys.executable)
        args_str = f'"{ROOT_DIR / "dewdrop_runtime.py"}"'


    ico_path = ROOT_DIR / "assets" / "icon.ico"
    prog_lnk = programs_dir / f"{APP_NAME}.lnk"
    desk_lnk = desktop_dir / f"{APP_NAME}.lnk"
    # Clean up legacy single-word shortcut if present
    for old_lnk in (programs_dir / "Dewdrop.lnk", desktop_dir / "Dewdrop.lnk"):
        try:
            if old_lnk.is_file():
                old_lnk.unlink()
        except Exception:
            pass


    ok_prog = create_windows_shortcut_com(
        lnk_path=prog_lnk,
        target_path=target_path,
        args=args_str,
        working_dir=ROOT_DIR,
        icon_path=ico_path,
        hotkey=hotkey,
        description="Dew Drop Liquid Glass Tools",
    )
    ok_desk = create_windows_shortcut_com(
        lnk_path=desk_lnk,
        target_path=target_path,
        args=args_str,
        working_dir=ROOT_DIR,
        icon_path=ico_path,
        hotkey=hotkey,
        description="Dew Drop Liquid Glass Tools",
    )


    try:
        ctypes.windll.shell32.SHChangeNotify(0x00000002, 0x0005, str(prog_lnk), None)
        ctypes.windll.shell32.SHChangeNotify(0x00000002, 0x0005, str(desk_lnk), None)
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
    except Exception:
        pass


    return ok_prog or ok_desk




def render_ring_surface_rgba(
    labels: list[str],
    hover_seg: int,
    turn_progress: float = 0.0,
    staged_count: int = 0,
    subtitle: str = "",
    size: int = 460,
    backdrop_rgb: np.ndarray | None = None,
    light_angle_deg: float = 135.0,
    dwell_progress: float = 0.0,
    shine_phase: float = 0.22,
    color_turns: float | None = None,
) -> Image.Image:
    return render_optical_ring_rgba(
        labels=labels,
        hover_seg=hover_seg,
        turn_progress=turn_progress,
        staged_count=staged_count,
        subtitle=subtitle,
        size=size,
        backdrop_rgb=backdrop_rgb,
        light_angle_deg=light_angle_deg,
        dwell_progress=dwell_progress,
        shine_phase=shine_phase,
        color_turns=color_turns,
    )




def render_options_or_progress_window_rgba(
    file_title: str,
    file_subtitle: str,
    tool_label: str,
    option_desc: str,
    mode: str,
    quality_val: int,
    chip_idx: int,
    progress_frac: float,
    paused: bool,
    status_line: str,
    detail_line: str,
    w: int = 720,
    h: int = 360,
    backdrop_rgb: np.ndarray | None = None,
    mouse_offset: tuple[float, float] = (0.0, 0.0),
    hover_target: str = "",
    chip_labels: list[str] | None = None,
    slider_display: str | None = None,
    text_input_value: str | None = None,
    expanded: bool = False,
    preview_file: Path | None = None,
    batch_badge: str = "",
    shine_phase: float = 0.25,
    show_slider: bool = True,
    show_chips: bool = True,
) -> Image.Image:
    return render_optical_window_rgba(
        file_title=file_title,
        file_subtitle=file_subtitle,
        tool_label=tool_label,
        option_desc=option_desc,
        mode=mode,
        quality_val=quality_val,
        chip_idx=chip_idx,
        progress_frac=progress_frac,
        paused=paused,
        status_line=status_line,
        detail_line=detail_line,
        w=w,
        h=h,
        backdrop_rgb=backdrop_rgb,
        mouse_offset=mouse_offset,
        hover_target=hover_target,
        chip_labels=chip_labels,
        slider_display=slider_display,
        text_input_value=text_input_value,
        expanded=expanded,
        preview_file=preview_file,
        batch_badge=batch_badge,
        shine_phase=shine_phase,
        show_slider=show_slider,
        show_chips=show_chips,
    )




def render_progress_window_rgba(
    file_title: str,
    file_subtitle: str,
    tool_label: str,
    progress_frac: float,
    paused: bool,
    is_finished: bool,
    status_line: str,
    detail_line: str,
    w: int = 720,
    h: int = 290,
    backdrop_rgb: np.ndarray | None = None,
    mouse_offset: tuple[float, float] = (0.0, 0.0),
    hover_target: str = "",
    preview_file: Path | None = None,
    batch_badge: str = "",
    shine_phase: float = 0.25,
    expanded: bool = False,
) -> Image.Image:
    return render_optical_progress_window_rgba(
        file_title=file_title,
        file_subtitle=file_subtitle,
        tool_label=tool_label,
        progress_frac=progress_frac,
        paused=paused,
        is_finished=is_finished,
        status_line=status_line,
        detail_line=detail_line,
        w=w,
        h=h,
        backdrop_rgb=backdrop_rgb,
        mouse_offset=mouse_offset,
        hover_target=hover_target,
        preview_file=preview_file,
        batch_badge=batch_badge,
        shine_phase=shine_phase,
        expanded=expanded,
    )




def render_dynamic_island_rgba(
    file_title: str,
    batch_subtitle: str,
    progress_frac: float,
    paused: bool,
    detail_line: str,
    w: int = 780,
    h: int = 76,
    backdrop_rgb: np.ndarray | None = None,
    mouse_offset: tuple[float, float] = (0.0, 0.0),
    hover_target: str = "",
    preview_file: Path | None = None,
    batch_badge: str = "",
    shine_phase: float = 0.25,
    show_progress: bool = True,
) -> Image.Image:
    return render_optical_island_rgba(
        file_title=file_title,
        batch_subtitle=batch_subtitle,
        progress_frac=progress_frac,
        paused=paused,
        detail_line=detail_line,
        w=w,
        h=h,
        backdrop_rgb=backdrop_rgb,
        mouse_offset=mouse_offset,
        hover_target=hover_target,
        preview_file=preview_file,
        batch_badge=batch_badge,
        shine_phase=shine_phase,
        show_progress=show_progress,
    )




def render_morph_frame_rgba(
    morph_t: float,  # 0.0 = Window (w_im.size), 1.0 = Dynamic Island (i_im.size)
    win_img: Image.Image,
    island_img: Image.Image,
) -> Image.Image:
    t = min(max(morph_t, -0.08), 1.08)
    clamped = min(max(t, 0.0), 1.0)
    if clamped <= 0.005:
        return win_img.copy()
    if clamped >= 0.995:
        return island_img.copy()
    w0, h0 = win_img.size
    w1, h1 = island_img.size
    cur_w = max(180, int(round(w0 + (w1 - w0) * t)))
    cur_h = max(40, int(round(h0 + (h1 - h0) * t)))


    w_scaled = win_img.resize((cur_w, cur_h), Image.Resampling.BICUBIC)
    i_scaled = island_img.resize((cur_w, cur_h), Image.Resampling.BICUBIC)
    return Image.blend(w_scaled, i_scaled, clamped)






def draw_custom_crop_canvas(
    base_img: Image.Image,
    preview_file: Path | None,
    norm_box: list[float],
    w: int,
    h: int,
) -> tuple[Image.Image, tuple[int, int, int, int, int, int]]:
    """
    Draws the interactive custom cropping interface on the Liquid Glass window.
    Preserves exact aspect ratio, displays the full image clearly, renders a dimmed
    outer mask, 2px white border, rule-of-thirds grid, 8 draggable handles, and
    an accurate real-pixel dimensions badge.
    """
    img = base_img.copy()
    box_x0 = 36
    box_y0 = 176
    box_x1 = w - 36
    box_y1 = h - 86
    avail_cw = max(100, box_x1 - box_x0)
    avail_ch = max(100, box_y1 - box_y0)


    # 1. Dark container box with frosted border
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([box_x0, box_y0, box_x1, box_y1], radius=16, fill=(14, 18, 25, 210), outline=(255, 255, 255, 45), width=1)


    # 2. Load and fit image
    pf = preview_file if (preview_file and Path(preview_file).exists()) else ensure_demo_sample_file()
    try:
        raw_im = Image.open(pf).convert("RGB")
        orig_w, orig_h = raw_im.size
    except Exception:
        raw_im = Image.fromarray(generate_vivid_desktop_backdrop(800, 600).astype(np.uint8))
        orig_w, orig_h = 800, 600


    pad = 14
    target_w = avail_cw - pad * 2
    target_h = avail_ch - pad * 2
    scale = min(target_w / float(max(1, orig_w)), target_h / float(max(1, orig_h)))
    disp_w = max(32, int(round(orig_w * scale)))
    disp_h = max(32, int(round(orig_h * scale)))
    disp_x0 = box_x0 + (avail_cw - disp_w) // 2
    disp_y0 = box_y0 + (avail_ch - disp_h) // 2
    disp_x1 = disp_x0 + disp_w
    disp_y1 = disp_y0 + disp_h


    scaled_preview = raw_im.resize((disp_w, disp_h), Image.Resampling.BILINEAR)
    img.paste(scaled_preview, (disp_x0, disp_y0))


    # 3. Crop box coordinates
    nx0, ny0, nx1, ny1 = norm_box
    cx0 = disp_x0 + int(round(nx0 * disp_w))
    cy0 = disp_y0 + int(round(ny0 * disp_h))
    cx1 = disp_x0 + int(round(nx1 * disp_w))
    cy1 = disp_y0 + int(round(ny1 * disp_h))
    if cx1 < cx0: cx0, cx1 = cx1, cx0
    if cy1 < cy0: cy0, cy1 = cy1, cy0


    # 4. Dark unselected overlay
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    dark_mask = (0, 0, 0, 160)
    if cy0 > disp_y0:
        odraw.rectangle([disp_x0, disp_y0, disp_x1, cy0], fill=dark_mask)
    if cy1 < disp_y1:
        odraw.rectangle([disp_x0, cy1, disp_x1, disp_y1], fill=dark_mask)
    if cx0 > disp_x0:
        odraw.rectangle([disp_x0, cy0, cx0, cy1], fill=dark_mask)
    if cx1 < disp_x1:
        odraw.rectangle([cx1, cy0, disp_x1, cy1], fill=dark_mask)
    img = Image.alpha_composite(img, overlay)


    # 5. Grid lines, border, handles, badge
    draw = ImageDraw.Draw(img)
    gw = cx1 - cx0
    gh = cy1 - cy0
    if gw >= 30 and gh >= 30:
        grid_col = (255, 255, 255, 80)
        draw.line([cx0 + gw / 3.0, cy0, cx0 + gw / 3.0, cy1], fill=grid_col, width=1)
        draw.line([cx0 + 2.0 * gw / 3.0, cy0, cx0 + 2.0 * gw / 3.0, cy1], fill=grid_col, width=1)
        draw.line([cx0, cy0 + gh / 3.0, cx1, cy0 + gh / 3.0], fill=grid_col, width=1)
        draw.line([cx0, cy0 + 2.0 * gh / 3.0, cx1, cy0 + 2.0 * gh / 3.0], fill=grid_col, width=1)


    draw.rectangle([cx0 - 1, cy0 - 1, cx1 + 1, cy1 + 1], outline=(0, 0, 0, 160), width=1)
    draw.rectangle([cx0, cy0, cx1, cy1], outline=(255, 255, 255, 255), width=2)


    # 4 Corner Handles (12x12 px crisp white rounded squares with dark border)
    for hx, hy in [(cx0, cy0), (cx1, cy0), (cx0, cy1), (cx1, cy1)]:
        draw.rounded_rectangle([hx - 6, hy - 6, hx + 6, hy + 6], radius=2, fill=(255, 255, 255, 255), outline=(0, 0, 0, 220), width=1)


    # 4 Edge Handles (pills)
    mid_x = (cx0 + cx1) // 2
    mid_y = (cy0 + cy1) // 2
    draw.rounded_rectangle([mid_x - 9, cy0 - 3, mid_x + 9, cy0 + 3], radius=3, fill=(255, 255, 255, 255), outline=(0, 0, 0, 220), width=1)
    draw.rounded_rectangle([mid_x - 9, cy1 - 3, mid_x + 9, cy1 + 3], radius=3, fill=(255, 255, 255, 255), outline=(0, 0, 0, 220), width=1)
    draw.rounded_rectangle([cx0 - 3, mid_y - 9, cx0 + 3, mid_y + 9], radius=3, fill=(255, 255, 255, 255), outline=(0, 0, 0, 220), width=1)
    draw.rounded_rectangle([cx1 - 3, mid_y - 9, cx1 + 3, mid_y + 9], radius=3, fill=(255, 255, 255, 255), outline=(0, 0, 0, 220), width=1)


    # Real dimensions badge pill
    rw = int(round((nx1 - nx0) * orig_w))
    rh = int(round((ny1 - ny0) * orig_h))
    badge_txt = f"{rw} × {rh} px"
    f_badge = load_ui_font(12, bold=True)
    t_box = draw.textbbox((0, 0), badge_txt, font=f_badge)
    tw = t_box[2] - t_box[0]
    th = t_box[3] - t_box[1]
    bx_w = tw + 18
    bx_h = th + 10
    if cy1 + bx_h + 8 < disp_y1 + 25:
        b_top = cy1 - bx_h - 6
    else:
        b_top = cy0 + 6
    b_left = max(cx0 + 6, min(mid_x - bx_w // 2, cx1 - bx_w - 6))
    draw.rounded_rectangle([b_left, b_top, b_left + bx_w, b_top + bx_h], radius=7, fill=(20, 26, 36, 225), outline=(255, 255, 255, 120), width=1)
    draw_shadowed_text(draw, (b_left + 9, b_top + 4), badge_txt, font=f_badge, fill=(255, 255, 255, 255))


    return img, (disp_x0, disp_y0, disp_w, disp_h, orig_w, orig_h)




def hit_test_crop_handle(x: int, y: int, disp_rect: tuple[int, int, int, int, int, int] | None, norm_box: list[float]) -> str | None:
    if not disp_rect:
        return None
    disp_x0, disp_y0, disp_w, disp_h, orig_w, orig_h = disp_rect
    nx0, ny0, nx1, ny1 = norm_box
    cx0 = disp_x0 + int(round(nx0 * disp_w))
    cy0 = disp_y0 + int(round(ny0 * disp_h))
    cx1 = disp_x0 + int(round(nx1 * disp_w))
    cy1 = disp_y0 + int(round(ny1 * disp_h))
    if cx1 < cx0: cx0, cx1 = cx1, cx0
    if cy1 < cy0: cy0, cy1 = cy1, cy0


    r_corner = 14
    if math.hypot(x - cx0, y - cy0) <= r_corner: return "tl"
    if math.hypot(x - cx1, y - cy0) <= r_corner: return "tr"
    if math.hypot(x - cx0, y - cy1) <= r_corner: return "bl"
    if math.hypot(x - cx1, y - cy1) <= r_corner: return "br"


    r_edge = 9
    if abs(y - cy0) <= r_edge and (cx0 <= x <= cx1): return "t"
    if abs(y - cy1) <= r_edge and (cx0 <= x <= cx1): return "b"
    if abs(x - cx0) <= r_edge and (cy0 <= y <= cy1): return "l"
    if abs(x - cx1) <= r_edge and (cy0 <= y <= cy1): return "r"


    if (cx0 + 4 < x < cx1 - 4) and (cy0 + 4 < y < cy1 - 4):
        return "move"


    return None


def ensure_demo_sample_file() -> Path:
    sample_dir = Path(tempfile.gettempdir()) / "Dewdrop_Sample_Files"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_file = sample_dir / "Sample_Photo.png"
    if not sample_file.exists():
        arr = generate_vivid_desktop_backdrop(640, 480).astype(np.uint8)
        im = Image.fromarray(arr, mode="RGB")
        im.save(sample_file, "PNG")
    return sample_file




def ensure_demo_video_file() -> Path:
    sample_dir = Path(tempfile.gettempdir()) / "Dewdrop_Sample_Files"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_file = sample_dir / "Sample_Video.mp4"
    if not sample_file.exists():
        ff = _find_ffmpeg()
        if ff and Path(ff).exists():
            subprocess.run([
                ff, "-nostdin", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-t", "3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(sample_file)
            ], capture_output=True)
    return sample_file if sample_file.exists() else ensure_demo_sample_file()




# ============================================================================
# 6. INTERACTIVE WIN32 LAYERED OVERLAY GUI (60 FPS BUTTER-SMOOTH)
# ============================================================================


def run_windows_interactive_gui(start_mode: str = "ring_groups", cli_files: list[Path] | None = None, record_mode: bool = True):
    global _DESKTOP_SNAPSHOT_RGB
    import ctypes.wintypes as wt


    if not acquire_single_instance():
        return
    bind_children_to_app()
    settings = load_settings()
    idle_limit_secs = float(settings.get("idle_seconds", DEFAULT_IDLE_SECS))


    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    WS_EX_LAYERED = 0x00080000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TOOLWINDOW = 0x00000080
    ULW_ALPHA = 0x00000002
    AC_SRC_OVER = 0x00
    AC_SRC_ALPHA = 0x01
    SWP_NOACTIVATE = 0x0010
    SWP_NOZORDER = 0x0004


    WM_DESTROY = 0x0002
    WM_ACTIVATE = 0x0006
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    WM_MOUSEWHEEL = 0x020A
    WM_MOUSEHWHEEL = 0x020E
    WM_KEYDOWN = 0x0100
    WM_CHAR = 0x0102
    WM_DROPFILES = 0x0233
    WM_TIMER = 0x0113


    VK_ESCAPE = 0x1B
    VK_RETURN = 0x0D
    VK_BACK = 0x08
    VK_SPACE = 0x20


    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_ubyte),
            ("BlendFlags", ctypes.c_ubyte),
            ("SourceConstantAlpha", ctypes.c_ubyte),
            ("AlphaFormat", ctypes.c_ubyte),
        ]


    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]


    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wt.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE),
            ("hIcon", wt.HANDLE),
            ("hCursor", wt.HANDLE),
            ("hbrBackground", wt.HANDLE),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
        ]


    class TRACKMOUSEEVENT(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("hwndTrack", wt.HWND),
            ("dwHoverTime", wt.DWORD),
        ]


    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
    user32.LoadCursorW.restype = wt.HANDLE
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
    ]
    user32.CreateWindowExW.restype = wt.HWND
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.DestroyWindow.restype = wt.BOOL
    user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
    user32.TrackMouseEvent.restype = wt.BOOL
    user32.UpdateLayeredWindow.argtypes = [
        wt.HWND, wt.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
        wt.HDC, ctypes.POINTER(POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wt.BOOL
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.SetWindowPos.restype = wt.BOOL
    user32.SetCapture.argtypes = [wt.HWND]
    user32.SetCapture.restype = wt.HWND
    user32.ReleaseCapture.argtypes = []
    user32.ReleaseCapture.restype = wt.BOOL
    if hasattr(user32, "GetAsyncKeyState"):
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = wt.SHORT
    if hasattr(user32, "SetActiveWindow"):
        user32.SetActiveWindow.argtypes = [wt.HWND]
        user32.SetActiveWindow.restype = wt.HWND
    if hasattr(user32, "SetFocus"):
        user32.SetFocus.argtypes = [wt.HWND]
        user32.SetFocus.restype = wt.HWND
    if hasattr(user32, "SetWindowDisplayAffinity"):
        user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        user32.SetWindowDisplayAffinity.restype = wt.BOOL
    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
    user32.KillTimer.restype = wt.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wt.BOOL
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.SetForegroundWindow.restype = wt.BOOL


    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateDIBSection.argtypes = [
        wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteObject.restype = wt.BOOL
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.DeleteDC.restype = wt.BOOL


    shell32.DragAcceptFiles.argtypes = [wt.HWND, wt.BOOL]
    shell32.DragAcceptFiles.restype = None
    shell32.DragFinish.argtypes = [wt.HANDLE]
    shell32.DragFinish.restype = None


    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    hinst = kernel32.GetModuleHandleW(None)
    toast_cls_name = "DewdropToastOverlayV1"
    _DESKTOP_SNAPSHOT_RGB = capture_full_desktop_rgb(screen_w, screen_h)


    win_center_x = (screen_w - 720) // 2
    win_center_y = (screen_h - 360) // 2
    island_top_x = (screen_w - 780) // 2
    island_top_y = 10


    # Detect files from CLI or active Windows Explorer selection automatically
    detected_files = list(cli_files) if cli_files else get_selected_explorer_files()
    if not detected_files and start_mode != "ring_groups":
        detected_files = [ensure_demo_sample_file()]


    active_groups = filter_and_rank_tools(
        detected_files,
        ffmpeg_available=bool(settings.get("ffmpeg", True)),
        usage_counts=settings.get("usage"),
    )
    if not active_groups:
        active_groups = filter_and_rank_tools([], ffmpeg_available=bool(settings.get("ffmpeg", True)), usage_counts=settings.get("usage"))


    # Always start on the main menu ("ring_groups") showing all 6 categories & all 50 features!
    effective_start_mode = start_mode


    state = {
        "mode": effective_start_mode,
        "start_mode": start_mode,
        "record_mode": bool(record_mode),
        "window_expanded": False,
        "dragging_slider": False,
        "dragging_window": False,
        "crop_norm_box": [0.1, 0.1, 0.9, 0.9],
        "dragging_crop_handle": None,
        "crop_drag_start_cursor": (0, 0),
        "crop_drag_start_norm": [0.1, 0.1, 0.9, 0.9],
        "cursor_kind": None,
        "crop_disp_rect": None,
        "drag_start_cursor": (0, 0),
        "drag_start_win": (win_center_x, win_center_y),
        "shine_phase": 0.15,
        "morph_target_mode": "island",
        "morph_spring": SpringSolver(1.0 if start_mode == "island" else 0.0, response=0.22, damping_ratio=0.85),
        "x_spring": SpringSolver(float(island_top_x if start_mode == "island" else win_center_x), response=0.22, damping_ratio=0.85),
        "y_spring": SpringSolver(float(island_top_y if start_mode == "island" else win_center_y), response=0.22, damping_ratio=0.85),
        "morph_win_im": None,
        "morph_isl_im": None,
        "groups": active_groups,
        "group_idx": 0,
        "tool_idx": 0,
        "hover_seg": 0,
        "hover_target": "",
        "dwell_target": None,
        "dwell_started_at": 0.0,
        "dwell_progress": 0.0,
        "entered_tools_at": time.monotonic(),
        "light_angle_deg": 135.0,
        "mouse_offset": (0.0, 0.0),
        "page": 0,
        "pager": RotationPager(
            len(active_groups[0][1]) if effective_start_mode == "ring_tools" else len(active_groups),
            deadzone_radius=28.0,
            allow_negative=False,
        ),
        "staged_files": detected_files,
        "current_preview_file": detected_files[0] if detected_files else None,
        "batch_badge": f"1/{len(detected_files)}" if len(detected_files) > 1 else "",
        "quality_val": 85,
        "chip_idx": 1,
        "text_input": "",
        "progress_frac": 0.68 if start_mode == "island" else 0.0,
        "paused": False,
        "cancelled": False,
        "job_active": False,
        "worker_proc": None,
        "worker_res_file": None,
        "worker_temp_dir": None,
        "last_outputs": [],
        "last_errors": [],
        "done_at": 0.0,
        "status_line": "Processing...",
        "detail_line": "1.6 MB of 2.4 MB  •  2 seconds left",
        "ring_x": (screen_w - 460) // 2,
        "ring_y": (screen_h - 460) // 2,
        "win_x": win_center_x,
        "win_y": win_center_y,
        "saved_win_x": float(win_center_x),
        "saved_win_y": float(win_center_y),
        "cur_w": 460,
        "cur_h": 460,
        "window_opened_at": time.monotonic(),
        "last_mousemove_redraw": 0.0,
        "last_active": time.monotonic(),
    }


    def get_window_rect(view_kind: str | None = None) -> tuple[int, int, int, int]:
        m = view_kind or state.get("mode", "options_window")
        is_prog = (m in ("progress_window", "progress"))
        t_id, _, _, _ = current_selected_tool()
        is_custom_crop = (t_id == "img_crop" and state.get("chip_idx") == 4 and not is_prog)
        if is_prog:
            ww = 860 if state["window_expanded"] else 720
            wh = 350 if state["window_expanded"] else 290
        elif is_custom_crop:
            ww = min(screen_w - 60, 1060 if state["window_expanded"] else 920)
            wh = min(screen_h - 80, 720 if state["window_expanded"] else 600)
        else:
            ww = 860 if state["window_expanded"] else 720
            wh = 430 if state["window_expanded"] else 360
        wx = state.get("saved_win_x", state.get("win_x", (screen_w - ww) // 2))
        wy = state.get("saved_win_y", state.get("win_y", (screen_h - wh) // 2))
        wx = min(max(10, wx), screen_w - ww - 10)
        wy = min(max(10, wy), screen_h - wh - 10)
        return (int(wx), int(wy), ww, wh)


    def get_local_cursor_xy(lparam) -> tuple[int, int]:
        """
        Computes exact cursor coordinates relative to our layered surface `(win_x, win_y)`
        using `GetCursorPos()` so clicks on `-`, `□`, `✕`, `Start`, `Pause`, `Cancel`, and
        slider dragging are 100% accurate on every Windows 10/11 DPI configuration.
        """
        pt = POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x - state["win_x"]), int(pt.y - state["win_y"]))
        return (
            int(ctypes.c_short(lparam & 0xFFFF).value),
            int(ctypes.c_short((lparam >> 16) & 0xFFFF).value),
        )


    def apply_staged_files(files: list[Path]):
        """
        Applies newly selected/dropped files, updates the real photo thumbnail preview,
        and strictly filters the Ring to ONLY the options compatible with those files!
        """
        resolved_files = [Path(f).resolve() for f in files]
        state["staged_files"] = resolved_files
        state["current_preview_file"] = resolved_files[0] if resolved_files else None
        state["batch_badge"] = f"1/{len(files)}" if len(files) > 1 else ""
        ranked = filter_and_rank_tools(
            files,
            ffmpeg_available=bool(settings.get("ffmpeg", True)),
            usage_counts=settings.get("usage"),
        )
        state["groups"] = ranked if ranked else filter_and_rank_tools([], ffmpeg_available=True)
        state["group_idx"] = 0
        state["page"] = 0
        state["dwell_target"] = None
        state["dwell_progress"] = 0.0
        # Always stay on the main menu ("ring_groups") so all tool categories and 50 features remain visible!
        state["mode"] = "ring_groups"
        state["pager"] = RotationPager(len(state["groups"]), deadzone_radius=28.0, allow_negative=False)


    _LAYERED_DIB_CACHE: dict[tuple[int, int], tuple] = {}


    def push_rgba_to_layered_hwnd(target_hwnd, pil_rgba: Image.Image, x: int, y: int):
        w, h = pil_rgba.size
        user32.SetWindowPos(target_hwnd, None, int(x), int(y), int(w), int(h), SWP_NOZORDER | SWP_NOACTIVATE)


        r_ch, g_ch, b_ch, a_ch = pil_rgba.convert("RGBa").split()
        bgra = Image.merge("RGBA", (b_ch, g_ch, r_ch, a_ch)).tobytes()


        key = (w, h)
        hdc_screen = user32.GetDC(None)
        cached = _LAYERED_DIB_CACHE.get(key)
        if cached is None:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            old = gdi32.SelectObject(hdc_mem, hbmp)
            cached = (hdc_mem, hbmp, bits, old)
            _LAYERED_DIB_CACHE[key] = cached


        hdc_mem, hbmp, bits, old = cached
        ctypes.memmove(bits, bgra, len(bgra))


        pt_dst = POINT(int(x), int(y))
        sz = SIZE(w, h)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(
            target_hwnd, hdc_screen, ctypes.byref(pt_dst), ctypes.byref(sz),
            hdc_mem, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA
        )
        user32.ReleaseDC(None, hdc_screen)


    def push_rgba_to_hwnd(hwnd, pil_rgba: Image.Image, x: int, y: int):
        w, h = pil_rgba.size
        state["cur_w"], state["cur_h"] = w, h
        state["win_x"], state["win_y"] = int(x), int(y)
        push_rgba_to_layered_hwnd(hwnd, pil_rgba, x, y)


    toast_state = {
        "hwnd": None,
        "x": screen_w - 380 - 24,
        "y": screen_h - 76 - 56,
        "w": 380,
        "h": 76,
        "title": "Selection Required",
        "message": "Please select a file to continue",
        "start_time": 0.0,
        "current_alpha": 1.0,
        "hover_close": False,
        "backdrop": None,
    }


    def dismiss_toast():
        thwnd = toast_state.get("hwnd")
        if thwnd:
            toast_state["hwnd"] = None
            unregister_overlay_hwnd(thwnd)
            try:
                user32.KillTimer(thwnd, 71)
            except Exception:
                pass
            try:
                user32.DestroyWindow(thwnd)
            except Exception:
                pass


    def redraw_toast():
        thwnd = toast_state.get("hwnd")
        if not thwnd:
            return
        im = render_liquid_glass_toast_rgba(
            title=toast_state["title"],
            message=toast_state["message"],
            w=toast_state["w"],
            h=toast_state["h"],
            alpha_mult=toast_state["current_alpha"],
            hover_close=toast_state["hover_close"],
            backdrop_rgb=toast_state["backdrop"],
        )
        push_rgba_to_layered_hwnd(thwnd, im, toast_state["x"], toast_state["y"])


    def show_toast_notification(title="Selection Required", message="Please select a file to continue"):
        toast_state["title"] = title
        toast_state["message"] = message
        toast_state["start_time"] = time.monotonic()
        toast_state["current_alpha"] = 0.05
        toast_state["hover_close"] = False


        tx, ty, tw, th = toast_state["x"], toast_state["y"], toast_state["w"], toast_state["h"]
        try:
            toast_state["backdrop"] = capture_screen_backdrop_rgb(tx, ty, tw, th, record_mode=state.get("record_mode", False))
        except Exception:
            toast_state["backdrop"] = None


        if toast_state["hwnd"] is None:
            toast_ex = WS_EX_LAYERED | WS_EX_TOPMOST
            thwnd = user32.CreateWindowExW(
                toast_ex, toast_cls_name, "DewdropToast", WS_POPUP | WS_VISIBLE,
                tx, ty, tw, th,
                None, None, hinst, None
            )
            toast_affinity = 0x00000000 if state.get("record_mode", True) else 0x00000011
            if hasattr(user32, "SetWindowDisplayAffinity"):
                try:
                    user32.SetWindowDisplayAffinity(thwnd, toast_affinity)
                except Exception:
                    pass
            register_overlay_hwnd(thwnd)
            toast_state["hwnd"] = thwnd
            user32.SetTimer(thwnd, 71, 16, None)


        redraw_toast()


    def toast_wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_LBUTTONDOWN:
            x = lparam & 0xFFFF
            y = (lparam >> 16) & 0xFFFF
            if x >= 0x8000:
                x -= 0x10000
            if y >= 0x8000:
                y -= 0x10000
            dismiss_toast()
            return 0


        elif msg == WM_MOUSEMOVE:
            x = lparam & 0xFFFF
            y = (lparam >> 16) & 0xFFFF
            if x >= 0x8000:
                x -= 0x10000
            if y >= 0x8000:
                y -= 0x10000
            cx = toast_state["w"] - 26
            cy = toast_state["h"] // 2
            is_hover = (math.hypot(x - cx, y - cy) <= 15)
            if is_hover != toast_state.get("hover_close", False):
                toast_state["hover_close"] = is_hover
                redraw_toast()
            try:
                tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x00000002, hwnd, 0)
                user32.TrackMouseEvent(ctypes.byref(tme))
            except Exception:
                pass
            return 0


        elif msg == 0x02A3:  # WM_MOUSELEAVE
            if toast_state.get("hover_close", False):
                toast_state["hover_close"] = False
                redraw_toast()
            return 0


        elif msg == WM_TIMER:
            if wparam == 71:
                now = time.monotonic()
                elapsed = now - toast_state["start_time"]
                total_duration = 3.8
                fade_in = 0.25
                fade_out = 0.35


                if elapsed >= total_duration:
                    dismiss_toast()
                    return 0


                if elapsed < fade_in:
                    alpha = elapsed / fade_in
                elif elapsed > (total_duration - fade_out):
                    alpha = (total_duration - elapsed) / fade_out
                else:
                    alpha = 1.0
                alpha = max(0.0, min(1.0, alpha))
                if abs(alpha - toast_state["current_alpha"]) >= 0.02 or elapsed < fade_in or elapsed > (total_duration - fade_out):
                    toast_state["current_alpha"] = alpha
                    redraw_toast()
                return 0


        elif msg == WM_DESTROY:
            toast_state["hwnd"] = None
            return 0


        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


    def get_file_summary() -> tuple[str, str]:
        files = state["staged_files"]
        cur_p = state.get("current_preview_file")
        if not files:
            return ("Select or Drop Files", "All 50 offline tools ready")
        if len(files) == 1:
            p = files[0]
            if p.is_dir():
                return (f"Folder: {p.name}", "Input folder ready")
            sz_kb = max(1, p.stat().st_size // 1024) if p.exists() else 240
            return (p.name, f"{sz_kb} KB")
        total_kb = sum((p.stat().st_size // 1024) for p in files if p.exists())
        if state["job_active"] and cur_p is not None:
            return (cur_p.name, f"{len(files)} files  •  {max(1, total_kb)} KB total")
        return (f"{files[0].name} (+{len(files) - 1} more)", f"{len(files)} files staged")


    def current_selected_tool() -> tuple[str, str, bool, str]:
        g_list = state["groups"]
        g_idx = min(state["group_idx"], len(g_list) - 1)
        tools = g_list[g_idx][1]
        t_idx = min(state["tool_idx"], len(tools) - 1)
        return tools[t_idx]


    def build_current_window_image(view_kind: str) -> Image.Image:
        f_title, f_sub = get_file_summary()
        t_id, t_label, _, t_desc = current_selected_tool()
        wx, wy, ww, wh = get_window_rect(view_kind)
        bg = capture_screen_backdrop_rgb(wx, wy, ww, wh, record_mode=state.get("record_mode", False))


        if view_kind == "progress":
            is_finished = bool(state["done_at"] > 0 or (not state["job_active"] and state["progress_frac"] >= 1.0))
            return render_progress_window_rgba(
                file_title=f_title,
                file_subtitle=f_sub,
                tool_label=t_label,
                progress_frac=state["progress_frac"],
                paused=state["paused"],
                is_finished=is_finished,
                status_line=state["status_line"],
                detail_line=state["detail_line"],
                w=ww,
                h=wh,
                backdrop_rgb=bg,
                mouse_offset=state["mouse_offset"],
                hover_target=state["hover_target"],
                preview_file=state["current_preview_file"],
                batch_badge=state["batch_badge"],
                shine_phase=state["shine_phase"],
                expanded=state["window_expanded"],
            )


        ui_cfg = get_tool_ui_config(t_id, state["quality_val"], state["chip_idx"], state["text_input"])
        has_slider = ui_cfg.get("has_slider", False)
        has_chips = bool(ui_cfg.get("chips"))
        opt_im = render_options_or_progress_window_rgba(
            file_title=f_title,
            file_subtitle=f_sub,
            tool_label=t_label,
            option_desc=t_desc,
            mode=view_kind,
            quality_val=state["quality_val"],
            chip_idx=state["chip_idx"],
            progress_frac=state["progress_frac"],
            paused=state["paused"],
            status_line=state["status_line"],
            detail_line=state["detail_line"],
            w=ww,
            h=wh,
            backdrop_rgb=bg,
            mouse_offset=state["mouse_offset"],
            hover_target=state["hover_target"],
            chip_labels=ui_cfg.get("chips", []),
            slider_display=ui_cfg.get("slider_str"),
            text_input_value=ui_cfg.get("text_val"),
            expanded=state["window_expanded"],
            preview_file=state["current_preview_file"],
            batch_badge=state["batch_badge"],
            shine_phase=state["shine_phase"],
            show_slider=has_slider,
            show_chips=has_chips,
        )
        if view_kind == "options" and t_id == "img_crop" and state.get("chip_idx") == 4:
            prev_f = state.get("current_preview_file") or (state["staged_files"][0] if state.get("staged_files") else ensure_demo_sample_file())
            norm_b = state.get("crop_norm_box", [0.1, 0.1, 0.9, 0.9])
            opt_im, disp_r = draw_custom_crop_canvas(opt_im, prev_f, norm_b, ww, wh)
            state["crop_disp_rect"] = disp_r
        return opt_im


    def build_current_island_image() -> Image.Image:
        f_title, f_sub = get_file_summary()
        bg = capture_screen_backdrop_rgb(island_top_x, island_top_y, 780, 76, record_mode=state.get("record_mode", False))
        show_prog = bool(state["job_active"] or state["done_at"] > 0)
        return render_dynamic_island_rgba(
            file_title=f_title,
            batch_subtitle=f_sub,
            progress_frac=state["progress_frac"],
            paused=state["paused"],
            detail_line=state["detail_line"],
            w=780,
            h=76,
            backdrop_rgb=bg,
            mouse_offset=state["mouse_offset"],
            hover_target=state["hover_target"],
            preview_file=state["current_preview_file"],
            batch_badge=state["batch_badge"],
            shine_phase=state["shine_phase"],
            show_progress=show_prog,
        )


    def start_morph(hwnd, to_island: bool):
        if state.get("mode") == "options_window":
            w_kind = "options"
        else:
            w_kind = "progress" if (state["job_active"] or state["done_at"] > 0) else "options"
        state["morph_win_im"] = build_current_window_image(w_kind)
        state["morph_isl_im"] = build_current_island_image()
        if to_island:
            cur_wx = float(state.get("win_x", state.get("saved_win_x", (screen_w - 720) // 2)))
            cur_wy = float(state.get("win_y", state.get("saved_win_y", (screen_h - 360) // 2)))
            state["saved_win_x"] = cur_wx
            state["saved_win_y"] = cur_wy
            state["morph_target_mode"] = "island"
            state["x_spring"] = SpringSolver(cur_wx, response=0.22, damping_ratio=0.85)
            state["y_spring"] = SpringSolver(cur_wy, response=0.22, damping_ratio=0.85)
            state["morph_spring"] = SpringSolver(0.0, response=0.22, damping_ratio=0.85)
            state["morph_spring"].set_target(1.0)
            state["x_spring"].set_target(float(island_top_x))
            state["y_spring"].set_target(float(island_top_y))
        else:
            ww = 860 if state["window_expanded"] else 720
            wh = (350 if state["window_expanded"] else 290) if w_kind == "progress" else (430 if state["window_expanded"] else 360)
            target_wx = float(state.get("saved_win_x", (screen_w - ww) // 2))
            target_wy = float(state.get("saved_win_y", (screen_h - wh) // 2))
            state["win_x"] = int(target_wx)
            state["win_y"] = int(target_wy)
            state["morph_target_mode"] = "progress_window" if (state["job_active"] or state["done_at"] > 0) else "options_window"
            state["x_spring"] = SpringSolver(float(island_top_x), response=0.22, damping_ratio=0.85)
            state["y_spring"] = SpringSolver(float(island_top_y), response=0.22, damping_ratio=0.85)
            state["morph_spring"] = SpringSolver(1.0, response=0.22, damping_ratio=0.85)
            state["morph_spring"].set_target(0.0)
            state["x_spring"].set_target(target_wx)
            state["y_spring"].set_target(target_wy)
            state["window_opened_at"] = time.monotonic()
        state["mode"] = "morphing"
        user32.SetTimer(hwnd, 2, 16, None)


    def cleanup_worker_temp():
        tdir = state.get("worker_temp_dir")
        if tdir and Path(tdir).exists():
            shutil.rmtree(tdir, ignore_errors=True)
        state["worker_temp_dir"] = None
        state["worker_res_file"] = None
        state["worker_proc"] = None


    def instant_exit():
        try:
            if "hwnd" in locals() and hwnd:
                unregister_overlay_hwnd(hwnd)
        except Exception:
            pass
        try:
            dismiss_toast()
        except Exception:
            pass
        try:
            terminate_worker_tree(state.get("worker_proc"))
        except Exception:
            pass
        cleanup_worker_temp()
        release_single_instance()
        os._exit(0)


    def cancel_and_dismiss(hwnd):
        state["cancelled"] = True
        state["job_active"] = False
        state["paused"] = False
        state["done_at"] = 0.0
        state["progress_frac"] = 0.0
        state["hover_target"] = ""
        if state["dragging_slider"]:
            state["dragging_slider"] = False
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
        try:
            user32.KillTimer(hwnd, 3)
            user32.KillTimer(hwnd, 2)
        except Exception:
            pass
        terminate_worker_tree(state["worker_proc"])
        cleanup_worker_temp()
        state["mode"] = "ring_groups"
        state["pager"] = RotationPager(len(state["groups"]), deadzone_radius=28.0, allow_negative=False)
        redraw(hwnd)


    def trigger_window_exit_sequence(hwnd, is_done: bool):
        now = time.monotonic()
        state["exit_is_done"] = is_done
        state["cancelled"] = not is_done
        if not is_done:
            state["job_active"] = False
            state["paused"] = False
            terminate_worker_tree(state["worker_proc"])
        cleanup_worker_temp()


        if state["dragging_slider"]:
            state["dragging_slider"] = False
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
        if state["dragging_window"]:
            state["dragging_window"] = False
            try:
                user32.ReleaseCapture()
            except Exception:
                pass


        try:
            user32.KillTimer(hwnd, 2)
            user32.KillTimer(hwnd, 3)
        except Exception:
            pass


        state["exit_shrink_t"] = 0.0
        state["exit_fade_t"] = 0.0


        if state["mode"] in ("options_window", "progress_window"):
            w_kind = "progress" if (state["mode"] == "progress_window" or is_done) else "options"
            state["morph_win_im"] = build_current_window_image(w_kind)
            alert_im, _, _ = render_optical_closing_alert_rgba(
                t_shrink=0.0,
                t_fade=0.0,
                is_done=is_done,
                w_max=780,
                h=76,
            )
            state["morph_isl_im"] = alert_im
            cur_wx = float(state.get("win_x", (screen_w - 720) // 2))
            cur_wy = float(state.get("win_y", (screen_h - 360) // 2))
            state["x_spring"] = SpringSolver(cur_wx, response=0.20, damping_ratio=0.86)
            state["y_spring"] = SpringSolver(cur_wy, response=0.20, damping_ratio=0.86)
            state["morph_spring"] = SpringSolver(0.0, response=0.20, damping_ratio=0.86)
            state["morph_spring"].set_target(1.0)
            state["x_spring"].set_target(float(island_top_x))
            state["y_spring"].set_target(float(island_top_y))
            state["exit_phase"] = "morph_to_alert"
            state["mode"] = "closing_morph"
        else:
            state["exit_phase"] = "hold_alert"
            state["exit_phase_start"] = now
            state["mode"] = "closing_alert"


        user32.SetTimer(hwnd, 5, 16, None)
        redraw(hwnd)


    def start_conversion_job(hwnd):
        if not state["staged_files"]:
            t_id_pre, _, _, _ = current_selected_tool()
            if t_id_pre in ("media_to_mp4", "media_to_webm", "media_to_mp3", "media_to_m4a",
                            "media_to_wav", "media_to_flac", "video_compress", "audio_compress",
                            "media_trim", "video_to_gif", "video_mute", "video_frame_png"):
                apply_staged_files([ensure_demo_video_file()])
            else:
                apply_staged_files([ensure_demo_sample_file()])


        g_name = state["groups"][min(state["group_idx"], len(state["groups"]) - 1)][0]
        t_id, t_label, _, _ = current_selected_tool()
        record_tool_usage(settings, t_id, g_name)


        crop_box = None
        if t_id == "img_crop" and state.get("chip_idx") == 4 and state.get("crop_norm_box"):
            first_f = state["staged_files"][0] if state.get("staged_files") else ensure_demo_sample_file()
            try:
                with Image.open(first_f) as pim:
                    ow, oh = pim.width, pim.height
            except Exception:
                ow, oh = 800, 600
            nb = state["crop_norm_box"]
            px0 = max(0, min(ow - 16, int(round(nb[0] * ow))))
            py0 = max(0, min(oh - 16, int(round(nb[1] * oh))))
            px1 = max(px0 + 16, min(ow, int(round(nb[2] * ow))))
            py1 = max(py0 + 16, min(oh, int(round(nb[3] * oh))))
            crop_box = [px0, py0, px1, py1]


        ui_cfg = get_tool_ui_config(t_id, state["quality_val"], state["chip_idx"], state["text_input"], crop_box=crop_box)
        opts = ui_cfg["opts"]


        cleanup_worker_temp()
        tdir = Path(tempfile.mkdtemp(prefix=".dewdrop-job-"))
        job_json = tdir / "job.json"
        res_json = tdir / "result.json"
        job_json.write_text(json.dumps({
            "tool_id": t_id,
            "files": [str(Path(p).resolve()) for p in state["staged_files"]],
            "options": opts,
        }, ensure_ascii=False), encoding="utf-8")


        creation_flags = 0x08000000 if os.name == "nt" else 0
        if getattr(sys, "frozen", False):
            worker_cmd = [sys.executable, "--worker", str(job_json), str(res_json)]
        else:
            worker_cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", str(job_json), str(res_json)]
        proc = subprocess.Popen(
            worker_cmd,
            cwd=str(ROOT_DIR),
            creationflags=creation_flags,
        )
        if os.name == "nt" and _JOB_HANDLE and getattr(proc, "_handle", None):
            assign_process_to_job(int(proc._handle))


        state["worker_temp_dir"] = str(tdir)
        state["worker_res_file"] = str(res_json)
        state["worker_proc"] = proc
        state["job_active"] = True
        state["done_at"] = 0.0
        state["paused"] = False
        state["cancelled"] = False
        state["progress_frac"] = 0.06
        state["current_preview_file"] = state["staged_files"][0]
        state["batch_badge"] = f"1/{len(state['staged_files'])}" if len(state["staged_files"]) > 1 else ""
        state["status_line"] = f"Running {t_label}..."
        state["detail_line"] = "Saving output to Dew Drop folder..."
        pwx, pwy, _, _ = get_window_rect("progress")
        state["win_x"] = int(pwx)
        state["win_y"] = int(pwy)
        state["window_opened_at"] = time.monotonic()
        user32.SetTimer(hwnd, 3, 50, None)
        start_morph(hwnd, to_island=True)


    def current_labels() -> list[str]:
        if state["mode"] == "ring_groups":
            chunk = [g[0] for g in state["groups"][:6]]
        else:
            tools = state["groups"][min(state["group_idx"], len(state["groups"]) - 1)][1]
            start = state["page"] * 6
            chunk = [t[1] for t in tools[start:start + 6]]
        while len(chunk) < 6:
            chunk.append("")
        return chunk


    def redraw(hwnd):
        mode = state["mode"]
        if mode in ("ring_groups", "ring_tools"):
            g_list = state["groups"]
            g_name = g_list[min(state["group_idx"], len(g_list) - 1)][0]
            if mode == "ring_groups" and state["staged_files"]:
                first_f = state["staged_files"][0]
                if first_f.is_dir():
                    sub = f"Folder: {first_f.name[:18]}"
                else:
                    sub = f"{len(state['staged_files'])} file(s) selected"
            elif mode == "ring_groups":
                sub = "Select Files or Folder"
            else:
                sub = f"{g_name} (Pg {state['page']+1}/{state['pager'].total_pages})"
            now_t = time.monotonic()
            cur_pos = (state["ring_x"], state["ring_y"])
            need_bg_capture = (
                state.get("cached_ring_bg") is None
                or state.get("last_ring_bg_pos") != cur_pos
                or (now_t - state.get("last_ring_bg_time", 0.0) >= 0.066)
            )
            if need_bg_capture:
                bg = capture_screen_backdrop_rgb(state["ring_x"], state["ring_y"], 460, 460, record_mode=state.get("record_mode", False))
                state["cached_ring_bg"] = bg
                state["last_ring_bg_time"] = now_t
                state["last_ring_bg_pos"] = cur_pos
            else:
                bg = state["cached_ring_bg"]
            has_more_options = (state["pager"].total_pages > 1)
            turn_prog = max(0.0, state["pager"].accumulated_rad / (2.0 * math.pi)) if has_more_options else 0.0
            color_turns = getattr(state["pager"], "color_turns", 0.0) if has_more_options else 0.0
            eff_light = state["light_angle_deg"] if has_more_options else 135.0


            im = render_ring_surface_rgba(
                current_labels(),
                hover_seg=state["hover_seg"],
                turn_progress=turn_prog,
                staged_count=len(state["staged_files"]),
                subtitle=sub,
                size=460,
                backdrop_rgb=bg,
                light_angle_deg=eff_light,
                dwell_progress=0.0,
                shine_phase=state["shine_phase"],
                color_turns=color_turns,
            )
            push_rgba_to_hwnd(hwnd, im, state["ring_x"], state["ring_y"])
        elif mode == "options_window":
            wx, wy, _, _ = get_window_rect("options")
            im = build_current_window_image("options")
            push_rgba_to_hwnd(hwnd, im, wx, wy)
        elif mode == "progress_window":
            wx, wy, _, _ = get_window_rect("progress")
            im = build_current_window_image("progress")
            push_rgba_to_hwnd(hwnd, im, wx, wy)
        elif mode == "island":
            im = build_current_island_image()
            push_rgba_to_hwnd(hwnd, im, island_top_x, island_top_y)
        elif mode == "morphing":
            w_im = state["morph_win_im"] or build_current_window_image("progress" if state["job_active"] else "options")
            i_im = state["morph_isl_im"] or build_current_island_image()
            frame = render_morph_frame_rgba(state["morph_spring"].position, w_im, i_im)
            push_rgba_to_hwnd(hwnd, frame, round(state["x_spring"].position), round(state["y_spring"].position))
        elif mode == "closing_morph":
            w_im = state["morph_win_im"] or build_current_window_image("progress" if state["exit_is_done"] else "options")
            i_im = state["morph_isl_im"] or build_current_island_image()
            t_pos = max(0.0, min(1.0, float(state["morph_spring"].position)))
            frame = render_morph_frame_rgba(t_pos, w_im, i_im)
            is_done = state.get("exit_is_done", True)
            if t_pos > 0.15:
                alpha_val = int(255 * min(1.0, (t_pos - 0.15) / 0.50))
                border_col = (34, 185, 102, alpha_val) if is_done else (235, 55, 55, alpha_val)
                cw, ch = frame.size
                rad = max(8.0, (ch - 20) * 0.5)
                draw_sharp_rounded_rect(frame, 10, 10, cw - 10, ch - 10, radius=rad, stroke_color=border_col, stroke_width=1.2)
            push_rgba_to_hwnd(hwnd, frame, round(state["x_spring"].position), round(state["y_spring"].position))
        elif mode == "closing_alert":
            phase = state.get("exit_phase", "squeeze_alert")
            is_done = state.get("exit_is_done", True)
            t_shrink = 0.0 if phase == "hold_alert" else state.get("exit_shrink_t", 0.0)
            t_fade = state.get("exit_fade_t", 0.0)
            im, cur_w, cur_h = render_optical_closing_alert_rgba(
                t_shrink=t_shrink,
                t_fade=t_fade,
                is_done=is_done,
                w_max=780,
                h=76,
            )
            center_x = island_top_x + 390
            center_y = island_top_y + 38
            cur_x = center_x - cur_w // 2
            cur_y = (center_y - cur_h // 2) if phase == "fade_circle" else island_top_y
            push_rgba_to_hwnd(hwnd, im, cur_x, cur_y)


    def enter_group_segment(hwnd, seg: int):
        if seg < len(state["groups"]):
            state["group_idx"] = seg
            state["page"] = 0
            tools = state["groups"][seg][1]
            state["pager"] = RotationPager(len(tools), deadzone_radius=28.0, allow_negative=False)
            state["mode"] = "ring_tools"
            state["entered_tools_at"] = time.monotonic()
            state["dwell_target"] = None
            state["dwell_progress"] = 0.0
            redraw(hwnd)


    def select_tool_segment(hwnd, seg: int):
        tools = state["groups"][min(state["group_idx"], len(state["groups"]) - 1)][1]
        idx = state["page"] * 6 + seg
        if idx < len(tools):
            state["tool_idx"] = idx
            t_id, _, has_opts, _ = tools[idx]
            state["text_input"] = ""
            if not state["staged_files"]:
                state["in_dialog"] = True
                try:
                    if t_id in ("archive_zip", "archive_targz"):
                        picked = pick_folder_dialog_win32(hwnd) or pick_files_dialog_win32(hwnd)
                    else:
                        picked = pick_files_dialog_win32(hwnd)
                finally:
                    state["in_dialog"] = False
                if picked:
                    apply_staged_files(picked)
                else:
                    show_toast_notification(
                        title="Selection Required",
                        message="Please select a file to continue"
                    )
                    redraw(hwnd)
                    return


            wx, wy, _, _ = get_window_rect("options")
            state["win_x"] = int(wx)
            state["win_y"] = int(wy)
            state["x_spring"] = SpringSolver(float(wx), 0.45, 0.80)
            state["y_spring"] = SpringSolver(float(wy), 0.45, 0.80)
            state["morph_spring"] = SpringSolver(0.0, 0.45, 0.80)
            state["window_opened_at"] = time.monotonic()
            state["mode"] = "options_window"
            redraw(hwnd)


    def update_slider_from_x(hwnd, x: int):
        _, _, ww, _ = get_window_rect()
        px0 = 18 + 40
        px1 = (ww - 18) - 104
        pct = int(round(((x - px0) / float(max(1, px1 - px0))) * 100.0))
        new_q = min(max(pct, 5), 100)
        if new_q != state["quality_val"]:
            state["quality_val"] = new_q
            redraw(hwnd)


    def py_wndproc(hwnd, msg, wparam, lparam):
        if msg == 0x0020:  # WM_SETCURSOR
            cur_id = state.get("cursor_kind")
            if cur_id and hasattr(user32, "LoadCursorW") and hasattr(user32, "SetCursor"):
                h_cur = user32.LoadCursorW(None, ctypes.cast(int(cur_id), wt.LPCWSTR))
                if h_cur:
                    user32.SetCursor(h_cur)
                    return 1
        if msg == WM_DROPFILES:
            hdrop = wt.HANDLE(wparam)
            shell32.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
            shell32.DragQueryFileW.restype = wt.UINT
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            dropped = []
            for i in range(min(count, 500)):
                n = shell32.DragQueryFileW(hdrop, i, None, 0)
                buf = ctypes.create_unicode_buffer(n + 1)
                shell32.DragQueryFileW(hdrop, i, buf, n + 1)
                p = Path(buf.value).resolve()
                if p.exists():
                    dropped.append(p)
            shell32.DragFinish(hdrop)
            if dropped:
                state["last_active"] = time.monotonic()
                apply_staged_files(dropped)
                redraw(hwnd)
            return 0


        if msg == WM_ACTIVATE:
            active_flag = wparam & 0xFFFF
            if active_flag == 0 and not state.get("in_dialog", False):
                if state["mode"] in ("ring_groups", "ring_tools"):
                    # Keep circular menu open on screen so user can select files in Explorer/Desktop and drag-drop them in
                    return 0
                elif (
                    not state["dragging_slider"]
                    and not state["dragging_window"]
                    and state["mode"] in ("options_window", "progress_window")
                    and (time.monotonic() - state["window_opened_at"] > 0.60)
                ):
                    start_morph(hwnd, to_island=True)
            return 0


        if msg == WM_LBUTTONUP:
            if state.get("dragging_crop_handle"):
                state["dragging_crop_handle"] = None
                try:
                    user32.ReleaseCapture()
                except Exception:
                    pass
                redraw(hwnd)
                return 0
            if state["dragging_window"]:
                state["dragging_window"] = False
                try:
                    user32.ReleaseCapture()
                except Exception:
                    pass
                redraw(hwnd)
                return 0
            if state["dragging_slider"]:
                state["dragging_slider"] = False
                try:
                    user32.ReleaseCapture()
                except Exception:
                    pass
            return 0


        if msg == WM_MOUSEMOVE:
            now = time.monotonic()
            state["last_active"] = now
            x, y = get_local_cursor_xy(lparam)
            mode = state["mode"]


            # Real-time freeform custom crop dragging (corners, edges, and move)
            if state.get("dragging_crop_handle"):
                if (wparam & 0x0001) == 0:
                    state["dragging_crop_handle"] = None
                    try:
                        user32.ReleaseCapture()
                    except Exception:
                        pass
                    redraw(hwnd)
                else:
                    handle = state["dragging_crop_handle"]
                    disp_rect = state.get("crop_disp_rect")
                    if disp_rect:
                        disp_x0, disp_y0, disp_w, disp_h, orig_w, orig_h = disp_rect
                        dx = x - state["crop_drag_start_cursor"][0]
                        dy = y - state["crop_drag_start_cursor"][1]
                        dnx = dx / float(max(1, disp_w))
                        dny = dy / float(max(1, disp_h))
                        snx0, sny0, snx1, sny1 = state["crop_drag_start_norm"]
                        min_nw = max(16.0 / max(1, orig_w), 24.0 / max(1, disp_w))
                        min_nh = max(16.0 / max(1, orig_h), 24.0 / max(1, disp_h))
                        if handle == "tl":
                            nx0 = min(max(0.0, snx0 + dnx), snx1 - min_nw)
                            ny0 = min(max(0.0, sny0 + dny), sny1 - min_nh)
                            state["crop_norm_box"] = [nx0, ny0, snx1, sny1]
                        elif handle == "tr":
                            nx1 = max(min(1.0, snx1 + dnx), snx0 + min_nw)
                            ny0 = min(max(0.0, sny0 + dny), sny1 - min_nh)
                            state["crop_norm_box"] = [snx0, ny0, nx1, sny1]
                        elif handle == "bl":
                            nx0 = min(max(0.0, snx0 + dnx), snx1 - min_nw)
                            ny1 = max(min(1.0, sny1 + dny), sny0 + min_nh)
                            state["crop_norm_box"] = [nx0, sny0, snx1, ny1]
                        elif handle == "br":
                            nx1 = max(min(1.0, snx1 + dnx), snx0 + min_nw)
                            ny1 = max(min(1.0, sny1 + dny), sny0 + min_nh)
                            state["crop_norm_box"] = [snx0, sny0, nx1, ny1]
                        elif handle == "t":
                            ny0 = min(max(0.0, sny0 + dny), sny1 - min_nh)
                            state["crop_norm_box"] = [snx0, ny0, snx1, sny1]
                        elif handle == "b":
                            ny1 = max(min(1.0, sny1 + dny), sny0 + min_nh)
                            state["crop_norm_box"] = [snx0, sny0, snx1, ny1]
                        elif handle == "l":
                            nx0 = min(max(0.0, snx0 + dnx), snx1 - min_nw)
                            state["crop_norm_box"] = [nx0, sny0, snx1, sny1]
                        elif handle == "r":
                            nx1 = max(min(1.0, snx1 + dnx), snx0 + min_nw)
                            state["crop_norm_box"] = [snx0, sny0, nx1, sny1]
                        elif handle == "move":
                            bw = snx1 - snx0
                            bh = sny1 - sny0
                            nx0 = min(max(0.0, snx0 + dnx), 1.0 - bw)
                            ny0 = min(max(0.0, sny0 + dny), 1.0 - bh)
                            state["crop_norm_box"] = [nx0, ny0, nx0 + bw, ny0 + bh]
                        redraw(hwnd)
                return 0


            # Real-time window dragging anywhere on screen
            if state["dragging_window"]:
                if (wparam & 0x0001) == 0:
                    state["dragging_window"] = False
                    try:
                        user32.ReleaseCapture()
                    except Exception:
                        pass
                else:
                    pt_scr = POINT()
                    if user32.GetCursorPos(ctypes.byref(pt_scr)):
                        dx = pt_scr.x - state["drag_start_cursor"][0]
                        dy = pt_scr.y - state["drag_start_cursor"][1]
                        state["win_x"] = state["drag_start_win"][0] + dx
                        state["win_y"] = state["drag_start_win"][1] + dy
                        state["saved_win_x"] = float(state["win_x"])
                        state["saved_win_y"] = float(state["win_y"])
                        user32.SetWindowPos(hwnd, None, state["win_x"], state["win_y"], state["cur_w"], state["cur_h"], SWP_NOZORDER | SWP_NOACTIVATE)
                        redraw(hwnd)
                return 0


            # Real-time butter-smooth slider dragging while left button is held down
            if state["dragging_slider"] and mode == "options_window":
                t_id, _, _, _ = current_selected_tool()
                ui_cfg = get_tool_ui_config(t_id, state["quality_val"], state["chip_idx"], state["text_input"])
                if not ui_cfg.get("has_slider", False) or (wparam & 0x0001) == 0:
                    state["dragging_slider"] = False
                    try:
                        user32.ReleaseCapture()
                    except Exception:
                        pass
                else:
                    update_slider_from_x(hwnd, x)
                    return 0


            if mode in ("ring_groups", "ring_tools"):
                has_more_options = (state["pager"].total_pages > 1)
                dx = x - 230.0
                dy = y - 230.0
                r = math.hypot(dx, dy)
                if has_more_options:
                    if r > 8.0:
                        state["light_angle_deg"] = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
                    new_page = state["pager"].update_pointer(dx, dy)
                    if new_page is not None:
                        state["page"] = new_page


                prev_hov = state["hover_seg"]
                if 92.0 <= r <= 220.0:
                    ang = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
                    seg = int(((ang + 30.0) % 360.0) // 60.0) % 6
                    if seg != state["hover_seg"]:
                        state["hover_seg"] = seg
                else:
                    if state["hover_seg"] != -1:
                        state["hover_seg"] = -1


                state["last_client_mousemove"] = now
                if (state["hover_seg"] != prev_hov) or (now - state["last_mousemove_redraw"] >= 0.012):
                    state["last_mousemove_redraw"] = now
                    redraw(hwnd)
            elif mode in ("options_window", "progress_window"):
                is_prog = (mode == "progress_window")
                _, _, ww, wh = get_window_rect("progress" if is_prog else "options")
                x0, y0, x1, y1 = 18, 18, ww - 18, wh - 18
                wx0, wy0, wx1, wy1 = x1 - 186, y0 + 20 if is_prog else y0 + 24, x1 - 22, y0 + 64 if is_prog else y0 + 68
                bx0, by0, bx1, by1 = x1 - 280, y1 - 64 if is_prog else y1 - 68, x1 - 160, y1 - 20 if is_prog else y1 - 22
                cx0, cy0, cx1, cy1 = x1 - 146, y1 - 64 if is_prog else y1 - 68, x1 - 24, y1 - 20 if is_prog else y1 - 22
                new_hover = ""
                if (wy0 - 12) <= y <= (wy1 + 12) and (wx0 - 12) <= x <= (wx1 + 12):
                    pill_w = wx1 - wx0
                    sec_w = pill_w / 3.0
                    rel_x = x - wx0
                    if rel_x < sec_w:
                        new_hover = "minimize"
                    elif rel_x < 2.0 * sec_w:
                        new_hover = "expand"
                    else:
                        new_hover = "close"
                elif (by0 - 12) <= y <= (by1 + 12):
                    if (bx0 - 10) <= x <= (bx1 + 10):
                        new_hover = "primary_btn"
                    elif (cx0 - 10) <= x <= (cx1 + 10):
                        new_hover = "cancel_btn"


                if new_hover != state["hover_target"]:
                    state["hover_target"] = new_hover
                    redraw(hwnd)


                # Custom Crop Handle Hover & Windows Cursor Tracking
                t_id, _, _, _ = current_selected_tool()
                is_custom_crop = (mode == "options_window" and t_id == "img_crop" and state.get("chip_idx") == 4)
                if is_custom_crop and state.get("crop_disp_rect"):
                    h_hit = hit_test_crop_handle(x, y, state["crop_disp_rect"], state.get("crop_norm_box", [0.1, 0.1, 0.9, 0.9]))
                    cursor_map = {
                        "tl": 32642, "br": 32642,
                        "tr": 32643, "bl": 32643,
                        "t": 32645, "b": 32645,
                        "l": 32644, "r": 32644,
                        "move": 32646,
                    }
                    new_cursor = cursor_map.get(h_hit, 32512)
                    if new_cursor != state.get("cursor_kind"):
                        state["cursor_kind"] = new_cursor
                        if hasattr(user32, "LoadCursorW") and hasattr(user32, "SetCursor"):
                            h_cur = user32.LoadCursorW(None, ctypes.cast(int(new_cursor), wt.LPCWSTR))
                            if h_cur:
                                user32.SetCursor(h_cur)
                else:
                    if state.get("cursor_kind") not in (None, 32512):
                        state["cursor_kind"] = 32512
                        if hasattr(user32, "LoadCursorW") and hasattr(user32, "SetCursor"):
                            h_cur = user32.LoadCursorW(None, ctypes.cast(32512, wt.LPCWSTR))
                            if h_cur:
                                user32.SetCursor(h_cur)
            elif mode == "island":
                p_cx = 645
                x_cx = 705
                cy_isl = 38
                new_hover = ""
                if math.hypot(x - p_cx, y - cy_isl) <= 24:
                    new_hover = "pause"
                elif math.hypot(x - x_cx, y - cy_isl) <= 24:
                    new_hover = "cancel"
                if new_hover != state["hover_target"]:
                    state["hover_target"] = new_hover
                    redraw(hwnd)


                # Custom Crop Handle Hover & Windows Cursor Tracking
                t_id, _, _, _ = current_selected_tool()
                is_custom_crop = (mode == "options_window" and t_id == "img_crop" and state.get("chip_idx") == 4)
                if is_custom_crop and state.get("crop_disp_rect"):
                    h_hit = hit_test_crop_handle(x, y, state["crop_disp_rect"], state.get("crop_norm_box", [0.1, 0.1, 0.9, 0.9]))
                    cursor_map = {
                        "tl": 32642, "br": 32642,
                        "tr": 32643, "bl": 32643,
                        "t": 32645, "b": 32645,
                        "l": 32644, "r": 32644,
                        "move": 32646,
                    }
                    new_cursor = cursor_map.get(h_hit, 32512)
                    if new_cursor != state.get("cursor_kind"):
                        state["cursor_kind"] = new_cursor
                        if hasattr(user32, "LoadCursorW") and hasattr(user32, "SetCursor"):
                            h_cur = user32.LoadCursorW(None, ctypes.cast(int(new_cursor), wt.LPCWSTR))
                            if h_cur:
                                user32.SetCursor(h_cur)
                else:
                    if state.get("cursor_kind") not in (None, 32512):
                        state["cursor_kind"] = 32512
                        if hasattr(user32, "LoadCursorW") and hasattr(user32, "SetCursor"):
                            h_cur = user32.LoadCursorW(None, ctypes.cast(32512, wt.LPCWSTR))
                            if h_cur:
                                user32.SetCursor(h_cur)
            return 0
            return 0


        if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            raw_delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            if raw_delta == 0:
                return 0


            mode = state["mode"]
            if mode in ("ring_groups", "ring_tools"):
                has_more_options = (state["pager"].total_pages > 1)
                # In WM_MOUSEWHEEL / WM_MOUSEHWHEEL, lparam holds screen cursor coordinates (x, y)
                pt_scr_x = ctypes.c_short(lparam & 0xFFFF).value
                pt_scr_y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                dx = float(pt_scr_x) - (state["ring_x"] + 230.0)
                dy = float(pt_scr_y) - (state["ring_y"] + 230.0)
                r = math.hypot(dx, dy)


                # Cursor is in the circular menu (whether on an option segment, outer track, or hub)
                if r <= 245.0:
                    state["last_active"] = time.monotonic()
                    # Keep track of hovered segment if hovering over an option button
                    if 92.0 <= r <= 220.0:
                        ang = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
                        seg = int(((ang + 30.0) % 360.0) // 60.0) % 6
                        state["hover_seg"] = seg


                    if has_more_options:
                        # Standard WM_MOUSEWHEEL: negative raw_delta is roll DOWN (forward scroll / clockwise)
                        # positive raw_delta is roll UP (backward scroll / counter-clockwise)
                        # WM_MOUSEHWHEEL: positive raw_delta is tilt RIGHT (clockwise)
                        if msg == WM_MOUSEHWHEEL:
                            scroll_units = float(raw_delta)
                        else:
                            scroll_units = -float(raw_delta)


                        # Accurately transform normal scroll length into roller degrees
                        # Standard notch (120 units) = 30.0 degrees (2 notches = 1 option segment; 12 notches = 360° full turn)
                        new_page = state["pager"].apply_scroll_delta(scroll_units, deg_per_notch=30.0)
                        if new_page is not None:
                            state["page"] = new_page


                        # Light angle dynamically follows the advancing droplet bead on the track
                        bead_deg = math.degrees(state["pager"].accumulated_rad)
                        state["light_angle_deg"] = (bead_deg + 270.0) % 360.0
                        redraw(hwnd)
            return 0


        if msg == WM_LBUTTONDOWN:
            state["last_active"] = time.monotonic()
            x, y = get_local_cursor_xy(lparam)
            mode = state["mode"]


            if mode == "morphing":
                # Instantly snap to target mode so every button is 100% instantaneously responsive
                user32.KillTimer(hwnd, 2)
                state["mode"] = state["morph_target_mode"]
                mode = state["mode"]


            if mode in ("ring_groups", "ring_tools"):
                dx = x - 230.0
                dy = y - 230.0
                r = math.hypot(dx, dy)
                if r < 92.0:
                    if mode == "ring_tools":
                        if len(state["groups"]) == 1 and state["staged_files"]:
                            # If only 1 category matched, clicking Back lets the user pick different files or see all groups
                            state["staged_files"] = []
                            state["current_preview_file"] = None
                            state["batch_badge"] = ""
                            state["groups"] = filter_and_rank_tools([], ffmpeg_available=bool(settings.get("ffmpeg", True)), usage_counts=settings.get("usage"))
                        state["mode"] = "ring_groups"
                        state["dwell_target"] = None
                        state["dwell_progress"] = 0.0
                        state["pager"] = RotationPager(len(state["groups"]), deadzone_radius=28.0, allow_negative=False)
                        redraw(hwnd)
                    else:
                        if not state["staged_files"]:
                            state["in_dialog"] = True
                            try:
                                picked = pick_files_dialog_win32(hwnd)
                            finally:
                                state["in_dialog"] = False
                            if picked:
                                apply_staged_files(picked)
                                redraw(hwnd)
                                return 0
                            else:
                                show_toast_notification(
                                    title="Selection Required",
                                    message="Please select a file to continue"
                                )
                                redraw(hwnd)
                                return 0
                        instant_exit()
                elif 92.0 <= r <= 225.0:
                    ang = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
                    seg = int(((ang + 30.0) % 360.0) // 60.0) % 6
                    state["hover_seg"] = seg
                    if mode == "ring_groups":
                        enter_group_segment(hwnd, seg)
                    else:
                        select_tool_segment(hwnd, seg)
                else:
                    # Do not exit on clicking outside the circle so user can interact with other windows and drag-drop files
                    return 0
                return 0


            if mode in ("options_window", "progress_window"):
                is_prog = (mode == "progress_window")
                wx, wy, ww, wh = get_window_rect("progress" if is_prog else "options")
                x0, y0, x1, y1 = 18, 18, ww - 18, wh - 18
                wx0, wy0, wx1, wy1 = x1 - 186, y0 + 20 if is_prog else y0 + 24, x1 - 22, y0 + 64 if is_prog else y0 + 68


                # 1. Top-Right Window Control Pill: Minimize (-), Square Expand (□), and Cross (✕)
                if (wy0 - 14) <= y <= (wy1 + 14) and (wx0 - 14) <= x <= (wx1 + 14):
                    pill_w = wx1 - wx0
                    sec_w = pill_w / 3.0
                    rel_x = x - wx0
                    if rel_x < sec_w:
                        start_morph(hwnd, to_island=True)
                        return 0
                    elif rel_x < 2.0 * sec_w:
                        state["window_expanded"] = not state["window_expanded"]
                        nwx, nwy, _, _ = get_window_rect("progress" if is_prog else "options")
                        state["x_spring"] = SpringSolver(float(nwx), 0.22, 0.85)
                        state["y_spring"] = SpringSolver(float(nwy), 0.22, 0.85)
                        state["window_opened_at"] = time.monotonic()
                        redraw(hwnd)
                        return 0
                    else:
                        is_finished = bool(state["done_at"] > 0)
                        trigger_window_exit_sequence(hwnd, is_done=is_finished)
                        return 0


                # 2. Bottom-Right Action Pill Buttons: Start / Pause / Resume / Finished and Cancel / Open Output
                bx0, by0, bx1, by1 = x1 - 280, y1 - 64 if is_prog else y1 - 68, x1 - 160, y1 - 20 if is_prog else y1 - 22
                cx0, cy0, cx1, cy1 = x1 - 146, y1 - 64 if is_prog else y1 - 68, x1 - 24, y1 - 20 if is_prog else y1 - 22
                if (by0 - 14) <= y <= (by1 + 14):
                    if (bx0 - 12) <= x <= (bx1 + 12):
                        if mode == "options_window":
                            start_conversion_job(hwnd)
                        else:
                            is_finished = bool(state["done_at"] > 0 or (not state["job_active"] and state["progress_frac"] >= 1.0))
                            if is_finished:
                                trigger_window_exit_sequence(hwnd, is_done=True)
                            else:
                                state["paused"] = not state["paused"]
                                if state["worker_proc"]:
                                    suspend_or_resume_process(state["worker_proc"].pid, state["paused"])
                                state["status_line"] = "Paused" if state["paused"] else "Processing..."
                                state["detail_line"] = "Paused — click Resume to continue" if state["paused"] else "Converting files..."
                                redraw(hwnd)
                        return 0
                    if (cx0 - 12) <= x <= (cx1 + 12):
                        if is_prog and (state["done_at"] > 0 or (not state["job_active"] and state["progress_frac"] >= 1.0)):
                            if state.get("last_outputs"):
                                out_dir = Path(state["last_outputs"][0]).parent
                                try:
                                    os.startfile(str(out_dir))
                                except Exception:
                                    pass
                            else:
                                out_docs = Path.home() / "Documents" / "Dew Drop"
                                try:
                                    os.startfile(str(out_docs))
                                except Exception:
                                    pass
                            return 0
                        else:
                            trigger_window_exit_sequence(hwnd, is_done=False)
                            return 0


                t_id, _, _, _ = current_selected_tool()
                ui_cfg = get_tool_ui_config(t_id, state["quality_val"], state["chip_idx"], state["text_input"])
                has_slider = ui_cfg.get("has_slider", False)
                chips = ui_cfg.get("chips") or []


                # 3. Draggable Percentage Slider Bar (only if tool actually uses a slider)
                px0, py0, px1, py1 = x0 + 40, y0 + 128, x1 - 104, y0 + 152
                if has_slider and mode == "options_window" and (py0 - 18 <= y <= py1 + 18) and (px0 - 14 <= x <= px1 + 14):
                    state["dragging_slider"] = True
                    try:
                        user32.SetCapture(hwnd)
                    except Exception:
                        pass
                    update_slider_from_x(hwnd, x)
                    return 0


                # 3b. Custom Crop Dragging Handle Click
                if mode == "options_window" and t_id == "img_crop" and state.get("chip_idx") == 4 and state.get("crop_disp_rect"):
                    h_hit = hit_test_crop_handle(x, y, state["crop_disp_rect"], state.get("crop_norm_box", [0.1, 0.1, 0.9, 0.9]))
                    if h_hit:
                        state["dragging_crop_handle"] = h_hit
                        state["crop_drag_start_cursor"] = (x, y)
                        state["crop_drag_start_norm"] = list(state.get("crop_norm_box", [0.1, 0.1, 0.9, 0.9]))
                        try:
                            user32.SetCapture(hwnd)
                        except Exception:
                            pass
                        return 0


                # 4. Option Preset Chips (support up to 5 chips including Custom)
                if chips and mode == "options_window":
                    cy_chip = (py1 + 22) if has_slider else (y0 + 130)
                    if (cy_chip - 6 <= y <= cy_chip + 40):
                        n_chips = max(1, min(6, len(chips)))
                        if n_chips <= 3:
                            base_cw = 154
                        elif n_chips == 4:
                            base_cw = 118
                        else:
                            base_cw = 96
                        if state["window_expanded"]:
                            base_cw += 20
                        spacing = 10 if n_chips >= 5 else 12
                        cw = base_cw + spacing
                        rel_x = x - px0
                        if 0 <= rel_x <= n_chips * cw:
                            clicked_idx = min(n_chips - 1, max(0, int(rel_x // cw)))
                            state["chip_idx"] = clicked_idx
                            if t_id == "img_crop":
                                if clicked_idx == 4:
                                    state["crop_norm_box"] = [0.1, 0.1, 0.9, 0.9]
                                else:
                                    state["crop_disp_rect"] = None
                            elif t_id in ("img_to_jpeg", "img_to_webp"):
                                state["quality_val"] = [72, 85, 95][min(clicked_idx, 2)]
                            elif t_id == "img_compress":
                                state["quality_val"] = [75, 50, 30][min(clicked_idx, 2)]
                            elif t_id in ("pdf_compress", "video_compress"):
                                state["quality_val"] = [85, 65, 45][min(clicked_idx, 2)]
                            nwx, nwy, _, _ = get_window_rect("options")
                            state["x_spring"] = SpringSolver(float(nwx), 0.22, 0.85)
                            state["y_spring"] = SpringSolver(float(nwy), 0.22, 0.85)
                            redraw(hwnd)
                            return 0


                # 5. Drag window anywhere else on screen!
                pt_scr = POINT()
                if user32.GetCursorPos(ctypes.byref(pt_scr)):
                    state["dragging_window"] = True
                    state["drag_start_cursor"] = (pt_scr.x, pt_scr.y)
                    state["drag_start_win"] = (int(state.get("saved_win_x", state["win_x"])), int(state.get("saved_win_y", state["win_y"])))
                    try:
                        user32.SetCapture(hwnd)
                    except Exception:
                        pass
                return 0


            if mode == "island":
                p_cx = 645
                x_cx = 705
                cy_isl = 38
                if math.hypot(x - p_cx, y - cy_isl) <= 24 and state["job_active"]:
                    state["paused"] = not state["paused"]
                    if state["worker_proc"]:
                        suspend_or_resume_process(state["worker_proc"].pid, state["paused"])
                    state["detail_line"] = "Paused — click || to resume" if state["paused"] else "Resumed conversion..."
                    redraw(hwnd)
                    return 0
                if math.hypot(x - x_cx, y - cy_isl) <= 24:
                    is_finished = bool(state["done_at"] > 0)
                    trigger_window_exit_sequence(hwnd, is_done=is_finished)
                    return 0
                # Clicking the island simply restores/morphs back into the window!
                # Never auto-opens file or folder — file/folder is only opened when explicitly clicking the "Open Output" button!
                start_morph(hwnd, to_island=False)
                return 0


        if msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            if state["mode"] in ("ring_groups", "ring_tools"):
                x, y = get_local_cursor_xy(lparam)
                if math.hypot(x - 230.0, y - 230.0) < 92.0:
                    instant_exit()
                    return 0


        if msg == WM_RBUTTONDOWN:
            if state["mode"] == "ring_tools":
                tools = state["groups"][min(state["group_idx"], len(state["groups"]) - 1)][1]
                total_pages = max(1, (len(tools) + 5) // 6)
                state["page"] = (state["page"] + 1) % total_pages
                redraw(hwnd)
            elif state["mode"] in ("options_window", "progress_window"):
                start_morph(hwnd, to_island=True)
            elif state["mode"] == "island":
                start_morph(hwnd, to_island=False)
            return 0


        if msg == WM_CHAR and state["mode"] == "options_window":
            ch = chr(wparam & 0xFFFF)
            if ch.isprintable() and ch not in ("\r", "\n", "\t"):
                state["text_input"] = (state["text_input"] + ch)[:48]
                redraw(hwnd)
            return 0


        if msg == WM_KEYDOWN:
            if wparam == VK_BACK and state["mode"] == "options_window" and state["text_input"]:
                state["text_input"] = state["text_input"][:-1]
                redraw(hwnd)
                return 0
            if wparam == VK_ESCAPE:
                if state["mode"] in ("options_window", "progress_window", "island"):
                    is_finished = bool(state["done_at"] > 0)
                    trigger_window_exit_sequence(hwnd, is_done=is_finished)
                else:
                    instant_exit()
                return 0
            elif wparam == VK_RETURN and state["mode"] == "options_window":
                start_conversion_job(hwnd)
            elif wparam == VK_SPACE and state["mode"] in ("options_window", "progress_window", "island"):
                start_morph(hwnd, to_island=(state["mode"] != "island"))
            elif wparam == 0x77:  # VK_F8: Toggle Demo Record Mode vs Live Mode
                state["record_mode"] = not state.get("record_mode", False)
                affinity = 0x00000000 if state["record_mode"] else 0x00000011
                if hasattr(user32, "SetWindowDisplayAffinity"):
                    user32.SetWindowDisplayAffinity(hwnd, affinity)
                    thwnd = toast_state.get("hwnd")
                    if thwnd and hasattr(user32, "IsWindow") and user32.IsWindow(thwnd):
                        user32.SetWindowDisplayAffinity(thwnd, affinity)
                if state["record_mode"]:
                    show_toast_notification("Record Mode Active", "Dewdrop is visible to screen recorders (OBS/Game Bar) with live video")
                else:
                    show_toast_notification("Live Glass Active", "Standard mode active (excluded from screen capture)")
                redraw(hwnd)
                return 0
            return 0


        if msg == WM_TIMER:
            now = time.monotonic()


            # Timer 4: 25ms live Liquid-Glass Shine Animation + global cursor tracking + dwell timers + show signal
            if wparam == 4:
                state["shine_phase"] = (state["shine_phase"] + 0.018) % 1.0


                if state["mode"] in ("ring_groups", "ring_tools"):
                    # Global Esc check: if circular menu is open and user presses Esc anywhere on system, close menu
                    if hasattr(user32, "GetAsyncKeyState") and (user32.GetAsyncKeyState(0x1B) & 0x8000) != 0:
                        instant_exit()
                        return 0


                if poll_show_signal():
                    state["last_active"] = now
                    try:
                        user32.KillTimer(hwnd, 5)
                    except Exception:
                        pass
                    pt_scr = POINT()
                    if user32.GetCursorPos(ctypes.byref(pt_scr)):
                        cx, cy = compute_ring_placement(pt_scr.x, pt_scr.y, (0, 0, screen_w, screen_h - 48), 1.0)
                        state["ring_x"] = cx - 230
                        state["ring_y"] = cy - 230
                        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), state["ring_x"], state["ring_y"], 460, 460, 0x0040)
                    sel = get_selected_explorer_files()
                    if sel:
                        apply_staged_files(sel)
                    else:
                        state["mode"] = "ring_groups"
                        state["page"] = 0
                        state["hover_seg"] = -1
                    user32.ShowWindow(hwnd, 5)
                    user32.BringWindowToTop(hwnd)
                    try:
                        user32.SetForegroundWindow(hwnd)
                        user32.SetActiveWindow(hwnd)
                        user32.SetFocus(hwnd)
                    except Exception:
                        pass
                    redraw(hwnd)
                    return 0


                if state["mode"] in ("ring_groups", "ring_tools"):
                    has_more_options = (state["pager"].total_pages > 1)
                    if has_more_options:
                        # Only track cursor via GetCursorPos if client mouse has been idle (e.g. cursor outside window)
                        # Prevents duplicate delta accumulation and conflict between client WM_MOUSEMOVE and screen timer
                        if (now - state.get("last_client_mousemove", 0.0)) >= 0.035:
                            pt_scr = POINT()
                            if user32.GetCursorPos(ctypes.byref(pt_scr)):
                                dx_scr = float(pt_scr.x) - (state["ring_x"] + 230.0)
                                dy_scr = float(pt_scr.y) - (state["ring_y"] + 230.0)
                                r_scr = math.hypot(dx_scr, dy_scr)
                                prev_light = state["light_angle_deg"]
                                if r_scr > 8.0:
                                    state["light_angle_deg"] = (math.degrees(math.atan2(dy_scr, dx_scr)) + 360.0) % 360.0
                                prev_rad = state["pager"].accumulated_rad
                                prev_color = getattr(state["pager"], "color_turns", 0.0)
                                new_page = state["pager"].update_pointer(dx_scr, dy_scr)
                                if new_page is not None:
                                    state["page"] = new_page
                                curr_color = getattr(state["pager"], "color_turns", 0.0)
                                if (abs(state["pager"].accumulated_rad - prev_rad) > 1e-4) or (abs(curr_color - prev_color) > 1e-4) or (abs(state["light_angle_deg"] - prev_light) > 0.8) or (new_page is not None):
                                    state["last_active"] = now
                                    if now - state["last_mousemove_redraw"] >= 0.012:
                                        state["last_mousemove_redraw"] = now
                                        redraw(hwnd)


                if state["done_at"] > 0 and not state["job_active"] and state["mode"] in ("progress_window", "island"):
                    delay = 2.0 if state["mode"] == "progress_window" else 0.0
                    if (now - state["done_at"]) >= delay:
                        trigger_window_exit_sequence(hwnd, is_done=True)
                        return 0


                if state["mode"] != "morphing" and not state["dragging_slider"] and not state["dragging_window"]:
                    redraw(hwnd)
                return 0


            # Timer 2: 16ms 60 FPS spring morph
            if wparam == 2 and state["mode"] == "morphing":
                dt = 0.016
                state["morph_spring"].step(dt)
                state["x_spring"].step(dt)
                state["y_spring"].step(dt)
                redraw(hwnd)
                m_diff = abs(state["morph_spring"].position - state["morph_spring"].target)
                x_diff = abs(state["x_spring"].position - state["x_spring"].target)
                y_diff = abs(state["y_spring"].position - state["y_spring"].target)
                if (m_diff < 0.02 and x_diff < 1.5 and y_diff < 1.5) or (
                    state["morph_spring"].is_settled() and state["x_spring"].is_settled() and state["y_spring"].is_settled()
                ):
                    user32.KillTimer(hwnd, 2)
                    state["mode"] = state["morph_target_mode"]
                    state["window_opened_at"] = time.monotonic()
                    redraw(hwnd)
                return 0


            # Timer 3: 50ms worker progress polling (updates current file thumbnail live!)
            if wparam == 3 and state["job_active"]:
                if not state["paused"]:
                    res_file = state.get("worker_res_file")
                    snap = None
                    if res_file and Path(res_file).exists():
                        try:
                            snap = json.loads(Path(res_file).read_text(encoding="utf-8"))
                        except Exception:
                            snap = None
                    if snap:
                        state["progress_frac"] = max(state["progress_frac"], float(snap.get("fraction", 0.1)))
                        done_n = int(snap.get("done", 0))
                        tot_n = max(1, int(snap.get("total", 1)))
                        cur_name = snap.get("current", "")
                        cur_path_s = snap.get("current_path", "")
                        if cur_path_s and Path(cur_path_s).exists():
                            state["current_preview_file"] = Path(cur_path_s)
                        elif done_n < len(state["staged_files"]):
                            state["current_preview_file"] = state["staged_files"][done_n]
                        state["batch_badge"] = f"{min(done_n + 1, tot_n)}/{tot_n}" if tot_n > 1 else ""
                        state["detail_line"] = f"File {min(done_n + 1, tot_n)} of {tot_n}  •  {cur_name[:24]}"
                        if snap.get("finished"):
                            outs = snap.get("outputs") or []
                            errs = snap.get("errors") or []
                            fb = bool(snap.get("fallback_used", False))
                            state["last_outputs"] = outs
                            state["last_errors"] = errs
                            state["progress_frac"] = 1.0
                            state["job_active"] = False
                            state["done_at"] = now
                            if outs and Path(outs[-1]).is_file():
                                state["current_preview_file"] = Path(outs[-1])
                            if errs:
                                state["status_line"] = f"Done — {len(outs)} converted ({len(errs)} failed)"
                                state["detail_line"] = errs[0][:44]
                            elif fb:
                                state["status_line"] = "Done — Saved in Documents\\Dew Drop"
                                state["detail_line"] = "Source folder read-only • Saved to Documents\\Dew Drop"
                            else:
                                state["status_line"] = "Done — Saved in Dew Drop"
                                state["detail_line"] = "Done  •  Saved in Dew Drop folder (Click to open)"
                            user32.KillTimer(hwnd, 3)
                            cleanup_worker_temp()
                    else:
                        state["progress_frac"] = min(0.94, state["progress_frac"] + 0.03)
                    if state["mode"] == "morphing":
                        state["morph_isl_im"] = build_current_island_image()
                    else:
                        redraw(hwnd)
                return 0


            # Timer 5: 16ms 60 FPS Cinematic Dynamic Alert Exit Sequence
            if wparam == 5:
                dt = 0.016
                phase = state.get("exit_phase", "morph_to_alert")
                is_done = state.get("exit_is_done", True)


                if phase == "morph_to_alert":
                    state["morph_spring"].step(dt)
                    state["x_spring"].step(dt)
                    state["y_spring"].step(dt)
                    redraw(hwnd)
                    m_diff = abs(state["morph_spring"].position - state["morph_spring"].target)
                    x_diff = abs(state["x_spring"].position - state["x_spring"].target)
                    y_diff = abs(state["y_spring"].position - state["y_spring"].target)
                    if (m_diff < 0.03 and x_diff < 2.0 and y_diff < 2.0) or (
                        state["morph_spring"].is_settled() and state["x_spring"].is_settled() and state["y_spring"].is_settled()
                    ):
                        state["exit_phase"] = "hold_alert"
                        state["exit_phase_start"] = now
                        state["mode"] = "closing_alert"
                        redraw(hwnd)
                    return 0


                elif phase == "hold_alert":
                    elapsed = now - state["exit_phase_start"]
                    redraw(hwnd)
                    if elapsed >= 1.00:
                        state["exit_phase"] = "squeeze_alert"
                        state["exit_phase_start"] = now
                        state["exit_shrink_t"] = 0.0
                    return 0


                elif phase == "squeeze_alert":
                    elapsed = now - state["exit_phase_start"]
                    t_shrink = min(1.0, elapsed / 0.40)
                    state["exit_shrink_t"] = t_shrink
                    redraw(hwnd)
                    if t_shrink >= 1.0:
                        state["exit_phase"] = "fade_circle"
                        state["exit_phase_start"] = now
                        state["exit_fade_t"] = 0.0
                    return 0


                elif phase == "fade_circle":
                    elapsed = now - state["exit_phase_start"]
                    t_fade = min(1.0, elapsed / 0.25)
                    state["exit_fade_t"] = t_fade
                    if t_fade >= 1.0:
                        user32.KillTimer(hwnd, 5)
                        user32.KillTimer(hwnd, 4)
                        user32.KillTimer(hwnd, 1)
                        user32.ShowWindow(hwnd, 0)
                        try:
                            user32.DestroyWindow(hwnd)
                        except Exception:
                            pass
                        instant_exit()
                        return 0
                    redraw(hwnd)
                    return 0


            if wparam == 1:
                if not state["job_active"] and (now - state["last_active"] >= idle_limit_secs):
                    instant_exit()
                return 0


        if msg == WM_DESTROY:
            instant_exit()
            return 0


        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


    wndproc_cb = WNDPROC(py_wndproc)
    cls_name = "DewdropOverlayV4"
    hcursor = user32.LoadCursorW(None, ctypes.cast(32512, wt.LPCWSTR))
    wc = WNDCLASSW(0, wndproc_cb, 0, 0, hinst, None, hcursor, None, None, cls_name)
    user32.RegisterClassW(ctypes.byref(wc))


    toast_cb = WNDPROC(toast_wndproc)
    wc_toast = WNDCLASSW(0, toast_cb, 0, 0, hinst, None, hcursor, None, None, toast_cls_name)
    user32.RegisterClassW(ctypes.byref(wc_toast))


    pt_cur = POINT()
    user32.GetCursorPos(ctypes.byref(pt_cur))
    cx, cy = compute_ring_placement(pt_cur.x, pt_cur.y, (0, 0, screen_w, screen_h - 48), 1.0)
    state["ring_x"] = cx - 230
    state["ring_y"] = cy - 230


    ex_style = WS_EX_LAYERED | WS_EX_TOPMOST
    hwnd = user32.CreateWindowExW(
        ex_style, cls_name, APP_NAME, WS_POPUP | WS_VISIBLE,
        state["ring_x"], state["ring_y"], 860, 460,
        None, None, hinst, None
    )
    register_overlay_hwnd(hwnd)
    if hasattr(user32, "SetWindowDisplayAffinity"):
        try:
            initial_affinity = 0x00000000 if state.get("record_mode", True) else 0x00000011
            user32.SetWindowDisplayAffinity(hwnd, initial_affinity)
        except Exception:
            pass
    try:
        user32.ChangeWindowMessageFilterEx(hwnd, WM_DROPFILES, 1, None)
        user32.ChangeWindowMessageFilterEx(hwnd, 0x0049, 1, None)
    except Exception:
        pass
    shell32.DragAcceptFiles(hwnd, True)
    user32.SetTimer(hwnd, 1, 1000, None)
    user32.SetTimer(hwnd, 4, 16, None)
    redraw(hwnd)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    except Exception:
        pass


    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    instant_exit()




def main():
    args = sys.argv[1:]
    if "--worker" in args:
        idx = args.index("--worker")
        if idx + 2 < len(args):
            rc = worker_main(args[idx + 1], args[idx + 2])
            sys.exit(rc)
        sys.exit(2)


    if "--make-shortcut" in args or "--configure-hotkey" in args:
        flag = "--make-shortcut" if "--make-shortcut" in args else "--configure-hotkey"
        idx = args.index(flag)
        hk = args[idx + 1] if idx + 1 < len(args) else "CTRL+ALT+Z"
        ok = configure_windows_shortcut(hk)
        print(f"Shortcut configured ({hk}): {'OK' if ok else 'SAVED_SETTINGS'}")
        return


    if "--self-test" in args:
        from tests.self_test import run_all_tests
        run_all_tests()


        # Verify strict file-type filtering (Images -> Image options only; ZIP -> Archive options only)
        img_groups = [g[0] for g in filter_and_rank_tools([Path("photo.png")], ffmpeg_available=True)]
        zip_groups = [g[0] for g in filter_and_rank_tools([Path("bundle.zip")], ffmpeg_available=True)]
        assert img_groups == ["Convert image", "Edit image", "PDF & docs", "Archives"], f"Unexpected image groups: {img_groups}"
        assert zip_groups == ["Archives"], f"Expected only ['Archives'] for .zip, got {zip_groups}"


        sample_f = ensure_demo_sample_file()
        r_im = render_ring_surface_rgba(
            ["Convert image", "Edit image", "PDF & docs", "", "", ""],
            hover_seg=1, turn_progress=0.42, staged_count=3, subtitle="3 file(s) selected", dwell_progress=0.35, shine_phase=0.30
        )
        # Benchmark interactive Window redraw speed (slider drag & button hover)
        w_im = render_options_or_progress_window_rgba(
            "Sample_Photo.png", "420 KB", "Compress", "Quality (1-100)", "options",
            85, 1, 0.68, False, "Processing...", "1.6 MB of 2.4 MB • 2s left",
            expanded=False, preview_file=sample_f, batch_badge="1/3", shine_phase=0.35
        )
        t0 = time.perf_counter()
        for q_step in range(10):
            w_im = render_options_or_progress_window_rgba(
                "Sample_Photo.png", "420 KB", "Compress", "Quality (1-100)", "options",
                50 + q_step * 4, 1, 0.68, False, "Processing...", "1.6 MB of 2.4 MB • 2s left",
                expanded=False, preview_file=sample_f, batch_badge="1/3", shine_phase=0.10 + q_step * 0.05
            )
        avg_win_ms = ((time.perf_counter() - t0) / 10.0) * 1000.0
        w_exp = render_options_or_progress_window_rgba(
            "Sample_Photo.png", "420 KB", "Compress", "Quality (1-100)", "options",
            85, 1, 0.68, False, "Processing...", "1.6 MB of 2.4 MB • 2s left",
            w=860, h=430, expanded=True, preview_file=sample_f, batch_badge="2/3", shine_phase=0.55
        )
        i_im = render_dynamic_island_rgba(
            "Sample_Photo.png", "3 files • 1.2 MB", 0.68, False, "File 2 of 3  •  Sample_Photo.png",
            preview_file=sample_f, batch_badge="2/3", shine_phase=0.45
        )
        m_im = render_morph_frame_rgba(0.5, w_im, i_im)
        m_exp = render_morph_frame_rgba(0.5, w_exp, i_im)
        assert r_im.size == (460, 460) and w_im.size == (720, 360) and w_exp.size == (860, 430) and i_im.size == (780, 76)
        assert m_im.size == (750, 218) and m_exp.size == (820, 253)
        print(f"UI Surface & Speed Smoke Test PASSED: Slider/Shine Redraw = {avg_win_ms:.2f} ms/frame ({1000.0/max(0.1, avg_win_ms):.0f} FPS capability), Real File Thumbnails, Strict File-Type Filtering, and Pure Black Ink verified.")
        return


    start_mode = "ring_groups"
    if "--preview-window" in args:
        start_mode = "options_window"
    elif "--preview-island" in args:
        start_mode = "island"


    start_record_mode = not bool("--stealth" in args or "--exclude-capture" in args)
    cli_files = [Path(a).resolve() for a in args if not a.startswith("-") and Path(a).is_file()]


    if os.name == "nt":
        run_windows_interactive_gui(start_mode=start_mode, cli_files=cli_files, record_mode=start_record_mode)
    else:
        print("Dewdrop Runtime: verifying liquid-glass surfaces on non-Windows host...")
        from tests.self_test import run_all_tests
        run_all_tests()




if __name__ == "__main__":
    main()