"""Unit tests for gallery.deckstats on small hand-made decks.

    python -m unittest tests.test_deckstats
"""
import itertools
import math
import unittest

from gallery import deckstats
from gallery.collection import TagIndex
from gallery.deckstats import DeckCard, compute_deck_stats

_ids = itertools.count(1)

ALL_FORMATS = ("standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander", "brawl",
               "historic", "timeless")


def make_card(name, type_line="", mana_cost="", cmc=0, color_identity=(), legal=ALL_FORMATS, **fields):
    n = next(_ids)
    card = {
        "id": f"card-{n}", "oracle_id": f"oracle-{n}", "name": name, "layout": "normal",
        "type_line": type_line, "mana_cost": mana_cost, "cmc": cmc, "oracle_text": "",
        "colors": [], "color_identity": list(color_identity), "keywords": [], "rarity": "common",
        "legalities": {fmt: "legal" for fmt in legal}, "game_changer": False,
    }
    card.update(fields)
    return card


def basic(kind, color):
    return make_card(kind, f"Basic Land — {kind}", produced_mana=[color])


def line(card, quantity=1, section="main", tags=(), price=None):
    return DeckCard(section, quantity, card, card["oracle_id"], frozenset(tags), price)


TAGS = TagIndex(
    descendants={
        "ramp": frozenset({"ramp", "mana-rock", "mana-dork"}),
        "removal": frozenset({"removal", "removal-creature", "sweeper"}),
        "sweeper": frozenset({"sweeper"}),
        "draw": frozenset({"draw"}),
        "card-advantage": frozenset({"card-advantage", "draw"}),
        "tutor": frozenset({"tutor", "tutor-card", "tutor-creature", "tutor-land-basic", "fetchland",
                            "tutor-to-hand", "tutor-land-to-battlefield"}),
    },
    labels={},
)

GRIZZLY = make_card("Grizzly Bears", "Creature — Bear", "{1}{G}", 2, ["G"])


def filler(count, card=GRIZZLY):
    return [line(card, count)]


class PipTests(unittest.TestCase):
    def test_symbols(self):
        pips, strict = deckstats.parse_cost("{2}{W}{W}{W/U}{2/B}{R/P}{G/U/P}{C}{X}{S}")
        self.assertEqual(pips["W"], 2.5)
        self.assertEqual(pips["U"], 1.0)          # 1/2 from {W/U}, 1/2 from {G/U/P}
        self.assertEqual(pips["B"], 1)
        self.assertEqual(pips["R"], 1)
        self.assertEqual(pips["G"], 0.5)
        self.assertEqual(pips["C"], 1)
        self.assertEqual(strict, {"W": 2, "B": 1, "R": 1, "C": 1})   # hybrid halves demand no one color

    def test_split_counts_both_halves_and_dfc_counts_front(self):
        fire_ice = make_card("Fire // Ice", "Instant // Instant", "{1}{R} // {1}{U}", 4, ["R", "U"],
                             layout="split", card_faces=[
                                 {"name": "Fire", "type_line": "Instant", "mana_cost": "{1}{R}"},
                                 {"name": "Ice", "type_line": "Instant", "mana_cost": "{1}{U}"}])
        delver = make_card("Delver of Secrets // Insectile Aberration", None, None, 1, ["U"],
                           layout="transform", card_faces=[
                               {"name": "Delver of Secrets", "type_line": "Creature — Human Wizard",
                                "mana_cost": "{U}"},
                               {"name": "Insectile Aberration", "type_line": "Creature — Human Insect",
                                "mana_cost": "{G}{G}"}])     # a made-up back cost, which must not count
        stats = compute_deck_stats([line(fire_ice, 2), line(delver, 3)], "casual", TAGS)
        self.assertEqual(stats["pips"]["R"], 2)
        self.assertEqual(stats["pips"]["U"], 5)
        self.assertEqual(stats["pips"]["G"], 0)
        self.assertEqual(stats["types"], {"Creature": 3, "Instant": 2})
        self.assertEqual(stats["curve"]["4"], 2)
        self.assertEqual(stats["curve"]["1"], 3)
        self.assertAlmostEqual(stats["pip_share"]["U"], 5 / 7, places=3)

    def test_hybrid_pips_split_evenly(self):
        card = make_card("Kitchen Finks", "Creature — Ouphe", "{1}{G/W}{G/W}", 3, ["G", "W"])
        stats = compute_deck_stats([line(card, 4)], "casual", TAGS)
        self.assertEqual(stats["pips"]["G"], 4)
        self.assertEqual(stats["pips"]["W"], 4)
        self.assertEqual(stats["castability"]["hardest"], [])      # only hybrid symbols


