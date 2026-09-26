"""Price history questions: recent change per card, the portfolio's value over time,
and the series behind each card's chart.

Several sources can hold prices for the same printing (Scryfall's daily
snapshot, MTGJSON's vendors). A change or a portfolio line is only ever
computed within one source, so two sources that disagree by a few cents never
show up as a fake price move. Sources are tried in SOURCE_PREFERENCE order.
"""
from collections import defaultdict
from datetime import date, timedelta

from .db import unpack_points

# Which USD source to trust first when several cover the same days. Scryfall's
# USD is TCGplayer's market price; MTGJSON's TCGplayer "retail" is the same
# measure a day later, so it comes next. Changes and the portfolio use these two.
SOURCE_PREFERENCE = ["scryfall", "mtgjson:tcgplayer:retail", "mtgjson:manapool:retail",
                     "mtgjson:cardkingdom:retail"]
TRACKED_SOURCES = SOURCE_PREFERENCE[:2]

SOURCE_LABELS = {
    "scryfall": "TCGplayer market (daily Scryfall snapshot)",
    "mtgjson:tcgplayer:retail": "TCGplayer market (MTGJSON)",
    "mtgjson:tcgplayer:buylist": "TCGplayer buylist (MTGJSON)",
    "mtgjson:manapool:retail": "ManaPool (MTGJSON)",
    "mtgjson:cardkingdom:retail": "Card Kingdom retail (MTGJSON)",
    "mtgjson:cardkingdom:buylist": "Card Kingdom buylist (MTGJSON)",
    "mtgjson:cardmarket:retail": "Cardmarket (MTGJSON)",
}

CHANGE_WINDOWS = (1, 7, 30)
# A change may compare against a snapshot up to this many days older than the
# window asks for (a missed refresh); any older and the window would mislead.
CHANGE_SLACK_DAYS = 2
# A portfolio day needs this share of the collection's priceable printings,
# or its total would dip just because a source had gaps that day.
PORTFOLIO_MIN_COVERAGE = 0.8


def _rank(source):
    return SOURCE_PREFERENCE.index(source) if source in SOURCE_PREFERENCE else len(SOURCE_PREFERENCE)


def _change(points, window):
    """(old, new, percent) over `window` days ending at the series' newest day, or None."""
    newest = max(points)
    target = date.fromisoformat(newest) - timedelta(days=window)
    for slack in range(CHANGE_SLACK_DAYS + 1):
        old = points.get((target - timedelta(days=slack)).isoformat())
        if old:
            new = points[newest]
            return old, new, (new - old) / old * 100
    return None


