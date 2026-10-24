import math
import sys
import ctypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dewdrop.dewdrop_core import RotationPager

def test_scroll_window_proc_simulation():
    print("======================================================================")
    print("TESTING WM_MOUSEWHEEL & WM_MOUSEHWHEEL WINDOW MESSAGE INTEGRATION")
    print("======================================================================")

    # Simulate runtime state for ring menu
    pager = RotationPager(total_items=18, deadzone_radius=28.0, allow_negative=False)
    state = {
        "mode": "ring_groups",
        "ring_x": 500,
        "ring_y": 300,
        "page": 0,
        "pager": pager,
        "hover_seg": -1,
        "light_angle_deg": 135.0,
        "last_active": 0.0,
    }

    # Helper function matching dewdrop_runtime.py scroll handler
    def handle_scroll_msg(msg, wparam, lparam):
        WM_MOUSEWHEEL = 0x020A
        WM_MOUSEHWHEEL = 0x020E
        if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            raw_delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            if raw_delta == 0:
                return 0

            mode = state["mode"]
            if mode in ("ring_groups", "ring_tools"):
                has_more_options = (state["pager"].total_pages > 1)
                pt_scr_x = ctypes.c_short(lparam & 0xFFFF).value
                pt_scr_y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                dx = float(pt_scr_x) - (state["ring_x"] + 230.0)
                dy = float(pt_scr_y) - (state["ring_y"] + 230.0)
                r = math.hypot(dx, dy)

                if r <= 245.0:
                    if 92.0 <= r <= 220.0:
                        ang = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
                        seg = int(((ang + 30.0) % 360.0) // 60.0) % 6
                        state["hover_seg"] = seg

                    if has_more_options:
                        if msg == WM_MOUSEHWHEEL:
                            scroll_units = float(raw_delta)
                        else:
                            scroll_units = -float(raw_delta)

                        new_page = state["pager"].apply_scroll_delta(scroll_units, deg_per_notch=30.0)
                        if new_page is not None:
                            state["page"] = new_page

                        bead_deg = math.degrees(state["pager"].accumulated_rad)
                        state["light_angle_deg"] = (bead_deg + 270.0) % 360.0
            return 0

    # Cursor placed on Option 1 (Right side: ring_x + 230 + 130, ring_y + 230)
    cursor_x = 500 + 230 + 130  # 860
    cursor_y = 300 + 230        # 530
    lparam = (cursor_y << 16) | (cursor_x & 0xFFFF)

    # 1. Scroll 1 notch down (raw_delta = -120)
    wparam = (-120 & 0xFFFF) << 16
    handle_scroll_msg(0x020A, wparam, lparam)

    assert state["hover_seg"] == 1 or state["hover_seg"] == 2, f"Hover seg expected 1/2, got {state['hover_seg']}"
    assert abs(math.degrees(pager.total_rad) - 30.0) < 1e-2, f"Expected 30.0 deg, got {math.degrees(pager.total_rad)}"
    print(f" [✓] Cursor kept on option (seg {state['hover_seg']}): rolling 1 notch DOWN advances roller by 30.0°.")

    # 2. Scroll 11 more notches down (total 12 notches = 360°)
    for _ in range(11):
        handle_scroll_msg(0x020A, wparam, lparam)

    assert state["page"] == 1, f"Expected page 1 after full 360°, got {state['page']}"
    assert pager.current_page == 1
    print(f" [✓] Completed 360° turn via mouse roller: page advances to {state['page']}, option hover maintained.")

    # 3. Scroll 2 more notches into Page 1 (420° total)
    for _ in range(2):
        handle_scroll_msg(0x020A, wparam, lparam)
    assert abs(math.degrees(pager.total_rad) - 420.0) < 1e-2
    assert abs(math.degrees(pager.accumulated_rad) - 60.0) < 1e-2
    print(f" [✓] Continuing scroll into Page 1: progress arc advances to {math.degrees(pager.accumulated_rad):.1f}°.")

    # 4. Reverse scroll UP (raw_delta = +120)
    wparam_up = (120 & 0xFFFF) << 16
    # Reverse 4 notches (420° - 120° = 300° > 180° threshold)
    for _ in range(4):
        handle_scroll_msg(0x020A, wparam_up, lparam)
    assert state["page"] == 1, "Must stay on Page 1 due to 50% CCW hysteresis"
    print(f" [✓] Scrolling UP (CCW) past 360° to 300° cleanly retains Page 1.")

    # Reverse 5 more notches (300° - 150° = 150° <= 180° threshold)
    for _ in range(5):
        handle_scroll_msg(0x020A, wparam_up, lparam)
    assert state["page"] == 0, f"Must return to Page 0 past 50% CCW, got {state['page']}"
    print(f" [✓] Scrolling UP (CCW) past 50% threshold returns to Page 0.")

    # 5. Touchpad horizontal swipe test (WM_MOUSEHWHEEL 0x020E)
    wparam_h = (120 & 0xFFFF) << 16  # tilt right
    handle_scroll_msg(0x020E, wparam_h, lparam)
    assert abs(math.degrees(pager.total_rad) - 180.0) < 1e-2
    print(" [✓] Touchpad horizontal swipe (WM_MOUSEHWHEEL) accurately converts scroll length into roller degrees.")

    print("======================================================================")
    print("ALL WINDOW PROC SCROLL SIMULATION TESTS PASSED 100%!")
    print("======================================================================")

if __name__ == "__main__":
    test_scroll_window_proc_simulation()
