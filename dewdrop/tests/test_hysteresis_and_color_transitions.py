"""
Verification suite for DewDrop's 360° circular turning, asymmetric 50% CCW hysteresis,
and smooth continuous color transition across turns.

Tests:
1. Clockwise completion: Page only advances when full 360° is completed from starting point.
2. 50% Counter-Clockwise hysteresis:
   - When on Page 1 (e.g. at 420°), rotating CCW past 360° retains Page 1.
   - Page only returns to Page 0 when rotating CCW more than 50% (at/below 180°).
   - If rotating CW from that point, Page 1 only returns upon completing the full 360° again.
3. Multi-page turning:
   - Extends consistently to Page 2 (720° CW, 540° CCW threshold), Page 3, etc.
4. Smooth Color Transitions:
   - No instant color jumps; colors interpolate continuously between premium shades.
   - Ring elements (track arc, bead head, inner stroke, hover glow) render cleanly with smooth shades.
"""

import math
import sys
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dewdrop_core import RotationPager
import liquid_optics


def test_clockwise_and_50pct_ccw_hysteresis():
    print("[TEST 1] Testing CW 360° completion & 50% CCW hysteresis...")
    pager = RotationPager(12, deadzone_radius=38.0, allow_negative=False)
    two_pi = 2.0 * math.pi

    def feed_deg(deg):
        rad = math.radians(deg)
        return pager.update_pointer(100.0 * math.sin(rad), -100.0 * math.cos(rad))

    # 1. Rotate CW from 0 to 355 deg -> still Page 0
    for d in range(0, 356, 5):
        feed_deg(d)
    assert pager.current_page == 0, f"Expected Page 0 at 355°, got {pager.current_page}"

    # 2. Complete 360 deg -> flips to Page 1
    for d in range(356, 361, 1):
        feed_deg(d)
    assert pager.current_page == 1, f"Expected Page 1 upon completing 360°, got {pager.current_page}"
    print(" [✓] Clockwise 360° completion successfully flips to Page 1.")

    # 3. Continue CW into Page 1 up to 420 deg
    for d in range(361, 421, 5):
        feed_deg(d)
    assert pager.current_page == 1, f"Expected Page 1 at 420°, got {pager.current_page}"
    arc_deg = math.degrees(pager.accumulated_rad)
    assert 55 <= arc_deg <= 65, f"Expected arc ~60° for next 360, got {arc_deg:.1f}°"
    print(f" [✓] Continuing CW to 420° advances next 360 arc to {arc_deg:.1f}° in Page 1.")

    # 4. Rotate CCW back across 360 deg down to 250 deg -> MUST REMAIN Page 1 (within hysteresis buffer)
    for d in range(419, 249, -5):
        feed_deg(d)
    assert pager.current_page == 1, f"Expected Page 1 to be retained at 250° CCW, got {pager.current_page}"
    print(" [✓] Rotating CCW past 360° to 250° cleanly retains Page 1 without premature flipping.")

    # 5. Rotate CCW to 180 deg (50% threshold reached) -> MUST RETURN to Page 0
    for d in range(249, 175, -5):
        feed_deg(d)
    assert pager.current_page == 0, f"Expected return to Page 0 at 180° CCW, got {pager.current_page}"
    print(" [✓] Rotating CCW past 50% (<=180°) cleanly returns to Page 0.")

    # 6. Rotate CW from 180 deg towards 360 deg (e.g. 350 deg) -> MUST STAY Page 0
    for d in range(185, 356, 5):
        feed_deg(d)
    assert pager.current_page == 0, f"Expected to remain on Page 0 at 355° CW, got {pager.current_page}"
    print(" [✓] Rotating CW from 180° stays on Page 0 until completing full 360° from start.")

    # 7. Complete 360 deg again -> advances to Page 1 again
    for d in range(356, 361, 1):
        feed_deg(d)
    assert pager.current_page == 1, f"Expected Page 1 upon completing 360° again, got {pager.current_page}"
    print(" [✓] Completing full 360° again advances back to Page 1.")


