use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::f64::consts::PI;
use std::path::{Path, PathBuf};

pub mod constants {
    pub const APP_IDENTITY_NAME: &str = "Dewdrop";
    pub const OUTPUT_SUBFOLDER_NAME: &str = "Dew Drop";
    pub const DEFAULT_HOTKEY: &str = "CTRL+ALT+Z";
    pub const DEFAULT_IDLE_TIMEOUT_SECS: f64 = 120.0;
    pub const RING_OUTER_DIAMETER_PX: f64 = 460.0;
    pub const RING_SAFE_MARGIN_PX: f64 = 48.0;
    pub const SEGMENT_COUNT: usize = 6;
    pub const DWELL_TO_ENTER_SECS: f64 = 1.5;
    pub const DWELL_TO_BACK_SECS: f64 = 1.5;
    pub const ENTER_GRACE_PERIOD_SECS: f64 = 1.0;
    pub const PAGING_FULL_TURN_RAD: f64 = 2.0 * std::f64::consts::PI;
    pub const PAGING_HYSTERESIS_RAD: f64 = 0.18;
    pub const PAGING_INNER_DEADZONE_PX: f64 = 38.0;
    pub const SPRING_RESPONSE_SECS: f64 = 0.45;
    pub const SPRING_DAMPING_RATIO: f64 = 0.80;
    pub const MAX_BATCH_FILES: usize = 100;
    pub const MAX_STANDARD_INPUT_BYTES: u64 = 100 * 1024 * 1024;
    pub const MAX_STREAMED_INPUT_BYTES: u64 = 2 * 1024 * 1024 * 1024;
    pub const MAX_OUTPUT_BYTES: u64 = 512 * 1024 * 1024;
    pub const MAX_IMAGE_PIXELS: u64 = 40_000_000;
    pub const MAX_PDF_PAGES: usize = 200;
    pub const MAX_ARCHIVE_FILES: usize = 2_000;
    pub const WORKER_MEMORY_CAP_BYTES: usize = 3 * 1024 * 1024 * 1024;
}

/// Semi-implicit / analytical damped harmonic oscillator for liquid-glass morphing and pop-in.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct SpringSolver {
    pub position: f64,
    pub velocity: f64,
    pub target: f64,
    pub response: f64,
    pub damping_ratio: f64,
}

impl SpringSolver {
    pub fn new(initial: f64) -> Self {
        Self {
            position: initial,
            velocity: 0.0,
            target: initial,
            response: constants::SPRING_RESPONSE_SECS,
            damping_ratio: constants::SPRING_DAMPING_RATIO,
        }
    }

    pub fn with_params(initial: f64, response: f64, damping_ratio: f64) -> Self {
        Self {
            position: initial,
            velocity: 0.0,
            target: initial,
            response: response.max(0.05),
            damping_ratio: damping_ratio.clamp(0.1, 2.0),
        }
    }

    pub fn set_target(&mut self, new_target: f64) {
        self.target = new_target;
    }

    pub fn step(&mut self, dt: f64) -> f64 {
        let sub_dt = 1.0 / 240.0;
        let mut remaining = dt.clamp(0.0, 0.25);
        let omega = 2.0 * PI / self.response;
        let k = omega * omega;
        let c = 2.0 * self.damping_ratio * omega;

        while remaining > 1e-9 {
            let h = remaining.min(sub_dt);
            let displacement = self.position - self.target;
            let accel = -k * displacement - c * self.velocity;
            self.velocity += accel * h;
            self.position += self.velocity * h;
            remaining -= h;
        }
        self.position
    }

    pub fn is_settled(&self) -> bool {
        (self.position - self.target).abs() < 0.0008 && self.velocity.abs() < 0.005
    }
}

/// 2D rectangle representing monitor work areas or window geometry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkAreaRect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl WorkAreaRect {
    pub fn width(&self) -> i32 {
        (self.right - self.left).max(0)
    }
    pub fn height(&self) -> i32 {
        (self.bottom - self.top).max(0)
    }
}

