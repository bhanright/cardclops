"""Unit tests for gallery.goldfish on small hand-made decks.

    python -m unittest tests.test_goldfish
"""
import itertools
import time
import unittest
from collections import Counter

from gallery import goldfish
from gallery.collection import TagIndex
from gallery.deckstats import DeckCard

_ids = itertools.count(1)

TAGS = TagIndex(
    descendants={
        "ramp": frozenset({"ramp", "mana-rock", "mana-dork", "land-ramp", "multi-land-ramp", "ritual"}),
        "land-ramp": frozenset({"land-ramp", "multi-land-ramp"}),
    },
    labels={},
)


def make_card(name, type_line="", mana_cost="", cmc=0, color_identity=(), **fields):
    n = next(_ids)
    card = {
        "id": f"card-{n}", "oracle_id": f"oracle-{n}", "name": name, "layout": "normal",
        "type_line": type_line, "mana_cost": mana_cost, "cmc": cmc, "oracle_text": "",
        "colors": [], "color_identity": list(color_identity), "keywords": [], "rarity": "common",
        "legalities": {"commander": "legal", "modern": "legal"},
    }
    card.update(fields)
    return card


def basic(kind, color):
    return make_card(kind, f"Basic Land — {kind}", produced_mana=[color])


def line(card, quantity=1, section="main", tags=()):
    return DeckCard(section, quantity, card, card["oracle_id"], frozenset(tags), None)


MOUNTAIN = basic("Mountain", "R")
FOREST = basic("Forest", "G")
ISLAND = basic("Island", "U")
SHOCK = make_card("Shock", "Instant", "{R}", 1, ["R"])
OPT = make_card("Opt", "Instant", "{U}", 1, ["U"])
BEARS = make_card("Grizzly Bears", "Creature — Bear", "{1}{G}", 2, ["G"])
ELEPHANT = make_card("Trained Armodon", "Creature — Elephant", "{1}{G}{G}", 3, ["G"])
WILDS = make_card("Evolving Wilds", "Land", oracle_text=(
    "{T}, Sacrifice this land: Search your library for a basic land card, put it onto the battlefield "
    "tapped, then shuffle."))
RAMPANT = make_card("Rampant Growth", "Sorcery", "{1}{G}", 2, ["G"], oracle_text=(
    "Search your library for a basic land card, put that card onto the battlefield tapped, then shuffle."))
CULTIVATE = make_card("Cultivate", "Sorcery", "{2}{G}", 3, ["G"], oracle_text=(
    "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield "
    "tapped and the other into your hand, then shuffle."))
SOL_RING = make_card("Sol Ring", "Artifact", "{1}", 1, oracle_text="{T}: Add {C}{C}.", produced_mana=["C"])
SIMIC = make_card("Tatyova, Benthic Druid", "Legendary Creature — Merfolk Druid", "{3}{G}{U}", 5, ["G", "U"])
OMNATH = make_card("Omnath, Locus of Mana", "Legendary Creature — Elemental", "{2}{G}", 3, ["G"])


def simulate(cards, deck_format="modern", **options):
    options.setdefault("games", 400)
    return goldfish.simulate(cards, deck_format, TAGS, **options)


def pool_of(*masks):
    return Counter(masks)


W, U, B, R, G, C = (goldfish.COLOR_BIT[color] for color in "WUBRGC")


