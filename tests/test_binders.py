"""Binders (gallery/binders.py): filing copies, searching them, and surviving re-imports.

Run with: python -m unittest tests.test_binders
"""
import json
import sqlite3
import unittest
import uuid

from gallery import db, ingest
from gallery.binders import BinderBook
from gallery.collection import Collection
from gallery.query import compile_query


def _card(name, set_code, number, usd):
    oracle = "o-" + name
    return {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": name, "set": set_code, "set_name": "Test",
            "collector_number": number, "type_line": "Artifact", "mana_cost": "{1}", "cmc": 1, "colors": [],
            "color_identity": [], "legalities": {"commander": "legal"}, "rarity": "common", "finishes": ["nonfoil", "foil"],
            "prices": {"usd": usd, "usd_foil": usd}, "layout": "normal", "lang": "en"}


class BinderTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(db.SCHEMA)
        self.ring = _card("Sol Ring", "c21", "263", "1.00")
        self.bolt = _card("Lightning Bolt", "m10", "146", "2.00")
        for card in (self.ring, self.bolt):
            self.connection.execute("INSERT INTO cards VALUES (?, ?, ?, ?)", (card["id"], card["oracle_id"], card["name"], json.dumps(card)))
        self.import_rows([(self.ring, "normal", 3), (self.ring, "normal", 2, "played"), (self.bolt, "foil", 4)])

    def import_rows(self, rows, binder=None):
        holdings = []
        for card, finish, quantity, *condition in rows:
            row = {"scryfall_id": card["id"], "name": card["name"], "set_code": card["set"],
                   "collector_number": card["collector_number"], "finish": finish, "quantity": quantity,
                   "condition": condition[0] if condition else "near_mint", "language": "en"}
            if binder:
                row.update(binder=binder, binder_type="binder")
            holdings.append(row)
        ingest.store_holdings(self.connection, holdings, "x.csv")
        self.collection = Collection(self.connection)
        self.book = BinderBook(self.connection, self.collection)
        return holdings

    def find(self, text):
        query = compile_query(text)
        return sorted({(e.name, e.finish) for e in self.collection.entries if query.matches(e)})

    def test_filing_moving_and_taking_out(self):
        trade = self.book.create("Trade", "binder")
        box = self.book.create("Box 3", "box")
        # A printing split over two rows (two conditions) is one pile of 5.
        self.assertEqual(self.book.put(trade, [{"scryfall_id": self.ring["id"], "finish": "normal", "quantity": 4}]), 4)
        self.assertEqual(self.book.put(box, [{"scryfall_id": self.ring["id"], "finish": "normal", "quantity": None}]), 1)
        self.assertEqual(self.book.put(box, [{"scryfall_id": self.ring["id"], "finish": "normal", "quantity": 9}]), 0)
        self.assertEqual(self.book.put(box, [{"scryfall_id": self.ring["id"], "finish": "normal", "quantity": 2}], from_binder=trade), 2)
        self.assertEqual(self.book.summary(trade)["copies"], 2)
        self.assertEqual(self.book.summary(box)["copies"], 3)
        self.assertEqual(self.book.listing()["unsorted"]["copies"], 4)            # the foil Bolts
        self.book.take_out(box, [{"scryfall_id": self.ring["id"], "finish": "normal", "quantity": None}])
        self.assertEqual(self.book.listing()["unsorted"]["copies"], 7)
        with self.assertRaises(ValueError):
            self.book.create("trade")                                          # names are unique

    def test_search_terms(self):
        trade = self.book.create("Trade")
        self.book.put(trade, [{"scryfall_id": self.ring["id"], "finish": "normal", "quantity": 5}])
        self.assertEqual(self.find("binder:trade"), [("Sol Ring", "normal")])
        self.assertEqual(self.find("binder:any"), [("Sol Ring", "normal")])
        self.assertEqual(self.find("binder:none"), [("Lightning Bolt", "foil")])
        self.assertEqual(self.find("-binder:trade"), [("Lightning Bolt", "foil")])

    def test_binders_survive_a_reimport_and_deleting_one_unsorts_it(self):
        trade = self.book.create("Trade")
        self.book.put(trade, self.book.items_for(self.collection.entries))
        self.import_rows([(self.ring, "normal", 5), (self.bolt, "foil", 4)])   # rows replaced
        self.assertEqual(self.book.summary(trade)["copies"], 9)
        self.assertEqual(self.find("binder:none"), [])
        self.import_rows([(self.ring, "normal", 1), (self.bolt, "foil", 4)])   # sold four Sol Rings
        self.assertEqual(self.book.summary(trade)["short"], 4)
        self.book.delete(trade)
        self.assertEqual(self.book.listing()["unsorted"]["copies"], 5)

    def test_a_manabox_export_with_binders_fills_them(self):
        rows = self.import_rows([(self.ring, "normal", 2), (self.bolt, "foil", 1)], binder="Deck box")
        filled = BinderBook(self.connection).from_import(rows)
        self.assertEqual(filled, ["Deck box"])
        self.book.reload()
        deck_box = next(b for b, binder in self.book.binders.items() if binder["name"] == "Deck box")
        self.assertEqual(self.book.summary(deck_box)["copies"], 3)
        BinderBook(self.connection).from_import(rows[:1])                      # a later export: exactly the file
        self.book.reload()
        self.assertEqual(self.book.summary(deck_box)["copies"], 2)


if __name__ == "__main__":
    unittest.main()
