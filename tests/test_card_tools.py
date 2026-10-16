from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from card_tools import archive_digest, archive_hashes, compare_archives, export_archive_folder, restore_backup, timestamped_backup
from formats.common import SaveArchive, SaveEntry


class CardToolTests(unittest.TestCase):
    def _archive(self, payload: bytes = b"data") -> SaveArchive:
        return SaveArchive(
            dirname="BASLUS-TEST",
            entries=[SaveEntry("icon.sys", b"icon"), SaveEntry("SAVE.DAT", payload)],
        )

    def test_hashes_and_compare(self):
        a = self._archive(b"one")
        b = self._archive(b"two")
        self.assertNotEqual(archive_digest(a), archive_digest(b))
        hashes = archive_hashes(a)
        self.assertIn("SAVE.DAT", hashes)
        diffs = compare_archives(a, b)
        statuses = {d.name: d.status for d in diffs}
        self.assertEqual(statuses["icon.sys"], "Identical")
        self.assertEqual(statuses["SAVE.DAT"], "Changed")

    def test_backup_restore_and_folder_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            card = root / "card.ps2"
            card.write_bytes(b"before")
            backup = timestamped_backup(card)
            self.assertTrue(backup.is_file())
            card.write_bytes(b"after")
            restore_backup(backup, card)
            self.assertEqual(card.read_bytes(), b"before")

            out = export_archive_folder(self._archive(), root / "export")
            self.assertEqual((out / "SAVE.DAT").read_bytes(), b"data")


if __name__ == "__main__":
    unittest.main()
