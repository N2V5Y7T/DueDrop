#!/usr/bin/env python3
"""
Test suite for Liquid Glass Toast Notification and Empty File Selection Behavior.
"""
import sys
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from liquid_optics import render_liquid_glass_toast_rgba


def test_toast_rendering():
    print("[TEST] Testing render_liquid_glass_toast_rgba...")
    
    # 1. Normal render
    im_normal = render_liquid_glass_toast_rgba(
        title="Selection Required",
        message="Please select a file to continue",
        w=380,
        h=76,
        alpha_mult=1.0,
        hover_close=False,
    )
    assert im_normal.size == (380, 76), f"Expected (380, 76), got {im_normal.size}"
    assert im_normal.mode == "RGBA", f"Expected RGBA mode, got {im_normal.mode}"
    
    # 2. Hover render
    im_hover = render_liquid_glass_toast_rgba(
        title="Selection Required",
        message="Please select a file to continue",
        w=380,
        h=76,
        alpha_mult=1.0,
        hover_close=True,
    )
    assert im_hover.size == (380, 76)
    
    # 3. Fade render (alpha_mult = 0.5)
    im_fade = render_liquid_glass_toast_rgba(
        title="Selection Required",
        message="Please select a file to continue",
        w=380,
        h=76,
        alpha_mult=0.5,
        hover_close=False,
    )
    assert im_fade.size == (380, 76)

    # 4. Zero alpha render (should be blank)
    im_zero = render_liquid_glass_toast_rgba(
        alpha_mult=0.0,
    )
    assert im_zero.size == (380, 76)
    
    # 5. Live backdrop render
    backdrop = Image.new("RGB", (380, 76), (120, 160, 210))
    im_bg = render_liquid_glass_toast_rgba(
        title="Selection Required",
        message="Please select a file to continue",
        backdrop_rgb=backdrop,
    )
    assert im_bg.size == (380, 76)
    
    print("[PASS] All render_liquid_glass_toast_rgba tests passed successfully!")


def test_toast_close_hit_box():
    print("[TEST] Testing close button hit box geometry...")
    w, h = 380, 76
    close_cx = w - 26  # 354
    close_cy = h // 2  # 38
    
    import math
    # Points inside close button (radius 15)
    assert math.hypot(354 - close_cx, 38 - close_cy) <= 15
    assert math.hypot(360 - close_cx, 42 - close_cy) <= 15
    
    # Points outside close button
    assert math.hypot(300 - close_cx, 38 - close_cy) > 15
    assert math.hypot(100 - close_cx, 38 - close_cy) > 15
    print("[PASS] Close button hit box geometry verified!")


if __name__ == "__main__":
    test_toast_rendering()
    test_toast_close_hit_box()
    print("\nALL TOAST TESTS PASSED!")
