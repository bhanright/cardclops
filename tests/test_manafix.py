"""Unit tests for gallery.manafix on small hand-made decks.

    python -m unittest tests.test_manafix
"""
import itertools
import unittest
from collections import Counter

from gallery.deckstats import DeckCard, compute_deck_stats
from gallery.manafix import SpareLand, enters_tapped, suggest_swaps
from tests.test_deckstats import TAGS, basic, make_card

_line_ids = itertools.count(1)
_pools = itertools.count(1)

COMMANDER = make_card("Simic Boss", "Legendary Creature — Frog", "{G}{U}", 2, ["G", "U"])
BLUE_SPELL = make_card("Blue Wizard", "Creature — Wizard", "{1}{U}{U}", 3, ["U"])
GREEN_SPELL = make_card("Green Beast", "Creature — Beast", "{2}{G}", 3, ["G"])
FOREST = basic("Forest", "G")
ISLAND = basic("Island", "U")


def line(card, quantity=1, section="main"):
    return DeckCard(section, quantity, card, card["oracle_id"], frozenset(), None, next(_line_ids))


def spare(card, count=1, price=1.0, finish="normal"):
    return SpareLand(f"{card['id']}-{next(_pools)}|{finish}", card, card["oracle_id"], frozenset(), count, price)


def dual(name, colors, tapped=False, **fields):
    text = ("This land enters tapped.\n" if tapped else "") + "{T}: Add one of two colors."
    return make_card(name, "Land", color_identity=colors, produced_mana=list(colors), oracle_text=text, **fields)


def simic_deck(forests=30, islands=5, extra=()):
    """A Simic commander deck heavy on blue spells but built mostly of Forests."""
    return ([line(COMMANDER, section="commander"), line(BLUE_SPELL, 40), line(GREEN_SPELL, 24 - len(extra)),
             line(FOREST, forests), line(ISLAND, islands)] + list(extra))


def adds(result):
    return Counter(swap["add"]["pool"] for swap in result["swaps"])


class SwapTests(unittest.TestCase):
    def test_short_on_blue_swaps_a_forest_for_a_blue_dual(self):
        harbor = dual("Hinterland Harbor", ["G", "U"])
        result = suggest_swaps(simic_deck(), [spare(harbor)], "commander", {"G", "U"})
        first = result["swaps"][0]
        self.assertEqual(first["remove"]["name"], "Forest")
        self.assertEqual(first["remove"]["why"], "basic")
        self.assertEqual(first["add"]["name"], "Hinterland Harbor")
        self.assertEqual(first["note"], "Adds a blue source; replaces a Forest (green stays at 30 sources)")
        self.assertGreater(first["score_after"], result["before"]["score"])
        self.assertEqual(result["after"]["sources"]["U"], result["before"]["sources"]["U"] + 1)
        self.assertEqual(result["after"]["sources"]["G"], result["before"]["sources"]["G"])
        self.assertEqual(len(result["swaps"]), 1)                 # one spare copy, no Islands to add
        self.assertIsNone(result["message"])

    def test_no_swap_when_nothing_helps(self):
        deck = [line(COMMANDER, section="commander"), line(GREEN_SPELL, 63), line(FOREST, 36)]
        mono = make_card("Mono Green Boss", "Legendary Creature — Elf", "{G}", 1, ["G"])
        deck[0] = line(mono, section="commander")
        other_forest = basic("Forest", "G")
        result = suggest_swaps(deck, [spare(other_forest, 5)], "commander", {"G"})
        self.assertEqual(result["swaps"], [])
        self.assertEqual(result["before"], result["after"])
        self.assertTrue(result["message"])
        empty = suggest_swaps(deck, [spare(dual("Blue Red", ["U", "R"]))], "commander", {"G"})
        self.assertEqual(empty["message"], "No spare lands in these colors legal in commander")
        self.assertEqual(empty["considered"]["spare_lands"], 0)

    def test_never_adds_a_card_already_in_the_deck(self):
        harbor = dual("Hinterland Harbor", ["G", "U"])
        second_printing = dict(harbor, id="reprint-of-harbor")
        deck = simic_deck(forests=29, extra=[line(harbor)])
        result = suggest_swaps(deck, [spare(second_printing, 3)], "commander", {"G", "U"})
        self.assertEqual(result["swaps"], [])
        self.assertEqual(result["considered"]["spare_lands"], 0)

    def test_nonbasic_added_once_and_spare_counts_respected(self):
        harbor = dual("Hinterland Harbor", ["G", "U"])
        islands = spare(basic("Island", "U"), count=2, price=0.1)
        result = suggest_swaps(simic_deck(), [spare(harbor, 4), islands], "commander", {"G", "U"}, max_swaps=6)
        counts = adds(result)
        self.assertLessEqual(counts[islands.pool], 2)
        self.assertEqual(sum(1 for s in result["swaps"] if s["add"]["name"] == "Hinterland Harbor"), 1)
        self.assertEqual(len(result["swaps"]), 3)
        scores = [result["before"]["score"]] + [swap["score_after"] for swap in result["swaps"]]
        self.assertEqual(scores, sorted(scores))                   # each swap raises the score
        self.assertEqual(result["after"]["score"], result["swaps"][-1]["score_after"])

    def test_max_swaps(self):
        islands = spare(basic("Island", "U"), count=20)
        result = suggest_swaps(simic_deck(forests=33, islands=2), [islands], "commander", {"G", "U"}, max_swaps=2)
        self.assertEqual(len(result["swaps"]), 2)

    def test_prefers_untapped_lands_and_removing_tapped_ones(self):
        tapped = dual("Tapped Simic", ["G", "U"], tapped=True)
        untapped = dual("Untapped Simic", ["G", "U"])
        result = suggest_swaps(simic_deck(), [spare(tapped, price=0.1), spare(untapped, price=5.0)],
                               "commander", {"G", "U"}, max_swaps=1)
        self.assertEqual(result["swaps"][0]["add"]["name"], "Untapped Simic")
        # A green land that enters tapped goes before a Forest when both lose the same.
        slow_forest = make_card("Slow Grove", "Land", produced_mana=["G"],
                                oracle_text="This land enters tapped.\n{T}: Add {G}.")
        deck = simic_deck(forests=28, extra=[line(slow_forest, 1)])
        result = suggest_swaps(deck, [spare(untapped)], "commander", {"G", "U"})
        self.assertEqual(result["swaps"][0]["remove"]["name"], "Slow Grove")
        self.assertEqual(result["swaps"][0]["remove"]["why"], "enters tapped")
        self.assertEqual(result["before"]["tapped_lands"], 1)
        self.assertEqual(result["after"]["tapped_lands"], 0)
        self.assertIn("one fewer land enters tapped", result["swaps"][0]["note"])

    def test_respects_identity_and_legality(self):
        izzet = dual("Izzet Land", ["U", "R"])
        banned = dual("Banned Simic", ["G", "U"], legalities={"commander": "banned"})
        legal = dual("Legal Simic", ["G", "U"])
        result = suggest_swaps(simic_deck(), [spare(izzet), spare(banned)], "commander", {"G", "U"})
        self.assertEqual(result["swaps"], [])
        self.assertEqual(result["considered"]["spare_lands"], 0)
        result = suggest_swaps(simic_deck(), [spare(izzet), spare(banned), spare(legal)], "commander", {"G", "U"})
        self.assertEqual([swap["add"]["name"] for swap in result["swaps"]], ["Legal Simic"])
        # Casual decks have no card pool to check.
        result = suggest_swaps(simic_deck(), [spare(banned)], "casual", {"G", "U"})
        self.assertEqual([swap["add"]["name"] for swap in result["swaps"]], ["Banned Simic"])

    def test_fetch_counts_by_what_it_fetches(self):
        wilds = make_card("Evolving Wilds", "Land", oracle_text="{T}, Sacrifice this land: Search your library for "
                          "a basic land card, put it onto the battlefield tapped, then shuffle.")
        result = suggest_swaps(simic_deck(), [spare(wilds)], "commander", {"G", "U"})
        self.assertEqual([swap["add"]["name"] for swap in result["swaps"]], ["Evolving Wilds"])
        wastes_only = [line(COMMANDER, section="commander"), line(BLUE_SPELL, 63),
                       line(basic("Wastes", "C"), 36)]
        result = suggest_swaps(wastes_only, [spare(wilds)], "commander", {"G", "U"})
        self.assertEqual(result["considered"]["spare_lands"], 0)     # no basics in these colors to fetch