class CurveAndTypeTests(unittest.TestCase):
    def test_curve_types_and_averages(self):
        golem = make_card("Stone Golem", "Legendary Artifact Creature — Golem", "{5}", 5)
        bolt = make_card("Lightning Bolt", "Instant", "{R}", 1, ["R"])
        titan = make_card("Big Titan", "Creature — Giant", "{6}{G}{G}", 8, ["G"])
        mountain = basic("Mountain", "R")
        stats = compute_deck_stats([line(golem), line(bolt, 4), line(titan), line(mountain, 10)], "casual", TAGS)
        self.assertEqual(stats["types"], {"Creature": 2, "Instant": 4, "Artifact": 1, "Land": 10})
        self.assertEqual(stats["counts"]["cards"], 16)
        self.assertEqual(stats["counts"]["lands"], 10)
        self.assertEqual(stats["counts"]["basic_lands"], 10)
        self.assertEqual(stats["counts"]["nonlands"], 6)
        self.assertEqual(stats["counts"]["creatures"], 2)
        self.assertEqual(stats["curve"], {"0": 0, "1": 4, "2": 0, "3": 0, "4": 0, "5": 1, "6": 0, "7+": 1})
        self.assertEqual(stats["curve_by_type"]["Creature"]["5"], 1)     # stacked by first type only
        self.assertNotIn("Artifact", stats["curve_by_type"])
        self.assertEqual(stats["average_mv"]["nonland"], round((5 + 4 + 8) / 6, 2))
        self.assertEqual(stats["average_mv"]["all"], round((5 + 4 + 8) / 16, 2))

    def test_sideboard_and_maybeboard_do_not_count(self):
        stats = compute_deck_stats([line(GRIZZLY, 4), line(GRIZZLY, 3, "sideboard"), line(GRIZZLY, 9, "maybeboard")],
                                   "casual", TAGS)
        self.assertEqual(stats["counts"]["cards"], 4)
        self.assertEqual(stats["counts"]["sideboard"], 3)

    def test_reversible_card_uses_face_cmc_and_name(self):
        crypt = make_card("Blood Crypt // Blood Crypt", None, None, None, ["B", "R"], layout="reversible_card",
                          produced_mana=["B", "R"], card_faces=[
                              {"name": "Blood Crypt", "type_line": "Land — Swamp Mountain", "cmc": 0.0},
                              {"name": "Blood Crypt", "type_line": "Land — Swamp Mountain", "cmc": 0.0}])
        crypt.pop("cmc")
        stats = compute_deck_stats([line(crypt, 2)], "casual", TAGS)
        self.assertEqual(stats["counts"]["lands"], 2)
        self.assertEqual(stats["land_kinds"]["dual"], 2)
        self.assertEqual(deckstats.card_name(crypt), "Blood Crypt")


