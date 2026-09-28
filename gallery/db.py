"""SQLite schema and connection helper.

Two database files, opened as one connection (paths.py says where they live):
- cardclops.sqlite, yours: the collection, recorded price history, decks and
  their versions, watchlist, alerts and settings. None of it can be downloaded
  again, so it is backed up after every refresh.
- cards.sqlite, cache: Scryfall's card data, rebuilt by the next refresh if lost.
It is attached as the schema "cache"; table names are unique across both files,
so queries never need to say which file a table is in.
"""
import json
import shutil
import sqlite3
import zlib
from datetime import date, datetime, timedelta

from .paths import (BACKUP_DIR, CACHE_DATABASE_PATH, DATABASE_PATH, IMAGE_CACHE_DIR, LEGACY_DATA_DIR,
                    LOG_DIR, RAW_DIR, ensure_dirs)

USER_SCHEMA = """
-- One row per line of the ManaBox export: a printing in a given finish/condition.
CREATE TABLE IF NOT EXISTS holdings (
    row_id            INTEGER PRIMARY KEY,
    scryfall_id       TEXT NOT NULL,
    name              TEXT NOT NULL,
    set_code          TEXT,
    collector_number  TEXT,
    finish            TEXT NOT NULL,      -- normal | foil | etched
    rarity            TEXT,
    quantity          INTEGER NOT NULL,
    manabox_id        TEXT,
    purchase_price    REAL,               -- per copy, in purchase_currency
    purchase_currency TEXT,
    misprint          INTEGER,
    altered           INTEGER,
    signed            INTEGER,
    proxy             INTEGER,
    condition         TEXT,
    language          TEXT,
    added_at          TEXT,               -- ISO timestamp from ManaBox, or when added by hand
    source            TEXT NOT NULL DEFAULT 'import'   -- import (a collection CSV) | manual (added in Cardclops)
);
CREATE INDEX IF NOT EXISTS holdings_by_card ON holdings(scryfall_id);

-- Prices, one row per printing, source, finish and currency, with the points
-- packed as zlib-compressed JSON {"YYYY-MM-DD": price} (see pack_points).
-- The last 30 days are daily; older days are thinned to one closing price per
-- month (see compact_points), so a series stays around 30 + 12/year points.
-- `source` names where the numbers came from ('scryfall',
-- 'mtgjson:tcgplayer:retail', ...).
CREATE TABLE IF NOT EXISTS price_series (
    scryfall_id TEXT NOT NULL,
    source      TEXT NOT NULL,
    finish      TEXT NOT NULL,            -- normal | foil | etched
    currency    TEXT NOT NULL,            -- USD | EUR
    points      BLOB NOT NULL,
    PRIMARY KEY (scryfall_id, source, finish, currency)
) WITHOUT ROWID;

-- Format legality as last seen, so a refresh can report bans and rotations
-- that touch the collection.
CREATE TABLE IF NOT EXISTS legality_seen (
    oracle_id  TEXT PRIMARY KEY,
    legalities TEXT NOT NULL              -- JSON {format: status}
);
CREATE TABLE IF NOT EXISTS legality_changes (
    day        TEXT NOT NULL,
    oracle_id  TEXT NOT NULL,
    name       TEXT NOT NULL,
    format     TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL
);

-- Decks you have built (docs/DECKS.md). Everything here is yours: it is not
-- rebuilt from any download, so it is worth backing up along with prices.
CREATE TABLE IF NOT EXISTS decks (
    deck_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    format      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',     -- active | inactive
    priority    INTEGER NOT NULL DEFAULT 0,         -- lower claims shared copies first
    copy_policy TEXT NOT NULL DEFAULT 'default',    -- default | budget | bling (docs/TOOLS2.md)
    notes       TEXT NOT NULL DEFAULT '',
    source      TEXT,
    source_url  TEXT,
    raw_text    TEXT,                               -- the list as imported
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deck_lines (
    line_id          INTEGER PRIMARY KEY,
    deck_id          INTEGER NOT NULL REFERENCES decks(deck_id) ON DELETE CASCADE,
    position         INTEGER NOT NULL,
    section          TEXT NOT NULL,                 -- commander | companion | main | sideboard | maybeboard
    quantity         INTEGER NOT NULL,
    name             TEXT NOT NULL,                 -- as written, or the matched card's name
    oracle_id        TEXT,                          -- null when no card matched
    scryfall_id      TEXT,                          -- the printing to show and to prefer
    requested_set    TEXT,
    requested_number TEXT,
    requested_finish TEXT                           -- normal | foil | etched, when the list says
);
CREATE INDEX IF NOT EXISTS deck_lines_by_deck ON deck_lines(deck_id);
CREATE INDEX IF NOT EXISTS deck_lines_by_oracle ON deck_lines(oracle_id);
-- Copies you chose for a line, by pool ("<scryfall_id>|<finish>"); the rest are allocated automatically.
CREATE TABLE IF NOT EXISTS deck_pins (
    line_id   INTEGER NOT NULL REFERENCES deck_lines(line_id) ON DELETE CASCADE,
    pool      TEXT NOT NULL,
    quantity  INTEGER NOT NULL,
    PRIMARY KEY (line_id, pool)
) WITHOUT ROWID;

-- Every version of every deck's list, with a snapshot of its headline
-- statistics, for the changelog (docs/TOOLS2.md).
CREATE TABLE IF NOT EXISTS deck_versions (
    version_id  INTEGER PRIMARY KEY,
    deck_id     INTEGER NOT NULL REFERENCES decks(deck_id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    reason      TEXT NOT NULL,                      -- import | replace | sync | manafix | commander | restore
    lines       TEXT NOT NULL,                      -- JSON list of line records
    stats       TEXT                                -- JSON headline statistics
);
CREATE INDEX IF NOT EXISTS deck_versions_by_deck ON deck_versions(deck_id);

-- Price alerts (docs/TOOLS2.md): printings you watch, and alerts that fired.
CREATE TABLE IF NOT EXISTS watchlist (
    watch_id    INTEGER PRIMARY KEY,
    scryfall_id TEXT NOT NULL,
    finish      TEXT NOT NULL DEFAULT 'normal',
    target_usd  REAL NOT NULL,
    direction   TEXT NOT NULL DEFAULT 'below',      -- below | above
    note        TEXT NOT NULL DEFAULT '',
    armed       INTEGER NOT NULL DEFAULT 1,         -- 0 after firing, until the price goes back across
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id    INTEGER PRIMARY KEY,
    day         TEXT NOT NULL,
    kind        TEXT NOT NULL,                      -- target | move | reprint | legality
    key         TEXT NOT NULL,                      -- what makes the alert unique within a day and kind
    scryfall_id TEXT,
    name        TEXT NOT NULL,
    message     TEXT NOT NULL,
    price_usd   REAL,
    seen        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (day, kind, key)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Binders, boxes and lists you file copies into (gallery/binders.py). Contents are counts per
-- printing and finish, not holdings rows, so they survive re-importing the collection.
CREATE TABLE IF NOT EXISTS binders (
    binder_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'binder',     -- binder | box | list
    notes       TEXT NOT NULL DEFAULT '',
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS binder_cards (
    binder_id   INTEGER NOT NULL REFERENCES binders(binder_id) ON DELETE CASCADE,
    scryfall_id TEXT NOT NULL,
    finish      TEXT NOT NULL,                      -- normal | foil | etched
    quantity    INTEGER NOT NULL,
    PRIMARY KEY (binder_id, scryfall_id, finish)
) WITHOUT ROWID;
"""

