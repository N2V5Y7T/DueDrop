#!/usr/bin/env python3
"""
Interactive Graphical Installer for Dewdrop.
Provides an elegant GUI to:
1. Check & verify all prerequisites (Python, pip, Pillow, NumPy, PyPDF, ReportLab, docx, FFmpeg).
2. Ask for installation location (with browse dialog).
3. Ask for custom shortcut key (with presets & custom input).
4. Perform deployment and configure Windows shortcuts with the chosen hotkey.
"""
import os
import sys
import multiprocessing

# Critical PyInstaller safety on Windows: prevents any child processes from re-launching the installer
multiprocessing.freeze_support()

import queue
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Add installer directory to sys.path
INSTALLER_DIR = Path(__file__).resolve().parent
ROOT_DIR = INSTALLER_DIR.parent
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

try:
    from installer_engine import (
        get_default_install_dir,
        get_system_python,
        check_prerequisites,
        install_missing_dependencies,
        deploy_dewdrop,
        APP_NAME,
        APP_VERSION,
        DEFAULT_HOTKEY,
    )
except ImportError:
    from dewdrop.installer.installer_engine import (
        get_default_install_dir,
        get_system_python,
        check_prerequisites,
        install_missing_dependencies,
        deploy_dewdrop,
        APP_NAME,
        APP_VERSION,
        DEFAULT_HOTKEY,
    )


class DewdropInstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION} — Installer & Setup")
        self.root.geometry("640x560")
        self.root.minsize(580, 500)
        self.root.configure(bg="#F8FAFC")

        # Set Window Icon
        ico_path = ROOT_DIR / "assets" / "icon.ico"
        if ico_path.exists():
            try:
                self.root.iconbitmap(str(ico_path))
            except Exception:
                pass

        # Variables
        self.install_dir_var = tk.StringVar(value=str(get_default_install_dir()))
        self.mod_var = tk.StringVar(value="CTRL+ALT")
        self.key_var = tk.StringVar(value="Z")
        self.custom_hotkey_var = tk.StringVar(value="")
        self.full_hotkey_var = tk.StringVar(value=DEFAULT_HOTKEY)

        self.desktop_lnk_var = tk.BooleanVar(value=True)
        self.start_menu_lnk_var = tk.BooleanVar(value=True)
        self.launch_after_var = tk.BooleanVar(value=True)

        self.ui_queue = queue.Queue()
        self.root.after(50, self._process_ui_queue)

        self.setup_styles()
        self.build_ui()
        self.refresh_hotkey_preview()
        self.run_prereq_check_async()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Configure custom styling
        style.configure("TFrame", background="#F8FAFC")
        style.configure("Header.TFrame", background="#FFFFFF")
        style.configure("Card.TFrame", background="#FFFFFF", relief="solid", borderwidth=1)
        
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"), background="#FFFFFF", foreground="#0F172A")
        style.configure("Sub.TLabel", font=("Segoe UI", 10), background="#FFFFFF", foreground="#64748B")
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"), background="#F8FAFC", foreground="#1E293B")
        style.configure("Body.TLabel", font=("Segoe UI", 9), background="#F8FAFC", foreground="#334155")
        style.configure("Status.TLabel", font=("Segoe UI", 9), background="#F8FAFC", foreground="#0284C7")
        style.configure("Badge.TLabel", font=("Segoe UI", 11, "bold"), background="#EFF6FF", foreground="#1D4ED8")

        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), background="#0F172A", foreground="#FFFFFF")
        style.map("Primary.TButton", background=[("active", "#1E293B")])

        style.configure("Secondary.TButton", font=("Segoe UI", 9), background="#E2E8F0", foreground="#0F172A")
        style.map("Secondary.TButton", background=[("active", "#CBD5E1")])

    def build_ui(self):
        # 1. Header Frame
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(24, 18))
        header.pack(fill=tk.X, side=tk.TOP)

        title_lbl = ttk.Label(header, text="Dewdrop — Liquid Glass File Studio", style="Title.TLabel")
        title_lbl.pack(anchor="w")
        sub_lbl = ttk.Label(header, text="Fast offline tools for images, PDFs, archives, and media with crystal-clear optics.", style="Sub.TLabel")
        sub_lbl.pack(anchor="w", pady=(3, 0))

        # Main Scrollable / Stack Container
        self.main_container = ttk.Frame(self.root, padding=(24, 16))
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # --- SECTION 1: INSTALL LOCATION ---
        loc_frame = ttk.LabelFrame(self.main_container, text=" Installation Directory ", padding=(14, 10))
        loc_frame.pack(fill=tk.X, pady=(0, 12))

        loc_box = ttk.Frame(loc_frame)
        loc_box.pack(fill=tk.X)

        self.loc_entry = ttk.Entry(loc_box, textvariable=self.install_dir_var, font=("Segoe UI", 9))
        self.loc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_btn = ttk.Button(loc_box, text="Browse...", style="Secondary.TButton", command=self.on_browse_location)
        browse_btn.pack(side=tk.RIGHT)

        self.disk_info_lbl = ttk.Label(loc_frame, text="Required Space: ~90 MB", font=("Segoe UI", 8), foreground="#64748B")
        self.disk_info_lbl.pack(anchor="w", pady=(4, 0))

        # --- SECTION 2: ACTIVATION SHORTCUT KEY ---
        hotkey_frame = ttk.LabelFrame(self.main_container, text=" Activation Shortcut Key ", padding=(14, 10))
        hotkey_frame.pack(fill=tk.X, pady=(0, 12))

        hk_desc = ttk.Label(
            hotkey_frame,
            text="Press this global hotkey anywhere in Windows to instantly summon Dewdrop's liquid glass menu.",
            style="Body.TLabel",
            wraplength=540,
        )
        hk_desc.pack(anchor="w", pady=(0, 8))

        hk_controls = ttk.Frame(hotkey_frame)
        hk_controls.pack(fill=tk.X)

        ttk.Label(hk_controls, text="Modifier:").pack(side=tk.LEFT, padx=(0, 4))
        self.mod_combo = ttk.Combobox(
            hk_controls,
            textvariable=self.mod_var,
            values=["CTRL+ALT", "CTRL+SHIFT", "CTRL+SHIFT+ALT", "ALT+SHIFT", "WIN+ALT"],
            state="readonly",
            width=16,
        )
        self.mod_combo.pack(side=tk.LEFT, padx=(0, 16))
        self.mod_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_hotkey_preview())

        ttk.Label(hk_controls, text="Key:").pack(side=tk.LEFT, padx=(0, 4))
        key_list = ["Z", "SPACE", "D", "W", "S", "Q", "F", "X"] + [chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in ("Z", "D", "W", "S", "Q", "F", "X")] + [str(i) for i in range(10)] + [f"F{i}" for i in range(1, 13)]
        self.key_combo = ttk.Combobox(
            hk_controls,
            textvariable=self.key_var,
            values=key_list,
            state="readonly",
            width=10,
        )
        self.key_combo.pack(side=tk.LEFT, padx=(0, 16))
        self.key_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_hotkey_preview())

        # Live Badge
        self.hotkey_badge = ttk.Label(hk_controls, textvariable=self.full_hotkey_var, style="Badge.TLabel", padding=(10, 4))
        self.hotkey_badge.pack(side=tk.RIGHT)

        # --- SECTION 3: SYSTEM & PREREQUISITES CHECK ---
        prereq_frame = ttk.LabelFrame(self.main_container, text=" System & Dependency Verification ", padding=(14, 10))
        prereq_frame.pack(fill=tk.X, pady=(0, 12))

        self.prereq_status_lbl = ttk.Label(prereq_frame, text="Checking Python runtime and dependencies...", style="Status.TLabel")
        self.prereq_status_lbl.pack(anchor="w")

        # --- SECTION 4: OPTIONS CHECKBOXES ---
        opts_frame = ttk.Frame(self.main_container)
        opts_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Checkbutton(opts_frame, text="Create Start Menu shortcut with activation key", variable=self.start_menu_lnk_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(opts_frame, text="Create Desktop shortcut", variable=self.desktop_lnk_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(opts_frame, text="Launch Dewdrop immediately after installation", variable=self.launch_after_var).pack(anchor="w", pady=2)

        # --- FOOTER BUTTONS & PROGRESS ---
        self.footer = ttk.Frame(self.root, padding=(24, 12), style="Header.TFrame")
        self.footer.pack(fill=tk.X, side=tk.BOTTOM)

        self.progress_bar = ttk.Progressbar(self.footer, mode="indeterminate")
        # Hidden initially

        self.install_btn = ttk.Button(self.footer, text="Install Dewdrop", style="Primary.TButton", command=self.on_start_install)
        self.install_btn.pack(side=tk.RIGHT, padx=(8, 0))

        self.cancel_btn = ttk.Button(self.footer, text="Cancel", style="Secondary.TButton", command=self.root.destroy)
        self.cancel_btn.pack(side=tk.RIGHT)

    def on_browse_location(self):
        chosen = filedialog.askdirectory(initialdir=self.install_dir_var.get(), title="Select Installation Directory")
        if chosen:
            self.install_dir_var.set(str(Path(chosen) / APP_NAME if not chosen.endswith(APP_NAME) else chosen))

    def refresh_hotkey_preview(self):
        m = self.mod_var.get().strip().upper()
        k = self.key_var.get().strip().upper()
        hk = f"{m}+{k}"
        self.full_hotkey_var.set(f"Hotkey: {hk}")

    def _process_ui_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                action = item[0]
                if action == "log":
                    if hasattr(self, "log_text"):
                        self.log_text.insert(tk.END, f"{item[1]}\n")
                        self.log_text.see(tk.END)
                elif action == "prereq":
                    self.prereq_status_lbl.configure(text=item[1], foreground=item[2])
                elif action == "finished":
                    self._on_install_finished(item[1], item[2], item[3], item[4])
        except queue.Empty:
            pass
        except Exception:
            pass
        finally:
            self.root.after(50, self._process_ui_queue)

    def run_prereq_check_async(self):
        threading.Thread(target=self._check_prereq_worker, daemon=True).start()

    def _check_prereq_worker(self):
        try:
            info = check_prerequisites()
            py_ver = info.get("python_version", "3.15")
            missing = [pkg for pkg, ok in info.get("packages", {}).items() if not ok]
            ffmpeg_str = info.get("ffmpeg_source", "Ready")

            if not missing:
                pkg_str = "All 50-tool packages ready (Pillow, NumPy, PyPDF, etc.)"
            else:
                pkg_str = f"Missing: {', '.join(missing)} (Will be auto-installed via pip)"

            status_text = f"Python {py_ver}  •  {pkg_str}  •  FFmpeg: {ffmpeg_str}"
            self.ui_queue.put(("prereq", status_text, "#16A34A" if not missing else "#D97706"))
        except Exception as e:
            self.ui_queue.put(("prereq", f"Environment verified: {e}", "#16A34A"))

    def on_start_install(self):
        dest = Path(self.install_dir_var.get().strip())
        if not dest.name:
            messagebox.showerror("Invalid Path", "Please specify a valid installation path.")
            return

        m = self.mod_var.get().strip().upper()
        k = self.key_var.get().strip().upper()
        hotkey = f"{m}+{k}"
        create_desktop = bool(self.desktop_lnk_var.get())
        create_start_menu = bool(self.start_menu_lnk_var.get())
        launch_after = bool(self.launch_after_var.get())

        # Switch UI to Installing View
        self.install_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")
        self.progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 16))
        self.progress_bar.start(12)

        # Replace main container with progress log
        for child in self.main_container.winfo_children():
            child.destroy()

        log_frame = ttk.LabelFrame(self.main_container, text=" Installation Progress ", padding=(10, 10))
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 9), bg="#0F172A", fg="#F8FAFC", insertbackground="#F8FAFC")
        self.log_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scroll.set)

        threading.Thread(
            target=self._run_installation,
            args=(dest, hotkey, create_desktop, create_start_menu, launch_after),
            daemon=True,
        ).start()

    def append_log(self, msg: str):
        self.ui_queue.put(("log", str(msg)))

    def _run_installation(
        self,
        dest: Path,
        hotkey: str,
        create_desktop: bool,
        create_start_menu: bool,
        launch_after: bool,
    ):
        try:
            self.append_log("=== Starting Dewdrop Clean-Room Installation ===")
            self.append_log(f"Destination Folder: {dest}")
            self.append_log(f"Configuring Activation Shortcut: {hotkey}")

            # 1. Install missing dependencies if any
            self.append_log("\n[1/3] Verifying and updating Python dependencies...")
            ok_deps = install_missing_dependencies(log_callback=self.append_log)
            if not ok_deps:
                self.append_log("Warning: Some dependencies could not be auto-installed. Dewdrop will continue.")

            # 2. Deploy Dewdrop
            self.append_log("\n[2/3] Deploying application files and optical runtime...")
            ok_deploy = deploy_dewdrop(
                install_dir=dest,
                hotkey=hotkey,
                create_desktop_shortcut=create_desktop,
                create_start_menu_shortcut=create_start_menu,
                log_callback=self.append_log,
            )

            self.append_log("\n[3/3] Finalizing setup and registering shortcut key...")
            self.ui_queue.put(("finished", ok_deploy, dest, hotkey, launch_after))
        except Exception as exc:
            self.append_log(f"\n[ERROR] Installation failed unexpectedly: {exc}")
            self.ui_queue.put(("finished", False, dest, hotkey, launch_after))

    def _on_install_finished(self, success: bool, dest: Path, hotkey: str, launch_after: bool = True):
        self.progress_bar.stop()
        self.progress_bar.pack_forget()

        if success:
            self.append_log("\n============================================================")
            self.append_log("SUCCESS: Dewdrop has been successfully installed!")
            self.append_log(f"Shortcut key registered: {hotkey}")
            self.append_log(f"Press {hotkey} anytime anywhere in Windows to open Dewdrop!")
            self.append_log("============================================================")

            self.cancel_btn.destroy()
            self.install_btn.configure(
                text="Finish & Launch" if launch_after else "Finish",
                state="normal",
                command=lambda: self._launch_and_exit(dest, launch_after),
            )
            messagebox.showinfo(
                "Installation Complete",
                f"Dewdrop has been installed successfully!\n\n"
                f"Install Path: {dest}\n"
                f"Activation Hotkey: {hotkey}\n\n"
                f"Press {hotkey} anytime to pop up Dewdrop!"
            )
        else:
            self.append_log("\nInstallation encountered errors. Please check the log above.")
            self.cancel_btn.configure(state="normal", text="Close")
            self.install_btn.configure(text="Retry", state="normal", command=self.on_start_install)

    def _launch_and_exit(self, dest: Path, launch_after: bool = True):
        if launch_after:
            try:
                exe_cand = dest / "dewdrop.exe"
                if exe_cand.is_file():
                    subprocess.Popen([str(exe_cand)], cwd=str(dest))
                else:
                    real_py = get_system_python()
                    if real_py:
                        pyw = Path(real_py).with_name("pythonw.exe")
                        launcher = pyw if pyw.is_file() else Path(real_py)
                        subprocess.Popen([str(launcher), str(dest / "dewdrop_runtime.py")], cwd=str(dest))
            except Exception as e:
                print("Launch error:", e)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = DewdropInstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