/// Computes the centre (x, y) for the circular menu so that:
/// 1. It opens at a safe distance (`radius + margin`) from the cursor toward the roomiest direction.
/// 2. It always fits completely inside the monitor work area.
pub fn compute_ring_placement(
    cursor_x: i32,
    cursor_y: i32,
    work_area: WorkAreaRect,
    dpi_scale: f64,
) -> (i32, i32) {
    let scale = dpi_scale.max(0.5);
    let radius = ((constants::RING_OUTER_DIAMETER_PX * 0.5) * scale).round() as i32;
    let margin = (constants::RING_SAFE_MARGIN_PX * scale).round() as i32;
    let safe_dist = radius + margin;

    let space_left = cursor_x - work_area.left;
    let space_right = work_area.right - cursor_x;
    let space_top = cursor_y - work_area.top;
    let space_bottom = work_area.bottom - cursor_y;

    let dx_dir: f64 = if space_right >= space_left { 1.0 } else { -1.0 };
    let dy_dir: f64 = if space_bottom >= space_top { 1.0 } else { -1.0 };

    let horiz_room = space_right.max(space_left).max(1) as f64;
    let vert_room = space_bottom.max(space_top).max(1) as f64;
    let norm = (horiz_room * horiz_room + vert_room * vert_room).sqrt();
    let vx = dx_dir * (horiz_room / norm);
    let vy = dy_dir * (vert_room / norm);

    let raw_cx = cursor_x + (vx * safe_dist as f64).round() as i32;
    let raw_cy = cursor_y + (vy * safe_dist as f64).round() as i32;

    let pad = (8.0 * scale).round() as i32;
    let min_x = work_area.left + radius + pad;
    let max_x = (work_area.right - radius - pad).max(min_x);
    let min_y = work_area.top + radius + pad;
    let max_y = (work_area.bottom - radius - pad).max(min_y);

    let clamped_x = raw_cx.clamp(min_x, max_x);
    let clamped_y = raw_cy.clamp(min_y, max_y);

    let dist_sq = ((clamped_x - cursor_x) as f64).powi(2) + ((clamped_y - cursor_y) as f64).powi(2);
    let min_clear = (radius + (margin / 2)) as f64;
    if dist_sq < min_clear * min_clear {
        let candidates = [
            (cursor_x + safe_dist, cursor_y),
            (cursor_x - safe_dist, cursor_y),
            (cursor_x, cursor_y + safe_dist),
            (cursor_x, cursor_y - safe_dist),
            (cursor_x + safe_dist, cursor_y + safe_dist),
            (cursor_x - safe_dist, cursor_y + safe_dist),
            (cursor_x + safe_dist, cursor_y - safe_dist),
            (cursor_x - safe_dist, cursor_y - safe_dist),
        ];
        let mut best = (clamped_x, clamped_y);
        let mut best_d2 = dist_sq;
        for (cx, cy) in candidates {
            let x = cx.clamp(min_x, max_x);
            let y = cy.clamp(min_y, max_y);
            let d2 = ((x - cursor_x) as f64).powi(2) + ((y - cursor_y) as f64).powi(2);
            if d2 > best_d2 {
                best_d2 = d2;
                best = (x, y);
            }
        }
        return best;
    }

    (clamped_x, clamped_y)
}

/// Maps a point relative to ring centre `(dx, dy)` to segment index `0..6`.
pub fn hit_test_ring_segment(
    dx: f64,
    dy: f64,
    inner_radius: f64,
    outer_radius: f64,
) -> Option<usize> {
    let r = (dx * dx + dy * dy).sqrt();
    if r < inner_radius || r > outer_radius {
        return None;
    }
    let mut clock_deg = dx.atan2(-dy).to_degrees();
    if clock_deg < 0.0 {
        clock_deg += 360.0;
    }
    let shifted = (clock_deg + 30.0) % 360.0;
    Some((shifted / 60.0).floor() as usize % constants::SEGMENT_COUNT)
}

/// Tracks cumulative 360° clockwise / counter-clockwise cursor rotation with dead-zone and hysteresis.
#[derive(Debug, Clone)]
pub struct RotationPager {
    pub current_page: usize,
    pub total_pages: usize,
    pub accumulated_rad: f64,
    last_angle: Option<f64>,
    pub deadzone_radius: f64,
}

