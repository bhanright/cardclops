"""Unit tests for gallery.query on small hand-made cards.

    python -m unittest tests.test_query
"""
import itertools
import unittest

from gallery import collection, query
from gallery.query import QueryError, compile_query

_ids = itertools.count(1)


def make_card(name, **fields):
    n = next(_ids)
    card = {
        "id": f"card-{n}", "oracle_id": f"oracle-{n}", "name": name, "layout": "normal",
        "mana_cost": "", "cmc": 0, "type_line": "", "oracle_text": "", "colors": [],
        "color_identity": [], "keywords": [], "rarity": "common", "set": "tst", "set_name": "Test Set",
        "set_type": "expansion", "collector_number": str(n), "released_at": "2020-01-01",
        "artist": "Some Artist", "legalities": {}, "prices": {}, "games": ["paper"], "frame": "2015",
        "border_color": "black", "reserved": False, "game_changer": False, "promo": False,
        "full_art": False, "reprint": False,
    }
    card.update(fields)
    return card


def make_entry(card, tags=(), **holding):
    row = {"row_id": next(_ids), "scryfall_id": card["id"], "finish": "normal", "quantity": 1,
           "condition": "near_mint", "language": "en", "purchase_price": None,
           "added_at": "2024-06-01T12:00:00.000Z", "misprint": 0}
    row.update(holding)
    oracle_id = card.get("oracle_id") or card["card_faces"][0]["oracle_id"]
    return collection.build_entry(row, card, {oracle_id: frozenset(tags)})


TAGS = collection.TagIndex(
    descendants={
        "removal": frozenset({"removal", "removal-burn", "removal-destroy"}),
        "removal-burn": frozenset({"removal-burn"}),
        "removal-destroy": frozenset({"removal-destroy"}),
        "ramp": frozenset({"ramp"}),
    },
    labels={},
)

LEGAL_EVERYWHERE = {f: "legal" for f in ("standard", "modern", "legacy", "vintage", "pauper", "commander")}