class PaymentTests(unittest.TestCase):
    def variants(self, cost):
        return goldfish.cost_variants([cost])

    def payable(self, cost, *masks):
        pool = pool_of(*masks)
        return any(goldfish.can_pay(pool, sum(pool.values()), variant) for variant in self.variants(cost))

    def test_generic_and_colored(self):
        self.assertTrue(self.payable("{2}{G}", G, R, R))
        self.assertFalse(self.payable("{2}{G}", R, R, R))
        self.assertFalse(self.payable("{2}{G}", G, R))                   # not enough mana
        self.assertTrue(self.payable("{G}{G}", G, G | U))
        self.assertFalse(self.payable("{G}{U}", G | U, R))

    def test_matching_is_exact(self):
        # Two dual lands and a basic: {W}{U}{B} needs W from W|U, U from U|B, B from Swamp.
        self.assertTrue(self.payable("{W}{U}{B}", W | U, U | B, B))
        self.assertTrue(self.payable("{W}{U}{B}", W | U, W | U, B))
        # Every symbol has a source, but the three W|B and U|B lands cannot cover four symbols.
        self.assertFalse(self.payable("{W}{U}{B}{B}", W | B, W | B, U | B, R))
        self.assertTrue(self.payable("{W}{U}{B}{B}", W | B, W | B, U | B, B))
        self.assertTrue(self.payable("{1}{W}{U}", W | U, W | U, R))
        self.assertFalse(self.payable("{W}{W}{U}", W | U, W | U, R))

    def test_hybrid(self):
        self.assertTrue(self.payable("{R/G}{R/G}", R, G))
        self.assertTrue(self.payable("{R/G}", G))
        self.assertFalse(self.payable("{R/G}", U))

    def test_phyrexian_is_color_or_one_generic(self):
        self.assertTrue(self.payable("{1}{G/P}", R, R))
        self.assertFalse(self.payable("{1}{G/P}", R))
        self.assertTrue(self.payable("{G/U/P}", B))

    def test_twobrid_and_colorless(self):
        self.assertTrue(self.payable("{2/W}", W))
        self.assertTrue(self.payable("{2/W}", R, R))
        self.assertFalse(self.payable("{2/W}", R))
        self.assertTrue(self.payable("{C}", C))
        self.assertFalse(self.payable("{C}", W | U | B | R | G))        # Command Tower cannot pay {C}
        self.assertEqual(self.variants("{X}{R}")[0].total, 2)            # X is cast as 1

    def test_pay_keeps_flexible_mana(self):
        pool = pool_of(G, G | U, R)
        variant = self.variants("{1}{G}")[0]
        goldfish.pay(pool, variant, keep_mask=U)
        self.assertEqual(+pool, Counter({G | U: 1}))                    # G for {G}, R for {1}; the dual stays
        pool = pool_of(W | U, U | B, B)
        goldfish.pay(pool, self.variants("{W}{U}{B}")[0])
        self.assertEqual(sum(pool.values()), 0)

    def test_no_mana_cost_is_never_cast(self):
        self.assertEqual(goldfish.cost_variants([""]), [])
        self.assertEqual(goldfish.cost_variants(["{0}"])[0].total, 0)


