"""The JSON API, without HTTP: a request in, a Response out (docs/API.md lists the endpoints).

The web server (gallery/server.py) hands every /api/ request here and writes the Response back;
the browser edition (gallery/browser.py) calls handle() directly, with no server. So
nothing here may touch sockets, headers or the HTTP handler: the security checks, static files and
card images stay with the server.
"""
import base64
import os
import re
import threading
import traceback
import urllib.parse
from dataclasses import dataclass, field
from http import HTTPStatus

from . import ask, backups, manual, query
from . import setup as setup_module
from .net import NetError
from .runtime import IN_BROWSER
from .rules import rulings as card_rulings

ALERT_ROUTES = ("watchlist", "alerts", "alert-settings", "cards")


@dataclass
class Response:
    """What to answer. kind: "json" (body is data), "text" (body is a str), "file" (body is bytes,
    offered to save as `filename`), or "download" (body is data, offered to save as a JSON file).
    `after` runs once the answer is sent: a callable, or "shutdown" for the host to stop itself."""
    body: object = None
    status: int = HTTPStatus.OK
    kind: str = "json"
    content_type: str | None = None
    filename: str | None = None
    after: object = None
    headers: dict = field(default_factory=dict)


def _error(message, status):
    return Response({"error": message}, status)


class Api:
    """Routes one request against a Gallery. `app_window` is the Windows app's window (show() and
    close()), when there is one."""

    def __init__(self, gallery, app_window=None):
        self.gallery = gallery
        self.app_window = app_window

    def handle(self, method, path, params=None, body=None):
        """method "GET"/"POST"/"PATCH"/"PUT"/"DELETE"; path like "/api/decks/3"; params the query
        string's values; body the decoded JSON (writes)."""
        params, body = params or {}, body or {}
        try:
            if method == "GET":
                # Each piece is decoded after splitting, so an encoded "/" stays inside its piece.
                return self._get([urllib.parse.unquote(p) for p in path.strip("/").split("/")], params)
            if method == "POST":
                return self._post(path, body)
            return self._write(method, path, body)
        except Exception as error:          # a bad request must not take the server down
            traceback.print_exc()
            return _error(f"{type(error).__name__}: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    # ---- reading --------------------------------------------------------------------------------

    def _get(self, parts, params):
        gallery = self.gallery
        route = parts[1] if len(parts) > 1 else ""
        if parts[1:] == ["app", "port"]:
            return Response(self._port_settings())
        if route == "summary":
            return Response(gallery.summary())
        if route == "backup" and len(parts) == 2:
            with gallery.lock:
                data, filename = backups.snapshot(gallery.connection)
            return Response(data, kind="file", content_type="application/vnd.sqlite3", filename=filename)
        if route == "search":
            return Response(gallery.search(params))
        if route == "card" and len(parts) == 4 and parts[3] == "rulings":
            entries = gallery.collection.by_scryfall_id.get(parts[2])
            oracle_id = entries[0].oracle_id if entries else params.get("oracle_id")
            if not oracle_id:
                return _error("no such card", HTTPStatus.NOT_FOUND)
            try:
                return Response({"rulings": card_rulings(gallery.connection, parts[2], oracle_id, gallery.lock)})
            except Exception as error:
                return Response({"rulings": [], "error": f"Couldn't reach Scryfall for rulings ({error})"})
        if route == "rules":
            return self._rules(parts, params)
        if route == "card" and len(parts) == 3:
            detail = gallery.card(parts[2], whole_card=params.get("holdings") == "card")
            return Response(detail) if detail else _error("not in collection", HTTPStatus.NOT_FOUND)
        if route == "prices" and len(parts) == 3:
            return Response(gallery.price_series(parts[2]))
        if route == "portfolio":
            return Response(gallery.portfolio())
        if route == "movers":
            return Response(gallery.movers(params))
        if route == "stats":
            return Response(gallery.stats())
        if route == "extras":
            return Response(gallery.extras(params))
        if route == "tags":
            return Response(gallery.tags(params.get("q", "")))
        if route == "legality-changes":
            return Response(gallery.legality_changes())
        if route == "reprints":
            return Response(gallery.reprints(params))
        if route == "sets":
            with gallery.lock:
                if len(parts) == 2:
                    return Response(gallery.setbook.list(params))
                if len(parts) == 4 and parts[3] == "missing.txt":
                    return Response(gallery.setbook.missing_text(parts[2].lower(), params), kind="text")
                try:
                    return Response(gallery.setbook.detail(parts[2].lower(), params))
                except KeyError:
                    return _error("no such set", HTTPStatus.NOT_FOUND)
        if route == "setup" and len(parts) == 3 and parts[2] == "status":
            return Response(gallery.jobs.status())
        if route == "setup" and len(parts) == 3 and parts[2] == "progress":
            return Response(gallery.jobs.progress())
        if route in ALERT_ROUTES:
            return self._alerts("GET", parts, params, {})
        if route == "collection" and parts[2:] == ["manual"]:
            with gallery.lock:
                return Response({"rows": manual.rows(gallery.connection)})
        if route == "build" and len(parts) == 3 and parts[2] == "commanders":
            return Response(gallery.build_commanders(params))
        if route == "build" and len(parts) == 3 and parts[2] == "draft":
            if not params.get("commander"):
                return _error("Choose a commander", HTTPStatus.BAD_REQUEST)
            return Response(gallery.build_draft(params))
        if route == "archidekt" and len(parts) == 3 and parts[2] == "decks":
            from . import archidekt
            username = params.get("username", "").strip()
            if not username:
                return _error("Give an Archidekt username", HTTPStatus.BAD_REQUEST)
            try:
                return Response({"decks": archidekt.list_decks(username)})
            except NetError as error:
                return _error(f"Archidekt answered {error.status}" if error.status else str(error),
                              HTTPStatus.BAD_GATEWAY)
        if route == "binders":
            with gallery.lock:
                if len(parts) == 2:
                    return Response(gallery.binderbook.listing())
                if len(parts) == 4 and parts[2].isdigit() and parts[3] == "export":
                    binder_id = int(parts[2])
                    if binder_id not in gallery.binderbook.binders:
                        return _error("no such binder", HTTPStatus.NOT_FOUND)
                    text = gallery.binderbook.export_csv(binder_id)
                    name = re.sub(r"[^A-Za-z0-9 _-]", "", gallery.binderbook.binders[binder_id]["name"]).strip() or "binder"
                    return Response(text.encode(), kind="file", content_type="text/csv; charset=utf-8", filename=f"{name}.csv")
            return _error("unknown endpoint", HTTPStatus.NOT_FOUND)
        if route == "decks":
            return self._deck_read(parts, params)
        return _error("unknown endpoint", HTTPStatus.NOT_FOUND)

    def _rules(self, parts, params):
        book = self.gallery.rulebook
        try:
            if len(parts) == 2:
                book.check_now_and_then()
                return Response(book.overview())
            if parts[2] == "section" and len(parts) == 4:
                return Response(book.section(parts[3]))
            if parts[2] == "search":
                return Response(book.search(params.get("q", "")))
            if parts[2] == "glossary":
                return Response(book.glossary())
        except KeyError as error:
            return _error(str(error).strip("'"), HTTPStatus.NOT_FOUND)
        except LookupError as error:
            return _error(str(error), HTTPStatus.CONFLICT)
        return _error("unknown endpoint", HTTPStatus.NOT_FOUND)

    def _deck_read(self, parts, params):
        gallery = self.gallery
        if len(parts) == 2:
            try:
                return Response(gallery.decks_list(params.get("q", "")))
            except ValueError as error:
                return _error(str(error), HTTPStatus.BAD_REQUEST)
        if parts[2] == "export":
            with gallery.lock:
                data = gallery.deckbook.export_all()
            return Response(data, kind="download", filename=f"cardclops-decks-{data['exported_at'][:10]}.json")
        deck_id = int(parts[2])
        if deck_id not in gallery.deckbook.decks:
            return _error("no such deck", HTTPStatus.NOT_FOUND)
        action = parts[3] if len(parts) > 3 else None
        readers = {None: "deck_detail", "value-history": "deck_value_history", "suggestions": "deck_suggestions",
                   "versions": "deck_versions", "copy-policies": "deck_copy_policies"}
        if action in readers:
            return Response(getattr(gallery, readers[action])(deck_id))
        if action == "manafix":
            return Response(gallery.deck_manafix(deck_id, params))
        if action == "goldfish":
            return Response(gallery.deck_goldfish(deck_id, params))
        return _error("unknown endpoint", HTTPStatus.NOT_FOUND)

    # ---- writing --------------------------------------------------------------------------------

    def _post(self, path, body):
        gallery = self.gallery
        if path.startswith("/api/decks"):
            return self._decks(path, "POST", body)
        if path.startswith("/api/binders"):
            return self._binders("POST", path, body)
        if path in ("/api/backup/check", "/api/backup/restore"):
            return self._restore(path, body)
        if path == "/api/rules/download":
            try:
                return Response(gallery.rulebook.download())
            except Exception as error:
                return _error(f"Couldn't download the rules: {error}", HTTPStatus.BAD_GATEWAY)
        if path == "/api/app/port":
            return self._save_port(body)
        if path.startswith("/api/setup/") or path in ("/api/refresh", "/api/quit", "/api/app/show"):
            return self._setup(path, body)
        if path.startswith("/api/collection/"):
            return self._collection("POST", path, body)
        parts = path.strip("/").split("/")
        if len(parts) > 1 and parts[1] in ALERT_ROUTES:
            return self._alerts("POST", parts, {}, body)
        if path == "/api/deckcheck":
            return Response(gallery.deck_checker.check(body.get("text", ""), gallery.summarize))
        if path == "/api/ask":
            question = (body.get("question") or "").strip()
            if not question:
                return _error("Ask a question first.", HTTPStatus.BAD_REQUEST)
            try:
                return Response(ask.translate(question, gallery.collection.tag_index, query.compile_query,
                                              api_key=(body.get("api_key") or "").strip() or None))
            except (ask.AskError, ValueError) as error:
                return Response({"error": str(error)})
        return _error("unknown endpoint", HTTPStatus.NOT_FOUND)

    def _write(self, method, path, body):
        """PATCH, PUT and DELETE."""
        if path.startswith("/api/decks/"):
            return self._decks(path, method, body)
        if path.startswith("/api/binders"):
            return self._binders(method, path, body)
        if path.startswith("/api/collection/"):
            return self._collection(method, path, body)
        parts = path.strip("/").split("/")
        if len(parts) > 1 and parts[1] in ALERT_ROUTES:
            return self._alerts(method, parts, {}, body)
        return _error("unknown endpoint", HTTPStatus.NOT_FOUND)

    def _restore(self, path, body):
        """A backup file, sent as base64: `check` says what it holds; `restore` replaces your
        database with it, then fetches any card details it needs (gallery/backups.py)."""
        gallery = self.gallery
        try:
            data = base64.b64decode(body.get("data") or "", validate=True)
        except ValueError:
            return _error("The file didn't arrive whole; try again.", HTTPStatus.BAD_REQUEST)
        try:
            if path == "/api/backup/check":
                return Response(backups.describe(data))
            with gallery.lock:
                found = backups.restore(gallery.connection, data)
            gallery.load()
        except ValueError as error:
            return _error(str(error), HTTPStatus.BAD_REQUEST)
        # Cards the restored collection holds that this device hasn't details for yet.
        try:
            found["refreshing"] = bool(gallery.jobs.refresh()) if backups.missing_cards(gallery.connection) else False
        except RuntimeError:                           # a job is already running; it will catch up next time
            found["refreshing"] = False
        return Response(found)

    def _setup(self, path, body):
        jobs = getattr(self.gallery, "jobs", None)
        try:
            if path == "/api/setup/download":
                return Response(jobs.download(bool(body.get("price_history", True))))
            if path == "/api/setup/import":
                if not body.get("text"):
                    return _error("Choose a CSV file first.", HTTPStatus.BAD_REQUEST)
                mode = "add" if body.get("mode") == "add" else "replace"
                return Response(jobs.import_collection(body.get("filename", "collection.csv"), body["text"], mode))
            if path == "/api/setup/options":
                scheduled = setup_module.set_daily_refresh(bool(body.get("daily_refresh")))
                return Response({"daily_refresh_scheduled": scheduled})
            if path == "/api/setup/complete":
                return Response(jobs.complete())
            if path == "/api/refresh":
                return Response(jobs.refresh())
            if path == "/api/quit":
                if IN_BROWSER:
                    return _error("In a browser, close the tab to quit.", HTTPStatus.BAD_REQUEST)
                if self.app_window:                   # the Windows app: closing its window ends it
                    window = self.app_window
                    return Response({"quitting": True}, after=lambda: threading.Thread(target=window.close, daemon=True).start())
                return Response({"quitting": True}, after="shutdown")
            if path == "/api/app/show":
                # Starting Cardclops.exe while it runs brings its window forward (gallery/app.py).
                if self.app_window:
                    self.app_window.show()
                return Response({"shown": bool(self.app_window)})
        except RuntimeError as error:
            return _error(str(error), HTTPStatus.CONFLICT)
        return _error("unknown endpoint", HTTPStatus.NOT_FOUND)

    # ---- the Windows app's port (Settings → App) ----

    def _port_settings(self):
        from .paths import DEFAULT_PORT, saved_port
        return {"port": getattr(self.gallery, "port", None), "saved": saved_port() or DEFAULT_PORT,
                "default": DEFAULT_PORT, "from_environment": bool(os.environ.get("CARDCLOPS_PORT")),
                # Only the Windows app picks its own port: the phone app takes a free one, a server
                # its service's, and the browser edition has none.
                "available": bool(self.app_window) or setup_module.installed()}

    def _save_port(self, body):
        from .paths import DEFAULT_PORT, save_port, valid_port
        if not self._port_settings()["available"]:
            return _error("Only the Windows app chooses its port.", HTTPStatus.BAD_REQUEST)
        wanted = body.get("port")
        port = valid_port(wanted) if wanted not in (None, "") else DEFAULT_PORT
        if port is None:
            return _error("Choose a port from 1024 to 65535.", HTTPStatus.BAD_REQUEST)
        save_port(None if port == DEFAULT_PORT else port)
        return Response({**self._port_settings(), "restart_needed": port != getattr(self.gallery, "port", None)})

    def _collection(self, method, path, body):
        try:
            return Response(self.gallery.collection_change(method, path.strip("/").split("/"), body))
        except KeyError as error:
            return _error(str(error).strip("'"), HTTPStatus.NOT_FOUND)
        except (ValueError, LookupError) as error:
            return _error(str(error), HTTPStatus.BAD_REQUEST)

    def _alerts(self, method, parts, params, body):
        try:
            return Response(self.gallery.alert_request(method, parts, params, body))
        except KeyError as error:
            return _error(str(error), HTTPStatus.NOT_FOUND)
        except (ValueError, LookupError) as error:
            return _error(str(error), HTTPStatus.BAD_REQUEST)

    def _binders(self, method, path, body):
        try:
            return Response(self.gallery.binder_change(method, path.strip("/").split("/"), body))
        except KeyError as error:
            return _error(str(error).strip("'"), HTTPStatus.NOT_FOUND)
        except (ValueError, LookupError, query.QueryError) as error:
            return _error(str(error), HTTPStatus.BAD_REQUEST)

    def _decks(self, path, method, body):
        parts = path.strip("/").split("/")          # api, decks, <id> or "import", [action]
        deck_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        action = parts[3] if len(parts) > 3 else (parts[2] if deck_id is None and len(parts) > 2 else None)
        try:
            return Response(self.gallery.deck_change(method, deck_id, action, body, tuple(parts[4:])))
        except KeyError as error:
            return _error(str(error), HTTPStatus.NOT_FOUND)
        except (ValueError, LookupError) as error:
            return _error(str(error), HTTPStatus.BAD_REQUEST)
