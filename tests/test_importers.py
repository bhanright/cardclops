"""Collection CSV importers (gallery/importers.py).

    python -m unittest tests.test_importers

The sample headers and rows follow real exports of each app (see the module docstring); the
card cache is a handful of hand-made printings. The last test reads the owner's real ManaBox
export and card cache when they are on this computer, and is skipped otherwise.
"""
import csv
import io
import os
import sqlite3
import time
import unittest
from pathlib import Path

from gallery import db, ingest
from gallery.paths import CACHE_DATABASE_PATH
from gallery.importers import detect_format, parse_collection

# (set, collector number, scryfall id, name); oracle ids are derived from the name.
PRINTINGS = [
    ("mid", "2", "id-farmhand", "Ambitious Farmhand // Seasoned Cathar"),
    ("cmm", "509", "id-tutor-cmm", "Demonic Tutor"),
    ("plst", "DDC-49", "id-tutor-plst", "Demonic Tutor"),
    ("m11", "149", "id-bolt-m11", "Lightning Bolt"),
    ("2x2", "117", "id-bolt-2x2", "Lightning Bolt"),
    ("apc", "128", "id-fire-ice", "Fire // Ice"),
    ("tmh2", "14", "id-clue", "Clue"),
    ("sld", "VS", "id-seer", "Viscera Seer"),
    ("gk2", "1", "id-isperia", "Isperia, Supreme Judge"),
    ("ltr", "103", "id-bowmasters", "Orcish Bowmasters"),
    ("ltr", "433", "id-bowmasters-borderless", "Orcish Bowmasters"),
    ("csp", "8", "id-jotun", "Jötun Grunt"),
    ("znr", "266", "id-plains-266", "Plains"),
    ("znr", "267", "id-plains-267", "Plains"),
    ("eld", "39", "id-borrower", "Brazen Borrower // Petty Theft"),
    ("war", "1★", "id-karn-star", "Karn, the Great Creator"),
]
SETS = [
    ("mid", "Innistrad: Midnight Hunt", "expansion", "2021-09-24"), ("cmm", "Commander Masters", "masters", "2023-08-04"),
    ("plst", "The List", "masters", "2020-03-01"), ("m11", "Magic 2011", "core", "2010-07-16"),
    ("2x2", "Double Masters 2022", "masters", "2022-07-08"), ("apc", "Apocalypse", "expansion", "2001-06-04"),
    ("tmh2", "Modern Horizons 2 Tokens", "token", "2021-06-18"), ("mh2", "Modern Horizons 2", "draft_innovation", "2021-06-18"),
    ("sld", "Secret Lair Drop", "box", "2019-12-02"), ("gk2", "RNA Guild Kit", "masters", "2019-11-08"),
    ("ltr", "The Lord of the Rings: Tales of Middle-earth", "expansion", "2023-06-23"), ("csp", "Coldsnap", "expansion", "2006-07-21"),
    ("znr", "Zendikar Rising", "expansion", "2020-09-25"), ("eld", "Throne of Eldraine", "expansion", "2019-10-04"),
    ("war", "War of the Spark", "expansion", "2019-05-03"), ("m14", "Magic 2014", "core", "2013-07-19"),
]
# Representative printing per card (oracle_cards leaves tokens out, as Scryfall's bulk data does here).
REPRESENTATIVE = {
    "Ambitious Farmhand // Seasoned Cathar": "id-farmhand", "Demonic Tutor": "id-tutor-cmm",
    "Lightning Bolt": "id-bolt-2x2", "Fire // Ice": "id-fire-ice", "Viscera Seer": "id-seer",
    "Isperia, Supreme Judge": "id-isperia", "Orcish Bowmasters": "id-bowmasters", "Jötun Grunt": "id-jotun",
    "Plains": "id-plains-266", "Brazen Borrower // Petty Theft": "id-borrower", "Karn, the Great Creator": "id-karn-star",
}

def holdings_columns():
    """The columns ingest.py stores for each holding, so the importer and the store cannot drift."""
    return list(ingest.HOLDING_COLUMNS)


