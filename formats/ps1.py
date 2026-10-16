from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import struct

from psvcrypto.psv_crypto import PSV_DEFAULT_SEED, sign_ps1_psv, sign_vmp, verify_ps1_signature, verify_vmp_signature

from .common import SaveFormatError

PSV_MAGIC = b"\x00VSP"
PS1_PSV_TYPE = 1
PS1_BLOCK_SIZE = 0x2000
PS1_CARD_SIZE = 0x20000
PS1_SLOT_COUNT = 15
PS1_DIR_FRAME_SIZE = 0x80
PS1_DATA_OFFSET = 0x84

PS1_STATE_FREE = 0xA0
PS1_STATE_INITIAL = 0x51
PS1_STATE_MIDDLE = 0x52
PS1_STATE_END = 0x53
PS1_STATE_DELETED_INITIAL = 0xA1
PS1_STATE_DELETED_MIDDLE = 0xA2
PS1_STATE_DELETED_END = 0xA3


@dataclass(slots=True)
class PS1Save:
    filename: str
    data: bytes
    title: str = ""
    region: str = ""
    product_code: str = ""
    identifier: str = ""
    source_format: str = "RAW"
    start_slot: int | None = None
    slots: list[int] = field(default_factory=list)
    dir_header: bytes | None = None
    signature_valid: bool | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def blocks(self) -> int:
        return max(1, math.ceil(len(self.data) / PS1_BLOCK_SIZE))

    @property
    def payload_size(self) -> int:
        return len(self.data)


@dataclass(slots=True)
class PS1MemoryCard:
    path: Path | None
    raw: bytearray
    container_format: str
    saves: list[PS1Save]
    free_blocks: int
    warnings: list[str] = field(default_factory=list)
    vmp_signature_valid: bool | None = None
    wrapper: bytes = b""

    @property
    def size_bytes(self) -> int:
        return len(self.raw)



