from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


PS2_MODE_DIR = 0x8427
PS2_MODE_FILE = 0x8497


class SaveFormatError(ValueError):
    """Raised when a save container is invalid or truncated."""


@dataclass(slots=True)
class SaveEntry:
    name: str
    data: bytes
    mode: int = PS2_MODE_FILE
    created: datetime | None = None
    modified: datetime | None = None
    attr: int = 0

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(slots=True)
class SaveArchive:
    dirname: str
    entries: list[SaveEntry] = field(default_factory=list)
    mode: int = PS2_MODE_DIR
    created: datetime | None = None
    modified: datetime | None = None
    attr: int = 0
    source_format: str = ""
    signature_valid: bool | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self.entries)

    def get(self, name: str) -> SaveEntry | None:
        low = name.lower()
        for entry in self.entries:
            if entry.name.lower() == low:
                return entry
        return None

    def names(self) -> list[str]:
        return [e.name for e in self.entries]


def decode_ps2_name(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    for codec in ("shift_jis", "ascii", "latin-1"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace")


def encode_ps2_name(name: str, size: int = 32) -> bytes:
    try:
        raw = name.encode("shift_jis")
    except UnicodeEncodeError:
        raw = name.encode("ascii", errors="replace")
    if len(raw) >= size:
        raw = raw[: size - 1]
    return raw + b"\0" * (size - len(raw))


def default_timestamp() -> datetime:
    # PS2 timestamps have one-second resolution and no timezone information.
    return datetime.now().replace(microsecond=0)


def human_size(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    n = float(value)
    for unit in units:
        if n < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{value} B"


def safe_output_path(input_path: Path, suffix: str) -> Path:
    return input_path.with_suffix(suffix)


def ensure_unique_names(entries: Iterable[SaveEntry]) -> None:
    seen: set[str] = set()
    for entry in entries:
        key = entry.name.casefold()
        if key in seen:
            raise SaveFormatError(f"Duplicate file name in save: {entry.name}")
        seen.add(key)
