"""Price alerts (docs/TOOLS2.md): a watchlist of printings with target prices,
plus automatic alerts on the collection — big price moves, wide reprints, and
bans or rotations. The daily refresh runs `check` and shows a Windows
notification when something new fires.
"""
import json
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import scryfall
from .db import get_meta, set_meta, unpack_points
from .paths import SCRIPTS_DIR, app_port
from .pricebook import TRACKED_SOURCES, _rank

# Every automatic alert and notifications start OFF: nobody should get alerts
# they didn't ask for. The setup wizard offers them as opt-ins. Watchlist target
# alerts exist only once the user adds a watch item.
DEFAULT_SETTINGS = {"move_alerts": False, "held_move_pct": 20, "held_move_window": 7, "held_min_value_usd": 10,
                    "reprint_alerts": False, "legality_alerts": False, "windows_notifications": False}
NOTIFY_SCRIPT = SCRIPTS_DIR / "notify.ps1"
HISTORY_KEPT = timedelta(days=365)        # alerts you've seen, and legality changes, older than this go


class Alerts:
    def __init__(self, connection):
        self.connection = connection

    # ---- settings ----

    def settings(self):
        stored = json.loads(get_meta(self.connection, "alert_settings", "{}") or "{}")
        return {**DEFAULT_SETTINGS, **stored}

    def update_settings(self, changes):
        current = self.settings()
        for key, value in changes.items():
            if key not in DEFAULT_SETTINGS:
                raise ValueError(f"unknown setting {key}")
            if key == "held_move_window" and int(value) not in (1, 7, 30):
                raise ValueError("held_move_window must be 1, 7 or 30")
            current[key] = type(DEFAULT_SETTINGS[key])(value)
        set_meta(self.connection, "alert_settings", json.dumps(current))
        self.connection.commit()
        return current

    # ---- watchlist ----

    def _latest_prices(self, scryfall_id, finish):
        """(newest price, {day: price}) from the most trusted USD source with data, else Scryfall's card."""
        rows = self.connection.execute(
            "SELECT source, points FROM price_series WHERE scryfall_id = ? AND finish = ? AND currency = 'USD' "
            "AND source IN (?, ?)", (scryfall_id, finish, *TRACKED_SOURCES)).fetchall()
        series = sorted(((_rank(r["source"]), unpack_points(r["points"])) for r in rows), key=lambda x: x[0])
        # Changes come from the longest series; the current price from the most trusted one.
        longest = max((points for _, points in series), key=len, default={})
        current = next((points[max(points)] for _, points in series if points), None)
        return current, longest

    def _change(self, points, window):
        if len(points) < 2:
            return None
        newest = max(points)
        target = (date.fromisoformat(newest) - timedelta(days=window)).isoformat()
        earlier = [d for d in points if d <= target]
        if not earlier or (date.fromisoformat(target) - date.fromisoformat(max(earlier))).days > 2:
            return None
        old = points[max(earlier)]
        return round((points[newest] - old) / old * 100, 1) if old else None

    def item(self, row, collection):
        row = dict(row)
        card = self._card(row["scryfall_id"]) or {}
        price, points = self._latest_prices(row["scryfall_id"], row["finish"])
        if price is None:
            key = {"normal": "usd", "foil": "usd_foil", "etched": "usd_etched"}.get(row["finish"], "usd")
            raw = (card.get("prices") or {}).get(key)
            price = float(raw) if raw else None
        target = row["target_usd"]
        triggered = price is not None and (price <= target if row["direction"] == "below" else price >= target)
        oracle_id = card.get("oracle_id")
        return {
            "watch_id": row["watch_id"], "scryfall_id": row["scryfall_id"], "oracle_id": oracle_id,
            "name": card.get("name", "?"), "set_code": card.get("set"), "collector_number": card.get("collector_number"),
            "finish": row["finish"], "target_usd": target, "direction": row["direction"], "note": row["note"],
            "created_at": row["created_at"], "price_usd": price,
            "change": {f"d{w}": self._change(points, w) for w in (1, 7, 30)},
            "distance_pct": round((price - target) / target * 100, 1) if price is not None and target else None,
            "triggered": triggered,
            "owned": sum(e.quantity for e in collection.by_oracle_id.get(oracle_id, [])) if oracle_id else 0,
            "image": f"/img/{row['scryfall_id']}/front/normal",
        }

    def _card(self, scryfall_id):
        row = self.connection.execute("SELECT raw FROM cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def items(self, collection):
        rows = self.connection.execute("SELECT * FROM watchlist ORDER BY created_at DESC").fetchall()
        return [self.item(row, collection) for row in rows]

    def add(self, body, collection):
        from .ingest import _record_prices, _store_card
        scryfall_id = body["scryfall_id"]
        if self._card(scryfall_id) is None:          # not owned or in a deck: fetch it once, then refresh keeps it
            for card in scryfall.fetch_cards_by_id([scryfall_id]):
                _store_card(self.connection, card)
                _record_prices(self.connection, card, date.today().isoformat())
        direction = body.get("direction", "below")
        if direction not in ("below", "above"):
            raise ValueError("direction must be below or above")
        watch_id = self.connection.execute(
            "INSERT INTO watchlist (scryfall_id, finish, target_usd, direction, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (scryfall_id, body.get("finish", "normal"), float(body["target_usd"]), direction, body.get("note", ""),
             datetime.now().isoformat(timespec="seconds"))).lastrowid
        self.connection.commit()
        return self.item(self.connection.execute("SELECT * FROM watchlist WHERE watch_id = ?", (watch_id,)).fetchone(),
                         collection)

    def update(self, watch_id, body, collection):
        fields = {k: body[k] for k in ("target_usd", "direction", "note", "finish") if k in body}
        if fields:
            fields["armed"] = 1                       # a new target starts fresh
            assignments = ", ".join(f"{k} = ?" for k in fields)
            self.connection.execute(f"UPDATE watchlist SET {assignments} WHERE watch_id = ?", (*fields.values(), watch_id))
            self.connection.commit()
        row = self.connection.execute("SELECT * FROM watchlist WHERE watch_id = ?", (watch_id,)).fetchone()
        if row is None:
            raise KeyError(f"no watch item {watch_id}")
        return self.item(row, collection)

    def remove(self, watch_id):
        self.connection.execute("DELETE FROM watchlist WHERE watch_id = ?", (watch_id,))
        self.connection.commit()

    def lookup(self, query, oracle_id, collection, resolver):
        """Cards by name from the local index; printings (live from Scryfall) for the first or chosen one."""
        from .decks import fold
        cards = []
        if oracle_id:
            name = resolver.representative.get(oracle_id, (None, "?", None))[1]
            cards = [{"oracle_id": oracle_id, "name": name}]
        else:
            needle = fold(query or "")
            if len(needle) < 2:
                return {"cards": []}
            seen = set()
            for key, found in resolver.by_name.items():
                if needle in key and found not in seen and found in resolver.representative:
                    seen.add(found)
                    cards.append({"oracle_id": found, "name": resolver.representative[found][1]})
            cards.sort(key=lambda c: (not fold(c["name"]).startswith(needle), len(c["name"])))
            cards = cards[:10]
        for index, card in enumerate(cards):
            card["printings"] = self._printings(card["oracle_id"]) if index == 0 else None
        return {"cards": cards}

    def _printings(self, oracle_id):
        url = (f"{scryfall.API}/cards/search?unique=prints&order=released&q="
               + urllib.parse.quote(f"oracleid:{oracle_id} -is:digital"))
        try:
            data = scryfall.api_get(url)
        except Exception:
            return []
        printings = []
        for card in data.get("data", []):
            front = card.get("image_uris") or ((card.get("card_faces") or [{}])[0].get("image_uris")) or {}
            printings.append({"scryfall_id": card["id"], "set_code": card.get("set"), "set_name": card.get("set_name"),
                              "collector_number": card.get("collector_number"), "released_at": card.get("released_at"),
                              "finishes": card.get("finishes", []),
                              "prices": {k: card.get("prices", {}).get(k) for k in ("usd", "usd_foil", "usd_etched")},
                              "image": front.get("normal")})
        return printings

    # ---- alerts ----

    def list(self, unseen_only=False, limit=100):
        where = "WHERE seen = 0" if unseen_only else ""
        rows = self.connection.execute(f"SELECT * FROM alerts {where} ORDER BY day DESC, alert_id DESC LIMIT ?",
                                       (limit,)).fetchall()
        unseen = self.connection.execute("SELECT COUNT(*) FROM alerts WHERE seen = 0").fetchone()[0]
        return {"alerts": [{**dict(r), "seen": bool(r["seen"]),
                            "image": f"/img/{r['scryfall_id']}/front/normal" if r["scryfall_id"] else None}
                           for r in rows], "unseen": unseen}

    def mark_seen(self, ids=None):
        if ids is None:
            self.connection.execute("UPDATE alerts SET seen = 1")
        else:
            self.connection.executemany("UPDATE alerts SET seen = 1 WHERE alert_id = ?", [(int(i),) for i in ids])
        self.connection.commit()
        return {"unseen": self.connection.execute("SELECT COUNT(*) FROM alerts WHERE seen = 0").fetchone()[0]}

    def _fire(self, day, kind, key, scryfall_id, name, message, price):
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO alerts (day, kind, key, scryfall_id, name, message, price_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (day, kind, key, scryfall_id, name, message, price))
        return cursor.rowcount

    def check(self, collection, prices, radar):
        """Run every check once; returns the alerts that are new."""
        settings = self.settings()
        day = date.today().isoformat()
        before = self.connection.execute("SELECT COALESCE(MAX(alert_id), 0) FROM alerts").fetchone()[0]
        cutoff = (date.today() - HISTORY_KEPT).isoformat()
        self.connection.execute("DELETE FROM alerts WHERE seen = 1 AND day < ?", (cutoff,))
        self.connection.execute("DELETE FROM legality_changes WHERE day < ?", (cutoff,))

        # Targets on the watchlist fire once per crossing.
        for row in self.connection.execute("SELECT * FROM watchlist").fetchall():
            item = self.item(row, collection)
            if item["triggered"] and row["armed"]:
                verb = "fell to" if row["direction"] == "below" else "rose to"
                self._fire(day, "target", f"watch:{row['watch_id']}", row["scryfall_id"], item["name"],
                           f"{item['name']} ({(item['set_code'] or '').upper()}) {verb} ${item['price_usd']:.2f}, "
                           f"target ${row['target_usd']:.2f}", item["price_usd"])
                self.connection.execute("UPDATE watchlist SET armed = 0 WHERE watch_id = ?", (row["watch_id"],))
            elif not item["triggered"] and not row["armed"]:
                self.connection.execute("UPDATE watchlist SET armed = 1 WHERE watch_id = ?", (row["watch_id"],))

        # Big moves on cards you hold.
        window = int(settings["held_move_window"])
        held = defaultdict(int)
        if settings["move_alerts"]:
            for entry in collection.entries:
                held[(entry.scryfall_id, entry.finish)] += entry.quantity
        names = {(e.scryfall_id, e.finish): e for e in collection.entries}
        for key, quantity in held.items():
            change = prices.changes.get(key, {}).get(window)
            if not change:
                continue
            old, new, percent = change
            if abs(percent) >= settings["held_move_pct"] and new * quantity >= settings["held_min_value_usd"]:
                entry = names[key]
                word = "up" if percent > 0 else "down"
                self._fire(day, "move", f"{key[0]}|{key[1]}", key[0], entry.name,
                           f"{entry.name} ({entry.set_code.upper()}{' foil' if key[1] != 'normal' else ''}) is {word} "
                           f"{abs(percent):.0f}% in {window} day{'s' if window > 1 else ''}: ${old:.2f} → ${new:.2f} "
                           f"(you hold {quantity})", new)

        # Wide reprints of valuable cards you hold: once per card and set. The first
        # check only notes what is already announced, so it doesn't bury you in old news.
        if settings["reprint_alerts"]:
            known = set(json.loads(get_meta(self.connection, "reprint_alert_keys", "null") or "null") or [])
            first_run = get_meta(self.connection, "reprint_alert_keys") is None
            cards, _ = radar._cards(settings["held_min_value_usd"])
            for card in cards:
                entry = card["entry"]
                for reprint in card["reprints"]:
                    key = f"{entry.oracle_id}|{reprint['set_code']}"
                    if reprint["impact"] != "wide" or key in known:
                        continue
                    known.add(key)
                    if first_run:
                        continue
                    when = f"out {reprint['released_at']}" if card["days_until"] > 0 else "just released"
                    self._fire(day, "reprint", key, entry.scryfall_id, entry.name,
                               f"{entry.name} is being reprinted in {reprint['set_name']} ({when}); "
                               f"your copies are worth ${card['held_value_usd']:.2f}", entry.price_usd)
            set_meta(self.connection, "reprint_alert_keys", json.dumps(sorted(known)))

        # Bans, unbans and rotations touching your cards.
        if settings["legality_alerts"]:
            last = int(get_meta(self.connection, "alerts_legality_rowid", 0) or 0)
            for row in self.connection.execute("SELECT rowid, * FROM legality_changes WHERE rowid > ?", (last,)).fetchall():
                if row["oracle_id"] in collection.by_oracle_id:
                    entry = collection.by_oracle_id[row["oracle_id"]][0]
                    self._fire(day, "legality", f"{row['oracle_id']}|{row['format']}|{row['new_status']}",
                               entry.scryfall_id, row["name"],
                               f"{row['name']} is now {row['new_status'].replace('_', ' ')} in {row['format'].title()}"
                               f" (was {(row['old_status'] or 'unknown').replace('_', ' ')})", None)
                last = max(last, row["rowid"])
            set_meta(self.connection, "alerts_legality_rowid", last)

        self.connection.commit()
        return [dict(r) for r in self.connection.execute("SELECT * FROM alerts WHERE alert_id > ? ORDER BY alert_id",
                                                         (before,)).fetchall()]

    def notify(self, new_alerts):
        """A Windows notification summarizing new alerts; clicking it opens the gallery's alerts view."""
        if not new_alerts or not self.settings()["windows_notifications"] or sys.platform != "win32":
            return False
        title = f"{len(new_alerts)} new card alert{'s' if len(new_alerts) > 1 else ''}"
        body = "\n".join(a["message"] for a in new_alerts[:3])
        if len(new_alerts) > 3:
            body += f"\n…and {len(new_alerts) - 3} more"
        return show_notification(title, body)


def show_notification(title, body):
    """Windows PowerShell 5.1 can reach the WinRT toast API without any module; PowerShell 7 cannot."""
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-WindowStyle", "Hidden", "-File", str(NOTIFY_SCRIPT), title, body,
                        f"http://localhost:{app_port()}/#/alerts"],
                       check=True, timeout=30, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except (OSError, subprocess.SubprocessError):
        return False
