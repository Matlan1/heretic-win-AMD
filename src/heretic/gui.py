# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Graphical launcher for Heretic.

Heretic itself runs as an interactive terminal application. This module
provides an optional graphical front-end that builds a Heretic command line
from a form and launches it in a terminal, for users who prefer not to type
command-line options by hand.

The form is generated automatically from the fields of the ``Settings`` model
in ``config.py``, so it stays in sync with the available configuration options
without any duplication: every scalar, boolean, and enumeration setting becomes
a widget, labelled with the setting's own description. The launcher depends only
on the Python standard library (``tkinter``) and does not import any of
Heretic's heavyweight machine-learning dependencies, so it starts instantly.
"""

from __future__ import annotations

import enum
import json
import os
import platform
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic.fields import FieldInfo

from .config import Settings

# Settings shown prominently at the top of the window, in this order. Any of
# these that do not exist in the current Settings model are silently skipped,
# and every remaining scalar setting is placed under "Advanced settings", so
# the form always covers the full set of configurable options.
PRIMARY_SETTINGS: tuple[str, ...] = (
    "model",
    "evaluate_model",
    "quantization",
    "n_trials",
    "n_startup_trials",
    "batch_size",
    "max_response_length",
    "seed",
    "system_prompt",
)

# Name of the field that holds the model to decensor. It is passed to Heretic
# as a positional argument rather than as an option.
MODEL_FIELD = "model"

# Name of the file used to remember the most recently entered values between
# sessions, stored in the user's configuration directory.
STATE_FILE_NAME = "launcher.json"


def configuration_directory() -> Path:
    """Return the per-user directory in which to store launcher state."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "heretic"


def heretic_executable() -> str:
    """Return the command used to invoke Heretic itself.

    The graphical launcher is installed alongside the ``heretic`` executable,
    so resolving it on the search path works both for ``pip`` installations and
    inside a ``uv`` environment. If it cannot be found, the bare name is
    returned and the user is expected to have it on their path.
    """
    return shutil.which("heretic") or "heretic"


def kebab_case(name: str) -> str:
    """Convert a setting's field name to its command-line option spelling."""
    return name.replace("_", "-")


def scalar_type(annotation: Any) -> type | None:
    """Return the underlying scalar type of a setting's annotation, or None.

    Optional annotations (``X | None``) are reduced to ``X``. Annotations that
    do not resolve to a single boolean, integer, float, string, or enumeration
    type (for example lists, dictionaries, and nested models) return None and
    are not represented in the form; those remain configurable through a
    configuration file or extra command-line arguments.
    """
    if get_origin(annotation) in (Union, UnionType):
        members = [
            argument for argument in get_args(annotation) if argument is not type(None)
        ]
        # Reduce unions such as "int | str" to a plain string field.
        if len(members) != 1:
            return str if str in members else None
        annotation = members[0]

    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            return annotation
        if annotation in (bool, int, float, str):
            return annotation
    return None


