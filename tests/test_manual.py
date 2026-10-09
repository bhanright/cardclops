"""Cards added by hand, and how imports treat them (gallery/manual.py, ingest.store_holdings).

Run with: python -m unittest tests.test_manual
"""
import json
import sqlite3
import unittest

from gallery import db, ingest, manual


def card(scryfall_id, name, finishes=("nonfoil", "foil")):
    return {"id": scryfall_id, "oracle_id": "o-" + scryfall_id, "name": name, "set": "tst", "set_name": "Test",
            "collector_number": "7", "rarity": "rare", "finishes": list(finishes), "lang": "en",
            "prices": {"usd": "2.00", "usd_foil": "5.00"}, "type_line": "Creature — Spider", "layout": "normal"}


def imported(scryfall_id, finish="normal", quantity=1, name="Imported"):
    return {"scryfall_id": scryfall_id, "name": name, "finish": finish, "quantity": quantity,
            "condition": "near_mint", "language": "en"}


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(db.SCHEMA)
        for c in (card("spider", "Giant Spider"), card("ring", "Sol Ring", ("nonfoil",))):
            self.connection.execute("INSERT INTO cards VALUES (?, ?, ?, ?)", (c["id"], c["oracle_id"], c["name"], json.dumps(c)))

    def rows(self):
        return [(r["scryfall_id"], r["finish"], r["quantity"], r["source"]) for r in
                self.connection.execute("SELECT * FROM holdings ORDER BY source, scryfall_id, finish")]

    def test_add_edit_and_remove(self):
        row = manual.add(self.connection, {"scryfall_id": "spider", "finish": "foil", "quantity": 2})
        self.assertEqual((row["name"], row["finish"], row["quantity"], row["price_usd"]), ("Giant Spider", "foil", 2, 5.0))
        self.assertEqual(manual.update(self.connection, row["row_id"], {"quantity": 3})["quantity"], 3)
        self.assertEqual(len(manual.rows(self.connection)), 1)
        manual.delete(self.connection, row["row_id"])
        self.assertEqual(manual.rows(self.connection), [])

    def test_checks(self):
        with self.assertRaises(ValueError):
            manual.add(self.connection, {"scryfall_id": "ring", "finish": "foil"})      # never printed in foil
        with self.assertRaises(ValueError):
            manual.add(self.connection, {"scryfall_id": "ring", "quantity": 0})
        with self.assertRaises(ValueError):
            manual.add(self.connection, {"scryfall_id": "ring", "condition": "mint-ish"})

    def test_imports_keep_hand_added_cards(self):
        manual.add(self.connection, {"scryfall_id": "spider"})
        ingest.store_holdings(self.connection, [imported("ring")], "export.csv")
        ingest.store_holdings(self.connection, [imported("ring", quantity=2)], "export.csv")
        self.assertEqual(self.rows(), [("ring", "normal", 2, "import"), ("spider", "normal", 1, "manual")])

    def test_an_import_takes_over_from_the_same_printing_added_by_hand(self):
        manual.add(self.connection, {"scryfall_id": "spider", "quantity": 3})
        manual.add(self.connection, {"scryfall_id": "spider", "finish": "foil"})
        reconciled = ingest.store_holdings(self.connection, [imported("spider", quantity=2, name="Giant Spider")], "x.csv")
        self.assertEqual(reconciled, [{"name": "Giant Spider", "finish": "normal", "removed": 2}])
        self.assertEqual(self.rows(), [("spider", "normal", 2, "import"), ("spider", "foil", 1, "manual"),
                                       ("spider", "normal", 1, "manual")])
        ingest.store_holdings(self.connection, [imported("spider", quantity=3)], "x.csv")
        self.assertEqual(self.rows(), [("spider", "normal", 3, "import"), ("spider", "foil", 1, "manual")])

    def test_importing_the_same_file_again_changes_nothing(self):
        manual.add(self.connection, {"scryfall_id": "spider", "quantity": 5})
        for _ in range(3):                                   # the audit's case: 5 by hand, 2 imported, thrice
            ingest.store_holdings(self.connection, [imported("spider", quantity=2)], "x.csv")
        self.assertEqual(self.rows(), [("spider", "normal", 2, "import"), ("spider", "normal", 3, "manual")])
        # Copies added by hand after an import aren't taken over by the same import again...
        manual.add(self.connection, {"scryfall_id": "spider", "finish": "foil"})
        ingest.store_holdings(self.connection, [imported("spider", quantity=2)], "x.csv")
        self.assertIn(("spider", "foil", 1, "manual"), self.rows())
        # ...but an import that grows does take over the difference.
        ingest.store_holdings(self.connection, [imported("spider", quantity=4), imported("spider", "foil")], "x.csv")
        self.assertEqual(self.rows(), [("spider", "foil", 1, "import"), ("spider", "normal", 4, "import"),
                                       ("spider", "normal", 1, "manual")])

    def test_adding_a_binder_keeps_the_rest_of_the_collection(self):
        ingest.store_holdings(self.connection, [imported("ring")], "collection.csv")
        trade = [dict(imported("spider", quantity=2), binder="Trade")]
        ingest.store_holdings(self.connection, trade, "trade.csv", mode="add")
        self.assertEqual(self.rows(), [("spider", "normal", 2, "added:Trade"), ("ring", "normal", 1, "import")])
        # The same binder again updates it rather than counting it twice; other binders stay.
        ingest.store_holdings(self.connection, [dict(imported("spider", quantity=3), binder="Trade")], "trade.csv", mode="add")
        ingest.store_holdings(self.connection, [dict(imported("ring"), binder="Deck box")], "box.csv", mode="add")
        self.assertEqual(self.rows(), [("ring", "normal", 1, "added:Deck box"), ("spider", "normal", 3, "added:Trade"),
                                       ("ring", "normal", 1, "import")])
        # A file without binder names is filed under its name.
        ingest.store_holdings(self.connection, [imported("spider", "foil")], "loose.csv", mode="add")
        self.assertIn(("spider", "foil", 1, "added:loose.csv"), self.rows())
        # A whole-collection import replaces the added copies too: it already has them.
        ingest.store_holdings(self.connection, [imported("ring", quantity=2)], "collection.csv")
        self.assertEqual(self.rows(), [("ring", "normal", 2, "import")])

    def test_adding_a_binder_the_whole_collection_already_had_replaces_it(self):
        ingest.store_holdings(self.connection, [dict(imported("spider", quantity=2), binder="Trade"),
                                                dict(imported("ring"), binder="Deck box")], "collection.csv")
        # The Trade binder, exported again after a change: 3 spiders now, not 2 + 3.
        ingest.store_holdings(self.connection, [dict(imported("spider", quantity=3), binder="Trade")], "trade.csv", mode="add")
        self.assertEqual(self.rows(), [("spider", "normal", 3, "added:Trade"), ("ring", "normal", 1, "import")])

    def test_an_added_binder_takes_over_cards_added_by_hand(self):
        manual.add(self.connection, {"scryfall_id": "spider", "quantity": 2})
        reconciled = ingest.store_holdings(self.connection, [dict(imported("spider", name="Giant Spider"), binder="Trade")],
                                           "trade.csv", mode="add")
        self.assertEqual(reconciled, [{"name": "Giant Spider", "finish": "normal", "removed": 1}])
        ingest.store_holdings(self.connection, [dict(imported("spider"), binder="Trade")], "trade.csv", mode="add")
        self.assertEqual(self.rows(), [("spider", "normal", 1, "added:Trade"), ("spider", "normal", 1, "manual")])

    def test_imported_rows_cannot_be_edited_here(self):
        ingest.store_holdings(self.connection, [imported("ring")], "x.csv")
        row_id = self.connection.execute("SELECT row_id FROM holdings").fetchone()[0]
        with self.assertRaises(KeyError):
            manual.update(self.connection, row_id, {"quantity": 5})
        with self.assertRaises(KeyError):
            manual.delete(self.connection, row_id)


if __name__ == "__main__":
    unittest.main()
