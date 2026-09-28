"""Backup and restore of your own database (gallery/backups.py).

Run with: python -m unittest tests.test_backups
"""
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gallery import backups, db
from gallery.api import Api


def make_user(folder, name, copies):
    connection = db.connect(folder / f"{name}.sqlite", folder / f"{name}-cards.sqlite")
    connection.execute("INSERT INTO holdings (scryfall_id, name, finish, quantity) VALUES ('s1', 'Sol Ring', 'normal', ?)",
                       (copies,))
    connection.execute("INSERT INTO decks (name, format, created_at, updated_at) VALUES ('Rings', 'commander', 'x', 'x')")
    connection.commit()
    return connection


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_a_backup_restores_into_another_database_and_leaves_its_card_data(self):
        mine = make_user(self.folder, "mine", 3)
        data, filename = backups.snapshot(mine)
        self.assertTrue(filename.startswith("cardclops-backup-"))
        self.assertEqual(backups.describe(data)["copies"], 3)

        other = make_user(self.folder, "other", 9)
        other.execute("INSERT INTO cards (scryfall_id, oracle_id, name, raw) VALUES ('s1', 'o1', 'Sol Ring', '{}')")
        other.commit()
        found = backups.restore(other, data)
        self.assertEqual((found["copies"], found["decks"]), (3, 1))
        self.assertEqual(other.execute("SELECT quantity FROM holdings").fetchone()[0], 3)
        self.assertEqual(other.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 1)     # the cache is untouched

    def test_a_backup_from_an_older_version_gains_the_newer_tables(self):
        old = sqlite3.connect(self.folder / "old.sqlite")
        old.executescript("CREATE TABLE holdings (row_id INTEGER PRIMARY KEY, scryfall_id TEXT, name TEXT, "
                          "finish TEXT, quantity INTEGER); CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
        old.close()
        target = make_user(self.folder, "target", 1)
        backups.restore(target, (self.folder / "old.sqlite").read_bytes())
        tables = {row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertIn("binders", tables)
        self.assertIn("source", {row[1] for row in target.execute("PRAGMA table_info(holdings)")})

    def test_other_files_are_refused(self):
        with self.assertRaises(ValueError):
            backups.describe(b"name,quantity\nSol Ring,1\n")
        other = sqlite3.connect(self.folder / "other.sqlite")
        other.execute("CREATE TABLE t (x)")
        other.commit()
        other.close()
        with self.assertRaises(ValueError):
            backups.describe((self.folder / "other.sqlite").read_bytes())

    def test_the_reminder_is_for_the_browser_with_data_and_no_recent_backup(self):
        mine = make_user(self.folder, "mine", 1)
        self.assertFalse(backups.status(mine)["due"])                        # the apps keep their own copies
        with mock.patch("gallery.backups.IN_BROWSER", True):
            self.assertTrue(backups.status(mine)["due"])
            backups.snapshot(mine)
            self.assertFalse(backups.status(mine)["due"])


class RestoreRouteTests(unittest.TestCase):
    def test_check_then_restore_through_the_api(self):
        folder = Path(tempfile.mkdtemp())
        source = make_user(folder, "source", 4)
        data = base64.b64encode(backups.snapshot(source)[0]).decode()
        target = make_user(folder, "target", 1)
        gallery = mock.Mock(connection=target, lock=mock.MagicMock())
        api = Api(gallery)
        self.assertEqual(api.handle("POST", "/api/backup/check", body={"data": data}).body["copies"], 4)
        result = api.handle("POST", "/api/backup/restore", body={"data": data}).body
        self.assertEqual(result["copies"], 4)
        gallery.load.assert_called_once()
        gallery.jobs.refresh.assert_called_once()          # s1 has no card details here yet
        self.assertEqual(api.handle("POST", "/api/backup/restore", body={"data": "***"}).status, 400)


if __name__ == "__main__":
    unittest.main()
