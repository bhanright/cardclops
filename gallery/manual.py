"""Adding cards to the collection by hand (docs/API.md, "Adding cards by hand").

For cards that never made it into ManaBox. They are holdings rows with source
'manual': an import keeps them, and takes over from them once the import itself
contains the same printing and finish (see ingest.store_holdings).
"""
import json
from datetime import date, datetime

from . import scryfall

FINISHES = ("normal", "foil", "etched")
CONDITIONS = ("near_mint", "excellent", "good", "light_played", "played", "poor")
# Scryfall names finishes nonfoil/foil/etched; the collection calls nonfoil "normal".
SCRYFALL_FINISH = {"normal": "nonfoil", "foil": "foil", "etched": "etched"}
EDITABLE = ("quantity", "finish", "condition", "language", "purchase_price")


def _card(connection, scryfall_id):
    """The printing's Scryfall object, fetched and cached (with today's price) if it isn't yet."""
    row = connection.execute("SELECT raw FROM cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
    if row:
        return json.loads(row[0])
    from .ingest import _record_prices, _store_card
    found = scryfall.fetch_cards_by_id([scryfall_id])
    if not found:
        raise ValueError("Scryfall doesn't know that printing")
    card = found[0]
    _store_card(connection, card)
    _record_prices(connection, card, date.today().isoformat())
    return card


def _check(fields, card):
    if "quantity" in fields:
        fields["quantity"] = int(fields["quantity"])
        if fields["quantity"] < 1:
            raise ValueError("Quantity must be at least 1 (remove the card instead)")
    if "finish" in fields:
        if fields["finish"] not in FINISHES:
            raise ValueError("finish must be normal, foil or etched")
        offered = card.get("finishes") or ["nonfoil"]
        if SCRYFALL_FINISH[fields["finish"]] not in offered:
            raise ValueError(f"{card['name']} ({card.get('set', '').upper()}) wasn't printed in that finish")
    if "condition" in fields and fields["condition"] not in CONDITIONS:
        raise ValueError(f"condition must be one of {', '.join(CONDITIONS)}")
    if "purchase_price" in fields and fields["purchase_price"] not in (None, ""):
        fields["purchase_price"] = float(fields["purchase_price"])
    elif "purchase_price" in fields:
        fields["purchase_price"] = None
    return fields


def row_json(connection, row):
    card = _card(connection, row["scryfall_id"])
    key = {"normal": "usd", "foil": "usd_foil", "etched": "usd_etched"}[row["finish"]]
    price = (card.get("prices") or {}).get(key) or ((card.get("prices") or {}).get("usd_foil")
                                                     if row["finish"] == "etched" else None)
    return {"row_id": row["row_id"], "scryfall_id": row["scryfall_id"], "name": row["name"],
            "set_code": row["set_code"], "set_name": card.get("set_name"), "collector_number": row["collector_number"],
            "finish": row["finish"], "quantity": row["quantity"], "condition": row["condition"],
            "language": row["language"], "purchase_price": row["purchase_price"], "added_at": row["added_at"],
            "price_usd": float(price) if price else None, "image": f"/img/{row['scryfall_id']}/front/normal"}


def add(connection, body):
    scryfall_id = (body.get("scryfall_id") or "").strip()
    if not scryfall_id:
        raise ValueError("Choose a printing")
    card = _card(connection, scryfall_id)
    fields = _check({"finish": body.get("finish") or "normal", "quantity": body.get("quantity", 1),
                     "condition": body.get("condition") or "near_mint",
                     "purchase_price": body.get("purchase_price")}, card)
    row_id = connection.execute(
        "INSERT INTO holdings (scryfall_id, name, set_code, collector_number, finish, rarity, quantity, "
        "purchase_price, purchase_currency, misprint, altered, signed, proxy, condition, language, added_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'USD', 0, 0, 0, 0, ?, ?, ?, 'manual')",
        (scryfall_id, card["name"], card.get("set", ""), card.get("collector_number"), fields["finish"],
         card.get("rarity"), fields["quantity"], fields["purchase_price"], fields["condition"],
         (body.get("language") or card.get("lang") or "en"), datetime.now().isoformat(timespec="seconds"))).lastrowid
    connection.commit()
    return row_json(connection, _row(connection, row_id))


def _row(connection, row_id):
    row = connection.execute("SELECT * FROM holdings WHERE row_id = ? AND source = 'manual'", (row_id,)).fetchone()
    if row is None:
        raise KeyError(f"no hand-added row {row_id} (imported cards change by re-importing)")
    return row


def rows(connection):
    return [row_json(connection, row) for row in connection.execute(
        "SELECT * FROM holdings WHERE source = 'manual' ORDER BY added_at DESC, row_id DESC").fetchall()]


def update(connection, row_id, body):
    row = _row(connection, row_id)
    fields = _check({key: body[key] for key in EDITABLE if key in body}, _card(connection, row["scryfall_id"]))
    if fields:
        connection.execute(f"UPDATE holdings SET {', '.join(f'{k} = ?' for k in fields)} WHERE row_id = ?",
                           (*fields.values(), row_id))
        connection.commit()
    return row_json(connection, _row(connection, row_id))


def delete(connection, row_id):
    _row(connection, row_id)
    connection.execute("DELETE FROM holdings WHERE row_id = ?", (row_id,))
    connection.commit()
    return {"deleted": row_id}
