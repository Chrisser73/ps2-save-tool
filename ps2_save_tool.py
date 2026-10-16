from __future__ import annotations

from pathlib import Path
import copy
from datetime import datetime
import re
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from PIL import Image, ImageTk

from card_tools import archive_digest, archive_hashes, clone_file, export_archive_folder, restore_backup, timestamped_backup
from card_windows import CompareWindow, DualPS2CardManager

from formats import (
    PS1MemoryCard,
    PS1Save,
    SaveArchive,
    create_ps1_memory_card,
    delete_save_from_card,
    detect_path,
    extract_icon_frames_rgba,
    import_save_into_card,
    read_ps1_card,
    read_ps1_single,
    read_psu,
    read_psv,
    save_ps1_card,
    validate_ps1_card,
    write_ps1_single,
    write_psu,
    write_psv,
)
from formats.common import SaveEntry, human_size
from formats.detect import UnknownFormatError
from formats.memorycard import (
    CardInfo,
    MemoryCardBackendUnavailable,
    create_memory_card,
    delete_save,
    export_save_as_psu,
    export_save_as_max,
    import_save,
    inspect_memory_card,
    load_save_archive,
    migrate_card_saves,
    replace_save_from_archive,
    validate_card,
    write_archive_as_max,
    load_external_ps2_save,
    rebuild_card_to_new_image,
)
from ps1icon import render_ps1_icon
from ps2icon import parse_icon_model, parse_icon_sys, render_builtin_ps2_icon, render_icon, render_placeholder, render_texture, update_icon_sys_title

APP_NAME = "PS2 Save Tool"
APP_VERSION = "0.4.1"


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _asset_path(*names: str) -> Path | None:
    # A frozen one-file build uses only PyInstaller-bundled assets. No app.ico/app.png
    # file is required beside the EXE. Source runs load the files from the project root.
    if getattr(sys, "frozen", False):
        roots = [Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))]
    else:
        roots = [_runtime_root()]
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _fmt_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "—"


def _ps2_game_id(dirname: str) -> str:
    """Return a normalized PS2 product/save id prefix such as BESLES-52622."""
    match = re.search(r"([A-Z][A-Z0-9]{3,10}-\d{4,6})", dirname.upper())
    return match.group(1) if match else dirname.upper().strip()


def _replace_archive_title(archive: SaveArchive, title: str) -> None:
    icon = archive.get("icon.sys")
    if icon is None:
        raise ValueError("This PS2 save has no icon.sys metadata file")
    icon.data = update_icon_sys_title(icon.data, title)


class MetadataDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, dirname: str, title: str, allow_directory: bool = True):
        super().__init__(parent)
        self.title("Edit PS2 Save Metadata")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: tuple[str, str] | None = None

        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Directory / Save ID:").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.dirname_var = tk.StringVar(value=dirname)
        self.dirname_entry = ttk.Entry(body, textvariable=self.dirname_var, width=44)
        self.dirname_entry.grid(row=0, column=1, sticky="ew", pady=4)
        if not allow_directory:
            self.dirname_entry.state(["disabled"])

        ttk.Label(body, text="Browser title:").grid(row=1, column=0, sticky="nw", padx=(0, 10), pady=4)
        self.title_text = tk.Text(body, width=44, height=2, wrap="none")
        self.title_text.grid(row=1, column=1, sticky="ew", pady=4)
        self.title_text.insert("1.0", title.replace(" / ", "\n", 1) if " / " in title else title)
        ttk.Label(body, text="Use one newline in the title if you want a two-line PS2 Browser title.", foreground="#666666").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 10)
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Apply", command=self._accept).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Control-Return>", lambda _e: self._accept())
        self.wait_visibility()
        self.focus_set()

    def _accept(self) -> None:
        dirname = self.dirname_var.get().strip()
        title = self.title_text.get("1.0", "end-1c").strip()
        if not dirname:
            messagebox.showerror(APP_NAME, "Directory / Save ID cannot be empty.", parent=self)
            return
        try:
            raw = dirname.encode("ascii")
        except UnicodeEncodeError:
            messagebox.showerror(APP_NAME, "PS2 directory names must use ASCII characters.", parent=self)
            return
        if len(raw) > 31:
            messagebox.showerror(APP_NAME, "PS2 directory names are limited to 31 ASCII bytes.", parent=self)
            return
        if not title:
            messagebox.showerror(APP_NAME, "Browser title cannot be empty.", parent=self)
            return
        self.result = (dirname, title)
        self.destroy()


class FormatChoiceDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, formats: tuple[str, ...]):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: str | None = None
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Output format").pack(anchor="w")
        self.value = tk.StringVar(value=formats[0])
        ttk.Combobox(body, textvariable=self.value, values=formats, state="readonly", width=18).pack(fill="x", pady=(6, 12))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Continue", command=self._accept).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._accept())
        self.wait_visibility()
        self.focus_set()

    def _accept(self) -> None:
        self.result = self.value.get().upper()
        self.destroy()


class CreateCardDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("Create Memory Card")
        self.resizable(False, False)
        self.result: tuple[str, int, bool] | None = None
        self.transient(parent)
        self.grab_set()

        body = ttk.Frame(self, padding=18)
        body.grid(sticky="nsew")

        ttk.Label(body, text="System").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.system_var = tk.StringVar(value="PlayStation 2")
        system = ttk.Combobox(
            body,
            textvariable=self.system_var,
            values=("PlayStation 2", "PlayStation 1"),
            state="readonly",
            width=24,
        )
        system.grid(row=1, column=0, sticky="ew")
        system.bind("<<ComboboxSelected>>", lambda _e: self._update_controls())

        ttk.Label(body, text="Card size").grid(row=2, column=0, sticky="w", pady=(12, 6))
        self.size_var = tk.StringVar(value="8 MB")
        self.size_combo = ttk.Combobox(
            body,
            textvariable=self.size_var,
            values=("8 MB", "16 MB", "32 MB", "64 MB"),
            state="readonly",
            width=24,
        )
        self.size_combo.grid(row=3, column=0, sticky="ew")
        self.ps1_size_label = ttk.Label(body, text="PS1 cards use the standard 128 KB / 15-save-block layout.")

        self.no_ecc_var = tk.BooleanVar(value=False)
        self.no_ecc_check = ttk.Checkbutton(
            body,
            text="Raw image without ECC (recommended for .mc2)",
            variable=self.no_ecc_var,
        )
        self.no_ecc_check.grid(row=5, column=0, sticky="w", pady=(12, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Continue", command=self._accept).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._accept())
        self._update_controls()
        self.wait_visibility()
        self.focus_set()

    def _update_controls(self) -> None:
        ps1 = self.system_var.get() == "PlayStation 1"
        if ps1:
            self.size_combo.grid_remove()
            self.ps1_size_label.grid(row=3, column=0, sticky="w")
            self.no_ecc_check.state(["disabled"])
        else:
            self.ps1_size_label.grid_remove()
            self.size_combo.grid()
            self.no_ecc_check.state(["!disabled"])

    def _accept(self) -> None:
        system = "PS1" if self.system_var.get() == "PlayStation 1" else "PS2"
        size = 1 if system == "PS1" else int(self.size_var.get().split()[0])
        self.result = (system, size, self.no_ecc_var.get())
        self.destroy()


class PS2SaveTool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1140x780")
        self.minsize(940, 650)
        self._app_icon_photo = None
        self._apply_app_icon()

        self.current_path: Path | None = None
        self.current_format: str | None = None
        self.current_archive: SaveArchive | None = None
        self.current_card: CardInfo | None = None
        self.current_card_archive: SaveArchive | None = None
        self.current_ps1_save: PS1Save | None = None
        self.current_ps1_card: PS1MemoryCard | None = None
        self.last_dir = Path.cwd()
        self.read_only_var = tk.BooleanVar(value=False)
        self.auto_backup_var = tk.BooleanVar(value=True)
        self.last_card_backup: Path | None = None
        self.ps2_filter_var = tk.StringVar(value="")
        self.ps1_filter_var = tk.StringVar(value="")

        self.ps2_icon_sys = None
        self.ps2_icon_model = None
        self.ps2_preview_mode = tk.StringVar(value="Rendered")
        self.ps2_preview_cache: dict[str, object] = {}
        self.ps1_icon_frames: list[bytes] = []
        self.preview_kind: str | None = None
        self.preview_target: ttk.Label | None = None
        self.icon_started = time.monotonic()
        self.preview_photo = None
        self.welcome_logo_photo = None

        self._build_ui()
        self._show_welcome()

    def _apply_app_icon(self) -> None:
        """Apply the bundled application icon without requiring external runtime assets."""
        ico = _asset_path("app.ico", "icon.ico")
        png = _asset_path("app.png", "icon.png")
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PS2SaveTool")
            except Exception:
                pass
            if ico is not None:
                try:
                    self.iconbitmap(str(ico))
                    return
                except Exception:
                    pass
        if png is not None:
            try:
                self._app_icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._app_icon_photo)
            except Exception:
                pass
        elif ico is not None:
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=(14, 12, 14, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="File").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(top, textvariable=self.path_var)
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.path_entry.bind("<Return>", lambda _e: self._open_from_entry())
        ttk.Button(top, text="Browse…", command=self._browse).grid(row=0, column=2, padx=(8, 0))

        self.detect_var = tk.StringVar(value="Select a save file or memory card.")
        ttk.Label(top, textvariable=self.detect_var).grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))

        tool_tabs = ttk.Notebook(top)
        tool_tabs.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        tools_tab = ttk.Frame(tool_tabs, padding=(10, 8))
        experimental_tab = ttk.Frame(tool_tabs, padding=(10, 8))
        tool_tabs.add(tools_tab, text="Tools")
        tool_tabs.add(experimental_tab, text="Experimental")

        card_tools_box = ttk.LabelFrame(tools_tab, text="Memory Card Tools", padding=8)
        card_tools_box.pack(side="left", fill="x")
        ttk.Button(card_tools_box, text="Create Memory Card…", command=self._create_card).pack(side="left")
        ttk.Button(card_tools_box, text="Merge Cards…", command=self._open_dual_card_manager).pack(side="left", padx=(8, 0))

        save_tools_box = ttk.LabelFrame(tools_tab, text="PS2 Save Tools", padding=8)
        save_tools_box.pack(side="left", fill="x", padx=(10, 0))
        ttk.Button(save_tools_box, text="Inject into PS3 PSV…", command=self._inject_ps3_psv).pack(side="left")

        ttk.Label(
            experimental_tab,
            text="Experimental operations never modify the source card in place.",
        ).pack(side="left", padx=(0, 12))
        ttk.Button(experimental_tab, text="Rebuild / Repair PS2 Card…", command=self._experimental_repair_card).pack(side="left")

        self.content = ttk.Frame(self, padding=(14, 4, 14, 8))
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.save_frame = ttk.Frame(self.content)
        self.card_frame = ttk.Frame(self.content)
        self.ps1_save_frame = ttk.Frame(self.content)
        self.ps1_card_frame = ttk.Frame(self.content)
        self.welcome_frame = ttk.Frame(self.content)
        for frame in (self.save_frame, self.card_frame, self.ps1_save_frame, self.ps1_card_frame, self.welcome_frame):
            frame.grid(row=0, column=0, sticky="nsew")

        self._build_ps2_save_frame()
        self._build_ps2_card_frame()
        self._build_ps1_save_frame()
        self._build_ps1_card_frame()

        self.status_var = tk.StringVar(value="Ready")
        ttk.Separator(self).grid(row=2, column=0, sticky="ew")
        footer = ttk.Frame(self, padding=(14, 6))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        year = datetime.now().year
        ttk.Label(footer, text=f"© {year} by Chrisser73 · ").grid(row=0, column=1, sticky="e")
        github = tk.Label(footer, text="Follow me on GitHub", fg="#0067c0", cursor="hand2")
        github.grid(row=0, column=2, sticky="e")
        github.bind("<Button-1>", lambda _e: webbrowser.open_new_tab("https://github.com/Chrisser73/"))

    def _build_ps2_save_frame(self) -> None:
        frame = self.save_frame
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(1, weight=1)

        meta = ttk.LabelFrame(frame, text="Save Inspector", padding=10)
        meta.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 10))
        meta.columnconfigure(1, weight=1)
        self.save_meta_vars = {key: tk.StringVar(value="—") for key in (
            "format", "dirname", "title", "size", "created", "modified", "signature"
        )}
        labels = (
            ("Format", "format"), ("Directory", "dirname"), ("Game title", "title"),
            ("Payload size", "size"), ("Created", "created"), ("Modified", "modified"),
            ("PSV signature", "signature"),
        )
        for row, (caption, key) in enumerate(labels):
            ttk.Label(meta, text=caption + ":").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=2)
            ttk.Label(meta, textvariable=self.save_meta_vars[key], wraplength=600).grid(row=row, column=1, sticky="w", pady=2)

        files_box = ttk.LabelFrame(frame, text="Contents", padding=8)
        files_box.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        files_box.columnconfigure(0, weight=1)
        files_box.rowconfigure(0, weight=1)
        self.file_tree = ttk.Treeview(files_box, columns=("size", "mode", "attr", "created", "modified"), show="tree headings")
        for col, text in (("#0", "File"), ("size", "Size"), ("mode", "Mode"), ("attr", "Attribute"), ("created", "Created"), ("modified", "Modified")):
            self.file_tree.heading(col, text=text)
        self.file_tree.column("#0", width=230, stretch=True)
        self.file_tree.column("size", width=95, anchor="e")
        self.file_tree.column("mode", width=82, anchor="center")
        self.file_tree.column("attr", width=82, anchor="center")
        self.file_tree.column("created", width=145)
        self.file_tree.column("modified", width=145)
        scroll = ttk.Scrollbar(files_box, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=scroll.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        preview_box = ttk.LabelFrame(right, text="PS2 Icon Preview", padding=10)
        preview_box.grid(row=0, column=0, sticky="ew")
        preview_modes = ttk.Frame(preview_box)
        preview_modes.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(
            preview_modes, text="Rendered", value="Rendered", variable=self.ps2_preview_mode,
            command=self._ps2_preview_mode_changed,
        ).pack(side="left")
        ttk.Radiobutton(
            preview_modes, text="Texture", value="Texture", variable=self.ps2_preview_mode,
            command=self._ps2_preview_mode_changed,
        ).pack(side="left", padx=(10, 0))
        self.preview_label = ttk.Label(preview_box, anchor="center")
        self.preview_label.pack(fill="both", expand=True)

        convert_box = ttk.LabelFrame(right, text="Convert", padding=10)
        convert_box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(convert_box, text="Output format").grid(row=0, column=0, sticky="w")
        self.convert_var = tk.StringVar(value="PSU")
        self.convert_combo = ttk.Combobox(convert_box, textvariable=self.convert_var, values=("PSV", "PSU"), state="readonly", width=12)
        self.convert_combo.grid(row=0, column=1, sticky="e", padx=(12, 0))
        convert_box.columnconfigure(0, weight=1)
        ttk.Button(convert_box, text="Convert…", command=self._convert_ps2_save).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(convert_box, text="Export Save Folder…", command=self._export_current_ps2_save_folder).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(convert_box, text="Edit Metadata…", command=self._edit_current_ps2_metadata).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        warning_box = ttk.LabelFrame(right, text="Validation", padding=10)
        warning_box.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self.warning_var = tk.StringVar(value="No warnings.")
        ttk.Label(warning_box, textvariable=self.warning_var, wraplength=330, justify="left").pack(fill="both", expand=True)

    def _build_ps2_card_frame(self) -> None:
        frame = self.card_frame
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)
        frame.rowconfigure(1, weight=1)

        info = ttk.LabelFrame(frame, text="PS2 Memory Card", padding=10)
        info.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        info.columnconfigure(5, weight=1)
        self.card_size_var = tk.StringVar(value="—")
        self.card_free_var = tk.StringVar(value="—")
        self.card_count_var = tk.StringVar(value="—")
        ttk.Label(info, text="Image size:").grid(row=0, column=0, sticky="w")
        ttk.Label(info, textvariable=self.card_size_var).grid(row=0, column=1, sticky="w", padx=(5, 20))
        ttk.Label(info, text="Free:").grid(row=0, column=2, sticky="w")
        ttk.Label(info, textvariable=self.card_free_var).grid(row=0, column=3, sticky="w", padx=(5, 20))
        ttk.Label(info, text="Saves:").grid(row=0, column=4, sticky="w")
        ttk.Label(info, textvariable=self.card_count_var).grid(row=0, column=5, sticky="w", padx=(5, 20))
        ttk.Button(info, text="Validate", command=self._validate_ps2_card).grid(row=0, column=6, padx=(8, 0))

        usage = ttk.Frame(info)
        usage.grid(row=1, column=0, columnspan=8, sticky="ew", pady=(9, 0))
        usage.columnconfigure(1, weight=1)
        self.card_usage_var = tk.DoubleVar(value=0.0)
        self.card_usage_text_var = tk.StringVar(value="Used 0%")
        ttk.Label(usage, textvariable=self.card_usage_text_var).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.card_usage_bar = ttk.Progressbar(usage, variable=self.card_usage_var, maximum=100.0)
        self.card_usage_bar.grid(row=0, column=1, sticky="ew")

        tools = ttk.Frame(info)
        tools.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(9, 0))
        tools.columnconfigure(1, weight=1)
        ttk.Label(tools, text="Filter:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        filter_entry = ttk.Entry(tools, textvariable=self.ps2_filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew")
        filter_entry.bind("<KeyRelease>", lambda _e: self._refresh_ps2_card_tree())
        ttk.Checkbutton(tools, text="Read only", variable=self.read_only_var).grid(row=0, column=2, padx=(10, 0))
        ttk.Checkbutton(tools, text="Auto backup", variable=self.auto_backup_var).grid(row=0, column=3, padx=(10, 0))

        operations = ttk.LabelFrame(info, text="Current Card & Save Actions", padding=7)
        operations.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(9, 0))
        ttk.Button(operations, text="Batch Import…", command=self._import_ps2_card_save).pack(side="left")
        ttk.Button(operations, text="Batch Export…", command=self._batch_export_ps2_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(operations, text="Clone Card…", command=self._clone_current_card).pack(side="left", padx=(14, 0))
        ttk.Button(operations, text="Migrate Card…", command=self._migrate_current_card).pack(side="left", padx=(6, 0))
        ttk.Button(operations, text="Restore Backup…", command=self._restore_last_backup).pack(side="left", padx=(6, 0))
        ttk.Button(operations, text="Compare Selected Save…", command=self._compare_ps2_save).pack(side="left", padx=(14, 0))

        saves_box = ttk.LabelFrame(frame, text="Saves", padding=8)
        saves_box.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        saves_box.columnconfigure(0, weight=1)
        saves_box.rowconfigure(0, weight=1)
        self.card_tree = ttk.Treeview(saves_box, columns=("title", "size", "dir"), show="headings", selectmode="extended")
        self.card_tree.heading("title", text="Game / Save")
        self.card_tree.heading("size", text="Size")
        self.card_tree.heading("dir", text="Directory")
        self.card_tree.column("title", width=260, stretch=True)
        self.card_tree.column("size", width=80, anchor="e")
        self.card_tree.column("dir", width=150, stretch=True)
        scroll = ttk.Scrollbar(saves_box, orient="vertical", command=self.card_tree.yview)
        self.card_tree.configure(yscrollcommand=scroll.set)
        self.card_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.card_tree.bind("<<TreeviewSelect>>", self._ps2_card_selection_changed)

        detail = ttk.Frame(frame)
        detail.grid(row=1, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(1, weight=1)
        selected = ttk.LabelFrame(detail, text="Selected Save", padding=10)
        selected.grid(row=0, column=0, sticky="ew")
        selected.columnconfigure(1, weight=1)
        self.card_sel_dir_var = tk.StringVar(value="—")
        self.card_sel_title_var = tk.StringVar(value="—")
        self.card_sel_info_var = tk.StringVar(value="—")
        ttk.Label(selected, text="Directory:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(selected, textvariable=self.card_sel_dir_var).grid(row=0, column=1, sticky="w")
        ttk.Label(selected, text="Title:").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Label(selected, textvariable=self.card_sel_title_var, wraplength=500).grid(row=1, column=1, sticky="w")
        ttk.Label(selected, text="Details:").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Label(selected, textvariable=self.card_sel_info_var, wraplength=420).grid(row=2, column=1, sticky="w")
        ttk.Button(selected, text="Edit Metadata…", command=self._edit_selected_ps2_card_metadata).grid(
            row=0, column=2, rowspan=3, sticky="e", padx=(12, 0)
        )

        content = ttk.Frame(detail)
        content.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)
        icon_box = ttk.LabelFrame(content, text="PS2 Icon Preview", padding=10)
        icon_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        preview_modes = ttk.Frame(icon_box)
        preview_modes.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(
            preview_modes, text="Rendered", value="Rendered", variable=self.ps2_preview_mode,
            command=self._ps2_preview_mode_changed,
        ).pack(side="left")
        ttk.Radiobutton(
            preview_modes, text="Texture", value="Texture", variable=self.ps2_preview_mode,
            command=self._ps2_preview_mode_changed,
        ).pack(side="left", padx=(10, 0))
        self.card_preview_label = ttk.Label(icon_box, anchor="center")
        self.card_preview_label.pack(fill="both", expand=True)
        files_box = ttk.LabelFrame(content, text="Files", padding=8)
        files_box.grid(row=0, column=1, sticky="nsew")
        files_box.columnconfigure(0, weight=1)
        files_box.rowconfigure(0, weight=1)
        self.card_files_tree = ttk.Treeview(files_box, columns=("size",), show="tree headings", selectmode="extended")
        self.card_files_tree.heading("#0", text="File")
        self.card_files_tree.heading("size", text="Size")
        self.card_files_tree.column("#0", width=220, stretch=True)
        self.card_files_tree.column("size", width=90, anchor="e")
        self.card_files_tree.grid(row=0, column=0, sticky="nsew")

        file_actions = ttk.Frame(files_box)
        file_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(file_actions, text="Extract…", command=self._extract_selected_card_files).pack(side="left")
        ttk.Button(file_actions, text="Replace…", command=self._replace_selected_card_file).pack(side="left", padx=(5, 0))
        ttk.Button(file_actions, text="Add…", command=self._add_card_file).pack(side="left", padx=(5, 0))
        ttk.Button(file_actions, text="Delete", command=self._delete_selected_card_files).pack(side="left", padx=(5, 0))
        ttk.Button(file_actions, text="Export Folder…", command=self._extract_ps2_save_folder).pack(side="right")

        buttons = ttk.Frame(detail)
        buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(buttons, text="Export:").pack(side="left")
        self.card_export_var = tk.StringVar(value="PSU")
        self.card_export_combo = ttk.Combobox(
            buttons, textvariable=self.card_export_var, values=("PSU", "PSV", "MAX"),
            state="readonly", width=7
        )
        self.card_export_combo.pack(side="left", padx=(6, 5))
        ttk.Button(buttons, text="Export…", command=self._export_selected_ps2_card_format).pack(side="left")
        ttk.Button(buttons, text="Delete Save", command=self._delete_ps2_card_save).pack(side="right")

    def _build_ps1_save_frame(self) -> None:
        frame = self.ps1_save_frame
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(1, weight=1)

        meta = ttk.LabelFrame(frame, text="PS1 Save Inspector", padding=10)
        meta.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 10))
        meta.columnconfigure(1, weight=1)
        self.ps1_meta_vars = {key: tk.StringVar(value="—") for key in (
            "format", "filename", "title", "product", "identifier", "blocks", "size", "signature"
        )}
        for row, (caption, key) in enumerate((
            ("Format", "format"), ("Save name", "filename"), ("Title", "title"),
            ("Product code", "product"), ("Identifier", "identifier"), ("Blocks", "blocks"),
            ("Payload size", "size"), ("PSV signature", "signature"),
        )):
            ttk.Label(meta, text=caption + ":").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=2)
            ttk.Label(meta, textvariable=self.ps1_meta_vars[key], wraplength=600).grid(row=row, column=1, sticky="w", pady=2)

        notes = ttk.LabelFrame(frame, text="Format Details", padding=10)
        notes.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.ps1_detail_var = tk.StringVar(value="—")
        ttk.Label(notes, textvariable=self.ps1_detail_var, justify="left", wraplength=650).pack(anchor="nw")

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        icon_box = ttk.LabelFrame(right, text="PS1 Icon Preview", padding=10)
        icon_box.grid(row=0, column=0, sticky="ew")
        self.ps1_preview_label = ttk.Label(icon_box, anchor="center")
        self.ps1_preview_label.pack(fill="both", expand=True)

        convert_box = ttk.LabelFrame(right, text="Convert", padding=10)
        convert_box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(convert_box, text="Output format").grid(row=0, column=0, sticky="w")
        self.ps1_convert_var = tk.StringVar(value="MCS")
        self.ps1_convert_combo = ttk.Combobox(convert_box, textvariable=self.ps1_convert_var, values=("PSV", "MCS", "PSX", "RAW"), state="readonly", width=12)
        self.ps1_convert_combo.grid(row=0, column=1, sticky="e", padx=(12, 0))
        convert_box.columnconfigure(0, weight=1)
        ttk.Button(convert_box, text="Convert…", command=self._convert_ps1_save).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        warning_box = ttk.LabelFrame(right, text="Validation", padding=10)
        warning_box.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self.ps1_warning_var = tk.StringVar(value="No warnings.")
        ttk.Label(warning_box, textvariable=self.ps1_warning_var, wraplength=330, justify="left").pack(fill="both", expand=True)

    def _build_ps1_card_frame(self) -> None:
        frame = self.ps1_card_frame
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)
        frame.rowconfigure(1, weight=1)

        info = ttk.LabelFrame(frame, text="PS1 Memory Card", padding=10)
        info.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        info.columnconfigure(7, weight=1)
        self.ps1_card_format_var = tk.StringVar(value="—")
        self.ps1_card_size_var = tk.StringVar(value="—")
        self.ps1_card_free_var = tk.StringVar(value="—")
        self.ps1_card_count_var = tk.StringVar(value="—")
        ttk.Label(info, text="Format:").grid(row=0, column=0, sticky="w")
        ttk.Label(info, textvariable=self.ps1_card_format_var).grid(row=0, column=1, sticky="w", padx=(5, 18))
        ttk.Label(info, text="Image:").grid(row=0, column=2, sticky="w")
        ttk.Label(info, textvariable=self.ps1_card_size_var).grid(row=0, column=3, sticky="w", padx=(5, 18))
        ttk.Label(info, text="Free blocks:").grid(row=0, column=4, sticky="w")
        ttk.Label(info, textvariable=self.ps1_card_free_var).grid(row=0, column=5, sticky="w", padx=(5, 18))
        ttk.Label(info, text="Saves:").grid(row=0, column=6, sticky="w")
        ttk.Label(info, textvariable=self.ps1_card_count_var).grid(row=0, column=7, sticky="w", padx=(5, 18))
        ttk.Button(info, text="Validate", command=self._validate_ps1_card).grid(row=0, column=8, padx=(8, 0))

        tools = ttk.Frame(info)
        tools.grid(row=1, column=0, columnspan=10, sticky="ew", pady=(9, 0))
        tools.columnconfigure(1, weight=1)
        ttk.Label(tools, text="Filter:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        filter_entry = ttk.Entry(tools, textvariable=self.ps1_filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew")
        filter_entry.bind("<KeyRelease>", lambda _e: self._refresh_ps1_card_tree())
        ttk.Checkbutton(tools, text="Read only", variable=self.read_only_var).grid(row=0, column=2, padx=(10, 0))
        ttk.Checkbutton(tools, text="Auto backup", variable=self.auto_backup_var).grid(row=0, column=3, padx=(10, 0))

        operations = ttk.LabelFrame(info, text="Current Card Actions", padding=7)
        operations.grid(row=2, column=0, columnspan=10, sticky="ew", pady=(9, 0))
        ttk.Button(operations, text="Batch Import…", command=self._import_ps1_card_save).pack(side="left")
        ttk.Button(operations, text="Batch Export…", command=self._batch_export_ps1_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(operations, text="Clone Card…", command=self._clone_current_card).pack(side="left", padx=(14, 0))
        ttk.Button(operations, text="Restore Backup…", command=self._restore_last_backup).pack(side="left", padx=(6, 0))

        saves_box = ttk.LabelFrame(frame, text="Saves", padding=8)
        saves_box.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        saves_box.columnconfigure(0, weight=1)
        saves_box.rowconfigure(0, weight=1)
        self.ps1_card_tree = ttk.Treeview(saves_box, columns=("title", "blocks", "id"), show="headings", selectmode="extended")
        self.ps1_card_tree.heading("title", text="Game / Save")
        self.ps1_card_tree.heading("blocks", text="Blocks")
        self.ps1_card_tree.heading("id", text="Product / ID")
        self.ps1_card_tree.column("title", width=260, stretch=True)
        self.ps1_card_tree.column("blocks", width=70, anchor="e")
        self.ps1_card_tree.column("id", width=160, stretch=True)
        scroll = ttk.Scrollbar(saves_box, orient="vertical", command=self.ps1_card_tree.yview)
        self.ps1_card_tree.configure(yscrollcommand=scroll.set)
        self.ps1_card_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.ps1_card_tree.bind("<<TreeviewSelect>>", self._ps1_card_selection_changed)

        detail = ttk.Frame(frame)
        detail.grid(row=1, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(1, weight=1)
        selected = ttk.LabelFrame(detail, text="Selected Save", padding=10)
        selected.grid(row=0, column=0, sticky="ew")
        selected.columnconfigure(1, weight=1)
        self.ps1_card_name_var = tk.StringVar(value="—")
        self.ps1_card_title_var = tk.StringVar(value="—")
        self.ps1_card_product_var = tk.StringVar(value="—")
        ttk.Label(selected, text="Save name:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(selected, textvariable=self.ps1_card_name_var).grid(row=0, column=1, sticky="w")
        ttk.Label(selected, text="Title:").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Label(selected, textvariable=self.ps1_card_title_var, wraplength=500).grid(row=1, column=1, sticky="w")
        ttk.Label(selected, text="Product / ID:").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Label(selected, textvariable=self.ps1_card_product_var).grid(row=2, column=1, sticky="w")

        icon_box = ttk.LabelFrame(detail, text="PS1 Icon Preview", padding=10)
        icon_box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.ps1_card_preview_label = ttk.Label(icon_box, anchor="center")
        self.ps1_card_preview_label.pack(fill="both", expand=True)

        buttons = ttk.Frame(detail)
        buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Export PSV…", command=lambda: self._export_ps1_card_save("PSV")).pack(side="left")
        ttk.Button(buttons, text="Export MCS…", command=lambda: self._export_ps1_card_save("MCS")).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Export PSX…", command=lambda: self._export_ps1_card_save("PSX")).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Delete", command=self._delete_ps1_card_save).pack(side="right")

    # ---------- opening / detection ----------
    def _show_welcome(self) -> None:
        for child in self.welcome_frame.winfo_children():
            child.destroy()
        inner = ttk.Frame(self.welcome_frame, padding=40)
        inner.pack(expand=True)
        logo_path = _asset_path("app.png", "app.ico", "icon.png", "icon.ico")
        if logo_path is not None:
            try:
                logo = Image.open(logo_path).convert("RGBA")
                logo.thumbnail((112, 112), Image.Resampling.LANCZOS)
                self.welcome_logo_photo = ImageTk.PhotoImage(logo)
                ttk.Label(inner, image=self.welcome_logo_photo).pack(pady=(0, 12))
            except Exception:
                self.welcome_logo_photo = None
        ttk.Label(inner, text=APP_NAME, font=("Segoe UI", 20, "bold")).pack()
        ttk.Label(
            inner,
            text="Open a PS1/PS2 save or memory-card image.\nThe format is detected automatically.",
            justify="center",
        ).pack(pady=(12, 0))
        self.welcome_frame.tkraise()

    def _browse(self) -> None:
        filename = filedialog.askopenfilename(
            title="Open PlayStation save or memory card",
            initialdir=self.last_dir,
            filetypes=(
                ("Supported saves/cards", "*.psv *.psu *.ps2 *.mc2 *.mcd *.vm2 *.vm1 *.vmp *.mcr *.vmc *.gme *.vgs *.srm *.bin *.mcs *.psx *.ps1"),
                ("PS1 files", "*.psv *.vm1 *.vmp *.mcr *.vmc *.gme *.vgs *.srm *.mcd *.bin *.mcs *.psx *.ps1"),
                ("PS2 files", "*.psv *.psu *.ps2 *.mc2 *.mcd *.vm2 *.bin *.vmc"),
                ("All files", "*.*"),
            ),
        )
        if filename:
            self._open_path(Path(filename))

    def _open_from_entry(self) -> None:
        text = self.path_var.get().strip().strip('"')
        if text:
            self._open_path(Path(text))

    def _clear_current(self) -> None:
        self.current_archive = None
        self.current_card = None
        self.current_ps1_save = None
        self.current_ps1_card = None
        self.preview_kind = None
        self.preview_target = None
        self.ps2_icon_sys = None
        self.ps2_icon_model = None
        self.ps2_preview_cache.clear()
        self.ps1_icon_frames = []

    def _open_path(self, path: Path) -> None:
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            fmt = detect_path(path)
            self._clear_current()
            self.current_path = path
            self.current_format = fmt
            self.last_dir = path.parent
            self.path_var.set(str(path))

            if fmt == "PSU":
                self._show_ps2_archive(read_psu(path))
                self.detect_var.set("Detected: PSU — PlayStation 2 save archive")
            elif fmt == "PSV":
                self._show_ps2_archive(read_psv(path))
                self.detect_var.set("Detected: PSV — PlayStation 3 PS2 save")
            elif fmt == "MEMORY_CARD":
                self._show_ps2_memory_card(path)
                suffix = path.suffix.upper().lstrip(".") or "PS2"
                self.detect_var.set(f"Detected: {suffix} — PS2 memory card image")
            elif fmt in ("PS1_PSV", "PS1_MCS", "PS1_PSX", "PS1_RAW"):
                save = read_ps1_single(path)
                self._show_ps1_save(save)
                label = {"PS1_PSV": "PSV — PlayStation 3 PS1 save", "PS1_MCS": "MCS — PlayStation 1 single save", "PS1_PSX": "PSX — PlayStation 1 single save", "PS1_RAW": "RAW — PlayStation 1 single save"}[fmt]
                self.detect_var.set("Detected: " + label)
            elif fmt == "PS1_CARD":
                card = read_ps1_card(path)
                self._show_ps1_memory_card(card)
                self.detect_var.set(f"Detected: {card.container_format} — PS1 memory card image")
            else:
                raise UnknownFormatError(f"Unsupported detected format: {fmt}")
            self.status_var.set(f"Opened {path.name}")
        except (OSError, ValueError, RuntimeError, UnknownFormatError) as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            self.status_var.set("Open failed")

    # ---------- PS2 save ----------
    def _show_ps2_archive(self, archive: SaveArchive) -> None:
        self.current_archive = archive
        self.save_frame.tkraise()
        title = self._archive_title(archive)
        self.save_meta_vars["format"].set(archive.source_format or self.current_format or "—")
        self.save_meta_vars["dirname"].set(archive.dirname or "—")
        self.save_meta_vars["title"].set(title or "—")
        self.save_meta_vars["size"].set(human_size(archive.total_size))
        self.save_meta_vars["created"].set(_fmt_time(archive.created))
        self.save_meta_vars["modified"].set(_fmt_time(archive.modified))
        if archive.source_format == "PSV":
            sig = "Valid" if archive.signature_valid is True else "INVALID" if archive.signature_valid is False else "Not checked"
        else:
            sig = "Not applicable"
        self.save_meta_vars["signature"].set(sig)

        self.file_tree.delete(*self.file_tree.get_children())
        for entry in archive.entries:
            self.file_tree.insert("", "end", text=entry.name, values=(
                human_size(entry.size), f"0x{entry.mode:04X}", f"0x{entry.attr:08X}", _fmt_time(entry.created), _fmt_time(entry.modified)
            ))
        source = (archive.source_format or self.current_format or "").upper()
        values = [fmt for fmt in ("PSV", "PSU", "MAX") if fmt != source]
        self.convert_combo.configure(values=values)
        self.convert_var.set(values[0] if values else "PSU")
        self.warning_var.set("\n".join("• " + w for w in archive.warnings) if archive.warnings else "No structural warnings detected.")
        self._load_ps2_icon(archive, self.preview_label)

    def _archive_title(self, archive: SaveArchive) -> str:
        entry = archive.get("icon.sys")
        if not entry:
            return ""
        try:
            return parse_icon_sys(entry.data).title.replace("\n", " / ")
        except Exception:
            return ""

    def _load_ps2_icon(self, archive: SaveArchive, label: ttk.Label) -> None:
        self.ps2_icon_sys = None
        self.ps2_icon_model = None
        self.ps2_preview_cache.clear()
        self.ps1_icon_frames = []
        icon_entry = archive.get("icon.sys")
        icon_sys = None
        if icon_entry:
            try:
                icon_sys = parse_icon_sys(icon_entry.data)
            except Exception as exc:
                self.status_var.set(f"icon.sys warning: {exc}")

        candidates = []
        seen: set[str] = set()
        if icon_sys:
            for name in (icon_sys.icon_name, icon_sys.copy_icon_name, icon_sys.delete_icon_name):
                if name and name.casefold() not in seen:
                    entry = archive.get(name)
                    if entry:
                        candidates.append(entry)
                        seen.add(name.casefold())
        # Several real saves contain a usable ICN/ICO file but icon.sys points at a
        # missing copy/delete model or uses unusual case. Fall back to model probing.
        for entry in archive.entries:
            low = entry.name.casefold()
            if low in seen or low == "icon.sys":
                continue
            if low.endswith((".ico", ".icn")):
                candidates.append(entry)
                seen.add(low)
        for entry in archive.entries:
            low = entry.name.casefold()
            if low not in seen and low != "icon.sys":
                candidates.append(entry)
                seen.add(low)

        last_error: Exception | None = None
        for entry in candidates:
            try:
                model = parse_icon_model(entry.data)
                self.ps2_icon_sys = icon_sys
                self.ps2_icon_model = model
                self.preview_kind = "PS2"
                self.preview_target = label
                self.icon_started = time.monotonic()
                self._render_current_ps2_preview(seconds=0.0)
                return
            except Exception as exc:
                last_error = exc

        self.preview_kind = None
        self.preview_target = label
        if icon_sys and icon_sys.icon_name.startswith("_SCE"):
            self._set_preview(render_builtin_ps2_icon(icon_sys), label)
        elif icon_sys:
            self._set_preview(render_placeholder(icon_sys.title or "No icon model"), label)
        elif not icon_entry:
            self._set_preview(render_placeholder("No icon.sys"), label)
        else:
            self._set_preview(render_placeholder("Icon preview\nunavailable"), label)
        if last_error:
            self.status_var.set(f"Icon preview: {last_error}")

    def _convert_ps2_save(self) -> None:
        archive = self.current_archive
        if not archive or not self.current_path:
            return
        target = self.convert_var.get().upper()
        suffix = {"PSV": ".psv", "PSU": ".psu", "MAX": ".max"}.get(target)
        if suffix is None:
            messagebox.showerror(APP_NAME, f"Unsupported PS2 output format: {target}", parent=self)
            return
        output = filedialog.asksaveasfilename(
            title=f"Save as {target}", initialdir=self.last_dir,
            initialfile=self.current_path.with_suffix(suffix).name,
            defaultextension=suffix, filetypes=((f"{target} file", f"*{suffix}"), ("All files", "*.*")),
        )
        if not output:
            return
        try:
            if target == "PSV":
                write_psv(archive, output)
            elif target == "PSU":
                write_psu(archive, output)
            else:
                write_archive_as_max(archive, output)
            self.last_dir = Path(output).parent
            self.status_var.set(f"Created {Path(output).name}")
            messagebox.showinfo(APP_NAME, f"Created:\n{output}", parent=self)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Conversion failed:\n{exc}", parent=self)

    def _export_current_ps2_save_folder(self) -> None:
        archive = self.current_archive
        if archive is None:
            return
        directory = filedialog.askdirectory(title="Export PS2 save folder", initialdir=self.last_dir, parent=self)
        if not directory:
            return
        try:
            output = export_archive_folder(archive, directory)
            self.last_dir = Path(directory)
            self.status_var.set(f"Exported save folder: {output.name}")
            messagebox.showinfo(APP_NAME, f"Save contents exported to:\n{output}", parent=self)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Save-folder export failed:\n{exc}", parent=self)

    def _edit_current_ps2_metadata(self) -> None:
        archive = self.current_archive
        path = self.current_path
        if archive is None or path is None:
            return
        dialog = MetadataDialog(self, dirname=archive.dirname, title=self._archive_title(archive) or archive.dirname)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        dirname, title = dialog.result
        updated = copy.deepcopy(archive)
        try:
            updated.dirname = dirname
            _replace_archive_title(updated, title)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Metadata could not be updated:\n{exc}", parent=self)
            return
        if not messagebox.askyesno(
            APP_NAME,
            "Write the metadata changes back to the opened save now?\n\nA backup of the original file will be created first.",
            parent=self,
        ):
            self.current_archive = updated
            self._show_ps2_archive(updated)
            self.status_var.set("Metadata changed in memory; convert/export to save it")
            return
        backup = None
        try:
            backup = timestamped_backup(path)
            if (archive.source_format or self.current_format) == "PSV":
                write_psv(updated, path)
                verify = read_psv(path)
                if verify.signature_valid is not True:
                    raise RuntimeError("The rewritten PSV did not pass signature verification")
                updated = verify
            else:
                write_psu(updated, path)
                updated = read_psu(path)
            self.current_archive = updated
            self._show_ps2_archive(updated)
            self.status_var.set(f"Metadata saved • Backup: {backup.name}")
        except Exception as exc:
            if backup and backup.is_file():
                try:
                    restore_backup(backup, path)
                except Exception:
                    pass
            messagebox.showerror(APP_NAME, f"Could not save metadata:\n{exc}\n\nThe original file was restored from backup.", parent=self)

    def _inject_ps3_psv(self) -> None:
        target_name = filedialog.askopenfilename(
            title="Select the target PS3 PS2 PSV",
            initialdir=self.last_dir,
            filetypes=(("PS3 PS2 save", "*.psv"), ("All files", "*.*")),
            parent=self,
        )
        if not target_name:
            return
        target_path = Path(target_name)
        source_name = filedialog.askopenfilename(
            title="Select the modified compatible PS2 save",
            initialdir=target_path.parent,
            filetypes=(("PS2 saves", "*.psv *.psu *.max *.cbs *.xps *.sps"), ("All files", "*.*")),
            parent=self,
        )
        if not source_name:
            return
        source_path = Path(source_name)
        try:
            target = read_psv(target_path)
            if target.signature_valid is not True:
                raise ValueError("Target PSV is not valid: its PS3 PSV signature could not be verified.")
            source = load_external_ps2_save(source_path)
            if source.source_format == "PSV" and source.signature_valid is not True:
                raise ValueError("Source PSV is not valid: its signature could not be verified.")
            if not source.entries or not target.entries:
                raise ValueError("Target or source save contains no payload files.")

            target_id = _ps2_game_id(target.dirname)
            source_id = _ps2_game_id(source.dirname)
            if target_id != source_id:
                raise ValueError(
                    "The saves are not compatible.\n\n"
                    f"Target game / region: {target_id}\n"
                    f"Source game / region: {source_id}"
                )
            preserve_names = {"icon.sys"}
            target_icon = target.get("icon.sys")
            if target_icon:
                try:
                    sysinfo = parse_icon_sys(target_icon.data)
                    preserve_names.update(
                        name.casefold() for name in (sysinfo.icon_name, sysinfo.copy_icon_name, sysinfo.delete_icon_name) if name
                    )
                except Exception:
                    pass
            target_preserved = [copy.deepcopy(e) for e in target.entries if e.name.casefold() in preserve_names]
            source_payload = [copy.deepcopy(e) for e in source.entries if e.name.casefold() not in preserve_names]
            if not source_payload:
                raise ValueError("The source save contains no non-icon game data to inject.")

            injected = copy.deepcopy(target)
            injected.entries = target_preserved + source_payload
            injected.dirname = target.dirname
            injected.source_format = "PSV"
            injected.signature_valid = None
            injected.warnings = []

            backup = timestamped_backup(target_path)
            temp = target_path.with_name(target_path.name + ".inject.tmp")
            try:
                write_psv(injected, temp)
                verified = read_psv(temp)
                if verified.signature_valid is not True:
                    raise RuntimeError("Injected PSV failed signature verification")
                if verified.dirname.casefold() != target.dirname.casefold():
                    raise RuntimeError("Injected PSV changed the expected save directory")
                temp.replace(target_path)
            except Exception:
                try:
                    temp.unlink(missing_ok=True)
                except Exception:
                    pass
                restore_backup(backup, target_path)
                raise

            self.last_dir = target_path.parent
            self.status_var.set(f"Injected compatible save into {target_path.name}")
            messagebox.showinfo(
                APP_NAME,
                "Injection completed successfully.\n\n"
                f"Game / region: {target_id}\nTarget directory: {target.dirname}\nSource directory: {source.dirname}\n"
                f"Backup: {backup}",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"PS3 PSV injection blocked / failed:\n{exc}", parent=self)

    # ---------- PS2 cards ----------
    def _show_ps2_memory_card(self, path: Path) -> None:
        try:
            card = inspect_memory_card(path)
        except MemoryCardBackendUnavailable as exc:
            raise MemoryCardBackendUnavailable(
                f"{exc}\n\nPS1 cards and PSV/PSU conversion work locally, but PS2 .ps2/.mc2 card operations need mymc++."
            ) from exc
        self.current_card = card
        self.current_card_archive = None
        self.card_frame.tkraise()
        self.card_size_var.set(human_size(card.size_bytes))
        self.card_free_var.set(human_size(card.free_bytes))
        self.card_count_var.set(str(len(card.saves)))
        used = max(0, card.size_bytes - card.free_bytes)
        percent = (used / card.size_bytes * 100.0) if card.size_bytes else 0.0
        self.card_usage_var.set(max(0.0, min(100.0, percent)))
        self.card_usage_text_var.set(f"Used {human_size(used)} ({percent:.1f}%)")
        self._refresh_ps2_card_tree()
        self.card_files_tree.delete(*self.card_files_tree.get_children())
        self.card_sel_dir_var.set("—")
        self.card_sel_title_var.set("—")
        self.card_sel_info_var.set("—")
        self.preview_kind = None
        self._set_preview(render_placeholder("Select a save"), self.card_preview_label)

    def _refresh_ps2_card_tree(self) -> None:
        if not hasattr(self, "card_tree"):
            return
        current = set(self.card_tree.selection())
        self.card_tree.delete(*self.card_tree.get_children())
        card = self.current_card
        if card is None:
            return
        needle = self.ps2_filter_var.get().strip().casefold()
        for index, save in enumerate(card.saves):
            haystack = f"{save.title} {save.dirname}".casefold()
            if needle and needle not in haystack:
                continue
            iid = str(index)
            self.card_tree.insert("", "end", iid=iid, values=(save.title, human_size(save.size), save.dirname))
            if iid in current:
                self.card_tree.selection_add(iid)

    def _selected_ps2_card_saves(self) -> list:
        if self.current_card is None:
            return []
        saves = []
        for selected in self.card_tree.selection():
            try:
                saves.append(self.current_card.saves[int(selected)])
            except (ValueError, IndexError):
                pass
        return saves

    def _selected_ps2_card_save(self):
        saves = self._selected_ps2_card_saves()
        return saves[0] if saves else None

    def _ps2_card_selection_changed(self, _event=None) -> None:
        save = self._selected_ps2_card_save()
        if not save or not self.current_path:
            return
        self.card_sel_dir_var.set(save.dirname)
        self.card_sel_title_var.set(save.title)
        self.status_var.set(f"Reading {save.dirname}…")
        self.update_idletasks()
        try:
            archive = load_save_archive(self.current_path, save.dirname)
            self.current_card_archive = archive
            self.card_files_tree.delete(*self.card_files_tree.get_children())
            for index, entry in enumerate(archive.entries):
                self.card_files_tree.insert("", "end", iid=str(index), text=entry.name, values=(human_size(entry.size),))
            self.card_sel_info_var.set(f"{len(archive.entries)} files • {human_size(archive.total_size)}")
            self._load_ps2_icon(archive, self.card_preview_label)
            self.status_var.set(f"Loaded: {save.title}")
        except Exception as exc:
            self.current_card_archive = None
            self.card_files_tree.delete(*self.card_files_tree.get_children())
            self.card_sel_info_var.set("Could not inspect save")
            self._set_preview(render_placeholder("Preview failed"), self.card_preview_label)
            self.status_var.set(f"Could not read save: {exc}")

    def _check_card_writable(self) -> bool:
        if self.read_only_var.get():
            messagebox.showwarning(APP_NAME, "Read-only mode is enabled. Disable it before changing the memory card.", parent=self)
            return False
        return True

    def _backup_current_card(self) -> Path | None:
        if not self.current_path or not self.auto_backup_var.get():
            return None
        backup = timestamped_backup(self.current_path)
        self.last_card_backup = backup
        return backup

    def _import_ps2_card_save(self) -> None:
        if not self.current_path or not self._check_card_writable():
            return
        filenames = filedialog.askopenfilenames(
            title="Import save(s) into PS2 memory card", initialdir=self.last_dir,
            filetypes=(("PS2 save files", "*.psu *.psv *.max *.cbs *.xps *.sps"), ("All files", "*.*")),
        )
        if not filenames:
            return
        backup = None
        try:
            backup = self._backup_current_card()
            for filename in filenames:
                import_save(self.current_path, filename)
            self.last_dir = Path(filenames[0]).parent
            self._show_ps2_memory_card(self.current_path)
            self.status_var.set(f"Imported {len(filenames)} save(s)" + (f" • Backup: {backup.name}" if backup else ""))
        except Exception as exc:
            if backup and backup.is_file():
                try:
                    restore_backup(backup, self.current_path)
                    self._show_ps2_memory_card(self.current_path)
                except Exception:
                    pass
            messagebox.showerror(APP_NAME, f"Import failed:\n{exc}" + ("\n\nThe pre-change backup was restored." if backup else ""), parent=self)

    def _export_ps2_card_save(self, target: str) -> None:
        save = self._selected_ps2_card_save()
        if not save or not self.current_path:
            messagebox.showinfo(APP_NAME, "Select a save first.", parent=self)
            return
        suffix = {"PSV": ".psv", "PSU": ".psu", "MAX": ".max"}.get(target)
        if suffix is None:
            messagebox.showerror(APP_NAME, f"Unsupported export format: {target}", parent=self)
            return
        output = filedialog.asksaveasfilename(
            title=f"Export {target}", initialdir=self.last_dir, initialfile=save.dirname + suffix,
            defaultextension=suffix, filetypes=((f"{target} file", f"*{suffix}"), ("All files", "*.*")),
        )
        if not output:
            return
        try:
            if target == "PSU":
                export_save_as_psu(self.current_path, save.dirname, output)
            elif target == "MAX":
                export_save_as_max(self.current_path, save.dirname, output)
            else:
                write_psv(load_save_archive(self.current_path, save.dirname), output)
            self.last_dir = Path(output).parent
            self.status_var.set(f"Exported {Path(output).name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Export failed:\n{exc}", parent=self)

    def _batch_export_ps2_card(self, target: str) -> None:
        if not self.current_card or not self.current_path:
            return
        saves = self._selected_ps2_card_saves() or list(self.current_card.saves)
        if not saves:
            return
        directory = filedialog.askdirectory(title=f"Export {len(saves)} save(s) as {target}", initialdir=self.last_dir, parent=self)
        if not directory:
            return
        root = Path(directory)
        suffix = {"PSV": ".psv", "PSU": ".psu", "MAX": ".max"}[target]
        completed = 0
        try:
            for save in saves:
                output = root / f"{save.dirname}{suffix}"
                if target == "PSU":
                    export_save_as_psu(self.current_path, save.dirname, output)
                elif target == "MAX":
                    export_save_as_max(self.current_path, save.dirname, output)
                else:
                    write_psv(load_save_archive(self.current_path, save.dirname), output)
                completed += 1
            self.last_dir = root
            self.status_var.set(f"Batch export complete: {completed} {target} save(s)")
            messagebox.showinfo(APP_NAME, f"Exported {completed} save(s) to:\n{root}", parent=self)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Batch export stopped after {completed} save(s):\n{exc}", parent=self)

    def _batch_export_ps2_dialog(self) -> None:
        if self.current_card is None:
            messagebox.showinfo(APP_NAME, "Open a PS2 memory card first.", parent=self)
            return
        dialog = FormatChoiceDialog(self, "Batch Export PS2 Saves", ("PSV", "PSU", "MAX"))
        self.wait_window(dialog)
        if dialog.result:
            self._batch_export_ps2_card(dialog.result)

    def _edit_selected_ps2_card_metadata(self) -> None:
        pair = self._load_selected_ps2_archive()
        if pair is None or not self.current_path or not self._check_card_writable():
            return
        save, archive = pair
        dialog = MetadataDialog(self, dirname=archive.dirname, title=self._archive_title(archive) or save.title)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        dirname, title = dialog.result
        updated = copy.deepcopy(archive)
        backup = None
        try:
            updated.dirname = dirname
            _replace_archive_title(updated, title)
            backup = self._backup_current_card()
            replace_save_from_archive(self.current_path, save.dirname, updated)
            self._show_ps2_memory_card(self.current_path)
            self.status_var.set("Save metadata updated" + (f" • Backup: {backup.name}" if backup else ""))
        except Exception as exc:
            if backup and backup.is_file():
                try:
                    restore_backup(backup, self.current_path)
                    self._show_ps2_memory_card(self.current_path)
                except Exception:
                    pass
            messagebox.showerror(APP_NAME, f"Metadata update failed:\n{exc}" + ("\n\nThe pre-change backup was restored." if backup else ""), parent=self)

    def _delete_ps2_card_save(self) -> None:
        saves = self._selected_ps2_card_saves()
        if not saves or not self.current_path or not self._check_card_writable():
            return
        names = "\n".join(f"• {save.title} ({save.dirname})" for save in saves)
        if not messagebox.askyesno(APP_NAME, f"Delete {len(saves)} selected save(s) from this memory card?\n\n{names}\n\nThis changes the card image in place.", parent=self):
            return
        backup = None
        try:
            backup = self._backup_current_card()
            for save in saves:
                delete_save(self.current_path, save.dirname)
            self._show_ps2_memory_card(self.current_path)
            self.status_var.set(f"Deleted {len(saves)} save(s)" + (f" • Backup: {backup.name}" if backup else ""))
        except Exception as exc:
            if backup and backup.is_file():
                try:
                    restore_backup(backup, self.current_path)
                    self._show_ps2_memory_card(self.current_path)
                except Exception:
                    pass
            messagebox.showerror(APP_NAME, f"Delete failed:\n{exc}" + ("\n\nThe pre-change backup was restored." if backup else ""), parent=self)

    def _validate_ps2_card(self) -> None:
        if not self.current_path:
            return
        self.status_var.set("Validating PS2 memory card…")
        self.update_idletasks()
        try:
            backend = validate_card(self.current_path)
            card = inspect_memory_card(self.current_path)
            lines = ["Filesystem check:", backend or "No errors reported.", "", f"Card saves: {len(card.saves)}"]
            warnings = 0
            digest_map: dict[str, list[str]] = {}
            for save in card.saves:
                try:
                    archive = load_save_archive(self.current_path, save.dirname)
                    digest_map.setdefault(archive_digest(archive), []).append(save.dirname)
                    local = []
                    if not archive.entries:
                        local.append("contains no files")
                    icon_entry = archive.get("icon.sys")
                    if icon_entry:
                        try:
                            sysinfo = parse_icon_sys(icon_entry.data)
                            if sysinfo.icon_name and not sysinfo.icon_name.startswith("_SCE") and not archive.get(sysinfo.icon_name):
                                local.append(f"missing icon model {sysinfo.icon_name}")
                        except Exception as exc:
                            local.append(f"invalid icon.sys ({exc})")
                    if local:
                        warnings += len(local)
                        lines.append(f"• {save.dirname}: " + "; ".join(local))
                except Exception as exc:
                    warnings += 1
                    lines.append(f"• {save.dirname}: could not inspect ({exc})")
            duplicates = [names for names in digest_map.values() if len(names) > 1]
            if duplicates:
                lines.append("")
                lines.append("Possible duplicate saves (identical payload hash):")
                for names in duplicates:
                    lines.append("• " + " / ".join(names))
            if warnings == 0:
                lines.append("No save-structure warnings detected.")
            messagebox.showinfo(APP_NAME, "\n".join(lines), parent=self)
            self.status_var.set(f"Validation finished • {warnings} warning(s)")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Validation failed:\n{exc}", parent=self)

    def _experimental_repair_card(self) -> None:
        source = self.current_path if self.current_format == "MEMORY_CARD" else None
        if source is None:
            filename = filedialog.askopenfilename(
                title="Select PS2 memory card to rebuild",
                initialdir=self.last_dir,
                filetypes=(("PS2 memory cards", "*.ps2 *.mc2 *.mcd *.vm2 *.vmc *.bin"), ("All files", "*.*")),
                parent=self,
            )
            if not filename:
                return
            source = Path(filename)
        try:
            info = inspect_memory_card(source)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not inspect source card:\n{exc}", parent=self)
            return
        size_mb = min((8, 16, 32, 64), key=lambda n: abs(info.size_bytes - n * 1024 * 1024))
        output = filedialog.asksaveasfilename(
            title="Save rebuilt PS2 memory card",
            initialdir=source.parent,
            initialfile=f"{source.stem}_repaired{source.suffix or '.ps2'}",
            defaultextension=source.suffix or ".ps2",
            filetypes=(("PS2 memory card", "*.ps2 *.mc2"), ("All files", "*.*")),
            parent=self,
        )
        if not output:
            return
        destination = Path(output)
        if destination.resolve() == source.resolve():
            messagebox.showerror(APP_NAME, "Experimental repair must write to a NEW card image. The source is never modified in place.", parent=self)
            return
        if destination.exists() and not messagebox.askyesno(APP_NAME, f"Overwrite existing destination?\n{destination}", parent=self):
            return
        try:
            copied, errors = rebuild_card_to_new_image(
                source,
                destination,
                size_mb=size_mb,
                no_ecc=destination.suffix.lower() == ".mc2",
            )
            text = f"Rebuilt card created:\n{destination}\n\nCopied saves: {copied} / {len(info.saves)}"
            if errors:
                text += "\n\nSkipped / failed saves:\n" + "\n".join("• " + e for e in errors)
            else:
                text += "\n\nNo save-copy errors were reported."
            messagebox.showinfo(APP_NAME, text, parent=self)
            self.status_var.set(f"Experimental rebuild complete: {copied}/{len(info.saves)} saves")
            self.last_dir = destination.parent
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Experimental rebuild failed:\n{exc}", parent=self)

    def _open_dual_card_manager(self) -> None:
        initial = self.current_path if self.current_format == "MEMORY_CARD" else None
        DualPS2CardManager(self, initial_a=initial, auto_backup=self.auto_backup_var.get())

    def _clone_current_card(self) -> None:
        if not self.current_path or self.current_format not in ("MEMORY_CARD", "PS1_CARD"):
            messagebox.showinfo(APP_NAME, "Open a memory card first.", parent=self)
            return
        output = filedialog.asksaveasfilename(
            title="Clone memory card", initialdir=self.last_dir, initialfile=self.current_path.name,
            defaultextension=self.current_path.suffix, filetypes=(("Memory card", f"*{self.current_path.suffix}"), ("All files", "*.*")),
        )
        if not output:
            return
        try:
            clone_file(self.current_path, output)
            self.last_dir = Path(output).parent
            messagebox.showinfo(APP_NAME, f"Exact card clone created:\n{output}", parent=self)
            self.status_var.set(f"Cloned card to {Path(output).name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Clone failed:\n{exc}", parent=self)

    def _migrate_current_card(self) -> None:
        if not self.current_path or self.current_format != "MEMORY_CARD":
            messagebox.showinfo(APP_NAME, "Open a PS2 memory card first.", parent=self)
            return
        dialog = CreateCardDialog(self)
        dialog.system_var.set("PlayStation 2")
        dialog._update_controls()
        self.wait_window(dialog)
        if dialog.result is None:
            return
        system, size_mb, no_ecc = dialog.result
        if system != "PS2":
            return
        output = filedialog.asksaveasfilename(
            title="Create migration target", initialdir=self.last_dir, initialfile=f"{self.current_path.stem}_{size_mb}MB.ps2",
            defaultextension=".ps2", filetypes=(("PS2 memory card", "*.ps2 *.mc2"), ("All files", "*.*")),
        )
        if not output:
            return
        dest = Path(output)
        if dest.exists() and not messagebox.askyesno(APP_NAME, f"Overwrite existing file?\n{dest}", parent=self):
            return
        try:
            if dest.exists():
                dest.unlink()
            create_memory_card(dest, size_mb=size_mb, no_ecc=no_ecc, overwrite=True)
            copied = migrate_card_saves(self.current_path, dest)
            self.last_dir = dest.parent
            messagebox.showinfo(APP_NAME, f"Created {size_mb} MB card and migrated {copied} save(s):\n{dest}", parent=self)
            self.status_var.set(f"Migration complete: {copied} save(s)")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Migration failed:\n{exc}", parent=self)

    def _restore_last_backup(self) -> None:
        if not self.current_path or not self._check_card_writable():
            return
        backup = self.last_card_backup
        if backup is None or not backup.is_file():
            filename = filedialog.askopenfilename(
                title="Select memory-card backup", initialdir=self.current_path.parent / "_backups",
                filetypes=(("Memory card backups", "*.ps2 *.mc2 *.mcd *.vm2 *.vm1 *.mcr *.vmp *.gme *.vgs"), ("All files", "*.*")),
            )
            if not filename:
                return
            backup = Path(filename)
        if not messagebox.askyesno(APP_NAME, f"Restore this backup over the currently opened card?\n\n{backup}\n\nCurrent card: {self.current_path}", parent=self):
            return
        try:
            # Always preserve the current state before a restore, independent of Auto backup.
            safety = timestamped_backup(self.current_path)
            restore_backup(backup, self.current_path)
            self._open_path(self.current_path)
            self.last_card_backup = safety
            self.status_var.set(f"Backup restored • previous state saved as {safety.name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Restore failed:\n{exc}", parent=self)

    def _load_selected_ps2_archive(self) -> tuple[object, SaveArchive] | None:
        save = self._selected_ps2_card_save()
        if not save or not self.current_path:
            messagebox.showinfo(APP_NAME, "Select a save first.", parent=self)
            return None
        return save, load_save_archive(self.current_path, save.dirname)

    def _extract_ps2_save_folder(self) -> None:
        pair = self._load_selected_ps2_archive()
        if pair is None:
            return
        _save, archive = pair
        directory = filedialog.askdirectory(title="Extract save folder", initialdir=self.last_dir, parent=self)
        if not directory:
            return
        try:
            output = export_archive_folder(archive, directory)
            self.last_dir = Path(directory)
            self.status_var.set(f"Extracted save folder: {output.name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Extraction failed:\n{exc}", parent=self)

    def _compare_ps2_save(self) -> None:
        pair = self._load_selected_ps2_archive()
        if pair is None:
            return
        save, left = pair
        filename = filedialog.askopenfilename(
            title="Compare with PSV or PSU", initialdir=self.last_dir,
            filetypes=(("PS2 save files", "*.psu *.psv"), ("All files", "*.*")),
        )
        if not filename:
            return
        try:
            fmt = detect_path(filename)
            if fmt == "PSU":
                right = read_psu(filename)
            elif fmt == "PSV":
                right = read_psv(filename)
            else:
                raise ValueError("Choose a PS2 PSU or PS2 PSV save for comparison.")
            CompareWindow(self, save.dirname, left, Path(filename).name, right)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Compare failed:\n{exc}", parent=self)


    def _export_selected_ps2_card_format(self) -> None:
        target = self.card_export_var.get().strip().upper() if hasattr(self, "card_export_var") else "PSU"
        self._export_ps2_card_save(target)

    def _selected_card_file_indices(self) -> list[int]:
        if self.current_card_archive is None:
            return []
        indices: list[int] = []
        for item in self.card_files_tree.selection():
            try:
                idx = int(item)
            except ValueError:
                continue
            if 0 <= idx < len(self.current_card_archive.entries):
                indices.append(idx)
        return sorted(set(indices))

    def _commit_inline_card_archive(self, updated: SaveArchive, dirname: str) -> None:
        if not self.current_path or not self._check_card_writable():
            raise RuntimeError("Card is read-only or no longer open")
        backup = self._backup_current_card()
        try:
            replace_save_from_archive(self.current_path, dirname, updated)
        except Exception:
            if backup and backup.is_file():
                restore_backup(backup, self.current_path)
            raise

        self._show_ps2_memory_card(self.current_path)
        if self.current_card is not None:
            for index, save in enumerate(self.current_card.saves):
                if save.dirname.casefold() == updated.dirname.casefold():
                    iid = str(index)
                    if self.card_tree.exists(iid):
                        self.card_tree.selection_set(iid)
                        self.card_tree.focus(iid)
                        self.card_tree.see(iid)
                        self._ps2_card_selection_changed()
                    break
        self.status_var.set(
            f"Updated files in {updated.dirname}" + (f" • Backup: {backup.name}" if backup else "")
        )

    def _extract_selected_card_files(self) -> None:
        archive = self.current_card_archive
        indices = self._selected_card_file_indices()
        if archive is None or not indices:
            messagebox.showinfo(APP_NAME, "Select one or more files in the Files panel first.", parent=self)
            return
        directory = filedialog.askdirectory(title="Extract selected save files", initialdir=self.last_dir, parent=self)
        if not directory:
            return
        root = Path(directory)
        try:
            for idx in indices:
                entry = archive.entries[idx]
                (root / Path(entry.name).name).write_bytes(entry.data)
            self.last_dir = root
            self.status_var.set(f"Extracted {len(indices)} file(s)")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not extract file(s):\n{exc}", parent=self)

    def _replace_selected_card_file(self) -> None:
        archive = self.current_card_archive
        indices = self._selected_card_file_indices()
        save = self._selected_ps2_card_save()
        if archive is None or save is None or len(indices) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one file in the Files panel to replace.", parent=self)
            return
        if not self._check_card_writable():
            return
        idx = indices[0]
        entry = archive.entries[idx]
        source = filedialog.askopenfilename(title=f"Replace {entry.name}", initialdir=self.last_dir, parent=self)
        if not source:
            return
        try:
            updated = copy.deepcopy(archive)
            updated.entries[idx].data = Path(source).read_bytes()
            self.last_dir = Path(source).parent
            self._commit_inline_card_archive(updated, save.dirname)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not replace file:\n{exc}", parent=self)

    def _add_card_file(self) -> None:
        archive = self.current_card_archive
        save = self._selected_ps2_card_save()
        if archive is None or save is None:
            messagebox.showinfo(APP_NAME, "Select a save first.", parent=self)
            return
        if not self._check_card_writable():
            return
        source = filedialog.askopenfilename(title="Add file to selected save", initialdir=self.last_dir, parent=self)
        if not source:
            return
        src = Path(source)
        if any(entry.name.casefold() == src.name.casefold() for entry in archive.entries):
            messagebox.showerror(APP_NAME, f"A file named '{src.name}' already exists in this save.", parent=self)
            return
        try:
            updated = copy.deepcopy(archive)
            updated.entries.append(SaveEntry(name=src.name, data=src.read_bytes()))
            self.last_dir = src.parent
            self._commit_inline_card_archive(updated, save.dirname)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not add file:\n{exc}", parent=self)

    def _delete_selected_card_files(self) -> None:
        archive = self.current_card_archive
        save = self._selected_ps2_card_save()
        indices = self._selected_card_file_indices()
        if archive is None or save is None or not indices:
            messagebox.showinfo(APP_NAME, "Select one or more files in the Files panel first.", parent=self)
            return
        if not self._check_card_writable():
            return
        names = [archive.entries[idx].name for idx in indices]
        if not messagebox.askyesno(
            APP_NAME,
            "Delete the selected file(s) from this save?\n\n" + "\n".join(names),
            parent=self,
        ):
            return
        try:
            updated = copy.deepcopy(archive)
            for idx in reversed(indices):
                del updated.entries[idx]
            self._commit_inline_card_archive(updated, save.dirname)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not delete file(s):\n{exc}", parent=self)

    # ---------- PS1 save ----------
    def _show_ps1_save(self, save: PS1Save) -> None:
        self.current_ps1_save = save
        self.ps1_save_frame.tkraise()
        self.ps1_meta_vars["format"].set(save.source_format)
        self.ps1_meta_vars["filename"].set(save.filename or "—")
        self.ps1_meta_vars["title"].set(save.title or "—")
        self.ps1_meta_vars["product"].set((save.region + save.product_code) or "—")
        self.ps1_meta_vars["identifier"].set(save.identifier or "—")
        self.ps1_meta_vars["blocks"].set(str(save.blocks))
        self.ps1_meta_vars["size"].set(human_size(save.payload_size))
        if save.source_format == "PSV":
            sig = "Valid" if save.signature_valid is True else "INVALID" if save.signature_valid is False else "Not checked"
        else:
            sig = "Not applicable"
        self.ps1_meta_vars["signature"].set(sig)
        self.ps1_detail_var.set(
            "PS1 saves use 8 KB card blocks. Their Browser icon is 16×16 pixels with up to three animation frames."
        )
        self.ps1_warning_var.set("\n".join("• " + w for w in save.warnings) if save.warnings else "No structural warnings detected.")
        values = [fmt for fmt in ("PSV", "MCS", "PSX", "RAW") if fmt != save.source_format]
        self.ps1_convert_combo.configure(values=values)
        self.ps1_convert_var.set(values[0] if values else "RAW")
        self._load_ps1_icon(save, self.ps1_preview_label)

    def _load_ps1_icon(self, save: PS1Save, label: ttk.Label) -> None:
        self.ps2_icon_sys = None
        self.ps2_icon_model = None
        self.ps1_icon_frames = extract_icon_frames_rgba(save)
        self.preview_kind = "PS1"
        self.preview_target = label
        self.icon_started = time.monotonic()
        frame = self.ps1_icon_frames[0] if self.ps1_icon_frames else None
        self._set_preview(render_ps1_icon(frame), label)

    def _convert_ps1_save(self) -> None:
        save = self.current_ps1_save
        if not save or not self.current_path:
            return
        target = self.ps1_convert_var.get().upper()
        suffix = {"PSV": ".psv", "MCS": ".mcs", "PSX": ".psx", "RAW": ".ps1"}[target]
        output = filedialog.asksaveasfilename(
            title=f"Save as {target}", initialdir=self.last_dir,
            initialfile=self.current_path.with_suffix(suffix).name, defaultextension=suffix,
            filetypes=((f"{target} file", f"*{suffix}"), ("All files", "*.*")),
        )
        if not output:
            return
        try:
            write_ps1_single(save, output, target)
            self.last_dir = Path(output).parent
            self.status_var.set(f"Created {Path(output).name}")
            messagebox.showinfo(APP_NAME, f"Created:\n{output}", parent=self)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Conversion failed:\n{exc}", parent=self)

    # ---------- PS1 cards ----------
    def _show_ps1_memory_card(self, card: PS1MemoryCard) -> None:
        self.current_ps1_card = card
        self.ps1_card_frame.tkraise()
        self.ps1_card_format_var.set(card.container_format)
        self.ps1_card_size_var.set("128.0 KB")
        self.ps1_card_free_var.set(f"{card.free_blocks} / 15")
        self.ps1_card_count_var.set(str(len(card.saves)))
        self._refresh_ps1_card_tree()
        self.ps1_card_name_var.set("—")
        self.ps1_card_title_var.set("—")
        self.ps1_card_product_var.set("—")
        self.preview_kind = None
        self._set_preview(render_ps1_icon(None), self.ps1_card_preview_label)

    def _refresh_ps1_card_tree(self) -> None:
        if not hasattr(self, "ps1_card_tree"):
            return
        self.ps1_card_tree.delete(*self.ps1_card_tree.get_children())
        card = self.current_ps1_card
        if card is None:
            return
        needle = self.ps1_filter_var.get().strip().casefold()
        for idx, save in enumerate(card.saves):
            title = save.title or save.filename or f"Save {idx + 1}"
            product = ((save.region + save.product_code) + (" / " + save.identifier if save.identifier else "")).strip(" /")
            haystack = f"{title} {save.filename} {product}".casefold()
            if needle and needle not in haystack:
                continue
            self.ps1_card_tree.insert("", "end", iid=str(idx), values=(title, save.blocks, product or "—"))

    def _selected_ps1_card_saves(self) -> list[PS1Save]:
        card = self.current_ps1_card
        if not card:
            return []
        out: list[PS1Save] = []
        for selected in self.ps1_card_tree.selection():
            try:
                out.append(card.saves[int(selected)])
            except (ValueError, IndexError):
                pass
        return out

    def _selected_ps1_card_save(self) -> PS1Save | None:
        saves = self._selected_ps1_card_saves()
        return saves[0] if saves else None

    def _ps1_card_selection_changed(self, _event=None) -> None:
        save = self._selected_ps1_card_save()
        if not save:
            return
        self.ps1_card_name_var.set(save.filename or "—")
        self.ps1_card_title_var.set(save.title or "—")
        product = (save.region + save.product_code).strip()
        if save.identifier:
            product += (" / " if product else "") + save.identifier
        self.ps1_card_product_var.set(product or "—")
        self._load_ps1_icon(save, self.ps1_card_preview_label)
        blocks = ", ".join(str(slot + 1) for slot in save.slots)
        self.status_var.set(f"Selected PS1 save — card block(s): {blocks}")

    def _import_ps1_card_save(self) -> None:
        card = self.current_ps1_card
        if card is None or self.current_path is None or not self._check_card_writable():
            return
        filenames = filedialog.askopenfilenames(
            title="Import save(s) into PS1 memory card", initialdir=self.last_dir,
            filetypes=(("PS1 save files", "*.mcs *.psv *.psx *.ps1"), ("All files", "*.*")),
        )
        if not filenames:
            return
        backup = None
        try:
            backup = self._backup_current_card()
            for filename in filenames:
                save = read_ps1_single(filename)
                import_save_into_card(card, save)
            save_ps1_card(card, self.current_path)
            self.last_dir = Path(filenames[0]).parent
            refreshed = read_ps1_card(self.current_path)
            self.current_ps1_card = refreshed
            self._show_ps1_memory_card(refreshed)
            self.status_var.set(f"Imported {len(filenames)} save(s)" + (f" • Backup: {backup.name}" if backup else ""))
        except Exception as exc:
            if backup and backup.is_file():
                try:
                    restore_backup(backup, self.current_path)
                    self.current_ps1_card = read_ps1_card(self.current_path)
                    self._show_ps1_memory_card(self.current_ps1_card)
                except Exception:
                    pass
            messagebox.showerror(APP_NAME, f"Import failed:\n{exc}" + ("\n\nThe pre-change backup was restored." if backup else ""), parent=self)

    def _export_ps1_card_save(self, target: str) -> None:
        save = self._selected_ps1_card_save()
        if not save:
            messagebox.showinfo(APP_NAME, "Select a save first.", parent=self)
            return
        suffix = {"PSV": ".psv", "MCS": ".mcs", "PSX": ".psx"}[target]
        initial = (save.filename or "PS1_SAVE") + suffix
        output = filedialog.asksaveasfilename(
            title=f"Export {target}", initialdir=self.last_dir, initialfile=initial,
            defaultextension=suffix, filetypes=((f"{target} file", f"*{suffix}"), ("All files", "*.*")),
        )
        if not output:
            return
        try:
            write_ps1_single(save, output, target)
            self.last_dir = Path(output).parent
            self.status_var.set(f"Exported {Path(output).name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Export failed:\n{exc}", parent=self)

    def _batch_export_ps1_dialog(self) -> None:
        if self.current_ps1_card is None:
            messagebox.showinfo(APP_NAME, "Open a PS1 memory card first.", parent=self)
            return
        dialog = FormatChoiceDialog(self, "Batch Export PS1 Saves", ("PSV", "MCS", "PSX"))
        self.wait_window(dialog)
        if dialog.result:
            self._batch_export_ps1_card(dialog.result)

    def _batch_export_ps1_card(self, target: str) -> None:
        card = self.current_ps1_card
        if card is None:
            return
        saves = self._selected_ps1_card_saves() or list(card.saves)
        if not saves:
            return
        directory = filedialog.askdirectory(title=f"Export {len(saves)} PS1 save(s) as {target}", initialdir=self.last_dir, parent=self)
        if not directory:
            return
        root = Path(directory)
        suffix = {"PSV": ".psv", "MCS": ".mcs", "PSX": ".psx"}[target]
        completed = 0
        try:
            for idx, save in enumerate(saves, 1):
                base = save.filename or save.identifier or f"PS1_SAVE_{idx:02d}"
                safe = "".join(c if c not in '<>:"/\\|?*' else "_" for c in base)
                write_ps1_single(save, root / f"{safe}{suffix}", target)
                completed += 1
            self.last_dir = root
            messagebox.showinfo(APP_NAME, f"Exported {completed} save(s) to:\n{root}", parent=self)
            self.status_var.set(f"PS1 batch export complete: {completed} save(s)")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Batch export stopped after {completed} save(s):\n{exc}", parent=self)

    def _delete_ps1_card_save(self) -> None:
        card = self.current_ps1_card
        saves = self._selected_ps1_card_saves()
        if card is None or not saves or self.current_path is None or not self._check_card_writable():
            return
        names = "\n".join("• " + (save.title or save.filename or "PS1 save") for save in saves)
        if not messagebox.askyesno(APP_NAME, f"Delete {len(saves)} selected PS1 save(s)?\n\n{names}\n\nThis changes the card image in place.", parent=self):
            return
        backup = None
        try:
            backup = self._backup_current_card()
            # Delete highest start slots first so earlier slot references remain stable.
            for save in sorted(saves, key=lambda s: s.start_slot if s.start_slot is not None else -1, reverse=True):
                if save.start_slot is not None:
                    delete_save_from_card(card, save.start_slot)
            save_ps1_card(card, self.current_path)
            refreshed = read_ps1_card(self.current_path)
            self.current_ps1_card = refreshed
            self._show_ps1_memory_card(refreshed)
            self.status_var.set(f"Deleted {len(saves)} PS1 save(s)" + (f" • Backup: {backup.name}" if backup else ""))
        except Exception as exc:
            if backup and backup.is_file():
                try:
                    restore_backup(backup, self.current_path)
                    self.current_ps1_card = read_ps1_card(self.current_path)
                    self._show_ps1_memory_card(self.current_ps1_card)
                except Exception:
                    pass
            messagebox.showerror(APP_NAME, f"Delete failed:\n{exc}" + ("\n\nThe pre-change backup was restored." if backup else ""), parent=self)

    def _validate_ps1_card(self) -> None:
        card = self.current_ps1_card
        if card is None:
            return
        issues = validate_ps1_card(card)
        details = [f"Container: {card.container_format}", f"Saves: {len(card.saves)}", f"Free blocks: {card.free_blocks} / 15", ""]
        details += ["No structural warnings detected."] if not issues else ["Warnings:"] + ["• " + issue for issue in issues]
        messagebox.showinfo(APP_NAME, "\n".join(details), parent=self)
        self.status_var.set("PS1 memory card validation finished")

    # ---------- preview / create ----------
    def _render_current_ps2_preview(self, *, seconds: float = 0.0) -> None:
        target = self.preview_target
        model = self.ps2_icon_model
        if target is None or model is None:
            return
        mode = self.ps2_preview_mode.get()
        cached = self.ps2_preview_cache.get(mode)
        if cached is None:
            if mode == "Texture":
                cached = render_texture(model)
            else:
                cached = render_icon(self.ps2_icon_sys, model, 0.0)
            self.ps2_preview_cache[mode] = cached
        self._set_preview(cached, target)

    def _ps2_preview_mode_changed(self) -> None:
        if self.preview_kind != "PS2" or self.ps2_icon_model is None:
            return
        self.icon_started = time.monotonic()
        self._render_current_ps2_preview(seconds=0.0)
        if self.ps2_preview_mode.get() == "Texture":
            if self.ps2_icon_model.texture_rgba:
                self.status_var.set("PS2 icon texture — raw 128×128 texture atlas")
            else:
                self.status_var.set("PS2 icon has no embedded texture")

    def _set_preview(self, image, label: ttk.Label) -> None:
        photo = ImageTk.PhotoImage(image)
        label.configure(image=photo)
        label.image = photo
        self.preview_photo = photo

    def _animate_icon(self) -> None:
        # Animation is intentionally disabled. Static previews keep the GUI responsive
        # even for complex PS2 models and match the tool's management-first workflow.
        return

    def _create_card(self) -> None:
        dialog = CreateCardDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        system, size_mb, no_ecc = dialog.result

        if system == "PS1":
            filename = filedialog.asksaveasfilename(
                title="Create PS1 memory card", initialdir=self.last_dir, initialfile="MemoryCard1.vm1",
                defaultextension=".vm1",
                filetypes=(("PS3 PS1 virtual card", "*.vm1"), ("Raw PS1 card", "*.mcr *.mcd *.bin *.vmc *.srm"), ("VMP card", "*.vmp"), ("All files", "*.*")),
            )
        else:
            filename = filedialog.asksaveasfilename(
                title="Create PS2 memory card", initialdir=self.last_dir, initialfile=f"Mcd001_{size_mb}MB.ps2",
                defaultextension=".ps2",
                filetypes=(("PCSX2 memory card", "*.ps2"), ("MemCard PRO2", "*.mc2"), ("All files", "*.*")),
            )
        if not filename:
            return
        path = Path(filename)
        overwrite = path.exists()
        if overwrite and not messagebox.askyesno(APP_NAME, f"Overwrite {path.name}?", parent=self):
            return
        try:
            if system == "PS1":
                create_ps1_memory_card(path, overwrite=overwrite)
            else:
                if path.suffix.lower() == ".mc2":
                    no_ecc = True
                create_memory_card(path, size_mb=size_mb, no_ecc=no_ecc, overwrite=overwrite)
            self.last_dir = path.parent
            self._open_path(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not create memory card:\n{exc}", parent=self)


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PS2SaveTool")
        except Exception:
            pass
    app = PS2SaveTool()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        app.after(100, lambda: app._open_path(path))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