# Scryfall's card data. "{schema}" is "cache" in the app (a second file) and
# "main" when everything lives in one database, as in the tests.
CACHE_SCHEMA = """
-- Scryfall's card object for every printing the collection holds.
-- `raw` keeps the whole object so new features can read fields we did not
-- pull out into columns.
CREATE TABLE IF NOT EXISTS {schema}.cards (
    scryfall_id    TEXT PRIMARY KEY,
    oracle_id      TEXT,
    name           TEXT NOT NULL,
    raw            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS {schema}.cards_by_oracle ON cards(oracle_id);

-- One row per distinct card (oracle identity) in all of Magic, used where the
-- question is about cards the collection does not hold: deck checks and set sizes.
CREATE TABLE IF NOT EXISTS {schema}.oracle_cards (
    oracle_id      TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    name_folded    TEXT NOT NULL,          -- lower case, for decklist matching
    type_line      TEXT,
    mana_cost      TEXT,
    color_identity TEXT,
    cheapest_usd   REAL,                  -- cheapest non-foil printing, when known
    scryfall_id    TEXT                   -- a representative printing, for images
);
CREATE INDEX IF NOT EXISTS {schema}.oracle_by_name ON oracle_cards(name_folded);

CREATE TABLE IF NOT EXISTS {schema}.sets (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    set_type    TEXT,
    released_at TEXT,
    card_count  INTEGER,
    icon_svg_uri TEXT,
    printed_size INTEGER,                 -- the set's own numbering, without variants (when Scryfall knows it)
    digital     INTEGER,
    parent_set_code TEXT
);

-- Every card of every set, for the collector view (docs/SETUP.md): enough to
-- draw an unowned card and price it, without keeping Scryfall's whole object.
CREATE TABLE IF NOT EXISTS {schema}.set_cards (
    scryfall_id      TEXT PRIMARY KEY,
    set_code         TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    name             TEXT NOT NULL,
    oracle_id        TEXT,
    rarity           TEXT,
    type_line        TEXT,
    mana_cost        TEXT,
    colors           TEXT,                -- letters from WUBRG
    layout           TEXT,
    finishes         TEXT,                -- JSON list: nonfoil, foil, etched
    prices           TEXT,                -- JSON {usd, usd_foil, usd_etched}
    image_front      TEXT,                -- Scryfall's "normal" image URL; other sizes swap the size segment
    image_back       TEXT,
    variation        INTEGER,             -- 1 for promos and variants outside the main numbering
    released_at      TEXT,
    in_booster       INTEGER              -- 1 when the card comes in the set's boosters
);
CREATE INDEX IF NOT EXISTS {schema}.set_cards_by_set ON set_cards(set_code);
CREATE INDEX IF NOT EXISTS {schema}.set_cards_by_oracle ON set_cards(oracle_id);

-- Scryfall Tagger's functional tags ("removal-destroy", "ramp", ...).
CREATE TABLE IF NOT EXISTS {schema}.oracle_tags (
    slug        TEXT PRIMARY KEY,
    tag_id      TEXT,
    label       TEXT,
    description TEXT,
    parent_ids  TEXT,                     -- JSON list of tag_id
    child_ids   TEXT                      -- JSON list of tag_id
);
CREATE TABLE IF NOT EXISTS {schema}.oracle_taggings (
    slug      TEXT NOT NULL,
    oracle_id TEXT NOT NULL,
    PRIMARY KEY (slug, oracle_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS {schema}.taggings_by_oracle ON oracle_taggings(oracle_id);

-- Every printing in Scryfall's default bulk file, so a decklist line such as
-- "1 Sol Ring (C21) 263" resolves even when you don't own that printing.
CREATE TABLE IF NOT EXISTS {schema}.printings (
    set_code         TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    scryfall_id      TEXT NOT NULL,
    oracle_id        TEXT,
    name             TEXT NOT NULL,
    PRIMARY KEY (set_code, collector_number)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS {schema}.printings_by_id ON printings(scryfall_id);

-- Reprints previewed or released in the last 90 days (docs/TOOLS.md, reprint
-- radar). Rebuilt from each refresh's bulk file.
-- Scryfall's rulings for a card (gallery/rules.py), fetched when a card is opened; kept a month.
CREATE TABLE IF NOT EXISTS {schema}.rulings (
    oracle_id   TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    data        TEXT NOT NULL                       -- JSON [{date, source, text}]
);

CREATE TABLE IF NOT EXISTS {schema}.new_printings (
    scryfall_id      TEXT PRIMARY KEY,
    oracle_id        TEXT NOT NULL,
    name             TEXT NOT NULL,
    set_code         TEXT,
    set_name         TEXT,
    set_type         TEXT,
    released_at      TEXT,
    collector_number TEXT,
    rarity           TEXT,
    previewed_at     TEXT,
    image_uris       TEXT                   -- JSON {size: url} of the front face
);
CREATE INDEX IF NOT EXISTS {schema}.new_printings_by_oracle ON new_printings(oracle_id);

"""

