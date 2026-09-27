"""Unit tests for gallery.manafix on small hand-made decks.

    python -m unittest tests.test_manafix
"""
import itertools
import unittest
from collections import Counter

from gallery import manafix
from gallery.deckstats import DeckCard, compute_deck_stats
from gallery.manafix import (SpareLand, comes_in_tapped, enters_tapped, fetch_kind, is_utility_land,
                             suggest_swaps)
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


def dual(name, colors, tapped=False, extra_text="", **fields):
    symbols = " or ".join("{%s}" % c for c in colors)
    text = ("This land enters tapped.\n" if tapped else "") + f"{{T}}: Add {symbols}." + extra_text
    return make_card(name, "Land", color_identity=colors, produced_mana=list(colors), oracle_text=text, **fields)


def land(name, text, produced=(), colors=()):
    return make_card(name, "Land", color_identity=list(colors), produced_mana=list(produced) or None,
                     oracle_text=text)


def simic_deck(forests=30, islands=5, extra=()):
    """A Simic commander deck heavy on blue spells but built mostly of Forests."""
    return ([line(COMMANDER, section="commander"), line(BLUE_SPELL, 40),
             line(GREEN_SPELL, 24 - sum(item.quantity for item in extra)),
             line(FOREST, forests), line(ISLAND, islands)] + list(extra))


MORAINE_TEXT = ("{T}: Add {C}.\n{2}, {T}, Sacrifice this land: Search your library for a basic land card, "
                "put it onto the battlefield tapped, then shuffle.")
WILDS_TEXT = ("{T}, Sacrifice this land: Search your library for a basic land card, put it onto the "
              "battlefield tapped, then shuffle.")
MISTY_TEXT = ("{T}, Pay 1 life, Sacrifice this land: Search your library for a Forest or Island card, put it "
              "onto the battlefield, then shuffle.")


