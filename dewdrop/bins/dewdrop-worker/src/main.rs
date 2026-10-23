#![cfg_attr(not(test), windows_subsystem = "windows")]

use dewdrop_engine::{JobRequest, ProgressEvent};
use std::env;
use std::fs;
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
    let args: Vec<String> = env::args().skip(1).collect();
    let Some(job_json_path) = args.first() else {
        std::process::exit(2);
    };
    let result_json_path = args
        .get(1)
        .cloned()
        .unwrap_or_else(|| format!("{}.result.json", job_json_path));

    if let Some(script) = locate_runtime_script() {
        if let Ok(status) = Command::new("python")
            .arg(&script)
            .arg("--worker")
            .arg(job_json_path)
            .arg(&result_json_path)
            .status()
        {
            std::process::exit(status.code().unwrap_or(1));
        }
    }

    let raw = match fs::read_to_string(job_json_path) {
        Ok(s) => s,
        Err(e) => {
            let evt = ProgressEvent {
                fraction: 0.0,
                bytes_processed: 0,
                bytes_total: 0,
                status_text: "Failed to read job specification".to_string(),
                finished: true,
                output_path: None,
                error: Some(e.to_string()),
            };
            println!("{}", serde_json::to_string(&evt).unwrap_or_default());
            std::process::exit(1);
        }
    };
    let req: JobRequest = match serde_json::from_str(&raw) {
        Ok(r) => r,
        Err(e) => {
            let evt = ProgressEvent {
                fraction: 0.0,
                bytes_processed: 0,
                bytes_total: 0,
                status_text: "Invalid job JSON".to_string(),
                finished: true,
                output_path: None,
                error: Some(e.to_string()),
            };
            println!("{}", serde_json::to_string(&evt).unwrap_or_default());
            std::process::exit(1);
        }
    };

    let err_evt = ProgressEvent {
        fraction: 0.0,
        bytes_processed: 0,
        bytes_total: 0,
        status_text: format!("Worker runtime unavailable for {}", req.tool_id),
        finished: true,
        output_path: None,
        error: Some("Python execution environment is not available to process job".to_string()),
    };
    println!("{}", serde_json::to_string(&err_evt).unwrap_or_default());
    std::process::exit(1);
}
