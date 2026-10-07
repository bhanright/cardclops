"""Setup, import and refresh in the browser edition (docs/PUBLIC_EDITION_PLAN.md, phase 3).

The apps run these jobs on a thread and rebuild the catalog from Scryfall's bulk file. In the
browser there is one thread, so each job is a coroutine on Pyodide's event loop: it waits for the
network with `await`, and the page's requests are answered in between. Two rules follow:

- A job never awaits while holding `gallery.lock`; with one thread, a request waiting for that lock
  would wait forever.
- A job uses the page's own database connection. The browser's file system has no locks, so a
  second connection writing the same files could corrupt them.

The catalog comes ready-made as the card pack (gallery/pack.py); your own cards' Scryfall objects
come from Scryfall's /cards/collection, 75 at a time; each day's prices for them come from the pack.
"""
import asyncio
import json
import os
import traceback
from datetime import datetime

from . import combos, net, pack, scryfall
from .db import get_meta, merge_price_points, set_meta
from .ingest import (PRICE_KEYS, _number, _record_prices, _store_card, decode_legalities, store_holdings,
                     tracked_printings)
from .runtime import request_save
from .collection import slim
from .setup import STALE_AFTER, Jobs

SCRYFALL_COLLECTION = scryfall.API + "/cards/collection"      # 2 calls a second at most (gallery/scryfall.py)
PACK_MB_ESTIMATE = 26                     # shown before the manifest has been read
REFRESH_BATCH = 1000                      # cards updated between pauses for the page's requests
def data_url(path=""):
    return os.environ["CARDCLOPS_DATA_URL"] + path


