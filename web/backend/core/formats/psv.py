from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

from psvcrypto.psv_crypto import PSV_DEFAULT_SEED, sign_ps2_psv, verify_ps2_signature
from ps2icon.icon_sys import parse_icon_sys

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

PSV_MAGIC = b"\x00VSP"
PSV_HEADER_SIZE = 64
PS2_HEADER_SIZE = 40
PS2_DIR_SIZE = 56
PS2_FILEINFO_SIZE = 60
PS2_TYPE = 2


def _decode_time(raw: bytes) -> datetime | None:
    if len(raw) != 8:
        return None
    _reserved, second, minute, hour, day, month, year = struct.unpack("<6BH", raw)
    if not year or not month or not day:
        return None
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _encode_time(value: datetime | None) -> bytes:
    value = value or default_timestamp()
    return struct.pack(
        "<6BH",
        0,
        max(0, min(59, value.second)),
        max(0, min(59, value.minute)),
        max(0, min(23, value.hour)),
        max(1, min(31, value.day)),
        max(1, min(12, value.month)),
        max(1, min(65535, value.year)),
    )


def looks_like_psv(data: bytes) -> bool:
    return len(data) >= 0xA0 and data[:4] == PSV_MAGIC and struct.unpack_from("<I", data, 0x3C)[0] == PS2_TYPE


def parse_psv_bytes(data: bytes, *, verify_signature: bool = True) -> SaveArchive:
    if len(data) < 0xA0:
        raise SaveFormatError("PSV file is too small")
    if data[:4] != PSV_MAGIC:
        raise SaveFormatError("Not a PSV file")

    save_type = struct.unpack_from("<I", data, 0x3C)[0]
    if save_type != PS2_TYPE:
        raise SaveFormatError(f"This PSV contains save type {save_type}; only PS2 PSV saves are supported")

    (
        display_size,
        sys_pos,
        sys_size,
        icon1_pos,
        icon1_size,
        icon2_pos,
        icon2_size,
        icon3_pos,
        icon3_size,
        number_of_files,
    ) = struct.unpack_from("<10I", data, PSV_HEADER_SIZE)

    dir_off = PSV_HEADER_SIZE + PS2_HEADER_SIZE
    created = _decode_time(data[dir_off : dir_off + 8])
    modified = _decode_time(data[dir_off + 8 : dir_off + 16])
    dir_count, attr = struct.unpack_from("<II", data, dir_off + 16)
    dirname = decode_ps2_name(data[dir_off + 24 : dir_off + 56])

    if dir_count != number_of_files + 2:
        raise SaveFormatError(
            f"PSV directory count mismatch ({dir_count} vs expected {number_of_files + 2})"
        )
    infos_end = dir_off + PS2_DIR_SIZE + number_of_files * PS2_FILEINFO_SIZE
    if infos_end > len(data):
        raise SaveFormatError("PSV file-info table is truncated")

    entries: list[SaveEntry] = []
    ranges: list[tuple[int, int, str]] = []
    info_pos = dir_off + PS2_DIR_SIZE
    for _index in range(number_of_files):
        raw = data[info_pos : info_pos + PS2_FILEINFO_SIZE]
        e_created = _decode_time(raw[0:8])
        e_modified = _decode_time(raw[8:16])
        filesize, e_attr = struct.unpack_from("<II", raw, 16)
        name = decode_ps2_name(raw[24:56])
        payload_pos = struct.unpack_from("<I", raw, 56)[0]
        info_pos += PS2_FILEINFO_SIZE

        end = payload_pos + filesize
        if payload_pos < infos_end or end > len(data) or end < payload_pos:
            raise SaveFormatError(f"PSV payload range for '{name}' is invalid or truncated")
        ranges.append((payload_pos, end, name))
        entries.append(
            SaveEntry(
                name=name,
                data=data[payload_pos:end],
                mode=e_attr or PS2_MODE_FILE,
                created=e_created,
                modified=e_modified,
                attr=0,
            )
        )

    sorted_ranges = sorted(ranges)
    for left, right in zip(sorted_ranges, sorted_ranges[1:]):
        if left[1] > right[0]:
            raise SaveFormatError(f"PSV payloads overlap: '{left[2]}' and '{right[2]}'")

    warnings: list[str] = []
    actual_display = sum(e.size for e in entries)
    if actual_display != display_size:
        warnings.append(f"Display size says {display_size} bytes; files contain {actual_display} bytes")

    for label, pos, size in (
        ("icon.sys", sys_pos, sys_size),
        ("icon 1", icon1_pos, icon1_size),
        ("icon 2", icon2_pos, icon2_size),
        ("icon 3", icon3_pos, icon3_size),
    ):
        if (pos or size) and (pos < infos_end or pos + size > len(data)):
            warnings.append(f"{label} position in the PSV header is invalid")

    signature_valid: bool | None
    if verify_signature:
        try:
            signature_valid = verify_ps2_signature(data)
        except RuntimeError:
            signature_valid = None
            warnings.append("PSV signature was not checked because no AES backend is installed")
    else:
        signature_valid = None

    archive = SaveArchive(
        dirname=dirname,
        entries=entries,
        mode=attr or PS2_MODE_DIR,
        created=created,
        modified=modified,
        attr=0,
        source_format="PSV",
        signature_valid=signature_valid,
        warnings=warnings,
    )
    ensure_unique_names(archive.entries)
    return archive


