"""The card pack: the catalog every user shares, built once a day for the browser edition.

A catalog is the part of the card cache that doesn't depend on whose collection it is (db.py's
CATALOG_SCHEMA: every printing, every card, sets, function tags, recent reprints). The apps build
theirs from Scryfall's bulk file on every refresh. The browser edition can't sensibly stream
Scryfall's ~500 MB in every visitor's browser, so the server, which refreshes anyway, copies its
catalog into a compact file each day (`build`), and browsers download that (`install`).
docs/PUBLIC_EDITION_PLAN.md has the reasoning and Scryfall's terms.

A published pack folder holds:
    manifest.json           what's current: the pack file, the rules file, the price files
    cards-<stamp>.sqlite.gz the catalog; a new name each day, so it can be cached forever
    rules/<name>.txt        the Comprehensive Rules (Wizards' site doesn't answer web pages)
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .db import CATALOG_SCHEMA, CATALOG_TABLES

# Bumped when the pack's tables change in a way older engines can't read; a browser only
# installs packs of its own format.
PACK_FORMAT = 1
PACKS_KEPT = 2                  # the newest, and the one before for browsers mid-download

# `printings` is the same rows as set_cards; the pack makes it a view rather than a copy (11 MB).
PRINTINGS_VIEW = """
CREATE VIEW printings AS
    SELECT set_code, collector_number, scryfall_id, oracle_id, name FROM set_cards;
CREATE INDEX set_cards_by_number ON set_cards(set_code, collector_number);
"""
# Image URLs follow a fixed pattern from the id (gallery/sets.py image_for, web/sw.js), so the pack
# keeps only whether there's a back face: 25 MB less.
DROP_IMAGE_URLS = ("UPDATE set_cards SET image_front = NULL, "
                   "image_back = CASE WHEN image_back IS NOT NULL THEN 'y' END")


# Tables stored in the order of their key (WITHOUT ROWID), so the key isn't kept a second time in an
# index: card ids are random and don't compress, and they're most of the pack. Taggings are looked
# up by card, never by tag, so they're keyed card first and lose their by-card index.
KEYED_TABLES = {"set_cards": "scryfall_id", "oracle_cards": "oracle_id", "oracle_taggings": "oracle_id, slug"}
# set_cards_by_set is covered by the by-number index PRINTINGS_VIEW adds, which starts with the set.
DROPPED_INDEXES = ("taggings_by_oracle", "set_cards_by_set")


def _key_table(pack, table, key):
    """Recreate an empty table WITHOUT ROWID with `key` as its primary key, keeping its columns and
    its other indexes."""
    columns = ", ".join(f"{row[1]} {row[2]}" for row in pack.execute(f"PRAGMA table_info({table})"))
    indexes = [sql for (name, sql) in pack.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (table,))
        if name not in DROPPED_INDEXES]
    pack.execute(f"DROP TABLE {table}")
    pack.execute(f"CREATE TABLE {table} ({columns}, PRIMARY KEY ({key})) WITHOUT ROWID")
    for sql in indexes:
        pack.execute(sql)


def build(cache_path, out_dir, rules_dir=None, log=print):
    """Write today's pack from a refreshed cache database into `out_dir`, update its manifest and
    return the manifest. `rules_dir` is where the app keeps the downloaded rules file."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{Path(cache_path).as_posix()}?mode=ro", uri=True)
    info = dict(source.execute("SELECT key, value FROM catalog_info").fetchall())
    if "legality_formats" not in info or not source.execute("SELECT COUNT(*) FROM set_cards").fetchone()[0]:
        raise RuntimeError(f"{cache_path} has no complete catalog: refresh it first")
    source.close()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    work = out_dir / "building.sqlite"
    work.unlink(missing_ok=True)
    pack = sqlite3.connect(work)
    pack.executescript(CATALOG_SCHEMA.replace("{schema}", "main"))
    pack.execute("DROP TABLE printings")
    for table, key in KEYED_TABLES.items():
        _key_table(pack, table, key)
    pack.executescript(PRINTINGS_VIEW)
    pack.execute("ATTACH DATABASE ? AS source", (str(cache_path),))       # only read
    for table in CATALOG_TABLES:
        if table == "printings":
            continue
        columns = ", ".join(row[1] for row in pack.execute(f"PRAGMA main.table_info({table})"))
        pack.execute(f"INSERT INTO main.{table} ({columns}) SELECT {columns} FROM source.{table}")
    pack.execute(DROP_IMAGE_URLS)
    pack.execute("INSERT OR REPLACE INTO catalog_info VALUES ('pack_format', ?)", (str(PACK_FORMAT),))
    pack.execute("INSERT OR REPLACE INTO catalog_info VALUES ('pack_built_at', ?)", (stamp,))
    pack.commit()
    pack.execute("DETACH DATABASE source")
    pack.execute("VACUUM")
    pack.close()

    name = f"cards-{stamp}.sqlite.gz"
    raw = work.read_bytes()
    packed = gzip.compress(raw, compresslevel=9, mtime=0)
    (out_dir / name).write_bytes(packed)
    work.unlink()
    log(f"Pack {name}: {len(raw) / 1e6:.1f} MB, {len(packed) / 1e6:.1f} MB compressed")

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update(format=PACK_FORMAT, built_at=stamp, scryfall_updated_at=info.get("scryfall_updated_at"),
                    pack={"file": name, "bytes": len(packed), "sqlite_bytes": len(raw),
                          "sha256": hashlib.sha256(packed).hexdigest()},
                    credits="Card data from Scryfall (scryfall.com). Price history from MTGJSON (mtgjson.com), "
                            "MIT licensed. Magic: The Gathering is (c) Wizards of the Coast.")
    rules = _publish_rules(rules_dir, out_dir, log) if rules_dir else None
    if rules:
        manifest["rules"] = rules
    _write_json(manifest_path, manifest)
    for old in sorted(out_dir.glob("cards-*.sqlite.gz"))[:-PACKS_KEPT]:
        old.unlink()
    return manifest