class SourceTests(unittest.TestCase):
    def test_fetchlands(self):
        wilds = make_card("Evolving Wilds", "Land", oracle_text="{T}, Sacrifice this land: Search your library for "
                          "a basic land card, put it onto the battlefield tapped, then shuffle.")
        mesa = make_card("Arid Mesa", "Land", oracle_text="{T}, Pay 1 life, Sacrifice this land: Search your library "
                         "for a Mountain or Plains card, put it onto the battlefield, then shuffle.")
        deck = [line(wilds, 2), line(mesa), line(basic("Island", "U"), 5), line(basic("Forest", "G"), 5),
                line(make_card("Boros Guy", "Creature", "{R}{W}", 2, ["R", "W"])),
                line(make_card("Simic Guy", "Creature", "{G}{U}", 2, ["G", "U"]))]
        stats = compute_deck_stats(deck, "casual", TAGS)
        # Evolving Wilds finds the deck's basics (U, G); Arid Mesa finds Mountain or Plains types.
        self.assertEqual(stats["sources"]["U"], 7)
        self.assertEqual(stats["sources"]["G"], 7)
        self.assertEqual(stats["sources"]["R"], 1)
        self.assertEqual(stats["sources"]["W"], 1)
        self.assertEqual(stats["land_kinds"]["fetch"], 3)
        self.assertEqual(stats["land_kinds"]["basic"], 10)

    def test_any_color_land_capped_to_identity(self):
        tower = make_card("Command Tower", "Land", produced_mana=["B", "G", "R", "U", "W"])
        commander = make_card("Simic Boss", "Legendary Creature — Frog", "{G}{U}", 2, ["G", "U"])
        stats = compute_deck_stats([line(commander, section="commander"), line(tower)], "commander", TAGS)
        self.assertEqual(stats["sources"], {"W": 0, "U": 1, "B": 0, "R": 0, "G": 1, "C": 0, "any": 1})
        self.assertEqual(stats["land_kinds"]["five_color"], 1)

    def test_mdfc_with_land_back(self):
        emeria = make_card("Emeria's Call // Emeria, Shattered Skyclave", None, None, 7, ["W"], layout="modal_dfc",
                           produced_mana=["W"], card_faces=[
                               {"name": "Emeria's Call", "type_line": "Sorcery", "mana_cost": "{4}{W}{W}{W}"},
                               {"name": "Emeria, Shattered Skyclave", "type_line": "Land", "mana_cost": ""}])
        stats = compute_deck_stats([line(emeria), line(basic("Plains", "W"), 3)], "casual", TAGS)
        self.assertEqual(stats["counts"]["lands"], 3)            # a spell for counts and curve
        self.assertEqual(stats["counts"]["mdfc_lands"], 1)
        self.assertEqual(stats["curve"]["7+"], 1)
        self.assertEqual(stats["types"], {"Sorcery": 1, "Land": 3})
        self.assertEqual(stats["sources"]["W"], 4)               # ...but a full white source
        self.assertEqual(stats["pips"]["W"], 3)
        self.assertEqual(sum(stats["land_kinds"].values()), 3)

    def test_conditional_mana_is_not_a_source(self):
        five = ["B", "G", "R", "U", "W"]
        cavern = make_card("Cavern of Souls", "Land", produced_mana=["C"] + five, oracle_text=(
            "As this land enters, choose a creature type.\n{T}: Add {C}.\n{T}: Add one mana of any color. "
            "Spend this mana only to cast a creature spell of the chosen type, and that spell can't be countered."))
        shores = make_card("Unknown Shores", "Land", produced_mana=["C"] + five,
                           oracle_text="{T}: Add {C}.\n{1}, {T}: Add one mana of any color.")
        hub = make_card("Aether Hub", "Land", produced_mana=["C"] + five, oracle_text=(
            "When this land enters, you get {E}.\n{T}: Add {C}.\n{T}, Pay {E}: Add one mana of any color."))
        brass = make_card("City of Brass", "Land", produced_mana=five, oracle_text=(
            "Whenever this land becomes tapped, it deals 1 damage to you.\n{T}: Add one mana of any color."))
        mire = make_card("Twilight Mire", "Land", produced_mana=["B", "C", "G"],
                         oracle_text="{T}: Add {C}.\n{B/G}, {T}: Add {B}{B}, {B}{G}, or {G}{G}.")
        signet = make_card("Golgari Signet", "Artifact", "{2}", 2, produced_mana=["B", "G"],
                           oracle_text="{1}, {T}: Add {B}{G}.")
        self.assertEqual(deckstats.produced_colors(cavern), {"C"})
        self.assertEqual(deckstats.produced_colors(shores), {"C"})
        self.assertEqual(deckstats.produced_colors(hub), {"C"})
        self.assertEqual(deckstats.produced_colors(brass), set(five))
        self.assertEqual(deckstats.produced_colors(mire), {"B", "C", "G"})
        self.assertEqual(deckstats.produced_colors(signet), {"B", "G"})
        commander = make_card("Golgari Boss", "Legendary Creature — Elf", "{B}{G}", 2, ["B", "G"])
        stats = compute_deck_stats([line(commander, section="commander"), line(cavern), line(shores), line(brass)],
                                   "commander", TAGS)
        self.assertEqual(stats["sources"], {"W": 0, "U": 0, "B": 1, "R": 0, "G": 1, "C": 2, "any": 1})

    def test_nonland_sources_need_a_ramp_tag_and_a_permanent(self):
        signet = make_card("Arcane Signet", "Artifact", "{2}", 2, produced_mana=["B", "G", "R", "U", "W"])
        elves = make_card("Llanowar Elves", "Creature — Elf Druid", "{G}", 1, ["G"], produced_mana=["G"])
        ritual = make_card("Dark Ritual", "Instant", "{B}", 1, ["B"], produced_mana=["B"])
        untagged = make_card("Mana Cow", "Creature — Ox", "{G}", 1, ["G"], produced_mana=["G"])
        deck = [line(signet, tags={"mana-rock"}), line(elves, 2, tags={"mana-dork"}), line(ritual, tags={"ramp"}),
                line(untagged), line(make_card("Golgari Guy", "Creature", "{B}{G}", 2, ["B", "G"]))]
        stats = compute_deck_stats(deck, "casual", TAGS)
        self.assertEqual(stats["nonland_sources"], {"W": 0, "U": 0, "B": 1, "R": 0, "G": 3, "C": 0, "any": 1})
        self.assertEqual(stats["functions"]["ramp"], ["Arcane Signet", "Dark Ritual", "Llanowar Elves"])


