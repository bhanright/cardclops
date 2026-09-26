"""Unit tests for gallery.builder ("What can I build?") on small hand-made collections.

    python -m unittest tests.test_builder
"""
import itertools
import unittest
from collections import Counter, defaultdict

from gallery import builder as builder_module
from gallery.builder import Builder
from gallery.collection import TagIndex, build_entry
from gallery.decks import parse_decklist
from gallery.similar import SimilarityIndex

_ids = itertools.count(1)

TAGS = TagIndex(
    descendants={
        "ramp": frozenset({"ramp", "mana-rock", "land-ramp"}),
        "draw": frozenset({"draw"}),
        "card-advantage": frozenset({"card-advantage", "draw"}),
        "removal": frozenset({"removal", "removal-creature", "sweeper"}),
        "sweeper": frozenset({"sweeper"}),
    },
    labels={},
)
COLOR_OF_BASIC = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}


def make_card(name, type_line="Creature — Bear", mana_cost="{1}{G}", cmc=2, identity=("G",), edhrec=None,
              legal=True, layout="normal", oracle_text="", produced_mana=None, **fields):
    n = next(_ids)
    card = {
        "id": f"card-{n}", "oracle_id": f"oracle-{n}", "name": name, "layout": layout,
        "type_line": type_line, "mana_cost": mana_cost, "cmc": cmc, "oracle_text": oracle_text,
        "colors": list(identity), "color_identity": list(identity), "keywords": [], "rarity": "common",
        "set": "tst", "set_name": "Test Set", "set_type": "expansion", "collector_number": str(n),
        "released_at": "2020-01-01", "artist": "Someone", "prices": {"usd": "0.25", "usd_foil": "1.00"},
        "legalities": {"commander": "legal" if legal is True else legal}, "game_changer": False,
        "edhrec_rank": edhrec,
    }
    if produced_mana is not None:
        card["produced_mana"] = produced_mana
    card.update(fields)
    return card


def basic(kind):
    return make_card(kind, f"Basic Land — {kind}", "", 0, (), produced_mana=[COLOR_OF_BASIC[kind]],
                     oracle_text=f"({{T}}: Add {{{COLOR_OF_BASIC[kind]}}}.)")


def legend(name, identity=("G",), mana_cost="{2}{G}{G}", cmc=4, **fields):
    return make_card(name, "Legendary Creature — Elf Druid", mana_cost, cmc, identity, **fields)


class FakeCollection:
    def __init__(self):
        self.entries = []
        self.by_oracle_id = defaultdict(list)

    def add(self, card, quantity=1, used=0, finish="normal", tags=()):
        holding = {"row_id": next(_ids), "scryfall_id": card["id"], "finish": finish, "quantity": quantity,
                   "condition": "near_mint", "language": "en", "purchase_price": None, "added_at": "",
                   "misprint": 0}
        entry = build_entry(holding, card, {card["oracle_id"]: frozenset(tags)})
        entry.used = used
        self.entries.append(entry)
        self.by_oracle_id[entry.oracle_id].append(entry)
        return entry


class StubSimilarity:
    """similar() from a fixed table: {oracle_id: [(other, score), ...]}."""

    def __init__(self, table=None):
        self.table = table or {}

    def similar(self, oracle_id, limit=12):
        return sorted(self.table.get(oracle_id, []), key=lambda item: -item[1])[:limit]