USER_TABLES = ("holdings", "price_series", "legality_seen", "legality_changes", "decks", "deck_lines",
               "deck_pins", "deck_versions", "watchlist", "alerts", "meta", "binders", "binder_cards")
CACHE_TABLES = ("cards", "oracle_cards", "sets", "oracle_tags", "oracle_taggings", "printings", "new_printings",
                "set_cards", "rulings")

# Everything in one database: for tests and scratch copies.
SCHEMA = USER_SCHEMA + CACHE_SCHEMA.replace("{schema}", "main")
BACKUPS_KEPT = 14


def connect(path=DATABASE_PATH, cache_path=CACHE_DATABASE_PATH):
    """Your database with the card cache attached. With `cache_path=None`, one file holds everything."""
    ensure_dirs()
    if path == DATABASE_PATH and not DATABASE_PATH.exists() and (LEGACY_DATA_DIR / "collection.sqlite").exists():
        migrate_legacy()
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")         # deleting a deck deletes its lines and pins
    connection.executescript(USER_SCHEMA)
    if cache_path is None:
        connection.executescript(CACHE_SCHEMA.replace("{schema}", "main"))
        _add_missing_columns(connection, cache_schema="main")
    else:
        connection.execute("ATTACH DATABASE ? AS cache", (str(cache_path),))
        connection.execute("PRAGMA cache.journal_mode=WAL")
        _add_missing_columns(connection, cache_schema="cache", only_cache=True)   # before indexes that use them
        connection.executescript(CACHE_SCHEMA.replace("{schema}", "cache"))
        _add_missing_columns(connection, cache_schema="cache")
    return connection


