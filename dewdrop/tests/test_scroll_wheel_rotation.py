import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dewdrop.dewdrop_core import RotationPager

def test_scroll_wheel_rotation():
    print("======================================================================")
    print("TESTING MOUSE ROLLER & TOUCHPAD SCROLL TO DEGREE ROTATION")
    print("======================================================================")

    # 12 items -> 2 pages of 6
    pager = RotationPager(total_items=12, deadzone_radius=28.0, allow_negative=False)

    print("[TEST 1] Testing normal mouse wheel scrolling (WHEEL_DELTA = 120)...")
    # 1 notch down (120 units) -> 30 degrees (pi/6 rad)
    new_page = pager.apply_scroll_delta(120.0, deg_per_notch=30.0)
    assert new_page is None, f"Page 0 should not advance on 1 notch, got {new_page}"
    expected_rad = math.radians(30.0)
    assert abs(pager.total_rad - expected_rad) < 1e-4, f"total_rad={pager.total_rad}, expected {expected_rad}"
    assert abs(pager.accumulated_rad - expected_rad) < 1e-4, f"accumulated_rad={pager.accumulated_rad}"
    print(f" [✓] 1 wheel notch (120 units) rotates ring by 30.0° ({pager.total_rad:.4f} rad).")

    # Scroll 11 more notches down (total 12 notches = 360°)
    for _ in range(11):
        new_page = pager.apply_scroll_delta(120.0, deg_per_notch=30.0)

    assert new_page == 1, f"Expected page 1 after 12 notches (360°), got {new_page}"
    assert pager.current_page == 1, f"Expected current_page == 1, got {pager.current_page}"
    print(" [✓] 12 wheel notches (360° total rotation) completes 1 full turn and advances to Page 1.")

    # Continue scrolling down into Page 1 (2 more notches = 60° into 2nd cycle)
    for _ in range(2):
        pager.apply_scroll_delta(120.0, deg_per_notch=30.0)
    expected_total = math.radians(420.0)
    expected_acc = math.radians(60.0)
    assert abs(pager.total_rad - expected_total) < 1e-3
    assert abs(pager.accumulated_rad - expected_acc) < 1e-3
    print(f" [✓] Continuing scroll down to 420° advances progress arc to {math.degrees(pager.accumulated_rad):.1f}° in Page 1.")

    print("\n[TEST 2] Testing scroll wheel CCW reverse with 50% hysteresis...")
    # Scroll CCW (scroll up = negative units) past 360° to 300° (4 notches CCW = -120°):
    # Total becomes 420° - 120° = 300° (still > 180°, so stays on Page 1)
    for _ in range(4):
        p = pager.apply_scroll_delta(-120.0, deg_per_notch=30.0)
    assert pager.current_page == 1, f"Page should remain 1 at 300° (>180° threshold), got {pager.current_page}"
    print(f" [✓] Scrolling up (CCW) past 360° to 300° cleanly retains Page 1 without premature flipping.")

    # Scroll CCW further past 180° threshold (e.g. 5 more notches CCW = -150° -> 150°)
    p = None
    for _ in range(5):
        res = pager.apply_scroll_delta(-120.0, deg_per_notch=30.0)
        if res is not None:
            p = res
    assert pager.current_page == 0, f"Page should return to 0 when scrolled CCW past 50% (180°), got {pager.current_page}"
    print(f" [✓] Scrolling up (CCW) past 50% (<=180°) cleanly returns to Page 0.")

    print("\n[TEST 3] Testing precision touchpad fine-grained continuous scroll...")
    pager_tp = RotationPager(total_items=18, deadzone_radius=28.0, allow_negative=False)
    # Touchpad delivers small micro-deltas, e.g. 10 units at a time
    for _ in range(36):
        pager_tp.apply_scroll_delta(10.0, deg_per_notch=30.0)
    # Total 36 * 10 = 360 units = 3 notches = 90.0°
    assert abs(pager_tp.total_rad - math.radians(90.0)) < 1e-3
    print(f" [✓] Touchpad micro-deltas accumulate seamlessly: 36x10 units = {math.degrees(pager_tp.total_rad):.1f}°.")

    print("======================================================================")
    print("ALL MOUSE ROLLER & TOUCHPAD ROTATION TESTS PASSED 100%!")
    print("======================================================================")

if __name__ == "__main__":
    test_scroll_wheel_rotation()