def spells(collection, count, prefix="Spell", identity=("G",), cost="{1}{G}", cmc=2, tags=(), edhrec_from=1000,
           **fields):
    cards = []
    for i in range(count):
        card = make_card(f"{prefix} {i}", "Creature — Bear", cost, cmc, identity, edhrec=edhrec_from + i, **fields)
        collection.add(card, tags=tags)
        cards.append(card)
    return cards


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeCollection()
        self.commander = legend("Gruff, Elf Leader")
        self.c.add(self.commander, quantity=1)
        self.good = make_card("Llanowar Pal", edhrec=10)
        self.c.add(self.good, tags={"mana-rock"})
        self.blue = make_card("Blue Thing", mana_cost="{U}", identity=("U",), edhrec=5)
        self.c.add(self.blue)
        self.banned = make_card("Banned Thing", edhrec=1, legal="banned")
        self.c.add(self.banned)
        self.used_up = make_card("Used Up", edhrec=2)
        self.c.add(self.used_up, quantity=2, used=2)
        self.token = make_card("Bear Token", "Token Creature — Bear", "", 0, ("G",), layout="token")
        self.c.add(self.token)
        self.colorless = make_card("Rock", "Artifact", "{2}", 2, (), edhrec=3)
        self.c.add(self.colorless)
        self.c.add(basic("Forest"), quantity=5)
        self.c.add(basic("Island"), quantity=5)
        self.builder = Builder(self.c, StubSimilarity(), TAGS)

    def test_pool_respects_identity_legality_spare_and_kind(self):
        rows = self.builder.rank()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Llanowar Pal and Rock; not the blue card, the banned one, the used-up one, the token or Gruff himself.
        self.assertEqual(row["pool_size"], 2)
        draft = self.builder.draft(self.commander["oracle_id"])
        names = {line["card"].name for line in draft["lines"]}
        self.assertEqual(names, {"Gruff, Elf Leader", "Llanowar Pal", "Rock", "Forest"})
        # Only green basics: five Forests, no Islands.
        forests = sum(line["quantity"] for line in draft["lines"] if line["card"].name == "Forest")
        self.assertEqual(forests, 5)
        self.assertLess(sum(line["quantity"] for line in draft["lines"]), 100)
        self.assertTrue(any("fills only" in note for note in draft["notes"]))

    def test_commander_needs_no_spare_copy(self):
        c = FakeCollection()
        commander = legend("Busy Leader")
        c.add(commander, quantity=1, used=1)
        builder = Builder(c, StubSimilarity(), TAGS)
        self.assertEqual([row["card"].name for row in builder.rank()], ["Busy Leader"])
        self.assertEqual(builder.draft(commander["oracle_id"])["commander"].name, "Busy Leader")

    def test_non_commanders_are_refused(self):
        with self.assertRaises(ValueError):
            self.builder.draft(self.good["oracle_id"])
        with self.assertRaises(ValueError):
            self.builder.draft("no-such-card")

    def test_can_be_your_commander_counts(self):
        c = FakeCollection()
        walker = make_card("Walker", "Legendary Planeswalker — Someone", "{3}{G}", 4, ("G",),
                           oracle_text="Walker can be your commander.")
        c.add(walker)
        illegal = legend("Banned Leader", legal="banned")
        c.add(illegal)
        builder = Builder(c, StubSimilarity(), TAGS)
        self.assertEqual([row["card"].name for row in builder.rank()], ["Walker"])


class PartTests(unittest.TestCase):
    def test_parts_cap_and_a_card_fills_one_part(self):
        c = FakeCollection()
        commander = legend("Leader")
        c.add(commander)
        spells(c, 12, "Ramp", tags={"mana-rock"}, edhrec_from=100)        # 12 ramp: 10 fill ramp
        both = make_card("Rampy Draw", edhrec=1)                           # best-ranked: fills ramp, not draw
        c.add(both, tags={"mana-rock", "draw"})
        spells(c, 4, "Draw", tags={"draw"}, edhrec_from=200)
        spells(c, 2, "Kill", tags={"removal-creature"}, edhrec_from=300)
        spells(c, 5, "Wipe", tags={"sweeper"}, edhrec_from=400)            # sweepers are wipes, not removal
        spells(c, 3, "Other", edhrec_from=500)
        c.add(basic("Forest"), quantity=30)
        builder = Builder(c, StubSimilarity(), TAGS)
        row = builder.rank()[0]
        have = {part: value["have"] for part, value in row["parts"].items()}
        # Ramp takes Rampy Draw and 9 of 12; the 3 left over fall through to synergy.
        self.assertEqual(have, {"lands": 30, "ramp": 10, "draw": 4, "removal": 2, "wipes": 3, "synergy": 3 + 3 + 2})
        self.assertAlmostEqual(row["coverage"], round((30 + 10 + 4 + 2 + 3 + 8) / 99, 4))
        for part, value in row["parts"].items():
            self.assertLessEqual(value["have"], value["target"])
            self.assertEqual(value["target"], builder_module.PART_TARGETS[part])

    def test_commander_that_fills_a_part_is_left_out(self):
        c = FakeCollection()
        dork = legend("Dork Leader", edhrec=1)
        c.add(dork, tags={"mana-rock"})
        spells(c, 3, "Ramp", tags={"mana-rock"})
        builder = Builder(c, StubSimilarity(), TAGS)
        self.assertEqual(builder.rank()[0]["parts"]["ramp"]["have"], 3)


