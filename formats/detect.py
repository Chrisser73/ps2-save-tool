from __future__ import annotations

from pathlib import Path
import struct

from .psu import looks_like_psu
from .psv import PSV_MAGIC
from .ps1 import PS1_CARD_SIZE, looks_like_ps1_card

PS2_CARD_MAGIC = b"Sony PS2 Memory Card Format "


class UnknownFormatError(ValueError):
    pass


def psv_type(data: bytes) -> int | None:
    if len(data) < 0x40 or data[:4] != PSV_MAGIC:
        return None
    return struct.unpack_from("<I", data, 0x3C)[0]


def detect_bytes(data: bytes) -> str:
    save_type = psv_type(data)
    if save_type == 2:
        return "PSV"
    if save_type == 1:
        return "PS1_PSV"
    if looks_like_psu(data):
        return "PSU"
    if data.startswith(PS2_CARD_MAGIC):
        return "MEMORY_CARD"
    if looks_like_ps1_card(data):
        return "PS1_CARD"
    if len(data) >= 128 + 2 and data[0] in (0x51, 0xA1) and data[128:130] in (b"SC", b"sc"):
        return "PS1_MCS"
    if len(data) >= 56 and data[54:56] in (b"SC", b"sc"):
        return "PS1_PSX"
    if len(data) >= 2 and data[:2] in (b"SC", b"sc"):
        return "PS1_RAW"
    raise UnknownFormatError(
        "The selected file is not a supported PS1/PS2 save or memory-card image"
    )


def detect_path(path: str | Path) -> str:
    path = Path(path)
    # PS1 cards are only 128 KB; reading 4 KB was insufficient to distinguish a
    # raw card from a few wrapper formats by size/content.
    with path.open("rb") as fh:
        head = fh.read(max(4096, PS1_CARD_SIZE + 3904))
    return detect_bytes(head)