def build_cards():
    E = {}
    E["bolt"] = make_entry(make_card(
        "Lightning Bolt", mana_cost="{R}", cmc=1, type_line="Instant",
        oracle_text="Lightning Bolt deals 3 damage to any target.", colors=["R"], color_identity=["R"],
        rarity="common", set="lea", set_type="core", collector_number="161", released_at="1993-08-05",
        artist="Christopher Rush", legalities=dict(LEGAL_EVERYWHERE, standard="not_legal"),
        prices={"usd": "1.50", "eur": "1.20", "tix": "0.05"}, games=["paper", "mtgo"], frame="1993",
        flavor_text="The sparkmage shrieked."),
        tags={"removal-burn"}, quantity=4, purchase_price=1.0, added_at="2025-03-01T10:00:00.000Z")
    E["bears"] = make_entry(make_card(
        "Grizzly Bears", mana_cost="{1}{G}", cmc=2, type_line="Creature — Bear", power="2", toughness="2",
        colors=["G"], color_identity=["G"], legalities=LEGAL_EVERYWHERE, prices={"usd": "0.10"},
        games=["paper", "arena"]))
    E["serra"] = make_entry(make_card(
        "Serra Angel", mana_cost="{3}{W}{W}", cmc=5, type_line="Creature — Angel", power="4", toughness="4",
        oracle_text="Flying, vigilance", keywords=["Flying", "Vigilance"], colors=["W"],
        color_identity=["W"], rarity="uncommon", legalities=LEGAL_EVERYWHERE, reprint=True,
        watermark="orzhov"), condition="light_played")
    E["goyf"] = make_entry(make_card(
        "Tarmogoyf", mana_cost="{1}{G}", cmc=2, type_line="Creature — Lhurgoyf", power="*",
        toughness="1+*", oracle_text="Tarmogoyf's power is equal to the number of card types among "
        "cards in all graveyards and its toughness is equal to that number plus 1.",
        colors=["G"], color_identity=["G"], rarity="mythic", game_changer=False))
    E["delver"] = make_entry(make_card(
        "Delver of Secrets // Insectile Aberration", layout="transform", mana_cost="", cmc=1,
        type_line="Creature — Human Wizard // Creature — Human Insect", colors=None,
        color_identity=["U"], keywords=["Flying", "Transform"], oracle_text=None,
        card_faces=[
            {"name": "Delver of Secrets", "mana_cost": "{U}", "type_line": "Creature — Human Wizard",
             "oracle_text": "At the beginning of your upkeep, look at the top card of your library. "
                            "You may reveal that card. If an instant or sorcery card is revealed "
                            "this way, transform this creature.",
             "colors": ["U"], "power": "1", "toughness": "1"},
            {"name": "Insectile Aberration", "mana_cost": "", "type_line": "Creature — Human Insect",
             "oracle_text": "Flying", "colors": ["U"], "power": "3", "toughness": "2"},
        ]))
    E["cathar"] = make_entry(make_card(
        "Brutal Cathar // Moonrage Brute", layout="transform", cmc=3, colors=None, color_identity=["R", "W"],
        type_line="Creature — Human Soldier Werewolf // Creature — Werewolf",
        card_faces=[
            {"name": "Brutal Cathar", "mana_cost": "{2}{W}", "type_line": "Creature — Human Soldier Werewolf",
             "oracle_text": "When this creature enters or transforms into Brutal Cathar, exile target "
                            "creature an opponent controls until this creature leaves the battlefield.",
             "colors": ["W"], "power": "2", "toughness": "2"},
            {"name": "Moonrage Brute", "mana_cost": "", "type_line": "Creature — Werewolf",
             "oracle_text": "First strike", "colors": ["R"], "power": "3", "toughness": "3"},
        ]))
    E["fireice"] = make_entry(make_card(
        "Fire // Ice", layout="split", mana_cost="{1}{R} // {1}{U}", cmc=4, type_line="Instant // Instant",
        colors=["R", "U"], color_identity=["R", "U"], image_uris={"normal": "x"},
        card_faces=[
            {"name": "Fire", "mana_cost": "{1}{R}", "type_line": "Instant",
             "oracle_text": "Fire deals 2 damage divided as you choose among one or two targets."},
            {"name": "Ice", "mana_cost": "{1}{U}", "type_line": "Instant",
             "oracle_text": "Tap target permanent.\nDraw a card."},
        ]))
    E["finks"] = make_entry(make_card(
        "Kitchen Finks", mana_cost="{1}{G/W}{G/W}", cmc=3, type_line="Creature — Ouphe", power="3",
        toughness="2", oracle_text="When this creature enters, you gain 2 life.\nPersist (When this "
        "creature dies, if it had no -1/-1 counters on it, return it to the battlefield under its "
        "owner's control with a -1/-1 counter on it.)", keywords=["Persist"], colors=["G", "W"],
        color_identity=["G", "W"], rarity="uncommon"))
    E["pod"] = make_entry(make_card(
        "Birthing Pod", mana_cost="{3}{G/P}", cmc=4, type_line="Artifact",
        oracle_text="({G/P} can be paid with either {G} or 2 life.)\n{1}{G/P}, {T}, Sacrifice a creature: "
        "Search your library for a creature card with mana value equal to 1 plus the sacrificed "
        "creature's mana value, put that card onto the battlefield, then shuffle.",
        colors=["G"], color_identity=["G"], rarity="rare", legalities={"modern": "banned"}))
    E["jace"] = make_entry(make_card(
        "Jace, the Mind Sculptor", mana_cost="{2}{U}{U}", cmc=4, type_line="Legendary Planeswalker — Jace",
        loyalty="3", oracle_text="+2: Look at the top card of target player's library.", colors=["U"],
        color_identity=["U"], rarity="mythic", legalities={"legacy": "legal", "commander": "legal"}))
    E["atraxa"] = make_entry(make_card(
        "Atraxa, Praetors' Voice", mana_cost="{G}{W}{U}{B}", cmc=4,
        type_line="Legendary Creature — Phyrexian Angel Horror", power="4", toughness="4",
        oracle_text="Flying, vigilance, deathtouch, lifelink\nAt the beginning of your end step, proliferate.",
        keywords=["Flying", "Vigilance", "Deathtouch", "Lifelink", "Proliferate"],
        colors=["W", "U", "B", "G"], color_identity=["W", "U", "B", "G"], rarity="mythic",
        legalities={"commander": "legal"}, prices={"usd_foil": "20.00", "usd": "15.00"}, game_changer=True,
        promo=True, frame_effects=["legendary"], released_at="2016-11-11", set="c16", set_type="commander"),
        tags={"ramp"}, finish="foil", purchase_price=10.0, language="fr",
        added_at="2026-02-01T00:00:00.000Z")
    E["fountain"] = make_entry(make_card(
        "Hallowed Fountain", type_line="Land — Plains Island",
        oracle_text="({T}: Add {W} or {U}.)\nAs Hallowed Fountain enters, you may pay 2 life. If you "
        "don't, it enters tapped.", color_identity=["W", "U"], produced_mana=["W", "U"], rarity="rare",
        full_art=True, border_color="borderless"))
    E["mox"] = make_entry(make_card(
        "Mox Pearl", mana_cost="{0}", cmc=0, type_line="Artifact", oracle_text="{T}: Add {W}.",
        color_identity=["W"], rarity="rare", reserved=True, produced_mana=["W"],
        legalities={"vintage": "restricted", "legacy": "banned"}, released_at="1993-08-05", frame="1993"),
        misprint=1, condition="poor", finish="etched")
    E["saproling"] = make_entry(make_card(
        "Saproling", layout="token", type_line="Token Creature — Saproling", power="1", toughness="1",
        colors=["G"], color_identity=["G"], set="tdom", set_type="token"))
    E["benalia"] = make_entry(make_card(
        "History of Benalia", mana_cost="{1}{W}{W}", cmc=3, type_line="Legendary Enchantment — Saga",
        oracle_text="(As this Saga enters and after your draw step, add a lore counter.)\nI, II — Create a "
        "2/2 white Knight creature token with vigilance.\nIII — Knights you control get +2/+1 until end "
        "of turn.", colors=["W"], color_identity=["W"], rarity="mythic", layout="saga"))
    E["giant"] = make_entry(make_card(
        "Bonecrusher Giant // Stomp", layout="adventure", mana_cost="{2}{R} // {1}{R}", cmc=3,
        type_line="Creature — Giant // Instant — Adventure", power="4", toughness="3", colors=["R"],
        color_identity=["R"], image_uris={"normal": "x"},
        card_faces=[
            {"name": "Bonecrusher Giant", "mana_cost": "{2}{R}", "type_line": "Creature — Giant",
             "oracle_text": "Whenever this creature becomes the target of a spell, this creature deals 2 "
                            "damage to that spell's controller.", "power": "4", "toughness": "3"},
            {"name": "Stomp", "mana_cost": "{1}{R}", "type_line": "Instant — Adventure",
             "oracle_text": "Damage can't be prevented this turn. Stomp deals 2 damage to any target."},
        ]))
    E["cleric"] = make_entry(make_card(
        "Dandân Cleric", mana_cost="{W}", cmc=1, type_line="Creature — Human Cleric", power="1",
        toughness="1", oracle_text="Lifelink", keywords=["Lifelink"], colors=["W"], color_identity=["W"]))
    return E


class QueryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.E = build_cards()
        cls.by_entry = {id(entry): key for key, entry in cls.E.items()}

    def keys(self, text):
        q = compile_query(text, TAGS)
        return {key for key, entry in self.E.items() if q.matches(entry)}

    def check(self, text, *expected):
        self.assertEqual(self.keys(text), set(expected), text)

    def error(self, text, fragment=None):
        with self.assertRaises(QueryError, msg=text) as caught:
            compile_query(text, TAGS)
        if fragment:
            self.assertIn(fragment, str(caught.exception))
        return caught.exception


class TestBoolean(QueryTestCase):
    def test_empty_matches_everything(self):
        for text in ("", "   ", None):
            q = compile_query(text, TAGS)
            self.assertTrue(all(q.matches(e) for e in self.E.values()))
            self.assertEqual(q.describe(), "all cards")

    def test_bare_words_are_anded_name_searches(self):
        self.check("lightning", "bolt")
        self.check("bolt lightning", "bolt")
        self.check("LIGHTNING bolt", "bolt")
        self.check("lightning giant")

    def test_quoted_phrase(self):
        self.check('"lightning bolt"', "bolt")
        self.check('"bolt lightning"')

    def test_exact_name(self):
        self.check('!"Lightning Bolt"', "bolt")
        self.check("!lightning")
        self.check("!fire", "fireice")          # a face's name counts
        self.check('!"fire // ice"', "fireice")

    def test_name_folds_accents_and_punctuation(self):
        self.check("dandan", "cleric")
        self.check("praetors voice", "atraxa")
        self.check("praetors' voice", "atraxa")

    def test_negation(self):
        everything = set(self.E)
        self.assertEqual(self.keys("-t:creature"), everything - self.keys("t:creature"))
        self.assertEqual(self.keys("-lightning"), everything - {"bolt"})
        self.check("-(c:r or c:g) t:instant")
        self.check("-(c:r or c:g) t:creature", "serra", "delver", "cleric")
        self.assertEqual(self.keys("-(c:r or c:g)"), everything - self.keys("c:r or c:g"))

    def test_or_binds_looser_than_and(self):
        self.check("t:instant or t:creature c:g", "bolt", "fireice", "giant", "bears", "goyf", "finks",
                   "saproling", "atraxa")
        self.check("t:creature c:g or t:instant", "bolt", "fireice", "giant", "bears", "goyf", "finks",
                   "saproling", "atraxa")
        self.check("t:instant OR t:planeswalker", "bolt", "fireice", "giant", "jace")

    def test_parentheses(self):
        self.check("(t:instant or t:creature) c:g", "bears", "goyf", "finks", "saproling", "atraxa")
        self.check("((t:instant))", "bolt", "fireice", "giant")
        self.check("t:instant (lightning or fire)", "bolt", "fireice")

    def test_explicit_and(self):
        self.check("t:instant and c:r", "bolt", "fireice", "giant")

    def test_colon_after_word_is_a_name(self):
        compile_query("circle of protection: red", TAGS)  # no error

    def test_errors(self):
        self.error("(t:instant", "never closed")
        self.error("t:instant)", "no matching")
        self.error('"lightning', "closing quote")
        self.error("o:/destroy", "closing /")
        self.error("o:/(/", "Invalid regular expression")
        self.error("or t:instant", "before it")
        self.error("t:instant or", "after it")
        self.error("()", "Empty parentheses")
        self.error("t:", "needs a value")
        self.error("t>creature", "does not support")
        self.error("s:/lea/", "does not take a regular expression")

    def test_unknown_keyword_suggests(self):
        exc = self.error("tpye:creature", "Unknown keyword")
        self.assertIn("type:", str(exc))
        exc = self.error("oracel:draw")
        self.assertIn("oracle:", str(exc))
        self.assertIsNotNone(exc.position)
        self.assertIn("character 1", str(exc))

    def test_error_position(self):
        exc = self.error("t:creature  zzz:1")
        self.assertEqual(exc.position, 12)

    def test_describe(self):
        text = compile_query("c:r t:instant -o:draw", TAGS).describe()
        self.assertIn("the colors include red", text)
        self.assertIn("the type includes “instant”", text)
        self.assertIn("the text doesn't include “draw”", text)
        text = compile_query("t:elf or mv<=2", TAGS).describe()
        self.assertIn(" or ", text)
        self.assertIn("the mana value is at most 2", text)
        text = compile_query("-(c:r or c:g)", TAGS).describe()
        self.assertIn("don't include", text)