impl RotationPager {
    pub fn new(total_items: usize, deadzone_radius: f64) -> Self {
        let total_pages = ((total_items.max(1) + constants::SEGMENT_COUNT - 1)
            / constants::SEGMENT_COUNT)
            .max(1);
        Self {
            current_page: 0,
            total_pages,
            accumulated_rad: 0.0,
            last_angle: None,
            deadzone_radius,
        }
    }

    pub fn reset_gesture(&mut self) {
        self.accumulated_rad = 0.0;
        self.last_angle = None;
    }

    pub fn update_pointer(&mut self, dx: f64, dy: f64) -> Option<usize> {
        let r = (dx * dx + dy * dy).sqrt();
        if r < self.deadzone_radius {
            self.last_angle = None;
            return None;
        }
        let angle = dx.atan2(-dy);
        if let Some(prev) = self.last_angle {
            let mut delta = angle - prev;
            while delta > PI {
                delta -= 2.0 * PI;
            }
            while delta < -PI {
                delta += 2.0 * PI;
            }
            if delta.abs() > 0.014 {
                self.accumulated_rad += delta;
                self.last_angle = Some(angle);
            }
        } else {
            self.last_angle = Some(angle);
        }

        let threshold = constants::PAGING_FULL_TURN_RAD + constants::PAGING_HYSTERESIS_RAD;
        if self.accumulated_rad >= threshold {
            self.accumulated_rad -= constants::PAGING_FULL_TURN_RAD;
            if self.total_pages > 1 {
                self.current_page = (self.current_page + 1) % self.total_pages;
            }
            return Some(self.current_page);
        } else if self.accumulated_rad <= -threshold {
            self.accumulated_rad += constants::PAGING_FULL_TURN_RAD;
            if self.total_pages > 1 {
                self.current_page = (self.current_page + self.total_pages - 1) % self.total_pages;
            }
            return Some(self.current_page);
        }
        None
    }

    /// Returns signed outer-ring progress arc fraction in `[-1.0, 1.0]`.
    pub fn progress_fraction(&self) -> f64 {
        (self.accumulated_rad / constants::PAGING_FULL_TURN_RAD).clamp(-1.0, 1.0)
    }
}

/// Idle-timer state machine enforcing the 2-minute lifecycle rules.
#[derive(Debug, Clone)]
pub struct IdleTimerStateMachine {
    pub timeout_secs: f64,
    pub elapsed_idle_secs: f64,
    pub active_jobs: usize,
    pub options_window_open: bool,
    pub island_visible: bool,
    pub pointer_over_island: bool,
    pub drag_in_progress: bool,
    pub enter_grace_remaining_secs: f64,
}

impl IdleTimerStateMachine {
    pub fn new(timeout_secs: f64) -> Self {
        Self {
            timeout_secs,
            elapsed_idle_secs: 0.0,
            active_jobs: 0,
            options_window_open: false,
            island_visible: false,
            pointer_over_island: false,
            drag_in_progress: false,
            enter_grace_remaining_secs: 0.0,
        }
    }

    pub fn is_paused(&self) -> bool {
        self.active_jobs > 0
            || self.options_window_open
            || self.island_visible
            || self.pointer_over_island
            || self.drag_in_progress
    }

    pub fn notify_activity(&mut self) {
        self.elapsed_idle_secs = 0.0;
    }

    pub fn trigger_segment_enter_grace(&mut self) {
        self.enter_grace_remaining_secs = constants::ENTER_GRACE_PERIOD_SECS;
        self.notify_activity();
    }

    pub fn is_in_enter_grace(&self) -> bool {
        self.enter_grace_remaining_secs > 0.0
    }

