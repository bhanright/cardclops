"""Importing the ManaBox export and refreshing Scryfall data.

`import_manabox` replaces the holdings with a new export. `refresh_scryfall`
pulls today's bulk files, stores the card data for every held printing,
records today's prices, and notes any legality changes since the last refresh.
"""
import csv
import json
from datetime import date, timedelta

from . import scryfall
from .db import merge_price_points, set_meta

FINISHES = ("normal", "foil", "etched")
# Scryfall price keys for each finish. USD comes from TCGplayer, EUR from
# Cardmarket and TIX from Cardhoarder.
PRICE_KEYS = {
    ("normal", "USD"): "usd", ("foil", "USD"): "usd_foil", ("etched", "USD"): "usd_etched",
    ("normal", "EUR"): "eur", ("foil", "EUR"): "eur_foil", ("normal", "TIX"): "tix",
}


def _flag(text):
    return 1 if str(text).strip().lower() == "true" else 0


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def import_manabox(connection, csv_path, log=print):
    """Replace the holdings table with the rows of a ManaBox CSV export."""
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"Name", "Scryfall ID", "Quantity", "Foil"}
    missing = required - set(rows[0].keys() if rows else ())
    if missing:
        raise ValueError(f"{csv_path} does not look like a ManaBox export (missing {', '.join(sorted(missing))})")

    store_holdings(connection, [
        {
            "scryfall_id": row["Scryfall ID"].strip(), "name": row["Name"], "set_code": row.get("Set code", "").lower(),
            "collector_number": row.get("Collector number"), "finish": row["Foil"].strip().lower() or "normal",
            "rarity": row.get("Rarity"), "quantity": int(row["Quantity"] or 0), "manabox_id": row.get("ManaBox ID"),
            "purchase_price": _number(row.get("Purchase price")),
            "purchase_currency": row.get("Purchase price currency"), "misprint": _flag(row.get("Misprint")),
            "altered": _flag(row.get("Altered")), "signed": _flag(row.get("Signed")), "proxy": _flag(row.get("Proxy")),
            "condition": row.get("Condition"), "language": row.get("Language"), "added_at": row.get("Added"),
        }
        for row in rows
    ], str(csv_path))
    copies = sum(int(row["Quantity"] or 0) for row in rows)
    log(f"Imported {len(rows):,} rows ({copies:,} copies) from {csv_path}")


HOLDING_COLUMNS = ("scryfall_id", "name", "set_code", "collector_number", "finish", "rarity", "quantity",
                   "manabox_id", "purchase_price", "purchase_currency", "misprint", "altered", "signed", "proxy",
                   "condition", "language", "added_at")


def store_holdings(connection, rows, source):
    """Replace the imported part of the collection with `rows` (dicts keyed by HOLDING_COLUMNS).

    Cards added by hand in Cardclops (source 'manual') stay. Where the import now holds more of
    the same printing and finish than the previous import did, those new copies take over: the
    manual quantity drops by the increase, so a card added by hand and later entered in ManaBox
    isn't counted twice, and importing an unchanged file changes nothing. Returns those
    adjustments as [{"name", "finish", "removed"}]."""
    previous = {}
    for row in connection.execute("SELECT scryfall_id, finish, quantity FROM holdings WHERE source != 'manual'"):
        key = (row["scryfall_id"], row["finish"] or "normal")
        previous[key] = previous.get(key, 0) + (row["quantity"] or 0)
    connection.execute("DELETE FROM holdings WHERE source != 'manual'")
    connection.executemany(
        f"INSERT INTO holdings ({', '.join(HOLDING_COLUMNS)}) VALUES ({', '.join('?' * len(HOLDING_COLUMNS))})",
        [tuple(row.get(column) for column in HOLDING_COLUMNS) for row in rows])
    imported = {}
    for row in rows:
        key = (row.get("scryfall_id"), row.get("finish") or "normal")
        imported[key] = imported.get(key, 0) + int(row.get("quantity") or 0)
    added = {key: quantity - previous.get(key, 0) for key, quantity in imported.items() if quantity > previous.get(key, 0)}
    reconciled = []
    for manual in connection.execute(
            "SELECT row_id, scryfall_id, finish, quantity, name FROM holdings WHERE source = 'manual'").fetchall():
        covered = added.get((manual["scryfall_id"], manual["finish"]), 0)
        if not covered:
            continue
        removed = min(covered, manual["quantity"])
        added[(manual["scryfall_id"], manual["finish"])] = covered - removed
        if removed == manual["quantity"]:
            connection.execute("DELETE FROM holdings WHERE row_id = ?", (manual["row_id"],))
        else:
            connection.execute("UPDATE holdings SET quantity = quantity - ? WHERE row_id = ?", (removed, manual["row_id"]))
        reconciled.append({"name": manual["name"], "finish": manual["finish"], "removed": removed})
    set_meta(connection, "manabox_file", str(source))
    set_meta(connection, "manabox_imported_at", date.today().isoformat())
    connection.commit()
    return reconciled