class TestText(QueryTestCase):
    def test_name_keyword(self):
        self.check("name:bolt", "bolt")
        self.check("n:bolt", "bolt")
        self.check("name:/^fire/", "fireice")
        self.check("name:/\\bbolt$/", "bolt")
        self.check("name!=fire t:instant", "bolt", "giant")
        self.check("/^fire/", "fireice")   # a bare regex searches names
        self.check("fire // ice", "fireice")

    def test_oracle(self):
        self.check('o:"any target"', "bolt", "giant")
        self.check("oracle:proliferate", "atraxa")
        self.check("o:/deals \\d damage/", "bolt", "fireice", "giant")
        self.check('o:"draw a card"', "fireice")     # faces are searched

    def test_oracle_ignores_reminder_text_but_fulloracle_does_not(self):
        self.check('o:"add {w}"', "mox")
        self.check('fo:"add {w}"', "mox", "fountain")
        self.check('fulloracle:"-1/-1 counter"', "finks")
        self.check('o:"-1/-1 counter"')

    def test_tilde_is_the_cards_name_or_this_object(self):
        self.check('o:"~ deals 3 damage"', "bolt")
        self.check('o:"when ~ enters"', "finks", "cathar")   # "this creature" wording
        self.check('o:"~ deals 2 damage"', "fireice", "giant")
        self.check("o:/^~ deals/", "bolt", "fireice")   # Stomp's is mid-line

    def test_type(self):
        self.check("t:legendary", "jace", "atraxa", "benalia")
        self.check("t:legend", "jace", "atraxa", "benalia")
        self.check('type:"legendary creature"', "atraxa")
        self.check("t:/^legendary (creature|planeswalker)/", "jace", "atraxa")
        self.check("t:adventure", "giant")

    def test_flavor_artist_watermark_lore(self):
        self.check("ft:sparkmage", "bolt")
        self.check("a:rush", "bolt")
        self.check('artist:"christopher rush"', "bolt")
        self.check("wm:orzhov", "serra")
        self.check("lore:sparkmage", "bolt")


