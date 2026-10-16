from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest

from formats.detect import detect_bytes
from formats.ps1 import (
    PS1Save,
    create_ps1_memory_card,
    delete_save_from_card,
    extract_icon_frames_rgba,
    format_ps1_card_raw,
    import_save_into_card,
    parse_mcs_bytes,
    parse_ps1_card_bytes,
    parse_ps1_psv_bytes,
    save_ps1_card,
    card_bytes_for_format,
    write_mcs_bytes,
    write_ps1_psv_bytes,
)
from psvcrypto.psv_crypto import verify_ps1_signature, verify_vmp_signature


def make_ps1_save() -> PS1Save:
    block = bytearray(0x2000)
    block[:2] = b"SC"
    block[2] = 0x12  # two animated icon frames
    block[3] = 1
    title = "Test PS1 Save".encode("shift_jis")
    block[4 : 4 + len(title)] = title
    # Palette: transparent black, red, green, blue, then white-ish entries.
    palette = [0x0000, 0x801F, 0x83E0, 0xFC00] + [0xFFFF] * 12
    for i, value in enumerate(palette):
        struct.pack_into("<H", block, 0x60 + i * 2, value)
    # Frame 1 alternates palette indexes 1/2; frame 2 alternates 2/3.
    block[0x80:0x100] = bytes([0x21]) * 0x80
    block[0x100:0x180] = bytes([0x32]) * 0x80
    return PS1Save(filename="BASLUS-12345TEST123", data=bytes(block), title="Test PS1 Save")


class PS1Tests(unittest.TestCase):
    def test_ps1_psv_roundtrip_and_signature(self):
        src = make_ps1_save()
        raw = write_ps1_psv_bytes(src)
        self.assertEqual(detect_bytes(raw), "PS1_PSV")
        self.assertEqual(struct.unpack_from("<I", raw, 0x38)[0], 0x14)
        self.assertTrue(verify_ps1_signature(raw))
        parsed = parse_ps1_psv_bytes(raw)
        self.assertTrue(parsed.signature_valid)
        self.assertEqual(parsed.filename, src.filename[:20])
        self.assertEqual(parsed.title, "Test PS1 Save")
        self.assertEqual(parsed.data, src.data)
        self.assertEqual(len(extract_icon_frames_rgba(parsed)), 2)

    def test_mcs_roundtrip(self):
        src = make_ps1_save()
        raw = write_mcs_bytes(src)
        self.assertEqual(detect_bytes(raw), "PS1_MCS")
        self.assertEqual(int.from_bytes(raw[4:8], "little"), len(src.data))
        self.assertEqual(int.from_bytes(raw[56:60], "little"), len(src.data))
        parsed = parse_mcs_bytes(raw)
        self.assertEqual(parsed.filename, src.filename[:20])
        self.assertEqual(parsed.data, src.data)

    def test_card_create_import_delete(self):
        card = parse_ps1_card_bytes(bytes(format_ps1_card_raw()), suffix=".vm1")
        self.assertEqual(card.free_blocks, 15)
        self.assertEqual(len(card.saves), 0)
        import_save_into_card(card, make_ps1_save())
        self.assertEqual(card.free_blocks, 14)
        self.assertEqual(len(card.saves), 1)
        self.assertEqual(card.saves[0].title, "Test PS1 Save")
        delete_save_from_card(card, card.saves[0].start_slot)
        self.assertEqual(card.free_blocks, 15)
        self.assertEqual(len(card.saves), 0)

    def test_gme_and_vgs_wrappers(self):
        card = parse_ps1_card_bytes(bytes(format_ps1_card_raw()), suffix=".vm1")
        gme = card_bytes_for_format(card, "GME")
        self.assertTrue(gme.startswith(b"123-456-STD"))
        self.assertEqual(gme[18], 1)
        self.assertEqual(gme[20], 1)
        self.assertEqual(gme[21], 0x4D)
        self.assertEqual(len(gme), 3904 + 0x20000)
        self.assertEqual(parse_ps1_card_bytes(gme, suffix=".gme").container_format, "GME")

        vgs = card_bytes_for_format(card, "VGS")
        self.assertEqual(vgs[:4], b"VgsM")
        self.assertEqual((vgs[4], vgs[8], vgs[12], vgs[17]), (1, 1, 1, 2))
        self.assertEqual(len(vgs), 64 + 0x20000)
        self.assertEqual(parse_ps1_card_bytes(vgs, suffix=".vgs").container_format, "VGS")

    def test_vmp_card_signing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.vmp"
            create_ps1_memory_card(path)
            raw = path.read_bytes()
            self.assertEqual(len(raw), 0x20080)
            self.assertTrue(raw.startswith(b"\x00PMV"))
            self.assertTrue(verify_vmp_signature(raw))
            card = parse_ps1_card_bytes(raw, suffix=".vmp", path=path)
            self.assertEqual(card.container_format, "VMP")
            self.assertTrue(card.vmp_signature_valid)


if __name__ == "__main__":
    unittest.main()
