"""Data folders from before the rename move to Cardclops's (gallery/paths.py).

Run with: python -m unittest tests.test_paths
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gallery import paths


class RenamedFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.old_user, self.new_user = self.temp / "Collection Gallery", self.temp / "Cardclops"
        self.old_cache, self.new_cache = self.temp / "cache" / "CollectionGallery", self.temp / "cache" / "Cardclops"
        (self.old_user / "Backups").mkdir(parents=True)
        (self.old_user / "gallery.sqlite").write_text("db")
        (self.old_user / "gallery.sqlite-wal").write_text("wal")
        (self.old_user / "Backups" / "gallery-2026-09-26.sqlite").write_text("backup")
        self.old_cache.mkdir(parents=True)
        (self.old_cache / "cards.sqlite").write_text("cache")

    def move(self, **environment):
        patches = {"LEGACY_USER_DIR": self.old_user, "USER_DIR": self.new_user,
                   "LEGACY_CACHE_DIR": self.old_cache, "CACHE_DIR": self.new_cache}
        with mock.patch.multiple(paths, **patches), mock.patch.dict(os.environ, environment):
            os.environ.pop("CARDCLOPS_HOME", None) if "CARDCLOPS_HOME" not in environment else None
            os.environ.pop("CARDCLOPS_CACHE", None) if "CARDCLOPS_CACHE" not in environment else None
            paths._move_renamed_folders()

    def test_folders_database_and_backups_are_renamed(self):
        self.move()
        self.assertFalse(self.old_user.exists())
        self.assertEqual((self.new_user / "cardclops.sqlite").read_text(), "db")
        self.assertEqual((self.new_user / "cardclops.sqlite-wal").read_text(), "wal")
        self.assertTrue((self.new_user / "Backups" / "cardclops-2026-09-26.sqlite").exists())
        self.assertEqual((self.new_cache / "cards.sqlite").read_text(), "cache")

    def test_an_existing_new_folder_is_never_overwritten(self):
        self.new_user.mkdir()
        self.move()
        self.assertTrue((self.old_user / "gallery.sqlite").exists())
        self.assertFalse((self.new_user / "cardclops.sqlite").exists())

    def test_a_chosen_location_is_left_alone(self):
        self.move(CARDCLOPS_HOME=str(self.temp / "elsewhere"))
        self.assertTrue(self.old_user.exists())


if __name__ == "__main__":
    unittest.main()
