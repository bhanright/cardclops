"""Your database as one file you can keep: download a backup, restore one (Settings → Backup).

The file is the whole of cardclops.sqlite: collection, decks and their history, binders, watchlist,
alerts, settings and the price history recorded so far. Card data isn't in it; that downloads again.
It's the same file in every edition, so a backup from the browser edition restores in the Windows
app and the other way round (the app keeps its own copies in Documents\\Cardclops\\Backups too).

This matters most in the browser edition, where the browser's storage is the only copy: clearing
the site's data deletes it. So the page reminds a browser user who hasn't downloaded a backup for
REMIND_AFTER_DAYS (the reminder is `status`'s "due").
"""
import os
import sqlite3
import tempfile
from datetime import datetime

from .db import USER_SCHEMA, _add_missing_columns, get_meta, set_meta
from .runtime import IN_BROWSER

REMIND_AFTER_DAYS = 30
# A file must have these to be a Cardclops database; the rest of the schema is added if missing
# (a backup from an older version).
REQUIRED_TABLES = {"holdings", "meta"}


def snapshot(connection):
    """(bytes of a consistent copy of your database, file name). Records when, for the reminder."""
    now = datetime.now()
    set_meta(connection, "backup_downloaded_at", now.isoformat(timespec="seconds"))
    connection.commit()
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        copy = sqlite3.connect(path)
        connection.backup(copy, name="main")          # SQLite's online backup: whole and consistent
        copy.close()
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        os.remove(path)
    return data, f"cardclops-backup-{now:%Y-%m-%d}.sqlite"


def status(connection):
    """{last, due}: when a backup was last downloaded, and whether to remind (browser edition,
    with a collection or decks, and none for REMIND_AFTER_DAYS)."""
    last = get_meta(connection, "backup_downloaded_at")
    has_data = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM holdings) OR EXISTS (SELECT 1 FROM decks)").fetchone()[0]
    stale = last is None or (datetime.now() - datetime.fromisoformat(last)).days >= REMIND_AFTER_DAYS
    return {"last": last, "due": bool(IN_BROWSER and has_data and stale)}


def describe(data):
    """What a backup file holds, after checking it's a sound Cardclops database; ValueError if not."""
    with _opened(data) as source:
        return _contents(source)


def restore(connection, data):
    """Replace your database with a backup's (the card data stays). Returns what it held."""
    with _opened(data) as source:
        found = _contents(source)
        connection.commit()
        source.backup(connection, name="main")     # page by page into the open database
    connection.executescript(USER_SCHEMA)          # tables added since the backup was made
    _add_missing_columns(connection)
    connection.commit()
    return found


class _opened:
    """A backup's bytes as an open, checked database in a temporary file."""

    def __init__(self, data):
        if not data.startswith(b"SQLite format 3\x00"):
            raise ValueError("That isn't a Cardclops backup (it isn't an SQLite database).")
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)

    def __enter__(self):
        self.connection = sqlite3.connect(self.path)
        try:
            tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not REQUIRED_TABLES <= tables:
                raise ValueError("That database isn't a Cardclops backup (it has no collection in it).")
            if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("That backup is damaged, so it can't be restored.")
        except sqlite3.DatabaseError as error:
            self.__exit__()
            raise ValueError(f"That backup can't be read ({error}).") from None
        except ValueError:
            self.__exit__()
            raise
        return self.connection

    def __exit__(self, *exc):
        if getattr(self, "connection", None):
            self.connection.close()
            self.connection = None
        if os.path.exists(self.path):
            os.remove(self.path)


def _contents(source):
    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    count = lambda table: source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in tables else 0
    copies = source.execute("SELECT COALESCE(SUM(quantity), 0) FROM holdings").fetchone()[0]
    made = source.execute("SELECT value FROM meta WHERE key = 'backup_downloaded_at'").fetchone()
    return {"copies": copies, "rows": count("holdings"), "decks": count("decks"), "binders": count("binders"),
            "watchlist": count("watchlist"), "made_at": made[0] if made else None}


def missing_cards(connection):
    """How many printings you hold that this device has no card details for (after a restore)."""
    return connection.execute(
        "SELECT COUNT(DISTINCT scryfall_id) FROM holdings WHERE scryfall_id NOT IN (SELECT scryfall_id FROM cards)").fetchone()[0]