    pub fn tick(&mut self, dt_secs: f64) -> bool {
        let dt = dt_secs.max(0.0);
        if self.enter_grace_remaining_secs > 0.0 {
            self.enter_grace_remaining_secs = (self.enter_grace_remaining_secs - dt).max(0.0);
        }
        if self.is_paused() {
            self.elapsed_idle_secs = 0.0;
            false
        } else {
            self.elapsed_idle_secs += dt;
            self.elapsed_idle_secs >= self.timeout_secs
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ToolGroup {
    ConvertImage,
    EditImage,
    PdfAndDocuments,
    VideoAndAudio,
    Archives,
    DataAndText,
}

impl ToolGroup {
    pub fn default_order() -> [ToolGroup; 6] {
        [
            ToolGroup::ConvertImage,
            ToolGroup::EditImage,
            ToolGroup::PdfAndDocuments,
            ToolGroup::VideoAndAudio,
            ToolGroup::Archives,
            ToolGroup::DataAndText,
        ]
    }

    pub fn display_name(&self) -> &'static str {
        match self {
            ToolGroup::ConvertImage => "Convert image",
            ToolGroup::EditImage => "Edit image",
            ToolGroup::PdfAndDocuments => "PDF & docs",
            ToolGroup::VideoAndAudio => "Video & audio",
            ToolGroup::Archives => "Archives",
            ToolGroup::DataAndText => "Data & text",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum InputCategory {
    Image,
    Pdf,
    WordDocx,
    Media,
    VideoOnly,
    ArchiveOrCompressed,
    TextOrData,
    JsonOnly,
    CsvOnly,
    SrtOnly,
    VttOnly,
    AnyFile,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSpec {
    pub id: &'static str,
    pub label: &'static str,
    pub group: ToolGroup,
    pub input_category: InputCategory,
    pub requires_ffmpeg: bool,
    pub combines_multiple: bool,
    pub has_options: bool,
    pub default_rank: usize,
}

pub fn catalogue_50_tools() -> Vec<ToolSpec> {
    use InputCategory::*;
    use ToolGroup::*;
    let raw: Vec<(&'static str, &'static str, ToolGroup, InputCategory, bool, bool, bool)> = vec![
        ("img_to_png", "PNG", ConvertImage, Image, false, false, true),
        ("img_to_jpeg", "JPEG", ConvertImage, Image, false, false, true),
        ("img_to_webp", "WEBP", ConvertImage, Image, false, false, true),
        ("img_to_gif", "GIF", ConvertImage, Image, false, false, true),
        ("img_to_ico", "ICO", ConvertImage, Image, false, false, true),
        ("img_compress", "Compress image", EditImage, Image, false, false, true),
        ("img_resize", "Resize", EditImage, Image, false, false, true),
        ("img_crop", "Crop to shape", EditImage, Image, false, false, true),
        ("img_rotate_flip", "Rotate / flip", EditImage, Image, false, false, true),
        ("img_effects", "Effects", EditImage, Image, false, false, true),
        ("img_watermark", "Text watermark", EditImage, Image, false, false, true),
        ("img_strip_meta", "Remove metadata", EditImage, Image, false, false, false),
        ("pdf_from_images", "Images to PDF", PdfAndDocuments, Image, false, true, true),
        ("pdf_merge", "Merge PDFs", PdfAndDocuments, Pdf, false, true, false),
        ("pdf_split", "Split PDF", PdfAndDocuments, Pdf, false, false, false),
        ("pdf_extract_pages", "Extract pages", PdfAndDocuments, Pdf, false, false, true),
        ("pdf_delete_pages", "Delete pages", PdfAndDocuments, Pdf, false, false, true),
        ("pdf_reverse", "Reverse page order", PdfAndDocuments, Pdf, false, false, false),
        ("pdf_rotate", "Rotate PDF", PdfAndDocuments, Pdf, false, false, true),
        ("pdf_compress", "Compress PDF", PdfAndDocuments, Pdf, false, false, true),
        ("pdf_to_text", "PDF to Text", PdfAndDocuments, Pdf, false, false, false),
        ("pdf_extract_images", "PDF to pictures", PdfAndDocuments, Pdf, false, false, false),
        ("pdf_strip_meta", "Remove PDF metadata", PdfAndDocuments, Pdf, false, false, false),
        ("pdf_to_word", "PDF to Word", PdfAndDocuments, Pdf, false, false, false),
        ("word_to_pdf", "Word to PDF", PdfAndDocuments, WordDocx, false, false, false),
        ("media_to_mp4", "MP4", VideoAndAudio, Media, true, false, false),
        ("media_to_webm", "WEBM", VideoAndAudio, Media, true, false, false),
        ("media_to_mp3", "MP3", VideoAndAudio, Media, true, false, false),
        ("media_to_m4a", "M4A", VideoAndAudio, Media, true, false, false),
        ("media_to_wav", "WAV", VideoAndAudio, Media, true, false, false),
        ("media_to_flac", "FLAC", VideoAndAudio, Media, true, false, false),
        ("video_compress", "Compress video", VideoAndAudio, VideoOnly, true, false, true),
        ("audio_compress", "Compress audio", VideoAndAudio, Media, true, false, true),
        ("media_trim", "Trim", VideoAndAudio, Media, true, false, true),
        ("video_to_gif", "Video to GIF", VideoAndAudio, VideoOnly, true, false, true),
        ("video_mute", "Mute video", VideoAndAudio, VideoOnly, true, false, false),
        ("video_frame_png", "Video frame to PNG", VideoAndAudio, VideoOnly, true, false, true),
        ("archive_zip", "ZIP", Archives, AnyFile, false, true, true),
        ("archive_targz", "TAR.GZ", Archives, AnyFile, false, true, true),
        ("archive_gzip", "GZIP", Archives, AnyFile, false, false, true),
        ("archive_extract", "Extract archive", Archives, ArchiveOrCompressed, false, false, false),
        ("json_pretty", "JSON pretty", DataAndText, JsonOnly, false, false, false),
        ("json_minify", "JSON minified", DataAndText, JsonOnly, false, false, false),
        ("json_to_csv", "JSON to CSV", DataAndText, JsonOnly, false, false, false),
        ("csv_to_json", "CSV to JSON", DataAndText, CsvOnly, false, false, false),
        ("text_utf8", "Text to UTF-8", DataAndText, TextOrData, false, false, false),
        ("text_crlf", "Windows line endings", DataAndText, TextOrData, false, false, false),
        ("srt_to_vtt", "SRT to VTT", DataAndText, SrtOnly, false, false, false),
        ("vtt_to_srt", "VTT to SRT", DataAndText, VttOnly, false, false, false),
        ("checksums_all", "Checksums", DataAndText, AnyFile, false, false, false),
    ];

    raw.into_iter()
        .enumerate()
        .map(
            |(idx, (id, label, group, input_category, requires_ffmpeg, combines_multiple, has_options))| {
                ToolSpec {
                    id,
                    label,
                    group,
                    input_category,
                    requires_ffmpeg,
                    combines_multiple,
                    has_options,
                    default_rank: idx,
                }
            },
        )
        .collect()
}

pub fn file_matches_category(path: &Path, cat: InputCategory) -> bool {
    let name_lower = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();

    match cat {
        InputCategory::AnyFile => true,
        InputCategory::Image => matches!(
            ext.as_str(),
            "png" | "jpg" | "jpeg" | "jpe" | "jfif" | "webp" | "tiff" | "tif" | "bmp" | "gif" | "ico" | "avif"
        ),
        InputCategory::Pdf => ext == "pdf",
        InputCategory::WordDocx => ext == "docx",
        InputCategory::VideoOnly => matches!(
            ext.as_str(),
            "mp4" | "mov" | "m4v" | "mkv" | "webm" | "avi" | "wmv" | "flv" | "ts" | "mpg" | "mpeg" | "3gp"
        ),
        InputCategory::Media => {
            file_matches_category(path, InputCategory::VideoOnly)
                || matches!(
                    ext.as_str(),
                    "mp3" | "wav" | "flac" | "m4a" | "aac" | "ogg" | "opus" | "wma" | "aiff" | "aif"
                )
        }
        InputCategory::ArchiveOrCompressed => {
            name_lower.ends_with(".tar.gz")
                || name_lower.ends_with(".tar.bz2")
                || name_lower.ends_with(".tar.xz")
                || matches!(
                    ext.as_str(),
                    "zip" | "tar" | "tgz" | "tbz2" | "txz" | "gz" | "bz2" | "xz"
                )
        }
        InputCategory::JsonOnly => ext == "json",
        InputCategory::CsvOnly => matches!(ext.as_str(), "csv" | "tsv"),
        InputCategory::SrtOnly => ext == "srt",
        InputCategory::VttOnly => ext == "vtt",
        InputCategory::TextOrData => matches!(
            ext.as_str(),
            "txt" | "csv" | "tsv" | "json" | "md" | "log" | "srt" | "vtt" | "xml" | "html" | "htm" | "ini" | "yaml" | "yml"
        ),
    }
}

pub fn filter_and_rank_tools(
    staged_files: &[PathBuf],
    ffmpeg_available: bool,
    usage_counts: &HashMap<String, u64>,
) -> (Vec<ToolGroup>, HashMap<ToolGroup, Vec<ToolSpec>>) {
    if staged_files.is_empty() {
        return (Vec::new(), HashMap::new());
    }
    let all_tools = catalogue_50_tools();
    let mut grouped: HashMap<ToolGroup, Vec<ToolSpec>> = HashMap::new();

    for tool in all_tools {
        if tool.requires_ffmpeg && !ffmpeg_available {
            continue;
        }
        if staged_files
            .iter()
            .all(|f| file_matches_category(f, tool.input_category))
        {
            grouped.entry(tool.group).or_default().push(tool);
        }
    }

    for tools in grouped.values_mut() {
        tools.sort_by(|a, b| {
            let ca = usage_counts.get(a.id).copied().unwrap_or(0);
            let cb = usage_counts.get(b.id).copied().unwrap_or(0);
            cb.cmp(&ca).then_with(|| a.default_rank.cmp(&b.default_rank))
        });
    }

    let mut active_groups: Vec<ToolGroup> = ToolGroup::default_order()
        .into_iter()
        .filter(|g| grouped.get(g).map(|v| !v.is_empty()).unwrap_or(false))
        .collect();

    active_groups.sort_by(|ga, gb| {
        let sum_a: u64 = grouped[ga]
            .iter()
            .map(|t| usage_counts.get(t.id).copied().unwrap_or(0))
            .sum();
        let sum_b: u64 = grouped[gb]
            .iter()
            .map(|t| usage_counts.get(t.id).copied().unwrap_or(0))
            .sum();
        let def_a = ToolGroup::default_order()
            .iter()
            .position(|x| x == ga)
            .unwrap_or(99);
        let def_b = ToolGroup::default_order()
            .iter()
            .position(|x| x == gb)
            .unwrap_or(99);
        sum_b.cmp(&sum_a).then_with(|| def_a.cmp(&def_b))
    });

    (active_groups, grouped)
}

pub fn sanitize_stem(raw_stem: &str) -> String {
    let cleaned: String = raw_stem
        .chars()
        .map(|c| match c {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' | '\0'..='\x1f' => '_',
            _ => c,
        })
        .collect();
    let trimmed = cleaned.trim().trim_end_matches('.').trim_start_matches('-');
    let base = if trimmed.is_empty() { "output" } else { trimmed };
    let upper = base.to_ascii_uppercase();
    let reserved = [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
        "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ];
    let prefix = upper.split('.').next().unwrap_or("");
    if reserved.contains(&prefix) {
        format!("_{}", base)
    } else {
        base.chars().take(140).collect()
    }
}

pub fn resolve_clash_free_output(
    source_file: &Path,
    new_stem: &str,
    new_extension: &str,
    existing_check: impl Fn(&Path) -> bool,
) -> PathBuf {
    let parent = source_file.parent().unwrap_or_else(|| Path::new("."));
    let dew_drop_dir = parent.join(constants::OUTPUT_SUBFOLDER_NAME);
    let safe_stem = sanitize_stem(new_stem);
    let ext_clean = new_extension.trim_start_matches('.');

    let first_candidate = if ext_clean.is_empty() {
        dew_drop_dir.join(&safe_stem)
    } else {
        dew_drop_dir.join(format!("{}.{}", safe_stem, ext_clean))
    };

    if !existing_check(&first_candidate) {
        return first_candidate;
    }

    for counter in 1..10_000 {
        let candidate = if ext_clean.is_empty() {
            dew_drop_dir.join(format!("{} ({})", safe_stem, counter))
        } else {
            dew_drop_dir.join(format!("{} ({}).{}", safe_stem, counter, ext_clean))
        };
        if !existing_check(&candidate) {
            return candidate;
        }
    }
    dew_drop_dir.join(format!("{}_overflow.{}", safe_stem, ext_clean))
}
