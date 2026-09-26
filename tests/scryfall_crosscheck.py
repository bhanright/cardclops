"""Compare gallery.query with Scryfall's own search, on the real collection.

    python tests/scryfall_crosscheck.py [--cache DIR] [query ...]

Not part of the unit tests: it needs the network and the built database.

For each query it asks Scryfall (unique=cards, include:extras so nothing is
hidden) for every matching card, keeps the oracle ids the collection holds,
and compares them with two readings of our engine:

  held  -- cards where some entry the collection holds matches.  This is what
           the gallery shows, but print-level keywords (r:, year, is:foil...)
           test only the printing held, while Scryfall matches a card when any
           printing does, so differences here can be legitimate.
  any   -- cards where any English printing in Scryfall's default-cards bulk
           file matches (holding fields left at defaults).  This is the
           like-for-like comparison with Scryfall.

Discrepancies are listed by name so each one can be explained.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gallery import collection, db, query, scryfall  # noqa: E402
from gallery.ingest import oracle_id_of  # noqa: E402
from gallery.paths import RAW_DIR  # noqa: E402

API = "https://api.scryfall.com/cards/search"
HEADERS = {"User-Agent": "Cardclops/0.1", "Accept": "application/json"}

QUERIES = [
    "c:b mv<3 o:/destroy target nonland permanent/",
    "c:b mv<3 otag:removal-nonland",
    "id<=esper t:legendary t:creature",
    "f:pauper r:common t:instant",
    "pow>tou t:creature c:g",
    "-c:b c>=2 is:permanent",
    'o:"draw a card" mv=2 c:u',
    "kw:flying t:creature r>=rare",
    "m:{G/U}",
    "year<=1997 t:creature",
    "date>=2025-06-01 r:mythic",
    "is:dfc",
    "banned:modern",
    "is:commander c:g",
    "m:2WW",
    'o:"~ enters tapped"',
    "c<=rg t:creature mv>=6",
    "pt>=12 t:creature",
    "is:spell c:w mv=1",
    "is:vanilla",
    "is:frenchvanilla c:w",
    "produces=wu",
    "c=2 t:instant",
    "loy>=5",
    "name:/^fire/ or !Shock",
    "is:historic c:r mv<=2 -t:land",
    "is:reserved",
    "is:gamechanger",
    "is:hybrid c:g mv<=3",
    "is:phyrexian",
    'a:"john avon"',
    "restricted:vintage",
    "t:/^legendary.*elf/",
    "(is:split or is:adventure) c:r",
    '-t:creature (o:"destroy target creature" or o:"exile target creature") mv<=2',
    'fo:"enters tapped" t:land id:c',
]

calls = 0


def fetch(url, cache_dir):
    global calls
    if cache_dir:
        path = Path(cache_dir) / (hashlib.sha1(url.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    time.sleep(0.15)
    calls += 1
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        data = json.load(error)
        if data.get("code") != "not_found":
            raise RuntimeError(f"{url}: {data.get('details')}") from None
        data = {"data": [], "has_more": False}
    if cache_dir:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return data


def oracle_of(card):
    return card.get("oracle_id") or (card.get("card_faces") or [{}])[0].get("oracle_id")


def scryfall_oracles(text, cache_dir):
    params = urllib.parse.urlencode({"q": f"({text}) include:extras", "unique": "cards"})
    url, found, warnings = f"{API}?{params}", set(), []
    while url:
        page = fetch(url, cache_dir)
        warnings += page.get("warnings", [])
        found.update(oracle_of(card) for card in page.get("data", []))
        url = page.get("next_page") if page.get("has_more") else None
    return found, warnings


def load_all_printings(held_oracles, tags):
    """Every printing of every held card from the newest default-cards bulk file."""
    bulk = sorted(RAW_DIR.glob("default-cards-*.jsonl.gz"))[-1]
    printings = []
    for card in scryfall.iter_bulk(bulk):
        if oracle_id_of(card) in held_oracles:
            holding = {"row_id": 0, "scryfall_id": card["id"], "finish": "normal", "quantity": 1,
                       "condition": "near_mint", "language": card.get("lang", "en"),
                       "purchase_price": None, "added_at": "", "misprint": 0}
            printings.append(collection.build_entry(holding, card, tags))
    return printings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", help="directory to cache Scryfall responses in")
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()

    connection = db.connect()
    coll = collection.Collection(connection)
    held_oracles = set(coll.by_oracle_id)
    names = {entry.oracle_id: entry.name for entry in coll.entries}
    tags = defaultdict(set)
    for row in connection.execute("SELECT slug, oracle_id FROM oracle_taggings"):
        tags[row["oracle_id"]].add(row["slug"])
    tags = {k: frozenset(v) for k, v in tags.items()}
    t0 = time.perf_counter()
    printings = load_all_printings(held_oracles, tags)
    print(f"{len(coll.entries):,} entries, {len(held_oracles):,} cards; "
          f"{len(printings):,} printings of them in the bulk file ({time.perf_counter() - t0:.1f} s)\n")

    rows = []
    for text in args.queries or QUERIES:
        q = query.compile_query(text, coll.tag_index)
        start = time.perf_counter()
        held = {entry.oracle_id for entry in coll.entries if q.matches(entry)}
        ms = (time.perf_counter() - start) * 1000
        anyprint = {entry.oracle_id for entry in printings if q.matches(entry)}
        theirs, warnings = scryfall_oracles(text, args.cache)
        theirs &= held_oracles
        rows.append((text, len(held), len(anyprint), len(theirs), len(anyprint & theirs), ms))
        print(f"== {text}   [{ms:.1f} ms over the collection]")
        if warnings or q.warnings:
            print(f"   warnings: scryfall={warnings} ours={q.warnings}")
        print(f"   held={len(held)}  any-print={len(anyprint)}  scryfall={len(theirs)}  "
              f"agree={len(anyprint & theirs)}")
        for label, ids in (("ours only (any-print)", anyprint - theirs),
                           ("scryfall only (any-print)", theirs - anyprint),
                           ("scryfall only, not our held printing", (theirs & anyprint) - held)):
            if ids:
                sample = ", ".join(sorted(names[i] for i in ids)[:12])
                print(f"   {label}: {len(ids)}: {sample}{' ...' if len(ids) > 12 else ''}")
        if held - theirs:
            print(f"   ours only (held): {len(held - theirs)}")
    print(f"\n{calls} Scryfall calls\n")
    print(f"{'query':50} {'held':>6} {'any':>6} {'scry':>6} {'both':>6} {'ms':>6}")
    for text, held, anyprint, theirs, both, ms in rows:
        print(f"{text[:50]:50} {held:6} {anyprint:6} {theirs:6} {both:6} {ms:6.1f}")


if __name__ == "__main__":
    main()