class SwapTests(unittest.TestCase):
    def test_short_on_blue_swaps_a_forest_for_an_untapped_blue_dual(self):
        harbor = dual("Hinterland Harbor", ["G", "U"])
        result = suggest_swaps(simic_deck(), [spare(harbor)], "commander", {"G", "U"})
        first = result["swaps"][0]
        self.assertEqual((first["remove"]["name"], first["remove"]["why"]), ("Forest", "basic"))
        self.assertEqual((first["add"]["name"], first["add"]["kind"]), ("Hinterland Harbor", "untapped"))
        self.assertRegex(first["note"], r"^An untapped U/G dual \(Hinterland Harbor\) instead of a Forest: "
                                        r"blue on turn 1 goes from \d+% to \d+%$")
        self.assertGreater(first["score_after"], result["before"]["score"] + manafix.MINIMUM_GAIN)
        self.assertEqual(result["after"]["sources"]["U"], result["before"]["sources"]["U"] + 1)
        self.assertEqual(result["after"]["sources"]["G"], result["before"]["sources"]["G"])
        self.assertEqual(len(result["swaps"]), 1)
        self.assertIsNone(result["message"])

    def test_no_swap_when_nothing_helps(self):
        mono = make_card("Mono Green Boss", "Legendary Creature — Elf", "{G}", 1, ["G"])
        deck = [line(mono, section="commander"), line(GREEN_SPELL, 63), line(FOREST, 36)]
        result = suggest_swaps(deck, [spare(basic("Forest", "G"), 5)], "commander", {"G"})
        self.assertEqual(result["swaps"], [])
        self.assertEqual(result["before"], result["after"])
        self.assertIn("already good", result["message"])
        empty = suggest_swaps(deck, [spare(dual("Blue Red", ["U", "R"]))], "commander", {"G"})
        self.assertEqual(empty["considered"]["spare_lands"], 0)
        self.assertIn("No spare lands fit this deck", empty["message"])

    def test_names_the_lands_that_would_help(self):
        tapped = dual("Tapped Simic", ["G", "U"], tapped=True)
        result = suggest_swaps(simic_deck(forests=33, islands=2), [spare(tapped)], "commander", {"G", "U"})
        self.assertEqual(result["swaps"], [])
        self.assertEqual(result["message"], "The lands that would help (untapped U/G duals) aren't among your "
                                            "spare copies")

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
        counts = Counter(swap["add"]["pool"] for swap in result["swaps"])
        self.assertLessEqual(counts[islands.pool], 2)
        self.assertEqual(sum(1 for s in result["swaps"] if s["add"]["name"] == "Hinterland Harbor"), 1)
        scores = [result["before"]["score"]] + [swap["score_after"] for swap in result["swaps"]]
        self.assertEqual(scores, sorted(scores))                   # each swap raises the score
        self.assertEqual(result["after"]["score"], result["swaps"][-1]["score_after"])

    def test_max_swaps(self):
        islands = spare(basic("Island", "U"), count=20)
        result = suggest_swaps(simic_deck(forests=33, islands=2), [islands], "commander", {"G", "U"}, max_swaps=2)
        self.assertEqual(len(result["swaps"]), 2)

    def test_prefers_untapped_lands_and_upgrades_tapped_ones(self):
        tapped = dual("Tapped Simic", ["G", "U"], tapped=True)
        untapped = dual("Untapped Simic", ["G", "U"])
        result = suggest_swaps(simic_deck(), [spare(tapped, price=0.1), spare(untapped, price=5.0)],
                               "commander", {"G", "U"}, max_swaps=1)
        self.assertEqual(result["swaps"][0]["add"]["name"], "Untapped Simic")
        slow_grove = dual("Slow Grove", ["G"], tapped=True)
        result = suggest_swaps(simic_deck(forests=28, extra=[line(slow_grove, 1)]), [spare(untapped)],
                               "commander", {"G", "U"})
        swap = result["swaps"][0]
        self.assertEqual((swap["remove"]["name"], swap["remove"]["why"]), ("Slow Grove", "enters tapped"))
        self.assertEqual((result["before"]["tapped_lands"], result["after"]["tapped_lands"]), (1, 0))
        self.assertIn("one fewer land that comes into play slow", swap["note"])

    def test_basic_for_a_tapped_dual_needs_a_bigger_gain(self):
        tapped = dual("Tapped Simic", ["G", "U"], tapped=True)
        untapped = dual("Untapped Simic", ["G", "U"])
        # Mildly short on blue: an untapped dual clears the bar, the same dual entering tapped does not.
        deck = simic_deck(forests=20, islands=15)
        self.assertEqual(suggest_swaps(deck, [spare(tapped)], "commander", {"G", "U"})["swaps"], [])
        self.assertEqual(len(suggest_swaps(deck, [spare(untapped)], "commander", {"G", "U"})["swaps"]), 1)

    def test_tapped_land_goes_only_for_an_untapped_land_with_all_its_colors(self):
        gain_land = dual("Simic Gain Land", ["G", "U"], tapped=True,
                         extra_text="\nWhen this land enters, you gain 1 life.")
        blue_only = dual("Untapped Blue", ["U"])
        deck = simic_deck(forests=0, islands=35 - 1, extra=[line(gain_land)])
        deck[2] = line(GREEN_SPELL, 23)
        result = suggest_swaps(deck, [spare(blue_only)], "commander", {"G", "U"})
        self.assertNotIn("Simic Gain Land", [swap["remove"]["name"] for swap in result["swaps"]])

    def test_utility_lands_are_never_removed(self):
        emeria = land("Emeria, the Sky Ruin", "This land enters tapped.\nAt the beginning of your upkeep, if you "
                      "control seven or more Plains, you may return target creature card from your graveyard to "
                      "the battlefield.\n{T}: Add {W}.", produced=["W"], colors=["W"])
        dakmor = land("Dakmor Salvage", "This land enters tapped.\n{T}: Add {B}.\nDredge 2", produced=["B"],
                      colors=["B"])
        self.assertTrue(is_utility_land(emeria))
        self.assertTrue(is_utility_land(dakmor))
        boss = make_card("Orzhov Boss", "Legendary Creature — Human", "{W}{B}", 2, ["W", "B"])
        spell = make_card("Orzhov Spell", "Sorcery", "{1}{W}{B}", 3, ["W", "B"])
        deck = [line(boss, section="commander"), line(spell, 63), line(emeria), line(dakmor),
                line(basic("Plains", "W"), 28), line(basic("Swamp", "B"), 6)]
        result = suggest_swaps(deck, [spare(dual("Scrubland", ["W", "B"]), 1)], "commander", {"W", "B"})
        self.assertTrue(result["swaps"])
        removed = {swap["remove"]["name"] for swap in result["swaps"]}
        self.assertFalse(removed & {"Emeria, the Sky Ruin", "Dakmor Salvage"})

    def test_at_most_one_costly_fetch_and_only_when_it_clearly_wins(self):
        moraines = [land(f"Moraine {n}", MORAINE_TEXT, produced=["C"]) for n in range(3)]
        result = suggest_swaps(simic_deck(forests=33, islands=2), [spare(m) for m in moraines], "commander",
                               {"G", "U"})
        costly = [swap for swap in result["swaps"] if swap["add"]["kind"] == "costly_fetch"]
        self.assertLessEqual(len(costly), 1)
        for swap in costly:
            self.assertIn("pays mana to fetch a basic", swap["note"])
        # A deck that already plays one gets none.
        deck = simic_deck(forests=32, islands=2, extra=[line(land("Old Moraine", MORAINE_TEXT, produced=["C"]))])
        result = suggest_swaps(deck, [spare(m) for m in moraines], "commander", {"G", "U"})
        self.assertEqual([s for s in result["swaps"] if s["add"]["kind"] == "costly_fetch"], [])

    def test_respects_identity_and_legality(self):
        izzet = dual("Izzet Land", ["U", "R"])
        banned = dual("Banned Simic", ["G", "U"], legalities={"commander": "banned"})
        legal = dual("Legal Simic", ["G", "U"])
        result = suggest_swaps(simic_deck(), [spare(izzet), spare(banned)], "commander", {"G", "U"})
        self.assertEqual(result["swaps"], [])
        self.assertEqual(result["considered"]["spare_lands"], 0)
        result = suggest_swaps(simic_deck(), [spare(izzet), spare(banned), spare(legal)], "commander", {"G", "U"})
        self.assertEqual([swap["add"]["name"] for swap in result["swaps"]], ["Legal Simic"])
        result = suggest_swaps(simic_deck(), [spare(banned)], "casual", {"G", "U"})
        self.assertEqual([swap["add"]["name"] for swap in result["swaps"]], ["Banned Simic"])

    def test_fetch_counts_by_what_it_fetches(self):
        misty = land("Misty Rainforest", MISTY_TEXT)
        result = suggest_swaps(simic_deck(), [spare(misty)], "commander", {"G", "U"})
        self.assertEqual([(s["add"]["name"], s["add"]["kind"]) for s in result["swaps"]],
                         [("Misty Rainforest", "fetch")])
        wilds = land("Evolving Wilds", WILDS_TEXT)
        wastes_only = [line(COMMANDER, section="commander"), line(BLUE_SPELL, 63), line(basic("Wastes", "C"), 36)]
        result = suggest_swaps(wastes_only, [spare(wilds)], "commander", {"G", "U"})
        self.assertEqual(result["considered"]["spare_lands"], 0)     # no basics in these colors to fetch

    def test_landfall_decks_keep_their_fetches(self):
        wilds = land("Evolving Wilds", WILDS_TEXT)
        untapped = dual("Untapped Simic", ["G", "U"])
        landfall = make_card("Landfall Beast", "Creature — Beast", "{2}{G}", 3, ["G"],
                             oracle_text="Landfall — Whenever a land you control enters, put a +1/+1 counter.")
        plain = simic_deck(forests=29, extra=[line(wilds)])
        themed = simic_deck(forests=29, extra=[line(wilds), line(landfall, 3)])
        plain_result = suggest_swaps(plain, [spare(untapped)], "commander", {"G", "U"})
        themed_result = suggest_swaps(themed, [spare(untapped)], "commander", {"G", "U"})
        self.assertEqual(plain_result["considered"]["removable"], 35)       # 34 basics and the Wilds
        self.assertEqual(themed_result["considered"]["removable"], 34)      # the basics only


