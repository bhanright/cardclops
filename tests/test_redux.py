"""The Redux format (gallery/redux.py), which Cardclops works out itself.

Run with: python -m unittest tests.test_redux
"""
import sqlite3
import unittest

from gallery import db, redux
from gallery.deckstats import compute_deck_stats
from tests.test_deckstats import TAGS, line, make_card


class StatusTests(unittest.TestCase):
    def test_the_lists_and_the_sets(self):
        self.assertEqual(redux.status_for("Black Lotus", True), "banned")
        self.assertEqual(redux.status_for("Contract from Below", True), "banned")       # ante
        self.assertEqual(redux.status_for("Sol Ring", True), "restricted")
        self.assertEqual(redux.status_for("Lightning Bolt", True), "legal")
        self.assertEqual(redux.status_for("Brainstorm", False), "legal")                # added for Ancestral Recall
        self.assertEqual(redux.status_for("Hallowed Fountain", False), "legal")         # a shockland
        self.assertEqual(redux.status_for("Counterspell", False), "not_legal")          # only if printed in the sets


class LoadTests(unittest.TestCase):
    def test_statuses_come_from_every_printing_in_the_catalog(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(db.CATALOG_SCHEMA.replace("{schema}", "main"))
        cards = [("o-bolt", "Lightning Bolt", "lea"), ("o-bolt", "Lightning Bolt", "m10"), ("o-ring", "Sol Ring", "2ed"),
                 ("o-storm", "Brainstorm", "ice"), ("o-goyf", "Tarmogoyf", "fut")]
        for number, (oracle_id, name, set_code) in enumerate(cards):
            connection.execute("INSERT INTO set_cards (scryfall_id, set_code, collector_number, name, oracle_id) "
                               "VALUES (?, ?, ?, ?, ?)", (f"s{number}", set_code, str(number), name, oracle_id))
        for oracle_id, name, _ in cards:
            connection.execute("INSERT OR IGNORE INTO oracle_cards (oracle_id, name, name_folded) VALUES (?, ?, ?)",
                               (oracle_id, name, name.lower()))
        redux.load(connection)
        status = {card["name"]: redux.annotate(card)["legalities"]["redux"] for card in (
            {"oracle_id": "o-bolt", "name": "Lightning Bolt"}, {"oracle_id": "o-ring", "name": "Sol Ring"},
            {"oracle_id": "o-storm", "name": "Brainstorm"}, {"oracle_id": "o-goyf", "name": "Tarmogoyf"})}
        self.assertEqual(status, {"Lightning Bolt": "legal", "Sol Ring": "restricted", "Brainstorm": "legal",
                                  "Tarmogoyf": "not_legal"})


class DeckTests(unittest.TestCase):
    def deck(self, *lines):
        return compute_deck_stats(list(lines), "redux", TAGS)["legality"]

    def test_a_dual_land_and_its_shockland_share_four(self):
        tundra = make_card("Tundra", "Land — Plains Island", legal=("redux",))
        fountain = make_card("Hallowed Fountain", "Land — Plains Island", legal=("redux",))
        filler = make_card("Plains", "Basic Land — Plains", legal=("redux",))
        fine = self.deck(line(tundra, 2), line(fountain, 2), line(filler, 56))
        self.assertTrue(fine["legal"], fine["problems"])
        over = self.deck(line(tundra, 3), line(fountain, 2), line(filler, 55))
        self.assertIn("Tundra and Hallowed Fountain", [p["name"] for p in over["problems"]])

    def test_restricted_banned_and_deck_size(self):
        ring = make_card("Sol Ring", "Artifact", "{1}", 1, legal=())
        ring["legalities"]["redux"] = "restricted"
        lotus = make_card("Black Lotus", "Artifact", "{0}", 0, legal=())
        lotus["legalities"]["redux"] = "banned"
        filler = make_card("Plains", "Basic Land — Plains", legal=("redux",))
        result = self.deck(line(ring, 2), line(lotus, 1), line(filler, 50))
        reasons = {p["name"]: p["reason"] for p in result["problems"]}
        self.assertIn("restricted", reasons["Sol Ring"])
        self.assertEqual(reasons["Black Lotus"], "banned")
        self.assertIn("at least 60", reasons[None])


if __name__ == "__main__":
    unittest.main()
