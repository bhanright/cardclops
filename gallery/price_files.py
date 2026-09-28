"""Price history for the browser edition, published beside the card pack (docs/PUBLIC_EDITION_PLAN.md).

The apps download MTGJSON's 90-day price file themselves (gallery/history.py): 47 MB compressed,
1.2 GB of JSON, too much for a phone's browser. So the server's daily job keeps the history of
every printing in a store of its own (`build`), and publishes the part the collection views use,
TCGplayer's market price (the same measure as Scryfall's usd), as 256 files split by the first two
characters of the Scryfall id. A browser fetches the files its cards fall in (`load_history`).
The store keeps every day it sees, thinned as db.compact_points does, so history grows past
MTGJSON's rolling 90 days.

A file, prices/market/<xx>.json:
    {"days": ["2026-07-01", ...],                     every day any series in the file has
     "cards": {"<scryfall id>": {"normal": [1.25, null, 1.3, ...], "foil": [...]}}}
with each series' prices lined up with "days" (null where it has none).

MTGJSON's data is MIT licensed; its notice travels in the pack manifest's credits.
"""
import asyncio
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import db

MARKET_SOURCE = "mtgjson:tcgplayer:retail"
SHARD_CHARACTERS = 2
RELOAD_AFTER_DAYS = 7             # a browser away this long fetches its cards' files again

STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_series (
    scryfall_id TEXT NOT NULL, source TEXT NOT NULL, finish TEXT NOT NULL, currency TEXT NOT NULL,
    points BLOB NOT NULL, PRIMARY KEY (scryfall_id, source, finish, currency)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


# ---- on the server -------------------------------------------------------------------------------

def _all_uuids(identifiers_path):
    """MTGJSON uuid -> Scryfall id, for every printing."""
    import csv
    import lzma                       # the server's; the browser's Python has no lzma
    with lzma.open(identifiers_path, "rt", encoding="utf-8", newline="") as handle:
        return {row["uuid"]: row["scryfallId"] for row in csv.DictReader(handle) if row.get("scryfallId")}


def update_store(store_path, log=print):
    """Merge MTGJSON's latest prices into the store: the 90-day file the first time, then each day's."""
    from . import history
    store = sqlite3.connect(store_path)
    store.executescript(STORE_SCHEMA)
    seeded = store.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
    identifiers = history._download(history.IDENTIFIERS_FILE, log, reuse=True)
    mapping = _all_uuids(identifiers)
    name = history.TODAY_FILE if seeded else history.FULL_HISTORY_FILE
    path = history._download(name, log, reuse=True)
    total = 0
    for uuid, record in history.iter_price_records(path):
        scryfall_id = mapping.get(uuid)
        if scryfall_id is None:
            continue
        for (source, finish, currency), points in history._series(record):
            if source == MARKET_SOURCE and currency == "USD":
                db.merge_price_points(store, scryfall_id, source, finish, currency, points)
                total += len(points)
    store.execute("INSERT OR REPLACE INTO meta VALUES ('seeded', ?)", (datetime.now().isoformat(timespec="seconds"),))
    store.commit()
    store.close()
    log(f"Price store: merged {total:,} points from {name}")


def publish(store_path, out_dir, log=print):
    """Write prices/market/<xx>.json from the store; returns the manifest's "prices" entry."""
    store = sqlite3.connect(store_path)
    shards = defaultdict(dict)
    for scryfall_id, finish, blob in store.execute(
            "SELECT scryfall_id, finish, points FROM price_series WHERE source = ? AND currency = 'USD'",
            (MARKET_SOURCE,)):
        shards[scryfall_id[:SHARD_CHARACTERS]].setdefault(scryfall_id, {})[finish] = db.unpack_points(blob)
    store.close()
    target = Path(out_dir) / "prices" / "market"
    target.mkdir(parents=True, exist_ok=True)
    newest = ""
    for prefix, cards in shards.items():
        days = sorted({day for series in cards.values() for points in series.values() for day in points})
        newest = max(newest, days[-1] if days else "")
        body = {"days": days, "cards": {
            scryfall_id: {finish: [points.get(day) for day in days] for finish, points in series.items()}
            for scryfall_id, series in cards.items()}}
        (target / f"{prefix}.json").write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    log(f"Price files: {len(shards)} files, {sum(len(c) for c in shards.values()):,} printings, to {newest}")
    return {"market": "prices/market/{prefix}.json", "prefix_length": SHARD_CHARACTERS, "newest_day": newest,
            "built_at": datetime.now(timezone.utc).strftime("%Y%m%d%H%M")}


# ---- in the browser ------------------------------------------------------------------------------

def _shard_points(shard, scryfall_id):
    days = shard["days"]
    for finish, prices in shard["cards"].get(scryfall_id, {}).items():
        yield finish, {day: price for day, price in zip(days, prices) if price is not None}


async def load_history(jobs, low, high):
    """Merge the published market history into price_series for your cards: all of them the first
    time and after a week away, otherwise only cards that are new since the last load."""
    from . import net
    from .browser_jobs import data_url
    gallery = jobs.gallery
    connection = gallery.connection
    manifest = jobs.manifest or await jobs._fetch_manifest()
    published = manifest.get("prices")
    if not published:
        return 0
    with gallery.lock:
        wanted = jobs._wanted_ids()
        last = db.get_meta(connection, "price_files_loaded_at")
        checked = set(json.loads(db.get_meta(connection, "price_files_ids") or "[]"))
    stale = not last or (datetime.now() - datetime.fromisoformat(last)).days >= RELOAD_AFTER_DAYS
    ids = wanted if stale else wanted - checked
    by_prefix = defaultdict(list)
    for scryfall_id in ids:
        by_prefix[scryfall_id[:published["prefix_length"]]].append(scryfall_id)
    for number, (prefix, members) in enumerate(sorted(by_prefix.items())):
        jobs._update(stage="Price history", message=f"{number + 1} of {len(by_prefix)} files",
                     percent=round(low + (high - low) * number / max(len(by_prefix), 1)))
        try:
            shard = json.loads(await net.fetch_async(data_url(published["market"].format(prefix=prefix))))
        except net.NetError as error:
            if error.status == 404:                  # no printing in the file has a price
                continue
            raise
        with gallery.lock:
            for scryfall_id in members:
                for finish, points in _shard_points(shard, scryfall_id):
                    if points:
                        db.merge_price_points(connection, scryfall_id, MARKET_SOURCE, finish, "USD", points)
        await asyncio.sleep(0)
    with gallery.lock:
        if stale:
            db.set_meta(connection, "price_files_loaded_at", datetime.now().isoformat(timespec="seconds"))
        db.set_meta(connection, "price_files_ids", json.dumps(sorted(wanted)))
        connection.commit()
    return len(ids)
