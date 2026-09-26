"""Decklist parsing and copy allocation (gallery/decks.py).

Run with: python -m unittest tests.test_decks
"""
import json
import sqlite3
import unittest
import uuid

from gallery import db
from gallery.collection import Collection
from gallery.decks import DeckBook, fold, parse_decklist


def sections(text):
    lines, _ = parse_decklist(text)
    return [(line.section, line.quantity, line.name, line.set_code, line.number, line.finish) for line in lines]


class ParseTests(unittest.TestCase):
    def test_moxfield_printings_and_finishes(self):
        self.assertEqual(sections("1 Sol Ring (C21) 263 *F*\n2 Counterspell (MH2) 267 *E*\n3x Island"), [
            ("main", 1, "Sol Ring", "c21", "263", "foil"),
            ("main", 2, "Counterspell", "mh2", "267", "etched"),
            ("main", 3, "Island", None, None, None),
        ])

    def test_arena_headers_and_about(self):
        text = "About\nName Izzet Tempo\n\nCommander\n1 Niv-Mizzet, Parun\n\nDeck\n4 Opt\n\nSideboard\n2 Negate\n"
        lines, name = parse_decklist(text)
        self.assertEqual(name, "Izzet Tempo")
        self.assertEqual([(l.section, l.name) for l in lines],
                         [("commander", "Niv-Mizzet, Parun"), ("main", "Opt"), ("sideboard", "Negate")])

    def test_archidekt_categories(self):
        text = ("1x Krenko, Mob Boss (m13) 139 [Commander{top}]\n1x Sol Ring (c21) 263 [Ramp]\n"
                "1x Impact Tremors [Maybeboard{noDeck}{noPrice}]\n1x Goblin Bombardment [Sac,Sideboard]\n"
                "1x Skullclamp [Draw{noDeck}]")
        self.assertEqual([(s, n) for s, _, n, *_ in sections(text)], [
            ("commander", "Krenko, Mob Boss"), ("main", "Sol Ring"), ("maybeboard", "Impact Tremors"),
            ("sideboard", "Goblin Bombardment"), ("maybeboard", "Skullclamp")])

    def test_mtgo_blank_line_sideboard(self):
        self.assertEqual([s for s, *_ in sections("4 Brainstorm\n20 Island\n\n3 Hydroblast")],
                         ["main", "main", "sideboard"])

    def test_commander_after_blank_line_in_a_hundred_card_list(self):
        text = "\n".join(f"1 Card {i}" for i in range(99)) + "\n\n1 Atraxa, Praetors' Voice"
        self.assertEqual(sections(text)[-1][0], "commander")

    def test_section_header_variants(self):
        for header in ("Sideboard", "SIDEBOARD:", "//Sideboard", "Sideboard (15)"):
            self.assertEqual(sections(f"4 Opt\n{header}\n2 Negate")[-1][0], "sideboard", header)

    def test_mtgo_dek_xml(self):
        xml = ('<Deck><Cards CatID="1" Quantity="4" Sideboard="false" Name="Lightning Bolt" />'
               '<Cards CatID="2" Quantity="2" Sideboard="true" Name="Pyroblast" /></Deck>')
        self.assertEqual(sections(xml), [("main", 4, "Lightning Bolt", None, None, None),
                                         ("sideboard", 2, "Pyroblast", None, None, None)])

    def test_csv(self):
        text = "Count,Name,Edition,Collector Number,Foil,Board\n4,Opt,xln,65,foil,main\n2,Negate,m20,69,,sideboard\n"
        self.assertEqual(sections(text), [("main", 4, "Opt", "xln", "65", "foil"),
                                          ("sideboard", 2, "Negate", "m20", "69", None)])

    def test_real_export_lines(self):
        """Lines captured from Moxfield and Archidekt exports (September 2026)."""
        self.assertEqual(sections("1 Alpine Moon (PM19) 128s *F*\n1 Snapcaster Mage (PLST) ISD-78\n"
                                  "1 Bruse Tarl, Boorish Herder (CMR) 517 *E*"), [
            ("main", 1, "Alpine Moon", "pm19", "128s", "foil"),
            ("main", 1, "Snapcaster Mage", "plst", "ISD-78", None),
            ("main", 1, "Bruse Tarl, Boorish Herder", "cmr", "517", "etched")])
        self.assertEqual(sections("1x Karlach, Fury of Avernus (clb) 507 *E* [Commander{top}] ^Have,#37d67a^\n"
                                  "1x Bojuka Bog (sld) 1532★ *F* [Land]\n"
                                  "1x Agate Instigator (blc) 21 [Considering{noDeck}{noPrice},Creature]"), [
            ("commander", 1, "Karlach, Fury of Avernus", "clb", "507", "etched"),
            ("main", 1, "Bojuka Bog", "sld", "1532★", "foil"),
            ("maybeboard", 1, "Agate Instigator", "blc", "21", None)])

    def test_tappedout_role_markers(self):
        self.assertEqual([s for s, *_ in sections("1x Krenko, Mob Boss *CMDR*\n1x Lurrus of the Dream-Den *CMPN*\n"
                                                  "1x Sol Ring (C21) *F*")],
                         ["commander", "companion", "main"])

    def test_mtgo_commander_after_sideboard_header(self):
        text = ("\n".join(f"1 Card {i}" for i in range(99)) + "\n\nSIDEBOARD:\n1 Some Companion"
                "\n\n1 Atraxa, Praetors' Voice")
        self.assertEqual([s for s, *_ in sections(text)][-2:], ["sideboard", "commander"])

    def test_dek_xml_commander_in_sideboard(self):
        cards = "".join(f'<Cards CatID="{i}" Quantity="1" Sideboard="false" Name="Card {i}" />' for i in range(99))
        xml = f'<Deck>{cards}<Cards CatID="0" Quantity="1" Sideboard="true" Name="Arcane Signet" /></Deck>'
        self.assertEqual(sections(xml)[-1][:3], ("commander", 1, "Arcane Signet"))

    def test_fold_matches_accents_and_split_names(self):
        self.assertEqual(fold("Lim-Dûl's Vault"), fold("Lim-Dul's Vault"))
        self.assertEqual(fold("Fire/Ice"), fold("Fire // Ice"))