class ProbabilityTests(unittest.TestCase):
    def test_hypergeometric_against_hand_computed(self):
        # P(at least 1 of 17 sources in 7 cards of 60) = 1 - C(43,7)/C(60,7)
        expected = 1 - math.comb(43, 7) / math.comb(60, 7)
        self.assertAlmostEqual(expected, 0.916563, places=6)
        self.assertAlmostEqual(deckstats.hypergeometric_at_least(60, 17, 7, 1), expected, places=12)
        self.assertAlmostEqual(deckstats.hypergeometric_exactly(60, 24, 7, 3), 0.308704, places=6)
        self.assertAlmostEqual(deckstats.hypergeometric_at_least(60, 17, 8, 2), 0.729222, places=6)
        self.assertEqual(deckstats.hypergeometric_at_least(60, 0, 7, 1), 0.0)
        self.assertEqual(deckstats.hypergeometric_at_least(60, 17, 7, 0), 1.0)

    def test_opening_hand_and_castability(self):
        mountain = basic("Mountain", "R")
        spell = make_card("Hill Giant", "Creature — Giant", "{2}{R}{R}", 4, ["R"])
        deck = [line(mountain, 17), line(spell, 43)]
        stats = compute_deck_stats(deck, "standard", TAGS)
        hand = stats["opening_hand"]
        self.assertEqual((hand["deck_size"], hand["lands"]), (60, 17))
        self.assertAlmostEqual(sum(hand["lands_in_seven"].values()), 1.0, places=3)
        self.assertAlmostEqual(hand["lands_in_seven"]["0"], math.comb(43, 7) / math.comb(60, 7), places=4)
        self.assertAlmostEqual(hand["expected_land_drops"]["1"], 0.9166, places=4)
        self.assertAlmostEqual(hand["expected_land_drops"]["2"], 0.7292, places=4)
        self.assertEqual(stats["castability"]["by_color"]["R"]["1"], round(0.916563, 4))
        hardest = stats["castability"]["hardest"]
        self.assertEqual(len(hardest), 1)                        # one entry per name, not per copy
        self.assertEqual((hardest[0]["turn"], hardest[0]["needed"], hardest[0]["color"]), (4, 2, "R"))
        self.assertAlmostEqual(hardest[0]["probability"],
                               deckstats.hypergeometric_at_least(60, 17, 10, 2), places=4)

    def test_commander_is_not_in_the_library(self):
        commander = make_card("Mono Green Boss", "Legendary Creature — Elf", "{G}", 1, ["G"])
        deck = [line(commander, section="commander"), line(basic("Forest", "G"), 38), line(GRIZZLY, 61)]
        stats = compute_deck_stats(deck, "commander", TAGS)
        self.assertEqual(stats["opening_hand"]["deck_size"], 99)
        self.assertEqual(stats["counts"]["cards"], 100)
        self.assertEqual(stats["curve"]["1"], 1)                 # the commander still counts for the curve