class BrowserJobs(Jobs):

    def __init__(self, gallery):
        super().__init__(gallery)
        self.manifest = None

    # ---- status ----

    def status(self):
        status = super().status()
        installed = pack.installed(self.gallery.connection)
        size = (self.manifest or {}).get("pack", {}).get("bytes")
        status.update(edition="browser", daily_refresh_scheduled=False, ask_available=False,
                      pack_built_at=installed.get("pack_built_at"),
                      downloads={"pack_mb": round(size / 1e6) if size else PACK_MB_ESTIMATE, "history_mb": 0})
        return status

    def refresh_if_stale(self):
        """On start: refresh in the background once setup is done and the pack is 20 hours old, or
        missing (browser.py discards a damaged one). During setup the wizard does the downloading."""
        with self.gallery.lock:
            connection = self.gallery.connection
            complete = get_meta(connection, "setup_complete") == "yes"
            updated = get_meta(connection, "scryfall_updated_at")
        if not complete:
            return False
        try:
            stale = datetime.now().astimezone() - datetime.fromisoformat(updated) > STALE_AFTER
        except (TypeError, ValueError):
            stale = True
        if stale:
            self.refresh()
        return stale

    # ---- running ----

    def start(self, name, work):
        """`work` is a coroutine function here."""
        with self.lock:
            if not self.state["done"]:
                raise RuntimeError(f"Another job is running ({self.state['job']}); wait for it to finish.")
            self.state = {"job": name, "stage": "Starting", "message": "", "percent": 0, "done": False,
                          "error": None, "result": None}
        asyncio.ensure_future(self._run_async(work))
        return {"job": name}

    async def _run_async(self, work):
        try:
            result = await work()
            self._update(stage="Done", percent=100, done=True, result=result)
        except Exception as error:
            traceback.print_exc()
            # Undo what wasn't committed: an open transaction would keep the files from being saved.
            self.gallery.connection.rollback()
            self._update(error=f"{type(error).__name__}: {error}", done=True)
        finally:
            request_save()

    # ---- steps ----

    async def _fetch_manifest(self):
        self.manifest = json.loads(await net.fetch_async(data_url("manifest.json")))
        if self.manifest.get("format") != pack.PACK_FORMAT:
            raise RuntimeError("The card data online is for a different version of Cardclops; reload the page.")
        return self.manifest

    async def _update_pack(self, low, high):
        """Bring the pack up to the published card data. A pack on the current base needs only the
        day's changes file (a few MB); otherwise the base pack (26 MB) comes first, then the changes.
        True if anything was downloaded."""
        manifest = await self._fetch_manifest()
        base, delta = manifest["pack"], manifest.get("delta")
        latest = delta["built_at"] if delta else base["built_at"]
        with self.gallery.lock:
            connection = self.gallery.connection
            have = pack.installed(connection)
        if have.get("pack_built_at") and have["pack_built_at"] >= latest:
            with self.gallery.lock:
                # Up to date. The card data's date is the installed pack's (it may have been cleared
                # when a damaged cache was discarded, browser.py).
                row = connection.execute("SELECT value FROM pack.catalog_info WHERE key = 'scryfall_updated_at'").fetchone()
                if row:
                    set_meta(connection, "scryfall_updated_at", row[0])
                    connection.commit()
            return False
        need_base = have.get("pack_base") != base["built_at"]
        middle = (low + high) // 2 if need_base and delta else high
        if need_base:
            packed = await self._download("Downloading card data", base, low, middle)
            self._update(stage="Unpacking card data", percent=middle, message="")
            await asyncio.sleep(0)                   # let the page see the new stage first
            with self.gallery.lock:
                pack.install(connection, packed)
        if delta:
            changes = await self._download("Downloading today's changes", delta, middle if need_base else low, high)
            self._update(stage="Updating card data", percent=high, message="")
            await asyncio.sleep(0)
            with self.gallery.lock:
                pack.apply_delta(connection, changes)
        with self.gallery.lock:
            set_meta(connection, "scryfall_updated_at", manifest.get("scryfall_updated_at") or latest)
            connection.commit()
        return True

    async def _update_combos(self):
        """Commander Spellbook's combos (gallery/combos.py), when the data host has a newer copy. A
        failure only costs the bracket estimate its combos, so it doesn't stop the job."""
        try:
            with self.gallery.lock:
                entry = combos.wanted(self.gallery.connection, self.manifest)
            if entry:
                self._update(stage="Downloading combo data", message="")
                packed = await net.fetch_async(data_url(entry["file"]))
                with self.gallery.lock:
                    combos.install(self.gallery.connection, packed)
        except Exception:
            traceback.print_exc()
            self.gallery.connection.rollback()

    async def _download(self, stage, published, low, high):
        """A published file's bytes, with the progress bar moving from `low` to `high`."""
        size = published["bytes"]
        self._update(stage=f"{stage} ({size / 1e6:.1f} MB)", percent=low)

        def progress(done, total):
            self._update(percent=round(low + (high - low) * min(done / (total or size), 1.0)),
                         message=f"{min(done, size) / 1e6:.1f} of {size / 1e6:.1f} MB")
        return await net.fetch_async(data_url(published["file"]), progress=progress)

    def _wanted_ids(self):
        connection = self.gallery.connection
        held = {row[0] for row in connection.execute("SELECT DISTINCT scryfall_id FROM holdings")}
        return held | tracked_printings(connection)

    async def _fetch_missing_cards(self, low, high):
        """Scryfall's objects for held or tracked printings we don't have yet, 75 per request."""
        with self.gallery.lock:
            have = {row[0] for row in self.gallery.connection.execute("SELECT scryfall_id FROM cards")}
            missing = sorted(self._wanted_ids() - have)
        day = self._price_day()
        for start in range(0, len(missing), 75):
            batch = missing[start:start + 75]
            self._update(stage="Card details from Scryfall", message=f"{start:,} of {len(missing):,} cards",
                         percent=round(low + (high - low) * start / max(len(missing), 1)))
            answer = await self._collection_request(batch)
            with self.gallery.lock:
                for card in answer.get("data", []):
                    # Stored without the fields nothing reads: the browser keeps every card in memory.
                    _store_card(self.gallery.connection, slim(card))
                    _record_prices(self.gallery.connection, card, day)
                self.gallery.connection.commit()
            await asyncio.sleep(scryfall.SECONDS_BETWEEN_SLOW_CALLS)
        return len(missing)

    async def _collection_request(self, batch):
        """One /cards/collection call. Scryfall answers a too-fast caller with a 429, which browsers
        may report as a failed fetch (the answer can lack the header that lets a page read it);
        either way, wait out the 30-second block and try again, twice at most."""
        payload = {"identifiers": [{"id": i} for i in batch]}
        for attempt in range(3):
            try:
                return json.loads(await net.fetch_async(SCRYFALL_COLLECTION, payload=payload))
            except net.NetError as error:
                if error.status not in (0, 429) or attempt == 2:
                    raise
                self._update(message="Scryfall asked us to slow down; waiting 30 seconds")
                await asyncio.sleep(scryfall.SECONDS_AFTER_429)

    def _price_day(self):
        return (get_meta(self.gallery.connection, "scryfall_updated_at") or datetime.now().isoformat())[:10]

    async def _refresh_from_pack(self, low, high):
        """Bring your cards up to the pack's day: today's prices recorded, and each card object's
        prices and legalities updated (the apps get both from the bulk file). In batches, committed
        one by one, so the page is answered and saves can happen in between."""
        connection = self.gallery.connection
        with self.gallery.lock:
            day = self._price_day()
            info = dict(connection.execute("SELECT key, value FROM pack.catalog_info").fetchall())
            ids = [row[0] for row in connection.execute("SELECT scryfall_id FROM cards")]
        formats = json.loads(info.get("legality_formats") or "[]")
        for start in range(0, len(ids), REFRESH_BATCH):
            self._update(stage="Today's prices", message=f"{start:,} of {len(ids):,} cards",
                         percent=round(low + (high - low) * start / max(len(ids), 1)))
            with self.gallery.lock:
                self._refresh_batch(ids[start:start + REFRESH_BATCH], day, formats)
                connection.commit()
            await asyncio.sleep(0)

    def _refresh_batch(self, ids, day, formats):
        connection = self.gallery.connection
        marks = ", ".join("?" * len(ids))
        rows = connection.execute(
            "SELECT c.scryfall_id, c.raw, s.prices, o.legalities FROM cards c "
            "LEFT JOIN set_cards s ON s.scryfall_id = c.scryfall_id "
            f"LEFT JOIN oracle_cards o ON o.oracle_id = c.oracle_id WHERE c.scryfall_id IN ({marks})", ids).fetchall()
        for scryfall_id, raw, prices_json, legalities in rows:
            if prices_json is None:
                continue                                    # not in the pack (a non-English printing, say)
            prices = json.loads(prices_json)
            card = json.loads(raw)
            card_prices = card.setdefault("prices", {})
            for key in ("usd", "usd_foil", "usd_etched", "eur", "eur_foil"):
                card_prices[key] = prices.get(key)
            if legalities and formats:
                card["legalities"] = decode_legalities(legalities, formats)
            connection.execute("UPDATE cards SET raw = ? WHERE scryfall_id = ?",
                               (json.dumps(card, separators=(",", ":")), scryfall_id))
            for (finish, currency), key in PRICE_KEYS.items():
                value = _number(prices.get(key))
                if value is not None:
                    merge_price_points(connection, scryfall_id, "scryfall", finish, currency, {day: value})

    async def _finish(self):
        """Legality changes, the page's views rebuilt, then the price alerts run against them (the
        apps' check_alerts builds a second collection of its own; here that's seconds of a frozen
        page for a big collection)."""
        from .ingest import _note_legality_changes
        gallery = self.gallery
        with gallery.lock:
            _note_legality_changes(gallery.connection, self._price_day())
            set_meta(gallery.connection, "data_version", datetime.now().isoformat(timespec="seconds"))
            gallery.connection.commit()
        self._update(stage="Loading", percent=96)
        await asyncio.sleep(0)
        gallery.load()
        if gallery.collection.entries:
            await asyncio.sleep(0)
            with gallery.lock:
                gallery.alerts.check(gallery.collection, gallery.prices, gallery.radar)
                gallery.connection.commit()

    # ---- jobs ----

    def download(self, price_history):
        """First run: the card pack."""
        with self.gallery.lock:
            connection = self.gallery.connection
            if get_meta(connection, "setup_complete") != "yes":
                set_meta(connection, "setup_started", datetime.now().isoformat(timespec="seconds"))
            set_meta(connection, "setup_price_history", "yes" if price_history else "no")
            connection.commit()

        async def work():
            await self._update_pack(0, 90)
            await self._update_combos()
            self._update(stage="Loading", percent=96)
            self.gallery.load()
            return {"card_data_date": (get_meta(self.gallery.connection, "scryfall_updated_at") or "")[:10]}
        return self.start("download", work)

    def import_collection(self, filename, text):
        """Replace the collection with an uploaded CSV, then fetch its cards' details."""
        async def work():
            from . import importers
            from .binders import BinderBook
            connection = self.gallery.connection
            with self.gallery.lock:
                if not connection.execute("SELECT COUNT(*) FROM printings").fetchone()[0]:
                    raise RuntimeError("Download the card data first.")
            self._update(stage="Matching cards", percent=5)
            await asyncio.sleep(0)
            with self.gallery.lock:
                parsed = importers.parse_collection(filename, text, connection)
                if not parsed["rows"]:
                    raise RuntimeError("No cards in that file matched. Is it a collection export?")
                self._update(stage="Saving your collection", percent=40)
                reconciled = store_holdings(connection, parsed["rows"], filename)
                binders_filled = BinderBook(connection).from_import(parsed["rows"])
                connection.commit()
            await self._fetch_missing_cards(45, 80)
            await self._refresh_from_pack(80, 85)
            if get_meta(connection, "setup_price_history") == "yes":
                from .price_files import load_history
                await load_history(self, 86, 94)
            await self._finish()
            copies = sum(r["quantity"] for r in parsed["rows"])
            return {"format": parsed.get("format"), "rows": len(parsed["rows"]), "copies": copies,
                    "matched": len(parsed["rows"]), "approximate": parsed.get("approximate", [])[:200],
                    "unmatched": parsed.get("unmatched", [])[:200], "total_rows": parsed.get("total_rows"),
                    "manual_reconciled": reconciled, "binders_filled": binders_filled}
        return self.start("import", work)

    def refresh(self):
        """Daily: a newer pack if there is one, details for new cards, the day's prices."""
        async def work():
            await self._update_pack(0, 60)
            await self._update_combos()
            await self._fetch_missing_cards(60, 80)
            await self._refresh_from_pack(80, 88)
            if get_meta(self.gallery.connection, "setup_price_history") == "yes":
                from .price_files import load_history
                await load_history(self, 88, 94)
            await self._finish()
            return {"card_data_date": self._price_day()}
        return self.start("refresh", work)