def big_collection(extra_legends=()):
    """A green commander with more than enough for a full deck, and a stub similarity for it."""
    c = FakeCollection()
    commander = legend("Big Leader", mana_cost="{2}{G}{U}", identity=("G", "U"))
    c.add(commander, finish="foil")
    others = []
    for i in range(10):
        others.append(make_card(f"Rock {i}", "Artifact", "{2}", 2, (), edhrec=10 + i, produced_mana=["C"]))
        c.add(others[-1], tags={"mana-rock"})
    for i in range(12):
        others.append(make_card(f"Card Draw {i}", "Sorcery", "{2}{U}", 3, ("U",), edhrec=40 + i))
        c.add(others[-1], tags={"draw"})
    for i in range(9):
        others.append(make_card(f"Kill {i}", "Instant", "{1}{G}", 2, ("G",), edhrec=60 + i))
        c.add(others[-1], tags={"removal"}, finish="etched" if i == 0 else "normal")
    for i in range(3):
        others.append(make_card(f"Wipe {i}", "Sorcery", "{4}{G}{G}", 6, ("G",), edhrec=80 + i))
        c.add(others[-1], tags={"sweeper"})
    for i in range(20):                      # expensive: tests the top-end rule
        others.append(make_card(f"Fatty {i}", "Creature — Beast", "{6}{G}", 7, ("G",), edhrec=90 + i))
        c.add(others[-1])
    for i in range(40):
        others.append(make_card(f"Bear {i}", "Creature — Bear", "{1}{G}", 2, ("G",), edhrec=2000 + i))
        c.add(others[-1])
    for i in range(10):
        others.append(make_card(f"Dual {i}", "Land", "", 0, (), edhrec=500 + i, produced_mana=["G", "U"],
                                oracle_text="{T}: Add {G} or {U}."))
        c.add(others[-1])
    c.add(basic("Forest"), quantity=20)
    forest_foil = basic("Forest")
    forest_foil["prices"] = {"usd": "3.00"}
    c.add(forest_foil, quantity=10)
    c.add(basic("Island"), quantity=20)
    c.add(basic("Swamp"), quantity=20)            # off-color: never used
    for card in extra_legends:
        c.add(card)
    fatties, bears = others[34:54], others[54:94]
    table = {commander["oracle_id"]: [(card["oracle_id"], 0.6) for card in fatties]
             + [(card["oracle_id"], 0.5 - i * 0.01) for i, card in enumerate(bears)]}
    return c, commander, table


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.c, self.commander, table = big_collection()
        self.builder = Builder(self.c, StubSimilarity(table), TAGS)
        self.draft = self.builder.draft(self.commander["oracle_id"])

    def test_one_hundred_cards_singleton_with_the_commander_first(self):
        lines = self.draft["lines"]
        self.assertEqual(sum(line["quantity"] for line in lines), 100)
        self.assertEqual(lines[0]["role"], "commander")
        self.assertEqual(lines[0]["card"].name, "Big Leader")
        self.assertIs(self.draft["commander"], lines[0]["card"])
        counts = Counter()
        for line in lines:
            counts[line["card"].name] += line["quantity"]
        for name, count in counts.items():
            if name not in ("Forest", "Island"):
                self.assertEqual(count, 1, name)
        self.assertNotIn("Swamp", counts)
        stats = self.draft["stats"]
        self.assertTrue(stats["legality"]["legal"], stats["legality"]["problems"])
        self.assertEqual(stats["counts"]["cards"], 100)

    def test_parts_curve_and_lands(self):
        roles = Counter()
        for line in self.draft["lines"]:
            roles[line["role"]] += line["quantity"]
        self.assertEqual(roles["ramp"], 10)
        self.assertEqual(roles["draw"], 10)
        self.assertEqual(roles["removal"], 8)
        self.assertEqual(roles["wipes"], 3)
        self.assertIn(roles["lands"], (35, 36, 37))
        self.assertEqual(self.draft["shortfalls"], {})
        top_end = sum(line["quantity"] for line in self.draft["lines"]
                      if line["category"] != "Land" and line["card"].cmc >= 6)
        self.assertLessEqual(top_end, builder_module.MAX_TOP_END)
        self.assertTrue(any("top end" in note for note in self.draft["notes"]))
        # Every dual makes both colors, so all ten play before the basics that fill the rest.
        self.assertEqual(sum(1 for line in self.draft["lines"] if line["card"].name.startswith("Dual")), 10)

    def test_basics_follow_the_pips(self):
        basics = Counter()
        for line in self.draft["lines"]:
            if line["card"].name in ("Forest", "Island"):
                basics[line["card"].name] += line["quantity"]
        pips = self.draft["stats"]["pips"]
        self.assertGreater(pips["G"], pips["U"])
        self.assertGreater(basics["Forest"], basics["Island"])
        self.assertGreaterEqual(basics["Island"], 1)
        # The cheapest Forests go first: the $3 printing is untouched unless the cheap ones run out.
        pricey = [line for line in self.draft["lines"] if line["card"].name == "Forest" and line["card"].price_usd == 3.0]
        if basics["Forest"] <= 20:
            self.assertEqual(pricey, [])

    def test_text_round_trips_through_the_parser(self):
        parsed, _ = parse_decklist(self.draft["text"])
        self.assertEqual(sum(line.quantity for line in parsed), 100)
        by_section = Counter()
        for line in parsed:
            by_section[line.section] += line.quantity
        self.assertEqual(by_section, {"commander": 1, "main": 99})
        commander = next(line for line in parsed if line.section == "commander")
        self.assertEqual(commander.name, "Big Leader")
        self.assertEqual(commander.finish, "foil")
        self.assertEqual(commander.set_code, "tst")
        by_name = {line.name: line for line in parsed}
        self.assertEqual(by_name["Kill 0"].finish, "etched")
        self.assertIsNone(by_name["Kill 1"].finish)
        entries = {line["card"].name: line["card"] for line in self.draft["lines"] if line["card"].name == "Kill 1"}
        self.assertEqual(by_name["Kill 1"].number, entries["Kill 1"].collector_number)
        self.assertTrue(self.draft["text"].startswith("Commander\n1 Big Leader (TST) "))
        self.assertIn("\n\nDeck\n", self.draft["text"])

    def test_draft_is_cached(self):
        self.assertIs(self.builder.draft(self.commander["oracle_id"]), self.draft)

    def test_partner_pair(self):
        c, commander, table = big_collection()
        first = legend("Partner One", mana_cost="{2}{G}", identity=("G",), keywords=["Partner"])
        second = legend("Partner Two", mana_cost="{2}{U}", identity=("U",), keywords=["Partner"])
        c.add(first)
        c.add(second)
        builder = Builder(c, StubSimilarity(table), TAGS)
        draft = builder.draft(first["oracle_id"], second["oracle_id"])
        self.assertEqual([line["card"].name for line in draft["lines"][:2]], ["Partner One", "Partner Two"])
        self.assertEqual(sum(line["quantity"] for line in draft["lines"]), 100)
        parsed, _ = parse_decklist(draft["text"])
        self.assertEqual(sum(line.quantity for line in parsed if line.section == "commander"), 2)
        self.assertTrue(draft["stats"]["legality"]["legal"], draft["stats"]["legality"])


class RankTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeCollection()
        self.fits_well = legend("Fits Well", edhrec=900)
        self.fits_badly = legend("Fits Badly", edhrec=100)
        self.blue = legend("Blue Leader", mana_cost="{2}{U}", identity=("U",), edhrec=1)
        for card in (self.fits_well, self.fits_badly, self.blue):
            self.c.add(card)
        green = spells(self.c, 40, "Green", edhrec_from=100)
        spells(self.c, 5, "Blue", identity=("U",), cost="{U}", edhrec_from=300)
        self.c.add(basic("Forest"), quantity=40)
        self.c.add(basic("Island"), quantity=40)
        table = {
            self.fits_well["oracle_id"]: [(card["oracle_id"], 0.4) for card in green[:20]],
            self.fits_badly["oracle_id"]: [(card["oracle_id"], 0.13) for card in green[:20]],
            self.blue["oracle_id"]: [],
        }
        self.builder = Builder(self.c, StubSimilarity(table), TAGS)

    def test_order_by_coverage_then_fit(self):
        rows = self.builder.rank()
        self.assertEqual([row["card"].name for row in rows], ["Fits Well", "Fits Badly", "Blue Leader"])
        self.assertGreater(rows[0]["synergy_score"], rows[1]["synergy_score"])
        self.assertEqual(rows[0]["coverage"], rows[1]["coverage"])
        self.assertGreater(rows[1]["coverage"], rows[2]["coverage"])
        self.assertEqual(rows[0]["color_identity"], ["G"])
        self.assertEqual(len(rows[0]["synergy"]), 6)
        self.assertEqual(rows[0]["edhrec_rank"], 900)

    def test_filters_and_limit(self):
        self.assertEqual([r["card"].name for r in self.builder.rank(colors="U")], ["Blue Leader"])
        self.assertEqual([r["card"].name for r in self.builder.rank(colors=["G"])], ["Fits Well", "Fits Badly"])
        self.assertEqual([r["card"].name for r in self.builder.rank(name_query="badly")], ["Fits Badly"])
        self.assertEqual(len(self.builder.rank(limit=1)), 1)

    def test_below_threshold_counts_nothing(self):
        c = FakeCollection()
        leader = legend("Leader")
        c.add(leader)
        green = spells(c, 5, "Green")
        table = {leader["oracle_id"]: [(card["oracle_id"], builder_module.SYNERGY_THRESHOLD / 2) for card in green]}
        row = Builder(c, StubSimilarity(table), TAGS).rank()[0]
        self.assertEqual(row["synergy_score"], 0.0)
        self.assertEqual(row["synergy"], [])
        self.assertEqual(row["parts"]["synergy"]["have"], 5)       # still playable cards


