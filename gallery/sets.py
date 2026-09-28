"""The collector view (docs/SETUP.md): every set, every card in it, and what you own.

Completion counts distinct cards of a set. "Main set" means the set's own
numbering: collector numbers up to its printed size that aren't variations or
promos; with variants, every card Scryfall lists in the set counts.
"""
import json
import re
from collections import defaultdict

DIGITS = re.compile(r"\d+")
IMAGE_SIZES = ("small", "normal", "large", "png", "art_crop", "border_crop")


def _number(collector_number):
    match = DIGITS.search(collector_number or "")
    return int(match.group()) if match else None


def collector_order(collector_number):
    """Numeric part first, so 2 sorts before 10 and 10a after 10."""
    number = _number(collector_number)
    return (number if number is not None else 10**9, collector_number or "")


def image_url(normal_url, size):
    """Scryfall's image URLs differ only in the size segment (and .png for png)."""
    if not normal_url or size not in IMAGE_SIZES:
        return None
    url = normal_url.replace("/normal/", f"/{size}/")
    return url.replace(".jpg", ".png") if size == "png" else url


class SetBook:
    def __init__(self, connection, collection):
        self.connection = connection
        self.collection = collection
        self.sets = {row["code"]: dict(row) for row in connection.execute(
            "SELECT * FROM sets WHERE COALESCE(digital, 0) = 0")}
        self.booster_sets = {row[0] for row in connection.execute(
            "SELECT DISTINCT set_code FROM set_cards WHERE in_booster = 1")}
        # What you own, by printing and by card.
        self.owned_printing = defaultdict(lambda: {"nonfoil": 0, "foil": 0, "value": 0.0})
        self.owned_card = defaultdict(int)
        for entry in collection.entries:
            slot = self.owned_printing[entry.scryfall_id]
            slot["foil" if entry.finish in ("foil", "etched") else "nonfoil"] += entry.quantity
            slot["value"] += entry.value_usd
            self.owned_card[entry.oracle_id] += entry.quantity
        self._summaries = None

    def main_size(self, code):
        return self.sets[code].get("printed_size") or 0

    def in_main_set(self, code, card):
        # The variation flag (ingest.is_variant_printing) already excludes promos, special
        # treatments and non-numeric numbers. For sets sold in boosters, the main set is what
        # the boosters contain (Commander-deck cards, bundle basics and Jumpstart extras share
        # the numbering but aren't in them). A printed size, when Scryfall has one, also cuts
        # off anything numbered past the set.
        number = _number(card["collector_number"])
        size = self.main_size(code)
        if code in self.booster_sets and not card["in_booster"]:
            return False
        return not card["variation"] and number is not None and (not size or number <= size)

    def _owned(self, card, mode, finish):
        """Copies that count for completion under the filters."""
        if mode == "card":
            return self.owned_card.get(card["oracle_id"], 0) if card["oracle_id"] else 0
        owned = self.owned_printing.get(card["scryfall_id"])
        if not owned:
            return 0
        return {"any": owned["nonfoil"] + owned["foil"], "nonfoil": owned["nonfoil"], "foil": owned["foil"]}[finish]

    def cards_of(self, code):
        """A set's cards in collector-number order (read on demand: all sets together are ~118k rows)."""
        rows = [dict(row) for row in self.connection.execute("SELECT * FROM set_cards WHERE set_code = ?", (code,))]
        return sorted(rows, key=lambda c: collector_order(c["collector_number"]))

    def summaries(self):
        """One row per set, main set in printing mode (cached until the collection or cache reloads)."""
        if self._summaries is None:
            by_set = defaultdict(list)          # only the few columns completion needs
            for row in self.connection.execute(
                    "SELECT scryfall_id, set_code, collector_number, variation, oracle_id, in_booster FROM set_cards"):
                if row["set_code"] in self.sets:
                    by_set[row["set_code"]].append(row)
            rows = []
            for code, info in self.sets.items():
                cards = by_set.get(code, [])
                if not cards:
                    continue
                main = [c for c in cards if self.in_main_set(code, c)] or cards
                owned = sum(1 for c in main if self._owned(c, "printing", "any"))
                copies = sum(self.owned_printing[c["scryfall_id"]]["nonfoil"] + self.owned_printing[c["scryfall_id"]]["foil"]
                             for c in cards if c["scryfall_id"] in self.owned_printing)
                value = sum(self.owned_printing[c["scryfall_id"]]["value"] for c in cards
                            if c["scryfall_id"] in self.owned_printing)
                rows.append({
                    "code": code, "name": info["name"], "set_type": info["set_type"], "released_at": info["released_at"],
                    "icon": info["icon_svg_uri"], "parent_set": info.get("parent_set_code"), "digital": False,
                    "cards": len(main), "cards_with_variants": len(cards), "owned": owned,
                    "completion": round(owned / len(main), 4) if main else 0.0,
                    "owned_copies": copies, "owned_value_usd": round(value, 2),
                })
            self._summaries = rows
        return self._summaries

    def list(self, params):
        rows = self.summaries()
        query = (params.get("q") or "").strip().lower()
        if query:
            rows = [r for r in rows if query in r["name"].lower() or query == r["code"]]
        types = {t for t in (params.get("types") or "").split(",") if t}
        if types:
            rows = [r for r in rows if r["set_type"] in types]
        if params.get("owned") in ("1", "true"):
            rows = [r for r in rows if r["owned_copies"]]
        sort = params.get("sort", "released")
        keys = {"released": lambda r: r["released_at"] or "", "name": lambda r: r["name"].lower(),
                "completion": lambda r: (r["completion"], r["owned"]), "owned_value": lambda r: r["owned_value_usd"]}
        rows = sorted(rows, key=keys.get(sort, keys["released"]), reverse=(sort != "name"))
        return {"sets": rows, "set_types": sorted({r["set_type"] for r in self.summaries() if r["set_type"]})}

    def detail(self, code, params):
        if code not in self.sets:
            raise KeyError(f"no set {code}")
        mode = params.get("mode", "printing") if params.get("mode") in ("printing", "card") else "printing"
        finish = params.get("finish", "any") if params.get("finish") in ("any", "nonfoil", "foil") else "any"
        variants = params.get("variants") in ("1", "true")
        summary = next((r for r in self.summaries() if r["code"] == code), None)
        cards, totals = [], {"cards": 0, "owned": 0, "by_rarity": defaultdict(lambda: {"owned": 0, "cards": 0}),
                             "owned_value_usd": 0.0, "cost_to_complete_usd": 0.0}
        for card in self.cards_of(code):
            main = self.in_main_set(code, card)
            counts = variants or main
            have = self._owned(card, mode, finish)
            printing = self.owned_printing.get(card["scryfall_id"], {"nonfoil": 0, "foil": 0, "value": 0.0})
            stored = json.loads(card["prices"] or "{}")          # only the prices known are stored
            prices = {key: stored.get(key) for key in ("usd", "usd_foil", "usd_etched")}
            same_card = self.owned_card.get(card["oracle_id"], 0) if card["oracle_id"] else 0
            if counts:
                totals["cards"] += 1
                rarity = totals["by_rarity"][card["rarity"] or "special"]
                rarity["cards"] += 1
                if have:
                    totals["owned"] += 1
                    rarity["owned"] += 1
                else:
                    price_key = "usd_foil" if finish == "foil" else "usd"
                    price = prices.get(price_key) or prices.get("usd") or prices.get("usd_foil") or prices.get("usd_etched")
                    totals["cost_to_complete_usd"] += float(price) if price else 0.0
            totals["owned_value_usd"] += printing["value"]
            cards.append({
                "scryfall_id": card["scryfall_id"], "collector_number": card["collector_number"], "name": card["name"],
                "rarity": card["rarity"], "type_line": card["type_line"], "mana_cost": card["mana_cost"],
                "colors": list(card["colors"] or ""),
                "image": f"/img/{card['scryfall_id']}/front/normal",
                "image_back": f"/img/{card['scryfall_id']}/back/normal" if card["image_back"] else None,
                "prices": prices, "finishes": json.loads(card["finishes"] or "[]"), "variant": not main,
                "owned": printing["nonfoil"] + printing["foil"], "owned_nonfoil": printing["nonfoil"],
                "owned_foil": printing["foil"],
                "owned_elsewhere": max(0, same_card - printing["nonfoil"] - printing["foil"]),
                "counts": counts,
            })
        totals["missing"] = totals["cards"] - totals["owned"]
        totals["completion"] = round(totals["owned"] / totals["cards"], 4) if totals["cards"] else 0.0
        totals["by_rarity"] = dict(totals["by_rarity"])
        totals["owned_value_usd"] = round(totals["owned_value_usd"], 2)
        totals["cost_to_complete_usd"] = round(totals["cost_to_complete_usd"], 2)
        return {"set": summary, "totals": totals, "cards": cards}

    def missing_text(self, code, params):
        detail = self.detail(code, params)
        return "\n".join(f"1 {c['name']} ({code.upper()}) {c['collector_number']}"
                         + (" *F*" if params.get("finish") == "foil" else "")
                         for c in detail["cards"] if c["counts"] and not self._owned_by_filters(c, params)) + "\n"

    def _owned_by_filters(self, card_json, params):
        mode = params.get("mode", "printing")
        finish = params.get("finish", "any")
        if mode == "card":
            return card_json["owned"] + card_json["owned_elsewhere"] > 0
        return {"any": card_json["owned"], "nonfoil": card_json["owned_nonfoil"],
                "foil": card_json["owned_foil"]}.get(finish, card_json["owned"]) > 0

    def image_for(self, scryfall_id, face):
        row = self.connection.execute("SELECT image_front, image_back FROM set_cards WHERE scryfall_id = ?",
                                      (scryfall_id,)).fetchone()
        if not row:
            return None
        url = row["image_back"] if face == "back" else row["image_front"]
        if url and url.startswith("https://"):
            return url
        if face == "back" and not url:
            return None
        # The card pack stores no URLs (gallery/pack.py); Scryfall's follow a fixed pattern.
        return f"https://cards.scryfall.io/normal/{face}/{scryfall_id[0]}/{scryfall_id[1]}/{scryfall_id}.jpg"
