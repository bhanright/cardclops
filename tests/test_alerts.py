"""Price alerts (gallery/alerts.py). Run with: python -m unittest tests.test_alerts"""
import json
import sqlite3
import unittest
import uuid
from types import SimpleNamespace

from gallery import db
from gallery.alerts import Alerts
from gallery.collection import Collection


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(db.SCHEMA)
        self.oracle = str(uuid.uuid4())
        self.card = {"id": str(uuid.uuid4()), "oracle_id": self.oracle, "name": "Sol Ring", "set": "c21",
                     "collector_number": "263", "type_line": "Artifact", "prices": {"usd": "2.00"}, "layout": "normal"}
        self.connection.execute("INSERT INTO cards VALUES (?, ?, 'Sol Ring', ?)",
                                (self.card["id"], self.oracle, json.dumps(self.card)))
        self.connection.execute("INSERT INTO holdings (scryfall_id, name, finish, quantity, condition, language) "
                                "VALUES (?, 'Sol Ring', 'normal', 10, 'near_mint', 'en')", (self.card["id"],))
        self.collection = Collection(self.connection)
        self.alerts = Alerts(self.connection)
        self.alerts.update_settings({"move_alerts": True, "reprint_alerts": True, "legality_alerts": True})
        self.prices = SimpleNamespace(changes={})
        self.reprints = []
        self.radar = SimpleNamespace(_cards=lambda min_value: (self.reprints, "2026-09-26"))

    def price(self, value):
        db.merge_price_points(self.connection, self.card["id"], "scryfall", "normal", "USD", {"2026-09-26": value})

    def check(self):
        return self.alerts.check(self.collection, self.prices, self.radar)

    def test_a_new_install_sends_nothing_it_was_not_asked_for(self):
        fresh = Alerts(self.connection)
        self.connection.execute("DELETE FROM meta WHERE key = 'alert_settings'")
        settings = fresh.settings()
        self.assertFalse(any(settings[k] for k in ("move_alerts", "reprint_alerts", "legality_alerts",
                                                   "windows_notifications")))
        self.prices.changes = {(self.card["id"], "normal"): {7: (1.0, 5.0, 400.0)}}
        self.reprints = []
        self.assertEqual(fresh.check(self.collection, self.prices, self.radar), [])
        self.assertFalse(fresh.notify([{"message": "x"}]))

    def test_target_fires_once_per_crossing(self):
        self.alerts.add({"scryfall_id": self.card["id"], "target_usd": 1.5, "direction": "below"}, self.collection)
        self.price(2.0)
        self.assertEqual(self.check(), [])
        self.price(1.4)
        self.assertEqual([a["kind"] for a in self.check()], ["target"])
        self.connection.execute("DELETE FROM alerts")           # a new day would allow it; being disarmed must not
        self.assertEqual(self.check(), [])
        self.price(1.8)
        self.check()                                            # back above the target: re-armed
        self.price(1.2)
        self.connection.execute("DELETE FROM alerts")
        self.assertEqual([a["kind"] for a in self.check()], ["target"])

    def test_moves_need_size_and_value(self):
        self.prices.changes = {(self.card["id"], "normal"): {7: (1.0, 1.1, 10.0)}}
        self.assertEqual(self.check(), [])                      # 10% is under the 20% default
        self.prices.changes = {(self.card["id"], "normal"): {7: (1.0, 1.5, 50.0)}}
        self.assertEqual([a["kind"] for a in self.check()], ["move"])
        self.assertEqual(self.check(), [])                      # once per card per day

    def test_first_reprint_check_only_records_what_is_known(self):
        entry = self.collection.entries[0]

        def reprint(set_code):
            return {"entry": entry, "held_value_usd": 20.0, "days_until": 5,
                    "reprints": [{"set_code": set_code, "set_name": set_code.upper(), "released_at": "2026-10-01",
                                  "impact": "wide"}]}
        self.reprints = [reprint("fdc")]
        self.assertEqual(self.check(), [])                      # already announced before the first check
        self.reprints = [reprint("fdc"), reprint("trk")]
        self.assertEqual([a["message"].split(" is ")[0] for a in self.check()], ["Sol Ring"])
        self.assertEqual(self.check(), [])


if __name__ == "__main__":
    unittest.main()
