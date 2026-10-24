import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[2]
DEWDROP_DIR = REPO_ROOT / "dewdrop"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(DEWDROP_DIR) not in sys.path:
    sys.path.insert(0, str(DEWDROP_DIR))

import pypdf
from dewdrop.dewdrop_core import (
    execute_tool,
    resolve_clash_free_output,
    _find_ffmpeg,
    has_ffmpeg,
)


def create_test_scan_pdf(path: Path) -> int:
    """Creates a sample PDF document scan (2000x2600) with intricate vector-like content."""
    im = Image.new("RGB", (2000, 2600), color=(250, 248, 240))
    d = ImageDraw.Draw(im)
    for y in range(80, 2500, 25):
        d.line([(80, y), (1920, y)], fill=(30, 30, 40), width=3)
    for x in range(150, 1800, 120):
        d.rectangle([x, 400, x + 70, 2200], fill=(210, 110, 70))
    buf = io.BytesIO()
    im.save(buf, format="PDF")
    data = buf.getvalue()
    path.write_bytes(data)
    return len(data)


def test_pdf_compression_performance():
    temp_dir = Path(tempfile.mkdtemp(prefix="test_pdf_comp_"))
    try:
        pdf_path = temp_dir / "document_scan.pdf"
        orig_size = create_test_scan_pdf(pdf_path)
        print(f"[TEST] Created document scan PDF: {orig_size:,} bytes")

        # 1. Test Balanced compression (level=2)
        out_balanced = execute_tool("pdf_compress", [pdf_path], {"level": 2})
        b_size = out_balanced.stat().st_size
        b_ratio = (1.0 - (b_size / orig_size)) * 100.0
        print(f"[TEST] Balanced compression: {b_size:,} bytes ({b_ratio:.1f}% size reduction)")
        assert out_balanced.exists()
        assert b_size < orig_size, f"Balanced size {b_size} should be smaller than {orig_size}"
        assert b_ratio >= 40.0, f"Expected at least 40% reduction, got {b_ratio:.1f}%"

        # Verify output is valid PDF
        r_b = pypdf.PdfReader(str(out_balanced))
        assert len(r_b.pages) == 1

        # 2. Test Strongest compression (level=3)
        out_strong = execute_tool("pdf_compress", [pdf_path], {"level": 3})
        s_size = out_strong.stat().st_size
        s_ratio = (1.0 - (s_size / orig_size)) * 100.0
        print(f"[TEST] Strongest compression: {s_size:,} bytes ({s_ratio:.1f}% size reduction)")
        assert s_size <= b_size, f"Strongest size {s_size} should be <= balanced {b_size}"
        assert s_ratio >= 50.0, f"Expected at least 50% reduction, got {s_ratio:.1f}%"

        # Verify output is valid PDF
        r_s = pypdf.PdfReader(str(out_strong))
        assert len(r_s.pages) == 1

        print("--> PDF COMPRESSION PERFORMANCE TEST PASSED!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_pdf_compress_never_bloats():
    temp_dir = Path(tempfile.mkdtemp(prefix="test_pdf_bloat_"))
    try:
        # Create tiny text-only PDF that is already compact
        w = pypdf.PdfWriter()
        w.add_blank_page(width=300, height=300)
        p = temp_dir / "tiny.pdf"
        with open(p, "wb") as f:
            w.write(f)
        orig_size = p.stat().st_size

        out = execute_tool("pdf_compress", [p], {"level": 2, "quality": 70})
        out_size = out.stat().st_size
        print(f"[TEST] Tiny PDF orig={orig_size} bytes, compressed={out_size} bytes")
        assert out_size <= orig_size, f"Compressed size {out_size} must not exceed original {orig_size}"
        print("--> PDF NEVER-BLOATS TEST PASSED!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_destination_resolution_and_drive_handling():
    temp_dir = Path(tempfile.mkdtemp(prefix="test_dest_dir_"))
    try:
        # 1. Source file in arbitrary directory
        src_file = temp_dir / "music" / "song.mp3"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_bytes(b"dummy audio")

        out1 = resolve_clash_free_output(src_file, "song", "wav")
        expected_parent = (temp_dir / "music" / "Dew Drop").resolve()
        assert out1.parent.resolve() == expected_parent, f"Expected {expected_parent}, got {out1.parent.resolve()}"
        out1.write_bytes(b"dummy wav")
        print("[TEST] Correctly created Dew Drop in file's own folder")

        # 2. Existing Dew Drop folder is reused
        out2 = resolve_clash_free_output(src_file, "song_hq", "wav")
        assert out2.parent.resolve() == expected_parent, f"Expected {expected_parent}, got {out2.parent.resolve()}"
        print("[TEST] Reused existing Dew Drop folder without creating duplicates")

        # 3. File already inside Dew Drop folder -> no nested Dew Drop\Dew Drop
        in_dew_file = out1
        out3 = resolve_clash_free_output(in_dew_file, "song_remix", "flac")
        assert out3.parent.resolve() == expected_parent, f"Expected {expected_parent}, got {out3.parent.resolve()}"
        assert "Dew Drop\\Dew Drop" not in str(out3) and "Dew Drop/Dew Drop" not in str(out3)
        print("[TEST] Avoided nested Dew Drop\\Dew Drop folder")

        # 4. Folder named Dewdrop (case-insensitive, no space) is reused
        sub_folder = temp_dir / "photos"
        sub_folder.mkdir(parents=True, exist_ok=True)
        dewdrop_existing = sub_folder / "Dewdrop"
        dewdrop_existing.mkdir(parents=True, exist_ok=True)
        img_file = sub_folder / "photo.png"
        img_file.write_bytes(b"dummy image")
        out4 = resolve_clash_free_output(img_file, "photo", "jpg")
        assert out4.parent.resolve() == dewdrop_existing.resolve()
        print("[TEST] Reused existing Dewdrop (no space) folder")

        # 5. Output clash resolution (1), (2)
        out5_a = resolve_clash_free_output(src_file, "clip", "mp4")
        out5_a.write_bytes(b"clip 1")
        out5_b = resolve_clash_free_output(src_file, "clip", "mp4")
        assert out5_b.name == "clip (1).mp4", f"Expected 'clip (1).mp4', got {out5_b.name}"
        out5_b.write_bytes(b"clip 2")
        out5_c = resolve_clash_free_output(src_file, "clip", "mp4")
        assert out5_c.name == "clip (2).mp4", f"Expected 'clip (2).mp4', got {out5_c.name}"
        print("[TEST] Clash numbers (1), (2) resolved properly")

        print("--> DESTINATION RESOLUTION TEST PASSED!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_ffmpeg_detection():
    ff = _find_ffmpeg()
    print(f"[TEST] FFmpeg detected at: {ff}")
    assert ff is not None and len(ff) > 0
    assert has_ffmpeg(), "Expected has_ffmpeg() to return True"
    print("--> FFMPEG DETECTION TEST PASSED!")


if __name__ == "__main__":
    print("=== Running PDF Compression & Destination Tests ===")
    test_pdf_compression_performance()
    test_pdf_compress_never_bloats()
    test_destination_resolution_and_drive_handling()
    test_ffmpeg_detection()
    print("\nALL PDF COMPRESSION & DESTINATION TESTS PASSED 100%!")
