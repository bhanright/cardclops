"""Cardclops in a web browser: the public edition's engine (docs/PUBLIC_EDITION_PLAN.md).

web/engine/worker.js runs this in Pyodide inside a Web Worker: start() once, then handle() for every
request the page's service worker (web/sw.js) passes on, so the web page talks to it exactly as it
talks to the server. The databases live in the browser's private storage (OPFS), which the worker
loads at /data and copies back after writes, whenever idle() says no transaction is open.
"""
import json
import os
import sqlite3

_api = None


def start(data_url, home="/data/user", cache="/data/cache", pack="/data/pack/cards.sqlite"):
    """Open the databases and build the engine. The environment is set before any other gallery
    module is imported, since gallery/paths.py reads it once. `data_url` is where the card pack and
    price files are published (gallery/pack.py)."""
    global _api
    os.environ.update(CARDCLOPS_HOME=home, CARDCLOPS_CACHE=cache, CARDCLOPS_PACK=pack, CARDCLOPS_DATA_URL=data_url,
                      CARDCLOPS_JOURNAL="MEMORY", CARDCLOPS_EDITION="browser")
    os.makedirs(os.path.dirname(pack), exist_ok=True)
    from .api import Api
    from .server import Gallery
    discarded = []
    try:
        gallery = Gallery(_connect(pack))
    except sqlite3.DatabaseError:
        discarded = _discard_damaged(pack)
        gallery = Gallery(_connect(pack))
    _api = Api(gallery)
    gallery.jobs.refresh_if_stale()          # a day-old pack, or cards the cache lost: fetched in the background
    return {"rows": len(gallery.collection.entries), "discarded": discarded}


def _connect(pack):
    from . import db
    from .paths import CACHE_DATABASE_PATH, DATABASE_PATH
    return db.connect(DATABASE_PATH, CACHE_DATABASE_PATH, pack_path=pack)


def _discard_damaged(pack):
    """A damaged card cache or pack is deleted, since both download again; a damaged database of
    your own is not touched, and the error stands (restoring a backup is the way back)."""
    from .db import set_meta
    from .paths import CACHE_DATABASE_PATH, DATABASE_PATH
    discarded = []
    for name, path in (("cache", CACHE_DATABASE_PATH), ("pack", pack)):
        if os.path.exists(path) and not _healthy(path):
            os.remove(path)
            discarded.append(name)
    if not _healthy(DATABASE_PATH):
        raise RuntimeError("Your Cardclops database in this browser is damaged. Restore a backup from Tools.")
    if discarded:
        connection = sqlite3.connect(DATABASE_PATH)
        set_meta(connection, "scryfall_updated_at", "")          # so the refresh on start fetches it all again
        connection.commit()
        connection.close()
    return discarded


def _healthy(path):
    try:
        connection = sqlite3.connect(path)
        try:
            return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return False


def idle():
    """True when no transaction is open, so the database files are whole and safe to copy."""
    return _api is None or not _api.gallery.connection.in_transaction


def handle(method, path, params_json="", body_json=""):
    """One request, as the service worker saw it. Returns a dict for the worker: status, kind,
    content_type, filename, body (text, or bytes for a file) and whether it may have written."""
    params = json.loads(params_json) if params_json else {}
    body = json.loads(body_json) if body_json else {}
    changes = _api.gallery.connection.total_changes
    response = _api.handle(method, path, params, body)
    # Saved when it wrote, whatever the method (a backup download records when it was made).
    wrote = method != "GET" or _api.gallery.connection.total_changes != changes
    content_type, text = response.content_type, response.body
    if response.kind == "json":
        content_type, text = "application/json; charset=utf-8", json.dumps(response.body, separators=(",", ":"))
    elif response.kind == "download":
        content_type, text = "application/json; charset=utf-8", json.dumps(response.body, indent=1, ensure_ascii=False)
    elif response.kind == "text":
        content_type = "text/plain; charset=utf-8"
    return {"status": int(response.status), "kind": response.kind, "content_type": content_type,
            "filename": response.filename, "body": text, "wrote": wrote}
