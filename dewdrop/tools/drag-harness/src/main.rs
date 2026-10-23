use dewdrop_core::WorkAreaRect;
use dewdrop_shell::ShellSession;
use std::path::PathBuf;

fn main() {
    let mut session = ShellSession::new(true, 120.0);
    let monitor = WorkAreaRect {
        left: 0,
        top: 0,
        right: 1920,
        bottom: 1040,
    };
    session.begin_file_drag(
        400,
        300,
        monitor,
        1.0,
        vec![PathBuf::from("sample_photo.png")],
    );
    println!(
        "Drag harness initialized ring at ({}, {}) with {} active groups",
        session.ring_center.0,
        session.ring_center.1,
        session.active_groups.len()
    );
}
