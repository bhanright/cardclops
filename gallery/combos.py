"""Combos from Commander Spellbook (commanderspellbook.com), for a Commander deck's bracket estimate.

Commander Spellbook publishes its whole database as one JSON file (about 700 MB). The server slims
it about weekly to what a deck check needs: Commander-legal combos of two to five named cards, their
Spellbook bracket tag, popularity and first few results, about 3 MB compressed (`publish`). Every
edition downloads that file from the data host and keeps it in the card cache (`update`, `install`),
so finding a deck's combos is a lookup in its own database (`find`).

Spellbook's bracket tags say which bracket a combo is at home in:
    E Exhibition (1), C Core and O Oddball (2), P Powerful and S Spicy (3), R Ruthless (4), B banned.
Combos that need a card described rather than named ("a sacrifice outlet") are left out: a deck
can't be checked for them by name.
"""
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import net

BULK_URL = "https://json.commanderspellbook.com/variants.json"
SITE = "https://commanderspellbook.com"
COMBO_FORMAT = 1                # bumped when the published file's layout changes
MAX_CARDS = 5
RESULTS_KEPT = 3                # results stored per combo, game-ending ones first
NEAR_MISSES_SHOWN = 10
BRACKET_OF_TAG = {"E": 1, "C": 2, "O": 2, "P": 3, "S": 3, "R": 4}
TAG_NAMES = {"E": "Exhibition", "C": "Core", "O": "Oddball", "P": "Powerful", "S": "Spicy", "R": "Ruthless"}
# Results that end the game outright, as Spellbook names them ("Near-infinite" ones don't).
GAME_ENDING_RESULTS = ("Win the game", "Infinite damage", "Infinite combat damage", "Infinite lifeloss",
                       "Infinite mill", "Infinite turns", "Infinite combat phases")


def data_url(path=""):
    """The data host: the same one the browser edition gets its card pack from."""
    return os.environ.get("CARDCLOPS_DATA_URL", "https://data.cardclops.com/") + path


# ---- on the server: slim Spellbook's file ------------------------------------------------------

def read_variants(path):
    """Spellbook's combos ("variants") one at a time from its bulk file, without loading the file
    whole (it would take several GB of memory)."""
    decoder = json.JSONDecoder()
    with open(path, encoding="utf-8") as file:
        text = ""
        while '"variants"' not in text:
            chunk = file.read(1 << 16)
            if not chunk:
                return
            text += chunk
        position = text.index("[", text.index('"variants"')) + 1
        while True:
            while position < len(text) and text[position] in " \t\r\n,":
                position += 1
            if position < len(text) and text[position] == "]":
                return
            try:
                variant, position = decoder.raw_decode(text, position)
            except json.JSONDecodeError:
                chunk = file.read(1 << 20)
                if not chunk:
                    raise
                text = text[position:] + chunk
                position = 0
                continue
            yield variant


def is_game_ending(result):
    lowered = result.lower()
    if "can't lose" in lowered or "unable to lose" in lowered or "chooses to lose" in lowered:
        return False
    return result.startswith(GAME_ENDING_RESULTS) or "lose the game" in lowered or "loses the game" in lowered


def slim(variants, timestamp=None):
    """The published combo file's contents from Spellbook's variants:
    {format, source_timestamp, cards: [[oracle_id, name]], results: [name],
     combos: [[spellbook_id, tag, popularity, identity, mana_value, game_ending,
               [card index], [index of cards that must be the commander], [result index]]]}"""
    cards, card_index, results, result_index, combos = [], {}, [], {}, []

    def index_of(table, lookup, key, value):
        if key not in lookup:
            lookup[key] = len(table)
            table.append(value)
        return lookup[key]

    for variant in variants:
        uses = variant.get("uses") or []
        tag = variant.get("bracketTag")
        if (variant.get("status") != "OK" or tag not in BRACKET_OF_TAG or variant.get("requires")
                or not (variant.get("legalities") or {}).get("commander") or variant.get("spoiler")
                or not 2 <= len(uses) <= MAX_CARDS):
            continue
        names = [result["feature"]["name"] for result in variant.get("produces") or []]
        ending = [name for name in names if is_game_ending(name)]
        kept = (ending + [name for name in names if name not in ending])[:RESULTS_KEPT]
        piece_indexes, commander_indexes = [], []
        for use in uses:
            card = use["card"]
            index = index_of(cards, card_index, card["oracleId"], [card["oracleId"], card["name"]])
            piece_indexes.append(index)
            if use.get("mustBeCommander"):
                commander_indexes.append(index)
        combos.append([variant["id"], tag, variant.get("popularity") or 0, variant.get("identity") or "C",
                       variant.get("manaValueNeeded") or 0, 1 if ending else 0, piece_indexes, commander_indexes,
                       [index_of(results, result_index, name, name) for name in kept]])
    return {"format": COMBO_FORMAT, "source_timestamp": timestamp, "cards": cards, "results": results,
            "combos": combos}


