# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""
launcher.py — Heretic Windows GUI launcher & installer.

A zero-dependency (stdlib-only) tkinter front-end for the Heretic
Windows/AMD ROCm fork. It is intentionally NOT a replacement for the
heretic TUI — heretic itself is interactive (rich + questionary) and
requires a real Windows console. Instead, this launcher:

  1. Shows install status (uv, dependencies, ROCm setup, GPU, torch).
  2. Runs the install steps (install uv, uv sync, ROCm setup) in
     separate console windows.
  3. Lets you configure a run (model, config preset, common options)
     and launches `uv run heretic ...` in a new console window.

Because it only uses the standard library, it runs on any Python 3.9+
interpreter — including before the project's dependencies are installed.

Launch it by double-clicking Heretic-Launcher.bat in the repo root, or:

    python scripts/launcher.py
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Paths and constants.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if not (REPO_ROOT / "pyproject.toml").is_file():
    REPO_ROOT = Path.cwd()

STATE_FILE = REPO_ROOT / ".heretic_launcher.json"
ROCM_MARKER = REPO_ROOT / ".heretic_rocm_arch"

CONFIG_PRESETS = {
    "(keep current config.toml)": None,
    "default — standard abliteration": "config.default.toml",
    "nohumor — suppress jokes/humor": "config.nohumor.toml",
    "noslop — suppress purple prose": "config.noslop.toml",
}

FORCE_ARCH_CHOICES = ["auto-detect", "rdna2", "rdna3", "rdna4", "cpu"]

UV_INSTALL_CMD = (
    "powershell -NoProfile -ExecutionPolicy Bypass -Command "
    '"irm https://astral.sh/uv/install.ps1 | iex"'
)

# Dark palette loosely matching the heretic banner (cyan on black).
BG = "#0d1117"
BG_PANEL = "#161b22"
FG = "#e6edf3"
FG_DIM = "#8b949e"
ACCENT = "#22d3ee"
OK = "#3fb950"
WARN = "#d29922"
ERR = "#f85149"


# ---------------------------------------------------------------------------
# System inspection helpers (run off the UI thread).
# ---------------------------------------------------------------------------
def find_uv() -> str | None:
    uv = shutil.which("uv")
    if uv:
        return uv
    # Default install location of the official installer, in case PATH
    # has not been refreshed since uv was installed.
    candidate = Path.home() / ".local" / "bin" / "uv.exe"
    if candidate.is_file():
        return str(candidate)
    return None


def detect_gpu_name() -> str:
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        names = [line.strip() for line in out.splitlines() if line.strip()]
        # Prefer the AMD adapter if there are several (e.g. iGPU + dGPU).
        for name in names:
            if "AMD" in name or "Radeon" in name:
                return name
        return names[0] if names else ""
    except Exception:
        return ""


def detect_torch_version() -> str:
    site = REPO_ROOT / ".venv" / "Lib" / "site-packages"
    try:
        for entry in site.iterdir():
            name = entry.name
            if name.startswith("torch-") and name.endswith(".dist-info"):
                return name[len("torch-") : -len(".dist-info")]
    except OSError:
        pass
    return ""


def venv_synced() -> bool:
    return (REPO_ROOT / ".venv" / "Scripts" / "python.exe").is_file()


def rocm_marker() -> str:
    try:
        return ROCM_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Console spawning.
