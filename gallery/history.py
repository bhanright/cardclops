"""Past prices from MTGJSON (MIT licensed, rebuilt daily around 06:00 UTC).

No free source offers per-printing daily history beyond MTGJSON's rolling 90
days, so the gallery backfills those 90 days once and then adds every new day
it sees: `load` pulls the 90-day file, `load_today` the one-day file that
`refresh` fetches each time. Running `load` again fills any gap shorter than
90 days, such as a week the computer was off.

Source names are 'mtgjson:<vendor>:<retail|buylist>'. TCGplayer's 'retail'
here is its market price, the same measure Scryfall reports as usd.
"""
import csv
import json
import lzma
import time
import urllib.request

from .db import merge_price_points, set_meta
from .paths import RAW_DIR, USER_AGENT, ssl_context

BASE = "https://mtgjson.com/api/v5"
IDENTIFIERS_FILE = "csv/cardIdentifiers.csv.xz"
FULL_HISTORY_FILE = "AllPrices.json.xz"       # ~47 MB, 90 days
TODAY_FILE = "AllPricesToday.json.xz"         # ~4 MB
PAPER_VENDORS = ("tcgplayer", "cardkingdom", "cardmarket", "manapool")


# The setup wizard downloads the price files before the collection exists and
# imports them after; a file this fresh is reused rather than fetched again.
REUSE_WITHIN_SECONDS = 36 * 3600


def _download(name, log, reuse=False):
    target = RAW_DIR / ("mtgjson-" + name.rsplit("/", 1)[-1])
    if reuse and target.exists() and time.time() - target.stat().st_mtime < REUSE_WITHIN_SECONDS:
        return target
    log(f"  downloading {name}")
    request = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": USER_AGENT})
    partial = target.with_suffix(".part")
    with urllib.request.urlopen(request, timeout=900, context=ssl_context()) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    partial.replace(target)
    return target


def _uuid_map(connection, log, reuse=False):
    """MTGJSON uuid -> Scryfall id, for printings the collection holds."""
    from .ingest import tracked_printings
    held = {row[0] for row in connection.execute("SELECT DISTINCT scryfall_id FROM holdings")}
    held |= tracked_printings(connection)
    path = _download(IDENTIFIERS_FILE, log, reuse)
    mapping = {}
    with lzma.open(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["scryfallId"] in held:
                mapping[row["uuid"]] = row["scryfallId"]
    log(f"  {len(set(mapping.values())):,} of {len(held):,} held printings have MTGJSON prices")
    return mapping


def iter_price_records(path, chunk_size=1 << 22):
    """Yield (uuid, record) from an AllPrices file without holding 1.2 GB of JSON in memory.

    The file is one line: {"meta": {...}, "data": {"<uuid>": {...}, ...}}. We
    find the start of "data" and decode one uuid's record at a time.
    """
    decoder = json.JSONDecoder()
    with lzma.open(path, "rt", encoding="utf-8") as handle:
        buffer = handle.read(chunk_size)
        position = buffer.index('"data"') + len('"data"')
        position = buffer.index("{", position) + 1
        while True:
            # Skip separators, then expect "uuid": {...} or the closing brace.
            while True:
                while position < len(buffer) and buffer[position] in " \n\r\t,":
                    position += 1
                if position < len(buffer):
                    break
                more = handle.read(chunk_size)
                if not more:
                    return
                buffer, position = buffer[position:] + more, 0
            if buffer[position] == "}":
                return
            while True:
                try:
                    key, after_key = decoder.raw_decode(buffer, position)
                    colon = buffer.index(":", after_key)
                    record, end = decoder.raw_decode(buffer, colon + 1)
                    break
                except (json.JSONDecodeError, ValueError):
                    more = handle.read(chunk_size)
                    if not more:
                        raise
                    buffer, position = buffer[position:] + more, 0
            yield key, record
            position = end
            if position > chunk_size:           # drop what we have consumed
                buffer, position = buffer[position:], 0


def _series(record):
    """((source, finish, currency), {day: price}) for each paper vendor series in one uuid's record."""
    for vendor, prices in (record.get("paper") or {}).items():
        if vendor not in PAPER_VENDORS:
            continue
        currency = prices.get("currency", "USD")
        for kind in ("retail", "buylist"):
            for finish, days in (prices.get(kind) or {}).items():
                points = {day: float(price) for day, price in days.items() if price is not None}
                if points:
                    yield (f"mtgjson:{vendor}:{kind}", finish, currency), points


def _import(connection, path, mapping, log):
    total = 0
    for uuid, record in iter_price_records(path):
        scryfall_id = mapping.get(uuid)
        if scryfall_id is None:
            continue
        for (source, finish, currency), points in _series(record):
            merge_price_points(connection, scryfall_id, source, finish, currency, points)
            total += len(points)
    connection.commit()
    return total


def prefetch(log=print):
    """Download the 90-day price files now; `load(..., reuse_downloads=True)` imports them later."""
    _download(IDENTIFIERS_FILE, log)
    _download(FULL_HISTORY_FILE, log)


def load(connection, log=print, reuse_downloads=False):
    """Backfill up to 90 days of daily prices for every held printing."""
    log("MTGJSON price history (90 days)")
    mapping = _uuid_map(connection, log, reuse_downloads)
    path = _download(FULL_HISTORY_FILE, log, reuse_downloads)
    total = _import(connection, path, mapping, log)
    set_meta(connection, "mtgjson_history_loaded", "yes")
    connection.commit()
    log(f"Stored {total:,} daily price points")


def load_today(connection, log=print):
    log("MTGJSON prices for today")
    mapping = _uuid_map(connection, log)
    path = _download(TODAY_FILE, log)
    total = _import(connection, path, mapping, log)
    log(f"Stored {total:,} price points")
