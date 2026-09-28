"""Binders: named places your copies live (binders, boxes, lists). docs/API.md, "Binders".

A binder holds counts of copies per printing and finish ("pools", like deck pins), not holdings
rows: an import replaces the rows, and the sorting must survive it. A pool's copies can be spread
over several binders; whatever isn't in one is "unsorted". When an import leaves fewer copies than
the binders hold, the binders keep their numbers and show the shortfall rather than forgetting them.
"""
import csv
import io
from collections import defaultdict
from datetime import datetime

KINDS = ("binder", "box", "list")
UNSORTED = "unsorted"


def pool_of(scryfall_id, finish):
    return f"{scryfall_id}|{finish or 'normal'}"


class BinderBook:
    def __init__(self, connection, collection=None):
        self.connection = connection
        self.collection = collection
        self.reload()

    # -- loading --

    def reload(self):
        connection = self.connection
        self.binders = {row["binder_id"]: dict(row) for row in connection.execute(
            "SELECT * FROM binders ORDER BY position, name COLLATE NOCASE")}
        self.contents = defaultdict(dict)                # binder_id -> {pool: quantity}
        for row in connection.execute("SELECT * FROM binder_cards"):
            if row["binder_id"] in self.binders and row["quantity"] > 0:
                self.contents[row["binder_id"]][pool_of(row["scryfall_id"], row["finish"])] = row["quantity"]
        self.mark_entries()

    def owned(self, pool):
        return sum(e.quantity for e in self._pool_entries(pool))

    def _pool_entries(self, pool):
        scryfall_id, finish = pool.split("|")
        return [e for e in self.collection.by_scryfall_id.get(scryfall_id, []) if e.finish == finish]

    def assigned(self, pool, except_binder=None):
        return sum(q.get(pool, 0) for b, q in self.contents.items() if b != except_binder)

    def mark_entries(self):
        """Give every collection entry its share of each binder (entry.binders: ((name, copies), ...)),
        spread over the pool's holdings rows the way deck copies are, so search can read it."""
        if self.collection is None:                  # the import job files binders without a collection
            return
        for entry in self.collection.entries:
            entry.binders = ()
        per_pool = defaultdict(list)
        for binder_id, pools in self.contents.items():
            for pool, quantity in pools.items():
                per_pool[pool].append((self.binders[binder_id]["name"], quantity))
        for pool, placed in per_pool.items():
            entries = self._pool_entries(pool)
            room = {id(e): e.quantity for e in entries}
            shares = defaultdict(list)
            for name, quantity in placed:
                for entry in entries:
                    take = min(quantity, room[id(entry)])
                    if take:
                        shares[id(entry)].append((name, take))
                        room[id(entry)] -= take
                        quantity -= take
                    if not quantity:
                        break
            for entry in entries:
                entry.binders = tuple(shares[id(entry)])

    # -- reading --

    def summary(self, binder_id):
        binder = self.binders[binder_id]
        pools = self.contents.get(binder_id, {})
        copies = value = short = 0
        for pool, quantity in pools.items():
            entries = self._pool_entries(pool)
            held = min(quantity, max(0, sum(e.quantity for e in entries) - self.assigned(pool, except_binder=binder_id)))
            short += quantity - held
            copies += quantity
            price = next((e.price_usd for e in entries if e.price_usd is not None), None)
            value += (price or 0) * quantity
        return {**binder, "copies": copies, "printings": len(pools), "value_usd": round(value, 2), "short": short}

    def listing(self):
        """Every binder's summary, plus the unsorted copies (in no binder)."""
        unsorted_copies = unsorted_value = 0
        for entry in self.collection.entries:
            loose = entry.quantity - sum(q for _, q in getattr(entry, "binders", ()))
            if loose > 0:
                unsorted_copies += loose
                unsorted_value += (entry.price_usd or 0) * loose
        return {"binders": [self.summary(b) for b in self.binders],
                "unsorted": {"copies": unsorted_copies, "value_usd": round(unsorted_value, 2)}}

    def of_card(self, oracle_id):
        """Where the copies of one card are: [{binder_id, name, kind, scryfall_id, finish, quantity}]."""
        pools = {pool_of(e.scryfall_id, e.finish) for e in self.collection.by_oracle_id.get(oracle_id, [])}
        return [{"binder_id": b, "name": self.binders[b]["name"], "kind": self.binders[b]["kind"],
                 "scryfall_id": pool.split("|")[0], "finish": pool.split("|")[1], "quantity": q}
                for b, contents in self.contents.items() for pool, q in contents.items() if pool in pools]

    # -- changing binders --

    def _now(self):
        return datetime.now().isoformat(timespec="seconds")

    def _check_name(self, name, except_id=None):
        name = (name or "").strip()
        if not name:
            raise ValueError("Give the binder a name")
        if name.lower() == UNSORTED:
            raise ValueError("“Unsorted” is kept for cards in no binder")
        if any(b["name"].casefold() == name.casefold() and b["binder_id"] != except_id for b in self.binders.values()):
            raise ValueError(f"There's already a binder called {name}")
        return name

    def create(self, name, kind="binder", notes=""):
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        position = 1 + max((b["position"] for b in self.binders.values()), default=0)
        binder_id = self.connection.execute(
            "INSERT INTO binders (name, kind, notes, position, created_at) VALUES (?, ?, ?, ?, ?)",
            (self._check_name(name), kind, notes or "", position, self._now())).lastrowid
        self._commit()
        return binder_id

    def update(self, binder_id, fields):
        self._binder(binder_id)
        changes = {}
        if "name" in fields:
            changes["name"] = self._check_name(fields["name"], except_id=binder_id)
        if "kind" in fields:
            if fields["kind"] not in KINDS:
                raise ValueError(f"kind must be one of {', '.join(KINDS)}")
            changes["kind"] = fields["kind"]
        if "notes" in fields:
            changes["notes"] = fields["notes"] or ""
        if changes:
            self.connection.execute(f"UPDATE binders SET {', '.join(f'{k} = ?' for k in changes)} WHERE binder_id = ?",
                                    (*changes.values(), binder_id))
            self._commit()

    def delete(self, binder_id):
        """The binder goes; its copies become unsorted (the cards themselves stay in the collection)."""
        self._binder(binder_id)
        self.connection.execute("DELETE FROM binder_cards WHERE binder_id = ?", (binder_id,))
        self.connection.execute("DELETE FROM binders WHERE binder_id = ?", (binder_id,))
        self._commit()

    def _binder(self, binder_id):
        if binder_id not in self.binders:
            raise KeyError(f"no binder {binder_id}")
        return self.binders[binder_id]

    def _commit(self):
        try:
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.reload()

    # -- moving copies --

    def put(self, binder_id, items, from_binder=None):
        """Put copies in a binder: items are {scryfall_id, finish, quantity}; quantity None means every
        copy that's free (unsorted, or in `from_binder` when moving). Returns the copies placed."""
        self._binder(binder_id)
        if from_binder is not None:
            self._binder(from_binder)
        placed = 0
        try:
            for item in items:
                pool = pool_of(item["scryfall_id"], item.get("finish"))
                if from_binder is not None:
                    free = self.contents.get(from_binder, {}).get(pool, 0)
                else:
                    free = max(0, self.owned(pool) - self.assigned(pool))
                wanted = free if item.get("quantity") is None else min(int(item["quantity"]), free)
                if wanted <= 0:
                    continue
                self._add(binder_id, pool, wanted)
                if from_binder is not None:
                    self._add(from_binder, pool, -wanted)
                placed += wanted
            self._commit()
        except Exception:
            self.connection.rollback()
            self.reload()
            raise
        return placed

    def take_out(self, binder_id, items):
        """Take copies out of a binder (they become unsorted): items as for put(); quantity None = all."""
        self._binder(binder_id)
        removed = 0
        try:
            for item in items:
                pool = pool_of(item["scryfall_id"], item.get("finish"))
                held = self.contents.get(binder_id, {}).get(pool, 0)
                wanted = held if item.get("quantity") is None else min(int(item["quantity"]), held)
                if wanted > 0:
                    self._add(binder_id, pool, -wanted)
                    removed += wanted
            self._commit()
        except Exception:
            self.connection.rollback()
            self.reload()
            raise
        return removed

    def _add(self, binder_id, pool, quantity):
        scryfall_id, finish = pool.split("|")
        current = self.contents[binder_id].get(pool, 0) + quantity
        if current > 0:
            self.connection.execute("INSERT OR REPLACE INTO binder_cards VALUES (?, ?, ?, ?)", (binder_id, scryfall_id, finish, current))
            self.contents[binder_id][pool] = current
        else:
            self.connection.execute("DELETE FROM binder_cards WHERE binder_id = ? AND scryfall_id = ? AND finish = ?",
                                    (binder_id, scryfall_id, finish))
            self.contents[binder_id].pop(pool, None)

    def items_for(self, entries):
        """put()/take_out() items for every pool among these collection entries (e.g. search results)."""
        seen = {}
        for entry in entries:
            seen.setdefault(pool_of(entry.scryfall_id, entry.finish), {"scryfall_id": entry.scryfall_id,
                                                                       "finish": entry.finish, "quantity": None})
        return list(seen.values())

    # -- ManaBox --

    def from_import(self, rows):
        """A collection export that names binders (ManaBox's Binder Name / Binder Type): each binder it
        names is created if needed and filled with exactly the copies the file puts there. Binders
        the file doesn't mention are left alone. Returns the binder names filled."""
        wanted = defaultdict(lambda: defaultdict(int))
        for row in rows:
            name = (row.get("binder") or "").strip()
            if name and name.lower() != UNSORTED and (row.get("binder_type") or "binder").lower() in ("binder", "box", ""):
                wanted[name][pool_of(row["scryfall_id"], row.get("finish"))] += int(row.get("quantity") or 0)
        if not wanted:
            return []
        by_name = {b["name"].casefold(): b["binder_id"] for b in self.binders.values()}
        try:
            for name, pools in wanted.items():
                binder_id = by_name.get(name.casefold())
                if binder_id is None:
                    position = 1 + max((b["position"] for b in self.binders.values()), default=0)
                    binder_id = self.connection.execute(
                        "INSERT INTO binders (name, kind, notes, position, created_at) VALUES (?, 'binder', '', ?, ?)",
                        (name, position, self._now())).lastrowid
                    self.binders[binder_id] = {"binder_id": binder_id, "name": name, "kind": "binder", "notes": "",
                                               "position": position}
                self.connection.execute("DELETE FROM binder_cards WHERE binder_id = ?", (binder_id,))
                self.contents[binder_id] = {}
                for pool, quantity in pools.items():
                    self._add(binder_id, pool, quantity)
            self._commit()
        except Exception:
            self.connection.rollback()
            self.reload()
            raise
        return sorted(wanted)

    def export_csv(self, binder_id):
        """The binder as a CSV ManaBox (and most apps) can import."""
        binder = self._binder(binder_id)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["Binder Name", "Name", "Set code", "Collector number", "Foil", "Quantity", "Scryfall ID"])
        for pool, quantity in sorted(self.contents.get(binder_id, {}).items()):
            scryfall_id, finish = pool.split("|")
            entries = self._pool_entries(pool)
            card = entries[0] if entries else None
            writer.writerow([binder["name"], card.name if card else "", card.set_code.upper() if card else "",
                             card.collector_number if card else "", {"foil": "foil", "etched": "etched"}.get(finish, "normal"),
                             quantity, scryfall_id])
        return out.getvalue()
