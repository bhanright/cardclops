"""The local web server: static files, the JSON API in docs/API.md, and card images.

Stdlib only. Everything is read from the in-memory Collection; the database
is touched for price series, the portfolio and a few reference tables.
"""
import hmac
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import ask, query
from .collection import COLOR_ORDER, Collection
from .db import get_meta
from .deckcheck import DeckChecker
from . import __version__
from . import manual
from . import setup as setup_module
from .decks import DeckBook
from .setup import Jobs
from .sets import SetBook, image_url
from .radar import Radar
from .alerts import Alerts
from .paths import IMAGE_CACHE_DIR, STATIC_DIR, USER_AGENT, ssl_context
from .pricebook import SOURCE_LABELS, PriceBook
from .similar import SimilarityIndex

IMAGE_SIZES = {"small", "normal", "large", "art_crop", "png", "border_crop"}
FORMATS = ["standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander", "oathbreaker",
           "brawl", "standardbrawl", "historic", "timeless", "alchemy", "explorer", "penny", "duel",
           "oldschool", "premodern", "predh", "paupercommander", "gladiator", "future"]
CARD_TYPES = ["Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land", "Battle", "Kindred"]
RARITY_ORDER = {"common": 0, "uncommon": 1, "rare": 2, "special": 3, "mythic": 4, "bonus": 5}
MAX_PAGE = 500
ALERT_ROUTES = ("watchlist", "alerts", "alert-settings", "cards")


