"""Deck changelog (docs/TOOLS2.md): every version of a deck's list, what changed
in its cards, and what changed in its numbers.

A version is recorded after each change to the list, holding the lines and a
snapshot of headline statistics as they were then (prices included), so the
changelog shows the deck's history rather than today's view of old lists.
"""
import json
from collections import defaultdict
from datetime import datetime

LINE_FIELDS = ("section", "quantity", "name", "oracle_id", "scryfall_id",
               "requested_set", "requested_number", "requested_finish")
FUNCTIONS = ("ramp", "card_draw", "removal", "board_wipes", "counterspells", "tutors", "protection", "recursion")


def headline(stats, castability):
    """The few numbers worth comparing between versions."""
    if not stats:
        return None
    return {
        "cards": stats["counts"]["cards"], "lands": stats["counts"]["lands"],
        "average_mv": stats["average_mv"]["nonland"], "castability": castability,
        "curve": stats["curve"], "pips": stats["pips"], "sources": stats["sources"],
        "functions": {name: len(stats["functions"].get(name, [])) for name in FUNCTIONS},
        "game_changers": len(stats["bracket_signals"].get("game_changers", [])),
        "price_usd": stats["price"]["total_usd"], "legal": stats["legality"]["legal"],
    }


def record(book, deck_id, reason):
    """Snapshot the deck as it now stands (call after the change and a reload)."""
    from .manafix import suggest_swaps
    lines = [{field: state.row[field] for field in LINE_FIELDS}
             for state in sorted(book.lines[deck_id], key=lambda s: s.row["position"])]
    stats = book.stats(deck_id)
    castability = None
    if stats:
        cards = book.deck_cards(deck_id)
        identity = set(book.summary(deck_id, stats)["color_identity"])
        castability = suggest_swaps(cards, [], book.decks[deck_id]["format"], identity, max_swaps=0,
                                    tag_index=book.collection.tag_index)["before"]["score"]
    book.connection.execute(
        "INSERT INTO deck_versions (deck_id, created_at, reason, lines, stats) VALUES (?, ?, ?, ?, ?)",
        (deck_id, datetime.now().isoformat(timespec="seconds"), reason, json.dumps(lines),
         json.dumps(headline(stats, castability))))
    book.connection.commit()


def _counts(lines):
    counts = defaultdict(int)
    names = {}
    for line in lines:
        key = (line["section"], line["oracle_id"] or line["name"].lower())
        counts[key] += line["quantity"]
        names[key] = line["name"]
    return counts, names


def _number_diff(new, old):
    if new is None or old is None:
        return None
    change = round(new - old, 3)
    return change or None


def _dict_diff(new, old):
    if not new or not old:
        return None
    changes = {k: round((new.get(k) or 0) - (old.get(k) or 0), 3) for k in set(new) | set(old)}
    changes = {k: v for k, v in changes.items() if v}
    return changes or None


def diff(new_stats, old_stats):
    if not new_stats or not old_stats:
        return None
    return {
        "lands": _number_diff(new_stats["lands"], old_stats["lands"]),
        "average_mv": _number_diff(new_stats["average_mv"], old_stats["average_mv"]),
        "castability": _number_diff(new_stats["castability"], old_stats["castability"]),
        "price_usd": _number_diff(new_stats["price_usd"], old_stats["price_usd"]),
        "game_changers": _number_diff(new_stats["game_changers"], old_stats["game_changers"]),
        "sources": _dict_diff(new_stats["sources"], old_stats["sources"]),
        "pips": _dict_diff(new_stats["pips"], old_stats["pips"]),
        "functions": _dict_diff(new_stats["functions"], old_stats["functions"]),
        "curve": _dict_diff(new_stats["curve"], old_stats["curve"]),
        "legal": None if new_stats["legal"] == old_stats["legal"] else new_stats["legal"],
    }


def history(connection, deck_id):
    rows = connection.execute("SELECT * FROM deck_versions WHERE deck_id = ? ORDER BY version_id",
                              (deck_id,)).fetchall()
    versions, previous = [], None
    for row in rows:
        lines = json.loads(row["lines"])
        stats = json.loads(row["stats"]) if row["stats"] else None
        added, removed = [], []
        if previous is not None:
            new_counts, new_names = _counts(lines)
            old_counts, old_names = _counts(previous["lines"])
            for key in sorted(set(new_counts) | set(old_counts), key=lambda k: (k[0], (new_names | old_names)[k])):
                change = new_counts.get(key, 0) - old_counts.get(key, 0)
                item = {"name": (new_names | old_names)[key], "section": key[0], "quantity": abs(change)}
                if change > 0:
                    added.append(item)
                elif change < 0:
                    removed.append(item)
        versions.append({
            "version_id": row["version_id"], "created_at": row["created_at"], "reason": row["reason"],
            "card_count": sum(l["quantity"] for l in lines if l["section"] in ("commander", "companion", "main")),
            "added": added, "removed": removed, "stats": stats,
            "diff": diff(stats, previous["stats"]) if previous is not None else None,
        })
        previous = {"lines": lines, "stats": stats}
    return versions[::-1]


def lines_of(connection, deck_id, version_id):
    row = connection.execute("SELECT lines FROM deck_versions WHERE deck_id = ? AND version_id = ?",
                             (deck_id, version_id)).fetchone()
    if row is None:
        raise KeyError(f"no version {version_id} of deck {deck_id}")
    return json.loads(row["lines"])