class PriceBook:
    def __init__(self, connection, collection):
        self.connection = connection
        self.collection = collection
        self.reload()

    def reload(self):
        """One pass over the tracked series of held printings: changes, portfolio and coverage."""
        quantities = defaultdict(int)
        for entry in self.collection.entries:
            quantities[(entry.scryfall_id, entry.finish)] += entry.quantity

        changes_by_source = defaultdict(dict)       # (id, finish) -> source -> {window: change}
        newest_price = {}                           # (id, finish) -> newest price in the fallback source
        daily = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))   # source -> day -> [value, priced]
        priceable = set()
        latest = None
        for scryfall_id, source, finish, blob in self.connection.execute(
            "SELECT scryfall_id, source, finish, points FROM price_series "
            "WHERE currency = 'USD' AND source IN (?, ?)", TRACKED_SOURCES,
        ):
            key = (scryfall_id, finish)
            quantity = quantities.get(key)
            if not quantity:
                continue
            points = unpack_points(blob)
            if not points:
                continue
            priceable.add(key)
            changes_by_source[key][source] = {
                window: change for window in CHANGE_WINDOWS if (change := _change(points, window))}
            by_day = daily[source]
            for day, price in points.items():
                slot = by_day[day]
                slot[0] += price * quantity
                slot[1] += 1
            newest = max(points)
            if source == "scryfall":
                latest = newest if latest is None or newest > latest else latest
            else:
                newest_price[key] = points[newest]

        # Scryfall has no price for some printings (foils, mostly) that
        # MTGJSON's TCGplayer feed does price; without this, ~4% of the
        # collection's value would count as zero.
        for entry in self.collection.entries:
            if entry.price_usd is None:
                entry.price_usd = newest_price.get((entry.scryfall_id, entry.finish))

        # Per window, the most trusted source that can answer it.
        self.changes = {}
        for key, by_source in changes_by_source.items():
            merged = {}
            for source in sorted(by_source, key=_rank):
                for window, change in by_source[source].items():
                    merged.setdefault(window, change)
            self.changes[key] = merged

        # The portfolio line comes from one source, the one covering the most
        # days, so switching vendors never shows up as a jump in value.
        needed = max(1, len(priceable)) * PORTFOLIO_MIN_COVERAGE
        covered = {source: {day: value for day, (value, priced) in days.items() if priced >= needed}
                   for source, days in daily.items()}
        covered = {source: days for source, days in covered.items() if days}
        self.portfolio_source = None
        self.portfolio_points = []
        if covered:
            best = min(covered, key=lambda source: (-len(covered[source]), _rank(source)))
            self.portfolio_source = best
            self.portfolio_points = [[day, round(value, 2)] for day, value in sorted(covered[best].items())]
        all_days = sorted({day for days in daily.values() for day in days})
        self.latest_day = latest or (all_days[-1] if all_days else None)
        self._coverage = {"first_day": all_days[0] if all_days else None,
                          "last_day": all_days[-1] if all_days else None, "days": len(all_days)}

    def change(self, entry, window):
        return self.changes.get((entry.scryfall_id, entry.finish), {}).get(window)

    def change_percent(self, entry, window):
        change = self.change(entry, window)
        return round(change[2], 1) if change else None

    def movers(self, window, min_price, limit):
        rows = []
        for entry in self.collection.entries:
            change = self.change(entry, window)
            if change and max(change[0], change[1]) >= min_price:
                rows.append((entry, change))
        rows.sort(key=lambda item: item[1][2])
        return rows[-limit:][::-1], rows[:limit]

    def series(self, scryfall_id, finishes):
        """Every stored series for a printing; the longest USD history for a held finish comes first."""
        rows = self.connection.execute(
            "SELECT source, finish, currency, points FROM price_series WHERE scryfall_id = ?",
            (scryfall_id,)).fetchall()
        series = [{"source": source, "label": SOURCE_LABELS.get(source, source), "finish": finish,
                   "currency": currency, "points": sorted(unpack_points(blob).items())}
                  for source, finish, currency, blob in rows]
        series.sort(key=lambda s: (s["currency"] != "USD", s["finish"] not in finishes, -len(s["points"]),
                                   _rank(s["source"])))
        return series

    def portfolio(self):
        return self.portfolio_points

    def coverage(self):
        sources = [row[0] for row in self.connection.execute("SELECT DISTINCT source FROM price_series")]
        from .db import DAILY_PRICE_DAYS
        return {**self._coverage, "daily_days": DAILY_PRICE_DAYS,
                "sources": [{"source": s, "label": SOURCE_LABELS.get(s, s)} for s in sorted(sources, key=_rank)]}

    def value_history(self, quantities):
        """Value over time of {(scryfall_id, finish): copies}, e.g. the copies one deck uses.

        Same rules as the portfolio: one source for the whole line, and only days
        on which most of the cards have a price.
        """
        if not quantities:
            return [], None
        ids = sorted({scryfall_id for scryfall_id, _ in quantities})
        daily = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for scryfall_id, source, finish, blob in self.connection.execute(
                f"SELECT scryfall_id, source, finish, points FROM price_series WHERE currency = 'USD' "
                f"AND source IN (?, ?) AND scryfall_id IN ({','.join('?' * len(chunk))})",
                (*TRACKED_SOURCES, *chunk),
            ):
                quantity = quantities.get((scryfall_id, finish))
                if not quantity:
                    continue
                for day, price in unpack_points(blob).items():
                    slot = daily[source][day]
                    slot[0] += price * quantity
                    slot[1] += 1
        needed = len(quantities) * PORTFOLIO_MIN_COVERAGE
        covered = {source: {day: v for day, (v, n) in days.items() if n >= needed} for source, days in daily.items()}
        covered = {source: days for source, days in covered.items() if days}
        if not covered:
            return [], None
        best = min(covered, key=lambda source: (-len(covered[source]), _rank(source)))
        return [[day, round(value, 2)] for day, value in sorted(covered[best].items())], best
