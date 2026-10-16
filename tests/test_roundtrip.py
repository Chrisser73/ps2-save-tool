from __future__ import annotations

from datetime import datetime
import struct
import unittest

from formats.common import PS2_MODE_DIR, PS2_MODE_FILE, SaveArchive, SaveEntry, SaveFormatError
from formats.detect import detect_bytes
from formats.psu import parse_psu_bytes, write_psu_bytes
from formats.psv import parse_psv_bytes, write_psv_bytes
from psvcrypto.psv_crypto import PSV_DEFAULT_SEED, verify_ps2_signature


def make_icon_sys() -> bytes:
    out = bytearray(964)
    out[:4] = b"PS2D"
    title = "Test Save".encode("shift_jis")
    out[192 : 192 + len(title)] = title
    out[260 : 260 + len(b"icon.ico")] = b"icon.ico"
    return bytes(out)


class SaveRoundTripTests(unittest.TestCase):
    def make_archive(self) -> SaveArchive:
        stamp = datetime(2025, 2, 3, 4, 5, 6)
        return SaveArchive(
            dirname="BASLUS-99999TEST",
            mode=PS2_MODE_DIR,
            created=stamp,
            modified=stamp,
            entries=[
                SaveEntry("icon.sys", make_icon_sys(), mode=PS2_MODE_FILE, created=stamp, modified=stamp),
                SaveEntry("DATA.BIN", b"hello ps2" * 37, mode=PS2_MODE_FILE, created=stamp, modified=stamp),
            ],
        )

    def test_psu_roundtrip(self):
        src = self.make_archive()
        raw = write_psu_bytes(src)
        self.assertEqual(detect_bytes(raw[:4096]), "PSU")
        parsed = parse_psu_bytes(raw)
        self.assertEqual(parsed.dirname, src.dirname)
        self.assertEqual(parsed.names(), src.names())
        self.assertEqual(parsed.get("DATA.BIN").data, src.get("DATA.BIN").data)
        self.assertEqual(parsed.get("DATA.BIN").mode, PS2_MODE_FILE)

    def test_psv_roundtrip_and_signature(self):
        src = self.make_archive()
        raw = write_psv_bytes(src, seed=b"\x11" * 20)
        self.assertEqual(detect_bytes(raw[:4096]), "PSV")
        self.assertTrue(verify_ps2_signature(raw))
        parsed = parse_psv_bytes(raw)
        self.assertTrue(parsed.signature_valid)
        self.assertEqual(parsed.dirname, src.dirname)
        self.assertEqual(parsed.names(), src.names())
        self.assertEqual(parsed.mode, PS2_MODE_DIR)
        self.assertEqual(parsed.get("DATA.BIN").mode, PS2_MODE_FILE)
        self.assertEqual(parsed.get("DATA.BIN").data, src.get("DATA.BIN").data)

    def test_psv_default_seed_and_signature(self):
        raw = write_psv_bytes(self.make_archive())
        self.assertEqual(raw[8:28], PSV_DEFAULT_SEED)
        self.assertTrue(verify_ps2_signature(raw))

    def test_psv_to_psu_roundtrip(self):
        src = self.make_archive()
        psv = write_psv_bytes(src, seed=b"\x22" * 20)
        parsed_psv = parse_psv_bytes(psv)
        psu = write_psu_bytes(parsed_psv)
        parsed_psu = parse_psu_bytes(psu)
        self.assertEqual(parsed_psu.dirname, src.dirname)
        self.assertEqual(parsed_psu.get("DATA.BIN").data, src.get("DATA.BIN").data)

    def test_signature_detects_tamper(self):
        raw = bytearray(write_psv_bytes(self.make_archive(), seed=b"\x33" * 20))
        raw[-1] ^= 0x01
        self.assertFalse(verify_ps2_signature(bytes(raw)))
        parsed = parse_psv_bytes(bytes(raw))
        self.assertFalse(parsed.signature_valid)

    def test_truncated_psu_is_rejected(self):
        raw = write_psu_bytes(self.make_archive())
        with self.assertRaises(SaveFormatError):
            parse_psu_bytes(raw[:-1000])


if __name__ == "__main__":
    unittest.main()