def _decode_ascii(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1", errors="replace").rstrip(" \xff")


def _encode_filename(name: str) -> bytes:
    raw = name.encode("latin-1", errors="replace")[:20]
    return raw + b"\0" * (20 - len(raw))


def _decode_sjis(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    for codec in ("shift_jis", "cp932", "latin-1"):
        try:
            return raw.decode(codec).strip()
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace").strip()


def _checksum(frame: bytes | bytearray) -> int:
    # PS1 directory frames XOR bytes 0..125. Byte 126 is unused and normally 0.
    value = 0
    for b in frame[:126]:
        value ^= b
    return value & 0xFF


def _fix_checksum(frame: bytearray) -> None:
    if len(frame) != PS1_DIR_FRAME_SIZE:
        raise ValueError("PS1 directory frame must be 128 bytes")
    frame[127] = _checksum(frame)


def _three_byte_le(data: bytes) -> int:
    return int.from_bytes(data[:3].ljust(3, b"\0"), "little")


def _set_three_byte_le(buf: bytearray, offset: int, value: int) -> None:
    buf[offset : offset + 3] = int(value).to_bytes(3, "little", signed=False)


def _parse_save_content(save: PS1Save) -> PS1Save:
    if len(save.data) < PS1_BLOCK_SIZE:
        save.warnings.append("Save payload is smaller than one PS1 memory-card block")
        return save
    block = save.data[:PS1_BLOCK_SIZE]
    if block[:2] not in (b"SC", b"sc"):
        save.warnings.append("First save block does not begin with the PS1 'SC' header")
        return save

    title = _decode_sjis(block[4:68])
    if title:
        save.title = title
    if save.filename:
        save.region = save.filename[:2]
        save.product_code = save.filename[2:12]
        save.identifier = save.filename[12:20]
    return save


def _normalise_payload(data: bytes) -> bytes:
    if not data:
        raise SaveFormatError("PS1 save contains no data")
    if len(data) % PS1_BLOCK_SIZE:
        # Single-save containers normally contain full card blocks. Preserve unusual
        # payloads by padding them rather than silently dropping bytes.
        data += b"\0" * (PS1_BLOCK_SIZE - len(data) % PS1_BLOCK_SIZE)
    return data


def looks_like_ps1_psv(data: bytes) -> bool:
    return len(data) >= PS1_DATA_OFFSET and data[:4] == PSV_MAGIC and struct.unpack_from("<I", data, 0x3C)[0] == 1


def parse_ps1_psv_bytes(data: bytes, *, verify_signature: bool = True) -> PS1Save:
    if len(data) < PS1_DATA_OFFSET:
        raise SaveFormatError("PS1 PSV file is too small")
    if data[:4] != PSV_MAGIC:
        raise SaveFormatError("Not a PSV file")
    save_type = struct.unpack_from("<I", data, 0x3C)[0]
    if save_type != PS1_PSV_TYPE:
        raise SaveFormatError(f"This PSV contains save type {save_type}; expected PS1 type 1")

    declared_size = struct.unpack_from("<I", data, 0x40)[0]
    data_pos = struct.unpack_from("<I", data, 0x44)[0]
    repeated_size = struct.unpack_from("<I", data, 0x5C)[0]
    if data_pos == 0:
        data_pos = PS1_DATA_OFFSET
    if data_pos < PS1_DATA_OFFSET or data_pos > len(data):
        raise SaveFormatError("PS1 PSV save-data offset is invalid")
    size = declared_size or repeated_size or (len(data) - data_pos)
    if repeated_size and declared_size and repeated_size != declared_size:
        # Real files generally repeat the same size. Keep parsing, but surface it.
        warning = f"PSV size fields differ ({declared_size} vs {repeated_size})"
    else:
        warning = ""
    if data_pos + size > len(data):
        raise SaveFormatError("PS1 PSV save payload is truncated")

    filename = _decode_ascii(data[0x64:0x78])
    payload = bytes(data[data_pos : data_pos + size])
    save = PS1Save(filename=filename, data=payload, source_format="PSV")
    if warning:
        save.warnings.append(warning)
    if len(payload) % PS1_BLOCK_SIZE:
        save.warnings.append("PS1 PSV payload is not a whole number of 8 KB blocks")

    if verify_signature:
        try:
            save.signature_valid = verify_ps1_signature(data)
        except RuntimeError:
            save.signature_valid = None
            save.warnings.append("PSV signature could not be checked because no AES backend is installed")
    return _parse_save_content(save)


def read_ps1_psv(path: str | Path, *, verify_signature: bool = True) -> PS1Save:
    return parse_ps1_psv_bytes(Path(path).read_bytes(), verify_signature=verify_signature)


def write_ps1_psv_bytes(save: PS1Save, *, seed: bytes = PSV_DEFAULT_SEED, sign: bool = True) -> bytes:
    if len(seed) != 20:
        raise ValueError("PSV seed must contain exactly 20 bytes")
    payload = _normalise_payload(save.data)

    out = bytearray()
    out += PSV_MAGIC
    out += struct.pack("<I", 0)
    out += seed
    out += b"\0" * 20
    # PS1 PSV uses the shorter 0x14 type-specific header marker.
    out += struct.pack("<IIII", 0, 0, 0x14, PS1_PSV_TYPE)

    # 68-byte PS1-specific header. 0x84 is the standard payload position and
    # 0x200/0x9003 are constants used by PS3-exported PS1 PSV saves.
    out += struct.pack("<I", len(payload))
    out += struct.pack("<I", PS1_DATA_OFFSET)
    out += struct.pack("<I", 0x200)
    out += b"\0" * 16
    out += struct.pack("<I", len(payload))
    out += struct.pack("<I", 0x9003)
    out += _encode_filename(save.filename)
    out += b"\0" * 12
    if len(out) != PS1_DATA_OFFSET:
        raise AssertionError(f"Internal PS1 PSV header error: {len(out)}")
    out += payload
    return sign_ps1_psv(bytes(out)) if sign else bytes(out)


def write_ps1_psv(save: PS1Save, path: str | Path) -> None:
    Path(path).write_bytes(write_ps1_psv_bytes(save))


def parse_mcs_bytes(data: bytes) -> PS1Save:
    if len(data) < PS1_DIR_FRAME_SIZE + PS1_BLOCK_SIZE:
        raise SaveFormatError("MCS file is too small")
    header = data[:PS1_DIR_FRAME_SIZE]
    if header[0] not in (PS1_STATE_INITIAL, PS1_STATE_DELETED_INITIAL):
        raise SaveFormatError("Not a PS1 MCS save")
    filename = _decode_ascii(header[10:30])
    size = _three_byte_le(header[4:7])
    payload = data[PS1_DIR_FRAME_SIZE:]
    if size and size <= len(payload):
        # Save formats carry full 8 KB blocks; retain the complete linked payload.
        blocks = max(1, math.ceil(size / PS1_BLOCK_SIZE))
        payload = payload[: blocks * PS1_BLOCK_SIZE]
    payload = _normalise_payload(payload)
    save = PS1Save(filename=filename, data=payload, source_format="MCS", dir_header=bytes(header))
    if _checksum(header) != header[127]:
        save.warnings.append("MCS directory-header XOR checksum is invalid")
    return _parse_save_content(save)


def read_mcs(path: str | Path) -> PS1Save:
    return parse_mcs_bytes(Path(path).read_bytes())


def write_mcs_bytes(save: PS1Save) -> bytes:
    payload = _normalise_payload(save.data)
    header = bytearray(save.dir_header or b"\0" * PS1_DIR_FRAME_SIZE)
    if len(header) != PS1_DIR_FRAME_SIZE:
        header = bytearray(PS1_DIR_FRAME_SIZE)
    header[0] = PS1_STATE_INITIAL
    # MCS stores the payload length in little endian and repeats it at 0x38.
    header[4:8] = len(payload).to_bytes(4, "little")
    header[56:60] = len(payload).to_bytes(4, "little")
    header[8:10] = b"\xFF\xFF"
    header[10:30] = _encode_filename(save.filename)
    _fix_checksum(header)
    return bytes(header) + payload


def write_mcs(save: PS1Save, path: str | Path) -> None:
    Path(path).write_bytes(write_mcs_bytes(save))


def parse_psx_bytes(data: bytes) -> PS1Save:
    if len(data) < 54 + PS1_BLOCK_SIZE or data[54:56] not in (b"SC", b"sc"):
        raise SaveFormatError("Not a supported PS1 PSX single-save file")
    filename = _decode_ascii(data[:20])
    payload = _normalise_payload(data[54:])
    return _parse_save_content(PS1Save(filename=filename, data=payload, source_format="PSX"))


def write_psx_bytes(save: PS1Save) -> bytes:
    # Common Action Replay / PSX header: 20-byte save name + 34 reserved bytes.
    return _encode_filename(save.filename) + b"\0" * 34 + _normalise_payload(save.data)


def read_psx(path: str | Path) -> PS1Save:
    return parse_psx_bytes(Path(path).read_bytes())


def write_psx(save: PS1Save, path: str | Path) -> None:
    Path(path).write_bytes(write_psx_bytes(save))


def parse_raw_save_bytes(data: bytes, *, filename: str = "") -> PS1Save:
    if len(data) < PS1_BLOCK_SIZE or data[:2] not in (b"SC", b"sc"):
        raise SaveFormatError("Not a raw PS1 single save")
    save = PS1Save(filename=filename[:20], data=_normalise_payload(data), source_format="RAW")
    return _parse_save_content(save)


def read_ps1_single(path: str | Path) -> PS1Save:
    path = Path(path)
    data = path.read_bytes()
    if looks_like_ps1_psv(data):
        return parse_ps1_psv_bytes(data)
    if len(data) >= 54 + 2 and data[54:56] in (b"SC", b"sc"):
        return parse_psx_bytes(data)
    if data and data[0] in (PS1_STATE_INITIAL, PS1_STATE_DELETED_INITIAL) and len(data) >= 128 + PS1_BLOCK_SIZE:
        return parse_mcs_bytes(data)
    if data[:2] in (b"SC", b"sc"):
        return parse_raw_save_bytes(data, filename=path.stem)
    raise SaveFormatError("Unsupported PS1 single-save format")


def write_ps1_single(save: PS1Save, path: str | Path, target: str) -> None:
    target = target.upper()
    if target == "PSV":
        write_ps1_psv(save, path)
    elif target == "MCS":
        write_mcs(save, path)
    elif target == "PSX":
        write_psx(save, path)
    elif target == "RAW":
        Path(path).write_bytes(_normalise_payload(save.data))
    else:
        raise ValueError(f"Unsupported PS1 output format: {target}")


def _unwrap_card(data: bytes, suffix: str = "") -> tuple[bytearray, str, bytes, bool | None]:
    suffix = suffix.lower()
    if data.startswith(b"\x00PMV") and len(data) >= 0x80 + PS1_CARD_SIZE:
        sig = None
        try:
            sig = verify_vmp_signature(data[: 0x80 + PS1_CARD_SIZE])
        except RuntimeError:
            pass
        return bytearray(data[0x80 : 0x80 + PS1_CARD_SIZE]), "VMP", bytes(data[:0x80]), sig
    if data.startswith(b"123-456-STD") and len(data) >= 3904 + PS1_CARD_SIZE:
        return bytearray(data[3904 : 3904 + PS1_CARD_SIZE]), "GME", bytes(data[:3904]), None
    if data.startswith(b"VgsM") and len(data) >= 64 + PS1_CARD_SIZE:
        return bytearray(data[64 : 64 + PS1_CARD_SIZE]), "VGS", bytes(data[:64]), None
    if len(data) >= PS1_CARD_SIZE and data[:2] == b"MC":
        fmt = suffix.lstrip(".").upper() if suffix in (".vm1", ".mcr", ".mcd", ".bin", ".vmc", ".srm") else "RAW"
        return bytearray(data[:PS1_CARD_SIZE]), fmt or "RAW", b"", None
    raise SaveFormatError("Not a supported PS1 memory-card image")


def looks_like_ps1_card(data: bytes) -> bool:
    if data.startswith((b"\x00PMV", b"123-456-STD", b"VgsM")):
        return True
    return len(data) >= PS1_CARD_SIZE and data[:2] == b"MC"


def _dir_frame(raw: bytes | bytearray, slot: int) -> bytes:
    if not 0 <= slot < PS1_SLOT_COUNT:
        raise IndexError(slot)
    off = PS1_DIR_FRAME_SIZE * (slot + 1)
    return bytes(raw[off : off + PS1_DIR_FRAME_SIZE])


def _block(raw: bytes | bytearray, slot: int) -> bytes:
    off = PS1_BLOCK_SIZE * (slot + 1)
    return bytes(raw[off : off + PS1_BLOCK_SIZE])


def _next_slot(frame: bytes) -> int | None:
    value = int.from_bytes(frame[8:10], "little")
    return None if value == 0xFFFF else value


def _follow_chain(raw: bytes | bytearray, start: int, warnings: list[str]) -> list[int]:
    slots: list[int] = []
    current: int | None = start
    seen: set[int] = set()
    while current is not None:
        if current in seen:
            warnings.append(f"Save chain starting at block {start + 1} contains a loop")
            break
        if not 0 <= current < PS1_SLOT_COUNT:
            warnings.append(f"Save chain starting at block {start + 1} points outside the card")
            break
        seen.add(current)
        slots.append(current)
        frame = _dir_frame(raw, current)
        current = _next_slot(frame)
        if len(slots) > PS1_SLOT_COUNT:
            break
    return slots


def parse_ps1_card_bytes(data: bytes, *, suffix: str = "", path: Path | None = None) -> PS1MemoryCard:
    raw, fmt, wrapper, vmp_sig = _unwrap_card(data, suffix)
    warnings: list[str] = []
    if raw[:2] != b"MC":
        raise SaveFormatError("PS1 memory-card header magic is invalid")

    frames = [_dir_frame(raw, slot) for slot in range(PS1_SLOT_COUNT)]
    for slot, frame in enumerate(frames):
        state = frame[0]
        if state not in (0, 0xFF) and _checksum(frame) != frame[127]:
            warnings.append(f"Directory block {slot + 1} has an invalid XOR checksum")

    saves: list[PS1Save] = []
    occupied: set[int] = set()
    for slot, frame in enumerate(frames):
        if frame[0] != PS1_STATE_INITIAL:
            continue
        local_warnings: list[str] = []
        chain = _follow_chain(raw, slot, local_warnings)
        occupied.update(chain)
        payload = b"".join(_block(raw, index) for index in chain)
        declared = _three_byte_le(frame[4:7])
        filename = _decode_ascii(frame[10:30])
        save = PS1Save(
            filename=filename,
            data=payload,
            source_format=fmt,
            start_slot=slot,
            slots=chain,
            dir_header=frame,
            warnings=local_warnings,
        )
        if declared and declared > len(payload):
            save.warnings.append(f"Directory says {declared} bytes but linked blocks contain only {len(payload)}")
        _parse_save_content(save)
        saves.append(save)

    free = sum(1 for frame in frames if frame[0] == PS1_STATE_FREE)
    card = PS1MemoryCard(
        path=path,
        raw=raw,
        container_format=fmt,
        saves=saves,
        free_blocks=free,
        warnings=warnings,
        vmp_signature_valid=vmp_sig,
        wrapper=wrapper,
    )
    return card


def read_ps1_card(path: str | Path) -> PS1MemoryCard:
    path = Path(path)
    return parse_ps1_card_bytes(path.read_bytes(), suffix=path.suffix, path=path)


def _make_free_frame() -> bytes:
    frame = bytearray(PS1_DIR_FRAME_SIZE)
    frame[0] = PS1_STATE_FREE
    frame[8:10] = b"\xFF\xFF"
    _fix_checksum(frame)
    return bytes(frame)


def format_ps1_card_raw() -> bytearray:
    raw = bytearray(PS1_CARD_SIZE)
    raw[0:2] = b"MC"
    raw[127] = 0x0E
    for slot in range(PS1_SLOT_COUNT):
        off = PS1_DIR_FRAME_SIZE * (slot + 1)
        raw[off : off + PS1_DIR_FRAME_SIZE] = _make_free_frame()

    # Broken-sector / replacement-sector frames. A formatted retail-style
    # PS1 card contains 20 reserved frames beginning at 0x800.
    for index in range(20):
        off = 0x800 + index * 0x80
        frame = bytearray(0x80)
        frame[0:4] = b"\xFF\xFF\xFF\xFF"
        frame[8:10] = b"\xFF\xFF"
        raw[off : off + 0x80] = frame
    raw[0x1F80 : 0x1F82] = b"MC"
    raw[0x1FFF] = 0x0E
    return raw


def _wrap_vmp(raw: bytes) -> bytes:
    if len(raw) != PS1_CARD_SIZE:
        raise ValueError("PS1 raw memory card must be exactly 128 KB")
    header = bytearray(0x80)
    header[:4] = b"\x00PMV"
    # Known VMP header version/size marker used by common converters.
    header[4:8] = (0x80).to_bytes(4, "little")
    header[0x0C:0x20] = PSV_DEFAULT_SEED
    output = bytes(header) + raw
    return sign_vmp(output)


def _wrap_vgs(raw: bytes) -> bytes:
    if len(raw) != PS1_CARD_SIZE:
        raise ValueError("PS1 raw memory card must be exactly 128 KB")
    # Virtual Game Station 64-byte wrapper.
    header = bytearray(64)
    header[:4] = b"VgsM"
    header[4] = 1
    header[8] = 1
    header[12] = 1
    header[17] = 2
    return bytes(header) + raw


def _wrap_gme(raw: bytes) -> bytes:
    if len(raw) != PS1_CARD_SIZE:
        raise ValueError("PS1 raw memory card must be exactly 128 KB")
    # DexDrive GME wrapper. Copy directory state/link bytes into the slot table
    # so old DexDrive-compatible tools see the same allocation chain.
    header = bytearray(3904)
    header[:11] = b"123-456-STD"
    header[18] = 1
    header[20] = 1
    header[21] = 0x4D
    for slot in range(PS1_SLOT_COUNT):
        frame = _dir_frame(raw, slot)
        header[22 + slot] = frame[0]
        header[38 + slot] = frame[8]
    return bytes(header) + raw


def card_bytes_for_format(card: PS1MemoryCard, target_format: str | None = None) -> bytes:
    fmt = (target_format or card.container_format or "RAW").upper()
    raw = bytes(card.raw)
    if fmt == "VMP":
        return _wrap_vmp(raw)
    if fmt == "GME":
        return _wrap_gme(raw)
    if fmt == "VGS":
        return _wrap_vgs(raw)
    return raw


def save_ps1_card(card: PS1MemoryCard, path: str | Path | None = None, *, target_format: str | None = None) -> None:
    output = Path(path) if path is not None else card.path
    if output is None:
        raise ValueError("No output path was supplied for the PS1 memory card")
    fmt = target_format
    if fmt is None:
        ext = output.suffix.lower()
        fmt = {".vmp": "VMP", ".gme": "GME", ".vgs": "VGS"}.get(ext, card.container_format)
    output.write_bytes(card_bytes_for_format(card, fmt))
    card.path = output


def create_ps1_memory_card(path: str | Path, *, overwrite: bool = False) -> None:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    fmt = {".vmp": "VMP", ".gme": "GME", ".vgs": "VGS"}.get(path.suffix.lower(), path.suffix.lstrip(".").upper() or "RAW")
    card = PS1MemoryCard(path=path, raw=format_ps1_card_raw(), container_format=fmt, saves=[], free_blocks=15)
    save_ps1_card(card, path, target_format=fmt)


def validate_ps1_card(card: PS1MemoryCard) -> list[str]:
    issues = list(card.warnings)
    used: set[int] = set()
    for save in card.saves:
        issues.extend(save.warnings)
        for slot in save.slots:
            if slot in used:
                issues.append(f"Memory-card block {slot + 1} is referenced by more than one save")
            used.add(slot)
    if card.container_format == "VMP" and card.vmp_signature_valid is False:
        issues.append("VMP signature is invalid")
    return issues


def import_save_into_card(card: PS1MemoryCard, save: PS1Save) -> None:
    payload = _normalise_payload(save.data)
    blocks_needed = len(payload) // PS1_BLOCK_SIZE
    free_slots = [slot for slot in range(PS1_SLOT_COUNT) if _dir_frame(card.raw, slot)[0] == PS1_STATE_FREE]
    if len(free_slots) < blocks_needed:
        raise SaveFormatError(f"PS1 memory card needs {blocks_needed} free blocks, but only {len(free_slots)} are available")

    # Prefer a contiguous run, as original/legacy PS1 tools do.
    slots: list[int] | None = None
    for start in range(0, PS1_SLOT_COUNT - blocks_needed + 1):
        candidate = list(range(start, start + blocks_needed))
        if all(slot in free_slots for slot in candidate):
            slots = candidate
            break
    if slots is None:
        # The on-card chain can represent non-contiguous blocks, so fragmented cards
        # can still be used safely when no contiguous run remains.
        slots = free_slots[:blocks_needed]

    for index, slot in enumerate(slots):
        frame = bytearray(PS1_DIR_FRAME_SIZE)
        if index == 0 and save.dir_header and len(save.dir_header) == PS1_DIR_FRAME_SIZE:
            frame[:] = save.dir_header
        state = PS1_STATE_INITIAL if index == 0 else PS1_STATE_END if index == blocks_needed - 1 else PS1_STATE_MIDDLE
        frame[0] = state
        _set_three_byte_le(frame, 4, len(payload))
        next_slot = slots[index + 1] if index + 1 < len(slots) else None
        frame[8:10] = (next_slot if next_slot is not None else 0xFFFF).to_bytes(2, "little")
        if index == 0:
            frame[10:30] = _encode_filename(save.filename)
        _fix_checksum(frame)
        off = PS1_DIR_FRAME_SIZE * (slot + 1)
        card.raw[off : off + PS1_DIR_FRAME_SIZE] = frame

        block_off = PS1_BLOCK_SIZE * (slot + 1)
        start = index * PS1_BLOCK_SIZE
        card.raw[block_off : block_off + PS1_BLOCK_SIZE] = payload[start : start + PS1_BLOCK_SIZE]

    _refresh_card(card)


def delete_save_from_card(card: PS1MemoryCard, start_slot: int) -> None:
    target = next((save for save in card.saves if save.start_slot == start_slot), None)
    if target is None:
        raise SaveFormatError("Selected PS1 save no longer exists on the card")
    for slot in target.slots:
        off = PS1_DIR_FRAME_SIZE * (slot + 1)
        card.raw[off : off + PS1_DIR_FRAME_SIZE] = _make_free_frame()
        block_off = PS1_BLOCK_SIZE * (slot + 1)
        card.raw[block_off : block_off + PS1_BLOCK_SIZE] = b"\0" * PS1_BLOCK_SIZE
    _refresh_card(card)


def _refresh_card(card: PS1MemoryCard) -> None:
    refreshed = parse_ps1_card_bytes(bytes(card.raw), suffix="." + card.container_format.lower() if card.container_format else "", path=card.path)
    card.saves = refreshed.saves
    card.free_blocks = refreshed.free_blocks
    card.warnings = refreshed.warnings


def extract_icon_frames_rgba(save: PS1Save) -> list[bytes]:
    if len(save.data) < PS1_BLOCK_SIZE or save.data[:2] not in (b"SC", b"sc"):
        return []
    first = save.data[:PS1_BLOCK_SIZE]
    icon_flag = first[2]
    frame_count = {0x11: 1, 0x12: 2, 0x13: 3}.get(icon_flag)
    if frame_count is None:
        low = icon_flag & 0x0F
        frame_count = low if 1 <= low <= 3 else 0
    if not frame_count:
        return []

    palette: list[tuple[int, int, int, int]] = []
    for i in range(16):
        value = struct.unpack_from("<H", first, 0x60 + i * 2)[0]
        r = (value & 0x1F) * 255 // 31
        g = ((value >> 5) & 0x1F) * 255 // 31
        b = ((value >> 10) & 0x1F) * 255 // 31
        # MemcardRex-compatible behaviour: only an all-zero entry is transparent.
        a = 0 if value == 0 else 255
        palette.append((r, g, b, a))

    frames: list[bytes] = []
    for frame_index in range(frame_count):
        offset = 0x80 + frame_index * 0x80
        if offset + 0x80 > len(first):
            break
        packed = first[offset : offset + 0x80]
        rgba = bytearray(16 * 16 * 4)
        pixel_index = 0
        for byte in packed:
            for palette_index in (byte & 0x0F, (byte >> 4) & 0x0F):
                color = palette[palette_index]
                j = pixel_index * 4
                rgba[j : j + 4] = bytes(color)
                pixel_index += 1
        frames.append(bytes(rgba))
    return frames