def bulk_timestamp(path):
    with open(path, encoding="utf-8") as file:
        head = file.read(200)
    start = head.find('"timestamp"')
    return json.loads(head[head.index(":", start) + 1:head.index(",", start)]) if start >= 0 else None


def publish(out_dir, work_dir, log=print):
    """Download Spellbook's bulk file into `work_dir`, slim it and write combos-<stamp>.json.gz into
    `out_dir`; the manifest entry for it."""
    work_dir.mkdir(parents=True, exist_ok=True)
    bulk = work_dir / "spellbook-variants.json"
    log("Downloading Commander Spellbook's combos")
    net.download(BULK_URL, bulk)
    try:
        data = slim(read_variants(bulk), bulk_timestamp(bulk))
    finally:
        bulk.unlink(missing_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    data["built_at"] = stamp
    packed = gzip.compress(json.dumps(data, separators=(",", ":")).encode(), compresslevel=9, mtime=0)
    name = f"combos-{stamp}.json.gz"
    (Path(out_dir) / name).write_bytes(packed)
    for old in sorted(Path(out_dir).glob("combos-*.json.gz"))[:-2]:
        old.unlink()
    log(f"Combos {name}: {len(data['combos']):,} combos of {len(data['cards']):,} cards, "
        f"{len(packed) / 1e6:.1f} MB compressed")
    return {"file": name, "built_at": stamp, "bytes": len(packed), "sha256": hashlib.sha256(packed).hexdigest(),
            "format": COMBO_FORMAT, "source_timestamp": data["source_timestamp"]}


# ---- in every edition: keep a copy in the card cache --------------------------------------------

def installed(connection):
    """The installed combo file's built_at stamp, or None."""
    row = connection.execute("SELECT value FROM combo_info WHERE key = 'built_at'").fetchone()
    return row[0] if row else None


def wanted(connection, manifest):
    """The manifest's combo entry when it's newer than the installed copy (and readable here)."""
    entry = (manifest or {}).get("combos")
    if not entry or entry.get("format") != COMBO_FORMAT or entry["built_at"] == installed(connection):
        return None
    return entry


def install(connection, packed):
    """Replace the cache's combos with a downloaded combo file's. Commits."""
    data = json.loads(gzip.decompress(packed))
    for table in ("combo_cards", "combos", "combo_pieces", "combo_results"):
        connection.execute(f"DELETE FROM {table}")
    connection.executemany("INSERT INTO combo_pieces VALUES (?, ?, ?)",
                           [(number, oracle_id, name) for number, (oracle_id, name) in enumerate(data["cards"])])
    connection.executemany("INSERT INTO combo_results VALUES (?, ?)", list(enumerate(data["results"])))
    numbers = lambda indexes: " ".join(map(str, indexes))
    connection.executemany("INSERT INTO combos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        (combo, spellbook_id, tag, popularity, identity, mana_value, ending, len(pieces), numbers(pieces),
         numbers(commander_pieces), numbers(results))
        for combo, (spellbook_id, tag, popularity, identity, mana_value, ending, pieces, commander_pieces, results)
        in enumerate(data["combos"])])
    connection.executemany("INSERT OR IGNORE INTO combo_cards VALUES (?, ?)", [
        (piece, combo) for combo, entry in enumerate(data["combos"]) for piece in entry[6]])
    connection.executemany("INSERT OR REPLACE INTO combo_info VALUES (?, ?)",
                           [("built_at", data["built_at"]), ("source_timestamp", data.get("source_timestamp") or "")])
    connection.commit()
    return len(data["combos"])


def update(connection, log=print):
    """The apps: download the published combo file when there's a newer one. Never fails the
    refresh it runs in; the deck pages just go without combos."""
    try:
        entry = wanted(connection, net.get_json(data_url("manifest.json")))
        if entry:
            log("Commander Spellbook combos")
            install(connection, net.get(data_url(entry["file"])))
    except Exception as error:
        log(f"Couldn't update the combo data: {error}")