def backup(connection, when=None):
    """Copy your database into Backups/ (SQLite's online backup, safe while the gallery runs); keep the newest 14."""
    when = when or datetime.now()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"cardclops-{when:%Y-%m-%d}.sqlite"
    destination = sqlite3.connect(target)
    try:
        connection.backup(destination, name="main")
    finally:
        destination.close()
    for old in sorted(BACKUP_DIR.glob("cardclops-*.sqlite"))[:-BACKUPS_KEPT]:
        old.unlink()
    return target


def migrate_legacy():
    """Move data/collection.sqlite (the single-file layout before September 2026) into the two new
    files, and data/raw and data/images into the cache folder. The old folder is renamed, not deleted."""
    legacy = LEGACY_DATA_DIR / "collection.sqlite"
    print(f"Moving your data from {LEGACY_DATA_DIR} to {DATABASE_PATH.parent} and {CACHE_DATABASE_PATH.parent}…")
    old = sqlite3.connect(legacy)
    old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    old.close()
    new = sqlite3.connect(DATABASE_PATH)
    new.executescript(USER_SCHEMA)
    new.execute("ATTACH DATABASE ? AS cache", (str(CACHE_DATABASE_PATH),))
    new.executescript(CACHE_SCHEMA.replace("{schema}", "cache"))
    new.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
    _add_missing_columns(new, schema="main", cache_schema="cache")
    legacy_tables = {row[0] for row in new.execute("SELECT name FROM legacy.sqlite_master WHERE type = 'table'")}
    for schema, tables in (("main", USER_TABLES), ("cache", CACHE_TABLES)):
        for table in tables:
            if table not in legacy_tables:
                continue
            target = [row[1] for row in new.execute(f"PRAGMA {schema}.table_info({table})")]
            source = {row[1] for row in new.execute(f"PRAGMA legacy.table_info({table})")}
            columns = ", ".join(c for c in target if c in source)       # by name: columns added later sit elsewhere
            new.execute(f"INSERT OR REPLACE INTO {schema}.{table} ({columns}) SELECT {columns} FROM legacy.{table}")
    new.commit()
    new.execute("DETACH DATABASE legacy")
    new.close()
    for folder, target in ((LEGACY_DATA_DIR / "raw", RAW_DIR), (LEGACY_DATA_DIR / "images", IMAGE_CACHE_DIR)):
        if folder.exists():
            for item in folder.iterdir():
                destination = target / item.name
                if not destination.exists():
                    shutil.move(str(item), str(destination))
    log = LEGACY_DATA_DIR / "refresh.log"
    if log.exists() and not (LOG_DIR / "refresh.log").exists():
        shutil.move(str(log), str(LOG_DIR / "refresh.log"))
    retired = LEGACY_DATA_DIR.with_name(f"data.migrated-{date.today():%Y%m%d}")
    LEGACY_DATA_DIR.rename(retired)
    print(f"Done. The old folder is kept as {retired}; delete it once you're happy.")


