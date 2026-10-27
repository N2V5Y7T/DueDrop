#!/usr/bin/env python3
import bz2
import csv
import gzip
import hashlib
import io
import json
import lzma
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path

import lxml.etree
import numpy as np

if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(100000)
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
import pypdf
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, NumberObject
import docx
from docx.shared import Inches, Pt
from xml.sax.saxutils import escape as xml_escape
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors as rl_colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_appdata_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "DewDrop"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


# Evidence directory removed (tests manage temporary directories independently)


def _find_ffmpeg() -> str:
    for candidate in (
        ROOT / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"),
        ROOT / "assets" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"),
        Path(sys.executable).parent / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"),
    ):
        if candidate.exists():
            return str(candidate)
    which_ff = shutil.which("ffmpeg")
    if which_ff:
        return which_ff
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        program_data = os.environ.get("ProgramData", "C:\\ProgramData")
        user_profile = os.environ.get("USERPROFILE", "")
        if local_app_data:
            winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            if winget_dir.exists():
                try:
                    for f in winget_dir.glob("**/ffmpeg.exe"):
                        if f.is_file():
                            return str(f)
                except Exception:
                    pass
        extra_candidates = [
            Path(program_files) / "ffmpeg" / "bin" / "ffmpeg.exe",
            Path(program_data) / "chocolatey" / "bin" / "ffmpeg.exe",
            Path(user_profile) / "scoop" / "shims" / "ffmpeg.exe",
        ]
        for cand in extra_candidates:
            if cand.exists():
                return str(cand)
    return "/usr/bin/ffmpeg"


def has_ffmpeg() -> bool:
    ff = _find_ffmpeg()
    return bool(ff and Path(ff).exists())


def _ffmpeg_base_cmd(ff_bin: str) -> list[str]:
    """
    Standard base invocation for FFmpeg enforcing security and non-interactive silence.
    Enforces protocol whitelist to block remote network calls and local file disclosure vectors.
    """
    return [
        ff_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-protocol_whitelist", "file,pipe",
    ]


MAX_TEXT_INPUT_BYTES = 100 * 1024 * 1024


def read_text_smart(path_or_bytes: Path | str | bytes) -> tuple[str, str]:
    """
    Robust text file reader that detects encoding in strict priority order:
    1. BOM checks: UTF-8-SIG (EF BB BF), UTF-32 (FF FE 00 00 / 00 00 FE FF), UTF-16 (FF FE / FE FF).
    2. Strict UTF-8 without BOM.
    3. Windows CP1252 (handles Western European accented chars and € without CJK corruption).
    4. Latin-1 fallback (never fails).
    Never decodes UTF-16 without a valid BOM to prevent CP1252 text from turning into CJK garbage.
    Enforces 100 MiB input size limit.
    """
    if isinstance(path_or_bytes, (str, Path)):
        p = Path(path_or_bytes)
        sz = p.stat().st_size
        if sz > MAX_TEXT_INPUT_BYTES:
            raise ValueError(f"Input file '{p.name}' exceeds 100 MiB limit ({sz} bytes)")
        raw = p.read_bytes()
    else:
        raw = bytes(path_or_bytes)
        if len(raw) > MAX_TEXT_INPUT_BYTES:
            raise ValueError(f"Input text data exceeds 100 MiB limit ({len(raw)} bytes)")

    # 1. BOM checks first
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe\x00\x00") or raw.startswith(b"\x00\x00\xfe\xff"):
        return raw.decode("utf-32"), "utf-32"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"

    # 1.5. UTF-16 heuristic without BOM (>=30% NUL bytes concentrated on odd or even indices)
    if len(raw) >= 4:
        nul_count = raw.count(0)
        if (nul_count / len(raw)) >= 0.30:
            odd_nuls = raw[1::2].count(0)
            even_nuls = raw[0::2].count(0)
            if odd_nuls > even_nuls:
                try:
                    return raw.decode("utf-16-le"), "utf-16-le"
                except UnicodeDecodeError:
                    pass
            elif even_nuls > odd_nuls:
                try:
                    return raw.decode("utf-16-be"), "utf-16-be"
                except UnicodeDecodeError:
                    pass

    # 2. Strict UTF-8
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # 3. CP1252
    try:
        return raw.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        pass

    # 4. Latin-1 fallback
    return raw.decode("latin-1"), "latin-1"


# ============================================================================
# 1. Pure File Transformation & Batch Processing Logic
# ============================================================================

class SpringSolver:
    def __init__(self, initial: float, response: float = 0.45, damping_ratio: float = 0.80):
        self.position = float(initial)
        self.velocity = 0.0
        self.target = float(initial)
        self.response = max(0.05, float(response))
        self.damping_ratio = min(max(float(damping_ratio), 0.1), 2.0)

    def set_target(self, target: float):
        self.target = float(target)

    def step(self, dt: float) -> float:
        sub_dt = 1.0 / 240.0
        rem = min(max(dt, 0.0), 0.25)
        omega = 2.0 * math.pi / self.response
        k = omega * omega
        c = 2.0 * self.damping_ratio * omega
        while rem > 1e-9:
            h = min(rem, sub_dt)
            disp = self.position - self.target
            acc = -k * disp - c * self.velocity
            self.velocity += acc * h
            self.position += self.velocity * h
            rem -= h
        return self.position

    def is_settled(self) -> bool:
        return abs(self.position - self.target) < 0.0008 and abs(self.velocity) < 0.005


def compute_ring_placement(cursor_x: int, cursor_y: int, work_area: tuple, dpi_scale: float = 1.0) -> tuple:
    left, top, right, bottom = work_area
    scale = max(0.5, dpi_scale)
    radius = round(230.0 * scale)
    margin = round(48.0 * scale)
    safe_dist = radius + margin

    space_left = cursor_x - left
    space_right = right - cursor_x
    space_top = cursor_y - top
    space_bottom = bottom - cursor_y

    dx_dir = 1.0 if space_right >= space_left else -1.0
    dy_dir = 1.0 if space_bottom >= space_top else -1.0

    h_room = float(max(space_right, space_left, 1))
    v_room = float(max(space_bottom, space_top, 1))
    norm = math.hypot(h_room, v_room)
    raw_cx = cursor_x + round(dx_dir * (h_room / norm) * safe_dist)
    raw_cy = cursor_y + round(dy_dir * (v_room / norm) * safe_dist)

    pad = round(8.0 * scale)
    min_x = left + radius + pad
    max_x = max(min_x, right - radius - pad)
    min_y = top + radius + pad
    max_y = max(min_y, bottom - radius - pad)

    cx = min(max(raw_cx, min_x), max_x)
    cy = min(max(raw_cy, min_y), max_y)

    dist_sq = (cx - cursor_x) ** 2 + (cy - cursor_y) ** 2
    min_clear = radius + (margin // 2)
    if dist_sq < min_clear * min_clear:
        candidates = [
            (cursor_x + safe_dist, cursor_y),
            (cursor_x - safe_dist, cursor_y),
            (cursor_x, cursor_y + safe_dist),
            (cursor_x, cursor_y - safe_dist),
            (cursor_x + safe_dist, cursor_y + safe_dist),
            (cursor_x - safe_dist, cursor_y + safe_dist),
            (cursor_x + safe_dist, cursor_y - safe_dist),
            (cursor_x - safe_dist, cursor_y - safe_dist),
        ]
        best = (cx, cy)
        best_d2 = dist_sq
        for cand_x, cand_y in candidates:
            x = min(max(cand_x, min_x), max_x)
            y = min(max(cand_y, min_y), max_y)
            d2 = (x - cursor_x) ** 2 + (y - cursor_y) ** 2
            if d2 > best_d2:
                best_d2 = d2
                best = (x, y)
        return best
    return (cx, cy)


class RotationPager:
    """
    Tracks cumulative 360° clockwise and counter-clockwise cursor rotation around the ring centre
    with asymmetric 50% CCW hysteresis. Always accumulates rotation angle so the 360° liquid-glass
    progress arc and dynamic shade transition animate smoothly across turns.

    1. Clockwise: Completing 1 full clockwise rotation (360°) advances to the next 6 options (Page 1)
       and starts the next 360° cycle with a smoothly transitioning new color.
    2. Counter-clockwise: When the next 6 options menu is open, the previous 6 options only return
       when rotating anti-clockwise more than 50% into the previous cycle (e.g. from 420° down to 180°).
    3. Hysteresis: If rotating clockwise again from that point, it only advances to the next 6 options
       when completing the full 360° again from the starting point.
    """
    def __init__(self, total_items: int, deadzone_radius: float = 28.0, allow_negative: bool = True):
        self.current_page = 0
        self.total_pages = max(1, (max(1, total_items) + 5) // 6)
        self.accumulated_rad = 0.0
        self.last_angle = None
        self.deadzone_radius = deadzone_radius
        self.completed_turns = 0
        self.allow_negative = allow_negative
        self.total_rad = 0.0
        self.ccw_accumulator = 0.0

    @property
    def color_turns(self) -> float:
        """Returns the continuous real-valued turn position for smooth color interpolation."""
        return max(0.0, self.total_rad / (2.0 * math.pi))

    def apply_delta_rad(self, delta: float) -> int | None:
        """
        Applies an angular delta in radians to the pager.
        Supports both continuous mouse orbiting and mouse roller / touchpad scroll wheel rotation.
        """
        if abs(delta) < 1e-5:
            return None

        two_pi = 2.0 * math.pi
        if not self.allow_negative:
            prev_page = self.current_page
            max_rad = float(self.total_pages) * two_pi

            if delta > 0:
                # Clockwise rotation:
                self.total_rad = min(max_rad, max(0.0, self.total_rad + delta))
                # Completing 1 full 360° turn advances to the next 6 options
                if self.current_page < self.total_pages - 1:
                    cw_threshold = float(self.current_page + 1) * two_pi - 0.02
                    if self.total_rad >= cw_threshold:
                        self.current_page += 1
            else:
                # Counter-clockwise (anticlockwise) rotation:
                self.total_rad = max(0.0, self.total_rad + delta)
                # When the next 6 option menu is open, the previous 6 only return when rotated
                # anti-clockwise more than 50% into the previous cycle (e.g. 420° -> 180°)
                if self.current_page > 0:
                    ccw_threshold = (float(self.current_page) - 0.5) * two_pi + 0.02
                    if self.total_rad <= ccw_threshold:
                        self.current_page -= 1

            # Visual 360° progress arc on the ring:
            page_base = float(self.current_page) * two_pi
            if self.total_rad >= page_base:
                self.accumulated_rad = min(two_pi, self.total_rad - page_base)
            else:
                prev_page_base = float(self.current_page - 1) * two_pi
                self.accumulated_rad = max(0.0, min(two_pi, self.total_rad - prev_page_base))

            if self.current_page != prev_page:
                return self.current_page
            return None
        else:
            self.accumulated_rad += delta
            threshold = 2.0 * math.pi + 0.18
            if self.accumulated_rad >= threshold:
                self.accumulated_rad -= 2.0 * math.pi
                self.completed_turns += 1
                if self.total_pages > 1:
                    self.current_page = (self.current_page + 1) % self.total_pages
                    return self.current_page
                return 0
            elif self.accumulated_rad <= -threshold:
                self.accumulated_rad += 2.0 * math.pi
                self.completed_turns -= 1
                if self.total_pages > 1:
                    self.current_page = (self.current_page - 1) % self.total_pages
                    return self.current_page
                return 0
            return None

    def apply_scroll_delta(self, scroll_units: float, deg_per_notch: float = 30.0) -> int | None:
        """
        Converts mouse wheel / touchpad scroll delta into angular rotation degrees/radians.
        Normal standard notch is WHEEL_DELTA = 120.
        scroll_units > 0 (wheel down / forward scroll) rotates clockwise.
        scroll_units < 0 (wheel up / back scroll) rotates counter-clockwise.
        """
        rad_per_unit = math.radians(deg_per_notch) / 120.0
        delta_rad = float(scroll_units) * rad_per_unit
        return self.apply_delta_rad(delta_rad)

    def update_pointer(self, dx: float, dy: float) -> int | None:
        if math.hypot(dx, dy) < self.deadzone_radius:
            return None
        angle = math.atan2(dx, -dy)
        if self.last_angle is not None:
            delta = angle - self.last_angle
            while delta > math.pi:
                delta -= 2.0 * math.pi
            while delta < -math.pi:
                delta += 2.0 * math.pi
            if abs(delta) > 0.75 * math.pi:
                # Flick or crossing near center: update reference without accumulating false half-turn
                self.last_angle = angle
                return None
            if abs(delta) > 1e-4:
                self.last_angle = angle
                return self.apply_delta_rad(delta)
        else:
            self.last_angle = angle
        return None


RESERVED_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$",
    *{f"COM{i}" for i in range(0, 10)},
    *{f"LPT{i}" for i in range(0, 10)},
}


def _strip_format_and_controls(s: str) -> str:
    """
    Strips Unicode format characters (category 'Cf', including bidi overrides like RLO U+202E,
    LRO, isolates, zero-width chars, BOMs) and C0/C1 control characters (\x00-\x1f, \x7f-\x9f).
    """
    return "".join(
        ch for ch in s
        if unicodedata.category(ch) != "Cf" and not ("\x00" <= ch <= "\x1f" or "\x7f" <= ch <= "\x9f")
    )


def sanitize_stem(raw_stem: str) -> str:
    raw_stem = _strip_format_and_controls(raw_stem)
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_stem).strip().rstrip(". ").lstrip("-")
    base = cleaned if cleaned else "output"
    prefix = base.split(".")[0].upper()
    if prefix in RESERVED_DEVICE_NAMES:
        base = "_" + base
    return base[:140]