# ---- a deck's combos ------------------------------------------------------------------------------

class ComboBook:
    """Looks up a deck's combos in the cache's copy."""

    def __init__(self, connection):
        self.connection = connection

    def available(self):
        try:
            return installed(self.connection) is not None
        except Exception:
            return False

    def find(self, oracle_ids, commander_ids, identity, owned=lambda oracle_id: False):
        """{included: [combo], near_misses: [combo with `missing`], near_miss_count} for a deck, or None
        without combo data. A combo is included when the deck has every piece (and a piece that must
        be the commander is one); a near miss lacks one piece that would fit the deck's colors."""
        if not oracle_ids or not self.available():
            return None
        marks = ",".join("?" * len(oracle_ids))
        pieces = {piece: oracle_id for piece, oracle_id in self.connection.execute(
            f"SELECT piece, oracle_id FROM combo_pieces WHERE oracle_id IN ({marks})", tuple(oracle_ids))}
        if not pieces:
            return {"included": [], "near_misses": [], "near_miss_count": 0}
        present = dict(self.connection.execute(
            f"SELECT combo, COUNT(*) FROM combo_cards WHERE piece IN ({','.join('?' * len(pieces))}) GROUP BY combo",
            tuple(pieces)).fetchall())
        commander_pieces = {piece for piece, oracle_id in pieces.items() if oracle_id in commander_ids}
        included, near = [], []
        candidates = list(present)
        for start in range(0, len(candidates), 900):
            batch = candidates[start:start + 900]
            for row in self.connection.execute(
                    f"SELECT * FROM combos WHERE combo IN ({','.join('?' * len(batch))}) AND size <= ? + 1",
                    (*batch, max(present[c] for c in batch))):
                have = present[row["combo"]]
                if have < row["size"] - 1:
                    continue
                combo_pieces = [int(n) for n in row["pieces"].split()]
                must_lead = {int(n) for n in row["commander_pieces"].split()}
                missing = [piece for piece in combo_pieces if piece not in pieces]
                if any(piece in pieces and piece not in commander_pieces for piece in must_lead):
                    continue                # a piece that must be in the command zone isn't
                if not missing:
                    included.append(row)
                elif not (must_lead & set(missing)) and set(row["identity"].replace("C", "")) <= set(identity):
                    near.append((row, missing[0]))
        included.sort(key=lambda row: (-BRACKET_OF_TAG[row["bracket_tag"]], -row["popularity"], row["size"]))
        near.sort(key=lambda pair: (-pair[0]["popularity"], pair[0]["size"]))
        shown = near[:NEAR_MISSES_SHOWN]
        names, oracle_of, result_names = self._names(included + [row for row, _ in shown])
        near_out = []
        for row, piece in shown:
            combo = _combo_out(row, names, result_names)
            combo["missing"] = names[piece]
            combo["missing_owned"] = bool(owned(oracle_of[piece]))
            near_out.append(combo)
        return {"included": [_combo_out(row, names, result_names) for row in included], "near_misses": near_out,
                "near_miss_count": len(near)}

    def _names(self, rows):
        wanted_pieces = {int(n) for row in rows for n in row["pieces"].split()}
        wanted_results = {int(n) for row in rows for n in row["results"].split()}
        names, oracle_of = {}, {}
        for piece, oracle_id, name in self.connection.execute(
                f"SELECT piece, oracle_id, name FROM combo_pieces WHERE piece IN ({','.join('?' * len(wanted_pieces))})",
                tuple(wanted_pieces)):
            names[piece], oracle_of[piece] = name, oracle_id
        result_names = dict(self.connection.execute(
            f"SELECT result, name FROM combo_results WHERE result IN ({','.join('?' * len(wanted_results))})",
            tuple(wanted_results)).fetchall())
        return names, oracle_of, result_names


def _combo_out(row, names, result_names):
    return {"id": row["spellbook_id"], "url": f"{SITE}/combo/{row['spellbook_id']}/", "tag": row["bracket_tag"],
            "tag_name": TAG_NAMES[row["bracket_tag"]], "bracket": BRACKET_OF_TAG[row["bracket_tag"]],
            "popularity": row["popularity"], "size": row["size"],
            "cards": [names[int(n)] for n in row["pieces"].split()],
            "results": [result_names[int(n)] for n in row["results"].split()],
            "game_ending": bool(row["game_ending"]), "mana_value": row["mana_value"]}