class TestColors(QueryTestCase):
    def test_color_contains(self):
        self.check("c:r", "bolt", "fireice", "giant", "cathar")
        self.check("c:rg")
        self.check("c:gw", "finks", "atraxa")
        self.check("c>=gw", "finks", "atraxa")
        self.check("color:red t:instant", "bolt", "fireice", "giant")

    def test_color_exact_subset_superset(self):
        self.check("c=g", "bears", "goyf", "pod", "saproling")
        self.check("c<=g t:creature", "bears", "goyf", "saproling")
        self.check("c<g", "fountain", "mox")
        self.check("c>g", "finks", "atraxa")
        self.check("c!=g t:creature", "serra", "delver", "cathar", "finks", "atraxa", "giant", "cleric")

    def test_colorless_and_multicolor(self):
        self.check("c:c", "fountain", "mox")
        self.check("c:colorless", "fountain", "mox")
        self.check("c:m", "fireice", "finks", "atraxa")
        self.check("c:multicolor -t:instant", "finks", "atraxa")
        self.check("c!=m c:c", "fountain", "mox")

    def test_color_count(self):
        self.check("c=2", "fireice", "finks")
        self.check("c>=3", "atraxa")
        self.check("c=0", "fountain", "mox")

    def test_nicknames(self):
        self.check("c:selesnya", "finks", "atraxa")
        self.check("c:izzet", "fireice")
        self.check("c>=abzan", "atraxa")
        self.check("c=white t:angel", "serra")

    def test_faces_are_separate_when_the_card_has_no_colors_of_its_own(self):
        self.check("c:w t:werewolf", "cathar")
        self.check("c:r t:werewolf", "cathar")
        self.check("c>=2 t:werewolf")

    def test_identity(self):
        self.check("id:esper t:creature", "serra", "delver", "cleric")
        self.check("id<=esper -t:creature", "jace", "fountain", "mox", "benalia")
        self.check("id=wubg", "atraxa")
        self.check("identity>=wu", "fountain", "atraxa")
        self.check("ci:c")
        self.check("commander:azorius -t:creature", "jace", "fountain", "mox", "benalia")
        self.check("id>=rw", "cathar")

    def test_bad_color(self):
        self.error("c:purple", "not a color")
        self.error("c:azorious", "azorius")