def sanitize_rel_path(rel_name: str, target_root: Path) -> Path:
    """
    Sanitizes archive relative paths, preventing directory traversal, absolute paths,
    alternate data streams (ADS), illegal format/bidi controls (Cf), C0/C1 controls,
    and illegal Windows reserved device names in both stem and suffix.
    """
    norm = _strip_format_and_controls(rel_name).replace("\\", "/")
    if norm.startswith("/") or ":" in norm:
        raise ValueError(f"Refused archive entry with absolute/drive path or ADS: {rel_name}")
    parts = [p for p in norm.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts) or len(parts) > 20:
        raise ValueError(f"Refused archive entry with path traversal: {rel_name}")
    safe_parts = []
    for p in parts:
        clean = p.split(":")[0]
        clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean).strip().rstrip(". ")
        if not clean:
            clean = "entry"
        part_path = Path(clean)
        stem = sanitize_stem(part_path.stem)
        suffix = _strip_format_and_controls(part_path.suffix)
        suffix = re.sub(r'[<>:"/\\|?*\x00-\x1f ]', "_", suffix).rstrip(". ")
        safe_parts.append(f"{stem}{suffix}"[:180])
    return target_root.joinpath(*safe_parts)


_LAST_FALLBACK_USED = False
_LAST_NOTICE = ""


def get_last_notice() -> str:
    global _LAST_NOTICE
    return _LAST_NOTICE


def set_last_notice(msg: str):
    global _LAST_NOTICE
    _LAST_NOTICE = msg


def resolve_clash_free_output(source_file: Path, new_stem: str, new_ext: str) -> Path:
    """
    Resolves `<source_folder>/Dew Drop/<stem>.<ext>` with automatic `(1)`, `(2)` clash resolution.
    If `<source_folder>/Dew Drop` is not writable (e.g. CD-ROM, locked network share, read-only dir),
    automatically falls back to `~/Documents/Dew Drop` and sets `_LAST_FALLBACK_USED`.
    Preserves exact source drive & folder location whether run locally or from another drive.
    """
    global _LAST_FALLBACK_USED
    _LAST_FALLBACK_USED = False

    src = Path(source_file).resolve()
    if src.is_dir() and src.name.lower() in ("dew drop", "dewdrop"):
        parent_dir = src
    else:
        parent_dir = src.parent

    # If the file/folder is already inside a Dew Drop folder, save directly inside it without nesting Dew Drop/Dew Drop
    if parent_dir.name.lower() in ("dew drop", "dewdrop"):
        dew_dir = parent_dir
    else:
        dew_dir = None
        try:
            cand1 = parent_dir / "Dew Drop"
            cand2 = parent_dir / "Dewdrop"
            if cand1.is_dir():
                dew_dir = cand1
            elif cand2.is_dir():
                dew_dir = cand2
            else:
                for child in parent_dir.iterdir():
                    if child.is_dir() and child.name.lower() in ("dew drop", "dewdrop"):
                        dew_dir = child
                        break
        except Exception:
            pass
        if dew_dir is None:
            dew_dir = parent_dir / "Dew Drop"

    try:
        dew_dir.mkdir(parents=True, exist_ok=True)
        probe = dew_dir / f".dewdrop-write-probe-{uuid.uuid4().hex[:8]}"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
    except (PermissionError, OSError):
        _LAST_FALLBACK_USED = True
        dew_dir = Path.home() / "Documents" / "Dew Drop"
        dew_dir.mkdir(parents=True, exist_ok=True)

    safe = sanitize_stem(new_stem)
    ext = new_ext.lstrip(".")
    cand = dew_dir / (f"{safe}.{ext}" if ext else safe)
    if not cand.exists():
        return cand
    for idx in range(1, 10000):
        cand = dew_dir / (f"{safe} ({idx}).{ext}" if ext else f"{safe} ({idx})")
        if not cand.exists():
            return cand
    raise RuntimeError("Output clash counter exhausted")


def was_output_fallback_used() -> bool:
    return _LAST_FALLBACK_USED


# ============================================================================
# 2. Tool Catalogue, Compatibility Filtering & Usage Ranking
# ============================================================================

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif", ".ico", ".avif"}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".ts"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz"}
TEXT_EXTS = {".txt", ".md", ".log", ".ini", ".cfg", ".yaml", ".yml", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".css"}

FORMAT_OF_SUFFIX = {
    ".png": ("PNG", ".png"),
    ".jpg": ("JPEG", ".jpg"),
    ".jpeg": ("JPEG", ".jpg"),
    ".webp": ("WEBP", ".webp"),
    ".bmp": ("BMP", ".bmp"),
    ".tiff": ("TIFF", ".tiff"),
    ".tif": ("TIFF", ".tif"),
    ".gif": ("GIF", ".gif"),
    ".ico": ("ICO", ".ico"),
    ".avif": ("WEBP", ".webp"),
}