def _publish_rules(rules_dir, out_dir, log):
    """Copy the app's current rules file beside the pack; None if it has none."""
    files = sorted(Path(rules_dir).glob("MagicCompRules*.txt"))
    if not files:
        log("No rules file to publish (open Tools → Rules once in the app to download it)")
        return None
    source = files[-1]
    target_dir = out_dir / "rules"
    target_dir.mkdir(exist_ok=True)
    shutil.copy(source, target_dir / source.name)
    for old in target_dir.glob("MagicCompRules*.txt"):
        if old.name != source.name:
            old.unlink()
    return {"file": f"rules/{source.name}", "name": source.name}


def _write_json(path, data):
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps(data, indent=1), encoding="utf-8")
    partial.replace(path)


# ---- in the browser ------------------------------------------------------------------------------

def installed(connection):
    """{built_at, format} of the attached pack, or {} before the first download."""
    try:
        return dict(connection.execute(
            "SELECT key, value FROM pack.catalog_info WHERE key IN ('pack_built_at', 'pack_format')").fetchall())
    except sqlite3.Error:
        return {}


def install(connection, packed):
    """Replace the attached pack with a downloaded one (gzip bytes) and attach it again."""
    from .db import attach_pack
    path = Path(os.environ["CARDCLOPS_PACK"])
    raw = gzip.decompress(packed)
    partial = path.with_suffix(".part")
    partial.write_bytes(raw)
    check = sqlite3.connect(partial)
    try:
        format_ = check.execute("SELECT value FROM catalog_info WHERE key = 'pack_format'").fetchone()
    finally:
        check.close()
    if not format_ or int(format_[0]) != PACK_FORMAT:
        partial.unlink()
        raise RuntimeError("The card data online is for a different version of Cardclops; reload the page")
    connection.commit()
    connection.execute("DETACH DATABASE pack")
    partial.replace(path)
    attach_pack(connection, path)
