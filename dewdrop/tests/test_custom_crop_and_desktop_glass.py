#!/usr/bin/env python3
"""
Comprehensive Automated Test Suite:
1. Dynamic Installation Directory & Protected Permissions Handling
2. Custom Cropping (Presets Preservation, Freeform Dragging, Pixel-Accuracy, Quality)
3. Windows Desktop Liquid Glass Backdrop (Non-black, Real Wallpaper, Optics Integration)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dewdrop_core import execute_tool, TOOL_SPECS, get_appdata_dir
from dewdrop_runtime import (
    get_tool_ui_config,
    get_desktop_wallpaper_rect,
    capture_live_screen_rect_rgb,
    capture_screen_backdrop_rgb,
    draw_custom_crop_canvas,
    hit_test_crop_handle,
    render_options_or_progress_window_rgba,
)
from installer.installer_engine import get_default_install_dir


class TestInstallationAndPermissions(unittest.TestCase):
    def test_dynamic_install_dir_detection(self):
        """Verify dynamic detection of installation dir without hardcoding."""
        detected = get_default_install_dir()
        self.assertTrue(isinstance(detected, Path))
        self.assertTrue(detected.exists(), f"Detected install dir does not exist: {detected}")
        # When installed on D:\program files, it should resolve to D:\program files
        if Path("D:/program files").exists():
            self.assertEqual(detected.resolve(), Path("D:/program files").resolve())

    def test_appdata_and_user_storage(self):
        """Verify settings and logs use standard user AppData directory."""
        appdata = get_appdata_dir()
        self.assertTrue(appdata.exists())
        self.assertTrue(os.access(str(appdata), os.W_OK), "AppData directory must be writable")
        self.assertIn("Dewdrop", str(appdata))


class TestCustomCropping(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dewdrop_crop_test_"))
        # Create a test image with distinct quadrant colors (640x480)
        self.test_img_path = self.temp_dir / "Test_Image.png"
        arr = np.zeros((480, 640, 3), dtype=np.uint8)
        arr[:240, :320] = [255, 50, 50]    # Top-Left: Red
        arr[:240, 320:] = [50, 255, 50]    # Top-Right: Green
        arr[240:, :320] = [50, 50, 255]    # Bottom-Left: Blue
        arr[240:, 320:] = [255, 255, 50]   # Bottom-Right: Yellow
        im = Image.fromarray(arr, mode="RGB")
        im.save(self.test_img_path, "PNG")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_crop_presets_preserved(self):
        """Verify that all 4 existing presets (1:1, 16:9, 4:3, 9:16) work identically."""
        # 1:1 Square
        out_1_1 = execute_tool("img_crop", [self.test_img_path], options={"ratio": "1:1"})
        with Image.open(out_1_1) as im:
            self.assertEqual(im.size[0], im.size[1], "1:1 preset must produce square image")
            self.assertEqual(im.size, (480, 480))

        # 16:9 Wide
        out_16_9 = execute_tool("img_crop", [self.test_img_path], options={"ratio": "16:9"})
        with Image.open(out_16_9) as im:
            ratio = im.size[0] / im.size[1]
            self.assertAlmostEqual(ratio, 16.0 / 9.0, places=2)

        # 4:3 Photo
        out_4_3 = execute_tool("img_crop", [self.test_img_path], options={"ratio": "4:3"})
        with Image.open(out_4_3) as im:
            ratio = im.size[0] / im.size[1]
            self.assertAlmostEqual(ratio, 4.0 / 3.0, places=2)

        # 9:16 Tall
        out_9_16 = execute_tool("img_crop", [self.test_img_path], options={"ratio": "9:16"})
        with Image.open(out_9_16) as im:
            ratio = im.size[0] / im.size[1]
            self.assertAlmostEqual(ratio, 9.0 / 16.0, places=2)

    def test_custom_crop_with_explicit_pixel_box(self):
        """Verify Custom option crops the exact requested rectangular region in original pixel coordinates."""
        # Crop Top-Left quadrant: [0, 0, 320, 240] (Red quadrant)
        out_custom_tl = execute_tool("img_crop", [self.test_img_path], options={"ratio": "custom", "box": [0, 0, 320, 240]})
        with Image.open(out_custom_tl) as im:
            self.assertEqual(im.size, (320, 240))
            # Verify pixels are pure red
            crop_arr = np.array(im)
            self.assertTrue(np.all(crop_arr[:, :, 0] > 200))
            self.assertTrue(np.all(crop_arr[:, :, 1] < 100))

        # Crop Bottom-Right quadrant: [320, 240, 640, 480] (Yellow quadrant)
        out_custom_br = execute_tool("img_crop", [self.test_img_path], options={"ratio": "custom", "box": [320, 240, 640, 480]})
        with Image.open(out_custom_br) as im:
            self.assertEqual(im.size, (320, 240))
            crop_arr = np.array(im)
            self.assertTrue(np.all(crop_arr[:, :, 0] > 200))
            self.assertTrue(np.all(crop_arr[:, :, 1] > 200))
            self.assertTrue(np.all(crop_arr[:, :, 2] < 100))

    def test_custom_crop_ui_config_and_chips(self):
        """Verify UI config offers exactly 5 chips including 'Custom'."""
        ui_cfg = get_tool_ui_config("img_crop", quality_val=85, chip_idx=4, text_val="", crop_box=[50, 60, 400, 300])
        self.assertEqual(len(ui_cfg["chips"]), 5)
        self.assertEqual(ui_cfg["chips"][4], "Custom")
        self.assertEqual(ui_cfg["opts"]["ratio"], "custom")
        self.assertEqual(ui_cfg["opts"]["box"], [50, 60, 400, 300])

        # Preset switching: chip 0 must restore 1:1 with no box
        ui_cfg_0 = get_tool_ui_config("img_crop", quality_val=85, chip_idx=0, text_val="")
        self.assertEqual(ui_cfg_0["opts"]["ratio"], "1:1")
        self.assertNotIn("box", ui_cfg_0["opts"])

    def test_custom_crop_hit_testing_handles(self):
        """Verify hit testing detects all 8 handles and move zone accurately."""
        # disp_rect: (disp_x0, disp_y0, disp_w, disp_h, orig_w, orig_h)
        disp_rect = (50, 180, 800, 400, 1600, 800)
        norm_box = [0.2, 0.2, 0.8, 0.8]
        # Screen crop box:
        # cx0 = 50 + 0.2*800 = 210
        # cy0 = 180 + 0.2*400 = 260
        # cx1 = 50 + 0.8*800 = 690
        # cy1 = 180 + 0.8*400 = 500

        # Corners
        self.assertEqual(hit_test_crop_handle(210, 260, disp_rect, norm_box), "tl")
        self.assertEqual(hit_test_crop_handle(690, 260, disp_rect, norm_box), "tr")
        self.assertEqual(hit_test_crop_handle(210, 500, disp_rect, norm_box), "bl")
        self.assertEqual(hit_test_crop_handle(690, 500, disp_rect, norm_box), "br")

        # Edges
        self.assertEqual(hit_test_crop_handle(450, 260, disp_rect, norm_box), "t")
        self.assertEqual(hit_test_crop_handle(450, 500, disp_rect, norm_box), "b")
        self.assertEqual(hit_test_crop_handle(210, 380, disp_rect, norm_box), "l")
        self.assertEqual(hit_test_crop_handle(690, 380, disp_rect, norm_box), "r")

        # Move
        self.assertEqual(hit_test_crop_handle(450, 380, disp_rect, norm_box), "move")

        # Outside
        self.assertIsNone(hit_test_crop_handle(50, 50, disp_rect, norm_box))

    def test_draw_custom_crop_canvas_renders_clearly(self):
        """Verify draw_custom_crop_canvas composites properly without distortion."""
        base_im = Image.new("RGBA", (920, 600), (20, 20, 20, 255))
        norm_box = [0.1, 0.1, 0.9, 0.9]
        res_im, disp_r = draw_custom_crop_canvas(base_im, self.test_img_path, norm_box, 920, 600)
        self.assertEqual(res_im.size, (920, 600))
        self.assertEqual(len(disp_r), 6)
        disp_x0, disp_y0, disp_w, disp_h, orig_w, orig_h = disp_r
        self.assertEqual((orig_w, orig_h), (640, 480))
        self.assertTrue(disp_w > 100 and disp_h > 100)
        # Check aspect ratio preservation: disp_w / disp_h should equal 640 / 480 (1.333)
        self.assertAlmostEqual(disp_w / disp_h, 640.0 / 480.0, places=2)


class TestDesktopLiquidGlassBackdrop(unittest.TestCase):
    def test_desktop_wallpaper_discovery_and_cropping(self):
        """Verify desktop wallpaper is discovered, non-black, and cropped accurately."""
        wp = get_desktop_wallpaper_rect(100, 100, 720, 360)
        if wp is not None:
            self.assertEqual(wp.shape, (360, 720, 3))
            self.assertTrue(wp.max() > 0, "Wallpaper crop must not be pure black")
            mean_val = float(wp.mean())
            self.assertTrue(mean_val > 5.0, f"Wallpaper mean intensity should be vivid, got {mean_val}")

    def test_capture_live_screen_rect_non_black(self):
        """Verify capture_live_screen_rect_rgb returns non-black backdrop when over desktop."""
        live_bg = capture_live_screen_rect_rgb(100, 100, 400, 300)
        if live_bg is not None:
            self.assertEqual(live_bg.shape, (300, 400, 3))
            self.assertTrue(live_bg.max() > 0, "Screen capture must not be all-black")

    def test_capture_screen_backdrop_rgb_never_returns_black(self):
        """Verify capture_screen_backdrop_rgb always returns a valid, non-black array."""
        bg = capture_screen_backdrop_rgb(200, 200, 720, 360, record_mode=True)
        self.assertIsNotNone(bg)
        self.assertEqual(bg.shape, (360, 720, 3))
        self.assertTrue(bg.max() > 0, "Backdrop must have non-zero pixels")

    def test_liquid_optics_window_with_desktop_wallpaper(self):
        """Verify render_optical_window_rgba renders clear Liquid Glass over the real desktop backdrop."""
        bg = capture_screen_backdrop_rgb(100, 100, 720, 360)
        win_im = render_options_or_progress_window_rgba(
            file_title="Sample_Photo.png",
            file_subtitle="420 KB",
            tool_label="Crop shape",
            option_desc="1:1 / 16:9 / 4:3 / 9:16 / Custom",
            mode="options",
            quality_val=85,
            chip_idx=4,
            progress_frac=0.0,
            paused=False,
            status_line="Ready",
            detail_line="Drag handles to crop",
            w=720,
            h=360,
            backdrop_rgb=bg,
            chip_labels=["1:1 Square", "16:9 Wide", "4:3 Photo", "9:16 Tall", "Custom"],
        )
        self.assertEqual(win_im.size, (720, 360))
        arr = np.array(win_im)
        # Liquid glass alpha should be smooth and translucent
        self.assertTrue(arr[:, :, 3].max() > 200, "Window should have solid border/elements")
        # Center glass should be transparent/refractive, not pure black
        center_rgb = arr[100:200, 200:500, :3]
        self.assertTrue(center_rgb.max() > 20, "Window glass must not be black")


if __name__ == "__main__":
    unittest.main()