class SimulationTests(unittest.TestCase):
    def test_contract_keys(self):
        result = simulate([line(MOUNTAIN, 24), line(SHOCK, 36)], games=50)
        self.assertEqual(set(result), {"games", "turns", "seed", "on_the_play", "elapsed_ms", "mulligans",
                                       "lands_by_turn", "mana_by_turn", "spent_by_turn", "efficiency",
                                       "commander", "screw_rate", "flood_rate", "color_screw_rate", "cards",
                                       "sample_game", "assumptions"})
        self.assertIsNone(result["commander"])
        self.assertEqual(len(result["sample_game"]), 10)
        self.assertEqual(set(result["sample_game"][0]), {"turn", "hand_size", "land", "cast", "mana", "spent"})
        self.assertEqual(result["cards"][0]["name"], "Shock")

    def test_deterministic_per_seed(self):
        deck = [line(FOREST, 17), line(ISLAND, 7), line(BEARS, 18), line(OPT, 18)]
        first, second = simulate(deck, seed=5), simulate(deck, seed=5)
        other = simulate(deck, seed=6)
        for result in (first, second, other):
            result.pop("elapsed_ms")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_mono_color_one_drops(self):
        result = simulate([line(MOUNTAIN, 24), line(SHOCK, 36)])
        self.assertEqual(result["color_screw_rate"], 0)
        self.assertGreater(result["spent_by_turn"]["1"], 0.95)         # a Shock on turn 1 nearly every game
        self.assertGreater(result["spent_by_turn"]["2"], 1.9)
        # Later every Shock drawn is cast at once: about 0.6 a turn (36 of 60 cards).
        for turn in range(6, 11):
            self.assertAlmostEqual(result["spent_by_turn"][str(turn)], 0.6, delta=0.12)
        # Every Shock seen by turn 10 (16 cards on the play, about 9.6 Shocks) gets cast.
        self.assertAlmostEqual(sum(result["spent_by_turn"].values()), 9.6, delta=0.8)
        self.assertAlmostEqual(result["lands_by_turn"]["1"], 1.0, delta=0.01)

    def test_missing_color_is_color_screw(self):
        result = simulate([line(MOUNTAIN, 24), line(OPT, 36)])
        self.assertGreater(result["color_screw_rate"], 0.9)
        self.assertEqual(result["spent_by_turn"]["10"], 0)
        self.assertEqual(result["cards"][0]["cast_rate"], 0)

    def test_mulligans(self):
        no_lands = simulate([line(SHOCK, 60)])
        self.assertEqual(no_lands["mulligans"]["rate"], 1.0)
        self.assertEqual(no_lands["mulligans"]["by_size"]["5"], 1.0)
        self.assertEqual(no_lands["mulligans"]["average_hand_size"], 5)
        normal = simulate([line(MOUNTAIN, 24), line(SHOCK, 36)], games=2000)
        self.assertGreater(normal["mulligans"]["rate"], 0.05)          # about 1 in 7 for 24 lands in 60
        self.assertLess(normal["mulligans"]["rate"], 0.3)
        self.assertGreater(normal["mulligans"]["by_size"]["7"], 0.7)

    def test_commander_free_mulligan(self):
        deck = [line(OMNATH, section="commander"), line(FOREST, 37), line(BEARS, 62)]
        free = simulate(deck, "commander")
        duel = simulate(deck, "duel")                                   # Duel Commander has no free mulligan
        # The free mulligan draws seven again, so more games keep seven cards.
        self.assertGreater(free["mulligans"]["by_size"]["7"], duel["mulligans"]["by_size"]["7"] + 0.1)
        self.assertGreater(free["mulligans"]["average_hand_size"], duel["mulligans"]["average_hand_size"])

    def test_commander_cast_by_turn(self):
        deck = [line(OMNATH, section="commander"), line(FOREST, 37), line(BEARS, 62)]
        result = simulate(deck, "commander")
        shares = [result["commander"]["cast_by_turn"][str(turn)] for turn in range(1, 11)]
        self.assertEqual(shares, sorted(shares))
        self.assertEqual(shares[0], 0)
        self.assertEqual(result["commander"]["median_turn"], 3)
        self.assertGreater(shares[-1], 0.95)
        row = next(row for row in result["cards"] if row["name"] == OMNATH["name"])
        self.assertEqual((row["mana_value"], row["median_turn"]), (3, 3))

    def test_two_color_commander_needs_both_colors(self):
        good = simulate([line(SIMIC, section="commander"), line(FOREST, 18), line(ISLAND, 18), line(BEARS, 63)],
                        "commander")
        bad = simulate([line(SIMIC, section="commander"), line(FOREST, 36), line(BEARS, 63)], "commander")
        # A five-drop with 36 lands and no ramp: about 40% to have five lands by turn 5 and
        # 55% by turn 7 (hypergeometric), and two-land hands with cheap plays are kept.
        self.assertIn(good["commander"]["median_turn"], (6, 7))
        self.assertEqual(bad["commander"]["cast_by_turn"]["10"], 0)
        self.assertIsNone(bad["commander"]["median_turn"])

    def test_land_ramp_adds_lands(self):
        plain = simulate([line(FOREST, 24), line(BEARS, 36)])
        ramp = simulate([line(FOREST, 24), line(BEARS, 24), line(RAMPANT, 6, tags={"land-ramp"}),
                         line(CULTIVATE, 6, tags={"land-ramp"})])
        self.assertGreater(ramp["lands_by_turn"]["5"], plain["lands_by_turn"]["5"] + 0.3)
        self.assertGreater(ramp["mana_by_turn"]["6"], plain["mana_by_turn"]["6"])

    def test_fetch_lands_fetch_the_needed_basic(self):
        # Only Forests make green, and six Evolving Wilds can find them: the Wilds fix the colors.
        basics_only = simulate([line(ISLAND, 18), line(FOREST, 6), line(ELEPHANT, 36)])
        fetches = simulate([line(ISLAND, 12), line(WILDS, 6), line(FOREST, 6), line(ELEPHANT, 36)])
        self.assertEqual(fetches["lands_by_turn"]["4"], basics_only["lands_by_turn"]["4"])   # a fetch is one land
        self.assertGreater(fetches["spent_by_turn"]["6"], basics_only["spent_by_turn"]["6"])
        self.assertLess(fetches["color_screw_rate"], basics_only["color_screw_rate"])
        all_fetches = simulate([line(WILDS, 12), line(FOREST, 12), line(BEARS, 36)], seed=3)
        logged = [turn["land"] for turn in all_fetches["sample_game"] if turn["land"]]
        self.assertTrue(any(name == "Evolving Wilds (Forest)" for name in logged) or "Forest" in logged)

    def test_mana_rock(self):
        without = simulate([line(FOREST, 24), line(BEARS, 36)])
        with_rocks = simulate([line(FOREST, 24), line(BEARS, 28), line(SOL_RING, 8, tags={"mana-rock"})])
        self.assertGreater(with_rocks["mana_by_turn"]["3"], without["mana_by_turn"]["3"] + 0.5)
        self.assertEqual(with_rocks["lands_by_turn"]["3"], without["lands_by_turn"]["3"])

    def test_speed(self):
        deck = [line(SIMIC, section="commander"), line(FOREST, 14), line(ISLAND, 14), line(WILDS, 8),
                line(RAMPANT, 5, tags={"land-ramp"}), line(SOL_RING, 5, tags={"mana-rock"}),
                line(BEARS, 25), line(ELEPHANT, 14), line(OPT, 14)]
        started = time.perf_counter()
        goldfish.simulate(deck, "commander", TAGS, games=2000, turns=10)
        self.assertLess(time.perf_counter() - started, 5.0)


if __name__ == "__main__":
    unittest.main()
