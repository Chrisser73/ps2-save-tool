from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

from .common import (
    PS2_MODE_DIR,
    PS2_MODE_FILE,
    SaveArchive,
    SaveEntry,
    SaveFormatError,
    decode_ps2_name,
    default_timestamp,
    encode_ps2_name,
    ensure_unique_names,
)

ENTRY_SIZE = 512
FILE_ALIGNMENT = 1024


def _decode_time(raw: bytes) -> datetime | None:
    if len(raw) != 8:
        return None
    _reserved, second, minute, hour, day, month, year = struct.unpack("<6BH", raw)
    if year == 0 or month == 0 or day == 0:
        return None
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _encode_time(value: datetime | None) -> bytes:
    value = value or default_timestamp()
    year = max(1, min(65535, value.year))
    return struct.pack(
        "<6BH",
        0,
        max(0, min(59, value.second)),
        max(0, min(59, value.minute)),
        max(0, min(23, value.hour)),
        max(1, min(31, value.day)),
        max(1, min(12, value.month)),
        year,
    )


def _parse_descriptor(raw: bytes) -> dict:
    if len(raw) != ENTRY_SIZE:
        raise SaveFormatError("Truncated PSU directory descriptor")
    mode, _unused, length = struct.unpack_from("<HHI", raw, 0)
    created = _decode_time(raw[8:16])
    cluster, dir_entry = struct.unpack_from("<II", raw, 16)
    modified = _decode_time(raw[24:32])
    attr = struct.unpack_from("<I", raw, 32)[0]
    name = decode_ps2_name(raw[64:96])
    return {
        "mode": mode,
        "length": length,
        "created": created,
        "cluster": cluster,
        "dir_entry": dir_entry,
        "modified": modified,
        "attr": attr,
        "name": name,
    }


def _descriptor(
    *,
    name: str,
    mode: int,
    length: int,
    created: datetime | None,
    modified: datetime | None,
    attr: int = 0,
) -> bytes:
    out = bytearray(ENTRY_SIZE)
    struct.pack_into("<HHI", out, 0, mode & 0xFFFF, 0, length & 0xFFFFFFFF)
    out[8:16] = _encode_time(created)
    struct.pack_into("<II", out, 16, 0, 0)
    out[24:32] = _encode_time(modified)
    struct.pack_into("<I", out, 32, attr & 0xFFFFFFFF)
    out[64:96] = encode_ps2_name(name, 32)
    return bytes(out)


def looks_like_psu(data: bytes) -> bool:
    if len(data) < ENTRY_SIZE * 3:
        return False
    try:
        root = _parse_descriptor(data[:ENTRY_SIZE])
        dot = _parse_descriptor(data[ENTRY_SIZE : ENTRY_SIZE * 2])
        dotdot = _parse_descriptor(data[ENTRY_SIZE * 2 : ENTRY_SIZE * 3])
    except SaveFormatError:
        return False
    return (
        bool(root["mode"] & 0x20)
        and dot["name"] == "."
        and dotdot["name"] == ".."
        and bool(dot["mode"] & 0x20)
        and bool(dotdot["mode"] & 0x20)
    )


def parse_psu_bytes(data: bytes) -> SaveArchive:
    if len(data) < ENTRY_SIZE * 3:
        raise SaveFormatError("PSU file is too small")

    root = _parse_descriptor(data[:ENTRY_SIZE])
    dot = _parse_descriptor(data[ENTRY_SIZE : ENTRY_SIZE * 2])
    dotdot = _parse_descriptor(data[ENTRY_SIZE * 2 : ENTRY_SIZE * 3])

    if not (root["mode"] & 0x20):
        raise SaveFormatError("PSU root entry is not a directory")
    if dot["name"] != "." or dotdot["name"] != "..":
        raise SaveFormatError("PSU is missing the . / .. directory entries")
    if root["length"] < 2:
        raise SaveFormatError("PSU root has an invalid file count")

    file_count = int(root["length"] - 2)
    pos = ENTRY_SIZE * 3
    entries: list[SaveEntry] = []
    warnings: list[str] = []

    for index in range(file_count):
        if pos + ENTRY_SIZE > len(data):
            raise SaveFormatError(f"PSU is truncated before file header {index + 1}")
        desc = _parse_descriptor(data[pos : pos + ENTRY_SIZE])
        pos += ENTRY_SIZE
        size = int(desc["length"])
        if pos + size > len(data):
            raise SaveFormatError(f"PSU file '{desc['name']}' is truncated")
        payload = data[pos : pos + size]
        pos += size
        pad = (-size) % FILE_ALIGNMENT
        if pos + pad > len(data):
            raise SaveFormatError(f"PSU padding after '{desc['name']}' is truncated")
        if pad and any(b not in (0x00, 0xFF) for b in data[pos : pos + pad]):
            warnings.append(f"Non-standard padding after {desc['name']}")
        pos += pad
        entries.append(
            SaveEntry(
                name=desc["name"],
                data=payload,
                mode=desc["mode"] or PS2_MODE_FILE,
                created=desc["created"],
                modified=desc["modified"],
                attr=desc["attr"],
            )
        )

    if pos < len(data) and any(b not in (0x00, 0xFF) for b in data[pos:]):
        warnings.append(f"{len(data) - pos} trailing byte(s) after the PSU payload")

    archive = SaveArchive(
        dirname=root["name"],
        entries=entries,
        mode=root["mode"],
        created=root["created"],
        modified=root["modified"],
        attr=root["attr"],
        source_format="PSU",
        warnings=warnings,
    )
    ensure_unique_names(archive.entries)
    return archive


def read_psu(path: str | Path) -> SaveArchive:
    return parse_psu_bytes(Path(path).read_bytes())


def write_psu_bytes(archive: SaveArchive) -> bytes:
    ensure_unique_names(archive.entries)
    created = archive.created or default_timestamp()
    modified = archive.modified or created
    root_mode = archive.mode or PS2_MODE_DIR

    out = bytearray()
    out += _descriptor(
        name=archive.dirname,
        mode=root_mode,
        length=len(archive.entries) + 2,
        created=created,
        modified=modified,
        attr=archive.attr,
    )
    out += _descriptor(
        name=".",
        mode=PS2_MODE_DIR,
        length=0,
        created=created,
        modified=created,
        attr=0,
    )
    out += _descriptor(
        name="..",
        mode=PS2_MODE_DIR,
        length=0,
        created=created,
        modified=created,
        attr=0,
    )

    for entry in archive.entries:
        out += _descriptor(
            name=entry.name,
            mode=entry.mode or PS2_MODE_FILE,
            length=len(entry.data),
            created=entry.created or created,
            modified=entry.modified or modified,
            attr=entry.attr,
        )
        out += entry.data
        pad = (-len(entry.data)) % FILE_ALIGNMENT
        if pad:
            out += b"\x00" * pad
    return bytes(out)


def write_psu(archive: SaveArchive, path: str | Path) -> None:
    Path(path).write_bytes(write_psu_bytes(archive))
