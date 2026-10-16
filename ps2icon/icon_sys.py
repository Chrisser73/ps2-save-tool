from __future__ import annotations

from dataclasses import dataclass
import struct


class IconSysError(ValueError):
    pass


@dataclass(slots=True)
class IconSys:
    title: str
    second_line_offset: int
    transparency: int
    background_colors: list[tuple[int, int, int, int]]
    light_directions: list[tuple[float, float, float, float]]
    light_colors: list[tuple[float, float, float, float]]
    ambient: tuple[float, float, float, float]
    icon_name: str
    copy_icon_name: str
    delete_icon_name: str
    raw_title: bytes


def _c_string(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    for codec in ("shift_jis", "ascii", "latin-1"):
        try:
            return raw.decode(codec).strip()
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace").strip()


def _decode_title(raw: bytes, second_line_offset: int) -> str:
    # second_line_offset is a byte offset into the 68-byte Shift-JIS title field.
    if 0 < second_line_offset < len(raw):
        first = _c_string(raw[:second_line_offset])
        second = _c_string(raw[second_line_offset:])
        if first and second:
            return f"{first}\n{second}"
    return _c_string(raw)


def parse_icon_sys(data: bytes) -> IconSys:
    if len(data) < 452:
        raise IconSysError("icon.sys is truncated")
    magic = data[:4]
    if magic not in (b"PS2D", b"PS2X"):
        # A few tools leave non-standard magic values; keep parsing if the file is large enough.
        if len(data) < 964:
            raise IconSysError("Not a PS2 icon.sys file")

    second_line_offset = struct.unpack_from("<H", data, 6)[0]
    transparency = struct.unpack_from("<I", data, 12)[0]

    background: list[tuple[int, int, int, int]] = []
    pos = 16
    for _ in range(4):
        background.append(struct.unpack_from("<4I", data, pos))
        pos += 16

    light_dirs: list[tuple[float, float, float, float]] = []
    for _ in range(3):
        light_dirs.append(struct.unpack_from("<4f", data, pos))
        pos += 16

    light_colors: list[tuple[float, float, float, float]] = []
    for _ in range(3):
        light_colors.append(struct.unpack_from("<4f", data, pos))
        pos += 16

    ambient = struct.unpack_from("<4f", data, pos)
    pos += 16
    title_raw = data[pos : pos + 68]
    pos += 68
    icon_name = _c_string(data[pos : pos + 64])
    pos += 64
    copy_name = _c_string(data[pos : pos + 64])
    pos += 64
    delete_name = _c_string(data[pos : pos + 64])

    return IconSys(
        title=_decode_title(title_raw, second_line_offset),
        second_line_offset=second_line_offset,
        transparency=transparency,
        background_colors=background,
        light_directions=light_dirs,
        light_colors=light_colors,
        ambient=ambient,
        icon_name=icon_name,
        copy_icon_name=copy_name,
        delete_icon_name=delete_name,
        raw_title=title_raw,
    )


def update_icon_sys_title(data: bytes, title: str) -> bytes:
    """Return icon.sys bytes with an updated visible save title.

    ``title`` may contain one newline to define the PS2 Browser's second line.
    All other icon.sys metadata is preserved.
    """
    if len(data) < 452:
        raise IconSysError("icon.sys is truncated")
    out = bytearray(data)
    normalized = title.replace("\r\n", "\n").replace("\r", "\n")
    first, sep, second = normalized.partition("\n")
    first_raw = first.encode("shift_jis", errors="replace")
    second_raw = second.encode("shift_jis", errors="replace") if sep else b""
    if second_raw:
        if len(first_raw) + 1 + len(second_raw) > 67:
            raise IconSysError("Title is too long for icon.sys (maximum 67 Shift-JIS bytes including line break)")
        field = first_raw + b"\0" + second_raw
        second_offset = len(first_raw) + 1
    else:
        if len(first_raw) > 67:
            raise IconSysError("Title is too long for icon.sys (maximum 67 Shift-JIS bytes)")
        field = first_raw
        second_offset = 0
    struct.pack_into("<H", out, 6, second_offset)
    out[192:260] = field.ljust(68, b"\0")
    return bytes(out)