class TestManaAndStats(QueryTestCase):
    def test_mana_contains(self):
        self.check("m:{R}", "bolt", "fireice", "giant")
        self.check("m:R t:creature", "giant")
        self.check("m:WW", "serra", "benalia")
        self.check("mana:{G/W}", "finks")
        self.check("m:{W/G}", "finks")             # hybrid order doesn't matter
        self.check("m:{G/W}{G/W}", "finks")
        self.check("m:{G/P}", "pod")
        self.check("m:2WW", "serra")               # {3}{W}{W} includes two generic

    def test_mana_comparisons(self):
        self.check("m={R}", "bolt")
        self.check("m={1}{G}", "bears", "goyf")
        self.check("m>{R}", "fireice", "giant")
        self.check("m<{3}{W}{W}{W} t:creature", "serra", "cleric", "cathar")   # no cost, no match: tokens, back faces
        self.check("m<{W}", "mox")
        self.check("m!={R} t:instant", "fireice", "giant")

    def test_bad_mana(self):
        self.error("m:G!", "not a mana cost")
        self.error("m:{}", "Empty mana symbol")

    def test_mana_value(self):
        self.check("mv=1", "bolt", "delver", "cleric")
        self.check("cmc>=5", "serra")
        self.check("manavalue>4", "serra")
        self.check("mv:odd t:creature", "serra", "delver", "cathar", "finks", "giant", "cleric")
        self.check("mv:even t:instant", "fireice")
        self.check("mv!=0 t:artifact", "pod")
        self.error("mv>x", "needs a number")

    def test_power_toughness_loyalty(self):
        self.check("pow>=4", "serra", "atraxa", "giant")
        self.check("power=3", "delver", "cathar", "finks")        # back faces count
        self.check("tou<2", "delver", "saproling", "cleric")
        self.check("toughness>=4", "serra", "atraxa")
        self.check("pow>=0 t:lhurgoyf")                          # * is not a number
        self.check("loy=3", "jace")
        self.check("loyalty>3")

    def test_stat_against_stat(self):
        self.check("pow>tou", "finks", "giant", "delver", "cathar")   # delver 3/2 back, cathar 3 vs 2
        self.check("pow<tou", "delver", "cathar")   # any face against any face
        self.check("pow=tou t:angel", "serra", "atraxa")

    def test_pt_total_uses_the_front_face(self):
        self.check("pt=4 t:bear", "bears")
        self.check("powtou=2 t:wizard", "delver")
        self.check("pt=5 t:wizard")


class TestPrintAndLegality(QueryTestCase):
    def test_rarity(self):
        self.check("r:mythic", "goyf", "jace", "atraxa", "benalia")
        self.check("r>=rare -r:mythic", "pod", "fountain", "mox")
        self.check("rarity<uncommon t:instant", "bolt", "fireice", "giant")
        self.check("r:m t:planeswalker", "jace")
        self.error("r:epic", "not a rarity")

    def test_set_number_type(self):
        self.check("s:lea", "bolt")
        self.check("e:LEA", "bolt")
        self.check("set:c16", "atraxa")
        self.check("edition:c16", "atraxa")
        self.check("cn:161", "bolt")
        self.check("number>160 s:lea", "bolt")
        self.check("st:commander", "atraxa")
        self.check("st:core", "bolt")

    def test_year_and_date(self):
        self.check("year=1993", "bolt", "mox")
        self.check("year<2000", "bolt", "mox")
        self.check("date>=2016-11-11 -year:2020", "atraxa")
        self.check("date<1994", "bolt", "mox")
        self.check("date:2016-11", "atraxa")
        self.error("date>last-week", "not a date")

    def test_formats(self):
        self.check("f:modern", "bolt", "bears", "serra")
        self.check("format:vintage", "bolt", "bears", "serra", "mox")    # restricted is still legal
        self.check("legal:commander t:legendary", "jace", "atraxa")
        self.check("banned:modern", "pod")
        self.check("banned:legacy", "mox")
        self.check("restricted:vintage", "mox")
        self.check("f:edh t:legendary", "jace", "atraxa")
        exc = self.error("f:moddern", "not a format")
        self.assertIn("modern", str(exc))

    def test_keywords(self):
        self.check("k:flying", "serra", "delver", "atraxa")
        self.check("kw:persist", "finks")
        self.check('keyword:"flying"', "serra", "delver", "atraxa")
        self.check("k:fly")

    def test_tags_include_descendants(self):
        self.check("otag:removal", "bolt")
        self.check("otag:removal-burn", "bolt")
        self.check("function:ramp", "atraxa")
        self.check("oracletag:removal-destroy")
        q = compile_query("otag:no-such-tag", TAGS)
        self.assertTrue(q.warnings)
        self.assertFalse(any(q.matches(e) for e in self.E.values()))

    def test_game_produces_border_frame(self):
        self.check("game:mtgo", "bolt")
        self.check("game:arena", "bears")
        self.check("produces:w", "fountain", "mox")
        self.check("produces=wu", "fountain")
        self.check("border:borderless", "fountain")
        self.check("frame:1993", "bolt", "mox")
        self.check("frame:legendary", "atraxa")
        self.error("game:xbox", "not a valid")


