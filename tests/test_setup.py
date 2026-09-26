"""First-run setup status (gallery/setup.py). Run with: python -m unittest tests.test_setup"""
import sqlite3
import threading
import unittest
from types import SimpleNamespace

from gallery import db
from gallery.setup import Jobs


class SetupStatusTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.executescript(db.SCHEMA)
        self.connection = connection
        self.jobs = Jobs(SimpleNamespace(connection=connection, lock=threading.Lock()))

    def add_data(self):
        self.connection.execute("INSERT INTO printings VALUES ('c21', '263', 'x', 'o', 'Sol Ring')")
        self.connection.execute("INSERT INTO holdings (scryfall_id, name, finish, quantity) VALUES ('x', 'Sol Ring', 'normal', 1)")

    def test_a_new_install_needs_setup(self):
        self.assertTrue(self.jobs.status()["needs_setup"])

    def test_an_install_from_before_the_wizard_is_complete(self):
        self.add_data()
        self.assertFalse(self.jobs.status()["needs_setup"])

    def test_a_wizard_in_progress_stays_open_after_the_import(self):
        db.set_meta(self.connection, "setup_started", "2026-09-26T16:00:00")
        self.add_data()
        self.assertTrue(self.jobs.status()["needs_setup"])
        self.jobs.complete()
        self.assertFalse(self.jobs.status()["needs_setup"])


if __name__ == "__main__":
    unittest.main()