class SettingWidget:
    """A single labelled form control bound to one Heretic setting."""

    def __init__(
        self, parent: tk.Widget, name: str, field: FieldInfo, kind: type
    ) -> None:
        self.name = name
        self.field = field
        self.kind = kind
        self.default = None if field.is_required() else field.default

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)

        label_text = name.replace("_", " ").capitalize()
        if field.is_required():
            label_text += " (required)"
        ttk.Label(self.frame, text=label_text, width=24, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=2
        )

        self.variable: tk.Variable
        self.control: tk.Widget
        self._build_control()

        description = (field.description or "").strip()
        if description:
            ttk.Label(
                self.frame,
                text=description,
                style="Description.TLabel",
                wraplength=520,
                justify="left",
            ).grid(row=1, column=1, sticky="w", pady=(0, 6))

    def _build_control(self) -> None:
        if issubclass(self.kind, enum.Enum):
            values = [member.value for member in self.kind]
            # An optional enumeration may be left unset to use Heretic's default.
            if self.default is None:
                values = ["(default)"] + values
            initial = (
                self.default.value
                if isinstance(self.default, enum.Enum)
                else "(default)"
            )
            self.variable = tk.StringVar(value=initial)
            self.control = ttk.Combobox(
                self.frame, textvariable=self.variable, values=values, state="readonly"
            )
        elif self.kind is bool:
            self.variable = tk.BooleanVar(value=bool(self.default))
            self.control = ttk.Checkbutton(self.frame, variable=self.variable)
        else:
            initial = "" if self.default is None else str(self.default)
            self.variable = tk.StringVar(value=initial)
            self.control = ttk.Entry(self.frame, textvariable=self.variable)
        self.control.grid(row=0, column=1, sticky="we", pady=2)

    def grid(self, row: int) -> None:
        self.frame.grid(row=row, column=0, sticky="we", padx=12)

    def raw_value(self) -> str | bool:
        """Return the control's value as entered by the user."""
        return self.variable.get()

    def set_raw_value(self, value: str | bool) -> None:
        try:
            self.variable.set(value)
        except tk.TclError:
            pass

    def _default_as_text(self) -> str | None:
        if self.default is None:
            return None
        if isinstance(self.default, enum.Enum):
            return self.default.value
        return str(self.default)

    def command_arguments(self) -> list[str]:
        """Return the command-line arguments contributed by this setting.

        Only settings that differ from their default produce arguments, so the
        generated command line stays as short as a hand-written one.
        """
        value = self.raw_value()
        option = f"--{kebab_case(self.name)}"

        if self.kind is bool:
            if bool(value) == bool(self.default):
                return []
            return [option] if value else [f"--no-{kebab_case(self.name)}"]

        text = str(value).strip()
        if not text or text == "(default)":
            return []
        if text == self._default_as_text():
            return []
        return [option, text]