def _card(name, oracle_id, set_code="tst", number="1", usd="1.00", type_line="Artifact"):
    return {"id": str(uuid.uuid4()), "oracle_id": oracle_id, "name": name, "set": set_code, "set_name": "Test",
            "collector_number": number, "type_line": type_line, "mana_cost": "{1}", "cmc": 1, "colors": [],
            "color_identity": [], "legalities": {"commander": "legal"}, "rarity": "common",
            "prices": {"usd": usd, "usd_foil": usd}, "layout": "normal", "lang": "en"}


class AllocationTests(unittest.TestCase):
    """Two printings of one card, three copies in all, shared by decks."""

    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(db.SCHEMA)
        self.oracle = str(uuid.uuid4())
        self.cheap = _card("Sol Ring", self.oracle, "c21", "263", "1.00")
        self.pricey = _card("Sol Ring", self.oracle, "lea", "270", "900.00")
        for card in (self.cheap, self.pricey):
            self.connection.execute("INSERT INTO cards VALUES (?, ?, ?, ?)",
                                    (card["id"], self.oracle, card["name"], json.dumps(card)))
            self.connection.execute("INSERT INTO printings VALUES (?, ?, ?, ?, ?)",
                                    (card["set"], card["collector_number"], card["id"], self.oracle, card["name"]))
        self.connection.execute("INSERT INTO oracle_cards VALUES (?, 'Sol Ring', 'sol ring', 'Artifact', '{1}', '', 1.0, ?)",
                                (self.oracle, self.cheap["id"]))
        holdings = [(self.cheap["id"], "normal", 2), (self.pricey["id"], "normal", 1)]
        for scryfall_id, finish, quantity in holdings:
            self.connection.execute(
                "INSERT INTO holdings (scryfall_id, name, finish, quantity, condition, language) "
                "VALUES (?, 'Sol Ring', ?, ?, 'near_mint', 'en')", (scryfall_id, finish, quantity))
        self.collection = Collection(self.connection)
        self.book = DeckBook(self.connection, self.collection)

    def deck(self, name, text, status="active"):
        imported, _ = self.book.import_decks([{"name": name, "text": text, "format": "casual", "status": status}])
        return imported[0]

    def line(self, deck_id):
        return self.book.lines[deck_id][0]

    def test_most_valuable_copy_first_unless_the_list_names_a_printing(self):
        plain = self.deck("A", "1 Sol Ring")
        named = self.deck("B", "1 Sol Ring (C21) 263")
        self.assertEqual(self.line(plain).allocations[0][0], f"{self.pricey['id']}|normal")
        self.assertEqual(self.line(named).allocations[0][0], f"{self.cheap['id']}|normal")

    def test_active_decks_never_share_a_copy_and_the_shortfall_names_the_holder(self):
        first = self.deck("First", "2 Sol Ring")
        second = self.deck("Second", "2 Sol Ring")
        self.assertEqual(self.line(first).owned, 2)
        state = self.line(second)
        self.assertEqual((state.owned, state.missing, state.missing_reason), (1, 1, "used_elsewhere"))
        self.assertEqual(state.used_elsewhere[0]["name"], "First")
        self.assertEqual(sum(e.used for e in self.collection.entries), 3)

    def test_inactive_decks_reserve_nothing(self):
        self.deck("Idea", "3 Sol Ring", status="inactive")
        self.assertEqual(sum(e.used for e in self.collection.entries), 0)
        real = self.deck("Real", "3 Sol Ring")
        self.assertEqual(self.line(real).owned, 3)

    def test_not_owned_when_the_collection_is_short(self):
        state = self.line(self.deck("Greedy", "5 Sol Ring"))
        self.assertEqual((state.owned, state.missing, state.missing_reason), (3, 2, "not_owned"))

    def test_pin_moves_the_copy_and_survives_a_list_replacement(self):
        deck_id = self.deck("Pinned", "1 Sol Ring")
        state = self.line(deck_id)
        self.book.pin(state.row["line_id"], f"{self.cheap['id']}|normal", 1)
        self.assertEqual(self.line(deck_id).allocations, [[f"{self.cheap['id']}|normal", 1, True]])
        self.book.replace_list(deck_id, "1 Sol Ring\n1 Plains")
        self.assertEqual(self.line(deck_id).allocations[0][:2], [f"{self.cheap['id']}|normal", 1])

    def test_priority_decides_who_gets_the_copy(self):
        first = self.deck("First", "3 Sol Ring")
        second = self.deck("Second", "1 Sol Ring")
        self.assertEqual(self.line(second).missing, 1)
        self.book.update(second, {"priority": -1})
        self.assertEqual((self.line(second).missing, self.line(first).missing), (0, 1))

    def test_maybeboard_cards_are_not_in_the_deck(self):
        self.deck("Brew", "1 Plains\nMaybeboard\n1 Sol Ring")
        self.assertEqual(self.collection.entries[0].decks, ())

    def test_basic_lands_use_the_cheapest_copies(self):
        oracle = str(uuid.uuid4())
        cheap = _card("Mountain", oracle, "m21", "271", "0.10", "Basic Land — Mountain")
        foil = _card("Mountain", oracle, "unf", "239", "26.00", "Basic Land — Mountain")
        for card in (cheap, foil):
            self.connection.execute("INSERT INTO cards VALUES (?, ?, ?, ?)", (card["id"], oracle, "Mountain", json.dumps(card)))
            self.connection.execute("INSERT INTO holdings (scryfall_id, name, finish, quantity, condition, language) "
                                    "VALUES (?, 'Mountain', 'normal', 1, 'near_mint', 'en')", (card["id"],))
        self.connection.execute("INSERT INTO oracle_cards VALUES (?, 'Mountain', 'mountain', 'Basic Land — Mountain', '', '', 0.1, ?)",
                                (oracle, cheap["id"]))
        self.collection.reload()
        self.book = DeckBook(self.connection, self.collection)
        state = self.line(self.deck("Red", "1 Mountain"))
        self.assertEqual(state.allocations[0][0], f"{cheap['id']}|normal")

    def test_copy_policies_choose_cheap_or_fancy_copies(self):
        deck_id = self.deck("Policy", "1 Sol Ring")
        self.assertEqual(self.line(deck_id).allocations[0][0], f"{self.pricey['id']}|normal")
        preview = self.book.policy_preview(deck_id)
        self.assertEqual(preview["current"], "default")
        self.assertEqual(preview["options"]["budget"]["value_change_usd"], -899.0)
        self.assertEqual(preview["options"]["budget"]["changes"][0]["to"]["scryfall_id"], self.cheap["id"])
        self.assertEqual(self.line(deck_id).allocations[0][0], f"{self.pricey['id']}|normal")   # preview changes nothing
        self.book.update(deck_id, {"copy_policy": "budget"})
        self.assertEqual(self.line(deck_id).allocations[0][0], f"{self.cheap['id']}|normal")
        with self.assertRaises(ValueError):
            self.book.update(deck_id, {"copy_policy": "gaudy"})

    def test_versions_record_changes_and_restore(self):
        from gallery import versions
        deck_id = self.deck("History", "1 Sol Ring")
        self.book.replace_list(deck_id, "2 Sol Ring")
        history = versions.history(self.connection, deck_id)
        self.assertEqual([v["reason"] for v in history], ["replace", "import"])
        self.assertEqual(history[0]["added"], [{"name": "Sol Ring", "section": "main", "quantity": 1}])
        self.assertIsNone(history[1]["diff"])
        self.book.restore(deck_id, history[1]["version_id"])
        self.assertEqual(self.line(deck_id).row["quantity"], 1)
        self.assertEqual(versions.history(self.connection, deck_id)[0]["reason"], "restore")

    def test_unknown_card_is_kept_and_reported(self):
        imported, warnings = self.book.import_decks([{"name": "X", "text": "1 Notacard", "format": "casual"}])
        self.assertIn("Notacard", warnings[0]["message"])
        self.assertEqual(self.line(imported[0]).missing_reason, "not_owned")


if __name__ == "__main__":
    unittest.main()
