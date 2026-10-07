"""Commander Spellbook combos (gallery/combos.py) and the bracket estimate (gallery/deckstats.py).

    python -m unittest tests.test_combos
"""
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from gallery import combos, db, deckstats
from gallery.deckstats import compute_deck_stats
from tests.test_deckstats import TAGS, line, make_card


def variant(spellbook_id, cards, tag="S", results=("Infinite mana",), identity="U", popularity=10,
            commander=(), requires=(), legal=True, status="OK"):
    return {"id": spellbook_id, "status": status, "bracketTag": tag, "identity": identity, "popularity": popularity,
            "manaValueNeeded": 0, "spoiler": False, "requires": list(requires),
            "legalities": {"commander": legal},
            "uses": [{"card": {"oracleId": card["oracle_id"], "name": card["name"]},
                      "mustBeCommander": card in commander} for card in cards],
            "produces": [{"feature": {"name": name}} for name in results]}


ORACLE = make_card("Thassa's Oracle", "Creature", "{U}{U}", 2, ["U"])
CONSULT = make_card("Demonic Consultation", "Instant", "{B}", 1, ["B"])
PACT = make_card("Tainted Pact", "Instant", "{1}{B}", 2, ["B"])
REVERSAL = make_card("Dramatic Reversal", "Instant", "{1}{U}", 2, ["U"])
SCEPTER = make_card("Isochron Scepter", "Artifact", "{2}", 2)
HELIOD = make_card("Heliod, Sun-Crowned", "Legendary Creature", "{2}{W}", 3, ["W"])
BALLISTA = make_card("Walking Ballista", "Artifact Creature", "{X}{X}", 0)
LEADER = make_card("Some Commander", "Legendary Creature", "{U}{B}", 2, ["U", "B"])
ENGINE = make_card("Engine", "Artifact", "{3}", 3)

VARIANTS = [
    variant("1-2", [ORACLE, CONSULT], tag="R", results=("Exile your library", "Win the game"), identity="UB",
            popularity=500),
    variant("1-3", [ORACLE, PACT], tag="R", results=("Win the game",), identity="UB", popularity=400),
    variant("4-5", [REVERSAL, SCEPTER], tag="S", identity="U", popularity=300),
    variant("6-7", [HELIOD, BALLISTA], tag="S", identity="W", popularity=200),          # outside the colors
    variant("8-9", [LEADER, ENGINE], tag="P", identity="UB", commander=[LEADER]),
    variant("10-11", [ORACLE, SCEPTER], tag="E", requires=[{"template": "a sac outlet"}]),  # left out
    variant("12-13", [ORACLE, REVERSAL], tag="B"),                                        # banned: left out
]


def published(variants=VARIANTS):
    data = combos.slim(variants, "2026-10-07T00:00:00")
    data["built_at"] = "202610070000"
    return gzip.compress(json.dumps(data).encode())


class ComboTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(db.CARD_CACHE_SCHEMA.replace("{schema}", "main"))
        combos.install(self.connection, published())
        self.book = combos.ComboBook(self.connection)

    def find(self, cards, commanders=(), identity="UB", owned=()):
        return self.book.find({c["oracle_id"] for c in cards}, {c["oracle_id"] for c in commanders}, identity,
                              owned=lambda oracle_id: oracle_id in {c["oracle_id"] for c in owned})

    def test_slim_keeps_only_checkable_legal_combos(self):
        data = json.loads(gzip.decompress(published()))
        self.assertEqual([c[0] for c in data["combos"]], ["1-2", "1-3", "4-5", "6-7", "8-9"])
        self.assertEqual(data["results"][data["combos"][0][8][0]], "Win the game")      # game-ending first

    def test_included_and_near_misses(self):
        found = self.find([ORACLE, CONSULT, REVERSAL], owned=[SCEPTER])
        self.assertEqual([c["cards"] for c in found["included"]], [["Thassa's Oracle", "Demonic Consultation"]])
        self.assertTrue(found["included"][0]["game_ending"])
        self.assertEqual({c["missing"]: c["missing_owned"] for c in found["near_misses"]},
                         {"Tainted Pact": False, "Isochron Scepter": True})

    def test_near_misses_stay_in_the_colors(self):
        self.assertEqual(self.find([BALLISTA])["near_misses"], [])
        self.assertEqual(len(self.find([BALLISTA], identity="WUB")["near_misses"]), 1)

    def test_a_piece_that_must_lead_counts_only_as_commander(self):
        self.assertEqual(self.find([LEADER, ENGINE])["included"], [])
        self.assertEqual(len(self.find([LEADER, ENGINE], commanders=[LEADER])["included"]), 1)
        self.assertEqual(self.find([ENGINE])["near_misses"], [])        # you can't just add your commander

    def test_no_data_means_none(self):
        empty = sqlite3.connect(":memory:")
        empty.executescript(db.CARD_CACHE_SCHEMA.replace("{schema}", "main"))
        self.assertIsNone(combos.ComboBook(empty).find({"x"}, set(), "U"))

    def test_wanted_only_when_newer(self):
        entry = {"file": "combos-x.json.gz", "built_at": "202610070000", "format": combos.COMBO_FORMAT}
        self.assertIsNone(combos.wanted(self.connection, {"combos": entry}))
        self.assertIsNotNone(combos.wanted(self.connection, {"combos": {**entry, "built_at": "202610140000"}}))
        self.assertIsNone(combos.wanted(self.connection, {}))

    def test_read_variants_streams_the_bulk_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "variants.json"
            path.write_text(json.dumps({"timestamp": "t", "version": "7", "variants": VARIANTS * 300}), encoding="utf-8")
            self.assertEqual(sum(1 for _ in combos.read_variants(path)), len(VARIANTS) * 300)
            self.assertEqual(combos.bulk_timestamp(path), "t")

    def test_game_ending_results(self):
        self.assertTrue(combos.is_game_ending("Each opponent loses the game"))
        self.assertTrue(combos.is_game_ending("Infinite damage"))
        self.assertFalse(combos.is_game_ending("Near-infinite damage"))
        self.assertFalse(combos.is_game_ending("You can't lose the game due to having 0 or less life"))


class BracketTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(db.CARD_CACHE_SCHEMA.replace("{schema}", "main"))
        combos.install(connection, published())
        self.book = combos.ComboBook(connection)

    def stats(self, cards, book=True):
        deck = [line(LEADER, section="commander")] + [line(card) for card in cards]
        return compute_deck_stats(deck, "commander", TAGS, combo_book=self.book if book else None)

    def test_plain_deck_is_core(self):
        bracket = self.stats([make_card("Bear", "Creature", "{1}{G}", 2, ["G"])])["bracket"]
        self.assertEqual((bracket["bracket"], bracket["name"], bracket["reasons"]), (2, "Core", []))

    def test_game_changers(self):
        changers = [make_card(f"Changer {n}", "Artifact", "{1}", 1, game_changer=True) for n in range(4)]
        self.assertEqual(self.stats(changers[:3])["bracket"]["bracket"], 3)
        self.assertEqual(self.stats(changers)["bracket"]["bracket"], 4)

    def test_combos_set_the_bracket(self):
        stats = self.stats([REVERSAL, SCEPTER])
        self.assertEqual(stats["bracket"]["bracket"], 3)
        self.assertEqual(stats["combos"]["counts"], {"combos": 1, "early": 0, "two_card": 1, "three_plus": 0,
                                                     "game_ending": 0})
        stats = self.stats([ORACLE, CONSULT, REVERSAL, SCEPTER])
        self.assertEqual(stats["bracket"]["bracket"], 4)
        self.assertEqual([r["title"] for r in stats["bracket"]["reasons"]], ["1 early combo", "1 late-game combo"])

    def test_without_combo_data(self):
        stats = self.stats([ORACLE, CONSULT], book=False)
        self.assertEqual(stats["bracket"]["bracket"], 2)
        self.assertFalse(stats["bracket"]["combos_checked"])
        self.assertIsNone(stats["combos"])

    def test_only_commander_gets_a_bracket(self):
        stats = compute_deck_stats([line(ORACLE, 4)], "modern", TAGS, combo_book=self.book)
        self.assertIsNone(stats["bracket"])
        self.assertIsNone(stats["combos"])

    def test_bracket_names(self):
        self.assertEqual(deckstats.BRACKET_NAMES[3], "Upgraded")


if __name__ == "__main__":
    unittest.main()