def make_cache():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(db.SCHEMA)
    connection.executemany(
        "INSERT INTO printings (set_code, collector_number, scryfall_id, oracle_id, name) VALUES (?, ?, ?, ?, ?)",
        [(s, n, i, "oracle-" + name, name) for s, n, i, name in PRINTINGS])
    connection.executemany("INSERT INTO sets (code, name, set_type, released_at) VALUES (?, ?, ?, ?)", SETS)
    connection.executemany(
        "INSERT INTO oracle_cards (oracle_id, name, name_folded, scryfall_id) VALUES (?, ?, ?, ?)",
        [("oracle-" + name, name, name.lower(), scryfall_id) for name, scryfall_id in REPRESENTATIVE.items()])
    return connection


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.connection = make_cache()
        self.addCleanup(self.connection.close)

    def parse(self, text, filename="export.csv", **kwargs):
        result = parse_collection(filename, text, self.connection, **kwargs)
        for row in result["rows"]:
            self.assertEqual(list(row), holdings_columns())
        return result

    def brief(self, result):
        return [(r["scryfall_id"], r["finish"], r["quantity"], r["condition"], r["language"]) for r in result["rows"]]

    def test_manabox(self):
        text = (
            "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,"
            "Misprint,Altered,Signed,Condition,Language,Proxy,Purchase price currency,Added\n"
            "Ambitious Farmhand // Seasoned Cathar,MID,Innistrad: Midnight Hunt,2,foil,uncommon,2,1001,id-farmhand,0.20,"
            "false,false,false,light_played,de,false,USD,2025-02-27T19:58:37.296Z\n"
            "Demonic Tutor,CMM,Commander Masters,509,etched,mythic,1,1002,id-tutor-cmm,,true,false,false,mint,en,false,USD,"
            "2025-03-01T10:00:00.000Z\n"
            "Lightning Bolt,M11,Magic 2011,149,normal,common,4,1003,id-bolt-m11,1.5,false,false,false,near_mint,zh_TW,false,"
            "EUR,2025-03-02T10:00:00.000Z\n"
            # A Scryfall ID the cache doesn't know (a non-English printing): the set and number still find it.
            "Lightning Bolt,2X2,Double Masters 2022,117,normal,uncommon,1,1004,00000000-0000-0000-0000-000000000000,,"
            "false,false,false,poor,ja,false,USD,2025-03-03T10:00:00.000Z\n"
            "Mystery Card,ZZZ,Nowhere,1,normal,common,1,1005,11111111-1111-1111-1111-111111111111,,false,false,false,"
            "near_mint,en,false,USD,2025-03-03T10:00:00.000Z\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "manabox")
        self.assertEqual(result["total_rows"], 5)
        self.assertEqual(self.brief(result), [
            ("id-farmhand", "foil", 2, "light_played", "de"), ("id-tutor-cmm", "etched", 1, "mint", "en"),
            ("id-bolt-m11", "normal", 4, "near_mint", "zht"), ("id-bolt-2x2", "normal", 1, "poor", "ja")])
        first = result["rows"][0]
        self.assertEqual((first["manabox_id"], first["purchase_price"], first["purchase_currency"], first["added_at"],
                          first["rarity"], first["set_code"], first["collector_number"]),
                         ("1001", 0.2, "USD", "2025-02-27T19:58:37.296Z", "uncommon", "mid", "2"))
        self.assertEqual(result["rows"][1]["misprint"], 1)
        self.assertEqual(result["rows"][2]["purchase_currency"], "EUR")
        self.assertEqual([(u["line"], u["reason"]) for u in result["unmatched"]], [(6, "No card named “Mystery Card”")])
        self.assertEqual(result["approximate"], [])

    def test_moxfield(self):
        text = (
            "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,Alter,Proxy,Purchase Price\n"
            "2,,Ambitious Farmhand // Seasoned Cathar,MID,Good (Lightly Played),German,foil,,2024-05-01 12:00:00.000000,2,,,0.20\n"
            "1,,Demonic Tutor,PLST,Damaged,English,etched,,,DDC-49,True,,\n"
            "1,1,Fire // Ice,APC,Near Mint,Traditional Chinese,,,,128,,,\n"
            "3,,Clue,TMH2,Heavily Played,Chinese,,,,14,,True,0.15\n"
            "1,,Not A Real Card,MID,Near Mint,English,,,,999,,,\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "moxfield")
        self.assertEqual(self.brief(result), [
            ("id-farmhand", "foil", 2, "good", "de"), ("id-tutor-plst", "etched", 1, "poor", "en"),
            ("id-fire-ice", "normal", 1, "near_mint", "zht"), ("id-clue", "normal", 3, "played", "zhs")])
        self.assertEqual(result["rows"][0]["purchase_price"], 0.2)
        self.assertEqual(result["rows"][0]["added_at"], "2024-05-01 12:00:00.000000")
        self.assertEqual((result["rows"][1]["altered"], result["rows"][3]["proxy"]), (1, 1))
        self.assertEqual(len(result["unmatched"]), 1)
        self.assertEqual(result["unmatched"][0]["line"], 6)

    def test_archidekt(self):
        text = (
            "Quantity,Name,Finish,Condition,Date Added,Language,Purchase Price,Tags,Edition Name,Edition Code,"
            "Multiverse Id,Scryfall ID,Collector Number\n"
            "2,Ambitious Farmhand // Seasoned Cathar,Foil,NM,2026-09-01,DE,0.20,,Innistrad: Midnight Hunt,mid,534752,id-farmhand,2\n"
            "1,Demonic Tutor,Etched,LP,2026-09-01,JP,,,Commander Masters,cmm,626364,id-tutor-cmm,509\n"
            "1,Lightning Bolt,Normal,MP,,CS,,,Magic 2011,m11,205227,,149\n"
            "1,Viscera Seer,Normal,D,,CT,,,Secret Lair Drop,sld,0,,VS\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "archidekt")
        self.assertEqual(self.brief(result), [
            ("id-farmhand", "foil", 2, "near_mint", "de"), ("id-tutor-cmm", "etched", 1, "light_played", "ja"),
            ("id-bolt-m11", "normal", 1, "played", "zhs"), ("id-seer", "normal", 1, "poor", "zht")])
        self.assertEqual(result["rows"][0]["added_at"], "2026-09-01")

    def test_deckbox_with_scryfall_ids(self):
        text = (
            "Count,Tradelist Count,Name,Edition,Edition Code,Card Number,Condition,Language,Foil,Signed,Artist Proof,"
            "Altered Art,Misprint,Promo,Textless,Printing Id,Printing Note,Tags,My Price,Cost,Rarity,Price,TcgPlayer ID,Scryfall ID\n"
            "1,,Ambitious Farmhand // Seasoned Cathar,Innistrad: Midnight Hunt,MID,2,Mint,English,,,,,,,,,,,,,,,,id-farmhand\n"
            "2,,Ambitious Farmhand // Seasoned Cathar,Innistrad: Midnight Hunt,MID,2,Near Mint,German,foil,signed,,,,,,,,,$0.20,,,,,id-farmhand\n"
            # Deckbox renumbers The List (DDC-49 -> 49); its Scryfall ID still names the printing.
            "1,,Demonic Tutor,The List,PLST,49,Heavily Played,English,,,,,,,,,,,,,,,,id-tutor-plst\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "deckbox")
        self.assertEqual(self.brief(result), [
            ("id-farmhand", "normal", 1, "mint", "en"), ("id-farmhand", "foil", 2, "near_mint", "de"),
            ("id-tutor-plst", "normal", 1, "played", "en")])
        self.assertEqual((result["rows"][1]["purchase_price"], result["rows"][1]["purchase_currency"],
                          result["rows"][1]["signed"]), (0.2, "USD", 1))

    def test_deckbox_legacy_header_by_set_name(self):
        text = (
            "Count,Tradelist Count,Name,Edition,Card Number,Condition,Language,Foil,Signed,Artist Proof,Altered Art,"
            "Misprint,Promo,Textless,My Price\n"
            "1,0,Clue,Extras: Modern Horizons 2,14,Near Mint,English,,,,,,,,$0.15\n"
            "4,0,Lightning Bolt,Magic 2011,149,Good (Lightly Played),Spanish,,,,,,,,\n"
            # Deckbox's own numbers and set names: "801" for VS, a guild kit named its own way.
            "1,0,Viscera Seer,Secret Lair Drop Series,801,Poor,English,foil,,,,,,,\n"
            "1,0,\"Isperia, Supreme Judge\",Ravnica Allegiance Guild Kit,1,Played,English,foil,,,,,,,\n"
            "1,0,Lightning Bolt,Prerelease Events,,Near Mint,English,,,,,,,,\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "deckbox")
        self.assertEqual(self.brief(result), [
            ("id-clue", "normal", 1, "near_mint", "en"), ("id-bolt-m11", "normal", 4, "good", "es"),
            ("id-seer", "foil", 1, "poor", "en"), ("id-isperia", "foil", 1, "played", "en"),
            ("id-bolt-2x2", "normal", 1, "near_mint", "en")])
        self.assertEqual([a["line"] for a in result["approximate"]], [6])
        self.assertIn("representative printing", result["approximate"][0]["note"])

    def test_tcgplayer_app(self):
        text = (
            "Quantity,Name,Simple Name,Set,Card Number,Set Code,Printing,Condition,Language,Rarity,Product ID,SKU\n"
            "1,Orcish Bowmasters (Borderless),Orcish Bowmasters,The Lord of the Rings: Tales of Middle-earth,433,LTR,Normal,"
            "Near Mint,English,Rare,498425,\n"
            "2,Plains (267) - Full Art,Plains,Zendikar Rising,267,ZNR,Normal,Near Mint,English,Land,221824,4543468\n"
            "1,Demonic Tutor,Demonic Tutor,Commander Masters,509,CMM,Foil,Moderately Played,Japanese,Mythic,504603,\n"
            "1,Ambitious Farmhand // Seasoned Cathar,Ambitious Farmhand,Innistrad: Midnight Hunt,2,MID,Normal,"
            "Lightly Played Foil,Traditional Chinese,Uncommon,248154,\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "tcgplayer")
        self.assertEqual(self.brief(result), [
            ("id-bowmasters-borderless", "normal", 1, "near_mint", "en"), ("id-plains-267", "normal", 2, "near_mint", "en"),
            ("id-tutor-cmm", "foil", 1, "played", "ja"), ("id-farmhand", "foil", 1, "light_played", "zht")])
        self.assertEqual(result["rows"][1]["rarity"], "common")

    def test_tcgplayer_seller_inventory(self):
        text = (
            "TCGplayer Id,Product Line,Set Name,Product Name,Title,Number,Rarity,Condition,TCG Market Price,"
            "TCG Direct Low,TCG Low Price With Shipping,TCG Low Price,Total Quantity,Add to Quantity,TCG Marketplace Price,Photo URL\n"
            "2953567,Magic: The Gathering,Throne of Eldraine,Brazen Borrower // Petty Theft,,39,Mythic,Near Mint,20.37,,,,3,,,\n"
            "2953568,Magic,Coldsnap,Jotun Grunt,,8,Uncommon,Near Mint Foil,0.5,,,,1,,,\n"
            "1234,Pokemon,Base Set,Charizard,,4,Rare,Near Mint,300,,,,1,,,\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "tcgplayer")
        self.assertEqual(self.brief(result), [
            ("id-borrower", "normal", 3, "near_mint", None), ("id-jotun", "foil", 1, "near_mint", None)])
        self.assertIsNone(result["rows"][0]["purchase_price"])          # market prices are not what was paid
        self.assertEqual(result["unmatched"][0]["reason"], "Not a Magic card (Pokemon)")

    def test_dragon_shield(self):
        text = (
            "sep=,\n"
            "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
            "Price Bought,Date Bought,LOW,MID,MARKET\n"
            "Green Deck,2,0,Ambitious Farmhand,MID,Innistrad: Midnight Hunt,2,NearMint,Foil,German,0.20,3/28/2021,,,\n"
            "Imported,1,0,Clue Token,TMH2,Modern Horizons 2 Tokens,14,LightPlayed,Normal,English,0,2024-01-01,,,\n"
            "Imported,1,0,\"Isperia, Supreme Judge\",GK2,Guild Kit: Azorius,1,Excellent,Rainbow Foil,English,0,2024-01-01,,,\n"
            "Imported,1,0,Brazen Borrower,ELD,Throne of Eldraine,39,Poor,Normal,Korean,0,2024-01-01,,,\n"
            "Imported,1,0,Fire // Ice,APC,Apocalypse,128,Mint,Normal,Simplified Chinese,0,2024-01-01,,,\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "dragonshield")
        self.assertEqual(self.brief(result), [
            ("id-farmhand", "foil", 2, "near_mint", "de"), ("id-clue", "normal", 1, "light_played", "en"),
            ("id-isperia", "foil", 1, "excellent", "en"), ("id-borrower", "normal", 1, "poor", "ko"),
            ("id-fire-ice", "normal", 1, "mint", "zhs")])
        self.assertEqual((result["rows"][0]["purchase_price"], result["rows"][0]["added_at"]), (0.2, "2021-03-28"))
        self.assertIsNone(result["rows"][1]["purchase_price"])           # 0 means no price was entered
        self.assertEqual((result["unmatched"], result["approximate"]), ([], []))

    def test_delver_lens(self):
        text = (
            "Name,Edition,Price,Language,Collector's number,Condition,Currency,Edition code,Foil,List name,QuantityX,Scryfall ID\n"
            "Demonic Tutor,Commander Masters,20.5,English,509,Near Mint,EUR,CMM,Foil,Binder,2x,id-tutor-cmm\n"
            "Lightning Bolt,Magic 2011,1.0,Italian,149,Slightly Played,USD,M11,,Binder,1x,\n"
            "Karn the Great Creator,War of the Spark,30,English,1*,Near Mint,USD,WAR,,Binder,1x,\n")
        result = self.parse(text)
        self.assertEqual(result["format"], "delverlens")
        self.assertEqual(self.brief(result)[:2], [
            ("id-tutor-cmm", "foil", 2, "near_mint", "en"), ("id-bolt-m11", "normal", 1, "light_played", "it")])
        # "Price" is the app's market price, not a purchase price.
        self.assertIsNone(result["rows"][0]["purchase_price"])
        # "1*" is Scryfall's "1★"; the name lacks its comma, so the match is kept but flagged.
        self.assertEqual(result["rows"][2]["scryfall_id"], "id-karn-star")
        self.assertEqual([a["line"] for a in result["approximate"]], [4])
        self.assertIn("not “Karn the Great Creator”", result["approximate"][0]["note"])

    def test_delver_lens_split_quantities(self):
        text = ('Reg Qty,Foil Qty,Name,Set,Acquired,Language\n'
                '"1","0","Lightning Bolt","Magic 2011","$1.00",""\n'
                '"2","3","Demonic Tutor","Commander Masters","$100.00",""\n'
                '"0","0","Fire // Ice","Apocalypse","",""\n')
        result = self.parse(text)
        self.assertEqual(result["format"], "delverlens")
        self.assertEqual([(r["scryfall_id"], r["finish"], r["quantity"], r["purchase_price"]) for r in result["rows"]], [
            ("id-bolt-m11", "normal", 1, 1.0), ("id-tutor-cmm", "normal", 2, 100.0), ("id-tutor-cmm", "foil", 3, 100.0)])
        self.assertEqual(result["unmatched"][0]["reason"], "Quantity is 0")

    def test_helvault(self):
        text = (
            "collector_number,estimated_price,extras,language,name,oracle_id,quantity,rarity,scryfall_id,set_code,set_name\n"
            '"2","0.23","foil","de","Ambitious Farmhand // Seasoned Cathar","o","1","uncommon","id-farmhand","mid","Innistrad: Midnight Hunt"\n'
            '"509","40","etchedFoil","en","Demonic Tutor","o","1","mythic","id-tutor-cmm","cmm","Commander Masters"\n'
            '"149","1","/foil","ja","Lightning Bolt","o","2","common","","m11","Magic 2011"\n'
            '"128","1","","en","Fire // Ice","o","1","uncommon","id-fire-ice","apc","Apocalypse"\n')
        result = self.parse(text)
        self.assertEqual(result["format"], "helvault")
        self.assertEqual(self.brief(result), [
            ("id-farmhand", "foil", 1, None, "de"), ("id-tutor-cmm", "etched", 1, None, "en"),
            ("id-bolt-m11", "foil", 2, None, "ja"), ("id-fire-ice", "normal", 1, None, "en")])
        self.assertIsNone(result["rows"][0]["purchase_price"])          # estimated_price is a market price

    def test_generic_files(self):
        # Set and number only, semicolons, as a European spreadsheet saves it.
        result = self.parse("set;number;qty\nltr;433;2\nWAR;1★;1\nzzz;1;1\n")
        self.assertEqual(result["format"], "generic")
        self.assertEqual([(r["scryfall_id"], r["quantity"]) for r in result["rows"]],
                         [("id-bowmasters-borderless", 2), ("id-karn-star", 1)])
        self.assertEqual(result["unmatched"][0]["reason"], "Unknown set “zzz” and no card name")
        # Just names: split cards written with one slash, accents dropped, a card face, a token.
        result = self.parse("Card Name\nFire/Ice\nJotun Grunt\nSeasoned Cathar\nClue\nAEther Snap\n")
        self.assertEqual([r["scryfall_id"] for r in result["rows"]],
                         ["id-fire-ice", "id-jotun", "id-farmhand", "id-clue"])
        self.assertEqual([a["line"] for a in result["approximate"]], [2, 3, 4, 5])
        self.assertTrue(all("no set given" in a["note"] for a in result["approximate"]))
        self.assertEqual(result["unmatched"], [{"line": 6, "text": "AEther Snap", "reason": "No card named “AEther Snap”"}])
        # Name and set without a number: two printings in the set is approximate, one is exact.
        result = self.parse("Name,Set\nOrcish Bowmasters,LTR\nLightning Bolt,Magic 2011\n")
        self.assertEqual([r["scryfall_id"] for r in result["rows"]], ["id-bowmasters", "id-bolt-m11"])
        self.assertEqual([a["line"] for a in result["approximate"]], [2])

    def test_rejects_files_that_are_not_collections(self):
        with self.assertRaises(ValueError):
            self.parse("hello,world\n1,2\n")

    def test_progress(self):
        calls = []
        text = "Name,Set code,Collector number\n" + "Lightning Bolt,M11,149\n" * 1200
        result = self.parse(text, progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(len(result["rows"]), 1200)
        self.assertEqual(calls, [(500, 1200), (1000, 1200), (1200, 1200)])

    def test_detect_format(self):
        headers = {
            "manabox": "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,"
                       "Misprint,Altered,Condition,Language,Purchase price currency",
            "moxfield": "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,"
                        "Alter,Proxy,Purchase Price",
            "archidekt": "Quantity,Name,Finish,Condition,Date Added,Language,Purchase Price,Tags,Edition Name,Edition Code,"
                         "Multiverse Id,Scryfall ID,Collector Number",
            "deckbox": "Count,Tradelist Count,Name,Edition,Card Number,Condition,Language,Foil,Signed,Artist Proof,"
                       "Altered Art,Misprint,Promo,Textless,My Price",
            "tcgplayer": "Quantity,Name,Simple Name,Set,Card Number,Set Code,Printing,Condition,Language,Rarity,Product ID,SKU",
            "dragonshield": "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,"
                            "Printing,Language,Price Bought,Date Bought",
            "delverlens": "Name,Edition code,Collector's number,QuantityX,Foil,Scryfall ID",
            "helvault": "extras,name,scryfall_id,quantity",
            "generic": "Name,Set,Number,Quantity",
        }
        for expected, header in headers.items():
            self.assertEqual(detect_format(header), expected, header)
            self.assertEqual(detect_format(header.split(",")), expected, header)


# A real ManaBox export to check against: set COLLECTION_GALLERY_TEST_EXPORT to its path. The card
# cache is the one a refresh built (gallery.paths). Without both, these tests are skipped.
REAL_EXPORT = Path(os.environ.get("COLLECTION_GALLERY_TEST_EXPORT", "no-such-file"))
REAL_CACHE = CACHE_DATABASE_PATH
LANGUAGE_NAMES = {"en": "English", "fr": "French", "de": "German", "ja": "Japanese"}


@unittest.skipUnless(REAL_EXPORT.exists() and REAL_CACHE.exists(),
                     "set COLLECTION_GALLERY_TEST_EXPORT to a ManaBox export, and run a refresh first")
class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = sqlite3.connect(f"file:{REAL_CACHE.as_posix()}?mode=ro", uri=True)
        cls.text = REAL_EXPORT.read_text(encoding="utf-8-sig")
        cls.source = list(csv.DictReader(io.StringIO(cls.text, newline="")))

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_manabox_export_matches_every_row(self):
        started = time.perf_counter()
        result = parse_collection(REAL_EXPORT.name, self.text, self.connection)
        elapsed = time.perf_counter() - started
        self.assertEqual(result["format"], "manabox")
        self.assertEqual((result["total_rows"], len(result["rows"])), (len(self.source), len(self.source)))
        self.assertEqual(sum(r["quantity"] for r in result["rows"]), sum(int(r["Quantity"]) for r in self.source))
        self.assertEqual((result["unmatched"], result["approximate"]), ([], []))
        self.assertEqual([r["scryfall_id"] for r in result["rows"]], [r["Scryfall ID"] for r in self.source])
        self.assertLess(elapsed, 5)

    def _round_trip(self, header, make_row, stride=7):
        rows = self.source[::stride]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(header.split(","))
        writer.writerows(make_row(r) for r in rows)
        result = parse_collection("converted.csv", out.getvalue(), self.connection)
        self.assertEqual((len(result["rows"]), result["unmatched"], result["approximate"]), (len(rows), [], []))
        return rows, result

    def test_moxfield_copy_round_trips(self):
        header = "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,Alter,Proxy,Purchase Price"
        rows, result = self._round_trip(header, lambda r: [
            r["Quantity"], "", r["Name"], r["Set code"], "Near Mint", LANGUAGE_NAMES[r["Language"]],
            "" if r["Foil"] == "normal" else r["Foil"], "", "", r["Collector number"], "", "", r["Purchase price"]])
        self.assertEqual(result["format"], "moxfield")
        self._same_printings(rows, result)
        self.assertEqual([r["finish"] for r in result["rows"]], [r["Foil"] for r in rows])

    def test_deckbox_copy_round_trips(self):
        # The older Deckbox export: a set *name*, no set code and no Scryfall ID.
        header = ("Count,Tradelist Count,Name,Edition,Card Number,Condition,Language,Foil,Signed,Artist Proof,"
                  "Altered Art,Misprint,Promo,Textless,My Price")
        rows, result = self._round_trip(header, lambda r: [
            r["Quantity"], "0", r["Name"], r["Set name"], r["Collector number"], "Near Mint",
            LANGUAGE_NAMES[r["Language"]], "" if r["Foil"] == "normal" else "foil", "", "", "", "", "", "",
            f"${r['Purchase price']}" if r["Purchase price"] else ""])
        self.assertEqual(result["format"], "deckbox")
        self._same_printings(rows, result)

    def _same_printings(self, rows, result):
        """Same printing for every row, except where ManaBox named a non-English printing by its own
        Scryfall ID: neither Moxfield nor Deckbox (without IDs) can carry that, so set and number
        find the English printing."""
        known = {row[0] for row in self.connection.execute("SELECT scryfall_id FROM printings")}
        for source, parsed in zip(rows, result["rows"]):
            if source["Scryfall ID"] in known:
                self.assertEqual(parsed["scryfall_id"], source["Scryfall ID"], source["Name"])
            else:
                self.assertEqual((parsed["set_code"], parsed["collector_number"]),
                                 (source["Set code"].lower(), source["Collector number"]))
        self.assertEqual([r["quantity"] for r in result["rows"]], [int(r["Quantity"]) for r in rows])


if __name__ == "__main__":
    unittest.main()
