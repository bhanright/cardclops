"""Talking to Scryfall: bulk files, the sets list, and the few cards bulk data misses.

Scryfall's guidelines: send a User-Agent and Accept header, keep API calls to
about ten per second, and use bulk files rather than the API for anything
large. Bulk files and card images are served from their CDN without limits.
"""
import gzip
import json
import time
import urllib.request

from .paths import RAW_DIR, USER_AGENT, ssl_context

API = "https://api.scryfall.com"
SECONDS_BETWEEN_API_CALLS = 0.12
_last_api_call = 0.0


def _request(url, data=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    return urllib.request.Request(url, data=data, headers=headers)


def api_get(url, data=None):
    """One polite API call: waits out the rate limit, returns parsed JSON."""
    global _last_api_call
    wait = SECONDS_BETWEEN_API_CALLS - (time.monotonic() - _last_api_call)
    if wait > 0:
        time.sleep(wait)
    _last_api_call = time.monotonic()
    with urllib.request.urlopen(_request(url, data), timeout=60, context=ssl_context()) as response:
        return json.load(response)


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
        partial = target.with_suffix(".part")
        total = entry.get("compressed_size") or 0
        done = 0
        with urllib.request.urlopen(_request(url), timeout=600, context=ssl_context()) as response, open(partial, "wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(done / total, 1.0))
        partial.rename(target)
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