class TestIs(QueryTestCase):
    def test_finish(self):
        self.check("is:foil", "atraxa")
        self.check("is:etched", "mox")
        self.assertEqual(self.keys("is:nonfoil"), set(self.E) - {"atraxa", "mox"})
        self.assertEqual(self.keys("not:foil"), set(self.E) - {"atraxa"})
        self.assertEqual(self.keys("-is:foil"), set(self.E) - {"atraxa"})
        self.check("-not:foil", "atraxa")

    def test_layouts(self):
        self.check("is:dfc", "delver", "cathar")
        self.check("is:transform", "delver", "cathar")
        self.check("is:mdfc")
        self.check("is:split", "fireice")
        self.check("is:adventure", "giant")
        self.check("is:saga", "benalia")
        self.check("is:token", "saproling")

    def test_permanent_spell_historic(self):
        self.assertEqual(self.keys("is:permanent"), set(self.E) - {"bolt", "fireice"})
        self.assertEqual(self.keys("is:spell"), set(self.E) - {"fountain", "saproling"})
        self.check("is:historic", "jace", "atraxa", "pod", "mox", "benalia")

    def test_creature_flavors(self):
        self.check("is:commander", "atraxa")
        self.check("is:vanilla", "bears", "saproling")
        self.check("is:frenchvanilla", "serra", "cleric")
        self.check("is:bear", "bears")
        self.check("is:party", "delver", "cleric")

    def test_card_flags(self):
        self.check("is:reserved", "mox")
        self.check("is:gamechanger", "atraxa")
        self.check("is:game_changer", "atraxa")
        self.check("is:promo", "atraxa")
        self.check("is:fullart", "fountain")
        self.check("is:full_art", "fountain")
        self.check("is:reprint", "serra")
        self.check("is:misprint", "mox")
        self.check("is:hybrid", "finks")
        self.check("is:phyrexian", "pod")

    def test_unsupported_is_is_ignored_with_a_warning(self):
        q = compile_query("is:fetchland t:instant", TAGS)
        self.assertTrue(q.warnings)
        self.check("is:fetchland t:instant", "bolt", "fireice", "giant")
        self.check("not:fetchland t:instant", "bolt", "fireice", "giant")
        q = compile_query("is:foill", TAGS)
        self.assertIn("foil", q.warnings[0])


class TestHoldings(QueryTestCase):
    def test_quantity_condition_language(self):
        self.check("qty>=4", "bolt")
        self.check("quantity=1 t:instant", "fireice", "giant")
        self.check("cond:poor", "mox")
        self.check("condition:lp", "serra")
        self.check("cond<nm", "serra", "mox")
        self.assertEqual(self.keys("cond:nm"), set(self.E) - {"serra", "mox"})
        self.check("lang:fr", "atraxa")
        self.check("language:french", "atraxa")
        self.check("lang:any t:planeswalker", "jace")
        self.error("cond:shiny", "not a condition")

    def test_added_and_prices(self):
        self.check("added>=2025-01-01", "bolt", "atraxa")
        self.check("added:2025", "bolt")
        self.check("added<2024-07", *(set(self.E) - {"bolt", "atraxa"}))
        self.check("usd<1", "bears")
        self.check("usd>10", "atraxa")               # the foil price, because the held copy is foil
        self.check("eur>1", "bolt")
        self.check("tix>0", "bolt")
        self.check("paid>5", "atraxa")
        self.check("gain>5", "atraxa")
        self.check("gain>0.4 gain<0.6", "bolt")
        self.check("usd>=0 t:angel", "atraxa")       # Serra Angel has no price, so never matches