class FastPathTests(unittest.TestCase):
    def test_fast_scores_match_similar(self):
        c = FakeCollection()
        words = ["flying", "trample", "draw a card", "destroy target creature", "create a 1/1 token",
                 "gain 2 life", "add {G}", "return target card", "sacrifice a creature", "counter target spell"]
        for i in range(60):
            text = ". ".join(words[j % len(words)] for j in (i, i * 3 + 1, i * 7 + 2)).capitalize()
            card = legend(f"Card {i}", oracle_text=text, edhrec=100 * i + 1) if i % 5 == 0 else make_card(
                f"Card {i}", oracle_text=text, edhrec=100 * i + 1)
            c.add(card, tags={"draw"} if i % 4 == 0 else ())
        index = SimilarityIndex(c)

        class OnlySimilar:
            similar = index.similar

        fast = Builder(c, index, TAGS)
        slow = Builder(c, OnlySimilar(), TAGS)
        self.assertIsNotNone(fast._fast)
        self.assertIsNone(slow._fast)
        candidates = set(c.by_oracle_id)
        for oracle_id in list(fast.commanders)[:6]:
            a = fast.fits(oracle_id, candidates, minimum=0.01)
            b = slow.fits(oracle_id, candidates, minimum=0.01)
            self.assertEqual([oid for _, _, oid in a], [oid for _, _, oid in b])
            for (fit_a, sim_a, _), (fit_b, sim_b, _) in zip(a, b):
                self.assertAlmostEqual(fit_a, fit_b)
                self.assertAlmostEqual(sim_a, sim_b)
        self.assertEqual([r["card"].name for r in fast.rank()], [r["card"].name for r in slow.rank()])


if __name__ == "__main__":
    unittest.main()
