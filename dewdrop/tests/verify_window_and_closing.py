import sys
from pathlib import Path
sys.path.insert(0, r"d:\anti")

import numpy as np
from PIL import Image
from dewdrop.liquid_optics import (
    render_optical_window_rgba,
    render_optical_progress_window_rgba,
    render_optical_closing_alert_rgba,
    generate_vivid_desktop_backdrop,
)

def test_distinct_windows():
    print("--- 1. Testing Distinct Options vs Progress Window ---")
    bg_opt = generate_vivid_desktop_backdrop(720, 360)
    bg_prog = generate_vivid_desktop_backdrop(720, 290)

    # 1. Options window (numeric tool with slider)
    im_opt_slider = render_optical_window_rgba(
        file_title="Vacation.jpg",
        file_subtitle="1.8 MB",
        tool_label="JPEG",
        option_desc="Quality (1-100)",
        mode="options",
        quality_val=85,
        w=720, h=360,
        backdrop_rgb=bg_opt,
        show_slider=True,
        show_chips=True,
        chip_labels=["Smaller (72%)", "Balanced (85%)", "Best (95%)"],
    )
    assert im_opt_slider.size == (720, 360), f"Expected (720, 360), got {im_opt_slider.size}"
    print(f" [✓] Options Window (with slider): size = {im_opt_slider.size}")

    # 2. Options window (tool without slider - e.g. PNG)
    im_opt_noslider = render_optical_window_rgba(
        file_title="Document.pdf",
        file_subtitle="320 KB",
        tool_label="PNG",
        option_desc="Output options",
        mode="options",
        w=720, h=360,
        backdrop_rgb=bg_opt,
        show_slider=False,
        show_chips=False,
    )
    assert im_opt_noslider.size == (720, 360)
    print(f" [✓] Options Window (no slider): size = {im_opt_noslider.size}")

    # 3. Dedicated Progress Window - Running state
    im_prog_running = render_optical_progress_window_rgba(
        file_title="Vacation.jpg",
        file_subtitle="1.8 MB",
        tool_label="JPEG",
        progress_frac=0.45,
        paused=False,
        is_finished=False,
        status_line="Compressing JPEG...",
        detail_line="File 1 of 3  •  Vacation.jpg",
        w=720, h=290,
        backdrop_rgb=bg_prog,
    )
    assert im_prog_running.size == (720, 290), f"Expected (720, 290), got {im_prog_running.size}"
    print(f" [✓] Progress Window (running): size = {im_prog_running.size}")

    # 4. Dedicated Progress Window - Finished state
    im_prog_done = render_optical_progress_window_rgba(
        file_title="Vacation.jpg",
        file_subtitle="3 files  •  4.2 MB total",
        tool_label="JPEG",
        progress_frac=1.0,
        paused=False,
        is_finished=True,
        status_line="Done — Saved in Dew Drop",
        detail_line="All 3 files converted successfully",
        w=720, h=290,
        backdrop_rgb=bg_prog,
    )
    assert im_prog_done.size == (720, 290)
    print(f" [✓] Progress Window (finished): size = {im_prog_done.size}")

def test_closing_alert_sequence():
    print("--- 2. Testing Cinematic Alert Closing Sequence ---")
    
    # Phase 2: Ends squeeze inward (width 780 -> 76)
    steps = [0.0, 0.25, 0.50, 0.75, 1.0]
    widths = []
    for t in steps:
        im, cur_w, cur_h = render_optical_closing_alert_rgba(
            t_shrink=t,
            t_fade=0.0,
            is_done=True,
            w_max=780,
            h=76,
        )
        assert cur_h == 76, f"Height must remain 76, got {cur_h}"
        assert im.size == (cur_w, 76), f"Image size mismatch: {im.size} vs ({cur_w}, 76)"
        widths.append(cur_w)
        print(f" [✓] Squeeze t={t:.2f}: width = {cur_w}px, height = {cur_h}px")

    # Verify monotonic decrease down to exact 76x76 circle
    assert widths[0] == 780, f"Expected 780, got {widths[0]}"
    assert widths[-1] == 76, f"Expected 76 (circle), got {widths[-1]}"
    for i in range(len(widths) - 1):
        assert widths[i] >= widths[i+1], f"Width must decrease: {widths[i]} >= {widths[i+1]}"

    # Phase 3: Circle scaling down & fading out (76 -> 0)
    fade_steps = [0.0, 0.33, 0.66, 1.0]
    diameters = []
    for tf in fade_steps:
        if tf == 0.0:
            continue
        im, cur_d, _ = render_optical_closing_alert_rgba(
            t_shrink=1.0,
            t_fade=tf,
            is_done=True,
            w_max=780,
            h=76,
        )
        assert im.size == (cur_d, cur_d), f"Must be square/circle: {im.size}"
        diameters.append(cur_d)
        print(f" [✓] Fade t={tf:.2f}: circle diameter = {cur_d}px")

    assert diameters[0] > diameters[-1], "Circle diameter must shrink"
    assert diameters[-1] <= 6, f"Expected circle to shrink to <= 6px, got {diameters[-1]}"

    # Also test Cancelled / Aborted state
    im_cancel, cw, ch = render_optical_closing_alert_rgba(
        t_shrink=0.5,
        t_fade=0.0,
        is_done=False,
    )
    assert im_cancel.size == (cw, ch)
    print(f" [✓] Cancelled alert rendered: size = {im_cancel.size}")

if __name__ == "__main__":
    test_distinct_windows()
    test_closing_alert_sequence()
    print("\nALL VERIFICATION TESTS PASSED 100%!")