class LauncherWindow(tk.Tk):
    """The main launcher window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Heretic")
        self.minsize(620, 640)

        self._configure_styles()
        self.widgets: dict[str, SettingWidget] = {}
        self._build_layout()
        self._load_state()
        self._update_command_preview()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("Description.TLabel", foreground="#6b7280")
        style.configure("Heading.TLabel", font=("TkDefaultFont", 11, "bold"))
        style.configure("Command.TLabel", font=("TkFixedFont", 9))

    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(12, 12, 12, 4))
        header.pack(fill="x")
        ttk.Label(header, text="Heretic", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Configure a run and launch it in a terminal.",
            style="Description.TLabel",
        ).pack(anchor="w")

        body = self._build_scrollable_body()
        primary, advanced = self._partition_settings()

        row = 0
        for name in primary:
            self._add_widget(body, name, row)
            row += 1

        ttk.Separator(body, orient="horizontal").grid(
            row=row, column=0, sticky="we", padx=12, pady=10
        )
        row += 1
        ttk.Label(body, text="Advanced settings", style="Heading.TLabel").grid(
            row=row, column=0, sticky="w", padx=12, pady=(0, 4)
        )
        row += 1
        for name in advanced:
            self._add_widget(body, name, row)
            row += 1

        self._build_footer()

    def _build_scrollable_body(self) -> ttk.Frame:
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)

        body.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window, width=event.width),
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Enable mouse-wheel scrolling while the pointer is over the form.
        def on_mouse_wheel(event: tk.Event) -> None:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        body.bind(
            "<Enter>", lambda event: canvas.bind_all("<MouseWheel>", on_mouse_wheel)
        )
        body.bind("<Leave>", lambda event: canvas.unbind_all("<MouseWheel>"))

        body.columnconfigure(0, weight=1)
        return body

    def _partition_settings(self) -> tuple[list[str], list[str]]:
        """Split the scalar settings into primary and advanced groups."""
        scalar_fields = {
            name: field
            for name, field in Settings.model_fields.items()
            if scalar_type(field.annotation) is not None
        }
        primary = [name for name in PRIMARY_SETTINGS if name in scalar_fields]
        advanced = [name for name in scalar_fields if name not in primary]
        return primary, advanced

    def _add_widget(self, parent: ttk.Frame, name: str, row: int) -> None:
        field = Settings.model_fields[name]
        kind = scalar_type(field.annotation)
        assert kind is not None
        widget = SettingWidget(parent, name, field, kind)
        widget.grid(row)
        widget.variable.trace_add("write", lambda *_: self._update_command_preview())
        self.widgets[name] = widget

    def _build_footer(self) -> None:
        footer = ttk.Frame(self, padding=12)
        footer.pack(fill="x")

        self.command_label = ttk.Label(
            footer, style="Command.TLabel", wraplength=580, justify="left"
        )
        self.command_label.pack(fill="x", pady=(0, 10))

        buttons = ttk.Frame(footer)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Copy command", command=self._copy_command).pack(
            side="left"
        )
        ttk.Button(buttons, text="Launch", command=self._launch).pack(side="right")

    def _command_line(self, program: str) -> list[str]:
        """Build the full Heretic command from the current form values.

        The model is passed positionally; every other changed setting becomes an
        option. Widgets are processed in display order so the command reads the
        same way each time. ``program`` is the leading token: the resolved
        executable when launching, or the plain name "heretic" when displaying
        the command for the user to read or copy.
        """
        command = [program]

        model = (
            str(self.widgets[MODEL_FIELD].raw_value()).strip()
            if MODEL_FIELD in self.widgets
            else ""
        )
        if model:
            command.append(model)

        for name, widget in self.widgets.items():
            if name == MODEL_FIELD:
                continue
            command.extend(widget.command_arguments())
        return command

    def _command_preview(self) -> str:
        return " ".join(quote(argument) for argument in self._command_line("heretic"))

    def _update_command_preview(self) -> None:
        self.command_label.configure(text=self._command_preview())

    def _copy_command(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._command_preview())

    def _launch(self) -> None:
        if (
            MODEL_FIELD in self.widgets
            and not str(self.widgets[MODEL_FIELD].raw_value()).strip()
        ):
            messagebox.showwarning(
                "Model required",
                "Enter a Hugging Face model ID or a path to a local model.",
            )
            return
        self._save_state()
        try:
            launch_in_terminal(self._command_line(heretic_executable()))
        except LaunchError as error:
            messagebox.showerror(
                "Could not open a terminal",
                f"{error}\n\nUse the “Copy command” button and run it in a terminal yourself.",
            )

    def _save_state(self) -> None:
        state = {name: widget.raw_value() for name, widget in self.widgets.items()}
        try:
            directory = configuration_directory()
            directory.mkdir(parents=True, exist_ok=True)
            (directory / STATE_FILE_NAME).write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _load_state(self) -> None:
        try:
            state = json.loads(
                (configuration_directory() / STATE_FILE_NAME).read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            return
        for name, value in state.items():
            if name in self.widgets:
                self.widgets[name].set_raw_value(value)


class LaunchError(Exception):
    """Raised when no terminal could be opened to run Heretic."""


def quote(argument: str) -> str:
    """Quote a single command-line argument for display and shell execution."""
    if argument and not any(character in argument for character in " \t\"'\\&|<>()"):
        return argument
    return '"' + argument.replace('"', '\\"') + '"'


def launch_in_terminal(command: list[str]) -> None:
    """Open a terminal window running ``command``.

    Heretic is an interactive terminal application, so it is started in a fresh
    terminal window rather than as a background process. The mechanism differs
    per platform; if no terminal can be found, ``LaunchError`` is raised.
    """
    system = platform.system()
    if system == "Windows":
        subprocess.Popen(
            ["cmd", "/c", "start", "Heretic", "cmd", "/k", *command],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return

    inner = " ".join(quote(argument) for argument in command)

    if system == "Darwin":
        script = "cd " + quote(str(Path.cwd())) + " && " + inner
        subprocess.Popen(
            [
                "osascript",
                "-e",
                'tell application "Terminal" to do script ' + json.dumps(script),
            ]
        )
        return

    # Linux and other Unix systems: try a sequence of common terminal emulators.
    for terminal in (
        "x-terminal-emulator",
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "xterm",
    ):
        if shutil.which(terminal) is None:
            continue
        if terminal == "gnome-terminal":
            subprocess.Popen([terminal, "--", "bash", "-lc", inner + "; exec bash"])
        else:
            subprocess.Popen(
                [terminal, "-e", "bash -lc " + quote(inner + "; exec bash")]
            )
        return

    raise LaunchError("No supported terminal emulator was found on this system.")


def main() -> None:
    """Entry point for the ``heretic-gui`` command."""
    try:
        window = LauncherWindow()
    except tk.TclError as error:
        sys.stderr.write(
            "The graphical launcher requires a working Tk installation.\n"
            "On Linux, install the system package for Tkinter (for example "
            "“python3-tk”) and ensure a display is available.\n"
            f"Details: {error}\n"
        )
        raise SystemExit(1) from error
    window.mainloop()


if __name__ == "__main__":
    main()
