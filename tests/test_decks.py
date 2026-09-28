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
        self.connection.execute("INSERT INTO oracle_cards VALUES (?, 'Sol Ring', 'sol ring', 'Artifact', '{1}', '', 1.0, ?, NULL)",
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
        self.connection.execute("INSERT INTO oracle_cards VALUES (?, 'Mountain', 'mountain', 'Basic Land — Mountain', '', '', 0.1, ?, NULL)",
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


class EditingTests(AllocationTests):
    """Names shared with oddities, partner commanders from an import, and editing single lines."""

    def add_card(self, name, type_line, set_code, number, **fields):
        oracle = str(uuid.uuid4())
        card = _card(name, oracle, set_code, number, "0.25", type_line)
        card.update(fields)
        self.connection.execute("INSERT INTO cards VALUES (?, ?, ?, ?)", (card["id"], oracle, name, json.dumps(card)))
        self.connection.execute("INSERT INTO printings VALUES (?, ?, ?, ?, ?)", (set_code, number, card["id"], oracle, name))
        self.connection.execute("INSERT INTO oracle_cards VALUES (?, ?, ?, ?, '', '', 0.25, ?, NULL)",
                                (oracle, name, fold(name), type_line, card["id"]))
        return oracle

    def fresh_book(self):
        self.collection.reload()
        self.book = DeckBook(self.connection, self.collection)

    def commander_deck(self, text):
        imported, _ = self.book.import_decks([{"name": "C", "text": text, "format": "commander", "status": "inactive"}])
        return imported[0]

    def names_in(self, deck_id, section):
        return sorted(s.row["name"] for s in self.book.lines[deck_id] if s.row["section"] == section)

    def test_a_game_card_wins_a_name_shared_with_a_front_card(self):
        self.add_card("Pym Particles", "Card", "fmsc", "28")          # the Jumpstart front card, seen first
        sorcery = self.add_card("Pym Particles", "Sorcery", "msh", "70")
        self.fresh_book()
        for text in ("1 Pym Particles", "1 Pym Particles (MSH) 70"):
            self.assertEqual(self.line(self.deck(text, text)).row["oracle_id"], sorcery, text)

    def test_lines_matched_to_a_front_card_are_repaired(self):
        front = self.add_card("Pym Particles", "Card", "fmsc", "28")
        sorcery = self.add_card("Pym Particles", "Sorcery", "msh", "70")
        deck_id = self.deck("Old", "1 Sol Ring")
        self.connection.execute("INSERT INTO deck_lines (deck_id, position, section, quantity, name, oracle_id, scryfall_id) "
                                "VALUES (?, 2, 'main', 1, 'Pym Particles', ?, NULL)", (deck_id, front))
        self.connection.commit()
        self.fresh_book()
        self.assertIn(sorcery, [s.row["oracle_id"] for s in self.book.lines[deck_id]])

    def test_partners_listed_first_both_become_commanders(self):
        self.add_card("Tymna the Weaver", "Legendary Creature — Human Cleric", "c16", "48", keywords=["Partner"])
        self.add_card("Thrasios, Triton Hero", "Legendary Creature — Merfolk Wizard", "c16", "46", keywords=["Partner"])
        self.add_card("Krenko, Mob Boss", "Legendary Creature — Goblin Warrior", "m13", "141")
        self.fresh_book()
        both = self.commander_deck("1 Tymna the Weaver\n1 Thrasios, Triton Hero\n1 Sol Ring")
        self.assertEqual(self.names_in(both, "commander"), ["Thrasios, Triton Hero", "Tymna the Weaver"])
        # A legendary creature without Partner right after stays in the deck.
        one = self.commander_deck("1 Tymna the Weaver\n1 Krenko, Mob Boss\n1 Sol Ring")
        self.assertEqual(self.names_in(one, "commander"), ["Tymna the Weaver"])

    def test_a_replacement_that_fails_halfway_leaves_the_deck_as_it_was(self):
        from unittest import mock
        deck_id = self.deck("Safe", "1 Sol Ring")
        uncached = str(uuid.uuid4())                    # a printing Scryfall must be asked about
        self.connection.execute("INSERT INTO printings VALUES ('sld', '999', ?, ?, 'Sol Ring')", (uncached, self.oracle))
        self.connection.commit()
        with mock.patch("gallery.scryfall.fetch_cards_by_id", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                self.book.replace_list(deck_id, "2 Sol Ring (SLD) 999")
        self.assertFalse(self.connection.in_transaction)
        self.book.update(deck_id, {"notes": "an unrelated save"})
        rows = self.connection.execute("SELECT quantity, requested_set FROM deck_lines WHERE deck_id = ?", (deck_id,)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [(1, None)])
        self.assertEqual([s.row["quantity"] for s in self.book.lines[deck_id]], [1])

    def test_quick_add_takes_decklist_lines(self):
        deck_id = self.deck("Quick", "1 Sol Ring")
        result = self.book.add_text(deck_id, "2 Sol Ring\n1 Sol Ring (C21) 263 *F*\nSideboard\n1 Sol Ring\n1 No Such Card", "main")
        self.assertEqual([(a["quantity"], a["section"]) for a in result["added"]], [(2, "main"), (1, "main"), (1, "sideboard")])
        self.assertEqual(len(result["warnings"]), 1)
        rows = sorted((s.row["section"], s.row["quantity"], s.row["requested_set"], s.row["requested_finish"])
                      for s in self.book.lines[deck_id])
        # The plain line merged into the existing one; the printing asked for is a line of its own.
        self.assertEqual(rows, [("main", 1, "c21", "foil"), ("main", 3, None, None), ("sideboard", 1, None, None)])
        with self.assertRaises(ValueError):
            self.book.add_text(deck_id, "1 No Such Card")

    def test_adding_editing_and_removing_a_line(self):
        deck_id = self.deck("Edit", "1 Sol Ring")
        self.book.add_line(deck_id, {"oracle_id": self.oracle})                  # same card, same section: merged
        self.assertEqual([s.row["quantity"] for s in self.book.lines[deck_id]], [2])
        line_id = self.book.add_line(deck_id, {"scryfall_id": self.cheap["id"], "section": "sideboard"})
        state = self.book.line_by_id[line_id]
        self.assertEqual((state.row["section"], state.row["requested_set"]), ("sideboard", "c21"))
        self.book.pin(line_id, f"{self.cheap['id']}|normal", 1)
        self.book.edit_line(line_id, {"quantity": 3, "section": "maybeboard"})
        self.assertEqual((self.book.line_by_id[line_id].row["quantity"], self.book.line_by_id[line_id].row["section"]),
                         (3, "maybeboard"))
        self.book.edit_line(line_id, {"quantity": 0})
        self.assertNotIn(line_id, self.book.line_by_id)
        with self.assertRaises(ValueError):
            self.book.add_line(deck_id, {"name": "No Such Card"})



class DecksFileTests(AllocationTests):
    """Export all decks to a file and read it back (the phone <-> cardclops.com transfer)."""

    def test_round_trip_keeps_lines_printings_settings_and_pins(self):
        deck_id = self.deck("Rings", "1 Sol Ring (C21) 263\n1 Plains\nSideboard\n1 Sol Ring")
        self.book.update(deck_id, {"status": "inactive", "priority": 3, "notes": "hello", "copy_policy": "budget"})
        main = next(s for s in self.book.lines[deck_id] if s.row["section"] == "main" and s.row["name"] == "Sol Ring")
        self.book.pin(main.row["line_id"], f"{self.cheap['id']}|normal", 1)
        data = json.loads(json.dumps(self.book.export_all()))        # through JSON, as a file would be
        self.book.delete(deck_id)
        result = self.book.import_file(data)
        self.assertEqual(result["imported"], ["Rings"])
        new_id = next(d for d, deck in self.book.decks.items() if deck["name"] == "Rings")
        deck = self.book.decks[new_id]
        self.assertEqual((deck["status"], deck["priority"], deck["notes"], deck["copy_policy"]), ("inactive", 3, "hello", "budget"))
        rows = sorted((s.row["section"], s.row["name"], s.row["requested_set"]) for s in self.book.lines[new_id])
        self.assertEqual(rows, [("main", "Plains", None), ("main", "Sol Ring", "c21"), ("sideboard", "Sol Ring", None)])
        main = next(s for s in self.book.lines[new_id] if s.row["section"] == "main" and s.row["name"] == "Sol Ring")
        self.assertEqual(self.book.pins[main.row["line_id"]], {f"{self.cheap['id']}|normal": 1})

    def test_decks_already_here_are_replaced_kept_or_skipped(self):
        deck_id = self.deck("Same", "1 Sol Ring")
        data = self.book.export_all()
        data["decks"][0]["lines"][0]["quantity"] = 2
        self.assertEqual(self.book.import_file(data, "skip")["skipped"], ["Same"])
        self.assertEqual(self.line(deck_id).row["quantity"], 1)
        self.assertEqual(self.book.import_file(data, "replace")["replaced"], ["Same"])
        self.assertEqual(self.line(deck_id).row["quantity"], 2)            # same deck, history kept
        self.assertEqual(self.book.import_file(data, "keep")["imported"], ["Same (imported)"])
        with self.assertRaises(ValueError):
            self.book.import_file({"decks": []})

if __name__ == "__main__":
    unittest.main()
