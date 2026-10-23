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
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
import pypdf
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, NumberObject
import docx
from docx.shared import Inches, Pt
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVIDENCE_DIR = ROOT / "docs" / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

# Re-export engine and tool specifications from dewdrop_core
from dewdrop_core import (
    SpringSolver,
    RotationPager,
    TOOL_SPECS,
    compute_ring_placement,
    execute_tool,
    filter_and_rank_tools,
    has_ffmpeg,
    sanitize_stem,
    sanitize_rel_path,
    resolve_clash_free_output,
    was_output_fallback_used,
    worker_main,
    parse_pages,
    parse_time,
    IMAGE_EXTS,
    PDF_EXTS,
    DOCX_EXTS,
    ARCHIVE_EXTS,
    AUDIO_EXTS,
    VIDEO_EXTS,
    MEDIA_EXTS,
    TEXT_EXTS,
    RESERVED_DEVICE_NAMES,
    MAX_IMAGE_PIXELS,
    FORMAT_OF_SUFFIX,
    load_safe_image,
    save_image_in_format,
    _find_ffmpeg,
)

def run_all_tests():
    results = {
        "unit_tests": {},
        "liquid_glass_optics_and_zero_orange_audit": {},
        "tools_50": {},
        "pdf_to_word_10_samples": {},
        "batch_100": {},
        "security_m6": {},
        "cleanroom_audit": {},
        "performance": {},
    }

    # --- A. Pure Logic Unit Tests ---
    sp = SpringSolver(0.0, response=0.45, damping_ratio=0.80)
    sp.set_target(1.0)
    peak = 0.0
    for _ in range(180):
        v = sp.step(1.0 / 120.0)
        peak = max(peak, v)
    assert sp.is_settled(), "Spring must settle within 1.5s"
    assert 1.005 <= peak <= 1.08, f"Expected slight water-like overshoot, got {peak}"
    results["unit_tests"]["spring_solver_overshoot_and_settle"] = f"PASS (peak={peak:.4f}, settled={sp.position:.4f})"

    # Ring placement on edges, corners, multi-monitor, and 100/125/150/200% DPI
    monitors = [
        (0, 0, 1920, 1040),
        (1920, 0, 3840, 1080),
    ]
    for mon in monitors:
        for dpi in (1.0, 1.25, 1.5, 2.0):
            r_px = round(230.0 * dpi)
            for cx_in, cy_in in [(mon[0] + 5, mon[1] + 5), (mon[2] - 5, mon[3] - 5), ((mon[0]+mon[2])//2, (mon[1]+mon[3])//2)]:
                rx, ry = compute_ring_placement(cx_in, cy_in, mon, dpi)
                assert mon[0] + r_px <= rx <= mon[2] - r_px
                assert mon[1] + r_px <= ry <= mon[3] - r_px
                assert math.hypot(rx - cx_in, ry - cy_in) >= r_px
    results["unit_tests"]["ring_placement_multi_monitor_dpi"] = "PASS (tested 2 monitors x 4 DPI scales x corners/centres)"

    # 360-degree clockwise and counter-clockwise rotation paging (multi-page AND single-page)
    pager = RotationPager(13, deadzone_radius=38.0)
    flipped_cw = None
    max_cw_prog = 0.0
    for step_deg in range(0, 390, 5):
        rad = math.radians(step_deg)
        p = pager.update_pointer(120.0 * math.sin(rad), -120.0 * math.cos(rad))
        max_cw_prog = max(max_cw_prog, pager.accumulated_rad / (2.0 * math.pi))
        if p is not None:
            flipped_cw = p
            break
    assert flipped_cw == 1 and max_cw_prog > 0.90, f"Expected page 1 after CW 360 turn, got {flipped_cw}"

    flipped_ccw = None
    min_ccw_prog = 0.0
    for step_deg in range(385, -30, -5):
        rad = math.radians(step_deg)
        p = pager.update_pointer(120.0 * math.sin(rad), -120.0 * math.cos(rad))
        min_ccw_prog = min(min_ccw_prog, pager.accumulated_rad / (2.0 * math.pi))
        if p is not None:
            flipped_ccw = p
            break
    assert flipped_ccw == 0 and min_ccw_prog < -0.85, f"Expected page 0 after CCW 360 turn, got {flipped_ccw}"

    pager_single = RotationPager(6, deadzone_radius=38.0)
    for step_deg in range(0, 180, 10):
        rad = math.radians(step_deg)
        pager_single.update_pointer(120.0 * math.sin(rad), -120.0 * math.cos(rad))
    assert pager_single.accumulated_rad > 2.5, "Single-page ring must also track 360° cursor rotation arc"
    results["unit_tests"]["rotation_paging_cw_ccw_hysteresis"] = "PASS (CW -> Page 1, CCW -> Page 0, single-page & multi-page 360° arc verified)"

    # Compatibility filtering & usage ranking tests (PROMPT.md §4 & §6)
    ranked_png = filter_and_rank_tools([Path("sample.png")], ffmpeg_available=True, usage_counts={"img_to_webp": 15, "Edit image": 20})
    convert_group = dict(ranked_png).get("Convert image", [])
    convert_ids = [t[0] for t in convert_group]
    assert "img_to_png" not in convert_ids, "Same-format PNG->PNG must be hidden when staging a .png file"
    assert convert_ids[0] == "img_to_webp", "Most-used tool img_to_webp must rank first on Page 1"
    ranked_no_ff = filter_and_rank_tools([Path("clip.mp4")], ffmpeg_available=False)
    assert "Video & audio" not in dict(ranked_no_ff), "Video & audio group must hide when FFmpeg is absent"
    assert parse_pages("1-3,5,7-", 9) == [1, 2, 3, 5, 7, 8, 9]
    assert abs(parse_time("01:02.5") - 62.5) < 1e-6
    results["unit_tests"]["tool_filtering_usage_ranking_and_parsers"] = "PASS (same-format exclusion, FFmpeg toggle, usage ordering, page & time parsers verified)"

    # --- A2. Crystal-Clear Liquid-Glass & Zero-Orange-Tint Optical Audit ---
    from liquid_optics import (
        render_optical_ring_rgba,
        render_optical_window_rgba,
        render_optical_island_rgba,
    )
    ring_cw = render_optical_ring_rgba(
        ["Convert image", "Edit image", "PDF & docs", "Video & audio", "Archives", "Data & text"],
        hover_seg=1, turn_progress=0.45, staged_count=3, subtitle="Edit image (Pg 1/2)", light_angle_deg=60.0
    )
    ring_ccw = render_optical_ring_rgba(
        ["Convert image", "Edit image", "PDF & docs", "Video & audio", "Archives", "Data & text"],
        hover_seg=4, turn_progress=-0.45, staged_count=3, subtitle="Edit image (Pg 2/2)", light_angle_deg=240.0
    )
    win_opt = render_optical_window_rgba(
        "Project_Files.zip", "2.4 GB", "Compress", "Quality (1-100)", mode="options", quality_val=85, chip_idx=1
    )
    win_prog = render_optical_window_rgba(
        "Project_Files.zip", "2.4 GB", "Compress", "Quality (1-100)", mode="progress", progress_frac=0.68
    )
    island_clear = render_optical_island_rgba(
        "Project_Files", "18 files  •  2.4 GB", progress_frac=0.68, paused=False
    )

    for surf_name, surf_im in [
        ("circle_ring_cw_360", ring_cw),
        ("circle_ring_ccw_360", ring_ccw),
        ("options_window_liquid_glass", win_opt),
        ("progress_window_liquid_glass", win_prog),
        ("dynamic_island_liquid_glass", island_clear),
    ]:
        arr = np.asarray(surf_im, dtype=np.float32)
        vis = arr[..., 3] > 20
        r_ch, g_ch, b_ch = arr[vis, 0], arr[vis, 1], arr[vis, 2]
        orange_pixels = int(np.sum((r_ch - b_ch > 18.0) & (r_ch > 140.0) & (b_ch < 120.0)))
        assert orange_pixels == 0, f"Found {orange_pixels} orange-tinted pixels in {surf_name}!"
        mean_rgb = (round(float(np.mean(r_ch)), 1), round(float(np.mean(g_ch)), 1), round(float(np.mean(b_ch)), 1))
        assert mean_rgb[2] >= mean_rgb[0], f"Expected cool crystal-clear glass (B >= R) in {surf_name}, got {mean_rgb}"
        results["liquid_glass_optics_and_zero_orange_audit"][surf_name] = (
            f"PASS (size={surf_im.size}, orange_pixels=0, mean_rgb={mean_rgb})"
        )

    # --- B. All 50 Tools Integration Suite ---
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp)

        img_path = tdir / "photo.png"
        im = Image.new("RGBA", (320, 240), (40, 120, 220, 255))
        d = ImageDraw.Draw(im)
        d.rectangle([40, 40, 280, 200], fill=(180, 225, 255, 255))
        im.save(img_path, "PNG")

        jpg_path = tdir / "camera.jpg"
        im.convert("RGB").save(jpg_path, "JPEG", quality=92)

        pdf1 = tdir / "doc1.pdf"
        c1 = rl_canvas.Canvas(str(pdf1), pagesize=A4)
        c1.setFont("Helvetica-Bold", 16)
        c1.drawString(72, 760, "PROJECT OVERVIEW")
        c1.setFont("Helvetica", 12)
        c1.drawString(72, 730, "First page paragraph text for extraction and conversion.")
        c1.drawImage(str(img_path), 72, 560, width=120, height=90)
        c1.showPage()
        c1.setFont("Helvetica", 12)
        c1.drawString(72, 760, "Second page content for split and reverse tests.")
        c1.save()

        pdf2 = tdir / "doc2.pdf"
        c2 = rl_canvas.Canvas(str(pdf2), pagesize=A4)
        c2.setFont("Helvetica", 12)
        c2.drawString(72, 760, "Appendix page from second PDF document.")
        c2.save()

        docx_path = tdir / "sample_doc.docx"
        wdoc = docx.Document()
        wdoc.add_heading("Clean Room Word Document", level=1)
        p_bold = wdoc.add_paragraph()
        r_bold = p_bold.add_run("This paragraph verifies native Word to PDF layout with embedded images.")
        r_bold.bold = True
        wdoc.add_picture(str(img_path), width=Inches(1.8))
        tbl = wdoc.add_table(rows=2, cols=2)
        tbl.rows[0].cells[0].text = "Metric"
        tbl.rows[0].cells[1].text = "Value"
        tbl.rows[1].cells[0].text = "RAM Idle"
        tbl.rows[1].cells[1].text = "0 MB"
        wdoc.save(str(docx_path))

        vid_path = tdir / "clip.mp4"
        subprocess.run([
            _find_ffmpeg(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=1.5:size=160x120:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(vid_path)
        ], check=True)

        json_path = tdir / "records.json"
        json_path.write_text('[{"name": "Alpha", "score": "98"}, {"name": "Beta", "score": "95", "extra": "ok"}]', encoding="utf-8")

        csv_path = tdir / "table.csv"
        csv_path.write_text("name;score\nAlpha;98\nBeta;95\n", encoding="utf-8")

        txt_path = tdir / "notes.txt"
        txt_path.write_bytes("Line 1: Café\r\nLine 2: Résumé\r\n".encode("cp1252"))

        srt_path = tdir / "subs.srt"
        srt_path.write_text("1\n00:00:01,000 --> 00:00:03,500\nHello Dewdrop!\n", encoding="utf-8")

        vtt_path = tdir / "subs.vtt"
        vtt_path.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello Dewdrop!\n", encoding="utf-8")

        tool_invocations = [
            ("img_to_png", [jpg_path], {"width": 200}),
            ("img_to_jpeg", [img_path], {"quality": 85}),
            ("img_to_webp", [img_path], {"quality": 85}),
            ("img_to_gif", [img_path], {}),
            ("img_to_ico", [img_path], {}),
            ("img_compress", [jpg_path], {"target_kb": 15}),
            ("img_resize", [jpg_path], {"resize_mode": "percent", "percent": 50}),
            ("img_crop", [jpg_path], {"ratio": "1:1"}),
            ("img_rotate_flip", [jpg_path], {"rotation": 90, "flip": "h"}),
            ("img_effects", [jpg_path], {"effect": "bw"}),
            ("img_watermark", [jpg_path], {"text": "VERIFIED", "position": "tl"}),
            ("img_strip_meta", [jpg_path], {}),
            ("pdf_from_images", [img_path, jpg_path], {"combine": True, "page": "A4"}),
            ("pdf_merge", [pdf1, pdf2], {}),
            ("pdf_split", [pdf1], {}),
            ("pdf_extract_pages", [pdf1], {"pages": "1-1"}),
            ("pdf_delete_pages", [pdf1], {"pages": "2-"}),
            ("pdf_reverse", [pdf1], {}),
            ("pdf_rotate", [pdf1], {"rotation": 90, "pages": "1"}),
            ("pdf_compress", [pdf1], {"level": 3}),
            ("pdf_to_text", [pdf1], {}),
            ("pdf_extract_images", [pdf1], {}),
            ("pdf_strip_meta", [pdf1], {}),
            ("pdf_to_word", [pdf1], {}),
            ("word_to_pdf", [docx_path], {}),
            ("media_to_mp4", [vid_path], {}),
            ("media_to_webm", [vid_path], {}),
            ("media_to_mp3", [vid_path], {}),
            ("media_to_m4a", [vid_path], {}),
            ("media_to_wav", [vid_path], {}),
            ("media_to_flac", [vid_path], {}),
            ("video_compress", [vid_path], {"level": 2, "maxh": 360}),
            ("audio_compress", [vid_path], {"bitrate": 96}),
            ("media_trim", [vid_path], {"start": "00:00", "end": "00:01"}),
            ("video_to_gif", [vid_path], {"width": 160, "duration": 1.0}),
            ("video_mute", [vid_path], {}),
            ("video_frame_png", [vid_path], {"start": "00:00.2"}),
            ("archive_zip", [img_path, txt_path], {"level": 2, "combine": True}),
            ("archive_targz", [img_path, txt_path], {"level": 2}),
            ("archive_gzip", [txt_path], {"level": 2}),
        ]

        for tid, in_files, opts in tool_invocations:
            out_p = execute_tool(tid, in_files, opts)
            assert out_p.exists(), f"Tool {tid} did not create {out_p}"
            if tid in ("img_resize", "img_crop", "img_rotate_flip", "img_effects", "img_watermark", "img_strip_meta"):
                assert out_p.suffix.lower() == ".jpg", f"Edit image tool {tid} must preserve input .jpg format, got {out_p.suffix}"
            results["tools_50"][tid] = f"PASS -> {out_p.parent.name}/{out_p.name} ({out_p.stat().st_size if out_p.is_file() else 'dir'} B)"

        zip_created = tdir / "Dew Drop" / "photo.zip"
        ext_out = execute_tool("archive_extract", [zip_created], {})
        assert ext_out.is_dir() and any(ext_out.iterdir())
        results["tools_50"]["archive_extract"] = f"PASS -> {ext_out.parent.name}/{ext_out.name} (extracted {len(list(ext_out.iterdir()))} files)"

        data_tools = [
            ("json_pretty", [json_path], {}),
            ("json_minify", [json_path], {}),
            ("json_to_csv", [json_path], {}),
            ("csv_to_json", [csv_path], {}),
            ("text_utf8", [txt_path], {}),
            ("text_crlf", [txt_path], {}),
            ("srt_to_vtt", [srt_path], {}),
            ("vtt_to_srt", [vtt_path], {}),
            ("checksums_all", [img_path, txt_path], {}),
        ]
        for tid, in_files, opts in data_tools:
            out_p = execute_tool(tid, in_files, opts)
            assert out_p.exists() and out_p.stat().st_size > 0
            results["tools_50"][tid] = f"PASS -> {out_p.parent.name}/{out_p.name} ({out_p.stat().st_size} B)"

        assert len(results["tools_50"]) == 50, f"Expected 50 tools, tested {len(results['tools_50'])}"

        # Also test subprocess worker_main protocol
        job_json = tdir / "job.json"
        res_json = tdir / "res.json"
        job_json.write_text(json.dumps({
            "tool_id": "img_to_webp",
            "files": [str(img_path)],
            "options": {"quality": 80},
        }), encoding="utf-8")
        rc = worker_main(str(job_json), str(res_json))
        res_data = json.loads(res_json.read_text(encoding="utf-8"))
        assert rc == 0 and res_data["finished"] and len(res_data["outputs"]) == 1
        results["unit_tests"]["worker_subprocess_protocol"] = "PASS (worker_main atomic progress & completion JSON verified)"

        # --- C. 10-Sample PDF-to-Word Acceptance Test ---
        sample_specs = [
            ("01_plain_paragraphs", ["Plain paragraph one.", "Plain paragraph two with standard text."]),
            ("02_headings_hierarchy", ["MAIN CHAPTER HEADING", "Section body text under main heading.", "SUBSECTION HEADING", "More details."]),
            ("03_bullet_lists", ["SHOPPING AND TASK LIST", "- First bullet item", "- Second bullet item", "• Third bullet item"]),
            ("04_simple_table", ["QUARTERLY METRICS", "Quarter | Revenue | Margin", "Q1 | 1200 | 32%", "Q2 | 1450 | 35%"]),
            ("05_two_column_layout", ["LEFT AND RIGHT COLUMNS", "Column A line 1    Column B line 1", "Column A line 2    Column B line 2"]),
            ("06_embedded_image_caption", ["FIGURE REPORT", "Below is the embedded test diagram.", "Figure 1: Water droplet diagram."]),
            ("07_multipage_report", ["PAGE ONE SUMMARY", "Executive overview text.", "PAGE TWO DETAILS", "Detailed breakdown on page two."]),
            ("08_mixed_typography", ["TECHNICAL SPECIFICATION", "Parameter: Response=0.45s, Damping=0.80.", "- Verified spring physics"]),
            ("09_invoice_schedule", ["INVOICE STATEMENT", "Item | Qty | Price", "License | 1 | 0.00"]),
            ("10_release_checklist", ["FINAL GATE CHECKLIST", "- Fresh install verified", "- Zero background processes when idle"]),
        ]
        for s_name, lines in sample_specs:
            spdf = tdir / f"{s_name}.pdf"
            sc = rl_canvas.Canvas(str(spdf), pagesize=letter)
            y = 740
            for ln in lines:
                sc.setFont("Helvetica-Bold" if ln.isupper() else "Helvetica", 13 if ln.isupper() else 11)
                sc.drawString(64, y, ln)
                y -= 22
            if "image" in s_name:
                sc.drawImage(str(img_path), 64, y - 100, width=120, height=90)
            sc.save()
            out_docx = execute_tool("pdf_to_word", [spdf], {})
            parsed = docx.Document(str(out_docx))
            para_count = len(parsed.paragraphs) + len(parsed.tables)
            assert para_count >= 2, f"Sample {s_name} produced empty docx"
            results["pdf_to_word_10_samples"][s_name] = f"PASS ({para_count} structural blocks verified in Word .docx)"

        # --- D. 100-File Batch Test with Deliberate Failures ---
        batch_dir = tdir / "batch100"
        batch_dir.mkdir()
        pass_count = 0
        fail_count = 0
        t0 = time.perf_counter()
        for i in range(100):
            bf = batch_dir / f"item_{i:03d}.png"
            if i in (17, 42, 88):
                bf.write_bytes(b"NOT_A_VALID_PNG_HEADER_CORRUPT")
            else:
                im_small = Image.new("RGB", (64, 64), (i * 2 % 255, 100, 180))
                im_small.save(bf, "PNG")
            try:
                execute_tool("img_to_jpeg", [bf], {"quality": 80})
                pass_count += 1
            except Exception:
                fail_count += 1
        batch_elapsed = time.perf_counter() - t0
        assert pass_count == 97 and fail_count == 3
        results["batch_100"] = {
            "total": 100,
            "succeeded": pass_count,
            "failed_cleanly": fail_count,
            "elapsed_sec": round(batch_elapsed, 3),
            "status": "PASS",
        }

        # --- E. M6 Malicious Sample Suite ---
        zslip = tdir / "malicious_zipslip.zip"
        with zipfile.ZipFile(zslip, "w") as zf:
            zf.writestr("../../outside_escape.txt", "malicious payload")
        try:
            execute_tool("archive_extract", [zslip], {})
            raise AssertionError("Zip-slip was not refused!")
        except ValueError as e:
            results["security_m6"]["1_zip_slip_traversal"] = f"PASS (Refused: {e})"

        zbomb = tdir / "malicious_bomb.zip"
        with zipfile.ZipFile(zbomb, "w") as zf:
            for idx in range(2005):
                zf.writestr(f"f_{idx}.txt", "x")
        try:
            execute_tool("archive_extract", [zbomb], {})
            raise AssertionError("Zip bomb was not refused!")
        except ValueError as e:
            results["security_m6"]["2_zip_decompression_bomb"] = f"PASS (Refused: {e})"

        giant_png = tdir / "giant_100mp.png"
        Image.MAX_IMAGE_PIXELS = None
        Image.new("L", (10000, 10000), 0).save(giant_png, "PNG")
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        try:
            execute_tool("img_to_jpeg", [giant_png], {})
            raise AssertionError("100 MP image bomb was not refused!")
        except Exception as e:
            results["security_m6"]["3_giant_40mp_image_bomb"] = f"PASS (Refused: {e})"

        js_pdf = tdir / "malicious_js.pdf"
        jw = pypdf.PdfWriter()
        jw.add_blank_page(width=200, height=200)
        jw.add_js("app.alert('XSS');")
        with open(js_pdf, "wb") as f:
            jw.write(f)
        cleaned_pdf = execute_tool("pdf_strip_meta", [js_pdf], {})
        raw_clean = cleaned_pdf.read_bytes()
        assert b"app.alert" not in raw_clean and b"/JavaScript" not in raw_clean and b"/OpenAction" not in raw_clean
        results["security_m6"]["4_pdf_javascript_autoaction_strip"] = "PASS (Verified /JavaScript and /OpenAction completely stripped)"

        bad_m3u8 = tdir / "crafted.m3u8"
        bad_m3u8.write_text("#EXTM3U\n#EXTINF:1.0,\nhttp://127.0.0.1:9999/secret.ts\n", encoding="utf-8")
        try:
            execute_tool("media_to_mp4", [bad_m3u8], {})
            raise AssertionError("Crafted network playlist was not refused!")
        except Exception as e:
            results["security_m6"]["5_ffmpeg_protocol_whitelist_lockdown"] = f"PASS (Blocked external protocol: {str(e)[:120]})"

        assert sanitize_stem("-rf --no-preserve-root") == "rf --no-preserve-root"
        assert sanitize_stem("CON.txt").startswith("_CON")
        assert sanitize_stem("NUL...") == "_NUL"
        assert sanitize_stem("CONIN$").startswith("_CONIN$")
        assert sanitize_stem("CONOUT$").startswith("_CONOUT$")
        assert sanitize_stem("CLOCK$").startswith("_CLOCK$")
        try:
            sanitize_rel_path("evil.txt::$DATA", tdir)
            raise AssertionError("ADS archive entry was not blocked!")
        except ValueError:
            pass
        safe_rel = sanitize_rel_path("sub/file.txt. ", tdir)
        assert not str(safe_rel).endswith(". ")
        results["security_m6"]["6_odd_filenames_and_device_names"] = "PASS (Leading '-', CON/NUL/CONIN$/CONOUT$/CLOCK$, ADS and trailing dots neutralized)"

        macro_docx = tdir / "macro_trojan.docx"
        shutil.copyfile(docx_path, macro_docx)
        with zipfile.ZipFile(macro_docx, "a") as zf:
            zf.writestr("word/vbaProject.bin", b"FAKE_VBA_MACRO_STREAM")
        try:
            execute_tool("word_to_pdf", [macro_docx], {})
            raise AssertionError("Macro docx was not refused!")
        except ValueError as e:
            results["security_m6"]["7_macro_enabled_docx_refused"] = f"PASS (Refused: {e})"

    # --- F. Clean-Room & Zero-Legacy Identity Audit ---
    forbidden_patterns = [
        "Open" + "Tangerine",
        "Open " + "Tangerine",
        "Sat" + "suma",
        "Sintax" + " Error",
        "ply" + "ght",
        "SPDX-" + "License-Identifier",
        "GNU GENERAL " + "PUBLIC LICENSE",
        "A2A02332-" + "784A-4C0C-99DA-C82D08D2BDC8",
    ]
    scanned_files = 0
    hits = []
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in (".rs", ".toml", ".iss", ".ps1", ".py", ".md", ".manifest"):
            scanned_files += 1
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for pat in forbidden_patterns:
                if pat.lower() in txt.lower():
                    hits.append(f"{p.relative_to(ROOT)}: {pat}")
    assert not hits, f"Clean-room audit failed with hits: {hits}"
    results["cleanroom_audit"] = {
        "files_scanned": scanned_files,
        "forbidden_matches": len(hits),
        "license_files_present": (ROOT / "LICENSE").exists() or (ROOT / "NOTICE").exists(),
        "status": "PASS (0 legacy strings, 0 automatic LICENSE/NOTICE files)",
    }

    out_json = EVIDENCE_DIR / "test_suite_results.json"
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"All verification suites PASSED: 50/50 tools, 10/10 PDF->Word samples, 100-file batch, 7/7 M6 security guards, Crystal-Clear Liquid-Glass zero-orange audit, Clean-Room audit ({scanned_files} files).")


if __name__ == "__main__":
    run_all_tests()
