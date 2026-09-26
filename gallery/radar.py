"""Reprint radar (docs/TOOLS.md): cards you own that are about to be, or were
just, reprinted, most valuable first.

A reprint adds supply, so the printings you hold tend to lose value around
the new printing's release. How much depends on how widely the new printing
is opened: a Commander precon or masters set moves prices; a Secret Lair drop
or a promo barely does.
"""
from collections import defaultdict
from datetime import date, timedelta

from .db import get_meta, unpack_points
from .pricebook import TRACKED_SOURCES, _rank

# Set types opened in quantity. Everything else (box = Secret Lair, promo,
# masterpiece, ...) is a limited printing.
WIDE_SET_TYPES = {"expansion", "core", "masters", "draft_innovation", "commander", "starter", "duel_deck",
                  "planechase", "archenemy", "funny", "eternal"}
# The List is typed "masters" but is a handful of cards per booster.
LIMITED_SETS = {"plst", "sld", "slp"}
RECENT_DAYS = 90
# Without a preview date, measure from this long before release: previews run about a month.
PREVIEW_LEAD_DAYS = 30


def impact_of(set_code, set_type):
    return "limited" if set_code in LIMITED_SETS or set_type not in WIDE_SET_TYPES else "wide"


class Radar:
    def __init__(self, connection, collection):
        self.connection = connection
        self.collection = collection

    def report(self, summarize, min_value=0.0):
        cards, as_of = self._cards(min_value)
        changes = self._changes_since([(c["entry"], c["since"]) for c in cards])
        upcoming, recent = [], []
        for card in cards:
            entry = card.pop("entry")
            since = card.pop("since")
            summary = summarize(entry, quantity=card["held_copies"], row_id=False)
            summary["value_usd"] = card["held_value_usd"]
            item = {"card": summary, **card, "change_since_preview_pct": changes.get((entry.scryfall_id, entry.finish, since))}
            (upcoming if card["days_until"] > 0 else recent).append(item)
        upcoming.sort(key=lambda c: -c["held_value_usd"])
        recent.sort(key=lambda c: -c["held_value_usd"])
        return {
            "as_of": as_of, "upcoming": upcoming, "recent": recent,
            "totals": {
                "upcoming_cards": len(upcoming),
                "upcoming_value_usd": round(sum(c["held_value_usd"] for c in upcoming), 2),
                "upcoming_wide_value_usd": round(sum(c["held_value_usd"] for c in upcoming if c["impact"] == "wide"), 2),
                "recent_cards": len(recent),
                "recent_value_usd": round(sum(c["held_value_usd"] for c in recent), 2),
            },
        }

    def headline(self):
        """The dashboard's one-line view: how many cards, how much value, the next release."""
        cards, _ = self._cards(0.0)
        upcoming = [c for c in cards if c["days_until"] > 0]
        return {"upcoming_cards": len(upcoming),
                "upcoming_value_usd": round(sum(c["held_value_usd"] for c in upcoming), 2),
                "next_release": min((r["released_at"] for c in upcoming for r in c["reprints"]), default=None)}

    def _cards(self, min_value):
        """One item per card you hold that has a new printing; upcoming if any is still to come."""
        connection = self.connection
        as_of = get_meta(connection, "radar_as_of") or date.today().isoformat()
        today = date.fromisoformat(as_of)
        by_oracle = defaultdict(list)
        for row in connection.execute("SELECT * FROM new_printings ORDER BY released_at"):
            if row["oracle_id"] in self.collection.by_oracle_id:
                by_oracle[row["oracle_id"]].append(dict(row))

        # Your printings are not "new printings" of themselves: drop rows for a printing you hold.
        # Basic lands are reprinted in every set, so they tell you nothing.
        cards = []
        for oracle_id, printings in by_oracle.items():
            entries = self.collection.by_oracle_id[oracle_id]
            if "Basic Land" in entries[0].type_line:
                continue
            held_ids = {e.scryfall_id for e in entries}
            printings = [p for p in printings if p["scryfall_id"] not in held_ids]
            to_come = [p for p in printings if p["released_at"] > as_of]
            printings = to_come or [p for p in printings
                                    if (today - date.fromisoformat(p["released_at"])).days <= RECENT_DAYS]
            if not printings:
                continue
            lead = max(entries, key=lambda e: e.price_usd or 0)
            held_value = sum(e.value_usd for e in entries)
            if held_value < min_value:
                continue
            released = date.fromisoformat(printings[0]["released_at"] if to_come else printings[-1]["released_at"])
            reprints = [{
                "scryfall_id": p["scryfall_id"], "set_code": p["set_code"], "set_name": p["set_name"],
                "set_type": p["set_type"], "released_at": p["released_at"],
                "collector_number": p["collector_number"], "rarity": p["rarity"],
                "impact": impact_of(p["set_code"], p["set_type"]),
                "image": f"/img/{p['scryfall_id']}/front/normal",
            } for p in printings]
            preview = min((p["previewed_at"] for p in printings if p["previewed_at"]), default=None)
            since = date.fromisoformat(preview) if preview else released - timedelta(days=PREVIEW_LEAD_DAYS)
            cards.append({
                "entry": lead,
                "held_copies": sum(e.quantity for e in entries), "held_value_usd": round(held_value, 2),
                "used": sum(e.used for e in entries), "spare": sum(e.spare for e in entries),
                "impact": "wide" if any(r["impact"] == "wide" for r in reprints) else "limited",
                "days_until": (released - today).days,
                "reprints": reprints, "since": since.isoformat(),
            })
        return cards, as_of

    def _changes_since(self, targets):
        """{(scryfall_id, finish, day): percent} — each printing's latest price against its price on `day`."""
        wanted = defaultdict(set)
        for entry, day in targets:
            wanted[entry.scryfall_id].add((entry.finish, day))
        result = {}
        ids = sorted(wanted)
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = self.connection.execute(
                f"SELECT scryfall_id, source, finish, points FROM price_series WHERE currency = 'USD' "
                f"AND source IN (?, ?) AND scryfall_id IN ({','.join('?' * len(chunk))})",
                (*TRACKED_SOURCES, *chunk)).fetchall()
            # One source per printing: the one with the longest history (a change within one source only).
            best = {}
            for scryfall_id, source, finish, blob in rows:
                points = unpack_points(blob)
                key = (scryfall_id, finish)
                candidate = (len(points), -_rank(source), points)
                if key not in best or candidate[:2] > best[key][:2]:
                    best[key] = candidate
            for scryfall_id in chunk:
                for finish, day in wanted[scryfall_id]:
                    points = best.get((scryfall_id, finish), (0, 0, None))[2]
                    if not points:
                        continue
                    before = [d for d in points if d <= day]
                    if not before:
                        continue
                    old, new = points[max(before)], points[max(points)]
                    if old:
                        result[(scryfall_id, finish, day)] = round((new - old) / old * 100, 1)
        return result