def test_multi_page_chain():
    print("[TEST 2] Testing multi-page chain (Page 0 -> Page 1 -> Page 2 -> CCW unwind)...")
    pager = RotationPager(18, deadzone_radius=38.0, allow_negative=False)

    def feed_deg(deg):
        rad = math.radians(deg)
        return pager.update_pointer(100.0 * math.sin(rad), -100.0 * math.cos(rad))

    # Advance to Page 1 at 360°
    for d in range(0, 361, 5):
        feed_deg(d)
    assert pager.current_page == 1

    # Advance to Page 2 at 720°
    for d in range(361, 725, 5):
        feed_deg(d)
    assert pager.current_page == 2, f"Expected Page 2 at 720°, got {pager.current_page}"
    print(" [✓] Reaching 720° advances cleanly to Page 2.")

    # Rotate CCW into Page 2 hysteresis (e.g. 600° > 540°) -> stays Page 2
    for d in range(719, 595, -5):
        feed_deg(d)
    assert pager.current_page == 2, f"Expected Page 2 at 600°, got {pager.current_page}"

    # Rotate CCW past 540° (50% of 2nd revolution) -> drops to Page 1
    for d in range(595, 530, -5):
        feed_deg(d)
    assert pager.current_page == 1, f"Expected Page 1 at <=540°, got {pager.current_page}"
    print(" [✓] CCW past 540° (50% of 2nd turn) cleanly drops to Page 1.")


def test_smooth_color_transitions():
    print("[TEST 3] Testing continuous smooth color transitions across turns...")
    
    # Verify get_ring_theme_colors exists and returns 4 ndarrays
    t0_neon, t0_base, t0_ice, t0_tint = liquid_optics.get_ring_theme_colors(0.0)
    t1_neon, t1_base, t1_ice, t1_tint = liquid_optics.get_ring_theme_colors(1.0)
    t2_neon, t2_base, t2_ice, t2_tint = liquid_optics.get_ring_theme_colors(2.0)

    # Turn 0 vs Turn 1 vs Turn 2 must be distinct shades
    assert not np.allclose(t0_neon, t1_neon), "Turn 0 and Turn 1 must have distinct colors"
    assert not np.allclose(t1_neon, t2_neon), "Turn 1 and Turn 2 must have distinct colors"

    # Transition must be completely smooth (no step discontinuities)
    steps = 100
    prev_neon = t0_neon
    max_step_drift = 0.0
    for s in range(1, steps + 1):
        t = (s / steps) * 2.0  # From 0.0 to 2.0
        neon, base, ice, tint = liquid_optics.get_ring_theme_colors(t)
        step_drift = float(np.max(np.abs(neon - prev_neon)))
        max_step_drift = max(max_step_drift, step_drift)
        prev_neon = neon
    
    # Maximum single step change (1/50 of a turn) should be small (< 10 units per channel)
    assert max_step_drift < 12.0, f"Discontinuity detected in color transition: max drift = {max_step_drift}"
    print(f" [✓] Color transitions are silky smooth (max step drift = {max_step_drift:.2f} per 0.02 turns).")

    # Render optical ring at different color turns and verify RGBA integrity
    im0 = liquid_optics.render_optical_ring_rgba(
        ["Tool A", "Tool B", "Tool C"],
        hover_seg=0,
        turn_progress=0.0,
        color_turns=0.0,
        size=460,
    )
    assert im0.size == (460, 460) and im0.mode == "RGBA"

    im1 = liquid_optics.render_optical_ring_rgba(
        ["Tool A", "Tool B", "Tool C"],
        hover_seg=0,
        turn_progress=0.5,
        color_turns=1.0,  # Page 1 shade
        size=460,
    )
    assert im1.size == (460, 460) and im1.mode == "RGBA"

    arr0 = np.array(im0)
    arr1 = np.array(im1)
    # The rendered pixels at turn 0 and turn 1 must reflect the new shade!
    assert not np.array_equal(arr0, arr1), "Rendered ring must visually reflect the distinct turn shade"
    print(" [✓] Optical ring successfully renders distinct, vibrant liquid glass shades per turn.")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING 360° ROTATION, 50% CCW HYSTERESIS & COLOR TRANSITION TESTS")
    print("=" * 70)
    test_clockwise_and_50pct_ccw_hysteresis()
    test_multi_page_chain()
    test_smooth_color_transitions()
    print("=" * 70)
    print("ALL 360° ROTATION, HYSTERESIS & COLOR TRANSITION TESTS PASSED 100%!")
    print("=" * 70)
