$ErrorActionPreference = "Stop"
Write-Host "=== Dewdrop Clean-Room Build & Verification Pipeline ==="

# 1. Format & Static Analysis
cargo fmt --check
cargo clippy --workspace -- -D warnings

# 2. Unit & Integration Test Suite
cargo test --workspace

# 3. Release Binaries (Static CRT, Fat LTO)
$env:RUSTFLAGS = "-C target-feature=+crt-static"
cargo build --release --workspace

# 4. Package Setup Executable via Inno Setup 6
if (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue) {
    ISCC.exe installer\Dewdrop.iss
    Write-Host "Installer built at dist\Dewdrop-Setup.exe"
} else {
    Write-Host "ISCC.exe not on PATH; skipping installer packaging step."
}