class ModelTests(unittest.TestCase):
    def test_sources_match_deckstats(self):
        tower = make_card("Command Tower", "Land", produced_mana=["B", "G", "R", "U", "W"],
                          oracle_text="{T}: Add one mana of any color in your commander's color identity.")
        wilds = land("Evolving Wilds", WILDS_TEXT)
        mdfc = make_card("Spell // Blue Land", None, None, 2, ["U"], layout="modal_dfc", produced_mana=["U"],
                         card_faces=[{"name": "Spell", "type_line": "Sorcery", "mana_cost": "{1}{U}"},
                                     {"name": "Blue Land", "type_line": "Land", "mana_cost": "",
                                      "oracle_text": "{T}: Add {U}."}])
        filter_land = make_card("Filter", "Land", produced_mana=["C", "G", "U"],
                                oracle_text="{T}: Add {C}.\n{G/U}, {T}: Add {G}{G}, {G}{U}, or {U}{U}.")
        deck = simic_deck(forests=25, extra=[line(tower), line(wilds, 2), line(mdfc, 3), line(filter_land)])
        stats = compute_deck_stats(deck, "commander", TAGS)
        result = suggest_swaps(deck, [], "commander", {"G", "U"}, tag_index=TAGS)
        self.assertEqual(result["before"]["sources"], stats["sources"])

    def test_untapped_lands_score_like_deckstats(self):
        """With only untapped lands and no rocks, the timing model is plain source counting."""
        deck = simic_deck(forests=20, islands=15)
        stats = compute_deck_stats(deck, "commander", TAGS)
        result = suggest_swaps(deck, [], "commander", {"G", "U"})
        self.assertEqual(result["before"]["by_color"], stats["castability"]["by_color"])
        self.assertEqual(result["before"]["tempo_cost"], 0)

    def test_tapped_and_costly_sources_count_less_early(self):
        self.assertEqual(manafix._delayed_weight(1), 0)
        self.assertAlmostEqual(manafix._delayed_weight(3), 8 / 9)
        self.assertEqual(manafix._costly_weight(2), 0)
        self.assertAlmostEqual(manafix._costly_weight(4), manafix.COSTLY_SHARE * 8 / 10)
        # A fractional source count lies between the whole counts around it.
        low, high = (manafix.hypergeometric_at_least(99, k, 7, 1) for k in (10, 11))
        self.assertAlmostEqual(manafix._probability(99, 10.25, 7, 1), low + 0.25 * (high - low))

    def test_land_classification(self):
        def text_land(text):
            return land("Some Land", text)
        self.assertTrue(enters_tapped(text_land("This land enters tapped.\n{T}: Add {W} or {B}.")))
        self.assertTrue(enters_tapped(text_land("Minas Morgul enters tapped.\n{T}: Add {B}.")))
        self.assertTrue(enters_tapped(text_land("This land enters tapped with two charge counters on it.")))
        self.assertFalse(enters_tapped(text_land("This land enters tapped unless you control two or more "
                                                 "basic lands.")))
        self.assertFalse(enters_tapped(text_land("As this land enters, you may pay 2 life. "
                                                 "If you don't, it enters tapped.")))
        # Conditions a normal deck meets early count as untapped; the rest as tapped.
        for easy in ("This land enters tapped unless you control a Plains or an Island.",
                     "This land enters tapped unless you have two or more opponents.",
                     "This land enters tapped unless you control two or fewer other lands.",
                     "As this land enters, you may reveal a Plains or Island card from your hand. "
                     "If you don't, this land enters tapped."):
            self.assertFalse(comes_in_tapped(text_land(easy + "\n{T}: Add {W} or {U}.")), easy)
        for hard in ("This land enters tapped unless a player has 13 or less life.",
                     "This land enters tapped unless you control a legendary creature.",
                     "As this land enters, you may reveal a Merfolk card from your hand. "
                     "If you don't, this land enters tapped."):
            self.assertTrue(comes_in_tapped(text_land(hard + "\n{T}: Add {W} or {U}.")), hard)
        self.assertEqual(fetch_kind(text_land(MISTY_TEXT)), "fetch")
        self.assertEqual(fetch_kind(text_land(WILDS_TEXT)), "slow")
        self.assertEqual(fetch_kind(text_land(MORAINE_TEXT)), "costly")
        self.assertEqual(fetch_kind(text_land("{T}: Add {C}.\nBasic landcycling {1} ({1}, Discard this card: "
                                              "Search your library for a basic land card, reveal it, put it into "
                                              "your hand, then shuffle.)")), "costly")
        self.assertFalse(is_utility_land(dual("Temple", ["W", "U"], tapped=True,
                                              extra_text="\nWhen this land enters, scry 1.")))
        self.assertTrue(is_utility_land(dual("Cycling Dual", ["W", "U"], tapped=True,
                                             extra_text="\nCycling {2} ({2}, Discard this card: Draw a card.)")))


if __name__ == "__main__":
    unittest.main()
