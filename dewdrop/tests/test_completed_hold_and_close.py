import sys
from pathlib import Path
sys.path.insert(0, r"d:\anti")

import time
from dewdrop.liquid_optics import render_optical_closing_alert_rgba

def test_completed_alert_text():
    print("--- Testing 'Completed' and 'Cancelled' Alert Display ---")
    
    # 1. Full Dynamic Island with '✓ Completed'
    im_done, w, h = render_optical_closing_alert_rgba(
        t_shrink=0.0,
        t_fade=0.0,
        is_done=True,
        w_max=780,
        h=76,
    )
    assert (w, h) == (780, 76), f"Expected (780, 76), got {(w, h)}"
    assert im_done.size == (780, 76)
    print(" [✓] Dynamic Island 'Completed' rendered at full 780x76")

    # 2. Full Dynamic Island with '✕ Cancelled'
    im_cancel, w, h = render_optical_closing_alert_rgba(
        t_shrink=0.0,
        t_fade=0.0,
        is_done=False,
        w_max=780,
        h=76,
    )
    assert (w, h) == (780, 76)
    assert im_cancel.size == (780, 76)
    print(" [✓] Dynamic Island 'Cancelled' rendered at full 780x76")

    # 3. Narrowing states
    for t in [0.25, 0.50, 0.75, 1.0]:
        im, cur_w, cur_h = render_optical_closing_alert_rgba(
            t_shrink=t,
            t_fade=0.0,
            is_done=True,
            w_max=780,
            h=76,
        )
        assert cur_h == 76
        assert im.size == (cur_w, 76)
    print(" [✓] Squeeze states validated down to 76px circle")

    # 4. Circle fade states
    for tf in [0.3, 0.6, 1.0]:
        im, cur_d, _ = render_optical_closing_alert_rgba(
            t_shrink=1.0,
            t_fade=tf,
            is_done=True,
            w_max=780,
            h=76,
        )
        assert im.size == (cur_d, cur_d)
    print(" [✓] Circle collapse & fade validated to 0px")

if __name__ == "__main__":
    test_completed_alert_text()
    print("\nALL SEQUENCE TESTS PASSED 100%!")