def oracle_id_of(card):
    """Reversible cards keep their oracle id on each face rather than the card."""
    if card.get("oracle_id"):
        return card["oracle_id"]
    faces = card.get("card_faces") or [{}]
    return faces[0].get("oracle_id")


def _store_card(connection, card):
    connection.execute(
        "INSERT OR REPLACE INTO cards (scryfall_id, oracle_id, name, raw) VALUES (?, ?, ?, ?)",
        (card["id"], oracle_id_of(card), card["name"], json.dumps(card, separators=(",", ":"))),
    )


def _record_prices(connection, card, day):
    for (finish, currency), key in PRICE_KEYS.items():
        value = _number((card.get("prices") or {}).get(key))
        if value is not None:
            merge_price_points(connection, card["id"], "scryfall", finish, currency, {day: value})


def tracked_printings(connection):
    """Printings you don't necessarily own but whose data and prices the gallery needs daily."""
    return {row[0] for row in connection.execute(
        "SELECT scryfall_id FROM deck_lines WHERE scryfall_id IS NOT NULL "
        "UNION SELECT scryfall_id FROM watchlist")}


def refresh_scryfall(connection, log=print, progress=None):
    """Pull today's Scryfall data. Safe to run as often as you like, and before any collection
    exists (the setup wizard downloads card data first). `progress(fraction)` reports the download."""
    held_ids = {row[0] for row in connection.execute("SELECT DISTINCT scryfall_id FROM holdings")}
    # Printings named by decklists or on the watchlist are kept up to date too, owned or not.
    held_ids |= tracked_printings(connection)

    log("Scryfall card data")
    bulk_path, updated_at = scryfall.bulk_file("default_cards", log, progress)
    price_day = updated_at[:10]

    cheapest = {}   # oracle_id -> (usd, scryfall_id, name, card) for the deck checker
    found = set()
    printings = []
    set_cards = []                  # every card of every set, for the collector view
    reprints = []                   # for the reprint radar: reprints out in the last 90 days or not out yet
    radar_since = (date.fromisoformat(price_day) - timedelta(days=90)).isoformat()
    for card in scryfall.iter_bulk(bulk_path):
        oracle_id = oracle_id_of(card)
        printings.append((card.get("set", ""), card.get("collector_number", ""), card["id"], oracle_id, card["name"]))
        set_cards.append(_set_card_row(card, oracle_id))
        if (card.get("reprint") and oracle_id and not card.get("digital") and card.get("released_at", "") >= radar_since
                and card.get("layout") not in ("token", "double_faced_token", "emblem", "art_series")):
            front = card.get("image_uris") or ((card.get("card_faces") or [{}])[0].get("image_uris")) or {}
            reprints.append((card["id"], oracle_id, card["name"], card.get("set"), card.get("set_name"),
                             card.get("set_type"), card.get("released_at"), card.get("collector_number"),
                             card.get("rarity"), (card.get("preview") or {}).get("previewed_at"), json.dumps(front)))
        if card["id"] in held_ids:
            found.add(card["id"])
            _store_card(connection, card)
            _record_prices(connection, card, price_day)
        if not oracle_id or card.get("digital") or card.get("layout") in ("token", "double_faced_token", "emblem", "art_series"):
            continue
        usd = _number((card.get("prices") or {}).get("usd"))
        best = cheapest.get(oracle_id)
        if best is None or (usd is not None and (best[0] is None or usd < best[0])):
            cheapest[oracle_id] = (usd, card["id"], card["name"], card)

    missing = held_ids - found
    if missing:
        log(f"  fetching {len(missing)} printings not in the bulk file")
        for card in scryfall.fetch_cards_by_id(missing):
            _store_card(connection, card)
            _record_prices(connection, card, price_day)

    connection.execute("DELETE FROM new_printings")
    connection.executemany("INSERT OR REPLACE INTO new_printings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", reprints)
    set_meta(connection, "radar_as_of", price_day)

    connection.execute("DELETE FROM set_cards")
    connection.executemany(f"INSERT OR REPLACE INTO set_cards VALUES ({', '.join('?' * 17)})", set_cards)

    connection.execute("DELETE FROM printings")
    connection.executemany("INSERT OR IGNORE INTO printings VALUES (?, ?, ?, ?, ?)", printings)

    connection.execute("DELETE FROM oracle_cards")
    connection.executemany(
        "INSERT INTO oracle_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (oracle_id, name, name.lower(), card.get("type_line"),
             card.get("mana_cost") or (card.get("card_faces") or [{}])[0].get("mana_cost"),
             "".join(card.get("color_identity", [])), usd, scryfall_id)
            for oracle_id, (usd, scryfall_id, name, card) in cheapest.items()
        ],
    )

    log("Scryfall sets")
    connection.execute("DELETE FROM sets")
    connection.executemany(
        "INSERT INTO sets (code, name, set_type, released_at, card_count, icon_svg_uri, printed_size, digital, "
        "parent_set_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(s["code"], s["name"], s.get("set_type"), s.get("released_at"), s.get("card_count"), s.get("icon_svg_uri"),
          s.get("printed_size"), int(bool(s.get("digital"))), s.get("parent_set_code"))
         for s in scryfall.fetch_sets()],
    )

    log("Scryfall function tags")
    tags_path, _ = scryfall.bulk_file("oracle_tags", log)
    connection.execute("DELETE FROM oracle_tags")
    connection.execute("DELETE FROM oracle_taggings")
    for tag in scryfall.iter_bulk(tags_path):
        connection.execute(
            "INSERT OR REPLACE INTO oracle_tags VALUES (?, ?, ?, ?, ?, ?)",
            (tag["slug"], tag["id"], tag.get("label"), tag.get("description"),
             json.dumps(tag.get("parent_ids", [])), json.dumps(tag.get("child_ids", []))),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO oracle_taggings VALUES (?, ?)",
            [(tag["slug"], tagging["oracle_id"]) for tagging in tag.get("taggings", [])],
        )

    changes = _note_legality_changes(connection, price_day)
    set_meta(connection, "scryfall_updated_at", updated_at)
    connection.commit()
    log(f"Stored {len(held_ids):,} printings, prices for {price_day}, {changes} legality changes")