# ---------------------------------------------------------------------------
def run_in_console(command: str, env: dict | None = None) -> None:
    """Run a command in a new console window that stays open afterwards."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    if sys.platform == "win32":
        subprocess.Popen(
            f'cmd /k "{command}"',
            cwd=REPO_ROOT,
            env=full_env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        # Non-Windows fallback (this fork targets Windows, but don't break).
        subprocess.Popen(shlex.split(command), cwd=REPO_ROOT, env=full_env)


def quote_arg(arg: str) -> str:
    if any(c in arg for c in ' &()^"'):
        return '"' + arg.replace('"', '""') + '"'
    return arg


# ---------------------------------------------------------------------------
# The launcher window.
# ---------------------------------------------------------------------------
class HereticLauncher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Heretic Launcher — Windows & AMD ROCm fork")
        self.configure(bg=BG)
        self.resizable(False, False)

        self.uv_path: str | None = None
        self._build_style()
        self._build_ui()
        self._load_state()
        self._bind_preview_updates()
        self.refresh_status()

    # -- styling ------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG_PANEL)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Panel.TLabel", background=BG_PANEL, foreground=FG)
        style.configure("Dim.TLabel", background=BG_PANEL, foreground=FG_DIM)
        style.configure(
            "Header.TLabel",
            background=BG,
            foreground=ACCENT,
            font=("Consolas", 16, "bold"),
        )
        style.configure("Sub.TLabel", background=BG, foreground=FG_DIM)
        style.configure(
            "Section.TLabel",
            background=BG,
            foreground=ACCENT,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TButton",
            background=BG_PANEL,
            foreground=FG,
            bordercolor=BG_PANEL,
            focuscolor=BG_PANEL,
            padding=(10, 4),
        )
        style.map("TButton", background=[("active", "#21262d")])
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#06262b",
            font=("Segoe UI", 10, "bold"),
            padding=(14, 6),
        )
        style.map("Accent.TButton", background=[("active", "#67e8f9")])
        style.configure("TEntry", insertcolor=FG)
        style.configure("TCheckbutton", background=BG, foreground=FG, focuscolor=BG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TCombobox", arrowcolor=FG)
        self.option_add("*TCombobox*Listbox.background", BG_PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)

    # -- UI construction ----------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 14, "pady": (0, 4)}
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=4, pady=4)

        ttk.Label(root, text="█ HERETIC", style="Header.TLabel").pack(
            anchor="w", padx=14, pady=(10, 0)
        )
        ttk.Label(
            root,
            text="Fully automatic censorship removal for language models — Windows & AMD ROCm fork",
            style="Sub.TLabel",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        # -- status panel -----------------------------------------------
        ttk.Label(root, text="SETUP STATUS", style="Section.TLabel").pack(
            anchor="w", **pad
        )
        panel = ttk.Frame(root, style="Panel.TFrame")
        panel.pack(fill="x", padx=14, pady=(0, 6))
        self.status_labels: dict[str, tk.Label] = {}
        for i, key in enumerate(("uv", "deps", "rocm", "torch", "gpu")):
            label = tk.Label(
                panel,
                text="…",
                bg=BG_PANEL,
                fg=FG_DIM,
                font=("Consolas", 9),
                anchor="w",
            )
            label.grid(
                row=i,
                column=0,
                sticky="w",
                padx=10,
                pady=(6 if i == 0 else 1, 6 if i == 4 else 1),
            )
            self.status_labels[key] = label
        panel.columnconfigure(0, weight=1)

        btn_row = ttk.Frame(root)
        btn_row.pack(fill="x", padx=14, pady=(0, 10))
        self.btn_install_uv = ttk.Button(
            btn_row, text="Install uv", command=self.install_uv
        )
        self.btn_install_uv.pack(side="left", padx=(0, 6))
        ttk.Button(
            btn_row, text="Install dependencies (uv sync)", command=self.run_uv_sync
        ).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Run ROCm setup", command=self.run_rocm_setup).pack(
            side="left", padx=(0, 6)
        )
        ttk.Label(btn_row, text="arch:", style="Sub.TLabel").pack(
            side="left", padx=(6, 2)
        )
        self.force_arch = tk.StringVar(value=FORCE_ARCH_CHOICES[0])
        ttk.Combobox(
            btn_row,
            textvariable=self.force_arch,
            values=FORCE_ARCH_CHOICES,
            state="readonly",
            width=11,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="⟳", width=3, command=self.refresh_status).pack(
            side="right"
        )

        # -- run configuration --------------------------------------------
        ttk.Label(root, text="RUN CONFIGURATION", style="Section.TLabel").pack(
            anchor="w", **pad
        )
        form = ttk.Frame(root)
        form.pack(fill="x", padx=14)

        ttk.Label(form, text="Model (HF ID, URL, or local path)").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.model = tk.StringVar()
        model_row = ttk.Frame(form)
        model_row.grid(row=0, column=1, columnspan=3, sticky="we", pady=2)
        ttk.Entry(model_row, textvariable=self.model, width=52).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(model_row, text="…", width=3, command=self.browse_model).pack(
            side="left", padx=(4, 0)
        )

        ttk.Label(form, text="Evaluate existing model (optional)").grid(
            row=1, column=0, sticky="w", pady=2
        )
        self.evaluate_model = tk.StringVar()
        ttk.Entry(form, textvariable=self.evaluate_model, width=55).grid(
            row=1, column=1, columnspan=3, sticky="we", pady=2
        )

        ttk.Label(form, text="Config preset").grid(row=2, column=0, sticky="w", pady=2)
        self.preset = tk.StringVar(value=next(iter(CONFIG_PRESETS)))
        ttk.Combobox(
            form,
            textvariable=self.preset,
            values=list(CONFIG_PRESETS),
            state="readonly",
            width=38,
        ).grid(row=2, column=1, sticky="w", pady=2)

        ttk.Label(form, text="Quantization").grid(
            row=2, column=2, sticky="e", padx=(10, 4)
        )
        self.quantization = tk.StringVar(value="(config)")
        ttk.Combobox(
            form,
            textvariable=self.quantization,
            values=["(config)", "none", "bnb_4bit"],
            state="readonly",
            width=10,
        ).grid(row=2, column=3, sticky="w")

        ttk.Label(form, text="Trials").grid(row=3, column=0, sticky="w", pady=2)
        nums = ttk.Frame(form)
        nums.grid(row=3, column=1, columnspan=3, sticky="w", pady=2)
        self.n_trials = tk.StringVar()
        ttk.Entry(nums, textvariable=self.n_trials, width=6).pack(side="left")
        ttk.Label(nums, text="  Batch size (0=auto)").pack(side="left")
        self.batch_size = tk.StringVar()
        ttk.Entry(nums, textvariable=self.batch_size, width=6).pack(
            side="left", padx=(4, 0)
        )
        ttk.Label(nums, text="  Max response tokens").pack(side="left")
        self.max_response_length = tk.StringVar()
        ttk.Entry(nums, textvariable=self.max_response_length, width=6).pack(
            side="left", padx=(4, 0)
        )
        ttk.Label(nums, text="  Seed").pack(side="left")
        self.seed = tk.StringVar()
        ttk.Entry(nums, textvariable=self.seed, width=8).pack(side="left", padx=(4, 0))

        checks = ttk.Frame(form)
        checks.grid(row=4, column=0, columnspan=4, sticky="w", pady=2)
        self.trust_remote_code = tk.BooleanVar()
        ttk.Checkbutton(
            checks, text="Trust remote code", variable=self.trust_remote_code
        ).pack(side="left", padx=(0, 14))
        self.print_responses = tk.BooleanVar()
        ttk.Checkbutton(
            checks, text="Print prompt/response pairs", variable=self.print_responses
        ).pack(side="left")

        ttk.Label(form, text="Extra CLI arguments").grid(
            row=5, column=0, sticky="w", pady=2
        )
        self.extra_args = tk.StringVar()
        ttk.Entry(form, textvariable=self.extra_args, width=55).grid(
            row=5, column=1, columnspan=3, sticky="we", pady=2
        )
        form.columnconfigure(1, weight=1)

        # -- command preview + launch --------------------------------------
        self.preview = tk.Text(
            root,
            height=2,
            bg=BG_PANEL,
            fg=ACCENT,
            font=("Consolas", 9),
            relief="flat",
            wrap="word",
            state="disabled",
            padx=8,
            pady=6,
        )
        self.preview.pack(fill="x", padx=14, pady=(8, 6))

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(
            bottom,
            text="▶  Launch Heretic",
            style="Accent.TButton",
            command=self.launch,
        ).pack(side="left")
        ttk.Button(
            bottom, text="Open repo folder", command=lambda: os.startfile(REPO_ROOT)
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            bottom,
            text="Windows/ROCm guide",
            command=lambda: os.startfile(REPO_ROOT / "WINDOWS_ROCM.md"),
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            bottom, text="Create desktop shortcut", command=self.create_shortcut
        ).pack(side="right")

    def _bind_preview_updates(self) -> None:
        for var in (
            self.model,
            self.evaluate_model,
            self.quantization,
            self.n_trials,
            self.batch_size,
            self.max_response_length,
            self.seed,
            self.trust_remote_code,
            self.print_responses,
            self.extra_args,
        ):
            var.trace_add("write", lambda *_: self.update_preview())
        self.update_preview()

    # -- status ---------------------------------------------------------
    def refresh_status(self) -> None:
        for label in self.status_labels.values():
            label.configure(text="…", fg=FG_DIM)
        threading.Thread(target=self._refresh_status_worker, daemon=True).start()

    def _refresh_status_worker(self) -> None:
        self.uv_path = find_uv()
        deps = venv_synced()
        marker = rocm_marker()
        torch = detect_torch_version()
        gpu = detect_gpu_name()

        def apply() -> None:
            def set_row(key: str, ok: bool, text: str, warn: bool = False) -> None:
                color = OK if ok else (WARN if warn else ERR)
                mark = "✔" if ok else ("●" if warn else "✘")
                self.status_labels[key].configure(text=f"{mark}  {text}", fg=color)

            set_row(
                "uv",
                bool(self.uv_path),
                f"uv package manager: {self.uv_path or 'not found — click Install uv'}",
            )
            set_row(
                "deps",
                deps,
                "dependencies installed (.venv)"
                if deps
                else "dependencies not installed — click Install dependencies",
            )
            if marker:
                set_row("rocm", True, f"ROCm configured: {marker}")
            else:
                set_row(
                    "rocm",
                    False,
                    "ROCm not configured — click Run ROCm setup (AMD GPUs only)",
                    warn=True,
                )
            if torch:
                is_gpu = "+rocm" in torch or "+cu" in torch
                set_row(
                    "torch",
                    is_gpu,
                    f"torch {torch}" + ("" if is_gpu else " (CPU-only build)"),
                    warn=not is_gpu,
                )
            else:
                set_row("torch", False, "torch not installed yet", warn=True)
            set_row("gpu", bool(gpu), f"GPU: {gpu or 'not detected'}", warn=not gpu)
            self.btn_install_uv.state(["disabled"] if self.uv_path else ["!disabled"])

        self.after(0, apply)

    # -- actions ----------------------------------------------------------
    def _uv(self) -> str | None:
        if not self.uv_path:
            self.uv_path = find_uv()
        if not self.uv_path:
            messagebox.showwarning(
                "uv not found",
                "The uv package manager is required.\n\n"
                'Click "Install uv" first, then retry.',
                parent=self,
            )
            return None
        return quote_arg(self.uv_path)

    def install_uv(self) -> None:
        run_in_console(UV_INSTALL_CMD)
        messagebox.showinfo(
            "Installing uv",
            "uv is being installed in the new console window.\n\n"
            "When it finishes, click the ⟳ button to refresh the status.",
            parent=self,
        )

    def run_uv_sync(self) -> None:
        uv = self._uv()
        if uv:
            run_in_console(f"{uv} sync")

    def run_rocm_setup(self) -> None:
        uv = self._uv()
        if not uv:
            return
        env = {}
        arch = self.force_arch.get()
        if arch != "auto-detect":
            env["HERETIC_FORCE_ARCH"] = arch
        run_in_console(f"{uv} run python scripts\\setup_rocm.py", env=env)

    def browse_model(self) -> None:
        path = filedialog.askdirectory(
            parent=self, title="Select local model directory"
        )
        if path:
            self.model.set(path)

    # -- command construction ----------------------------------------------
    def build_args(self) -> list[str]:
        args = []
        model = self.model.get().strip()
        if model:
            args.append(model)
        if self.evaluate_model.get().strip():
            args += ["--evaluate-model", self.evaluate_model.get().strip()]
        if self.quantization.get() != "(config)":
            args += ["--quantization", self.quantization.get()]
        for flag, var in (
            ("--n-trials", self.n_trials),
            ("--batch-size", self.batch_size),
            ("--max-response-length", self.max_response_length),
            ("--seed", self.seed),
        ):
            value = var.get().strip()
            if value:
                args += [flag, value]
        if self.trust_remote_code.get():
            args.append("--trust-remote-code")
        if self.print_responses.get():
            args.append("--print-responses")
        return args

    def build_command(self) -> str:
        command = "uv run heretic " + " ".join(quote_arg(a) for a in self.build_args())
        extra = self.extra_args.get().strip()
        if extra:
            command += " " + extra
        return command.rstrip()

    def update_preview(self) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "> " + self.build_command())
        self.preview.configure(state="disabled")

    # -- launch ------------------------------------------------------------
    def apply_preset(self) -> bool:
        preset_file = CONFIG_PRESETS.get(self.preset.get())
        if not preset_file:
            return True
        source = REPO_ROOT / preset_file
        target = REPO_ROOT / "config.toml"
        if target.is_file() and target.read_bytes() != source.read_bytes():
            if not messagebox.askyesno(
                "Overwrite config.toml?",
                f"Applying the preset will overwrite the existing config.toml\n"
                f"with {preset_file}.\n\n"
                f"A backup will be saved as config.toml.bak. Continue?",
                parent=self,
            ):
                return False
            shutil.copy2(target, REPO_ROOT / "config.toml.bak")
        shutil.copy2(source, target)
        return True

    def launch(self) -> None:
        if not self.model.get().strip() and not self.extra_args.get().strip():
            messagebox.showwarning(
                "No model specified",
                "Enter a Hugging Face model ID or URL (e.g.\n"
                "Qwen/Qwen2.5-0.5B-Instruct), or a local model path.",
                parent=self,
            )
            return
        if not self._uv():
            return
        if not self.apply_preset():
            return
        self._save_state()
        uv = quote_arg(self.uv_path or "uv")
        command = self.build_command()
        # Replace the leading display-form "uv" with the resolved path.
        command = uv + command[len("uv") :]
        run_in_console(command)

    # -- shortcut ------------------------------------------------------------
    def create_shortcut(self) -> None:
        if sys.platform != "win32":
            return
        bat = REPO_ROOT / "Heretic-Launcher.bat"
        script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            "$desktop = [Environment]::GetFolderPath('Desktop'); "
            '$s = $ws.CreateShortcut("$desktop\\Heretic.lnk"); '
            f"$s.TargetPath = '{bat}'; "
            f"$s.WorkingDirectory = '{REPO_ROOT}'; "
            "$s.Description = 'Heretic Launcher'; "
            "$s.Save()"
        )
        try:
            subprocess.check_call(
                ["powershell", "-NoProfile", "-Command", script],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            messagebox.showinfo(
                "Shortcut created",
                'A "Heretic" shortcut was created on your desktop.',
                parent=self,
            )
        except Exception as e:
            messagebox.showerror("Failed to create shortcut", str(e), parent=self)

    # -- state persistence ---------------------------------------------------
    def _save_state(self) -> None:
        state = {
            "model": self.model.get(),
            "evaluate_model": self.evaluate_model.get(),
            "preset": self.preset.get(),
            "quantization": self.quantization.get(),
            "n_trials": self.n_trials.get(),
            "batch_size": self.batch_size.get(),
            "max_response_length": self.max_response_length.get(),
            "seed": self.seed.get(),
            "trust_remote_code": self.trust_remote_code.get(),
            "print_responses": self.print_responses.get(),
            "extra_args": self.extra_args.get(),
        }
        try:
            STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load_state(self) -> None:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        setters = {
            "model": self.model,
            "evaluate_model": self.evaluate_model,
            "preset": self.preset,
            "quantization": self.quantization,
            "n_trials": self.n_trials,
            "batch_size": self.batch_size,
            "max_response_length": self.max_response_length,
            "seed": self.seed,
            "trust_remote_code": self.trust_remote_code,
            "print_responses": self.print_responses,
            "extra_args": self.extra_args,
        }
        for key, var in setters.items():
            if key in state:
                try:
                    var.set(state[key])
                except tk.TclError:
                    pass


def main() -> None:
    app = HereticLauncher()
    # Center on screen.
    app.update_idletasks()
    w, h = app.winfo_reqwidth(), app.winfo_reqheight()
    x = (app.winfo_screenwidth() - w) // 2
    y = (app.winfo_screenheight() - h) // 3
    app.geometry(f"+{x}+{y}")
    app.mainloop()


if __name__ == "__main__":
    main()