class LegalityTests(unittest.TestCase):
    def commander_deck(self, extra=(), main_count=99, commander=None):
        commander = commander or make_card("Simic Boss", "Legendary Creature — Frog", "{G}{U}", 2, ["G", "U"])
        forests = main_count - sum(item.quantity for item in extra)
        return [line(commander, section="commander"), line(basic("Forest", "G"), forests)] + list(extra)

    def reasons(self, stats):
        return {(problem["name"], problem["reason"]) for problem in stats["legality"]["problems"]}

    def test_size_singleton_identity_banned(self):
        crypt = make_card("Mana Crypt", "Artifact", "{0}", 0, legalities={"commander": "banned", "legacy": "banned",
                                                                          "vintage": "restricted"})
        bolt = make_card("Lightning Bolt", "Instant", "{R}", 1, ["R"])
        ponder = make_card("Ponder", "Sorcery", "{U}", 1, ["U"])
        stats = compute_deck_stats(self.commander_deck([line(crypt), line(bolt), line(ponder, 2)], main_count=98),
                                   "commander", TAGS)
        reasons = self.reasons(stats)
        self.assertIn(("Mana Crypt", "banned"), reasons)
        self.assertIn(("Lightning Bolt", "outside the commander's color identity"), reasons)
        self.assertIn(("Ponder", "2 copies; 1 allowed"), reasons)
        self.assertIn((None, "99 cards; commander decks need exactly 100"), reasons)
        self.assertEqual(len(reasons), 4)                        # basics are exempt from singleton
        self.assertFalse(stats["legality"]["legal"])
        self.assertFalse(stats["legality"]["other_formats"]["legacy"])
        self.assertTrue(stats["legality"]["other_formats"]["commander"] is False)

    def test_any_number_and_up_to_seven(self):
        rats = make_card("Relentless Rats", "Creature — Rat", "{1}{B}{B}", 3, ["B"],
                         oracle_text="A deck can have any number of cards named Relentless Rats.")
        dwarves = make_card("Seven Dwarves", "Creature — Dwarf", "{1}{R}", 2, ["R"],
                            oracle_text="A deck can have up to seven cards named Seven Dwarves.")
        commander = make_card("Rakdos Boss", "Legendary Creature — Devil", "{B}{R}", 2, ["B", "R"])
        deck = [line(commander, section="commander"), line(rats, 30), line(dwarves, 8),
                line(basic("Swamp", "B"), 61)]
        reasons = self.reasons(compute_deck_stats(deck, "commander", TAGS))
        self.assertEqual(reasons, {("Seven Dwarves", "8 copies; 7 allowed")})

    def test_commander_eligibility(self):
        bears = make_card("Plain Bear", "Creature — Bear", "{1}{G}", 2, ["G"])
        stats = compute_deck_stats(self.commander_deck(commander=bears), "commander", TAGS)
        self.assertIn(("Plain Bear", "not a legendary creature, so it cannot be a commander"), self.reasons(stats))
        walker = make_card("Teferi, Temporal Archmage", "Legendary Planeswalker — Teferi", "{4}{U}{U}", 6, ["U"],
                           oracle_text="Teferi, Temporal Archmage can be your commander.")
        islands = [line(walker, section="commander"), line(basic("Island", "U"), 99)]
        self.assertTrue(compute_deck_stats(islands, "commander", TAGS)["legality"]["legal"])

    def test_partners_and_backgrounds(self):
        first = make_card("Partner A", "Legendary Creature — Elf", "{G}", 1, ["G"], keywords=["Partner"])
        second = make_card("Partner B", "Legendary Creature — Elf", "{U}", 1, ["U"], keywords=["Partner"])
        deck = [line(first, section="commander"), line(second, section="commander"), line(basic("Forest", "G"), 98)]
        legality = compute_deck_stats(deck, "commander", TAGS)["legality"]
        self.assertTrue(legality["legal"], legality)
        self.assertEqual(legality["warnings"], [])
        chooser = make_card("Chooser", "Legendary Creature — Human", "{G}", 1, ["G"],
                            keywords=["Choose a background"])
        background = make_card("Some Background", "Legendary Enchantment — Background", "{U}", 1, ["U"])
        deck = [line(chooser, section="commander"), line(background, section="commander"),
                line(basic("Forest", "G"), 98)]
        legality = compute_deck_stats(deck, "commander", TAGS)["legality"]
        self.assertTrue(legality["legal"], legality)
        self.assertEqual(legality["warnings"], [])
        loner = make_card("Loner", "Legendary Creature — Human", "{U}", 1, ["U"])
        deck = [line(first, section="commander"), line(loner, section="commander"), line(basic("Forest", "G"), 98)]
        legality = compute_deck_stats(deck, "commander", TAGS)["legality"]
        self.assertTrue(legality["legal"])                       # only a warning when the pairing is unclear
        self.assertEqual(len(legality["warnings"]), 1)

    def test_constructed(self):
        bolt = make_card("Lightning Bolt", "Instant", "{R}", 1, ["R"], legal=("legacy", "vintage", "modern"))
        lotus = make_card("Black Lotus", "Artifact", "{0}", 0, legalities={"vintage": "restricted",
                                                                          "legacy": "banned"})
        deck = [line(bolt, 5), line(lotus, 2), line(basic("Mountain", "R"), 50), line(GRIZZLY, 4, "sideboard"),
                line(basic("Island", "U"), 6, "sideboard"), line(bolt, 6, "sideboard")]
        reasons = self.reasons(compute_deck_stats(deck, "vintage", TAGS))
        self.assertEqual(reasons, {("Lightning Bolt", "11 copies; 4 allowed"),
                                   ("Black Lotus", "restricted; 2 copies, 1 allowed"),
                                   (None, "57 cards; vintage decks need at least 60"),
                                   (None, "16-card sideboard; at most 15 allowed")})

    def test_casual_has_no_rules(self):
        crypt = make_card("Mana Crypt", "Artifact", "{0}", 0, legalities={"commander": "banned"})
        legality = compute_deck_stats([line(crypt, 9)], "casual", TAGS)["legality"]
        self.assertTrue(legality["legal"])
        self.assertEqual(legality["problems"], [])
        self.assertTrue(compute_deck_stats([line(crypt, 9)], None, TAGS)["legality"]["legal"])


