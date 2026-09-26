"""Reprint radar (gallery/radar.py). Run with: python -m unittest tests.test_radar"""
import json
import sqlite3
import unittest
import uuid

from gallery import db
from gallery.collection import Collection
from gallery.radar import Radar, impact_of


def _card(name, oracle_id, type_line="Artifact", usd="10.00"):
    return {"id": str(uuid.uuid4()), "oracle_id": oracle_id, "name": name, "set": "old", "set_name": "Old",
            "collector_number": "1", "type_line": type_line, "mana_cost": "{1}", "cmc": 1, "colors": [],
            "color_identity": [], "legalities": {}, "rarity": "rare", "prices": {"usd": usd}, "layout": "normal"}


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(db.SCHEMA)
        db.set_meta(self.connection, "radar_as_of", "2026-09-26")
        self.cards = {}
        for name, type_line, usd in (("Sol Ring", "Artifact", "2.00"), ("Mikaeus", "Legendary Creature", "200.00"),
                                     ("Forest", "Basic Land — Forest", "0.10"), ("Old News", "Artifact", "5.00")):
            oracle = str(uuid.uuid4())
            card = _card(name, oracle, type_line, usd)
            self.cards[name] = card
            self.connection.execute("INSERT INTO cards VALUES (?, ?, ?, ?)", (card["id"], oracle, name, json.dumps(card)))
            self.connection.execute("INSERT INTO holdings (scryfall_id, name, finish, quantity, condition, language) "
                                    "VALUES (?, ?, 'normal', 2, 'near_mint', 'en')", (card["id"], name))

        def new(name, set_code, set_type, released, scryfall_id=None):
            self.connection.execute("INSERT INTO new_printings VALUES (?, ?, ?, ?, ?, ?, ?, '1', 'rare', NULL, '{}')",
                                    (scryfall_id or str(uuid.uuid4()), self.cards[name]["oracle_id"], name,
                                     set_code, set_code.upper(), set_type, released))

        new("Sol Ring", "fdc", "commander", "2026-10-02")
        new("Sol Ring", "sld", "box", "2026-09-01")          # also a recent one: the card is still "upcoming"
        new("Mikaeus", "plst", "masters", "2026-11-09")
        new("Forest", "fra", "expansion", "2026-10-02")
        new("Old News", "sld", "box", "2026-08-01")
        new("Old News", "old", "expansion", "2026-01-01", scryfall_id=self.cards["Old News"]["id"])   # your own printing
        self.radar = Radar(self.connection, Collection(self.connection))

    def report(self):
        return self.radar.report(lambda entry, quantity=None, row_id=True: {"name": entry.name, "quantity": quantity})

    def test_upcoming_and_recent(self):
        report = self.report()
        self.assertEqual([c["card"]["name"] for c in report["upcoming"]], ["Mikaeus", "Sol Ring"])  # by value held
        self.assertEqual([c["card"]["name"] for c in report["recent"]], ["Old News"])
        sol = next(c for c in report["upcoming"] if c["card"]["name"] == "Sol Ring")
        self.assertEqual((sol["days_until"], sol["impact"], [r["set_code"] for r in sol["reprints"]]), (6, "wide", ["fdc"]))

    def test_the_list_is_limited_and_basics_are_ignored(self):
        report = self.report()
        mikaeus = next(c for c in report["upcoming"] if c["card"]["name"] == "Mikaeus")
        self.assertEqual(mikaeus["impact"], "limited")
        self.assertNotIn("Forest", [c["card"]["name"] for c in report["upcoming"] + report["recent"]])
        self.assertEqual(impact_of("hoc", "eternal"), "wide")

    def test_headline_agrees_with_the_report(self):
        headline = self.radar.headline()
        self.assertEqual((headline["upcoming_cards"], headline["next_release"]), (2, "2026-10-02"))
        self.assertEqual(headline["upcoming_value_usd"], self.report()["totals"]["upcoming_value_usd"])


if __name__ == "__main__":
    unittest.main()
