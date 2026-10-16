from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from card_tools import archive_hashes, compare_archives, restore_backup, timestamped_backup
from formats.common import SaveArchive, SaveEntry, human_size
from formats.memorycard import (
    copy_save_between_cards,
    delete_save,
    inspect_memory_card,
)


class RawFileManager(tk.Toplevel):
    """Small file-level editor for one extracted PS2 save.

    Changes live in memory until Save Changes is clicked. The caller supplies the
    commit callback, which can back up and replace the save on the memory card.
    """

    def __init__(self, parent, archive: SaveArchive, *, commit_callback=None, read_only: bool = False):
        super().__init__(parent)
        self.title(f"Save Files - {archive.dirname}")
        self.geometry("760x500")
        self.minsize(620, 400)
        self.transient(parent)
        self.archive = copy.deepcopy(archive)
        self.commit_callback = commit_callback
        self.read_only = read_only

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        ttk.Label(
            root,
            text=(f"Directory: {self.archive.dirname}" + ("   [READ ONLY]" if read_only else "")),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.tree = ttk.Treeview(root, columns=("size", "hash"), show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="File")
        self.tree.heading("size", text="Size")
        self.tree.heading("hash", text="SHA-256")
        self.tree.column("#0", width=260, stretch=True)
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("hash", width=250, stretch=True)
        scroll = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")

        buttons = ttk.Frame(root)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Extract Selected...", command=self._extract_selected).pack(side="left")
        ttk.Button(buttons, text="Extract All...", command=self._extract_all).pack(side="left", padx=(6, 0))
        self.replace_btn = ttk.Button(buttons, text="Replace File...", command=self._replace)
        self.replace_btn.pack(side="left", padx=(18, 0))
        self.add_btn = ttk.Button(buttons, text="Add File...", command=self._add)
        self.add_btn.pack(side="left", padx=(6, 0))
        self.delete_btn = ttk.Button(buttons, text="Delete File", command=self._delete)
        self.delete_btn.pack(side="left", padx=(6, 0))
        self.save_btn = ttk.Button(buttons, text="Save Changes", command=self._save)
        self.save_btn.pack(side="right")

        if read_only or commit_callback is None:
            for widget in (self.replace_btn, self.add_btn, self.delete_btn, self.save_btn):
                widget.configure(state="disabled")

        self._refresh()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        hashes = archive_hashes(self.archive)
        for idx, entry in enumerate(self.archive.entries):
            self.tree.insert("", "end", iid=str(idx), text=entry.name, values=(human_size(entry.size), hashes[entry.name][:20] + "..."))

    def _selected_indices(self) -> list[int]:
        out: list[int] = []
        for item in self.tree.selection():
            try:
                out.append(int(item))
            except ValueError:
                pass
        return sorted(set(out))

    def _extract_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            messagebox.showinfo("Save Files", "Select one or more files first.", parent=self)
            return
        directory = filedialog.askdirectory(title="Extract selected files", parent=self)
        if not directory:
            return
        root = Path(directory)
        for idx in indices:
            entry = self.archive.entries[idx]
            (root / Path(entry.name).name).write_bytes(entry.data)

    def _extract_all(self) -> None:
        directory = filedialog.askdirectory(title="Extract all save files", parent=self)
        if not directory:
            return
        root = Path(directory) / self.archive.dirname
        root.mkdir(parents=True, exist_ok=True)
        for entry in self.archive.entries:
            (root / Path(entry.name).name).write_bytes(entry.data)

    def _replace(self) -> None:
        indices = self._selected_indices()
        if len(indices) != 1:
            messagebox.showinfo("Save Files", "Select exactly one file to replace.", parent=self)
            return
        idx = indices[0]
        source = filedialog.askopenfilename(title=f"Replace {self.archive.entries[idx].name}", parent=self)
        if not source:
            return
        self.archive.entries[idx].data = Path(source).read_bytes()
        self._refresh()
        self.tree.selection_set(str(idx))

    def _add(self) -> None:
        source = filedialog.askopenfilename(title="Add file to save", parent=self)
        if not source:
            return
        name = Path(source).name
        if any(entry.name.casefold() == name.casefold() for entry in self.archive.entries):
            messagebox.showerror("Save Files", f"A file named '{name}' already exists.", parent=self)
            return
        self.archive.entries.append(SaveEntry(name=name, data=Path(source).read_bytes()))
        self._refresh()

    def _delete(self) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        names = [self.archive.entries[i].name for i in indices]
        if not messagebox.askyesno("Save Files", "Delete these file(s) from the save?\n\n" + "\n".join(names), parent=self):
            return
        for idx in reversed(indices):
            del self.archive.entries[idx]
        self._refresh()

    def _save(self) -> None:
        if self.commit_callback is None:
            return
        try:
            self.commit_callback(self.archive)
        except Exception as exc:
            messagebox.showerror("Save Files", f"Could not save changes:\n{exc}", parent=self)
            return
        messagebox.showinfo("Save Files", "Save files were updated successfully.", parent=self)
        self.destroy()


class CompareWindow(tk.Toplevel):
    def __init__(self, parent, left_name: str, left: SaveArchive, right_name: str, right: SaveArchive):
        super().__init__(parent)
        self.title("Compare Saves")
        self.geometry("900x540")
        self.minsize(720, 420)
        self.transient(parent)

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        ttk.Label(root, text=f"Left: {left_name}\nRight: {right_name}", justify="left").grid(row=0, column=0, sticky="w", pady=(0, 10))
        tree = ttk.Treeview(root, columns=("status", "left", "right"), show="tree headings")
        tree.heading("#0", text="File")
        tree.heading("status", text="Status")
        tree.heading("left", text="Left size")
        tree.heading("right", text="Right size")
        tree.column("#0", width=360, stretch=True)
        tree.column("status", width=130)
        tree.column("left", width=110, anchor="e")
        tree.column("right", width=110, anchor="e")
        scroll = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")

        diffs = compare_archives(left, right)
        for diff in diffs:
            tree.insert("", "end", text=diff.name, values=(
                diff.status,
                human_size(diff.left_size) if diff.left_size is not None else "—",
                human_size(diff.right_size) if diff.right_size is not None else "—",
            ))

        changed = sum(1 for d in diffs if d.status != "Identical")
        ttk.Label(root, text=f"{len(diffs)} file(s), {changed} difference(s)").grid(row=2, column=0, sticky="w", pady=(8, 0))


class DualPS2CardManager(tk.Toplevel):
    """Two-card copy view for PS2 memory cards."""

    def __init__(self, parent, *, initial_a: str | Path | None = None, auto_backup: bool = True):
        super().__init__(parent)
        self.title("Merge PS2 Memory Cards")
        self.geometry("1080x650")
        self.minsize(850, 500)
        self.transient(parent)
        self.auto_backup = tk.BooleanVar(value=auto_backup)
        self.path_vars = [tk.StringVar(), tk.StringVar()]
        self.filter_vars = [tk.StringVar(), tk.StringVar()]
        self.read_only_vars = [tk.BooleanVar(value=False), tk.BooleanVar(value=False)]
        self.cards = [None, None]
        self.trees: list[ttk.Treeview] = []
        self.info_vars = [tk.StringVar(value="No card loaded"), tk.StringVar(value="No card loaded")]

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(2, weight=1)
        root.rowconfigure(0, weight=1)

        for side in (0, 1):
            pane = self._build_side(root, side)
            pane.grid(row=0, column=side * 2, sticky="nsew")

        middle = ttk.Frame(root, padding=(10, 120))
        middle.grid(row=0, column=1, sticky="ns")
        ttk.Button(middle, text="Copy  →", command=lambda: self._copy(0, 1)).pack(fill="x", pady=5)
        ttk.Button(middle, text="←  Copy", command=lambda: self._copy(1, 0)).pack(fill="x", pady=5)
        ttk.Checkbutton(middle, text="Backup target", variable=self.auto_backup).pack(pady=(20, 0))

        if initial_a:
            self.path_vars[0].set(str(initial_a))
            self.after_idle(lambda: self._load(0))

    def _build_side(self, parent, side: int) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text=f"Card {'A' if side == 0 else 'B'}", padding=10)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(4, weight=1)
        top = ttk.Frame(box)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Entry(top, textvariable=self.path_vars[side]).grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text="Browse...", command=lambda s=side: self._browse(s)).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(box, textvariable=self.info_vars[side]).grid(row=1, column=0, sticky="w", pady=(7, 7))
        filter_frame = ttk.Frame(box)
        filter_frame.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        filter_frame.columnconfigure(1, weight=1)
        ttk.Label(filter_frame, text="Filter:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        entry = ttk.Entry(filter_frame, textvariable=self.filter_vars[side])
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<KeyRelease>", lambda _e, s=side: self._refresh_tree(s))
        ttk.Checkbutton(filter_frame, text="Read only", variable=self.read_only_vars[side]).grid(row=0, column=2, padx=(8, 0))

        tree = ttk.Treeview(box, columns=("title", "size"), show="headings", selectmode="extended")
        tree.heading("title", text="Game / Save")
        tree.heading("size", text="Size")
        tree.column("title", width=310, stretch=True)
        tree.column("size", width=80, anchor="e")
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=4, column=0, sticky="nsew")
        scroll.grid(row=4, column=1, sticky="ns")
        self.trees.append(tree)
        return box

    def _browse(self, side: int) -> None:
        path = filedialog.askopenfilename(
            title=f"Open PS2 memory card {'A' if side == 0 else 'B'}",
            filetypes=(("PS2 memory cards", "*.ps2 *.mc2 *.mcd *.vm2"), ("All files", "*.*")),
            parent=self,
        )
        if path:
            self.path_vars[side].set(path)
            self._load(side)

    def _load(self, side: int) -> None:
        path = Path(self.path_vars[side].get())
        try:
            card = inspect_memory_card(path)
        except Exception as exc:
            messagebox.showerror("Merge Cards", f"Could not open card:\n{exc}", parent=self)
            return
        self.cards[side] = card
        self.info_vars[side].set(f"{human_size(card.size_bytes)}  •  Free {human_size(card.free_bytes)}  •  {len(card.saves)} saves")
        self._refresh_tree(side)

    def _refresh_tree(self, side: int) -> None:
        tree = self.trees[side]
        tree.delete(*tree.get_children())
        card = self.cards[side]
        if card is None:
            return
        needle = self.filter_vars[side].get().strip().casefold()
        for idx, save in enumerate(card.saves):
            haystack = f"{save.title} {save.dirname}".casefold()
            if needle and needle not in haystack:
                continue
            tree.insert("", "end", iid=str(idx), values=(save.title, human_size(save.size)))

    def _copy(self, source: int, target: int) -> None:
        source_card = self.cards[source]
        target_card = self.cards[target]
        if source_card is None or target_card is None:
            messagebox.showinfo("Merge Cards", "Load both memory cards first.", parent=self)
            return
        if self.read_only_vars[target].get():
            messagebox.showwarning("Merge Cards", "The target card is read-only.", parent=self)
            return
        selected = self.trees[source].selection()
        if not selected:
            messagebox.showinfo("Merge Cards", "Select one or more saves to copy.", parent=self)
            return
        saves = []
        for item in selected:
            try:
                saves.append(source_card.saves[int(item)])
            except (ValueError, IndexError):
                pass
        if not saves:
            return

        target_path = Path(self.path_vars[target].get())
        source_path = Path(self.path_vars[source].get())
        try:
            if source_path.resolve() == target_path.resolve():
                messagebox.showwarning("Merge Cards", "Source and target must be different memory-card files.", parent=self)
                return
        except OSError:
            pass
        target_names = {s.dirname.casefold() for s in target_card.saves}
        duplicates = [s for s in saves if s.dirname.casefold() in target_names]
        replace_duplicates = False
        if duplicates:
            replace_duplicates = messagebox.askyesno(
                "Merge Cards",
                "The target card already contains one or more selected save directories.\n\nReplace duplicates?",
                parent=self,
            )
            if not replace_duplicates:
                saves = [s for s in saves if s.dirname.casefold() not in target_names]
                if not saves:
                    return

        backup = None
        try:
            if self.auto_backup.get():
                backup = timestamped_backup(target_path)
            for save in saves:
                if replace_duplicates and save.dirname.casefold() in target_names:
                    delete_save(target_path, save.dirname)
                copy_save_between_cards(source_path, save.dirname, target_path)
            self._load(target)
            text = f"Copied {len(saves)} save(s)."
            if backup:
                text += f"\n\nBackup: {backup}"
            messagebox.showinfo("Merge Cards", text, parent=self)
        except Exception as exc:
            restored = False
            if backup and backup.is_file():
                try:
                    restore_backup(backup, target_path)
                    restored = True
                except Exception:
                    pass
            messagebox.showerror(
                "Merge Cards",
                f"Copy failed:\n{exc}" + ("\n\nThe destination backup was restored." if restored else (f"\n\nA backup was created at:\n{backup}" if backup else "")),
                parent=self,
            )
            self._load(target)