class Gallery:
    """Everything the handlers need, built once at start-up."""

    def __init__(self, connection):
        self.connection = connection
        self.lock = threading.Lock()     # sqlite3 connection shared across handler threads
        print("Loading collection…")
        self.jobs = Jobs(self)
        self.load()
        print(f"Ready: {len(self.collection.entries):,} rows")

    def load(self):
        """Build every in-memory view of the data, then swap them in together."""
        connection = self.connection
        with self.lock:
            version = get_meta(connection, "data_version")
            collection = Collection(connection)
            query.prepare(collection.entries)      # precompute search fields so no query pays for them
            prices = PriceBook(connection, collection)
            similarity = SimilarityIndex(collection)
            deck_checker = DeckChecker(connection, collection)
            deckbook = DeckBook(connection, collection)          # sets entry.used and entry.decks
            radar = Radar(connection, collection)
            alerts = Alerts(connection)
            setbook = SetBook(connection, collection)
            set_info = {row["code"]: dict(row) for row in connection.execute("SELECT * FROM sets")}
        self.collection, self.prices, self.similarity, self.deck_checker, self.deckbook, self.set_info = (
            collection, prices, similarity, deck_checker, deckbook, set_info)
        self.radar = radar
        self.alerts = alerts
        self.setbook = setbook
        self.data_version = version

    def watch_for_new_data(self, interval_seconds=60):
        """Reload when `import`, `refresh` or `history` has run since we loaded (the daily task, say)."""
        def loop():
            while True:
                threading.Event().wait(interval_seconds)
                try:
                    with self.lock:
                        version = get_meta(self.connection, "data_version")
                    if version != self.data_version:
                        print(f"New data ({version}); reloading…")
                        self.load()
                        print(f"Reloaded: {len(self.collection.entries):,} rows")
                except Exception:
                    traceback.print_exc()
        threading.Thread(target=loop, name="data-watcher", daemon=True).start()

    # ---- shapes -------------------------------------------------------------

    def summarize(self, entry, quantity=None, finish=None, row_id=True):
        card = entry.card
        quantity = entry.quantity if quantity is None else quantity
        summary = {
            "row_id": entry.row_id if row_id else None,
            "scryfall_id": entry.scryfall_id, "oracle_id": entry.oracle_id, "name": entry.name,
            "set_code": entry.set_code, "set_name": entry.set_name, "collector_number": entry.collector_number,
            "rarity": entry.rarity, "finish": finish or entry.finish, "quantity": quantity,
            "mana_cost": entry.mana_cost, "type_line": entry.type_line, "cmc": entry.cmc,
            "colors": [c for c in COLOR_ORDER if c in entry.colors],
            "color_identity": [c for c in COLOR_ORDER if c in entry.color_identity],
            "layout": entry.layout, "double_faced": entry.is_double_faced,
            "price_usd": entry.price_usd,
            "value_usd": round((entry.price_usd or 0) * quantity, 2),
            "purchase_price": entry.purchase_price,
            "source": entry.source,
            "change": {f"d{w}": self.prices.change_percent(entry, w) for w in (1, 7, 30)},
            "image": f"/img/{entry.scryfall_id}/front/normal",
        }
        # Copies in active decks: this row's, or the whole card's when the tile stands for every copy.
        if row_id or entry.row_id is None:
            summary["used"], summary["spare"] = entry.used, entry.spare
        else:
            everything = self.collection.by_oracle_id.get(entry.oracle_id, [entry])
            summary["used"] = sum(e.used for e in everything)
            summary["spare"] = sum(e.spare for e in everything)
        if entry.is_double_faced:
            summary["image_back"] = f"/img/{entry.scryfall_id}/back/normal"
        return summary

    def grouped_summary(self, members):
        """One summary for a card held in several rows: quantities summed, the most valuable printing shown."""
        lead = max(members, key=lambda e: (e.price_usd or 0, e.quantity))
        finishes = {e.finish for e in members}
        summary = self.summarize(lead, quantity=sum(e.quantity for e in members),
                                 finish=finishes.pop() if len(finishes) == 1 else "mixed", row_id=False)
        summary["value_usd"] = round(sum(e.value_usd for e in members), 2)
        summary["printings"] = len({e.scryfall_id for e in members})
        summary["used"] = sum(e.used for e in members)
        summary["spare"] = sum(e.spare for e in members)
        return summary

    # ---- search -------------------------------------------------------------

    # Sort keys over a result row: (lead entry, copies, value, members). Rows are
    # sorted before any summary is built, so a 20k-card result costs one page of summaries.
    def _row_sort_key(self, sort):
        prices = self.prices

        def change(window):
            def key(row):
                percent = prices.change_percent(row[0], window)
                return percent if percent is not None else -1e9
            return key

        keys = {
            "name": lambda r: r[0].name.lower(),
            "usd": lambda r: r[0].price_usd if r[0].price_usd is not None else -1,
            "value": lambda r: r[2],
            "mv": lambda r: r[0].cmc,
            "set": lambda r: (r[0].set_code, _collector_sort(r[0].collector_number)),
            "rarity": lambda r: RARITY_ORDER.get(r[0].rarity, 9),
            "qty": lambda r: r[1],
            "gain": lambda r: ((r[0].price_usd or 0) - r[0].purchase_price) if r[0].purchase_price is not None else -1e9,
            "change1": change(1), "change7": change(7), "change30": change(30),
            "color": lambda r: (len(r[0].colors) or 9, tuple(sorted(COLOR_ORDER.index(c) for c in r[0].colors))),
            "added": lambda r: max(e.added_at for e in r[3]),
            "released": lambda r: r[0].released_at,
            "edhrec": lambda r: r[0].card.get("edhrec_rank") or 10**9,
            "artist": lambda r: r[0].artist,
            "power": lambda r: _number(r[0].power), "toughness": lambda r: _number(r[0].toughness),
        }
        return keys.get(sort, keys["name"])

    # Scryfall's order: names, mapped onto ours.
    ORDER_ALIASES = {"cmc": "mv", "price": "usd", "eur": "usd", "tix": "usd", "quantity": "qty",
                     "review": "name", "spoiled": "released"}

    def search(self, params):
        text = params.get("q", "").strip()
        try:
            compiled = query.compile_query(text, self.collection.tag_index)
        except query.QueryError as error:
            return {"query": {"text": text, "description": "", "warnings": []}, "error": str(error),
                    "totals": {"rows": 0, "copies": 0, "unique_cards": 0, "printings": 0, "value_usd": 0, "results": 0},
                    "legality": {}, "breakdown": {}, "results": [], "offset": 0, "limit": 0}
        matched = [entry for entry in self.collection.entries if compiled.matches(entry)]

        unique = getattr(compiled, "unique", None) or params.get("unique", "prints")
        sort = params.get("sort", "name")
        direction = params.get("dir", "asc")
        order = getattr(compiled, "order", None)
        if order:
            sort, direction = self.ORDER_ALIASES.get(order[0], order[0]), order[1] or direction

        if unique == "cards":
            groups = defaultdict(list)
            for entry in matched:
                groups[entry.oracle_id].append(entry)
            rows = [(max(members, key=lambda e: (e.price_usd or 0, e.quantity)),
                     sum(e.quantity for e in members), sum(e.value_usd for e in members), members)
                    for members in groups.values()]
        else:
            rows = [(entry, entry.quantity, entry.value_usd, (entry,)) for entry in matched]

        rows.sort(key=lambda r: r[0].name.lower())
        rows.sort(key=self._row_sort_key(sort), reverse=(direction == "desc"))

        offset = max(0, int(params.get("offset", 0) or 0))
        limit = min(MAX_PAGE, max(1, int(params.get("limit", 120) or 120)))
        page = rows[offset:offset + limit]
        results = [self.grouped_summary(list(r[3])) if unique == "cards" else self.summarize(r[0]) for r in page]
        return {
            "query": {"text": text, "description": compiled.describe(), "warnings": list(compiled.warnings)},
            "error": None,
            "totals": {**_totals(matched), "results": len(rows)},
            "legality": _legality(matched),
            "breakdown": _breakdown(matched),
            "results": results,
            "offset": offset, "limit": limit, "sort": sort, "dir": direction, "unique": unique,
        }

    # ---- other endpoints ----------------------------------------------------

    def summary(self):
        entries = self.collection.entries
        totals = _totals(entries)
        paid = [e.purchase_price * e.quantity for e in entries if e.purchase_price is not None]
        with self.lock:
            coverage = self.prices.coverage()
        return {
            **totals,
            "sets": len({e.set_code for e in entries}),
            "purchase_total_usd": round(sum(paid), 2),
            "scryfall_updated_at": get_meta(self.connection, "scryfall_updated_at"),
            "price_history": coverage,
            "recent_legality_changes": self.legality_changes()[:20],
            "ask_available": ask.available(),
            "decks": self.deckbook.totals(),
            "reprints": self.radar.headline(),
            "app": {"version": __version__, "installed": setup_module.installed(), "platform": sys.platform},
            "alerts": {"unseen": self.alerts.list(True, 1)["unseen"],
                       "watching": self.connection.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]},
        }

    def card(self, scryfall_id, whole_card=False):
        """Detail for one printing. With `whole_card`, holdings list every printing of the card
        (the gallery's "cards" grouping) rather than just this one."""
        printing_entries = self.collection.by_scryfall_id.get(scryfall_id)
        if not printing_entries:
            # A printing a decklist names but you don't own: show the card with no holdings.
            card = self.deckbook.card_data(scryfall_id) or self._fetch_set_card(scryfall_id)
            if card is None:
                return None
            from .collection import build_entry
            unowned = build_entry({"row_id": None, "scryfall_id": scryfall_id, "finish": "normal", "quantity": 0,
                                   "condition": "", "language": card.get("lang", "en"), "purchase_price": None,
                                   "added_at": "", "misprint": 0}, card, {})
            unowned.tags = frozenset(r[0] for r in self.connection.execute(
                "SELECT slug FROM oracle_taggings WHERE oracle_id = ?", (unowned.oracle_id,)))
            printing_entries = [unowned]
            whole_card = False
        lead = printing_entries[0]
        entries = self.collection.by_oracle_id[lead.oracle_id] if whole_card else \
            [e for e in printing_entries if e.row_id is not None]
        card = lead.card
        faces = []
        for index, face in enumerate(lead.faces):
            faces.append({key: face.get(key) for key in
                          ("name", "mana_cost", "type_line", "oracle_text", "flavor_text", "power", "toughness", "loyalty")})
            faces[-1]["image"] = f"/img/{scryfall_id}/{'back' if index and lead.is_double_faced else 'front'}/large"
        detail = {
            "scryfall_id": scryfall_id, "oracle_id": lead.oracle_id, "name": lead.name,
            "mana_cost": lead.mana_cost, "type_line": lead.type_line, "oracle_text": lead.oracle_text,
            "flavor_text": card.get("flavor_text"), "power": lead.power, "toughness": lead.toughness,
            "loyalty": lead.loyalty, "rarity": lead.rarity, "set_code": lead.set_code, "set_name": lead.set_name,
            "collector_number": lead.collector_number, "artist": lead.artist, "released_at": lead.released_at,
            "layout": lead.layout, "legalities": lead.legalities, "prices": card.get("prices", {}),
            "scryfall_uri": card.get("scryfall_uri"), "purchase_uris": card.get("purchase_uris", {}),
            "edhrec_rank": card.get("edhrec_rank"), "reserved": card.get("reserved", False),
            "game_changer": card.get("game_changer", False), "faces": faces,
            "image": f"/img/{scryfall_id}/front/large",
            "set_icon": (self.set_info.get(lead.set_code) or {}).get("icon_svg_uri"),
        }
        if lead.is_double_faced:
            detail["image_back"] = f"/img/{scryfall_id}/back/large"
        labels = self.collection.tag_index.labels
        tags = sorted(lead.tags)
        others = [e for e in self.collection.by_oracle_id.get(lead.oracle_id, []) if e.scryfall_id != scryfall_id]
        similar = []
        for oracle_id, score in (self.similarity.similar(lead.oracle_id) if lead.row_id is not None else []):
            best = max(self.collection.by_oracle_id[oracle_id], key=lambda e: e.price_usd or 0)
            summary = self.summarize(best, quantity=sum(e.quantity for e in self.collection.by_oracle_id[oracle_id]))
            summary["similarity"] = round(score, 3)
            similar.append(summary)
        return {
            "card": detail,
            "tags": [{"slug": slug, "description": labels.get(slug, "")} for slug in tags],
            "holdings": [
                {"row_id": e.row_id, "scryfall_id": e.scryfall_id, "set_code": e.set_code, "source": e.source,
                 "collector_number": e.collector_number,
                 "finish": e.finish, "quantity": e.quantity, "condition": e.condition,
                 "language": e.language, "purchase_price": e.purchase_price, "added_at": e.added_at,
                 "price_usd": e.price_usd,
                 "gain_usd": round(((e.price_usd or 0) - e.purchase_price) * e.quantity, 2)
                 if e.purchase_price is not None and e.price_usd is not None else None}
                for e in entries
            ],
            "other_printings": [self.summarize(e) for e in others],
            "similar": similar,
            "decks": self.deckbook.memberships(lead.oracle_id),
        }

    def _fetch_set_card(self, scryfall_id):
        """A printing seen only in the collector view: the cache keeps just enough to draw it,
        so its full card comes from Scryfall the first time it is opened, then stays cached."""
        with self.lock:
            known = self.connection.execute("SELECT 1 FROM set_cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
        if not known:
            return None
        from .ingest import _store_card
        from .scryfall import fetch_cards_by_id
        try:
            cards = fetch_cards_by_id([scryfall_id])
        except Exception:
            return None
        if not cards:
            return None
        with self.lock:
            _store_card(self.connection, cards[0])
            self.connection.commit()
        self.deckbook.card_cache.pop(scryfall_id, None)
        return cards[0]

    def price_series(self, scryfall_id):
        finishes = {e.finish for e in self.collection.by_scryfall_id.get(scryfall_id, [])}
        with self.lock:
            series = self.prices.series(scryfall_id, finishes)
        return {"series": series, "default": 0}

    def portfolio(self):
        source = self.prices.portfolio_source
        return {
            "points": self.prices.portfolio(),
            "source": source, "source_label": SOURCE_LABELS.get(source, source),
            "note": "Your current collection valued at each day's prices. History before your first "
                    "refresh comes from MTGJSON's rolling 90 days; it grows by a day every refresh.",
        }

    def movers(self, params):
        window = int(params.get("window", 7))
        min_price = float(params.get("min_price", 1) or 0)
        limit = min(100, int(params.get("limit", 25)))
        gainers, losers = self.prices.movers(window, min_price, limit)

        def mover(pair):
            entry, (old, new, percent) = pair
            summary = self.summarize(entry)
            summary.update(old_price=old, new_price=new, change_pct=round(percent, 1),
                           change_total_usd=round((new - old) * entry.quantity, 2))
            return summary

        days = [d for d in (self.prices.latest_day,) if d]
        return {"window": window, "to_day": days[0] if days else None,
                "gainers": [mover(p) for p in gainers], "losers": [mover(p) for p in losers]}

    def stats(self):
        entries = self.collection.entries
        by_set = defaultdict(list)
        for entry in entries:
            by_set[entry.set_code].append(entry)
        sets = []
        for code, members in by_set.items():
            info = self.set_info.get(code, {})
            sets.append({
                "code": code, "name": info.get("name") or members[0].set_name, "icon": info.get("icon_svg_uri"),
                "released_at": info.get("released_at"), "set_type": info.get("set_type"),
                "set_size": info.get("card_count"),
                "owned_unique": len({e.collector_number for e in members}),
                "copies": sum(e.quantity for e in members),
                "value_usd": round(sum(e.value_usd for e in members), 2),
            })
        sets.sort(key=lambda s: -s["value_usd"])
        months = defaultdict(lambda: [0, 0.0])
        for entry in entries:
            month = entry.added_at[:7] or "unknown"
            months[month][0] += entry.quantity
            months[month][1] += entry.value_usd
        paid_entries = [e for e in entries if e.purchase_price is not None and e.price_usd is not None]
        top = sorted(entries, key=lambda e: -(e.price_usd or 0))[:24]
        return {
            "sets": sets,
            "added_by_month": [[m, c, round(v, 2)] for m, (c, v) in sorted(months.items())],
            "top_value": [self.summarize(e) for e in top],
            "gain": {
                "purchase_total_usd": round(sum(e.purchase_price * e.quantity for e in paid_entries), 2),
                "current_total_usd": round(sum(e.price_usd * e.quantity for e in paid_entries), 2),
                "priced_rows": len(paid_entries),
            },
        }

    def extras(self, params):
        keep = max(0, int(params.get("keep", 4)))
        min_price = float(params.get("min_price", 0) or 0)
        cards, total_copies, total_value = [], 0, 0.0
        for oracle_id, members in self.collection.by_oracle_id.items():
            lead = members[0]
            if "Basic Land" in lead.type_line or "any number of cards named" in lead.oracle_text:
                continue
            owned = sum(e.quantity for e in members)
            used = sum(e.used for e in members)
            extra = owned - max(used, keep)          # copies in active decks are never spare
            if extra <= 0:
                continue
            # Value the extras at the cheapest copies not in a deck: you keep your best ones.
            copies = sorted((e.price_usd or 0) for e in members for _ in range(e.spare))
            extra_value = round(sum(copies[:extra]), 2)
            if max(copies[:extra], default=0) < min_price:
                continue
            best = max(members, key=lambda e: e.price_usd or 0)
            summary = self.summarize(best, quantity=owned, row_id=False)
            summary.update(extra=extra, extra_value_usd=extra_value)
            cards.append(summary)
            total_copies += extra
            total_value += extra_value
        cards.sort(key=lambda s: -s["extra_value_usd"])
        return {"keep": keep, "total_extra_copies": total_copies,
                "total_extra_value_usd": round(total_value, 2), "cards": cards[:1000]}

    def tags(self, prefix):
        prefix = prefix.lower().strip()
        counts = Counter()
        for oracle_id, members in self.collection.by_oracle_id.items():
            for tag in members[0].tags:
                counts[tag] += 1
        index = self.collection.tag_index
        rows = []
        for slug, description in index.labels.items():
            if prefix and prefix not in slug:
                continue
            count = sum(counts[s] for s in index.expand(slug))
            if count:
                rows.append({"slug": slug, "description": description, "count": count})
        rows.sort(key=lambda r: (not r["slug"].startswith(prefix), -r["count"]))
        return rows[:30]

    def legality_changes(self):
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM legality_changes ORDER BY day DESC, name").fetchall()
        result = []
        for row in rows:
            copies = sum(e.quantity for e in self.collection.by_oracle_id.get(row["oracle_id"], []))
            result.append({**dict(row), "copies": copies})
        return result

    # ---- adding cards by hand (docs/API.md) --------------------------------

    def collection_change(self, method, parts, body):
        """Add, edit or remove hand-added cards, then reload so every view (and deck) sees the change."""
        with self.lock:
            try:
                if method == "POST" and parts[2:] == ["add"]:
                    result = {"row": manual.add(self.connection, body)}
                elif method == "PATCH" and len(parts) == 4 and parts[2] == "manual":
                    result = {"row": manual.update(self.connection, int(parts[3]), body)}
                elif method == "DELETE" and len(parts) == 4 and parts[2] == "manual":
                    result = manual.delete(self.connection, int(parts[3]))
                else:
                    raise LookupError("unknown collection request")
            except Exception:
                self.connection.rollback()        # nothing half-written waits for the next save
                raise
        self.load()
        result["copies"] = sum(e.quantity for e in self.collection.entries)
        return result

    # ---- price alerts (docs/TOOLS2.md) --------------------------------------

    def alert_request(self, method, parts, params, body):
        """Everything under /api/watchlist, /api/alerts, /api/alert-settings and /api/cards/lookup."""
        alerts, collection = self.alerts, self.collection
        route = parts[1]
        if route == "cards" and len(parts) == 3 and parts[2] == "lookup" and method == "GET":
            return alerts.lookup(params.get("q", ""), params.get("oracle_id"), collection, self.deckbook.resolver)
        if route == "alert-settings":
            with self.lock:
                settings = alerts.settings() if method == "GET" else alerts.update_settings(body)
            # Desktop notifications exist only on Windows; elsewhere the page hides the option.
            return {**settings, "notifications_available": sys.platform == "win32"}
        with self.lock:
            if route == "watchlist":
                if len(parts) == 2 and method == "GET":
                    return {"items": alerts.items(collection)}
                if len(parts) == 2 and method == "POST":
                    return alerts.add(body, collection)
                watch_id = int(parts[2])
                if method == "PATCH":
                    return alerts.update(watch_id, body, collection)
                if method == "DELETE":
                    alerts.remove(watch_id)
                    return {"deleted": watch_id}
            if route == "alerts":
                if len(parts) == 2 and method == "GET":
                    return alerts.list(params.get("unseen") in ("1", "true"), min(500, int(params.get("limit", 100))))
                if len(parts) == 3 and parts[2] == "seen" and method == "POST":
                    return alerts.mark_seen(None if body.get("all") else body.get("ids", []))
                if len(parts) == 3 and parts[2] == "check" and method == "POST":
                    new = alerts.check(collection, self.prices, self.radar)
                    alerts.notify(new)
                    return {"new": len(new)}
        raise LookupError("unknown alerts request")

    def reprints(self, params):
        with self.lock:
            return self.radar.report(self.summarize, float(params.get("min_value", 0) or 0))

    # ---- decks (docs/DECKS.md) ---------------------------------------------

    def decks_list(self):
        book = self.deckbook
        return {"decks": [book.summary(d["deck_id"]) for d in book.ordered_decks()], "totals": book.totals()}

    def deck_detail(self, deck_id):
        return self.deckbook.detail(deck_id, self.summarize)

    def deck_value_history(self, deck_id):
        with self.lock:
            points, source = self.prices.value_history(self.deckbook.used_quantities(deck_id))
        return {"points": points, "source": source, "source_label": SOURCE_LABELS.get(source, source)}

    def deck_manafix(self, deck_id, params):
        from .manafix import suggest_swaps
        book = self.deckbook
        summary = book.summary(deck_id)
        result = suggest_swaps(book.deck_cards(deck_id), book.spare_lands(), book.decks[deck_id]["format"],
                               set(summary["color_identity"]), max_swaps=min(12, int(params.get("max_swaps", 6))),
                               tag_index=self.collection.tag_index)
        for swap in result.get("swaps", []):
            for side in ("remove", "add"):
                swap[side]["image"] = f"/img/{swap[side]['scryfall_id']}/front/normal"
        return result

    def builder(self):
        """The deck builder works from spare copies, so it is rebuilt whenever decks change."""
        from .builder import Builder
        book = self.deckbook
        if getattr(self, "_builder_for", None) is not book.taken_by:
            self._builder = Builder(self.collection, self.similarity, self.collection.tag_index)
            self._builder_for = book.taken_by
        return self._builder

    def build_commanders(self, params):
        started = time.monotonic()
        colors = params.get("colors") or None
        ranked = self.builder().rank(limit=min(500, int(params.get("limit", 60))),
                                     colors=set(colors.upper()) if colors else None,
                                     name_query=params.get("q") or None)
        commanders = []
        for item in ranked:
            item = dict(item)
            entry = item.pop("card")
            commanders.append({"card": self.summarize(entry, row_id=False), **item})
        return {"commanders": commanders, "computed_in_ms": round((time.monotonic() - started) * 1000)}

    def build_draft(self, params):
        draft = dict(self.builder().draft(params["commander"], params.get("partner") or None))
        draft["commander"] = self.summarize(draft["commander"], row_id=False)
        if draft.get("partner") is not None:
            draft["partner"] = self.summarize(draft["partner"], row_id=False)
        draft["lines"] = [{**line, "card": self.summarize(line["card"], quantity=line.get("quantity", 1))}
                          for line in draft["lines"]]
        return draft

    def deck_suggestions(self, deck_id):
        return self.deckbook.suggestions(deck_id, self.similarity, self.summarize)

    def archidekt_import(self, body):
        """Fetch decks from Archidekt (outside the lock: a second per deck), then import them."""
        from . import archidekt
        targets = [archidekt.deck_id_from(str(t)) for t in (body.get("urls") or body.get("ids") or [])]
        if not targets:
            raise ValueError("Give one or more Archidekt deck links")
        specs, failures = [], []
        for deck_id in targets:
            try:
                name, fmt, parsed, text = archidekt.fetch_deck(deck_id)
            except Exception as error:          # one unreachable deck shouldn't sink the rest
                failures.append({"deck": str(deck_id), "line": "", "message": f"Couldn't fetch: {error}"})
                continue
            specs.append({"name": name, "format": fmt, "parsed": parsed, "text": text,
                          "status": body.get("status") or "active", "source": "archidekt",
                          "source_url": f"https://archidekt.com/decks/{deck_id}"})
        with self.lock:
            imported, warnings = self.deckbook.import_decks(specs)
            return {"imported": [self.deckbook.summary(d) for d in imported], "warnings": failures + warnings}

    def archidekt_sync(self, deck_id):
        """Re-read a deck imported from Archidekt; pins survive for cards still in the list."""
        from . import archidekt
        deck = self.deckbook.decks[deck_id]
        if deck["source"] != "archidekt" or not deck["source_url"]:
            raise ValueError("This deck wasn't imported from Archidekt")
        _, _, parsed, text = archidekt.fetch_deck(archidekt.deck_id_from(deck["source_url"]))
        with self.lock:
            warnings = self.deckbook.replace_list(deck_id, text, parsed=parsed, reason="sync")
            return {**self.deckbook.summary(deck_id), "warnings": warnings}

    def deck_goldfish(self, deck_id, params):
        from .goldfish import simulate
        book = self.deckbook
        return simulate(book.deck_cards(deck_id), book.decks[deck_id]["format"], self.collection.tag_index,
                        games=max(100, min(10000, int(params.get("games", 2000)))),
                        turns=max(3, min(15, int(params.get("turns", 10)))),
                        seed=int(params.get("seed", 1)), on_the_play=params.get("play", "first") != "draw")

    def deck_versions(self, deck_id):
        from . import versions
        with self.lock:
            return {"versions": versions.history(self.connection, deck_id)}

    def deck_copy_policies(self, deck_id):
        with self.lock:
            return self.deckbook.policy_preview(deck_id)

    def deck_change(self, method, deck_id, action, body, rest=()):
        """Every deck write, under the lock: they touch the database and re-allocate copies."""
        book = self.deckbook
        if method == "POST" and deck_id is None and action == "import-archidekt":
            return self.archidekt_import(body)
        if method == "POST" and action == "sync" and deck_id in book.decks:
            return self.archidekt_sync(deck_id)
        with self.lock:
            if method == "POST" and deck_id is None and action == "import":
                imported, warnings = book.import_decks(body.get("decks") or [])
                return {"imported": [book.summary(d) for d in imported], "warnings": warnings}
            if deck_id not in book.decks:
                raise KeyError(f"no deck {deck_id}")
            if method == "PATCH" and action is None:
                book.update(deck_id, body)
                return book.summary(deck_id)
            if method == "PUT" and action == "list":
                warnings = book.replace_list(deck_id, body.get("text", ""))
                return {**book.summary(deck_id), "warnings": warnings}
            if method == "DELETE" and action is None:
                book.delete(deck_id)
                return {"deleted": deck_id}
            if method == "POST" and action == "versions" and len(rest) == 2 and rest[1] == "restore":
                book.restore(deck_id, int(rest[0]))
                return book.summary(deck_id)
            if method == "POST" and action == "manafix":
                book.apply_swaps(deck_id, body.get("swaps") or [])
                summary = book.summary(deck_id)
                if book.decks[deck_id]["source"] == "archidekt":
                    summary["warning"] = "This deck syncs from Archidekt; the next sync will replace these swaps."
                return summary
            if action == "lines":
                if method == "POST" and not rest:
                    line_id = book.add_line(deck_id, body)
                elif method in ("PATCH", "DELETE") and len(rest) == 1 and rest[0].isdigit():
                    line_id = int(rest[0])
                    if book.line_by_id.get(line_id) is None or book.line_by_id[line_id].row["deck_id"] != deck_id:
                        raise KeyError(f"no line {line_id} in deck {deck_id}")
                    book.edit_line(line_id, {"quantity": 0} if method == "DELETE" else body)
                else:
                    raise LookupError("unknown deck line request")
                summary = {**book.summary(deck_id), "line_id": line_id}
                if book.decks[deck_id]["source"] == "archidekt":
                    summary["warning"] = "This deck syncs from Archidekt; the next sync will replace this change."
                return summary
            if method == "POST" and action == "pin":
                state = book.pin(int(body["line_id"]), body.get("pool", ""), int(body.get("quantity", 0)))
                return book.line_json(state, self.summarize)
        raise LookupError("unknown deck action")

    def image_path(self, scryfall_id, face, size):
        """Local path for a card image, downloading it on first request."""
        entries = self.collection.by_scryfall_id.get(scryfall_id)
        card = entries[0].card if entries else self.deckbook.card_data(scryfall_id)
        if card is None:                    # a new printing on the reprint radar
            with self.lock:
                row = self.connection.execute(
                    "SELECT image_uris FROM new_printings WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
            card = {"image_uris": json.loads(row[0])} if row and row[0] else None
        if card is None:                    # any card of any set, for the collector view
            with self.lock:
                front, back = self.setbook.image_for(scryfall_id, "front"), self.setbook.image_for(scryfall_id, "back")
            if front:
                sizes = lambda url: {name: image_url(url, name) for name in IMAGE_SIZES}
                card = ({"card_faces": [{"image_uris": sizes(front)}, {"image_uris": sizes(back)}]} if back
                        else {"image_uris": sizes(front)})
        if card is None or size not in IMAGE_SIZES or face not in ("front", "back"):
            return None
        if "image_uris" in card:
            uris = card["image_uris"] if face == "front" else None
        else:
            faces = card.get("card_faces") or []
            index = 0 if face == "front" else 1
            uris = faces[index].get("image_uris") if len(faces) > index else None
        if not uris or size not in uris:
            return None
        extension = "png" if size == "png" else "jpg"
        path = IMAGE_CACHE_DIR / size / scryfall_id[0] / f"{scryfall_id}-{face}.{extension}"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(uris[size], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
                data = response.read()
            partial = path.with_suffix(f".{threading.get_ident()}.part")   # two tiles may ask at once
            partial.write_bytes(data)
            partial.replace(path)
        return path


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return -1.0


def _collector_sort(number):
    digits = re.match(r"\d+", number or "")
    return (int(digits.group()) if digits else 10**6, number or "")


def _totals(entries):
    return {
        "rows": len(entries),
        "copies": sum(e.quantity for e in entries),
        "unique_cards": len({e.oracle_id for e in entries}),
        "printings": len({e.scryfall_id for e in entries}),
        "value_usd": round(sum(e.value_usd for e in entries), 2),
    }


def _legality(entries):
    result = {}
    for fmt in FORMATS:
        legal = [e for e in entries if e.legalities.get(fmt) in ("legal", "restricted")]
        if legal or any(fmt in e.legalities for e in entries[:1]):
            result[fmt] = {"cards": len({e.oracle_id for e in legal}), "copies": sum(e.quantity for e in legal)}
    return result


def _breakdown(entries):
    colors = Counter({c: 0 for c in COLOR_ORDER + "CM"})
    curve = Counter({str(n): 0 for n in range(7)} | {"7+": 0})
    types = Counter()
    rarity = Counter()
    for entry in entries:
        n = entry.quantity
        for color in entry.colors:
            colors[color] += n
        if not entry.colors:
            colors["C"] += n
        elif len(entry.colors) > 1:
            colors["M"] += n
        front_type = entry.type_line.split(" // ")[0]
        if "Land" not in front_type:
            curve["7+" if entry.cmc >= 7 else str(int(entry.cmc))] += n
        for card_type in CARD_TYPES:
            if card_type in front_type:
                types[card_type] += n
        rarity[entry.rarity] += n
    return {"colors": dict(colors), "curve": dict(curve), "types": dict(types.most_common()),
            "rarity": dict(rarity.most_common())}


# The server listens only on this machine, but any web page open in the browser
# can still send it requests. Two defenses:
# - DNS rebinding: a hostile site can point its own domain at 127.0.0.1 and then
#   read responses as same-origin. Requests must name this machine in Host.
# - Cross-site writes: a page can send a "simple" POST (text/plain or a form)
#   without the browser asking permission first. Writes must be JSON, which makes
#   the browser ask first (a CORS preflight this server never grants), and must not
#   carry another site's Origin.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
# Set by an embedding app (gallery/android.py): every request must then carry this secret, as the
# cookie cardclops_token or the header X-Cardclops-Token.
ACCESS_TOKEN = os.environ.get("CARDCLOPS_ACCESS_TOKEN", "")
# A hosted copy is reached through a Cloudflare Tunnel under its own name (and Cloudflare Access
# decides who may reach it). CARDCLOPS_ALLOWED_HOSTS lists those names, e.g. "cardclops.com";
# pages served under them are https.
HOSTED_NAMES = {name.strip().lower() for name in os.environ.get("CARDCLOPS_ALLOWED_HOSTS", "").split(",")
                if name.strip()}


def _host_name(value):
    """'localhost:8765' -> 'localhost'; '[::1]:8765' -> '[::1]'."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[:value.find("]") + 1] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _is_local_origin(origin):
    parsed = urllib.parse.urlsplit(origin)
    host = _host_name(parsed.netloc)
    return (parsed.scheme == "http" and host in LOCAL_HOSTS) or (parsed.scheme == "https" and host in HOSTED_NAMES)


class Handler(SimpleHTTPRequestHandler):
    gallery: Gallery = None
    app_window = None       # the Windows app's window (gallery/app.py): show() and close()

    def _refused(self, write):
        """Answers 403 and returns True when a request fails the checks above."""
        reason = None
        if ACCESS_TOKEN and not self._has_token():
            reason = "This Cardclops only answers the app that started it."
        elif _host_name(self.headers.get("Host")) not in LOCAL_HOSTS | HOSTED_NAMES:
            reason = "This server only answers requests addressed to localhost."
        elif write:
            origin = self.headers.get("Origin")
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if origin and origin != "null" and not _is_local_origin(origin):
                reason = "Cross-site requests are not allowed."
            elif origin == "null":
                reason = "Requests from sandboxed or file pages are not allowed."
            elif self.command != "DELETE" and content_type != "application/json":
                reason = "Send writes as application/json."
        if reason is None:
            return False
        body = json.dumps({"error": reason}).encode()
        self.send_response(HTTPStatus.FORBIDDEN)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _has_token(self):
        """On a phone, other apps can reach 127.0.0.1 too; the Cardclops app sets this cookie in its WebView."""
        offered = self.headers.get("X-Cardclops-Token") or ""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "cardclops_token":
                offered = value
        return hmac.compare_digest(offered.encode(), ACCESS_TOKEN.encode())

    def do_HEAD(self):
        if not self._refused(write=False):
            super().do_HEAD()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        if not self.path.startswith("/img/"):
            super().log_message(format, *args)

    def _json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Static files change while developing; images carry their own caching.
        if not self.path.startswith("/img/") and not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        if self._refused(write=False):
            return
        url = urllib.parse.urlsplit(self.path)
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        parts = [urllib.parse.unquote(p) for p in url.path.strip("/").split("/")]
        gallery = self.gallery
        try:
            if parts[0] == "img" and len(parts) == 4:
                return self._image(*parts[1:])
            if parts[0] != "api":
                return super().do_GET()
            route = parts[1] if len(parts) > 1 else ""
            if route == "summary":
                return self._json(gallery.summary())
            if route == "search":
                return self._json(gallery.search(params))
            if route == "card" and len(parts) == 3:
                detail = gallery.card(parts[2], whole_card=params.get("holdings") == "card")
                return self._json(detail) if detail else self._json({"error": "not in collection"}, HTTPStatus.NOT_FOUND)
            if route == "prices" and len(parts) == 3:
                return self._json(gallery.price_series(parts[2]))
            if route == "portfolio":
                return self._json(gallery.portfolio())
            if route == "movers":
                return self._json(gallery.movers(params))
            if route == "stats":
                return self._json(gallery.stats())
            if route == "extras":
                return self._json(gallery.extras(params))
            if route == "tags":
                return self._json(gallery.tags(params.get("q", "")))
            if route == "legality-changes":
                return self._json(gallery.legality_changes())
            if route == "reprints":
                return self._json(gallery.reprints(params))
            if route == "sets":
                with gallery.lock:
                    if len(parts) == 2:
                        return self._json(gallery.setbook.list(params))
                    if len(parts) == 4 and parts[3] == "missing.txt":
                        return self._text(gallery.setbook.missing_text(parts[2].lower(), params))
                    try:
                        return self._json(gallery.setbook.detail(parts[2].lower(), params))
                    except KeyError:
                        return self._json({"error": "no such set"}, HTTPStatus.NOT_FOUND)
            if route == "setup" and len(parts) == 3 and parts[2] == "status":
                return self._json(gallery.jobs.status())
            if route == "setup" and len(parts) == 3 and parts[2] == "progress":
                return self._json(gallery.jobs.progress())
            if route in ALERT_ROUTES:
                return self._alert_request("GET", parts, params, {})
            if route == "collection" and parts[2:] == ["manual"]:
                with gallery.lock:
                    return self._json({"rows": manual.rows(gallery.connection)})
            if route == "build" and len(parts) == 3 and parts[2] == "commanders":
                return self._json(gallery.build_commanders(params))
            if route == "build" and len(parts) == 3 and parts[2] == "draft":
                if not params.get("commander"):
                    return self._json({"error": "Choose a commander"}, HTTPStatus.BAD_REQUEST)
                return self._json(gallery.build_draft(params))
            if route == "archidekt" and len(parts) == 3 and parts[2] == "decks":
                from . import archidekt
                username = params.get("username", "").strip()
                if not username:
                    return self._json({"error": "Give an Archidekt username"}, HTTPStatus.BAD_REQUEST)
                try:
                    return self._json({"decks": archidekt.list_decks(username)})
                except urllib.error.HTTPError as error:
                    return self._json({"error": f"Archidekt answered {error.code}"}, HTTPStatus.BAD_GATEWAY)
            if route == "decks":
                if len(parts) == 2:
                    return self._json(gallery.decks_list())
                deck_id = int(parts[2])
                if deck_id not in gallery.deckbook.decks:
                    return self._json({"error": "no such deck"}, HTTPStatus.NOT_FOUND)
                action = parts[3] if len(parts) > 3 else None
                if action is None:
                    return self._json(gallery.deck_detail(deck_id))
                if action == "value-history":
                    return self._json(gallery.deck_value_history(deck_id))
                if action == "suggestions":
                    return self._json(gallery.deck_suggestions(deck_id))
                if action == "manafix":
                    return self._json(gallery.deck_manafix(deck_id, params))
                if action == "versions":
                    return self._json(gallery.deck_versions(deck_id))
                if action == "goldfish":
                    return self._json(gallery.deck_goldfish(deck_id, params))
                if action == "copy-policies":
                    return self._json(gallery.deck_copy_policies(deck_id))
            return self._json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except Exception as error:     # a bad request must not take the server down
            traceback.print_exc()
            return self._json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        if self._refused(write=True):
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/api/decks"):
                return self._deck_write("POST", path, body)
            if path.startswith("/api/setup/") or path in ("/api/refresh", "/api/quit", "/api/app/show"):
                return self._setup_request(path, body)
            if path.startswith("/api/collection/"):
                return self._collection_request("POST", path, body)
            post_parts = path.strip("/").split("/")
            if len(post_parts) > 1 and post_parts[1] in ALERT_ROUTES:
                return self._alert_request("POST", post_parts, {}, body)
            if path == "/api/deckcheck":
                return self._json(self.gallery.deck_checker.check(body.get("text", ""), self.gallery.summarize))
            if path == "/api/ask":
                question = (body.get("question") or "").strip()
                if not question:
                    return self._json({"error": "Ask a question first."}, HTTPStatus.BAD_REQUEST)
                try:
                    return self._json(ask.translate(question, self.gallery.collection.tag_index, query.compile_query))
                except (ask.AskError, ValueError) as error:
                    return self._json({"error": str(error)})
            return self._json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            traceback.print_exc()
            return self._json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _setup_request(self, path, body):
        jobs = self.gallery.jobs
        try:
            if path == "/api/setup/download":
                return self._json(jobs.download(bool(body.get("price_history", True))))
            if path == "/api/setup/import":
                if not body.get("text"):
                    return self._json({"error": "Choose a CSV file first."}, HTTPStatus.BAD_REQUEST)
                return self._json(jobs.import_collection(body.get("filename", "collection.csv"), body["text"]))
            if path == "/api/setup/options":
                scheduled = setup_module.set_daily_refresh(bool(body.get("daily_refresh")))
                return self._json({"daily_refresh_scheduled": scheduled})
            if path == "/api/setup/complete":
                return self._json(jobs.complete())
            if path == "/api/refresh":
                return self._json(jobs.refresh())
            if path == "/api/quit":
                self._json({"quitting": True})
                if Handler.app_window:                 # the Windows app: closing its window ends it
                    threading.Thread(target=Handler.app_window.close, daemon=True).start()
                else:
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            if path == "/api/app/show":
                # Starting Cardclops.exe while it runs brings its window forward (gallery/app.py).
                if Handler.app_window:
                    Handler.app_window.show()
                return self._json({"shown": bool(Handler.app_window)})
        except RuntimeError as error:
            return self._json({"error": str(error)}, HTTPStatus.CONFLICT)
        return self._json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def _text(self, text):
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _collection_request(self, method, path, body):
        try:
            return self._json(self.gallery.collection_change(method, path.strip("/").split("/"), body))
        except KeyError as error:
            return self._json({"error": str(error).strip("'")}, HTTPStatus.NOT_FOUND)
        except (ValueError, LookupError) as error:
            return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _alert_request(self, method, parts, params, body):
        try:
            return self._json(self.gallery.alert_request(method, parts, params, body))
        except KeyError as error:
            return self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except (ValueError, LookupError) as error:
            return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _deck_write(self, method, path, body):
        parts = path.strip("/").split("/")          # api, decks, <id> or "import", [action]
        deck_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        action = parts[3] if len(parts) > 3 else (parts[2] if deck_id is None and len(parts) > 2 else None)
        try:
            return self._json(self.gallery.deck_change(method, deck_id, action, body, tuple(parts[4:])))
        except KeyError as error:
            return self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except (ValueError, LookupError) as error:
            return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _write_method(self, method):
        if self._refused(write=True):
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/api/decks/"):
                return self._deck_write(method, path, body)
            if path.startswith("/api/collection/"):
                return self._collection_request(method, path, body)
            write_parts = path.strip("/").split("/")
            if len(write_parts) > 1 and write_parts[1] in ALERT_ROUTES:
                return self._alert_request(method, write_parts, {}, body)
            return self._json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            traceback.print_exc()
            return self._json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self):
        self._write_method("PATCH")

    def do_PUT(self):
        self._write_method("PUT")

    def do_DELETE(self):
        self._write_method("DELETE")

    def _image(self, scryfall_id, face, size):
        try:
            path = self.gallery.image_path(scryfall_id, face, size)
        except Exception as error:
            return self._json({"error": f"image fetch failed: {error}"}, HTTPStatus.BAD_GATEWAY)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png" if path.suffix == ".png" else "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=2592000, immutable")
        self.end_headers()
        self.wfile.write(data)


class IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def start_in_background(connection, port=0, refresh_if_stale=True):
    """Start the gallery on a thread and return (server, port); port 0 picks a free one.
    For apps that embed Cardclops (the Android app) rather than run it as a program.
    The port is bound first, so a port in use raises OSError before anything else starts."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    Handler.gallery = Gallery(connection)
    Handler.gallery.watch_for_new_data()
    if refresh_if_stale:
        Handler.gallery.jobs.refresh_if_stale()
    threading.Thread(target=server.serve_forever, name="cardclops-server", daemon=True).start()
    return server, server.server_address[1]


def serve(connection, port, open_browser=False):
    Handler.gallery = Gallery(connection)
    Handler.gallery.watch_for_new_data()
    Handler.gallery.jobs.refresh_if_stale()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    # Windows resolves "localhost" to ::1 first; without an IPv6 listener every
    # request from a non-browser client waits ~2 s for the IPv4 fallback.
    try:
        server_v6 = IPv6Server(("::1", port), Handler)
        server_v6.daemon_threads = True
        threading.Thread(target=server_v6.serve_forever, name="ipv6-listener", daemon=True).start()
    except OSError:
        pass
    print(f"Collection gallery at http://localhost:{port}")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, (f"http://localhost:{port}/",)).start()
    try:
        server.serve_forever()                  # returns when /api/quit calls shutdown()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
