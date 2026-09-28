"""The Comprehensive Rules reader and card rulings (gallery/rules.py).

Run with: python -m unittest tests.test_rules
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gallery import db, rules

TEXT = """Magic: The Gathering Comprehensive Rules

These rules are effective as of September 25, 2026.

Contents

1. Game Concepts
100. General

7. Additional Rules
702. Keyword Abilities

Glossary

Credits

1. Game Concepts

100. General

100.1. These Magic rules apply to any Magic game with two or more players.

100.1a A two-player game is a game that begins with only two players.

7. Additional Rules

702. Keyword Abilities

702.2. Deathtouch

702.2b A creature with toughness greater than 0 that's been dealt damage by a source with deathtouch is destroyed. See rule 704.
Example: A 1/1 with deathtouch blocks a 10/10.

Glossary

Deathtouch
A keyword ability that causes damage dealt by an object to be especially effective. See rule 702.2, "Deathtouch."

Trample
A keyword ability that modifies how a creature assigns combat damage.

Credits

Magic: The Gathering Original Game Design: Richard Garfield
"""


class RulesTests(unittest.TestCase):
    def test_parse(self):
        data = rules.parse(TEXT)
        self.assertEqual(data["effective"], "September 25, 2026")
        self.assertEqual([c["title"] for c in data["chapters"]], ["Game Concepts", "Additional Rules"])
        self.assertEqual([r["number"] for r in data["rules"]], ["100.1", "100.1a", "702.2", "702.2b"])
        self.assertEqual(data["rules"][3]["examples"], ["A 1/1 with deathtouch blocks a 10/10."])
        self.assertEqual([g["term"] for g in data["glossary"]], ["Deathtouch", "Trample"])

    def test_book_sections_and_search(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "MagicCompRules-20260925.txt").write_text(TEXT, encoding="utf-8")
            book = rules.RuleBook(folder)
            self.assertEqual(book.overview()["effective"], "September 25, 2026")
            self.assertEqual([r["number"] for r in book.section("702")["rules"]], ["702.2", "702.2b"])
            self.assertEqual([r["number"] for r in book.search("702.2")["rules"]], ["702.2", "702.2b"])
            found = book.search("deathtouch")
            self.assertEqual([g["term"] for g in found["glossary"]], ["Deathtouch"])
            self.assertEqual([r["number"] for r in found["rules"]], ["702.2", "702.2b"])
            with self.assertRaises(KeyError):
                book.section("999")
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(rules.RuleBook(empty).overview(), {"downloaded": False})

    def test_rulings_are_fetched_once_then_cached(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(db.SCHEMA)
        answer = {"data": [{"published_at": "2023-06-16", "source": "wotc", "comment": "Protection from everything."},
                           {"published_at": "2024-01-01", "source": "scryfall", "comment": "A note."}]}
        with mock.patch("gallery.scryfall.api_get", return_value=answer) as fetch:
            first = rules.rulings(connection, "sid", "oid")
            second = rules.rulings(connection, "sid", "oid")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([r["date"] for r in first], ["2024-01-01", "2023-06-16"])      # newest first
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