# Columns added after a table was first created: CREATE TABLE IF NOT EXISTS
# leaves an existing table alone, so they are added here. The decks table holds
# the owner's own work and cannot be rebuilt from a download.
LATE_COLUMNS = [("user", "decks", "copy_policy", "TEXT NOT NULL DEFAULT 'default'"),
                ("user", "holdings", "source", "TEXT NOT NULL DEFAULT 'import'"),
                ("cache", "sets", "printed_size", "INTEGER"),
                ("cache", "sets", "digital", "INTEGER"),
                ("cache", "sets", "parent_set_code", "TEXT"),
                ("cache", "set_cards", "in_booster", "INTEGER")]


def _add_missing_columns(connection, schema="main", cache_schema="main", only_cache=False):
    for where, table, column, definition in LATE_COLUMNS:
        if only_cache and where != "cache":
            continue
        target = cache_schema if where == "cache" else schema
        existing = {row[1] for row in connection.execute(f"PRAGMA {target}.table_info({table})")}
        if existing and column not in existing:       # a missing table is created by the schema script
            connection.execute(f"ALTER TABLE {target}.{table} ADD COLUMN {column} {definition}")
    connection.commit()


def pack_points(points):
    """{day: price} -> compressed bytes. Dates repeat almost entirely, so zlib shrinks a series ~6x."""
    return zlib.compress(json.dumps(dict(sorted(points.items())), separators=(",", ":")).encode(), 6)


def unpack_points(blob):
    return json.loads(zlib.decompress(blob))


# Price points younger than this many days are kept daily. Older ones keep
# only the last recorded day of each calendar month: that month's closing
# price, so month-to-month changes stay exact. 30 keeps the 30-day change
# window answerable from daily data.
DAILY_PRICE_DAYS = 30


def compact_points(points, daily_days=DAILY_PRICE_DAYS):
    """Thin {day: price} to daily for the newest `daily_days` days and one close per month before.

    Age is measured from the series' own newest day, not the clock, so a
    series that stops updating is not thinned any further. Applying it twice
    changes nothing.
    """
    if not points:
        return points
    newest = date.fromisoformat(max(points))
    cutoff = (newest - timedelta(days=daily_days)).isoformat()
    month_close = {}
    kept = {}
    for day, price in points.items():
        if day >= cutoff:
            kept[day] = price
        elif day > month_close.get(day[:7], ""):
            month_close[day[:7]] = day
    for day in month_close.values():
        kept[day] = points[day]
    return kept


def merge_price_points(connection, scryfall_id, source, finish, currency, points):
    """Add {day: price} points to a series (a day already stored is overwritten), then compact it."""
    row = connection.execute(
        "SELECT points FROM price_series WHERE scryfall_id = ? AND source = ? AND finish = ? AND currency = ?",
        (scryfall_id, source, finish, currency)).fetchone()
    merged = unpack_points(row[0]) if row else {}
    merged.update(points)
    merged = compact_points(merged)
    connection.execute("INSERT OR REPLACE INTO price_series VALUES (?, ?, ?, ?, ?)",
                       (scryfall_id, source, finish, currency, pack_points(merged)))


def get_meta(connection, key, default=None):
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(connection, key, value):
    connection.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
