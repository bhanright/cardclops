"""The card pack and price files for the browser edition (gallery/pack.py, gallery/price_files.py).

Run with: python -m unittest tests.test_pack
"""
import gzip
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from gallery import db, pack, price_files
from gallery.ingest import decode_legalities, encode_legalities
from gallery.sets import SetBook

SID = "6d5537da-112e-4679-a113-b5d7ce32a66b"
OID = "0a1b2c3d-0000-4000-8000-000000000001"


def make_cache(folder):
    """A user database and cache as the apps have them, with one card in the catalog."""
    connection = db.connect(folder / "user.sqlite", folder / "cards.sqlite")
    connection.execute("INSERT INTO oracle_cards (oracle_id, name, name_folded, scryfall_id, legalities) "
                       "VALUES (?, 'Sol Ring', 'sol ring', ?, 'lb')", (OID, SID))
    connection.execute("INSERT INTO catalog_info VALUES ('legality_formats', ?)", (json.dumps(["commander", "vintage"]),))
    connection.execute("INSERT INTO set_cards (scryfall_id, set_code, collector_number, name, oracle_id, prices, "
                       "image_front, image_back) VALUES (?, 'c21', '263', 'Sol Ring', ?, '{\"usd\":\"1.5\"}', "
                       "'https://cards.scryfall.io/normal/front/6/d/x.jpg?1', NULL)", (SID, OID))
    connection.execute("INSERT INTO oracle_taggings VALUES ('ramp', ?)", (OID,))
    connection.commit()
    connection.close()
    return folder / "cards.sqlite"


class LegalityTests(unittest.TestCase):
    def test_letters_round_trip_in_the_given_order(self):
        formats = ["commander", "legacy", "vintage"]
        letters = encode_legalities({"commander": "banned", "vintage": "restricted"}, formats)
        self.assertEqual(letters, "bnr")
        self.assertEqual(decode_legalities(letters, formats),
                         {"commander": "banned", "legacy": "not_legal", "vintage": "restricted"})


class PackTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.cache = make_cache(self.folder)

    def test_build_writes_a_pack_the_browser_can_attach(self):
        out = self.folder / "published"
        manifest = pack.build(self.cache, out, log=lambda message: None)
        self.assertEqual(manifest["format"], pack.PACK_FORMAT)
        raw = gzip.decompress((out / manifest["pack"]["file"]).read_bytes())
        (self.folder / "pack.sqlite").write_bytes(raw)

        browser = db.connect(self.folder / "b-user.sqlite", self.folder / "b-cache.sqlite",
                             pack_path=self.folder / "pack.sqlite")
        # printings is a view over set_cards, and lookups by set and number work through it
        row = browser.execute("SELECT scryfall_id FROM printings WHERE set_code = 'c21' AND collector_number = '263'").fetchone()
        self.assertEqual(row[0], SID)
        # image URLs are dropped; the back face is a flag
        self.assertIsNone(browser.execute("SELECT image_front FROM set_cards").fetchone()[0])
        self.assertEqual(browser.execute("SELECT slug FROM oracle_taggings WHERE oracle_id = ?", (OID,)).fetchone()[0], "ramp")
        self.assertEqual(pack.installed(browser)["pack_format"], str(pack.PACK_FORMAT))

    def test_a_cache_made_before_the_pack_does_not_hide_it(self):
        # the browser's old cache file has catalog tables of its own; connecting drops them
        old = db.connect(self.folder / "b-user.sqlite", self.folder / "b-cache.sqlite")
        old.close()
        browser = db.connect(self.folder / "b-user.sqlite", self.folder / "b-cache.sqlite",
                             pack_path=self.folder / "empty-pack.sqlite")
        tables = {row[0] for row in browser.execute("SELECT name FROM cache.sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, {"cards", "rulings", "combos", "combo_cards", "combo_pieces", "combo_results",
                                  "combo_info"})

    def test_install_replaces_the_attached_pack(self):
        out = self.folder / "published"
        manifest = pack.build(self.cache, out, log=lambda message: None)
        target = self.folder / "pack.sqlite"
        browser = db.connect(self.folder / "b-user.sqlite", self.folder / "b-cache.sqlite", pack_path=target)
        self.assertEqual(browser.execute("SELECT COUNT(*) FROM set_cards").fetchone()[0], 0)
        with mock.patch.dict(os.environ, {"CARDCLOPS_PACK": str(target)}):
            pack.install(browser, (out / manifest["pack"]["file"]).read_bytes())
        self.assertEqual(browser.execute("SELECT COUNT(*) FROM set_cards").fetchone()[0], 1)

    def test_install_refuses_a_pack_of_another_format(self):
        out = self.folder / "published"
        manifest = pack.build(self.cache, out, log=lambda message: None)
        other =self.folder / "other.sqlite"
        other.write_bytes(gzip.decompress((out / manifest["pack"]["file"]).read_bytes()))
        edit = sqlite3.connect(other)
        edit.execute("UPDATE catalog_info SET value = '999' WHERE key = 'pack_format'")
        edit.commit()
        edit.close()
        target = self.folder / "pack.sqlite"
        browser = db.connect(self.folder / "b-user.sqlite", self.folder / "b-cache.sqlite", pack_path=target)
        with mock.patch.dict(os.environ, {"CARDCLOPS_PACK": str(target)}), self.assertRaises(RuntimeError):
            pack.install(browser, gzip.compress(other.read_bytes()))

    def test_set_card_images_are_derived_when_the_pack_has_no_urls(self):
        connection = db.connect(self.folder / "user.sqlite", self.cache)
        connection.execute("UPDATE set_cards SET image_front = NULL, image_back = 'y'")
        book = SetBook.__new__(SetBook)
        book.connection = connection
        self.assertEqual(book.image_for(SID, "back"),
                         f"https://cards.scryfall.io/normal/back/6/d/{SID}.jpg")


