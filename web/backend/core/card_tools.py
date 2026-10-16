from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import shutil
from typing import Iterable

from formats.common import SaveArchive


@dataclass(slots=True)
class ArchiveDifference:
    name: str
    status: str
    left_size: int | None
    right_size: int | None
    left_sha256: str | None
    right_sha256: str | None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_hashes(archive: SaveArchive) -> dict[str, str]:
    return {entry.name: sha256_bytes(entry.data) for entry in archive.entries}


def archive_digest(archive: SaveArchive) -> str:
    """Stable digest for a save's directory name, metadata-light file list and payloads."""
    h = hashlib.sha256()
    h.update(archive.dirname.encode("utf-8", errors="replace"))
    h.update(b"\0")
    for entry in sorted(archive.entries, key=lambda item: item.name.casefold()):
        h.update(entry.name.encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update(len(entry.data).to_bytes(8, "little"))
        h.update(entry.data)
    return h.hexdigest()


def compare_archives(left: SaveArchive, right: SaveArchive) -> list[ArchiveDifference]:
    left_map = {entry.name.casefold(): entry for entry in left.entries}
    right_map = {entry.name.casefold(): entry for entry in right.entries}
    names = sorted(set(left_map) | set(right_map))
    differences: list[ArchiveDifference] = []
    for key in names:
        a = left_map.get(key)
        b = right_map.get(key)
        display_name = a.name if a else b.name  # type: ignore[union-attr]
        if a is None:
            differences.append(
                ArchiveDifference(display_name, "Only in right", None, b.size, None, sha256_bytes(b.data))  # type: ignore[union-attr]
            )
            continue
        if b is None:
            differences.append(
                ArchiveDifference(display_name, "Only in left", a.size, None, sha256_bytes(a.data), None)
            )
            continue
        ah = sha256_bytes(a.data)
        bh = sha256_bytes(b.data)
        status = "Identical" if ah == bh else "Changed"
        differences.append(ArchiveDifference(display_name, status, a.size, b.size, ah, bh))
    return differences


def export_archive_folder(archive: SaveArchive, directory: str | Path) -> Path:
    root = Path(directory) / archive.dirname
    root.mkdir(parents=True, exist_ok=True)
    for entry in archive.entries:
        safe_name = Path(entry.name).name
        if not safe_name or safe_name in (".", ".."):
            continue
        (root / safe_name).write_bytes(entry.data)
    return root


def timestamped_backup(path: str | Path, *, backup_root: str | Path | None = None) -> Path:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if backup_root is None:
        backup_root_path = source.parent / "_backups"
    else:
        backup_root_path = Path(backup_root)
    backup_root_path.mkdir(parents=True, exist_ok=True)
    candidate = backup_root_path / f"{source.stem}_{stamp}{source.suffix}"
    counter = 1
    while candidate.exists():
        candidate = backup_root_path / f"{source.stem}_{stamp}_{counter}{source.suffix}"
        counter += 1
    shutil.copy2(source, candidate)
    return candidate


def clone_file(source: str | Path, destination: str | Path) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
    return destination_path


def restore_backup(backup: str | Path, destination: str | Path) -> None:
    backup_path = Path(backup)
    destination_path = Path(destination)
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    shutil.copy2(backup_path, destination_path)