class ConsistencyTests(unittest.TestCase):
    def test_sources_match_deckstats(self):
        tower = make_card("Command Tower", "Land", produced_mana=["B", "G", "R", "U", "W"])
        wilds = make_card("Evolving Wilds", "Land", oracle_text="{T}, Sacrifice this land: Search your library for "
                          "a basic land card, put it onto the battlefield tapped, then shuffle.")
        mdfc = make_card("Spell // Blue Land", None, None, 2, ["U"], layout="modal_dfc", produced_mana=["U"],
                         card_faces=[{"name": "Spell", "type_line": "Sorcery", "mana_cost": "{1}{U}"},
                                     {"name": "Blue Land", "type_line": "Land", "mana_cost": ""}])
        filter_land = make_card("Filter", "Land", produced_mana=["C", "G", "U"])
        deck = simic_deck(forests=25, extra=[line(tower), line(wilds, 2), line(mdfc, 3), line(filter_land)])
        stats = compute_deck_stats(deck, "commander", TAGS)
        result = suggest_swaps(deck, [], "commander", {"G", "U"}, tag_index=TAGS)
        self.assertEqual(result["before"]["sources"], stats["sources"])
        self.assertEqual(result["before"]["by_color"], stats["castability"]["by_color"])
        self.assertEqual(result["message"], "No spare lands in these colors legal in commander")

    def test_enters_tapped(self):
        def land(text):
            return make_card("Some Land", "Land", oracle_text=text)
        self.assertTrue(enters_tapped(land("This land enters tapped.\n{T}: Add {W} or {B}.")))
        self.assertTrue(enters_tapped(land("Minas Morgul enters tapped.\n{T}: Add {B}.")))
        self.assertTrue(enters_tapped(land("This land enters tapped with two charge counters on it.")))
        self.assertTrue(enters_tapped(land("Some Land enters the battlefield tapped.")))
        self.assertFalse(enters_tapped(land("This land enters tapped unless you control two or more basic lands.")))
        self.assertFalse(enters_tapped(land("As this land enters, you may pay 2 life. "
                                            "If you don't, it enters tapped.")))
        self.assertFalse(enters_tapped(land("If you control two or more other lands, this land enters tapped.")))
        self.assertFalse(enters_tapped(land("{T}: Add {C}.")))


if __name__ == "__main__":
    unittest.main()
