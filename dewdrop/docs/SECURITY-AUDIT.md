# Security Architecture & Malicious-Sample Audit

## 1. Threat Model & Defense-in-Depth Architecture
Dewdrop processes user-selected files locally on Windows without network connectivity. Every file path, file name, metadata tag, archive entry, PDF object, and media stream is treated as untrusted binary data.

| Threat Category | Mitigation Implemented | Verification Evidence |
|---|---|---|
| **Outbound Network Exfiltration / SSRF** | Zero networking crates or socket calls in `dewdrop` and `dewdrop-worker`; FFmpeg invoked with `-protocol_whitelist file,pipe`; optional Windows Firewall outbound block rules installed for `dewdrop.exe`, `dewdrop-worker.exe`, and `ffmpeg.exe`. | `security_m6.5_ffmpeg_protocol_whitelist_lockdown` (PASS) |
| **Arbitrary Code Execution via Active Document Content** | PDF writer strips `/JavaScript`, `/JS`, `/OpenAction`, `/AA`, `/Launch`, `/SubmitForm`, and `/EmbeddedFiles` from catalog and page dictionaries. Word-to-PDF scans `.docx` ZIP central directories and refuses any file containing `vbaProject.bin`. | `security_m6.4_pdf_javascript_autoaction_strip` (PASS), `security_m6.7_macro_enabled_docx_refused` (PASS) |
| **Archive Path Traversal ("Zip-Slip") & Link Attacks** | Custom guarded extractor validates every entry component before opening output files; refuses `..`, leading `/`, drive prefixes (`C:`), symlinks, hardlinks, FIFOs, and encrypted entries. | `security_m6.1_zip_slip_traversal` (PASS) |
| **Decompression & Memory Bombs** | Worker runs inside a Windows Job Object with a 3 GiB memory limit and kill-on-close flag; images checked against a 40 MP ceiling before allocation; archives streamed with a 2,000-file and 512 MiB uncompressed byte cap. | `security_m6.2_zip_decompression_bomb` (PASS), `security_m6.3_giant_40mp_image_bomb` (PASS) |
| **Command / Argument Injection & Reserved Device Names** | Processes started strictly via `CreateProcessW` argument arrays (never `cmd.exe` or shell interpolation); `--` precedes positional paths; `sanitize_stem` neutralizes leading `-`, trailing dots/spaces, and Windows device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`). | `security_m6.6_odd_filenames_and_device_names` (PASS) |
| **DLL Hijacking & Writable Search Paths** | Per-user installation under `%LOCALAPPDATA%\Programs\Dewdrop`; system DLL loading restricted to `%SystemRoot%\System32`; static CRT linkage (`+crt-static`). | Verified in `bins/dewdrop` and `installer/Dewdrop.iss` |

## 2. Malicious Sample Test Results
All 7 malicious fixture tests in `tests/run_cleanroom_verification.py` executed and passed:
1. `malicious_zipslip.zip` (`../../outside_escape.txt`) -> **Refused**
2. `malicious_bomb.zip` (2,005 entries exceeding 2,000 cap) -> **Refused**
3. `giant_100mp.png` (`10000x10000` = 100 MP > 40 MP cap) -> **Refused**
4. `malicious_js.pdf` (embedded `/JavaScript` + `/OpenAction`) -> **Neutralized / Stripped**
5. `crafted.m3u8` (external `http://127.0.0.1:9999/secret.ts` stream) -> **Blocked by `-protocol_whitelist file,pipe`**
6. `-rf --no-preserve-root`, `CON.txt`, `NUL...` -> **Sanitized to safe stems**
7. `macro_trojan.docx` (contains `word/vbaProject.bin`) -> **Refused**