class TestDecks(QueryTestCase):
    def setUp(self):
        for entry in self.E.values():
            entry.used, entry.decks = 0, ()
        bolt, serra, jace = self.E["bolt"], self.E["serra"], self.E["jace"]
        bolt.used, bolt.decks = 3, (("Izzet Tempo", "active"), ("Burn Pile", "inactive"))
        serra.decks = (("Mono-White Angels", "inactive"),)
        jace.used, jace.decks = 1, (("Izzet Tempo", "active"),)

    def tearDown(self):
        for entry in self.E.values():
            entry.used, entry.decks = 0, ()

    def test_deck_name(self):
        self.check("deck:izzet", "bolt", "jace")
        self.check("deck:burn", "bolt")                    # inactive decks count
        self.check('deck:"white angels"', "serra")
        self.check("deck:IZZET t:instant", "bolt")
        self.check("deck!=izzet deck:angels", "serra")
        self.check("deck:nothing")
        self.assertIn('a deck named “izzet”', compile_query("deck:izzet", TAGS).describe())

    def test_deck_any_means_an_active_deck(self):
        self.check("deck:any", "bolt", "jace")
        self.assertEqual(self.keys("-deck:any"), set(self.E) - {"bolt", "jace"})
        self.check('deck:"any"')                          # quoted: a deck literally named "any"

    def test_used_and_spare(self):
        self.check("used>0", "bolt", "jace")
        self.check("used=3", "bolt")
        self.check("used>=1 used<3", "jace")
        self.check("spare>=1 t:instant", "bolt", "fireice", "giant")   # bolt: 4 owned, 3 used
        self.check("spare=0", "jace")
        self.check("spare=1 qty>=4", "bolt")
        self.check("is:used", "bolt", "jace")
        self.assertEqual(self.keys("is:spare"), set(self.E) - {"jace"})
        self.assertEqual(self.keys("not:used"), set(self.E) - {"bolt", "jace"})

    def test_deck_fields_are_read_live_after_prepare(self):
        query.prepare(self.E.values())
        q = compile_query("deck:any used>0", TAGS)
        self.assertFalse(q.matches(self.E["serra"]))
        self.E["serra"].used, self.E["serra"].decks = 2, (("Angels", "active"),)
        self.assertTrue(q.matches(self.E["serra"]))           # same compiled query, no reload
        self.assertTrue(compile_query("spare<0", TAGS).matches(self.E["serra"]))


class TestDisplayKeywords(QueryTestCase):
    def test_order_unique_direction(self):
        q = compile_query("t:creature order:usd", TAGS)
        self.assertEqual(q.order, ("usd", "desc"))
        self.assertIsNone(q.unique)
        q = compile_query("order:name direction:desc unique:prints", TAGS)
        self.assertEqual(q.order, ("name", "desc"))
        self.assertEqual(q.unique, "prints")
        self.assertTrue(all(q.matches(e) for e in self.E.values()))
        q = compile_query("order:cmc", TAGS)
        self.assertEqual(q.order, ("cmc", "asc"))
        q = compile_query("order:spare", TAGS)
        self.assertEqual(q.order, ("spare", "desc"))
        q = compile_query("t:instant", TAGS)
        self.assertIsNone(q.order)
        self.error("unique:everything", "cards, prints or art")
        self.error("order:shininess", "Can't order")

    def test_display_terms_do_not_filter_inside_groups(self):
        self.check("(t:instant or order:usd)", "bolt", "fireice", "giant")
        self.check("-unique:art t:instant", "bolt", "fireice", "giant")
        self.check("t:instant include:extras prefer:newest display:grid", "bolt", "fireice", "giant")

    def test_unsupported_scryfall_keyword_warns(self):
        q = compile_query("t:instant cube:vintage", TAGS)
        self.assertTrue(q.warnings)
        self.check("t:instant cube:vintage", "bolt", "fireice", "giant")


class TestCaches(QueryTestCase):
    def test_prepare_and_view_cache(self):
        query.prepare(self.E.values())
        self.check("o:/damage/ t:instant", "bolt", "fireice", "giant")

    def test_view_is_not_shared_between_different_entries_of_a_card(self):
        card = make_card("Shared Card", type_line="Instant")
        first = make_entry(card)
        second = make_entry(dict(card, name="Renamed Card"))
        q = compile_query("renamed", TAGS)
        self.assertFalse(q.matches(first))
        self.assertTrue(q.matches(second))


if __name__ == "__main__":
    unittest.main()
