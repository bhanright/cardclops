"""Searching the deck list (gallery/decksearch.py).

Run with: python -m unittest tests.test_decksearch
"""
import unittest

from gallery.decksearch import search


def deck(deck_id, name, colors, commanders=(), fmt="commander", status="active", legal=True, missing=0,
         elsewhere=0, value=100.0, cards=100):
    return {"deck_id": deck_id, "name": name, "color_identity": list(colors),
            "commanders": [{"name": c} for c in commanders], "format": fmt, "status": status, "legal": legal,
            "missing": missing, "missing_used_elsewhere": elsewhere, "value_usd": value, "card_count": cards,
            "cost_to_complete_usd": 0, "owned": cards - missing, "priority": 0}


DECKS = [
    deck(1, "Merpeople", "U", ["Hakbal of the Surging Soul"]),
    deck(2, "Big Simic Energy", "UG", ["Aesi, Tyrant of Gyre Strait"], missing=3, value=450),
    deck(3, "Four-color Good Stuff", "WUBG", ["Atraxa, Praetors' Voice"], status="inactive", legal=False),
    deck(4, "Artifacts", "", ["Traxos, Scourge of Kroog"], elsewhere=2),
    deck(5, "Burn", "R", fmt="modern", cards=60, value=80),
]
CARDS = {1: ["sol ring", "pym particles"], 2: ["sol ring", "cultivate"], 4: ["sol ring"], 5: ["lightning bolt"]}


def ids(query):
    return [d["deck_id"] for d in search(DECKS, CARDS, query)]


class DeckSearchTests(unittest.TestCase):
    def test_words_match_deck_and_commander_names(self):
        self.assertEqual(ids("simic"), [2])
        self.assertEqual(ids("atraxa"), [3])
        self.assertEqual(ids('"good stuff"'), [3])

    def test_colors(self):
        self.assertEqual(ids("c:u"), [1, 2, 3])                   # includes blue
        self.assertEqual(ids("c:simic"), [2, 3])
        self.assertEqual(ids("c=ug"), [2])
        self.assertEqual(ids("c<=ug"), [1, 2, 4])                 # within blue-green (colorless fits)
        self.assertEqual(ids("c:c"), [4])
        self.assertEqual(ids("-c:u"), [4, 5])

    def test_cards_commanders_and_settings(self):
        self.assertEqual(ids('card:"sol ring"'), [1, 2, 4])
        self.assertEqual(ids("card:sol -card:cultivate"), [1, 4])
        self.assertEqual(ids("cmd:traxos"), [4])
        self.assertEqual(ids("f:modern"), [5])
        self.assertEqual(ids("f:edh is:active"), [1, 2, 4])
        self.assertEqual(ids("is:illegal"), [3])
        self.assertEqual(ids("is:conflict"), [4])
        self.assertEqual(ids("is:incomplete"), [2])

    def test_numbers(self):
        self.assertEqual(ids("value>200"), [2])
        self.assertEqual(ids("cards<100"), [5])
        self.assertEqual(ids("missing>=1 c:g"), [2])

    def test_mistakes_are_explained(self):
        for query in ("c:purple", "is:shiny", "value>lots"):
            with self.assertRaises(ValueError):
                ids(query)
        self.assertEqual(ids("weird:thing"), [])                   # unknown keys are plain words


if __name__ == "__main__":
    unittest.main()