def read_psv(path: str | Path, *, verify_signature: bool = True) -> SaveArchive:
    return parse_psv_bytes(Path(path).read_bytes(), verify_signature=verify_signature)


def _icon_positions(archive: SaveArchive, positions: dict[str, tuple[int, int]]) -> tuple[int, int, int, int, int, int, int, int]:
    sys_entry = archive.get("icon.sys")
    sys_pos, sys_size = positions.get(sys_entry.name.casefold(), (0, 0)) if sys_entry else (0, 0)

    icon_names: list[str] = []
    if sys_entry:
        try:
            icon_sys = parse_icon_sys(sys_entry.data)
            icon_names = [icon_sys.icon_name, icon_sys.copy_icon_name, icon_sys.delete_icon_name]
        except Exception:
            pass

    icon_pairs: list[tuple[int, int]] = []
    for name in icon_names[:3]:
        icon_pairs.append(positions.get(name.casefold(), (0, 0)) if name else (0, 0))
    while len(icon_pairs) < 3:
        icon_pairs.append((0, 0))

    return (
        sys_pos,
        sys_size,
        icon_pairs[0][0],
        icon_pairs[0][1],
        icon_pairs[1][0],
        icon_pairs[1][1],
        icon_pairs[2][0],
        icon_pairs[2][1],
    )


def write_psv_bytes(archive: SaveArchive, *, seed: bytes | None = None, sign: bool = True) -> bytes:
    ensure_unique_names(archive.entries)
    if seed is None:
        seed = PSV_DEFAULT_SEED
    if len(seed) != 20:
        raise ValueError("PSV seed must contain exactly 20 bytes")

    n = len(archive.entries)
    data_start = PSV_HEADER_SIZE + PS2_HEADER_SIZE + PS2_DIR_SIZE + PS2_FILEINFO_SIZE * n
    cursor = data_start
    positions: dict[str, tuple[int, int]] = {}
    for entry in archive.entries:
        positions[entry.name.casefold()] = (cursor, len(entry.data))
        cursor += len(entry.data)

    sys_pos, sys_size, i1_pos, i1_size, i2_pos, i2_size, i3_pos, i3_size = _icon_positions(
        archive, positions
    )

    created = archive.created or default_timestamp()
    modified = archive.modified or created

    out = bytearray()
    # Common 64-byte PSV header.
    out += PSV_MAGIC
    out += struct.pack("<I", 0)
    out += seed
    out += b"\0" * 20  # signature placeholder
    out += struct.pack("<IIII", 0, 0, 0x2C, PS2_TYPE)

    out += struct.pack(
        "<10I",
        archive.total_size,
        sys_pos,
        sys_size,
        i1_pos,
        i1_size,
        i2_pos,
        i2_size,
        i3_pos,
        i3_size,
        n,
    )

    out += _encode_time(created)
    out += _encode_time(modified)
    out += struct.pack("<II", n + 2, (archive.mode or PS2_MODE_DIR) & 0xFFFFFFFF)
    out += encode_ps2_name(archive.dirname, 32)

    for entry in archive.entries:
        payload_pos, _ = positions[entry.name.casefold()]
        out += _encode_time(entry.created or created)
        out += _encode_time(entry.modified or modified)
        out += struct.pack("<II", len(entry.data), (entry.mode or PS2_MODE_FILE) & 0xFFFFFFFF)
        out += encode_ps2_name(entry.name, 32)
        out += struct.pack("<I", payload_pos)

    if len(out) != data_start:
        raise AssertionError(f"Internal PSV layout error: expected {data_start}, got {len(out)}")

    for entry in archive.entries:
        out += entry.data

    return sign_ps2_psv(bytes(out)) if sign else bytes(out)


def write_psv(archive: SaveArchive, path: str | Path, *, sign: bool = True) -> None:
    Path(path).write_bytes(write_psv_bytes(archive, sign=sign))