class FunctionTests(unittest.TestCase):
    def test_functions_and_signals(self):
        wrath = make_card("Wrath of God", "Sorcery", "{2}{W}{W}", 4, ["W"])
        murder = make_card("Murder", "Instant", "{1}{B}{B}", 3, ["B"])
        tutor = make_card("Demonic Tutor", "Sorcery", "{1}{B}", 2, ["B"], game_changer=True)
        fetch = make_card("Evolving Wilds", "Land")
        cultivate = make_card("Cultivate", "Sorcery", "{2}{G}", 3, ["G"])
        deck = [line(wrath, tags={"sweeper"}), line(murder, tags={"removal-creature"}),
                line(tutor, tags={"tutor-card", "tutor-to-hand"}), line(fetch, tags={"fetchland", "ramp"}),
                line(cultivate, tags={"tutor-land-basic", "tutor-land-to-battlefield", "tutor-to-hand", "ramp"}),
                line(make_card("Time Walk", "Sorcery", "{1}{U}", 2, ["U"]), tags={"extra-turn"})]
        stats = compute_deck_stats(deck, "casual", TAGS)
        functions = stats["functions"]
        self.assertEqual(functions["removal"], ["Murder"])
        self.assertEqual(functions["board_wipes"], ["Wrath of God"])
        self.assertEqual(functions["tutors"], ["Demonic Tutor"])    # land tutors and fetchlands are not tutors
        self.assertEqual(functions["ramp"], ["Cultivate"])          # lands are not ramp
        self.assertEqual(stats["bracket_signals"]["game_changers"], ["Demonic Tutor"])
        self.assertEqual(stats["bracket_signals"]["extra_turns"], ["Time Walk"])
        self.assertEqual(stats["bracket_signals"]["tutors"], ["Demonic Tutor"])

    def test_price_and_rarity(self):
        cheap = make_card("Cheap", "Instant", "{R}", 1, rarity="common")
        pricey = make_card("Pricey", "Instant", "{R}", 1, rarity="mythic")
        stats = compute_deck_stats([line(cheap, 4, price=0.5), line(pricey, 2, price=10.0),
                                    line(GRIZZLY, 1, price=None), line(pricey, 3, "sideboard", price=99.0)],
                                   "casual", TAGS)
        self.assertEqual(stats["price"]["total_usd"], 22.0)
        self.assertEqual(stats["price"]["average_usd"], round(22.0 / 7, 2))
        self.assertEqual(stats["price"]["most_expensive"],
                         [{"name": "Pricey", "price_usd": 10.0}, {"name": "Cheap", "price_usd": 0.5}])
        self.assertEqual(stats["rarity"], {"common": 5, "mythic": 2})


if __name__ == "__main__":
    unittest.main()