SPECIAL_FRAMES = {"showcase", "extendedart", "inverted", "etched", "shatteredglass"}


def is_variant_printing(card):
    """Outside a set's main numbering: promos, variants and special treatments (Scryfall gives a
    printed set size for only a few sets, so the collector view can't just cut at a number)."""
    number = card.get("collector_number", "")
    basic = "Basic Land" in (card.get("type_line") or "")
    # Not promo_types: Scryfall tags ordinary cards with some of them ("universesbeyond").
    return bool(card.get("variation") or card.get("promo") or "boosterfun" in (card.get("promo_types") or [])
                or not number.isdigit() or number == "0"
                or set(card.get("frame_effects") or []) & SPECIAL_FRAMES
                or (card.get("border_color") == "borderless" and not basic)
                or (card.get("full_art") and not basic))


def _set_card_row(card, oracle_id):
    faces = card.get("card_faces") or []
    front = card.get("image_uris") or (faces[0].get("image_uris") if faces else None) or {}
    back = (faces[1].get("image_uris") if len(faces) > 1 and "image_uris" not in card else None) or {}
    prices = card.get("prices") or {}
    variation = is_variant_printing(card)
    colors = card.get("colors")
    if colors is None:
        colors = [c for face in faces for c in face.get("colors", [])]
    return (card["id"], card.get("set", ""), card.get("collector_number", ""), card["name"], oracle_id,
            card.get("rarity"), card.get("type_line") or (faces[0].get("type_line") if faces else None),
            card.get("mana_cost") or (faces[0].get("mana_cost") if faces else None),
            "".join(c for c in "WUBRG" if c in colors), card.get("layout"), json.dumps(card.get("finishes", [])),
            json.dumps({k: prices.get(k) for k in ("usd", "usd_foil", "usd_etched")}),
            front.get("normal"), back.get("normal"), int(variation), card.get("released_at"),
            int(bool(card.get("booster"))))


def _note_legality_changes(connection, day):
    """Compare each held card's legalities with the last refresh; log what moved."""
    seen = {row["oracle_id"]: json.loads(row["legalities"])
            for row in connection.execute("SELECT oracle_id, legalities FROM legality_seen")}
    first_run = not seen
    changes = 0
    for row in connection.execute(
        "SELECT oracle_id, name, json_extract(raw, '$.legalities') AS legalities FROM cards "
        "WHERE oracle_id IS NOT NULL GROUP BY oracle_id"
    ).fetchall():
        current = json.loads(row["legalities"] or "{}")
        before = seen.get(row["oracle_id"])
        if before is not None and not first_run:
            for fmt, status in current.items():
                if before.get(fmt) != status:
                    connection.execute(
                        "INSERT INTO legality_changes VALUES (?, ?, ?, ?, ?, ?)",
                        (day, row["oracle_id"], row["name"], fmt, before.get(fmt), status),
                    )
                    changes += 1
        connection.execute(
            "INSERT OR REPLACE INTO legality_seen VALUES (?, ?)",
            (row["oracle_id"], json.dumps(current)),
        )
    return changes