TOOL_SPECS = {
    # Group 1: Convert image (5)
    "img_to_png":   {"group": "Convert image", "label": "PNG",         "has_options": True,  "desc": "Max width / Rotate",     "exts": IMAGE_EXTS, "exclude_exts": {".png"}, "min_files": 1, "ffmpeg": False},
    "img_to_jpeg":  {"group": "Convert image", "label": "JPEG",        "has_options": True,  "desc": "Quality (1-100)",        "exts": IMAGE_EXTS, "exclude_exts": {".jpg", ".jpeg"}, "min_files": 1, "ffmpeg": False},
    "img_to_webp":  {"group": "Convert image", "label": "WEBP",        "has_options": True,  "desc": "Quality (1-100)",        "exts": IMAGE_EXTS, "exclude_exts": {".webp"}, "min_files": 1, "ffmpeg": False},
    "img_to_gif":   {"group": "Convert image", "label": "GIF",         "has_options": True,  "desc": "Max width / Rotate",     "exts": IMAGE_EXTS, "exclude_exts": {".gif"}, "min_files": 1, "ffmpeg": False},
    "img_to_ico":   {"group": "Convert image", "label": "ICO",         "has_options": True,  "desc": "Square pad (16-256px)",  "exts": IMAGE_EXTS, "exclude_exts": {".ico"}, "min_files": 1, "ffmpeg": False},
    # Group 2: Edit image (7)
    "img_compress":    {"group": "Edit image", "label": "Compress",    "has_options": True,  "desc": "Quality / Target KB",    "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "img_resize":      {"group": "Edit image", "label": "Resize",      "has_options": True,  "desc": "Scale % / Fit box",      "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "img_crop":        {"group": "Edit image", "label": "Crop shape",  "has_options": True,  "desc": "1:1 / 16:9 / 4:3 / 9:16 / Custom", "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "img_rotate_flip": {"group": "Edit image", "label": "Rotate/Flip", "has_options": True,  "desc": "Angle & Mirror",         "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "img_effects":     {"group": "Edit image", "label": "Effects",     "has_options": True,  "desc": "Filter preset",          "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "img_watermark":   {"group": "Edit image", "label": "Watermark",   "has_options": True,  "desc": "Stamp text & corner",    "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "img_strip_meta":  {"group": "Edit image", "label": "Clean EXIF",  "has_options": False, "desc": "Instant EXIF strip",     "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    # Group 3: PDF & docs (13)
    "pdf_from_images":    {"group": "PDF & docs", "label": "Images->PDF", "has_options": True,  "desc": "Page size & Combine",  "exts": IMAGE_EXTS - {".ico"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_merge":          {"group": "PDF & docs", "label": "Merge PDF",   "has_options": False, "desc": "Combine in order",     "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 2, "ffmpeg": False},
    "pdf_split":          {"group": "PDF & docs", "label": "Split PDF",   "has_options": False, "desc": "One file per page",    "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_extract_pages":  {"group": "PDF & docs", "label": "Extract pg",  "has_options": True,  "desc": "Page range (1-3,5)",   "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_delete_pages":   {"group": "PDF & docs", "label": "Delete pg",   "has_options": True,  "desc": "Pages to remove",      "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_reverse":        {"group": "PDF & docs", "label": "Reverse",     "has_options": False, "desc": "Last page first",      "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_rotate":         {"group": "PDF & docs", "label": "Rotate PDF",  "has_options": True,  "desc": "Angle & Page range",   "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_compress":       {"group": "PDF & docs", "label": "Compress",    "has_options": True,  "desc": "Compression strength", "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_to_text":        {"group": "PDF & docs", "label": "PDF->Text",   "has_options": False, "desc": "Extract plain text",   "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_extract_images": {"group": "PDF & docs", "label": "PDF->Pics",   "has_options": False, "desc": "Save embedded images", "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_strip_meta":     {"group": "PDF & docs", "label": "Clean PDF",   "has_options": False, "desc": "Remove scripts/meta",  "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "pdf_to_word":        {"group": "PDF & docs", "label": "PDF->Word",   "has_options": False, "desc": "Convert to .docx",     "exts": PDF_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "word_to_pdf":        {"group": "PDF & docs", "label": "Word->PDF",   "has_options": False, "desc": "Convert .docx to PDF", "exts": DOCX_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    # Group 4: Video & audio (12)
    "media_to_mp4":    {"group": "Video & audio", "label": "MP4",        "has_options": False, "desc": "H.264 + AAC",           "exts": VIDEO_EXTS, "exclude_exts": {".mp4"}, "min_files": 1, "ffmpeg": True},
    "media_to_webm":   {"group": "Video & audio", "label": "WEBM",       "has_options": False, "desc": "VP9 + Opus",            "exts": VIDEO_EXTS, "exclude_exts": {".webm"}, "min_files": 1, "ffmpeg": True},
    "media_to_mp3":    {"group": "Video & audio", "label": "MP3",        "has_options": False, "desc": "Extract / convert MP3", "exts": MEDIA_EXTS, "exclude_exts": {".mp3"}, "min_files": 1, "ffmpeg": True},
    "media_to_m4a":    {"group": "Video & audio", "label": "M4A",        "has_options": False, "desc": "AAC audio",             "exts": MEDIA_EXTS, "exclude_exts": {".m4a"}, "min_files": 1, "ffmpeg": True},
    "media_to_wav":    {"group": "Video & audio", "label": "WAV",        "has_options": False, "desc": "Lossless PCM WAV",      "exts": MEDIA_EXTS, "exclude_exts": {".wav"}, "min_files": 1, "ffmpeg": True},
    "media_to_flac":   {"group": "Video & audio", "label": "FLAC",       "has_options": False, "desc": "Lossless FLAC",         "exts": MEDIA_EXTS, "exclude_exts": {".flac"}, "min_files": 1, "ffmpeg": True},
    "video_compress":  {"group": "Video & audio", "label": "Shrink Vid", "has_options": True,  "desc": "Strength & Max height", "exts": VIDEO_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": True},
    "audio_compress":  {"group": "Video & audio", "label": "Shrink Aud", "has_options": True,  "desc": "Bitrate (64-320 kbps)", "exts": AUDIO_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": True},
    "media_trim":      {"group": "Video & audio", "label": "Trim",       "has_options": True,  "desc": "Start & End time",      "exts": MEDIA_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": True},
    "video_to_gif":    {"group": "Video & audio", "label": "Video->GIF", "has_options": True,  "desc": "Width & Duration",      "exts": VIDEO_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": True},
    "video_mute":      {"group": "Video & audio", "label": "Mute Video", "has_options": False, "desc": "Strip audio track",     "exts": VIDEO_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": True},
    "video_frame_png": {"group": "Video & audio", "label": "Frame->PNG", "has_options": True,  "desc": "Capture timestamp",     "exts": VIDEO_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": True},
    # Group 5: Archives (4)
    "archive_zip":     {"group": "Archives", "label": "Make ZIP", "has_options": True,  "desc": "Compression level",     "exts": None, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "archive_targz":   {"group": "Archives", "label": "TAR.GZ",   "has_options": True,  "desc": "Compression level",     "exts": None, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "archive_gzip":    {"group": "Archives", "label": "GZIP",     "has_options": True,  "desc": "Compression level",     "exts": None, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "archive_extract": {"group": "Archives", "label": "Extract",  "has_options": False, "desc": "Unpack to new folder",  "exts": ARCHIVE_EXTS, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    # Group 6: Data & text (9)
    "json_pretty":   {"group": "Data & text", "label": "JSON Pretty", "has_options": False, "desc": "Indent 2 spaces",      "exts": {".json"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "json_minify":   {"group": "Data & text", "label": "JSON Min",    "has_options": False, "desc": "Compact JSON",         "exts": {".json"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "json_to_csv":   {"group": "Data & text", "label": "JSON->CSV",   "has_options": False, "desc": "Objects to table",     "exts": {".json"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "csv_to_json":   {"group": "Data & text", "label": "CSV->JSON",   "has_options": False, "desc": "Rows to JSON list",    "exts": {".csv", ".tsv"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "text_utf8":     {"group": "Data & text", "label": "To UTF-8",    "has_options": False, "desc": "Normalize UTF-8 + LF", "exts": TEXT_EXTS | {".csv", ".tsv", ".json", ".srt", ".vtt"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "text_crlf":     {"group": "Data & text", "label": "CRLF Lines",  "has_options": False, "desc": "Windows line endings", "exts": TEXT_EXTS | {".csv", ".tsv", ".json", ".srt", ".vtt"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "srt_to_vtt":    {"group": "Data & text", "label": "SRT->VTT",    "has_options": False, "desc": "SubRip to WebVTT",     "exts": {".srt"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "vtt_to_srt":    {"group": "Data & text", "label": "VTT->SRT",    "has_options": False, "desc": "WebVTT to SubRip",     "exts": {".vtt"}, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
    "checksums_all": {"group": "Data & text", "label": "Checksums",   "has_options": False, "desc": "MD5/SHA1/256/512",     "exts": None, "exclude_exts": set(), "min_files": 1, "ffmpeg": False},
}

DEFAULT_GROUP_ORDER = [
    "Convert image",
    "Edit image",
    "PDF & docs",
    "Video & audio",
    "Archives",
    "Data & text",
]


def is_tool_compatible(tool_id: str, staged_files: list[Path], ffmpeg_available: bool = True) -> bool:
    spec = TOOL_SPECS.get(tool_id)
    if not spec:
        return False
    if spec["ffmpeg"] and not ffmpeg_available:
        return False
    if not staged_files:
        return True
    if len(staged_files) < spec["min_files"]:
        return False
    exts = {p.suffix.lower() for p in staged_files}
    if any(p.name.lower().endswith((".tar.gz", ".tar.bz2", ".tar.xz")) for p in staged_files):
        exts.add(".tar")

    # Strict category matching: when user selects Images, show ONLY image tools;
    # when user selects ZIP/Archives, show ONLY Archive tools; PDF/DOCX -> PDF tools; Media -> Media tools.
    data_all_exts = TEXT_EXTS | {".csv", ".tsv", ".json", ".srt", ".vtt"}
    if spec["exts"] is None:
        if exts.issubset(ARCHIVE_EXTS):
            if spec["group"] != "Archives":
                return False
        elif (
            exts.issubset(IMAGE_EXTS)
            or exts.issubset(PDF_EXTS | DOCX_EXTS)
            or exts.issubset(MEDIA_EXTS)
            or exts.issubset(data_all_exts)
        ):
            # Allow archiving (Make ZIP, TAR.GZ, GZIP) for any selected files
            if spec["group"] != "Archives":
                return False
    else:
        if not exts.issubset(spec["exts"]):
            return False
    if spec["exclude_exts"] and exts.issubset(spec["exclude_exts"]):
        return False
    return True


def filter_and_rank_tools(
    staged_files: list[Path] | None = None,
    ffmpeg_available: bool = True,
    usage_counts: dict[str, int] | None = None,
) -> list[tuple[str, list[tuple[str, str, bool, str]]]]:
    """
    Filters all 50 tools to those compatible with `staged_files` and `ffmpeg_available`,
    and sorts both groups and tools within each group by descending usage count (breaking ties
    by canonical order).
    """
    files = staged_files or []
    usage = usage_counts or {}
    grouped: dict[str, list[tuple[int, int, tuple[str, str, bool, str]]]] = {g: [] for g in DEFAULT_GROUP_ORDER}

    for order_idx, (tid, spec) in enumerate(TOOL_SPECS.items()):
        if not is_tool_compatible(tid, files, ffmpeg_available):
            continue
        gname = spec["group"]
        u_count = int(usage.get(tid, 0))
        entry = (tid, spec["label"], spec["has_options"], spec["desc"])
        grouped[gname].append((-u_count, order_idx, entry))

    result_groups = []
    for g_idx, gname in enumerate(DEFAULT_GROUP_ORDER):
        items = grouped[gname]
        if not items:
            continue
        items.sort(key=lambda x: (x[0], x[1]))
        tools_sorted = [it[2] for it in items]
        group_usage = sum(int(usage.get(t[0], 0)) for t in tools_sorted) + int(usage.get(f"group:{gname}", 0))
        result_groups.append((-group_usage, g_idx, gname, tools_sorted))

    result_groups.sort(key=lambda x: (x[0], x[1]))
    return [(g[2], g[3]) for g in result_groups]


# ============================================================================
# 3. Transformation Engine for All 50 Tools + Security Guards
# ============================================================================

MAX_IMAGE_PIXELS = 40_000_000
MAX_COMBINED_PIXELS = 120_000_000
MAX_PDF_PAGES = 200
MAX_ARCHIVE_FILES = 2_000
MAX_OUTPUT_BYTES = 512 * 1024 * 1024


def load_safe_image(path: Path) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    im = Image.open(path)
    w, h = im.size
    if w <= 0 or h <= 0 or (w * h) > MAX_IMAGE_PIXELS:
        raise ValueError(f"Image dimensions {w}x{h} exceed 40 MP ceiling")
    if getattr(im, "n_frames", 1) > 1:
        raise ValueError("Animated multi-frame images are not supported by static image tools")
    im.load()
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    return im


def flatten_alpha_to_rgb(im: Image.Image, bg_color=(255, 255, 255)) -> Image.Image:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, bg_color)
        canvas.paste(rgba, mask=rgba.split()[3])
        return canvas
    return im.convert("RGB")


def save_image_in_format(im: Image.Image, out: Path, pil_fmt: str, quality: int = 90, square_icon: bool = True):
    fmt = pil_fmt.upper()
    q = max(1, min(100, int(quality)))
    if fmt in ("JPEG", "JPG"):
        flatten_alpha_to_rgb(im).save(out, "JPEG", quality=q, optimize=True, progressive=True)
    elif fmt == "PNG":
        if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            im = im.convert("RGBA")
        im.save(out, "PNG", optimize=True, compress_level=9)
    elif fmt == "WEBP":
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.mode else "RGB")
        im.save(out, "WEBP", quality=q, method=6)
    elif fmt == "GIF":
        if im.mode not in ("P", "L"):
            im = im.convert("RGBA").quantize(colors=256)
        im.save(out, "GIF", optimize=True)
    elif fmt == "BMP":
        flatten_alpha_to_rgb(im).save(out, "BMP")
    elif fmt == "TIFF":
        im.save(out, "TIFF", compression="tiff_deflate")
    elif fmt == "ICO":
        rgba = im.convert("RGBA")
        if square_icon:
            rgba.thumbnail((256, 256), Image.Resampling.LANCZOS)
            w, h = rgba.size
            side = max(w, h)
            sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            sq.paste(rgba, ((side - w) // 2, (side - h) // 2))
        else:
            sq = rgba.resize((256, 256), Image.Resampling.LANCZOS)
        sq.save(out, "ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        im.save(out, fmt)


def fit_to_size_bytes(im: Image.Image, fmt: str, target_bytes: int) -> bytes:
    work = flatten_alpha_to_rgb(im) if fmt == "JPEG" else (im.convert("RGBA") if "A" in im.mode else im.convert("RGB"))
    for _ in range(6):
        lo, hi = 5, 95
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            buf = io.BytesIO()
            if fmt == "JPEG":
                work.save(buf, "JPEG", quality=mid, optimize=True, progressive=True)
            else:
                work.save(buf, "WEBP", quality=mid, method=6)
            data = buf.getvalue()
            if len(data) <= target_bytes:
                best = data
                lo = mid + 1
            else:
                hi = mid - 1
        if best is not None:
            return best
        nw, nh = max(64, int(work.width * 0.8)), max(64, int(work.height * 0.8))
        if (nw, nh) == work.size:
            break
        work = work.resize((nw, nh), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    work.save(buf, "JPEG" if fmt == "JPEG" else "WEBP", quality=5)
    return buf.getvalue()


def parse_pages(spec: str, total: int) -> list[int]:
    spec_s = (spec or "").strip()
    if not spec_s:
        raise ValueError("Enter a page range such as 1-3, 5, 8-")
    pages: list[int] = []
    seen = set()
    for part in spec_s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a_s, b_s = part.split("-", 1)
            a = int(a_s) if a_s.strip() else 1
            b = int(b_s) if b_s.strip() else total
            if a < 1 or b < 1 or a > total:
                raise ValueError(f"Page range '{part}' is out of bounds (1-{total})")
            b = min(b, total)
            step = 1 if a <= b else -1
            for p in range(a, b + step, step):
                if p not in seen:
                    seen.add(p)
                    pages.append(p)
        else:
            p = int(part)
            if p < 1 or p > total:
                raise ValueError(f"Page {p} is out of bounds (1-{total})")
            if p not in seen:
                seen.add(p)
                pages.append(p)
    if not pages:
        raise ValueError("No valid pages selected")
    return pages


def parse_time(spec: str) -> float:
    s = str(spec or "").strip()
    if not s:
        return 0.0
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError(f"Invalid timestamp: {s}")
    total = 0.0
    for p in parts:
        val = float(p)
        if val < 0:
            raise ValueError(f"Negative time component in {s}")
        total = total * 60.0 + val
    return total


def strip_pdf_active_content(writer: pypdf.PdfWriter):
    """
    Sanitizes PDF catalog & pages against embedded scripts, auto-actions, forms, and malicious annotations
    while preserving safe external http(s)/mailto links without /Next chains.
    Aborts with ValueError if sanitization fails so half-sanitized files are never produced.
    """
    try:
        root = writer._root_object
        for dangerous_key in ("/OpenAction", "/AA", "/JavaScript", "/JS", "/AcroForm", "/Collection", "/XFA", "/Metadata", "/PieceInfo"):
            if dangerous_key in root:
                del root[dangerous_key]
        if "/Names" in root:
            try:
                names_obj = root["/Names"].get_object()
                for k in ("/JavaScript", "/JS", "/EmbeddedFiles"):
                    if k in names_obj:
                        del names_obj[k]
            except Exception:
                del root["/Names"]

        if "/Outlines" in root:
            try:
                outl = root["/Outlines"]
                if hasattr(outl, "get_object"):
                    outl = outl.get_object()
                stack = []
                if "/First" in outl:
                    stack.append(outl["/First"])
                visited = set()
                loop_guard = 0
                while stack and loop_guard < 5000:
                    loop_guard += 1
                    curr_ref = stack.pop()
                    ref_id = getattr(curr_ref, "idnum", None) if hasattr(curr_ref, "idnum") else id(curr_ref)
                    if ref_id in visited:
                        continue
                    visited.add(ref_id)
                    item = curr_ref.get_object() if hasattr(curr_ref, "get_object") else curr_ref
                    if isinstance(item, dict):
                        for k in ("/A", "/AA", "/SE"):
                            if k in item:
                                del item[k]
                        if "/First" in item:
                            stack.append(item["/First"])
                        if "/Next" in item:
                            stack.append(item["/Next"])
            except Exception:
                if "/Outlines" in root:
                    del root["/Outlines"]

        for page in writer.pages:
            for k in ("/AA", "/Metadata", "/PieceInfo"):
                if k in page:
                    del page[k]
            if "/Annots" in page:
                safe_annots = ArrayObject()
                raw_annots = page["/Annots"]
                if hasattr(raw_annots, "get_object"):
                    raw_annots = raw_annots.get_object()
                for ref in raw_annots:
                    annot = ref.get_object() if hasattr(ref, "get_object") else ref
                    if not isinstance(annot, dict):
                        continue
                    if "/AA" in annot:
                        del annot["/AA"]
                    subtype = str(annot.get("/Subtype", ""))
                    if subtype != "/Link":
                        continue
                    act = annot.get("/A")
                    if act is not None:
                        act_obj = act.get_object() if hasattr(act, "get_object") else act
                        if isinstance(act_obj, dict) and act_obj.get("/S") == "/URI" and "/Next" not in act_obj:
                            uri = str(act_obj.get("/URI", "")).strip().lower()
                            if uri.startswith(("http://", "https://", "mailto:")):
                                safe_annots.append(ref)
                if safe_annots:
                    page[NameObject("/Annots")] = safe_annots
                else:
                    del page["/Annots"]
    except Exception as e:
        raise ValueError(f"PDF active content sanitization failed: {e}") from e


def finalize_sanitized_pdf(writer: pypdf.PdfWriter) -> bytes:
    """
    Purges unreferenced orphaned objects (such as /Launch, /JavaScript, /GoToR)
    by writing sanitized pages to memory, re-reading with PdfReader, and copying
    only the clean pages into a fresh PdfWriter while preserving rotation.
    Enforces MAX_PDF_PAGES limit and prevents half-sanitized output.
    """
    strip_pdf_active_content(writer)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    reader = pypdf.PdfReader(buf)
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds page limit ({len(reader.pages)} > {MAX_PDF_PAGES})")
    clean_writer = pypdf.PdfWriter()
    for page in reader.pages:
        clean_writer.add_page(page)
    strip_pdf_active_content(clean_writer)
    out_buf = io.BytesIO()
    clean_writer.write(out_buf)
    return out_buf.getvalue()


_UNICODE_FONT_REGISTERED = None


def _get_unicode_font_for_pdf() -> tuple[str, str, bool]:
    """
    Finds and registers a Unicode TTF from %WINDIR%\\Fonts.
    Tries Nirmala UI, Segoe UI, Arial Unicode MS, Arial.
    Returns (font_name_regular, font_name_bold, is_fallback).
    """
    global _UNICODE_FONT_REGISTERED
    if _UNICODE_FONT_REGISTERED is not None:
        return _UNICODE_FONT_REGISTERED

    windir = Path(os.environ.get("WINDIR", os.environ.get("SYSTEMROOT", "C:\\Windows")))
    fonts_dir = windir / "Fonts"

    candidate_families = [
        ("NirmalaUI", ["Nirmala.ttc", "Nirmala.ttf"], ["NirmalaB.ttc", "NirmalaB.ttf", "Nirmala.ttc"]),
        ("SegoeUI", ["segoeui.ttf", "SegUIVar.ttf"], ["segoeuib.ttf"]),
        ("ArialUnicode", ["ARIALUNI.TTF"], ["ARIALUNI.TTF"]),
        ("Arial", ["arial.ttf"], ["arialbd.ttf"]),
    ]

    for fam_name, regs, bolds in candidate_families:
        for reg_fn in regs:
            reg_path = fonts_dir / reg_fn
            if reg_path.is_file():
                try:
                    reg_font_name = f"{fam_name}_Reg"
                    if reg_path.suffix.lower() == ".ttc":
                        pdfmetrics.registerFont(TTFont(reg_font_name, str(reg_path), subfontIndex=0))
                    else:
                        pdfmetrics.registerFont(TTFont(reg_font_name, str(reg_path)))

                    bold_font_name = reg_font_name
                    for bold_fn in bolds:
                        bold_path = fonts_dir / bold_fn
                        if bold_path.is_file():
                            try:
                                b_name = f"{fam_name}_Bold"
                                if bold_path.suffix.lower() == ".ttc":
                                    pdfmetrics.registerFont(TTFont(b_name, str(bold_path), subfontIndex=0))
                                else:
                                    pdfmetrics.registerFont(TTFont(b_name, str(bold_path)))
                                bold_font_name = b_name
                                break
                            except Exception:
                                pass

                    _UNICODE_FONT_REGISTERED = (reg_font_name, bold_font_name, False)
                    return _UNICODE_FONT_REGISTERED
                except Exception:
                    continue

    _UNICODE_FONT_REGISTERED = ("Helvetica", "Helvetica-Bold", True)
    return _UNICODE_FONT_REGISTERED


def recompress_pdf_images(writer: pypdf.PdfWriter, quality: int = 70, max_side: int = 1800):
    seen = set()
    for page in writer.pages:
        try:
            for img_file in page.images:
                try:
                    ref_id = None
                    if hasattr(img_file, "indirect_reference") and img_file.indirect_reference:
                        ref_id = getattr(img_file.indirect_reference, "idnum", None)
                    if ref_id is not None and ref_id in seen:
                        continue
                    if ref_id is not None:
                        seen.add(ref_id)
                    im = img_file.image
                    if im is None:
                        continue
                    w, h = im.size
                    if w < 32 or h < 32 or (w * h) > MAX_IMAGE_PIXELS:
                        continue
                    if max_side and max(w, h) > max_side:
                        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                    if im.mode not in ("RGB", "L", "RGBA"):
                        im = im.convert("RGB")
                    img_file.replace(im, quality=quality, optimize=True)
                except Exception:
                    continue
        except Exception:
            continue


def open_safe_pdf(path: Path) -> pypdf.PdfReader:
    reader = pypdf.PdfReader(str(path))
    if reader.is_encrypted:
        raise ValueError(f"{path.name} is password-protected")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"{path.name} has {len(reader.pages)} pages (maximum is {MAX_PDF_PAGES})")
    return reader


def execute_tool(tool_id: str, inputs: list[Path], options: dict | None = None) -> Path:
    opts = options or {}
    first = inputs[0]
    stem = first.stem

    # --- GROUP 1: CONVERT IMAGE (5) ---
    if tool_id in ("img_to_png", "img_to_jpeg", "img_to_webp", "img_to_gif", "img_to_ico"):
        im = load_safe_image(first)
        rot = int(opts.get("rotation", 0))
        if rot in (90, 180, 270):
            im = im.rotate(-rot, expand=True)
        max_side = int(opts.get("width", 0))
        if max_side > 0 and max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

        fmt_map = {
            "img_to_png": ("png", "PNG"),
            "img_to_jpeg": ("jpg", "JPEG"),
            "img_to_webp": ("webp", "WEBP"),
            "img_to_gif": ("gif", "GIF"),
            "img_to_ico": ("ico", "ICO"),
        }
        ext, pil_fmt = fmt_map[tool_id]
        out = resolve_clash_free_output(first, stem, ext)
        save_image_in_format(
            im, out, pil_fmt,
            quality=int(opts.get("quality", 90)),
            square_icon=bool(opts.get("square_icon", True)),
        )
        return out

    # --- Group 2: Edit Image (7) — Keep Original Image Format ---
    if tool_id == "img_compress":
        im = load_safe_image(first)
        pil_fmt, ext_dot = FORMAT_OF_SUFFIX.get(first.suffix.lower(), ("JPEG", ".jpg"))
        if pil_fmt == "BMP":
            pil_fmt, ext_dot = "PNG", ".png"
        max_side = int(opts.get("width", 0))
        if max_side > 0 and max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        qual = max(1, min(100, int(opts.get("quality", 75))))
        target_kb = int(opts.get("target_kb", 0))
        out = resolve_clash_free_output(first, stem, ext_dot.lstrip("."))
        if target_kb > 0 and pil_fmt in ("JPEG", "WEBP"):
            out.write_bytes(fit_to_size_bytes(im, pil_fmt, target_kb * 1024))
        elif pil_fmt == "PNG":
            if target_kb > 0:
                target_bytes = target_kb * 1024
                best_data = None
                for n_colors in (256, 128, 64, 32, 16):
                    buf = io.BytesIO()
                    q_im = im.convert("RGBA").quantize(colors=n_colors, method=Image.Quantize.FASTOCTREE)
                    q_im.save(buf, "PNG", optimize=True, compress_level=9)
                    data = buf.getvalue()
                    if len(data) <= target_bytes:
                        best_data = data
                        break
                    best_data = data
                out.write_bytes(best_data or b"")
            elif qual < 95:
                num_colors = 256 if qual >= 70 else (128 if qual >= 40 else 64)
                q_im = im.convert("RGBA").quantize(colors=num_colors, method=Image.Quantize.FASTOCTREE)
                q_im.save(out, "PNG", optimize=True, compress_level=9)
            else:
                save_image_in_format(im, out, pil_fmt, quality=qual)
        else:
            save_image_in_format(im, out, pil_fmt, quality=qual)
        return out

    if tool_id == "img_resize":
        im = load_safe_image(first)
        pil_fmt, ext_dot = FORMAT_OF_SUFFIX.get(first.suffix.lower(), ("PNG", ".png"))
        mode = opts.get("resize_mode", "fit")
        w = int(opts.get("width", 320))
        h = int(opts.get("height", 240))
        if mode == "percent":
            pct = max(1, min(400, int(opts.get("percent", 50)))) / 100.0
            nw, nh = max(1, round(im.width * pct)), max(1, round(im.height * pct))
            if nw * nh > MAX_IMAGE_PIXELS:
                raise ValueError("Resized dimensions exceed 40 MP ceiling")
            im = im.resize((nw, nh), Image.Resampling.LANCZOS)
        elif mode == "exact":
            nw, nh = max(1, w), max(1, h if h > 0 else w)
            if nw * nh > MAX_IMAGE_PIXELS:
                raise ValueError("Resized dimensions exceed 40 MP ceiling")
            im = im.resize((nw, nh), Image.Resampling.LANCZOS)
        elif mode == "fill":
            nw, nh = max(1, w), max(1, h if h > 0 else w)
            if nw * nh > MAX_IMAGE_PIXELS:
                raise ValueError("Resized dimensions exceed 40 MP ceiling")
            im = ImageOps.fit(im, (nw, nh), Image.Resampling.LANCZOS)
        else:
            im.thumbnail((max(1, w), max(1, h if h > 0 else w)), Image.Resampling.LANCZOS)
        out = resolve_clash_free_output(first, stem, ext_dot.lstrip("."))
        save_image_in_format(im, out, pil_fmt, quality=int(opts.get("quality", 90)))
        return out

    if tool_id == "img_crop":
        im = load_safe_image(first)
        pil_fmt, ext_dot = FORMAT_OF_SUFFIX.get(first.suffix.lower(), ("PNG", ".png"))
        if "box" in opts and isinstance(opts["box"], (list, tuple)) and len(opts["box"]) == 4:
            bx0, by0, bx1, by1 = [int(round(float(c))) for c in opts["box"]]
            x0 = max(0, min(im.width - 1, min(bx0, bx1)))
            y0 = max(0, min(im.height - 1, min(by0, by1)))
            x1 = max(x0 + 1, min(im.width, max(bx0, bx1)))
            y1 = max(y0 + 1, min(im.height, max(by0, by1)))
            im = im.crop((x0, y0, x1, y1))
        else:
            ratio_str = str(opts.get("ratio", "1:1"))
            if ":" in ratio_str:
                rw, rh = [int(x) for x in ratio_str.split(":")]
            else:
                rw, rh = 1, 1
            target_ratio = rw / rh
            cur_ratio = im.width / im.height
            if cur_ratio > target_ratio:
                new_w = max(1, round(im.height * target_ratio))
                x0 = (im.width - new_w) // 2
                im = im.crop((x0, 0, x0 + new_w, im.height))
            else:
                new_h = max(1, round(im.width / target_ratio))
                y0 = (im.height - new_h) // 2
                im = im.crop((0, y0, im.width, y0 + new_h))
        out = resolve_clash_free_output(first, stem, ext_dot.lstrip("."))
        save_image_in_format(im, out, pil_fmt, quality=int(opts.get("quality", 92)))
        return out

    if tool_id == "img_rotate_flip":
        im = load_safe_image(first)
        pil_fmt, ext_dot = FORMAT_OF_SUFFIX.get(first.suffix.lower(), ("PNG", ".png"))
        rot = int(opts.get("rotation", 90))
        if rot in (90, 180, 270):
            im = im.rotate(-rot, expand=True)
        flip = opts.get("flip", "none")
        if flip == "h":
            im = ImageOps.mirror(im)
        elif flip == "v":
            im = ImageOps.flip(im)
        out = resolve_clash_free_output(first, stem, ext_dot.lstrip("."))
        save_image_in_format(im, out, pil_fmt, quality=int(opts.get("quality", 92)))
        return out

    if tool_id == "img_effects":
        im = load_safe_image(first)
        pil_fmt, ext_dot = FORMAT_OF_SUFFIX.get(first.suffix.lower(), ("PNG", ".png"))
        alpha = im.split()[-1] if im.mode in ("RGBA", "LA") else None
        orig_rgb = im.convert("RGB")
        rgb = orig_rgb.copy()
        effect = opts.get("effect", "grayscale")
        strength = int(opts.get("strength", 100))
        strength = max(0, min(100, strength))
        t = strength / 100.0

        if effect == "grayscale":
            effect_rgb = ImageOps.grayscale(orig_rgb).convert("RGB")
            rgb = Image.blend(orig_rgb, effect_rgb, t)
        elif effect == "sepia":
            g = ImageOps.grayscale(orig_rgb)
            effect_rgb = ImageOps.colorize(g, "#2b1d0e", "#f5e1c0")
            rgb = Image.blend(orig_rgb, effect_rgb, t)
        elif effect == "invert":
            rgb = ImageOps.invert(orig_rgb)
        elif effect == "autocontrast":
            rgb = ImageOps.autocontrast(orig_rgb, cutoff=1)
        elif effect == "sharpen":
            factor = 1.0 + 1.0 * t
            rgb = ImageEnhance.Sharpness(orig_rgb).enhance(factor)
        elif effect == "blur":
            blur_px = float(opts.get("blur_px", 3.0))
            rgb = orig_rgb.filter(ImageFilter.GaussianBlur(radius=blur_px))
        elif effect == "bw":
            g = ImageOps.grayscale(orig_rgb)
            rgb = g.point(lambda p: 255 if p >= 128 else 0).convert("RGB")
        if alpha is not None and pil_fmt in ("PNG", "WEBP", "TIFF"):
            rgb = rgb.convert("RGBA")
            rgb.putalpha(alpha)
        out = resolve_clash_free_output(first, stem, ext_dot.lstrip("."))
        save_image_in_format(rgb, out, pil_fmt, quality=int(opts.get("quality", 92)))
        return out

    if tool_id == "img_watermark":
        im = load_safe_image(first)
        pil_fmt, ext_dot = FORMAT_OF_SUFFIX.get(first.suffix.lower(), ("PNG", ".png"))
        rgba = im.convert("RGBA")
        txt = str(opts.get("text", "Confidential")).strip()[:80] or "Dewdrop"
        pos = str(opts.get("position", "br"))
        overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        fsize = max(14, min(rgba.width, rgba.height) // 14)
        font = None
        for fp in (
            ROOT / "assets" / "segoeuib.ttf",
            ROOT / "assets" / "Roboto-Bold.ttf",
            Path("C:/Windows/Fonts/segoeuib.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/calibri.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ):
            if Path(fp).exists():
                try:
                    font = ImageFont.truetype(str(fp), size=fsize)
                    break
                except Exception:
                    pass
        if font is None:
            try:
                font = ImageFont.load_default(size=fsize)
            except TypeError:
                font = ImageFont.load_default()
        bbox = d.textbbox((0, 0), txt, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = max(12, min(rgba.width, rgba.height) // 25)
        positions = {
            "br": (rgba.width - tw - pad, rgba.height - th - pad),
            "bl": (pad, rgba.height - th - pad),
            "tr": (rgba.width - tw - pad, pad),
            "tl": (pad, pad),
            "c":  ((rgba.width - tw) // 2, (rgba.height - th) // 2),
        }
        tx, ty = positions.get(pos, positions["br"])
        d.text((tx + 2, ty + 2), txt, font=font, fill=(0, 0, 0, 145))
        d.text((tx, ty), txt, font=font, fill=(255, 255, 255, 215))
        merged = Image.alpha_composite(rgba, overlay)
        out = resolve_clash_free_output(first, stem, ext_dot.lstrip("."))
        save_image_in_format(merged, out, pil_fmt, quality=int(opts.get("quality", 92)))
        return out

    if tool_id == "img_strip_meta":
        im = load_safe_image(first)
        pil_fmt, ext_dot = FORMAT_OF_SUFFIX.get(first.suffix.lower(), ("PNG", ".png"))
        if im.mode == "P":
            clean = Image.frombytes(im.mode, im.size, im.tobytes())
            clean.putpalette(im.getpalette())
            if "transparency" in im.info:
                clean.info["transparency"] = im.info["transparency"]
        else:
            clean = Image.frombytes(im.mode, im.size, im.tobytes())
        clean.info.pop("exif", None)
        clean.info.pop("icc_profile", None)
        clean.info.pop("photoshop", None)
        out = resolve_clash_free_output(first, stem, ext_dot.lstrip("."))
        save_image_in_format(clean, out, pil_fmt, quality=92)
        return out

    # --- GROUP 3: PDF AND DOCUMENTS (13) ---
    if tool_id == "pdf_from_images":
        combine = bool(opts.get("combine", True))
        page_mode = str(opts.get("page", "A4"))
        rot = int(opts.get("rotation", 0))
        max_w = int(opts.get("width", 0))
        pages_rgb = []
        total_px = 0
        for p in inputs:
            im = load_safe_image(p)
            if rot in (90, 180, 270):
                im = im.rotate(-rot, expand=True)
            if max_w > 0 and max(im.size) > max_w:
                im.thumbnail((max_w, max_w), Image.Resampling.LANCZOS)
            rgb = flatten_alpha_to_rgb(im)
            total_px += rgb.width * rgb.height
            if total_px > MAX_COMBINED_PIXELS:
                raise ValueError("Combined images exceed 120 MP memory budget")
            if page_mode == "A4":
                a4_w, a4_h = (1754, 1240) if rgb.width > rgb.height else (1240, 1754)
                sheet = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
                fit = rgb.copy()
                fit.thumbnail((a4_w - 96, a4_h - 96), Image.Resampling.LANCZOS)
                sheet.paste(fit, ((a4_w - fit.width) // 2, (a4_h - fit.height) // 2))
                pages_rgb.append(sheet)
            else:
                pages_rgb.append(rgb)

        save_kwargs = {"resolution": 150.0}
        if "quality" in opts and opts["quality"] is not None:
            save_kwargs["quality"] = max(1, min(100, int(opts["quality"])))

        if combine or len(pages_rgb) == 1:
            out = resolve_clash_free_output(first, stem, "pdf")
            pages_rgb[0].save(out, "PDF", save_all=True, append_images=pages_rgb[1:], **save_kwargs)
            return out
        else:
            out_first = None
            for p_in, page_im in zip(inputs, pages_rgb):
                out = resolve_clash_free_output(p_in, p_in.stem, "pdf")
                page_im.save(out, "PDF", **save_kwargs)
                if out_first is None:
                    out_first = out
            return out_first

    if tool_id == "pdf_merge":
        writer = pypdf.PdfWriter()
        total_pages = 0
        for p in inputs:
            reader = open_safe_pdf(p)
            total_pages += len(reader.pages)
            if total_pages > MAX_PDF_PAGES:
                raise ValueError("Combined PDF exceeds 200-page safety limit")
            for page in reader.pages:
                writer.add_page(page)
        out = resolve_clash_free_output(first, f"{stem}_merged", "pdf")
        out.write_bytes(finalize_sanitized_pdf(writer))
        return out

    if tool_id == "pdf_split":
        reader = open_safe_pdf(first)
        out_first = None
        for idx, page in enumerate(reader.pages, start=1):
            w = pypdf.PdfWriter()
            w.add_page(page)
            out = resolve_clash_free_output(first, f"{stem}_page_{idx:03d}", "pdf")
            out.write_bytes(finalize_sanitized_pdf(w))
            if out_first is None:
                out_first = out
        return out_first

    if tool_id in ("pdf_extract_pages", "pdf_delete_pages"):
        reader = open_safe_pdf(first)
        n = len(reader.pages)
        selected = parse_pages(str(opts.get("pages", "1")), n)
        writer = pypdf.PdfWriter()
        if tool_id == "pdf_extract_pages":
            for pno in selected:
                writer.add_page(reader.pages[pno - 1])
        else:
            drop_set = set(selected)
            for idx in range(1, n + 1):
                if idx not in drop_set:
                    writer.add_page(reader.pages[idx - 1])
        if len(writer.pages) == 0:
            raise ValueError("Resulting PDF has 0 pages")
        out = resolve_clash_free_output(first, stem, "pdf")
        out.write_bytes(finalize_sanitized_pdf(writer))
        return out

    if tool_id == "pdf_reverse":
        reader = open_safe_pdf(first)
        writer = pypdf.PdfWriter()
        for page in reversed(reader.pages):
            writer.add_page(page)
        out = resolve_clash_free_output(first, stem, "pdf")
        out.write_bytes(finalize_sanitized_pdf(writer))
        return out

    if tool_id == "pdf_rotate":
        reader = open_safe_pdf(first)
        n = len(reader.pages)
        angle = int(opts.get("rotation", 90)) or 90
        pages_spec = str(opts.get("pages", "")).strip()
        target_set = set(parse_pages(pages_spec, n)) if pages_spec else set(range(1, n + 1))
        writer = pypdf.PdfWriter()
        for idx, page in enumerate(reader.pages, start=1):
            if idx in target_set:
                page.rotate(angle)
            writer.add_page(page)
        out = resolve_clash_free_output(first, stem, "pdf")
        out.write_bytes(finalize_sanitized_pdf(writer))
        return out

    if tool_id == "pdf_compress":
        reader = open_safe_pdf(first)
        orig_file_size = first.stat().st_size if first.exists() else 0
        level = int(opts.get("level", 2))
        q = int(opts.get("quality", 65 if level == 2 else (40 if level >= 3 else 85)))
        max_side = int(opts.get("max_side", 1600 if level == 2 else (1000 if level >= 3 else 2400)))

        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        for page in writer.pages:
            try:
                page.compress_content_streams()
            except Exception:
                pass
        try:
            writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
        except TypeError:
            try:
                writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
            except Exception:
                pass
        except Exception:
            pass
        if level >= 2:
            recompress_pdf_images(writer, quality=q, max_side=max_side)
        writer.add_metadata({})
        compressed_bytes = finalize_sanitized_pdf(writer)

        if orig_file_size > 0 and len(compressed_bytes) > orig_file_size:
            w_lossless = pypdf.PdfWriter()
            for page in reader.pages:
                w_lossless.add_page(page)
            for page in w_lossless.pages:
                try:
                    page.compress_content_streams()
                except Exception:
                    pass
            compressed_bytes = finalize_sanitized_pdf(w_lossless)

        out = resolve_clash_free_output(first, stem, "pdf")
        out.write_bytes(compressed_bytes)
        return out

    if tool_id == "pdf_to_text":
        reader = open_safe_pdf(first)
        chunks = [page.extract_text() or "" for page in reader.pages]
        out = resolve_clash_free_output(first, stem, "txt")
        out.write_text("\n\n".join(chunks), encoding="utf-8")
        return out

    if tool_id == "pdf_extract_images":
        reader = open_safe_pdf(first)
        saved = None
        for p_idx, page in enumerate(reader.pages, start=1):
            for i_idx, img_file in enumerate(page.images, start=1):
                ext = Path(img_file.name).suffix.lstrip(".") or "png"
                out = resolve_clash_free_output(first, f"{stem}_p{p_idx}_{i_idx}", ext)
                out.write_bytes(img_file.data)
                if saved is None:
                    saved = out
        if saved is None:
            out = resolve_clash_free_output(first, f"{stem}_images", "txt")
            out.write_text("No embedded raster images found in PDF.", encoding="utf-8")
            return out
        return saved

    if tool_id == "pdf_strip_meta":
        reader = open_safe_pdf(first)
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        out = resolve_clash_free_output(first, stem, "pdf")
        out.write_bytes(finalize_sanitized_pdf(writer))
        return out

    if tool_id == "pdf_to_word":
        reader = open_safe_pdf(first)
        document = docx.Document()
        total_chars = 0
        total_imgs = 0
        for p_idx, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            total_chars += len(text)
            if p_idx > 0 and (text or len(page.images) > 0):
                document.add_page_break()
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.isupper() and len(line) < 72:
                    document.add_heading(line, level=1)
                elif line.startswith(("- ", "* ", "• ")):
                    document.add_paragraph(line[2:].strip(), style="List Bullet")
                elif "|" in line and line.count("|") >= 2:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    tbl = document.add_table(rows=1, cols=len(cells))
                    for c_i, val in enumerate(cells):
                        tbl.rows[0].cells[c_i].text = val
                else:
                    document.add_paragraph(line)
            for img_file in getattr(page, "images", []):
                try:
                    im = Image.open(io.BytesIO(img_file.data))
                    im.load()
                    png_buf = io.BytesIO()
                    im.save(png_buf, "PNG")
                    png_buf.seek(0)
                    w_in = min(5.5, max(1.2, im.width / 96.0))
                    document.add_picture(png_buf, width=Inches(w_in))
                    total_imgs += 1
                except Exception:
                    continue
        if total_chars == 0 and total_imgs == 0:
            raise ValueError("No text layer detected in PDF (scanned PDFs without text layers are not supported)")
        out = resolve_clash_free_output(first, stem, "docx")
        document.save(str(out))
        return out

    if tool_id == "word_to_pdf":
        # 1. Pre-check zip container limits before loading with docx
        with zipfile.ZipFile(first, "r") as zf:
            infolist = zf.infolist()
            if len(infolist) > 2000:
                raise ValueError("Refused Word document: archive member count exceeds 2,000 files limit")
            total_uncompressed = 0
            for info in infolist:
                if info.file_size > 100 * 1024 * 1024:
                    raise ValueError(f"Refused Word document: entry '{info.filename}' exceeds 100 MiB uncompressed limit")
                total_uncompressed += info.file_size
                if "vbaproject.bin" in info.filename.lower():
                    raise ValueError("Refused Word document containing executable VBA macros")
            if total_uncompressed > 200 * 1024 * 1024:
                raise ValueError("Refused Word document: total uncompressed content exceeds 200 MiB limit")

            if "word/document.xml" in zf.namelist():
                xml_parser = lxml.etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
                doc_xml = lxml.etree.fromstring(zf.read("word/document.xml"), parser=xml_parser)
                for gs in doc_xml.xpath("//*[local-name()='gridSpan']"):
                    val_str = next((v for k, v in gs.attrib.items() if k == "val" or k.endswith("}val")), "")
                    try:
                        val = int(val_str)
                    except (ValueError, TypeError):
                        val = 999999
                    if val < 1 or val > 63:
                        raise ValueError("Refused Word document: table column span exceeds 63")
                for tg in doc_xml.xpath("//*[local-name()='tblGrid']"):
                    cols = tg.xpath("./*[local-name()='gridCol']")
                    if len(cols) > 63:
                        raise ValueError("Refused Word document: table column count exceeds 63")
                for tbl_elem in doc_xml.xpath("//*[local-name()='tbl']"):
                    rows = tbl_elem.xpath("./*[local-name()='tr']")
                    if len(rows) > 5000:
                        raise ValueError("Refused Word document: table row count exceeds 5,000")
                total_tcs = len(doc_xml.xpath("//*[local-name()='tc']"))
                if total_tcs > 200000:
                    raise ValueError("Refused Word document: total cell count exceeds 200,000")

        doc_obj = docx.Document(str(first))
        out = resolve_clash_free_output(first, stem, "pdf")

        # 2. Register Unicode font with Devanagari / CJK support if available
        reg_font, bold_font, is_fallback = _get_unicode_font_for_pdf()

        doc_template = SimpleDocTemplate(
            str(out),
            pagesize=A4,
            leftMargin=40,
            rightMargin=40,
            topMargin=40,
            bottomMargin=40,
        )

        styles = getSampleStyleSheet()
        base_style = ParagraphStyle(
            "WordBody",
            parent=styles["Normal"],
            fontName=reg_font,
            fontSize=10,
            leading=14,
            textColor=rl_colors.black,
        )
        h1_style = ParagraphStyle(
            "WordH1",
            parent=base_style,
            fontName=bold_font,
            fontSize=15,
            leading=19,
            spaceAfter=6,
        )
        h2_style = ParagraphStyle(
            "WordH2",
            parent=base_style,
            fontName=bold_font,
            fontSize=12,
            leading=16,
            spaceAfter=4,
        )
        bullet_style = ParagraphStyle(
            "WordBullet",
            parent=base_style,
            leftIndent=14,
            firstLineIndent=-10,
            spaceAfter=2,
        )
        warning_style = ParagraphStyle(
            "WordWarning",
            parent=base_style,
            fontName=reg_font,
            fontSize=9,
            leading=12,
            textColor=rl_colors.HexColor("#B45309"),
            spaceAfter=8,
        )

        story = []
        if is_fallback:
            story.append(Paragraph(
                "<b>Notice:</b> System Unicode fonts (Nirmala UI, Segoe UI, Arial) were not accessible. "
                "Fallen back to standard Helvetica. Extended Unicode / Indic characters may not render.",
                warning_style
            ))
            story.append(Spacer(1, 4))

        cumulative_image_pixels = 0
        placed_image_parts = set()
        r_embed_qn = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"

        # 3. Iterate elements in exact document body order
        for elem in doc_obj.element.body:
            tag = elem.tag.split("}")[-1]

            if tag == "p":
                p = docx.text.paragraph.Paragraph(elem, doc_obj)

                # Process inline images inside paragraph
                for blip in elem.xpath(".//a:blip"):
                    embed_id = blip.get(r_embed_qn)
                    if embed_id and embed_id in doc_obj.part.related_parts:
                        img_part = doc_obj.part.related_parts[embed_id]
                        if embed_id not in placed_image_parts:
                            placed_image_parts.add(embed_id)
                            try:
                                blob = img_part.blob
                                with Image.open(io.BytesIO(blob)) as test_im:
                                    iw, ih = test_im.size
                                if iw > 0 and ih > 0 and (iw * ih) <= MAX_IMAGE_PIXELS:
                                    if cumulative_image_pixels + (iw * ih) <= MAX_COMBINED_PIXELS:
                                        cumulative_image_pixels += iw * ih
                                        avail_w = doc_template.width
                                        avail_h = doc_template.height * 0.60
                                        scale = min(1.0, avail_w / max(1, iw), avail_h / max(1, ih))
                                        dw, dh = iw * scale, ih * scale
                                        story.append(RLImage(io.BytesIO(blob), width=dw, height=dh))
                                        story.append(Spacer(1, 4))
                            except Exception:
                                pass

                # Extract styled text runs
                run_elems = elem.xpath(".//w:r[not(ancestor::w:del)]")
                runs_xml = []
                plain_texts = []
                for r_elem in run_elems:
                    r = docx.text.run.Run(r_elem, p)
                    r_text = r.text
                    if not r_text:
                        continue
                    plain_texts.append(r_text)
                    txt_part = xml_escape(r_text)
                    if not txt_part:
                        continue
                    if r.bold and r.italic:
                        runs_xml.append(f"<b><i>{txt_part}</i></b>")
                    elif r.bold:
                        runs_xml.append(f"<b>{txt_part}</b>")
                    elif r.italic:
                        runs_xml.append(f"<i>{txt_part}</i>")
                    else:
                        runs_xml.append(txt_part)

                fallback_text = "".join(plain_texts).strip()
                if not fallback_text and not runs_xml:
                    continue

                full_xml = "".join(runs_xml) if runs_xml else xml_escape(fallback_text)
                style_name = (p.style.name if p.style else "").lower()

                if "heading 1" in style_name:
                    chosen_style = h1_style
                elif "heading" in style_name:
                    chosen_style = h2_style
                elif "list bullet" in style_name:
                    full_xml = f"&bull;  {full_xml}"
                    chosen_style = bullet_style
                else:
                    chosen_style = base_style

                story.append(Paragraph(full_xml, chosen_style))
                story.append(Spacer(1, 4))

            elif tag == "tbl":
                tbl = docx.table.Table(elem, doc_obj)
                tbl_data = []
                for tr in elem.xpath("./w:tr"):
                    row_cells = []
                    for tc in tr.xpath("./w:tc")[:63]:
                        c = docx.table._Cell(tc, tbl)
                        c_text = xml_escape(c.text.strip())
                        row_cells.append(Paragraph(c_text, base_style))
                    if row_cells:
                        tbl_data.append(row_cells)

                if tbl_data:
                    max_cols = max(len(r) for r in tbl_data)
                    for r in tbl_data:
                        while len(r) < max_cols:
                            r.append(Paragraph("", base_style))
                    plat_tbl = Table(tbl_data)
                    plat_tbl.setStyle(TableStyle([
                        ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#94A3B8")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ]))
                    story.append(plat_tbl)
                    story.append(Spacer(1, 6))

        # Check for unplaced images in document relationships
        for rel_id, rel in doc_obj.part.rels.items():
            if "image" in getattr(rel, "reltype", "") and rel_id not in placed_image_parts:
                placed_image_parts.add(rel_id)
                try:
                    blob = rel.target_part.blob
                    with Image.open(io.BytesIO(blob)) as test_im:
                        iw, ih = test_im.size
                    if iw > 0 and ih > 0 and (iw * ih) <= MAX_IMAGE_PIXELS:
                        if cumulative_image_pixels + (iw * ih) <= MAX_COMBINED_PIXELS:
                            cumulative_image_pixels += iw * ih
                            avail_w = doc_template.width
                            avail_h = doc_template.height * 0.60
                            scale = min(1.0, avail_w / max(1, iw), avail_h / max(1, ih))
                            dw, dh = iw * scale, ih * scale
                            story.append(RLImage(io.BytesIO(blob), width=dw, height=dh))
                            story.append(Spacer(1, 4))
                except Exception:
                    pass

        if not story or (is_fallback and len(story) == 1):
            story.append(Paragraph("(Empty Document)", base_style))

        doc_template.build(story)
        return out

    # --- GROUP 4: VIDEO AND AUDIO (12, FFMPEG LOCK-DOWN) ---
    if tool_id in (
        "media_to_mp4", "media_to_webm", "media_to_mp3", "media_to_m4a",
        "media_to_wav", "media_to_flac", "video_compress", "audio_compress",
        "media_trim", "video_to_gif", "video_mute", "video_frame_png"
    ):
        disallowed_exts = {".m3u8", ".ts", ".sdp", ".txt"}
        if first.suffix.lower() in disallowed_exts:
            raise ValueError(f"Refused media playlist or concat stream file '{first.name}': disallowed stream vector")

        is_audio_in = first.suffix.lower() in AUDIO_EXTS
        ext_map = {
            "media_to_mp4": "mp4",
            "media_to_webm": "webm",
            "media_to_mp3": "mp3",
            "media_to_m4a": "m4a",
            "media_to_wav": "wav",
            "media_to_flac": "flac",
            "video_compress": "mp4",
            "audio_compress": "mp3",
            "media_trim": "mp3" if is_audio_in else "mp4",
            "video_to_gif": "gif",
            "video_mute": "mp4",
            "video_frame_png": "png",
        }
        out = resolve_clash_free_output(first, stem, ext_map[tool_id])
        ff_bin = _find_ffmpeg()

        # Probe duration for dynamic timeout calculation and audio detection
        dur_sec = 0.0
        has_audio = True
        audio_is_aac = False
        audio_bitrate_kbps: int | None = None
        try:
            probe_cmd = _ffmpeg_base_cmd(ff_bin) + ["-i", str(first.resolve())]
            probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
            if "Audio:" not in probe.stderr:
                has_audio = False
            else:
                if "Audio: aac" in probe.stderr:
                    audio_is_aac = True
                ab_m = re.search(r"Audio:[^\n]*?(\d+)\s*kb/s", probe.stderr)
                if ab_m:
                    audio_bitrate_kbps = int(ab_m.group(1))
            dur_m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", probe.stderr)
            if dur_m:
                dur_sec = float(dur_m.group(1)) * 3600.0 + float(dur_m.group(2)) * 60.0 + float(dur_m.group(3))
        except Exception:
            pass

        timeout_sec = max(180, int(dur_sec * 4)) if dur_sec > 0 else 180

        cmd = _ffmpeg_base_cmd(ff_bin) + ["-y", "-threads", "2"]
        start_sec = parse_time(opts.get("start", "")) if opts.get("start") else 0.0
        end_sec = parse_time(opts.get("end", "")) if opts.get("end") else 0.0
        if start_sec > 0:
            cmd += ["-ss", f"{start_sec:.3f}"]
        if end_sec > 0:
            if end_sec <= start_sec:
                raise ValueError("End time must be greater than start time")
            cmd += ["-to", f"{end_sec:.3f}"]
        cmd += ["-i", str(first.resolve())]

        if tool_id == "media_to_mp4":
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"]
        elif tool_id == "video_compress":
            lvl = int(opts.get("level", 2))
            crf = {1: "24", 2: "28", 3: "34"}.get(lvl, "28")
            if "quality" in opts and int(opts["quality"]) != 85:
                q_val = max(1, min(100, int(opts["quality"])))
                crf = str(int(round(38 - (q_val / 100.0) * 20)))
            maxh = int(opts.get("maxh", 0))
            if maxh > 0:
                cmd += ["-vf", f"scale=-2:'min({maxh},ih)'"]
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p"]
            if has_audio:
                if audio_is_aac:
                    cmd += ["-c:a", "copy"]
                else:
                    a_br = min(audio_bitrate_kbps, 128) if audio_bitrate_kbps is not None else 128
                    cmd += ["-c:a", "aac", "-b:a", f"{a_br}k"]
            else:
                cmd += ["-an"]
            cmd += ["-movflags", "+faststart"]
        elif tool_id == "media_trim":
            if is_audio_in:
                cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"]
            else:
                cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
        elif tool_id == "media_to_webm":
            cmd += ["-c:v", "libvpx-vp9", "-b:v", "200k", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-c:a", "libopus"]
        elif tool_id in ("media_to_mp3", "audio_compress"):
            cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", f"{int(opts.get('bitrate', 128))}k"]
        elif tool_id == "media_to_m4a":
            cmd += ["-vn", "-c:a", "aac", "-b:a", "192k"]
        elif tool_id == "media_to_wav":
            cmd += ["-vn", "-c:a", "pcm_s16le"]
        elif tool_id == "media_to_flac":
            cmd += ["-vn", "-c:a", "flac"]
        elif tool_id == "video_to_gif":
            gw = max(120, min(800, int(opts.get("width", 320))))
            dur = max(1.0, min(30.0, float(opts.get("duration", 6.0))))
            cmd += [
                "-t", f"{dur:.2f}",
                "-vf", f"fps=12,scale={gw}:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
            ]
        elif tool_id == "video_mute":
            cmd += ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        elif tool_id == "video_frame_png":
            cmd += ["-frames:v", "1"]

        # If extracting audio from a video that has NO audio track (e.g. silent screen recording),
        # synthesize clean silence for video duration so output is never empty or failed
        if tool_id in ("media_to_mp3", "media_to_m4a", "media_to_wav", "media_to_flac") and not is_audio_in and not has_audio:
            syn_dur = max(0.5, dur_sec) if dur_sec > 0 else 2.0
            cmd = _ffmpeg_base_cmd(ff_bin) + [
                "-y", "-f", "lavfi", "-t", f"{syn_dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"
            ]
            if tool_id in ("media_to_mp3", "audio_compress"):
                cmd += ["-c:a", "libmp3lame", "-b:a", f"{int(opts.get('bitrate', 128))}k"]
            elif tool_id == "media_to_m4a":
                cmd += ["-c:a", "aac", "-b:a", "192k"]
            elif tool_id == "media_to_wav":
                cmd += ["-c:a", "pcm_s16le"]
            elif tool_id == "media_to_flac":
                cmd += ["-c:a", "flac"]

        cmd += ["--", str(out.resolve())]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            if out.exists():
                try:
                    out.unlink()
                except Exception:
                    pass
            raise RuntimeError(f"FFmpeg operation timed out after {timeout_sec}s for '{first.name}'")

        if res.returncode != 0:
            if out.exists() and out.stat().st_size == 0:
                try:
                    out.unlink()
                except Exception:
                    pass
            raise RuntimeError(f"FFmpeg rejected input or failed: {res.stderr.strip()}")
        if not out.exists() or out.stat().st_size == 0:
            if out.exists():
                try:
                    out.unlink()
                except Exception:
                    pass
            raise RuntimeError(f"FFmpeg produced empty output file for {first.name}")

        # Ensure video_compress never increases file size
        if tool_id == "video_compress" and out.exists():
            orig_sz = first.stat().st_size if first.exists() else 0
            maxh = int(opts.get("maxh", 0))
            if orig_sz > 0 and out.stat().st_size >= orig_sz:
                out.unlink(missing_ok=True)
                cmd_fb = _ffmpeg_base_cmd(ff_bin) + [
                    "-y", "-i", str(first.resolve()),
                ]
                if maxh > 0:
                    cmd_fb += ["-vf", f"scale=-2:'min({maxh},ih)'"]
                cmd_fb += [
                    "-c:v", "libx264", "-preset", "slow", "-crf", "36", "-pix_fmt", "yuv420p",
                ]
                if has_audio:
                    if audio_is_aac:
                        cmd_fb += ["-c:a", "copy"]
                    else:
                        fb_br = min(audio_bitrate_kbps, 96) if audio_bitrate_kbps is not None else 96
                        cmd_fb += ["-c:a", "aac", "-b:a", f"{fb_br}k"]
                else:
                    cmd_fb += ["-an"]
                cmd_fb += ["-movflags", "+faststart", "--", str(out.resolve())]
                try:
                    subprocess.run(cmd_fb, capture_output=True, text=True, timeout=timeout_sec)
                except subprocess.TimeoutExpired:
                    if out.exists():
                        try:
                            out.unlink()
                        except Exception:
                            pass
                    raise RuntimeError(f"FFmpeg fallback compression timed out after {timeout_sec}s")
                if not out.exists() or out.stat().st_size >= orig_sz:
                    if maxh == 0:
                        out.write_bytes(first.read_bytes())
                        set_last_notice("no size reduction possible")
                    else:
                        if not out.exists():
                            out.write_bytes(first.read_bytes())
        return out

    # --- GROUP 5: ARCHIVES (4) ---
    if tool_id == "archive_zip":
        lvl = {1: 1, 2: 6, 3: 9}.get(int(opts.get("level", 2)), 6)
        combine = bool(opts.get("combine", True))
        
        def _add_to_zip(zf: zipfile.ZipFile, p: Path):
            if p.is_dir():
                base_stem = sanitize_stem(p.name) or "folder"
                zf.writestr(zipfile.ZipInfo(f"{base_stem}/"), "")
                for root, dirs, files in os.walk(p):
                    rel_root = Path(root).relative_to(p)
                    for d in dirs:
                        rel_path = (rel_root / d).as_posix().lstrip("./")
                        arc_dir = f"{base_stem}/{rel_path}".rstrip("/") + "/"
                        zf.writestr(zipfile.ZipInfo(arc_dir), "")
                    for f in files:
                        fpath = Path(root) / f
                        rel_path = (rel_root / f).as_posix().lstrip("./")
                        arc_name = f"{base_stem}/{rel_path}"
                        zf.write(fpath, arcname=arc_name)
            else:
                zf.write(p, arcname=sanitize_stem(p.stem) + p.suffix)

        if combine or len(inputs) == 1:
            out = resolve_clash_free_output(first, stem, "zip")
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=lvl) as zf:
                for p in inputs:
                    _add_to_zip(zf, p)
            return out
        else:
            out_first = None
            for p in inputs:
                out = resolve_clash_free_output(p, p.stem, "zip")
                with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=lvl) as zf:
                    _add_to_zip(zf, p)
                if out_first is None:
                    out_first = out
            return out_first

    if tool_id == "archive_targz":
        lvl = {1: 1, 2: 6, 3: 9}.get(int(opts.get("level", 2)), 6)
        out = resolve_clash_free_output(first, stem, "tar.gz")
        with gzip.open(out, "wb", compresslevel=lvl) as gz_out:
            with tarfile.open(fileobj=gz_out, mode="w") as tf:
                for p in inputs:
                    if p.is_dir():
                        base_stem = sanitize_stem(p.name) or "folder"
                        for root, dirs, files in os.walk(p):
                            rel_root = Path(root).relative_to(p)
                            for f in files:
                                fpath = Path(root) / f
                                rel_path = (rel_root / f).as_posix().lstrip("./")
                                arc_name = f"{base_stem}/{rel_path}"
                                info = tf.gettarinfo(str(fpath), arcname=arc_name)
                                info.uid = 0
                                info.gid = 0
                                info.uname = ""
                                info.gname = ""
                                with open(fpath, "rb") as fobj:
                                    tf.addfile(info, fobj)
                    else:
                        info = tf.gettarinfo(str(p), arcname=sanitize_stem(p.stem) + p.suffix)
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        with open(p, "rb") as fobj:
                            tf.addfile(info, fobj)
        return out

    if tool_id == "archive_gzip":
        lvl = {1: 1, 2: 6, 3: 9}.get(int(opts.get("level", 2)), 6)
        out = resolve_clash_free_output(first, first.name, "gz")
        with open(first, "rb") as f_in, gzip.open(out, "wb", compresslevel=lvl) as f_out:
            shutil.copyfileobj(f_in, f_out)
        return out

    if tool_id == "archive_extract":
        out_folder = resolve_clash_free_output(first, stem, "")
        out_folder.mkdir(parents=True, exist_ok=True)
        try:
            name_l = first.name.lower()
            total_written = 0
            file_count = 0
            used_dest_paths: set[str] = set()

            def resolve_archive_dest(rel_name: str) -> Path:
                dest = sanitize_rel_path(rel_name, out_folder)
                dest_key = str(dest.resolve()).lower()
                if dest_key not in used_dest_paths:
                    used_dest_paths.add(dest_key)
                    return dest
                counter = 1
                parent = dest.parent
                stem_part = dest.stem
                suffix_part = dest.suffix
                while True:
                    cand = parent / f"{stem_part} ({counter}){suffix_part}"
                    cand_key = str(cand.resolve()).lower()
                    if cand_key not in used_dest_paths:
                        used_dest_paths.add(cand_key)
                        return cand
                    counter += 1

            if name_l.endswith(".zip"):
                with zipfile.ZipFile(first, "r") as zf:
                    for info in zf.infolist():
                        if info.flag_bits & 0x1:
                            raise ValueError("Refused password-protected archive entry")
                        mode_hi = (info.external_attr >> 16) & 0xFFFF
                        if stat.S_ISLNK(mode_hi) or stat.S_ISCHR(mode_hi) or stat.S_ISBLK(mode_hi) or stat.S_ISFIFO(mode_hi) or stat.S_ISSOCK(mode_hi):
                            raise ValueError(f"Refused symlink/device entry in zip: {info.filename}")
                        if info.is_dir():
                            continue
                        file_count += 1
                        if file_count > MAX_ARCHIVE_FILES:
                            raise ValueError("Archive exceeds 2,000 file count limit")
                        dest = resolve_archive_dest(info.filename)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info, "r") as src, open(dest, "wb") as dst:
                            while True:
                                chunk = src.read(65536)
                                if not chunk:
                                    break
                                total_written += len(chunk)
                                if total_written > MAX_OUTPUT_BYTES:
                                    raise ValueError("Archive extraction exceeded 512 MiB limit")
                                dst.write(chunk)
            elif name_l.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
                with tarfile.open(first, "r:*") as tf:
                    for member in tf.getmembers():
                        if member.isdir():
                            continue
                        if not member.isfile():
                            raise ValueError("Refused symlink/device entry inside tar archive")
                        file_count += 1
                        if file_count > MAX_ARCHIVE_FILES:
                            raise ValueError("Archive exceeds 2,000 file count limit")
                        dest = resolve_archive_dest(member.name)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        src = tf.extractfile(member)
                        if src:
                            with open(dest, "wb") as dst:
                                while True:
                                    chunk = src.read(65536)
                                    if not chunk:
                                        break
                                    total_written += len(chunk)
                                    if total_written > MAX_OUTPUT_BYTES:
                                        raise ValueError("Archive extraction exceeded 512 MiB limit")
                                    dst.write(chunk)
            elif name_l.endswith((".gz", ".bz2", ".xz")):
                opener = gzip.open if name_l.endswith(".gz") else (bz2.open if name_l.endswith(".bz2") else lzma.open)
                dest = resolve_archive_dest(sanitize_stem(first.stem))
                with opener(first, "rb") as src, open(dest, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        total_written += len(chunk)
                        if total_written > MAX_OUTPUT_BYTES:
                            raise ValueError("Decompression exceeded 512 MiB limit")
                        dst.write(chunk)
            else:
                raise ValueError(f"Unsupported archive format: {first.name}")
        except Exception:
            shutil.rmtree(out_folder, ignore_errors=True)
            raise
        return out_folder

    # --- GROUP 6: DATA AND TEXT (9) ---
    if tool_id == "json_pretty":
        txt, _ = read_text_smart(first)
        obj = json.loads(txt)
        out = resolve_clash_free_output(first, stem, "json")
        try:
            dumped = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
            out.write_text(dumped, encoding="utf-8")
        except UnicodeEncodeError:
            dumped = json.dumps(obj, indent=2, ensure_ascii=True) + "\n"
            out.write_text(dumped, encoding="utf-8")
        return out

    if tool_id == "json_minify":
        txt, _ = read_text_smart(first)
        obj = json.loads(txt)
        out = resolve_clash_free_output(first, stem, "json")
        try:
            dumped = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
            out.write_text(dumped, encoding="utf-8")
        except UnicodeEncodeError:
            dumped = json.dumps(obj, separators=(",", ":"), ensure_ascii=True)
            out.write_text(dumped, encoding="utf-8")
        return out

    if tool_id == "json_to_csv":
        txt, _ = read_text_smart(first)
        data = json.loads(txt)
        if isinstance(data, dict):
            list_keys = [k for k, v in data.items() if isinstance(v, list) and v and isinstance(v[0], dict)]
            if len(list_keys) > 1:
                raise ValueError(f"JSON has multiple list-of-objects values: {', '.join(list_keys)}")
            elif len(list_keys) == 1:
                data = data[list_keys[0]]
            else:
                data = [data]
        if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
            raise ValueError("JSON to CSV expects a non-empty list of objects")
        cols: list[str] = []
        seen_cols = set()
        for r in data:
            for k in r.keys():
                ks = str(k)
                if ks not in seen_cols:
                    seen_cols.add(ks)
                    cols.append(ks)

        csv_safe = bool(opts.get("csv_safe", True))
        def _sanitize_csv_cell(val):
            if not csv_safe:
                return val
            if isinstance(val, str) and val and val[0] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + val
            return val

        header_map = {col: _sanitize_csv_cell(col) for col in cols}
        out_cols = [header_map[col] for col in cols]

        out = resolve_clash_free_output(first, stem, "csv")
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=out_cols)
            writer.writeheader()
            for r in data:
                writer.writerow({
                    header_map[str(k)]: _sanitize_csv_cell(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                    for k, v in r.items()
                })
        return out

    if tool_id == "csv_to_json":
        csv.field_size_limit(MAX_TEXT_INPUT_BYTES)
        text, _ = read_text_smart(first)
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        f_in = io.StringIO(text)
        reader_raw = csv.reader(f_in, dialect=dialect)
        try:
            raw_header = next(reader_raw)
        except StopIteration:
            raw_header = []
        deduped_headers = []
        counts = {}
        for h in raw_header:
            h_name = h if h != "" else "col"
            if h_name in counts:
                counts[h_name] += 1
                deduped_headers.append(f"{h_name}_{counts[h_name]}")
            else:
                counts[h_name] = 1
                deduped_headers.append(h_name)
        reader = csv.DictReader(f_in, fieldnames=deduped_headers, dialect=dialect, restkey="_extra", restval="")
        data = [dict(r) for r in reader]
        out = resolve_clash_free_output(first, stem, "json")
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return out

    if tool_id == "text_utf8":
        txt, _ = read_text_smart(first)
        txt = txt.replace("\r\n", "\n").replace("\r", "\n")
        out_ext = first.suffix.lstrip(".") or "txt"
        out = resolve_clash_free_output(first, stem, out_ext)
        out.write_text(txt, encoding="utf-8")
        return out

    if tool_id == "text_crlf":
        txt, _ = read_text_smart(first)
        lines = txt.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out_ext = first.suffix.lstrip(".") or "txt"
        out = resolve_clash_free_output(first, stem, out_ext)
        out.write_bytes("\r\n".join(lines).encode("utf-8"))
        return out

    if tool_id == "srt_to_vtt":
        srt, _ = read_text_smart(first)
        srt = srt.replace("\r\n", "\n").replace("\r", "\n").strip()
        body = re.sub(r"(\d+:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt)
        out = resolve_clash_free_output(first, stem, "vtt")
        out.write_text("WEBVTT\n\n" + body.lstrip() + "\n", encoding="utf-8")
        return out

    if tool_id == "vtt_to_srt":
        vtt, _ = read_text_smart(first)
        vtt = vtt.replace("\r\n", "\n").replace("\r", "\n").strip()
        raw_blocks = [b.strip() for b in re.split(r"\n\s*\n", vtt) if b.strip()]

        def _norm_timestamp_part(ts: str) -> str:
            ts = ts.strip().replace(",", ".")
            m_short = re.match(r"^(\d+):(\d{2})\.(\d{3})$", ts)
            if m_short:
                tot_mm, ss, mmm = int(m_short.group(1)), m_short.group(2), m_short.group(3)
                hh = tot_mm // 60
                mm = tot_mm % 60
                return f"{hh:02d}:{mm:02d}:{ss},{mmm}"
            m_long = re.match(r"^(\d+):(\d{2}):(\d{2})\.(\d{3})$", ts)
            if m_long:
                hh, mm, ss, mmm = int(m_long.group(1)), m_long.group(2), m_long.group(3), m_long.group(4)
                return f"{hh:02d}:{mm}:{ss},{mmm}"
            return ts.replace(".", ",")

        cues: list[str] = []
        cue_idx = 1
        for b in raw_blocks:
            lines = [l.rstrip() for l in b.split("\n")]
            if not lines:
                continue
            first_line = lines[0].strip()
            # Skip WEBVTT header and NOTE/STYLE/REGION metadata blocks
            if first_line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                continue
            timing_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if timing_idx == -1:
                continue
            parts = lines[timing_idx].split("-->")
            if len(parts) != 2:
                continue
            start_str = _norm_timestamp_part(parts[0])
            end_tokens = parts[1].strip().split()
            if not end_tokens:
                continue
            end_str = _norm_timestamp_part(end_tokens[0])
            payload = "\n".join(lines[timing_idx + 1:])
            cues.append(f"{cue_idx}\n{start_str} --> {end_str}\n{payload}".rstrip())
            cue_idx += 1

        out = resolve_clash_free_output(first, stem, "srt")
        out.write_text(("\n\n".join(cues) if cues else "") + ("\n" if cues else ""), encoding="utf-8")
        return out

    if tool_id == "checksums_all":
        lines = []
        for p in inputs:
            h_md5 = hashlib.md5()
            h_sha1 = hashlib.sha1()
            h_sha256 = hashlib.sha256()
            h_sha512 = hashlib.sha512()
            sz = 0
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    sz += len(chunk)
                    h_md5.update(chunk)
                    h_sha1.update(chunk)
                    h_sha256.update(chunk)
                    h_sha512.update(chunk)
            lines.extend([
                f"File   : {p.name}",
                f"Size   : {sz:,} bytes",
                f"MD5    : {h_md5.hexdigest()}",
                f"SHA1   : {h_sha1.hexdigest()}",
                f"SHA256 : {h_sha256.hexdigest()}",
                f"SHA512 : {h_sha512.hexdigest()}",
                "",
            ])
        out = resolve_clash_free_output(first, f"{stem}_checksums", "txt")
        out.write_text("\n".join(lines), encoding="utf-8")
        return out

    raise ValueError(f"Unknown tool_id: {tool_id}")


# ============================================================================
# 4. SUBPROCESS WORKER ENTRY POINT (`--worker <job.json> <result.json>`)
# ============================================================================

MULTI_FILE_BATCH_TOOLS = {"pdf_merge", "pdf_from_images", "archive_zip", "archive_targz", "checksums_all"}


def _atomic_write_json(path: Path, payload: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def worker_main(job_file: str, result_file: str) -> int:
    """
    Runs a conversion batch in an isolated worker process, writing atomic live status JSON
    to `result_file` after each item so the UI shell can poll progress, suspend/resume the
    worker process via NtSuspendProcess/NtResumeProcess, or terminate it on Cancel.
    """
    res_path = Path(result_file)
    try:
        spec = json.loads(Path(job_file).read_text(encoding="utf-8"))
        tool_id = str(spec["tool_id"])
        files = [Path(p) for p in spec["files"]]
        opts = dict(spec.get("options") or {})
    except Exception as exc:
        _atomic_write_json(res_path, {
            "ok": False, "finished": True, "fraction": 1.0,
            "done": 0, "total": 1, "current": "",
            "outputs": [], "errors": [f"Invalid job specification: {exc}"],
            "fallback_used": False,
        })
        return 2

    outputs: list[str] = []
    errors: list[str] = []
    fallback_any = False
    set_last_notice("")

    is_single_call = (
        tool_id in MULTI_FILE_BATCH_TOOLS
        and not (tool_id in ("pdf_from_images", "archive_zip") and not opts.get("combine", True))
    )
    total = 1 if is_single_call else max(1, len(files))

    _atomic_write_json(res_path, {
        "ok": True, "finished": False, "fraction": 0.04,
        "done": 0, "total": total, "current": files[0].name if files else "",
        "outputs": [], "errors": [], "fallback_used": False,
    })

    if is_single_call:
        try:
            out_p = execute_tool(tool_id, files, opts)
            outputs.append(str(out_p))
            fallback_any = fallback_any or was_output_fallback_used()
        except Exception as exc:
            errors.append(f"{files[0].name if files else tool_id}: {exc}")
        _atomic_write_json(res_path, {
            "ok": len(errors) == 0, "finished": True, "fraction": 1.0,
            "done": 1, "total": 1, "current": files[0].name if files else "",
            "outputs": outputs, "errors": errors, "fallback_used": fallback_any,
            "notice": get_last_notice(),
        })
        return 0 if not errors else 1

    for idx, fpath in enumerate(files):
        _atomic_write_json(res_path, {
            "ok": True, "finished": False,
            "fraction": max(0.05, idx / float(total)),
            "done": idx, "total": total, "current": fpath.name, "current_path": str(fpath),
            "outputs": outputs, "errors": errors, "fallback_used": fallback_any,
        })
        try:
            out_p = execute_tool(tool_id, [fpath], opts)
            outputs.append(str(out_p))
            fallback_any = fallback_any or was_output_fallback_used()
        except Exception as exc:
            errors.append(f"{fpath.name}: {exc}")

    _atomic_write_json(res_path, {
        "ok": len(outputs) > 0, "finished": True, "fraction": 1.0,
        "done": total, "total": total, "current": files[-1].name if files else "",
        "outputs": outputs, "errors": errors, "fallback_used": fallback_any,
        "notice": get_last_notice(),
    })
    return 0 if len(outputs) > 0 else 1