class DeltaTests(unittest.TestCase):
    """Most days publish only the changes since the base pack (gallery/pack.py)."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.cache = make_cache(self.folder)
        self.out = self.folder / "published"
        self.first = pack.build(self.cache, self.out, log=lambda message: None)

    def change_the_cache(self):
        cache = sqlite3.connect(self.cache)
        cache.execute("UPDATE set_cards SET prices = '{\"usd\":\"2.0\"}' WHERE scryfall_id = ?", (SID,))
        cache.execute("INSERT INTO set_cards (scryfall_id, set_code, collector_number, name) "
                      "VALUES ('new-card', 'c21', '264', 'New Card')")
        cache.execute("DELETE FROM oracle_taggings")
        cache.commit()
        cache.close()

    def installed_browser(self):
        target = self.folder / "pack.sqlite"
        browser = db.connect(self.folder / "b-user.sqlite", self.folder / "b-cache.sqlite", pack_path=target)
        with mock.patch.dict(os.environ, {"CARDCLOPS_PACK": str(target)}):
            pack.install(browser, (self.out / self.first["pack"]["file"]).read_bytes())
        return browser

    def test_the_next_day_publishes_changes_that_bring_a_pack_up_to_date(self):
        self.change_the_cache()
        with mock.patch("gallery.pack.datetime") as clock:
            clock.now.return_value = _later(self.first)
            clock.strptime.side_effect = datetime.strptime
            second = pack.build(self.cache, self.out, log=lambda message: None)
        self.assertEqual(second["pack"], self.first["pack"])                    # the base stays
        self.assertEqual(second["delta"]["base"], self.first["pack"]["built_at"])
        browser = self.installed_browser()
        pack.apply_delta(browser, (self.out / second["delta"]["file"]).read_bytes())
        self.assertEqual(browser.execute("SELECT prices FROM pack.set_cards WHERE scryfall_id = ?", (SID,)).fetchone()[0],
                         '{"usd":"2.0"}')
        self.assertEqual(browser.execute("SELECT name FROM printings WHERE collector_number = '264'").fetchone()[0], "New Card")
        self.assertEqual(browser.execute("SELECT COUNT(*) FROM pack.oracle_taggings").fetchone()[0], 0)
        self.assertEqual(pack.installed(browser)["pack_built_at"], second["delta"]["built_at"])
        self.assertEqual(pack.installed(browser)["pack_base"], self.first["pack"]["built_at"])

    def test_a_row_an_earlier_day_added_goes_when_it_goes_from_scryfall(self):
        def build_on(day):
            with mock.patch("gallery.pack.datetime") as clock:
                clock.now.return_value = _later(self.first, day)
                clock.strptime.side_effect = datetime.strptime
                return pack.build(self.cache, self.out, log=lambda message: None)
        self.change_the_cache()                                      # day 1 adds 'new-card'
        second = build_on(1)
        browser = self.installed_browser()
        pack.apply_delta(browser, (self.out / second["delta"]["file"]).read_bytes())
        cache = sqlite3.connect(self.cache)
        cache.execute("DELETE FROM set_cards WHERE scryfall_id = 'new-card'")      # day 2: renumbered away
        cache.commit()
        cache.close()
        third = build_on(2)
        self.assertNotIn("added_since_base", third["delta"])
        pack.apply_delta(browser, (self.out / third["delta"]["file"]).read_bytes())
        self.assertEqual(browser.execute("SELECT COUNT(*) FROM pack.set_cards WHERE scryfall_id = 'new-card'").fetchone()[0], 0)

    def test_changes_for_another_base_are_refused(self):
        self.change_the_cache()
        with mock.patch("gallery.pack.datetime") as clock:
            clock.now.return_value = _later(self.first)
            clock.strptime.side_effect = datetime.strptime
            second = pack.build(self.cache, self.out, log=lambda message: None)
        browser = self.installed_browser()
        browser.execute("UPDATE pack.catalog_info SET value = 'other' WHERE key = 'pack_base'")
        browser.commit()
        with self.assertRaises(RuntimeError):
            pack.apply_delta(browser, (self.out / second["delta"]["file"]).read_bytes())

    def test_a_week_old_base_or_large_changes_bring_a_new_base(self):
        self.change_the_cache()
        with mock.patch("gallery.pack.datetime") as clock:
            clock.now.return_value = _later(self.first, days=8)
            clock.strptime.side_effect = datetime.strptime
            weekly = pack.build(self.cache, self.out, log=lambda message: None)
        self.assertNotEqual(weekly["pack"]["built_at"], self.first["pack"]["built_at"])
        self.assertNotIn("delta", weekly)
        cache = sqlite3.connect(self.cache)
        cache.execute("UPDATE set_cards SET prices = '{\"usd\":\"9.0\"}'")
        cache.commit()
        cache.close()
        with mock.patch("gallery.pack.datetime") as clock, mock.patch("gallery.pack.DELTA_MAX_SHARE", 0.0):
            clock.now.return_value = _later(weekly, days=1)
            clock.strptime.side_effect = datetime.strptime
            big = pack.build(self.cache, self.out, log=lambda message: None)
        self.assertNotIn("delta", big)
        self.assertNotEqual(big["pack"]["built_at"], weekly["pack"]["built_at"])


def _later(manifest, days=1):
    """A clock `days` after the manifest's pack was built (UTC)."""
    built = datetime.strptime(manifest["pack"]["built_at"], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return built + timedelta(days=days)


class PriceFileTests(unittest.TestCase):
    def test_published_files_line_prices_up_with_days(self):
        folder = Path(tempfile.mkdtemp())
        store = sqlite3.connect(folder / "store.sqlite")
        store.executescript(price_files.STORE_SCHEMA)
        db.merge_price_points(store, SID, price_files.MARKET_SOURCE, "normal", "USD",
                              {"2026-09-01": 1.0, "2026-09-03": 1.5})
        db.merge_price_points(store, SID, price_files.MARKET_SOURCE, "foil", "USD", {"2026-09-02": 4.0})
        store.commit()
        store.close()
        entry = price_files.publish(folder / "store.sqlite", folder, log=lambda message: None)
        shard = json.loads((folder / entry["market"].format(prefix=SID[:2])).read_text())
        self.assertEqual(shard["days"], ["2026-09-01", "2026-09-02", "2026-09-03"])
        self.assertEqual(shard["cards"][SID]["normal"], [1.0, None, 1.5])
        self.assertEqual(dict(price_files._shard_points(shard, SID))["foil"], {"2026-09-02": 4.0})


class ScryfallPaceTests(unittest.TestCase):
    def test_the_two_a_second_endpoints_are_spaced_further_apart(self):
        from gallery import scryfall
        self.assertGreaterEqual(scryfall.seconds_between(scryfall.API + "/cards/collection"), 0.5)
        self.assertGreaterEqual(scryfall.seconds_between(scryfall.API + "/cards/search?q=x"), 0.5)
        self.assertLess(scryfall.seconds_between(scryfall.API + "/sets"), 0.5)

    def test_a_429_waits_out_the_block_and_tries_once_more(self):
        from gallery import net, scryfall
        answers = [net.NetError("u", 429), {"data": []}]

        def fake(url, data=None):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with mock.patch("gallery.net.get_json", side_effect=fake), mock.patch("time.sleep") as sleep:
            self.assertEqual(scryfall.api_get(scryfall.API + "/sets"), {"data": []})
        # The retry waits until the block has passed (31 s from the 429, less the moment since).
        self.assertGreater(max(call.args[0] for call in sleep.call_args_list), scryfall.SECONDS_AFTER_429 - 1)
        scryfall._blocked_until = 0.0           # the next test isn't blocked by this one's 429


class AskWithKeyTests(unittest.TestCase):
    """The Ask box through Anthropic's API with the visitor's key (browser edition)."""

    def setUp(self):
        self.tags = mock.Mock(labels={})

    def test_the_key_goes_to_anthropic_in_the_right_headers(self):
        from gallery import ask
        reply = {"content": [{"type": "text", "text": '{"query": "t:dragon", "explanation": "dragons"}'}]}
        with mock.patch("gallery.net.get", return_value=json.dumps(reply).encode()) as get:
            answer = ask.translate("my dragons", self.tags, lambda q, t: None, api_key="sk-ant-x")
        self.assertEqual(answer, {"query": "t:dragon", "explanation": "dragons"})
        url, data, headers = get.call_args.args[:3]
        self.assertEqual(url, ask.API_URL)
        self.assertEqual(headers["x-api-key"], "sk-ant-x")
        self.assertEqual(headers["anthropic-dangerous-direct-browser-access"], "true")
        self.assertEqual(json.loads(data)["model"], ask.API_MODEL)

    def test_a_refused_key_says_so(self):
        from gallery import ask, net
        with mock.patch("gallery.net.get", side_effect=net.NetError(ask.API_URL, 401)):
            with self.assertRaises(ask.AskError) as caught:
                ask.translate("my dragons", self.tags, lambda q, t: None, api_key="sk-ant-bad")
        self.assertIn("didn't accept that API key", str(caught.exception))

    def test_without_a_key_the_command_line_tool_is_used(self):
        from gallery import ask
        with mock.patch("gallery.ask._run_claude", return_value=("t:elf", "elves")) as cli:
            self.assertEqual(ask.translate("elves", self.tags, lambda q, t: None)["query"], "t:elf")
        cli.assert_called_once()


if __name__ == "__main__":
    unittest.main()
