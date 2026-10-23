use dewdrop_core::constants;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRequest {
    pub tool_id: String,
    pub inputs: Vec<PathBuf>,
    pub output_dir: PathBuf,
    pub options: HashMap<String, serde_json::Value>,
    pub ffmpeg_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressEvent {
    pub fraction: f32,
    pub bytes_processed: u64,
    pub bytes_total: u64,
    pub status_text: String,
    pub finished: bool,
    pub output_path: Option<PathBuf>,
    pub error: Option<String>,
}

/// Validates an archive entry relative path against zip-slip (`..`), absolute roots, and depth limits.
pub fn validate_archive_entry_path(entry_path: &Path) -> Result<PathBuf, String> {
    let mut clean = PathBuf::new();
    let mut depth = 0usize;
    for comp in entry_path.components() {
        match comp {
            Component::Normal(os_str) => {
                let s = os_str.to_str().ok_or("Non-UTF8 path inside archive")?;
                if s.contains(':') || s.starts_with('-') {
                    return Err(format!("Refused unsafe archive path component: {}", s));
                }
                let safe = dewdrop_core::sanitize_stem(s);
                clean.push(safe);
                depth += 1;
                if depth > 20 {
                    return Err("Archive directory nesting exceeds 20 levels".to_string());
                }
            }
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err("Refused archive entry containing path traversal (..) or root prefix".to_string());
            }
        }
    }
    if clean.as_os_str().is_empty() {
        return Err("Empty archive entry path".to_string());
    }
    Ok(clean)
}

/// Builds a locked-down FFmpeg command vector with explicit protocol whitelisting and no shell expansion.
pub fn build_locked_ffmpeg_args(
    tool_id: &str,
    input_file: &Path,
    output_file: &Path,
    options: &HashMap<String, serde_json::Value>,
) -> Result<Vec<String>, String> {
    let input_str = input_file
        .canonicalize()
        .unwrap_or_else(|_| input_file.to_path_buf())
        .to_string_lossy()
        .to_string();
    let output_str = output_file.to_string_lossy().to_string();

    let mut args = vec![
        "-nostdin".to_string(),
        "-y".to_string(),
        "-hide_banner".to_string(),
        "-loglevel".to_string(),
        "error".to_string(),
        "-protocol_whitelist".to_string(),
        "file,pipe".to_string(),
        "-threads".to_string(),
        "2".to_string(),
        "-progress".to_string(),
        "pipe:1".to_string(),
    ];

    if let Some(start) = options.get("start").and_then(|v| v.as_str()) {
        if !start.trim().is_empty() {
            args.push("-ss".to_string());
            args.push(start.trim().to_string());
        }
    }
    if let Some(end) = options.get("end").and_then(|v| v.as_str()) {
        if !end.trim().is_empty() {
            args.push("-to".to_string());
            args.push(end.trim().to_string());
        }
    }

    args.push("-i".to_string());
    args.push(input_str);

    match tool_id {
        "media_to_mp4" => {
            args.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "22", "-c:a", "aac", "-b:a", "192k"].map(String::from));
        }
        "media_to_webm" => {
            args.extend(["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus"].map(String::from));
        }
        "media_to_mp3" => {
            args.extend(["-vn", "-c:a", "libmp3lame", "-q:a", "2"].map(String::from));
        }
        "media_to_m4a" => {
            args.extend(["-vn", "-c:a", "aac", "-b:a", "192k"].map(String::from));
        }
        "media_to_wav" => {
            args.extend(["-vn", "-c:a", "pcm_s16le"].map(String::from));
        }
        "media_to_flac" => {
            args.extend(["-vn", "-c:a", "flac"].map(String::from));
        }
        "video_compress" => {
            let level = options.get("level").and_then(|v| v.as_u64()).unwrap_or(2);
            let crf = match level {
                1 => "23",
                3 => "31",
                _ => "27",
            };
            args.extend(["-c:v", "libx264", "-preset", "faster", "-crf", crf].map(String::from));
            if let Some(maxh) = options.get("maxh").and_then(|v| v.as_u64()) {
                if maxh > 0 {
                    args.push("-vf".to_string());
                    args.push(format!("scale=-2:'min({},ih)'", maxh));
                }
            }
            args.extend(["-c:a", "aac", "-b:a", "128k"].map(String::from));
        }
        "audio_compress" => {
            let kbps = options.get("bitrate").and_then(|v| v.as_u64()).unwrap_or(128);
            args.extend(["-vn", "-c:a", "libmp3lame", "-b:a", &format!("{}k", kbps)].map(String::from));
        }
        "media_trim" => {
            args.extend(["-c:v", "libx264", "-c:a", "aac"].map(String::from));
        }
        "video_to_gif" => {
            let width = options.get("width").and_then(|v| v.as_u64()).unwrap_or(480).clamp(64, 1280);
            args.extend([
                "-t".to_string(),
                "30".to_string(),
                "-vf".to_string(),
                format!("fps=12,scale={}:-1:flags=lanczos", width),
            ]);
        }
        "video_mute" => {
            args.extend(["-an", "-c:v", "libx264", "-crf", "22"].map(String::from));
        }
        "video_frame_png" => {
            args.extend(["-frames:v", "1"].map(String::from));
        }
        other => return Err(format!("Unsupported media tool: {}", other)),
    }

    args.push("--".to_string());
    args.push(output_str);
    Ok(args)
}

/// Checks image dimensions against the 40-megapixel bomb ceiling before allocation.
pub fn check_image_dimensions(width: u32, height: u32) -> Result<(), String> {
    let pixels = (width as u64).saturating_mul(height as u64);
    if width == 0 || height == 0 {
        return Err("Image has zero width or height".to_string());
    }
    if pixels > constants::MAX_IMAGE_PIXELS {
        return Err(format!(
            "Image dimensions ({}x{} = {:.1} MP) exceed the 40 MP safety ceiling",
            width,
            height,
            pixels as f64 / 1_000_000.0
        ));
    }
    Ok(())
}
