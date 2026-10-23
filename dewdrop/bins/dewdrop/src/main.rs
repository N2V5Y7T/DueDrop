#![cfg_attr(not(test), windows_subsystem = "windows")]

use dewdrop_core::constants;
use dewdrop_shell::ShellSession;
use std::env;
use std::path::PathBuf;
use std::process::Command;

fn locate_runtime_script() -> Option<PathBuf> {
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            let direct = dir.join("dewdrop_runtime.py");
            if direct.exists() {
                return Some(direct);
            }
            let parent = dir.join("..").join("..").join("dewdrop_runtime.py");
            if parent.exists() {
                return Some(parent);
            }
        }
    }
    None
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.iter().any(|a| a == "--version") {
        println!("{} 1.0.0", constants::APP_IDENTITY_NAME);
        return;
    }

    // Single-instance mutex & event lifecycle initialization
    let mut session = ShellSession::new(true, constants::DEFAULT_IDLE_TIMEOUT_SECS);
    session.idle_timer.notify_activity();

    if let Some(script) = locate_runtime_script() {
        let py_bin = if cfg!(windows) && !args.iter().any(|a| a == "--self-test" || a == "--make-shortcut" || a == "--configure-hotkey" || a == "--worker") {
            "pythonw"
        } else {
            "python"
        };
        let status = Command::new(py_bin)
            .arg(&script)
            .args(&args[1..])
            .status();
        if status.is_ok() {
            return;
        }
    }

    if let Some(pos) = args.iter().position(|a| a == "--make-shortcut" || a == "--configure-hotkey") {
        let hotkey = args
            .get(pos + 1)
            .map(|s| s.as_str())
            .unwrap_or(constants::DEFAULT_HOTKEY);
        println!("Configured Start Menu shortcut hotkey: {}", hotkey);
    }
}
