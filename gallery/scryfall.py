"""Talking to Scryfall: bulk files, the sets list, and the few cards bulk data misses.

Scryfall's guidelines: send a User-Agent and Accept header, keep to its rate
limits, and use bulk files rather than the API for anything large. Bulk files
and card images are served from their CDN without limits.

The limits (scryfall.com/docs/api/rate-limits, checked 2026-09-28) are hard:
2 a second for /cards/search, /cards/named, /cards/random and /cards/collection,
10 a second for everything else. A 429 answer blocks the caller for 30 seconds
and must not be ignored.
"""
import gzip
import json
import threading
import time

from . import net
from .paths import RAW_DIR

API = "https://api.scryfall.com"
SECONDS_BETWEEN_API_CALLS = 0.12
SECONDS_BETWEEN_SLOW_CALLS = 0.55               # the 2-a-second endpoints, with a margin
SLOW_ENDPOINTS = ("/cards/search", "/cards/named", "/cards/random", "/cards/collection")
SECONDS_AFTER_429 = 31
# One pace for the whole process: the app's request threads and its refresh job all call Scryfall,
# and the limits are per caller, not per thread.
_pace = threading.Lock()
_last_api_call = 0.0
_blocked_until = 0.0              # after a 429, nobody calls until Scryfall's block has passed


def seconds_between(url):
    """How long to leave after a call to `url` before the next one."""
    path = url.split("api.scryfall.com", 1)[-1]
    return SECONDS_BETWEEN_SLOW_CALLS if path.startswith(SLOW_ENDPOINTS) else SECONDS_BETWEEN_API_CALLS


def api_get(url, data=None):
    """One polite API call: waits out the rate limit, returns parsed JSON. After a 429 it waits
    out Scryfall's 30-second block and tries once more."""
    global _last_api_call, _blocked_until
    for attempt in (1, 2):
        with _pace:                 # threads take turns: each waits its gap after the previous call
            now = time.monotonic()
            wait = max(seconds_between(url) - (now - _last_api_call), _blocked_until - now)
            if wait > 0:
                time.sleep(wait)
            _last_api_call = time.monotonic()
        try:
            return net.get_json(url, data)
        except net.NetError as error:
            if error.status != 429 or attempt == 2:
                raise
            _blocked_until = time.monotonic() + SECONDS_AFTER_429


def bulk_file(kind, log=print, progress=None):
    """Path to today's copy of a Scryfall bulk file, downloading it if ours is older.

    `kind` is a bulk type such as 'default_cards', 'oracle_cards' or 'oracle_tags'.
    Files are named by Scryfall's own timestamp, so an unchanged file is never
    fetched twice.
    """
    listing = api_get(f"{API}/bulk-data")["data"]
    entry = next(item for item in listing if item["type"] == kind)
    url = entry["jsonl_download_uri"]
    target = RAW_DIR / url.rsplit("/", 1)[1]
    if not target.exists():
        for stale in RAW_DIR.glob(kind.replace("_", "-") + "-*.jsonl.gz"):
            stale.unlink()
        size_mb = entry.get("compressed_size", 0) / 1e6
        log(f"  downloading {kind} ({size_mb:.0f} MB)")
        net.download(url, target, progress, entry.get("compressed_size") or 0)
    return target, entry["updated_at"]


def iter_bulk(path):
    """Yield each object in a JSON-lines bulk file without loading it all."""
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            line = line.strip()
            if line:
                yield json.loads(line)


def fetch_cards_by_id(scryfall_ids):
    """Fetch card objects the bulk file lacks (non-English printings, mostly).

    The collection endpoint takes 75 identifiers per call.
    """
    ids = list(scryfall_ids)
    found = []
    for start in range(0, len(ids), 75):
        batch = [{"id": card_id} for card_id in ids[start:start + 75]]
        found.extend(api_get(f"{API}/cards/collection", {"identifiers": batch})["data"])
    return found


def fetch_sets():
    return api_get(f"{API}/sets")["data"]
