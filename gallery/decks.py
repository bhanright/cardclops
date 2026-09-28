"""Decks you have built, matched against the collection (docs/DECKS.md).

Three parts:
- parsing: decklist text in the shapes MTGO, Arena, Moxfield, Archidekt and
  ManaBox export, into lines with a section, quantity, name and (when the list
  says) a printing and finish;
- resolving: each line to a card (oracle id) and a printing to show;
- allocation: which copies in the collection each deck uses. Active decks
  claim copies in priority order; a copy is never counted in two active decks.
"""
import csv
import functools
import io
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import datetime

from . import redux, scryfall, versions
from .collection import build_entry
from .deckstats import can_be_commander, commanders_pair, has_partner_ability

SECTIONS = ("commander", "companion", "main", "sideboard", "maybeboard")
USES_COPIES = ("commander", "companion", "main", "sideboard")      # maybeboard reserves nothing
FINISHES = ("normal", "foil", "etched")
CATEGORY_ORDER = ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land")

SECTION_HEADERS = {
    "commander": "commander", "commanders": "commander", "command zone": "commander",
    "companion": "companion", "companions": "companion",
    "deck": "main", "main": "main", "mainboard": "main", "maindeck": "main", "main deck": "main",
    "sideboard": "sideboard", "side": "sideboard", "side board": "sideboard",
    "maybeboard": "maybeboard", "maybe": "maybeboard", "considering": "maybeboard", "wishlist": "maybeboard",
}
# Archidekt categories that are really sections.
CATEGORY_SECTIONS = {"commander": "commander", "companion": "companion", "sideboard": "sideboard",
                     "maybeboard": "maybeboard", "maybe": "maybeboard", "considering": "maybeboard"}

LINE = re.compile(r"^\s*(?:(\d+)\s*[xX]?\s+)?(.+?)\s*$")
SET_AND_NUMBER = re.compile(r"\(([A-Za-z0-9]{2,6})\)(?:\s+([^\s\[\]*#^()]+))?")
FINISH_MARKER = re.compile(r"\*([A-Za-z])\*")
ROLE_MARKER = re.compile(r"\*(CMDR|CMPN)\*", re.I)          # TappedOut: commander / companion
ARCHIDEKT_CATEGORIES = re.compile(r"\[([^\]]*)\]")
TAG_MARKERS = re.compile(r"(\^[^^]*\^|\s#\S+)")
MTGO_XML_CARD = re.compile(r'<Cards\b[^>]*?Quantity="(\d+)"[^>]*?Sideboard="(true|false)"[^>]*?Name="([^"]+)"', re.I)


@dataclass
class ParsedLine:
    section: str
    quantity: int
    name: str
    set_code: str | None = None
    number: str | None = None
    finish: str | None = None
    raw: str = ""
    scryfall_id: str | None = None        # when the source names the exact printing (Archidekt does)


def fold(name):
    """Name key for matching: lower case, accents dropped, slashes normalized."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\s*/{1,3}\s*", " // ", name.strip().lower())
    return re.sub(r"\s+", " ", name)


def _header(line):
    word = line.strip().strip("/").strip().rstrip(":").strip().lower()
    word = re.sub(r"\s*\(\d+\)$", "", word)          # "Sideboard (15)"
    return SECTION_HEADERS.get(word)


def _parse_line(text, section):
    raw = text
    match = LINE.match(text)
    if not match:
        return None
    quantity = int(match.group(1) or 1)
    rest = match.group(2)

    for role in ROLE_MARKER.findall(rest):
        section = "commander" if role.upper() == "CMDR" else "companion"
    rest = ROLE_MARKER.sub(" ", rest)
    finish = None
    for marker in FINISH_MARKER.findall(rest):
        finish = {"f": "foil", "e": "etched"}.get(marker.lower(), finish)
    rest = FINISH_MARKER.sub(" ", rest)

    for categories in ARCHIDEKT_CATEGORIES.findall(rest):
        for category in categories.split(","):
            flags = re.findall(r"\{(\w+)\}", category)
            label = re.sub(r"\{\w+\}", "", category).strip().lower()
            if label in CATEGORY_SECTIONS:
                section = CATEGORY_SECTIONS[label]
            elif "noDeck" in flags:
                section = "maybeboard"
    rest = ARCHIDEKT_CATEGORIES.sub(" ", rest)
    rest = TAG_MARKERS.sub(" ", rest)

    set_code = number = None
    printing = SET_AND_NUMBER.search(rest)
    if printing:
        set_code, number = printing.group(1).lower(), printing.group(2)
        rest = rest[:printing.start()]
    name = re.sub(r"\s+", " ", rest).strip()
    if not name:
        return None
    return ParsedLine(section, quantity, name, set_code, number, finish, raw)


def _parse_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    keys = {k.lower().strip(): k for k in rows[0]}

    def column(*names):
        return next((keys[n] for n in names if n in keys), None)

    name_key = column("name", "card name", "card")
    quantity_key = column("quantity", "count", "qty", "amount")
    set_key = column("set code", "set", "edition", "edition code")
    number_key = column("collector number", "collector_number", "number", "cn")
    foil_key = column("foil", "finish", "printing")
    board_key = column("board", "section", "category", "categories")
    lines = []
    for row in rows:
        name = (row.get(name_key) or "").strip()
        if not name:
            continue
        finish = (row.get(foil_key) or "").strip().lower() if foil_key else ""
        finish = "foil" if finish in ("foil", "true", "yes", "1") else "etched" if finish == "etched" else None
        board = (row.get(board_key) or "").strip().lower() if board_key else ""
        section = SECTION_HEADERS.get(board) or CATEGORY_SECTIONS.get(board) or "main"
        set_code = (row.get(set_key) or "").strip().lower() if set_key else ""
        lines.append(ParsedLine(section, int(float(row.get(quantity_key) or 1)) if quantity_key else 1, name,
                                set_code if 2 <= len(set_code) <= 6 else None,
                                (row.get(number_key) or "").strip() or None if number_key else None, finish, name))
    return lines


def parse_decklist(text):
    """Text of a decklist -> (lines, suggested deck name or None)."""
    text = text.replace("\r\n", "\n").replace("﻿", "")
    if "<Cards" in text and "Quantity=" in text:                   # MTGO .dek XML
        lines = [ParsedLine("sideboard" if side.lower() == "true" else "main", int(qty), name, raw=name)
                 for qty, side, name in MTGO_XML_CARD.findall(text)]
        _commander_at_the_end(lines, [[l for l in lines if l.section == "main"],
                                      [l for l in lines if l.section == "sideboard"]])
        return lines, None
    first = text.lstrip().split("\n", 1)[0].lower()
    if "," in first and ("name" in first) and any(k in first for k in ("quantity", "count", "qty")):
        return _parse_csv(text), None

    suggested_name = None
    groups = [[]]                  # blank-line separated groups, for the MTGO sideboard convention
    saw_header = False
    section = "main"
    in_about = False
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            if groups[-1]:
                groups.append([])
            in_about = False
            continue
        if line.lower() in ("about", "about:"):
            in_about = True
            continue
        if in_about:
            if line.lower().startswith("name "):
                suggested_name = line[5:].strip()
            continue
        header = _header(line)
        if header:
            section, saw_header = header, True
            if groups[-1]:
                groups.append([])
            continue
        if line.startswith("//") or line.startswith("#"):
            continue
        parsed = _parse_line(line, section)
        if parsed:
            groups[-1].append(parsed)
    groups = [g for g in groups if g]

    # No headers but a blank line: MTGO puts the sideboard after it.
    if not saw_header and len(groups) >= 2:
        for group in groups[1:]:
            for line in group:
                if line.section == "main":
                    line.section = "sideboard"
    lines = [line for group in groups for line in group]
    _commander_at_the_end(lines, groups)
    return lines, suggested_name


def _commander_at_the_end(lines, groups):
    """MTGO and plain-text Commander exports list the commander(s) last, after a blank line
    and often under the sideboard: one or two cards that bring the deck to exactly 100."""
    if any(line.section == "commander" for line in lines) or len(groups) < 2:
        return
    tail = groups[-1]
    tail_count = sum(line.quantity for line in tail)
    main_count = sum(line.quantity for line in lines if line.section == "main" and line not in tail)
    if tail_count <= 2 and main_count + tail_count == 100 and all(line.section in ("main", "sideboard") for line in tail):
        for line in tail:
            line.section = "commander"


# ---- resolving -------------------------------------------------------------------------------

# Card types a deck can play. Oddities have none of them: Jumpstart front cards and art cards
# ("Card"), tokens and emblems (their type line starts with the word).
GAME_TYPES = {"Artifact", "Battle", "Creature", "Enchantment", "Instant", "Kindred", "Land",
              "Planeswalker", "Sorcery", "Tribal", "Conspiracy"}


def is_game_card(type_line):
    words = (type_line or "").split(" // ")[0].split("—")[0].split()
    return bool(words) and words[0] not in ("Token", "Emblem") and bool(GAME_TYPES & set(words))

@dataclass
class Resolver:
    """Turns parsed lines into cards, using every card Scryfall knows (not just the ones you own)."""
    connection: object
    collection: object
    by_name: dict = field(default_factory=dict)       # folded name -> oracle_id
    representative: dict = field(default_factory=dict)  # oracle_id -> (scryfall_id, name, cheapest usd)

    def __post_init__(self):
        rows = self.connection.execute("SELECT oracle_id, name, type_line, scryfall_id, cheapest_usd FROM oracle_cards").fetchall()
        # A name can belong to a game card and to an oddity sharing it (Pym Particles, and the
        # Jumpstart front card named after its theme); the game card wins the name.
        for row in sorted(rows, key=lambda row: not is_game_card(row["type_line"])):
            self.representative[row["oracle_id"]] = (row["scryfall_id"], row["name"], row["cheapest_usd"])
            self.by_name.setdefault(fold(row["name"]), row["oracle_id"])
            if " // " in row["name"]:
                for face in row["name"].split(" // "):
                    self.by_name.setdefault(fold(face), row["oracle_id"])
        for entry in self.collection.entries:          # tokens and oddities missing from oracle_cards
            self.by_name.setdefault(fold(entry.name), entry.oracle_id)
            self.representative.setdefault(entry.oracle_id, (entry.scryfall_id, entry.name, entry.price_usd))

    def resolve(self, line):
        """(oracle_id, scryfall_id, canonical name, warning or None)."""
        printing = None
        if line.scryfall_id:
            printing = self.connection.execute(
                "SELECT scryfall_id, oracle_id, name FROM printings WHERE scryfall_id = ?", (line.scryfall_id,)).fetchone()
        if printing is None and line.set_code and line.number:
            printing = self.connection.execute(
                "SELECT scryfall_id, oracle_id, name FROM printings WHERE set_code = ? AND collector_number = ?",
                (line.set_code, line.number)).fetchone()
        if line.scryfall_id and printing:                        # an exact id beats any name match
            return printing["oracle_id"], printing["scryfall_id"], printing["name"], None
        name_key = fold(re.sub(r"^A-", "", line.name))          # Arena's rebalanced "A-" cards
        oracle_id = self.by_name.get(name_key) or self.by_name.get(fold(line.name))
        printed_names = {fold(printing["name"]), *(fold(face) for face in printing["name"].split(" // "))} if printing else set()
        if printing and (oracle_id is None or printing["oracle_id"] == oracle_id
                         or name_key in printed_names or fold(line.name) in printed_names):
            return printing["oracle_id"], printing["scryfall_id"], printing["name"], None
        if oracle_id is None:
            return None, None, line.name, f"No card named “{line.name}”"
        # Show a copy you own, the most valuable one, else Scryfall's representative printing.
        owned = self.collection.by_oracle_id.get(oracle_id)
        scryfall_id = (max(owned, key=lambda e: e.price_usd or 0).scryfall_id if owned
                       else self.representative[oracle_id][0])
        warning = (f"{line.set_code.upper()} #{line.number} is not a printing of {self.representative[oracle_id][1]}"
                   if printing else None)
        return oracle_id, scryfall_id, self.representative[oracle_id][1], warning


# ---- allocation ------------------------------------------------------------------------------

COPY_POLICIES = ("default", "budget", "bling")


def treatments(card):
    """How special a printing looks: borderless, full art, showcase, extended art, etched frame."""
    effects = set(card.get("frame_effects") or [])
    return sum((card.get("border_color") == "borderless", bool(card.get("full_art")),
                "showcase" in effects, "extendedart" in effects, "etched" in effects,
                bool(card.get("promo_types")) and "serialized" in card.get("promo_types", [])))


def pool_key(scryfall_id, finish):
    return f"{scryfall_id}|{finish}"


@dataclass
class LineState:
    """A deck line plus what the allocator gave it."""
    row: dict
    allocations: list = field(default_factory=list)   # [pool, quantity, pinned]
    missing: int = 0
    missing_reason: str | None = None
    used_elsewhere: list = field(default_factory=list)

    @property
    def owned(self):
        return sum(quantity for _, quantity, _ in self.allocations)


def all_or_nothing(method):
    """A deck write either completes or leaves the database as it was. Without the rollback, a write
    that fails halfway (a Scryfall fetch after the old lines are deleted) stays pending on the shared
    connection, and the next unrelated save would commit it."""
    @functools.wraps(method)
    def write(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            self.connection.rollback()
            self.reload()
            raise
    return write


class DeckBook:
    def __init__(self, connection, collection, resolver=None):
        self.connection = connection
        self.collection = collection
        self.resolver = resolver or Resolver(connection, collection)
        self._repair_oddity_lines()
        self.reload()

    def _repair_oddity_lines(self):
        """Lines an older import matched to a card no deck can play (a Jumpstart front card sharing
        a sorcery's name) are matched again, now that the game card wins the name."""
        connection = self.connection
        rows = connection.execute(
            "SELECT l.line_id, l.name, l.requested_set, l.requested_number, l.oracle_id, o.type_line "
            "FROM deck_lines l JOIN oracle_cards o ON o.oracle_id = l.oracle_id").fetchall()
        fixed = False
        for row in rows:
            if is_game_card(row["type_line"]):
                continue
            line = SimpleNamespace(name=row["name"], scryfall_id=None, set_code=row["requested_set"],
                                   number=row["requested_number"])
            oracle_id, scryfall_id, name, _ = self.resolver.resolve(line)
            found = connection.execute("SELECT type_line FROM oracle_cards WHERE oracle_id = ?", (oracle_id,)).fetchone()
            if oracle_id and oracle_id != row["oracle_id"] and found and is_game_card(found["type_line"]):
                connection.execute("UPDATE deck_lines SET oracle_id = ?, scryfall_id = ?, name = ? WHERE line_id = ?",
                                   (oracle_id, scryfall_id, name, row["line_id"]))
                fixed = True
        if fixed:
            connection.commit()

    # -- loading and allocating --

    def reload(self):
        connection = self.connection
        self.decks = {row["deck_id"]: dict(row) for row in connection.execute("SELECT * FROM decks")}
        self.lines = defaultdict(list)                  # deck_id -> [LineState]
        self.line_by_id = {}
        for row in connection.execute("SELECT * FROM deck_lines ORDER BY deck_id, position"):
            state = LineState(dict(row))
            self.lines[row["deck_id"]].append(state)
            self.line_by_id[row["line_id"]] = state
        self.pins = defaultdict(dict)                   # line_id -> {pool: quantity}
        for row in connection.execute("SELECT * FROM deck_pins"):
            self.pins[row["line_id"]][row["pool"]] = row["quantity"]
        self.card_cache = {}
        self.stats_cache = {}
        self.allocate()

    def card_data(self, scryfall_id):
        """Scryfall card dict for any printing a deck names; owned or not."""
        if scryfall_id in self.card_cache:
            return self.card_cache[scryfall_id]
        entries = self.collection.by_scryfall_id.get(scryfall_id)
        if entries:
            card = entries[0].card
        else:
            row = self.connection.execute("SELECT raw FROM cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
            card = redux.annotate(json.loads(row["raw"])) if row else None
        self.card_cache[scryfall_id] = card
        return card

    def ordered_decks(self, status=None):
        decks = [d for d in self.decks.values() if status is None or d["status"] == status]
        return sorted(decks, key=lambda d: (d["priority"], d["created_at"], d["deck_id"]))

    def allocate(self):
        collection = self.collection
        pools = defaultdict(list)                        # pool -> [Entry]
        for entry in collection.entries:
            entry.used = 0
            entry.decks = ()
            pools[pool_key(entry.scryfall_id, entry.finish)].append(entry)
        self.pools = pools
        self.pool_total = {key: sum(e.quantity for e in entries) for key, entries in pools.items()}
        self.pools_by_oracle = defaultdict(list)
        for key, entries in pools.items():
            self.pools_by_oracle[entries[0].oracle_id].append(key)
        free = dict(self.pool_total)
        self.taken_by = defaultdict(lambda: defaultdict(int))   # pool -> deck_id -> copies (active decks)

        def take(state, pool, wanted, pinned, ledger):
            got = min(wanted, ledger.get(pool, 0))
            if got > 0:
                ledger[pool] -= got
                state.allocations.append([pool, got, pinned])
            return got

        for states in self.lines.values():
            for state in states:
                state.allocations, state.missing, state.missing_reason, state.used_elsewhere = [], 0, None, []

        active = self.ordered_decks("active")
        # Pins first, across every active deck, so a chosen copy is never taken by auto-allocation.
        for deck in active:
            for state in self.lines[deck["deck_id"]]:
                if state.row["section"] in USES_COPIES:
                    for pool, quantity in self.pins.get(state.row["line_id"], {}).items():
                        take(state, pool, min(quantity, state.row["quantity"] - state.owned), True, free)
        for deck in active:
            for state in self._in_section_order(deck["deck_id"]):
                self._auto_allocate(state, free, take)
                for pool, quantity, _ in state.allocations:
                    self.taken_by[pool][deck["deck_id"]] += quantity
        # Inactive decks see what is left, without reserving it.
        for deck in self.ordered_decks("inactive"):
            ledger = dict(free)
            for state in self._in_section_order(deck["deck_id"]):
                for pool, quantity in self.pins.get(state.row["line_id"], {}).items():
                    take(state, pool, min(quantity, state.row["quantity"] - state.owned), True, ledger)
                self._auto_allocate(state, ledger, take)

        # Why is each short line short?
        for deck in self.decks.values():
            for state in self.lines[deck["deck_id"]]:
                if state.row["section"] not in USES_COPIES or state.row["oracle_id"] is None:
                    state.missing = state.row["quantity"] - state.owned if state.row["section"] in USES_COPIES else 0
                    state.missing_reason = "not_owned" if state.missing else None
                    continue
                state.missing = state.row["quantity"] - state.owned
                others = defaultdict(int)
                for pool in self.pools_by_oracle.get(state.row["oracle_id"], []):
                    for deck_id, quantity in self.taken_by[pool].items():
                        if deck_id != deck["deck_id"]:
                            others[deck_id] += quantity
                state.used_elsewhere = [{"deck_id": d, "name": self.decks[d]["name"], "quantity": q}
                                        for d, q in others.items()]
                if state.missing:
                    state.missing_reason = "used_elsewhere" if others else "not_owned"

        # Spread each pool's reserved copies over its holdings rows, and note deck membership.
        for pool, by_deck in self.taken_by.items():
            remaining = sum(by_deck.values())
            for entry in pools.get(pool, []):
                entry.used = min(entry.quantity, remaining)
                remaining -= entry.used
        membership = defaultdict(list)
        for deck in self.ordered_decks():
            # A maybeboard card is a candidate, not a member, so it doesn't make a card "in a deck".
            for oracle_id in {s.row["oracle_id"] for s in self.lines[deck["deck_id"]]
                              if s.row["oracle_id"] and s.row["section"] in USES_COPIES}:
                membership[oracle_id].append((deck["name"], deck["status"]))
        for oracle_id, decks in membership.items():
            for entry in collection.by_oracle_id.get(oracle_id, []):
                entry.decks = tuple(decks)
        self.stats_cache = {}

    def _in_section_order(self, deck_id):
        order = {section: i for i, section in enumerate(SECTIONS)}
        return sorted(self.lines[deck_id], key=lambda s: (order.get(s.row["section"], 9), s.row["position"]))

    def _auto_allocate(self, state, ledger, take):
        row = state.row
        if row["section"] not in USES_COPIES or row["oracle_id"] is None:
            return
        need = row["quantity"] - state.owned
        if need <= 0:
            return

        # Basic lands go the other way: nobody sleeves their $26 foil Mountain first.
        basic = "Basic Land" in (self._type_line(state) or "")
        policy = self.decks[row["deck_id"]].get("copy_policy") or "default"

        def preference(pool):
            scryfall_id, finish = pool.split("|")
            entry = self.pools[pool][0]
            price = entry.price_usd or 0
            if policy == "budget":
                taste = (-price,)
            elif policy == "bling":
                taste = (finish in ("foil", "etched"), treatments(entry.card), price)
            else:
                taste = (-price if basic else price,)
            if policy != "default":
                # Choosing budget or bling overrides the printing a list names: deck sites
                # record a printing whether or not it is the copy you sleeved.
                return taste
            return (scryfall_id == row["scryfall_id"] and finish == row["requested_finish"],
                    scryfall_id == row["scryfall_id"] and row["requested_set"] is not None,
                    row["requested_finish"] is not None and finish == row["requested_finish"],
                    *taste)

        for pool in sorted(self.pools_by_oracle.get(row["oracle_id"], []), key=preference, reverse=True):
            need -= take(state, pool, need, False, ledger)
            if need <= 0:
                break

    # -- shapes for the API --

    def pool_info(self, pool):
        entry = self.pools[pool][0]
        return {"pool": pool, "scryfall_id": entry.scryfall_id, "set_code": entry.set_code,
                "collector_number": entry.collector_number, "finish": entry.finish, "price_usd": entry.price_usd}

    def line_entry(self, state, quantity=None):
        """An Entry-like view of the printing a line shows, owned or not, for CardSummary and stats."""
        row = state.row
        scryfall_id = state.allocations[0][0].split("|")[0] if state.allocations else row["scryfall_id"]
        finish = state.allocations[0][0].split("|")[1] if state.allocations else (row["requested_finish"] or "normal")
        owned = self.collection.by_scryfall_id.get(scryfall_id)
        if owned:
            match = next((e for e in owned if e.finish == finish), owned[0])
            return match
        card = self.card_data(scryfall_id) if scryfall_id else None
        if card is None:
            return None
        holding = {"row_id": None, "scryfall_id": scryfall_id, "finish": finish, "quantity": quantity or row["quantity"],
                   "condition": "", "language": card.get("lang", "en"), "purchase_price": None, "added_at": "",
                   "misprint": 0}
        tags = {}
        oracle_id = row["oracle_id"]
        if oracle_id:
            tag_rows = self.connection.execute("SELECT slug FROM oracle_taggings WHERE oracle_id = ?", (oracle_id,))
            tags = {oracle_id: frozenset(r[0] for r in tag_rows)}
        entry = build_entry(holding, card, tags)
        if entry.price_usd is None:
            entry.price_usd = (self.resolver.representative.get(oracle_id) or (None, None, None))[2]
        return entry

    def cheapest(self, oracle_id):
        return (self.resolver.representative.get(oracle_id) or (None, None, None))[2]

    def summary(self, deck_id, stats=None):
        deck = self.decks[deck_id]
        states = self.lines[deck_id]
        sections = {s: 0 for s in SECTIONS}
        owned = missing = missing_not_owned = missing_elsewhere = 0
        value = cost = 0.0
        for state in states:
            row = state.row
            sections[row["section"]] += row["quantity"]
            if row["section"] not in USES_COPIES:
                continue
            owned += state.owned
            missing += state.missing
            if state.missing_reason == "used_elsewhere":
                missing_elsewhere += state.missing
            elif state.missing:
                missing_not_owned += state.missing
            for pool, quantity, _ in state.allocations:
                value += (self.pools[pool][0].price_usd or 0) * quantity
            if state.missing:
                cost += state.missing * (self.cheapest(row["oracle_id"]) or 0)
        commanders = []
        for state in states:
            if state.row["section"] == "commander" and state.row["oracle_id"]:
                entry = self.line_entry(state)
                sid = entry.scryfall_id if entry else state.row["scryfall_id"]
                commanders.append({"oracle_id": state.row["oracle_id"], "name": state.row["name"], "scryfall_id": sid,
                                   "image": f"/img/{sid}/front/normal", "art": f"/img/{sid}/front/art_crop"})
        identity = set()
        pool_for_identity = [s for s in states if s.row["section"] == "commander"] or \
                            [s for s in states if s.row["section"] in ("main", "companion")]
        for state in pool_for_identity:
            card = self.card_data(state.row["scryfall_id"]) if state.row["scryfall_id"] else None
            identity |= set((card or {}).get("color_identity", []))
        if commanders:
            cover = commanders[0]["art"]
        else:
            priced = [(self.cheapest(s.row["oracle_id"]) or 0, s.row["scryfall_id"]) for s in states
                      if s.row["scryfall_id"] and s.row["section"] == "main"]
            cover = f"/img/{max(priced)[1]}/front/art_crop" if priced else None
        stats = stats or self.stats(deck_id)
        return {
            "deck_id": deck_id, "name": deck["name"], "format": deck["format"], "status": deck["status"],
            "priority": deck["priority"], "copy_policy": deck.get("copy_policy") or "default",
            "commanders": commanders,
            "color_identity": [c for c in "WUBRG" if c in identity],
            "card_count": sections["commander"] + sections["companion"] + sections["main"],
            "sections": sections, "owned": owned, "missing": missing,
            "missing_not_owned": missing_not_owned, "missing_used_elsewhere": missing_elsewhere,
            "cost_to_complete_usd": round(cost, 2), "value_usd": round(value, 2),
            "legal": (stats or {}).get("legality", {}).get("legal"),
            "source": deck["source"], "source_url": deck["source_url"],
            "created_at": deck["created_at"], "updated_at": deck["updated_at"], "cover": cover,
        }

    def stats(self, deck_id):
        if deck_id in self.stats_cache:
            return self.stats_cache[deck_id]
        from .deckstats import compute_deck_stats
        result = compute_deck_stats(self.deck_cards(deck_id), self.decks[deck_id]["format"],
                                    self.collection.tag_index)
        self.stats_cache[deck_id] = result
        return result

    def deck_cards(self, deck_id):
        """The deck as deckstats DeckCards, one per resolved line, each knowing its line_id."""
        from .deckstats import DeckCard
        cards = []
        for state in self.lines[deck_id]:
            row = state.row
            if row["oracle_id"] is None:
                continue
            entry = self.line_entry(state)
            if entry is None:
                continue
            price = None
            if state.allocations:
                copies = [(self.pools[p][0].price_usd or 0, q) for p, q, _ in state.allocations]
                price = sum(pr * q for pr, q in copies) / sum(q for _, q in copies)
            elif self.cheapest(row["oracle_id"]) is not None:
                price = self.cheapest(row["oracle_id"])
            cards.append(DeckCard(section=row["section"], quantity=row["quantity"], card=entry.card,
                                  oracle_id=row["oracle_id"], tags=entry.tags, price_usd=price,
                                  line_id=row["line_id"]))
        return cards

    def spare_lands(self):
        """Every land pool with copies no active deck holds, for the mana-base fixer."""
        from .manafix import SpareLand
        free = self._free_counts()
        lands = []
        for pool, entries in self.pools.items():
            lead = entries[0]
            if free.get(pool, 0) > 0 and "Land" in lead.type_line.split(" // ")[0]:
                lands.append(SpareLand(pool=pool, card=lead.card, oracle_id=lead.oracle_id, tags=lead.tags,
                                       spare=free[pool], price_usd=lead.price_usd))
        return lands

    @all_or_nothing
    def apply_swaps(self, deck_id, swaps):
        """Each swap: one copy off a line (deleting it at zero), and a main-deck line pinned to a spare pool."""
        connection = self.connection
        position = connection.execute("SELECT COALESCE(MAX(position), 0) FROM deck_lines WHERE deck_id = ?",
                                       (deck_id,)).fetchone()[0]
        for swap in swaps:
            line_id, pool = int(swap["remove_line_id"]), swap["add_pool"]
            state = self.line_by_id.get(line_id)
            if state is None or state.row["deck_id"] != deck_id:
                raise ValueError(f"line {line_id} is not in this deck")
            if pool not in self.pools:
                raise ValueError("You don't own that printing in that finish")
            if state.row["quantity"] > 1:
                connection.execute("UPDATE deck_lines SET quantity = quantity - 1 WHERE line_id = ?", (line_id,))
                state.row["quantity"] -= 1
            else:
                connection.execute("DELETE FROM deck_lines WHERE line_id = ?", (line_id,))
            entry = self.pools[pool][0]
            existing = connection.execute(
                "SELECT line_id FROM deck_lines WHERE deck_id = ? AND section = 'main' AND oracle_id = ?",
                (deck_id, entry.oracle_id)).fetchone()
            if existing:                  # a second basic of the same kind joins its line
                connection.execute("UPDATE deck_lines SET quantity = quantity + 1 WHERE line_id = ?", (existing[0],))
                added = existing[0]
            else:
                position += 1
                added = connection.execute(
                    "INSERT INTO deck_lines (deck_id, position, section, quantity, name, oracle_id, scryfall_id, "
                    "requested_set, requested_number, requested_finish) VALUES (?, ?, 'main', 1, ?, ?, ?, ?, ?, ?)",
                    (deck_id, position, entry.name, entry.oracle_id, entry.scryfall_id, entry.set_code,
                     entry.collector_number, entry.finish)).lastrowid
            connection.execute(
                "INSERT INTO deck_pins VALUES (?, ?, 1) ON CONFLICT(line_id, pool) DO UPDATE SET quantity = quantity + 1",
                (added, pool))
        connection.execute("UPDATE decks SET updated_at = ? WHERE deck_id = ?", (self._now(), deck_id))
        connection.commit()
        self.reload()
        versions.record(self, deck_id, "manafix")

    def line_json(self, state, summarize):
        row = state.row
        entry = self.line_entry(state)
        summary = summarize(entry, quantity=row["quantity"]) if entry else None
        category = "Other"
        if entry:
            front = entry.type_line.split(" // ")[0]
            category = next((c for c in CATEGORY_ORDER if c in front), "Other")
        held_pools = self.pools_by_oracle.get(row["oracle_id"], []) if row["oracle_id"] else []
        allocated = {pool for pool, _, _ in state.allocations}
        free_now = self._free_counts()
        return {
            "line_id": row["line_id"], "section": row["section"], "quantity": row["quantity"], "name": row["name"],
            "oracle_id": row["oracle_id"],
            "requested": ({"set_code": row["requested_set"], "collector_number": row["requested_number"],
                           "finish": row["requested_finish"]}
                          if row["requested_set"] or row["requested_finish"] else None),
            "card": summary, "category": category, "tags": sorted(entry.tags) if entry else [],
            "can_be_commander": bool(entry) and can_be_commander(entry.card, self.decks[row["deck_id"]]["format"]),
            "pairs_with_commander": self._pairs_with_commander(state, entry),
            "allocations": [{**self.pool_info(pool), "quantity": quantity, "pinned": pinned}
                            for pool, quantity, pinned in state.allocations],
            "owned": state.owned, "missing": state.missing, "missing_reason": state.missing_reason,
            "used_elsewhere": state.used_elsewhere,
            "alternatives": [{**self.pool_info(pool), "free": free_now.get(pool, 0)}
                             for pool in held_pools if pool not in allocated],
        }

    def _pairs_with_commander(self, state, entry):
        """Whether a main-deck card could join the deck's one commander as its partner (or Background)."""
        if state.row["section"] != "main" or not entry:
            return False
        leaders = [st for st in self.lines[state.row["deck_id"]] if st.row["section"] == "commander"]
        leader = self.line_entry(leaders[0]) if len(leaders) == 1 else None
        fmt = self.decks[state.row["deck_id"]]["format"]
        return (bool(leader) and has_partner_ability(leader.card) and can_be_commander(entry.card, fmt)
                and commanders_pair(leader.card, entry.card))

    def _free_counts(self):
        if not hasattr(self, "_free_cache") or self._free_cache[0] is not self.taken_by:
            free = {pool: total - sum(self.taken_by[pool].values()) for pool, total in self.pool_total.items()}
            self._free_cache = (self.taken_by, free)
        return self._free_cache[1]

    def detail(self, deck_id, summarize):
        stats = self.stats(deck_id)
        order = {section: i for i, section in enumerate(SECTIONS)}
        lines = sorted(self.lines[deck_id], key=lambda s: (order[s.row["section"]], s.row["position"]))
        return {"deck": {**self.summary(deck_id, stats), "notes": self.decks[deck_id]["notes"]},
                "lines": [self.line_json(state, summarize) for state in lines],
                "stats": stats}

    def memberships(self, oracle_id):
        """Every deck whose list includes this card, with the copies each uses."""
        result = []
        for deck in self.ordered_decks():
            for state in self.lines[deck["deck_id"]]:
                if state.row["oracle_id"] == oracle_id:
                    result.append({
                        "deck_id": deck["deck_id"], "name": deck["name"], "status": deck["status"],
                        "section": state.row["section"], "quantity": state.row["quantity"],
                        "copies": [{k: v for k, v in self.pool_info(pool).items()
                                    if k in ("set_code", "collector_number", "finish")} | {"quantity": quantity}
                                   for pool, quantity, _ in state.allocations],
                    })
        return result

    def totals(self):
        active = self.ordered_decks("active")
        copies = sum(sum(by_deck.values()) for by_deck in self.taken_by.values())
        value = sum((self.pools[pool][0].price_usd or 0) * sum(by_deck.values())
                    for pool, by_deck in self.taken_by.items() if pool in self.pools)
        conflicts = sum(1 for deck in active for s in self.lines[deck["deck_id"]]
                        if s.missing_reason == "used_elsewhere")
        return {"active": len(active), "inactive": len(self.decks) - len(active),
                "copies_in_decks": copies, "value_in_decks_usd": round(value, 2), "conflicts": conflicts}

    def used_quantities(self, deck_id):
        """{(scryfall_id, finish): copies} the deck uses, for its value history."""
        result = defaultdict(int)
        for state in self.lines[deck_id]:
            for pool, quantity, _ in state.allocations:
                scryfall_id, finish = pool.split("|")
                result[(scryfall_id, finish)] += quantity
        return result

    def suggestions(self, deck_id, similarity, summarize, limit=24):
        deck = self.decks[deck_id]
        summary = self.summary(deck_id)
        identity = set(summary["color_identity"])
        in_deck = {s.row["oracle_id"] for s in self.lines[deck_id] if s.row["oracle_id"]}
        fmt = deck["format"]
        scores = defaultdict(float)
        because = defaultdict(list)
        # Lands are left out on both sides: a deck's lands all resemble each other,
        # so they would crowd every suggestion list with more lands.
        for state in self.lines[deck_id]:
            if state.row["section"] not in ("main", "commander") or not state.row["oracle_id"]:
                continue
            if "Land" in (self._type_line(state) or "").split(" // ")[0]:
                continue
            for other, score in similarity.similar(state.row["oracle_id"], limit=25):
                if other in in_deck:
                    continue
                scores[other] += score
                because[other].append((score, state.row["name"]))
        results = []
        for oracle_id, score in sorted(scores.items(), key=lambda kv: -kv[1]):
            entries = self.collection.by_oracle_id.get(oracle_id, [])
            spare = sum(e.spare for e in entries)
            if not entries or spare <= 0:
                continue
            lead = max(entries, key=lambda e: e.price_usd or 0)
            if "Land" in lead.type_line.split(" // ")[0]:
                continue
            if identity and not lead.color_identity <= identity:
                continue
            if fmt in lead.legalities and lead.legalities[fmt] not in ("legal", "restricted"):
                continue
            item = summarize(lead, quantity=sum(e.quantity for e in entries))
            item.update(score=round(score, 3), spare=spare,
                        because=[name for _, name in sorted(because[oracle_id], reverse=True)[:3]])
            results.append(item)
            if len(results) >= limit:
                break
        return {"cards": results}

    # -- changes --

    def _now(self):
        return datetime.now().isoformat(timespec="seconds")

    def _store_lines(self, deck_id, parsed, warnings, deck_name):
        """Resolve and insert lines; returns printings whose card data we do not have yet."""
        connection = self.connection
        unknown_printings = set()
        position = 0
        merged = {}
        for line in parsed:
            oracle_id, scryfall_id, name, warning = self.resolver.resolve(line)
            if warning:
                warnings.append({"deck": deck_name, "line": line.raw or line.name, "message": warning})
            key = (line.section, oracle_id or fold(line.name), scryfall_id if line.set_code else None,
                   line.finish)
            if key in merged:
                connection.execute("UPDATE deck_lines SET quantity = quantity + ? WHERE line_id = ?",
                                   (line.quantity, merged[key]))
                continue
            position += 1
            cursor = connection.execute(
                "INSERT INTO deck_lines (deck_id, position, section, quantity, name, oracle_id, scryfall_id, "
                "requested_set, requested_number, requested_finish) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (deck_id, position, line.section, line.quantity, name, oracle_id, scryfall_id,
                 line.set_code if oracle_id and line.set_code else None,
                 line.number if oracle_id and line.set_code else None, line.finish))
            merged[key] = cursor.lastrowid
            if scryfall_id and scryfall_id not in self.collection.by_scryfall_id:
                unknown_printings.add(scryfall_id)
        return unknown_printings

    def _fetch_missing_cards(self, scryfall_ids):
        from .ingest import _record_prices, _store_card
        have = {row[0] for row in self.connection.execute(
            f"SELECT scryfall_id FROM cards WHERE scryfall_id IN ({','.join('?' * len(scryfall_ids))})",
            list(scryfall_ids))} if scryfall_ids else set()
        wanted = set(scryfall_ids) - have
        if not wanted:
            return
        day = datetime.now().date().isoformat()
        for card in scryfall.fetch_cards_by_id(wanted):
            _store_card(self.connection, card)
            _record_prices(self.connection, card, day)

    def guess_format(self, deck_id):
        states = self.lines[deck_id]
        counted = [s for s in states if s.row["section"] in ("commander", "companion", "main")]
        total = sum(s.row["quantity"] for s in counted)
        has_commander = any(s.row["section"] == "commander" for s in states)
        singleton = all(s.row["quantity"] == 1 for s in counted
                        if s.row["oracle_id"] and "Basic Land" not in (self._type_line(s) or ""))
        if has_commander:
            return "commander" if total >= 90 else "brawl"
        if total in (99, 100) and singleton:
            return "commander"
        if total >= 40:
            for fmt in ("standard", "pioneer", "pauper", "modern", "legacy", "vintage"):
                if all(((self.card_data(s.row["scryfall_id"]) or {}).get("legalities", {}).get(fmt)
                        in ("legal", "restricted")) for s in counted if s.row["scryfall_id"]):
                    return fmt
        return "casual"

    def _first_card_as_commander(self, deck_id):
        """Moxfield's "Copy for Moxfield" export names no commander; it is the list's first card, or
        its first two when they are partners. For a Commander deck with no commander yet, promote the
        first card when it can lead a deck, and the second when it pairs with the first."""
        connection = self.connection
        fmt = connection.execute("SELECT format FROM decks WHERE deck_id = ?", (deck_id,)).fetchone()[0]
        states = sorted(self.lines[deck_id], key=lambda s: s.row["position"])
        if fmt not in ("commander", "brawl", "oathbreaker") or any(s.row["section"] == "commander" for s in states):
            return
        main = [s for s in states if s.row["section"] == "main"]
        cards = [self.card_data(s.row["scryfall_id"]) if s.row["scryfall_id"] and s.row["quantity"] == 1 else None
                 for s in main[:2]]
        first = cards[0] if cards else None
        if not first or not can_be_commander(first, fmt) or "Background" in (first.get("type_line") or ""):
            return
        connection.execute("UPDATE deck_lines SET section = 'commander' WHERE line_id = ?", (main[0].row["line_id"],))
        second = cards[1] if len(cards) > 1 else None
        if second and has_partner_ability(first) and can_be_commander(second, fmt) and commanders_pair(first, second):
            connection.execute("UPDATE deck_lines SET section = 'commander' WHERE line_id = ?", (main[1].row["line_id"],))

    def _type_line(self, state):
        card = self.card_data(state.row["scryfall_id"]) if state.row["scryfall_id"] else None
        return (card or {}).get("type_line", "")

    @all_or_nothing
    def import_decks(self, decks):
        connection = self.connection
        warnings, imported, fetch = [], [], set()
        for spec in decks:
            if spec.get("parsed") is not None:           # already parsed, e.g. from Archidekt
                parsed, suggested = spec["parsed"], None
            else:
                parsed, suggested = parse_decklist(spec.get("text", ""))
            name = (spec.get("name") or suggested or "Untitled deck").strip()
            if not parsed:
                warnings.append({"deck": name, "line": "", "message": "No cards found in this list"})
                continue
            now = self._now()
            cursor = connection.execute(
                "INSERT INTO decks (name, format, status, priority, notes, source, source_url, raw_text, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, '', ?, ?, ?, ?, ?)",
                (name, spec.get("format") or "auto", spec.get("status") or "active", spec.get("source") or "paste",
                 spec.get("source_url"), spec.get("text", ""), now, now))
            deck_id = cursor.lastrowid
            fetch |= self._store_lines(deck_id, parsed, warnings, name)
            imported.append(deck_id)
        self._fetch_missing_cards(fetch)
        connection.commit()
        self.reload()
        for deck_id in imported:
            if self.decks[deck_id]["format"] == "auto":
                connection.execute("UPDATE decks SET format = ? WHERE deck_id = ?", (self.guess_format(deck_id), deck_id))
            self._first_card_as_commander(deck_id)
        connection.commit()
        self.reload()
        for deck_id in imported:
            versions.record(self, deck_id, "import")
        return imported, warnings

    # -- the decks file: every deck, to move them to another copy of Cardclops (docs/DECKS.md) --

    FILE_FORMAT = "cardclops-decks"

    def export_all(self):
        """Every deck with its settings, lines (and the printings they ask for) and pins."""
        from . import __version__
        decks = []
        for deck in self.ordered_decks():
            lines = []
            for state in sorted(self.lines[deck["deck_id"]], key=lambda s: s.row["position"]):
                row = state.row
                line = {"section": row["section"], "quantity": row["quantity"], "name": row["name"]}
                if row["requested_set"]:
                    line.update(set_code=row["requested_set"], number=row["requested_number"], scryfall_id=row["scryfall_id"])
                if row["requested_finish"]:
                    line["finish"] = row["requested_finish"]
                pins = self.pins.get(row["line_id"])
                if pins:
                    line["pins"] = [{"pool": pool, "quantity": quantity} for pool, quantity in pins.items()]
                lines.append(line)
            decks.append({key: deck.get(key) for key in ("name", "format", "status", "priority", "copy_policy", "notes",
                                                        "source", "source_url")} | {"lines": lines})
        return {"format": self.FILE_FORMAT, "version": 1, "app_version": __version__,
                "exported_at": self._now(), "decks": decks}

    @all_or_nothing
    def import_file(self, data, on_conflict="replace"):
        """Decks from export_all(). A deck whose name is already here is replaced in place (keeping
        its history), kept beside the imported one ("keep"), or left alone ("skip"). Pins come
        across for copies this collection has. Returns {imported, replaced, skipped, warnings}."""
        if not isinstance(data, dict) or data.get("format") != self.FILE_FORMAT:
            raise ValueError("That isn't a Cardclops decks file (made with Export all decks)")
        if on_conflict not in ("replace", "keep", "skip"):
            raise ValueError("on_conflict must be replace, keep or skip")
        connection = self.connection
        here = {deck["name"].casefold(): deck["deck_id"] for deck in self.decks.values()}
        result = {"imported": [], "replaced": [], "skipped": [], "warnings": []}
        touched, fetch, pin_specs = [], set(), {}
        for spec in data.get("decks") or []:
            name = (spec.get("name") or "Untitled deck").strip()
            parsed = [ParsedLine(section=line.get("section") if line.get("section") in SECTIONS else "main",
                                 quantity=max(1, int(line.get("quantity") or 1)), name=str(line.get("name") or ""),
                                 set_code=line.get("set_code"), number=line.get("number"), finish=line.get("finish"),
                                 raw=str(line.get("name") or ""), scryfall_id=line.get("scryfall_id"))
                      for line in spec.get("lines") or [] if line.get("name")]
            fields = {"format": spec.get("format") or "auto",
                      "status": spec.get("status") if spec.get("status") in ("active", "inactive") else "inactive",
                      "priority": int(spec.get("priority") or 0),
                      "copy_policy": spec.get("copy_policy") if spec.get("copy_policy") in COPY_POLICIES else "default",
                      "notes": spec.get("notes") or "", "source": spec.get("source") or "file",
                      "source_url": spec.get("source_url")}
            existing = here.get(name.casefold())
            if existing and on_conflict == "skip":
                result["skipped"].append(name)
                continue
            now = self._now()
            if existing and on_conflict == "replace":
                deck_id = existing
                connection.execute(
                    "UPDATE decks SET format = ?, status = ?, priority = ?, copy_policy = ?, notes = ?, source = ?, "
                    "source_url = ?, updated_at = ? WHERE deck_id = ?", (*fields.values(), now, deck_id))
                connection.execute("DELETE FROM deck_lines WHERE deck_id = ?", (deck_id,))
                result["replaced"].append(name)
            else:
                if existing:
                    name = f"{name} (imported)"
                deck_id = connection.execute(
                    "INSERT INTO decks (name, format, status, priority, copy_policy, notes, source, source_url, raw_text, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
                    (name, *fields.values(), now, now)).lastrowid
                result["imported"].append(name)
            fetch |= self._store_lines(deck_id, parsed, result["warnings"], name)
            touched.append(deck_id)
            pin_specs[deck_id] = [(line.get("section"), line.get("name"), pin) for line in spec.get("lines") or []
                                  for pin in line.get("pins") or []]
        self._fetch_missing_cards(fetch)
        # Pins, for copies this collection holds: matched to the stored line by section and name.
        for deck_id, pins in pin_specs.items():
            line_ids = {(row["section"], row["name"]): row["line_id"] for row in connection.execute(
                "SELECT line_id, section, name FROM deck_lines WHERE deck_id = ?", (deck_id,))}
            for section, name, pin in pins:
                line_id = line_ids.get((section, name))
                if line_id and pin.get("pool") in self.pools and int(pin.get("quantity") or 0) > 0:
                    connection.execute("INSERT OR REPLACE INTO deck_pins VALUES (?, ?, ?)", (line_id, pin["pool"], int(pin["quantity"])))
        connection.commit()
        self.reload()
        for deck_id in touched:
            if self.decks[deck_id]["format"] == "auto":
                connection.execute("UPDATE decks SET format = ? WHERE deck_id = ?", (self.guess_format(deck_id), deck_id))
        connection.commit()
        self.reload()
        for deck_id in touched:
            versions.record(self, deck_id, "import")
        return result

    @all_or_nothing
    def replace_list(self, deck_id, text, parsed=None, reason="replace"):
        connection = self.connection
        old_pins = {}
        for state in self.lines[deck_id]:
            for pool, quantity in self.pins.get(state.row["line_id"], {}).items():
                old_pins[(state.row["section"], state.row["oracle_id"], pool)] = quantity
        if parsed is None:
            parsed, _ = parse_decklist(text)
        warnings = []
        connection.execute("DELETE FROM deck_lines WHERE deck_id = ?", (deck_id,))
        fetch = self._store_lines(deck_id, parsed, warnings, self.decks[deck_id]["name"])
        self._fetch_missing_cards(fetch)
        for row in connection.execute("SELECT line_id, section, oracle_id FROM deck_lines WHERE deck_id = ?", (deck_id,)):
            for (section, oracle_id, pool), quantity in old_pins.items():
                if section == row["section"] and oracle_id == row["oracle_id"]:
                    connection.execute("INSERT OR REPLACE INTO deck_pins VALUES (?, ?, ?)", (row["line_id"], pool, quantity))
        connection.execute("UPDATE decks SET raw_text = ?, updated_at = ? WHERE deck_id = ?", (text, self._now(), deck_id))
        connection.commit()
        self.reload()
        versions.record(self, deck_id, reason)
        return warnings

    @all_or_nothing
    def restore(self, deck_id, version_id):
        """Put back an earlier version's list; pins survive for cards still present."""
        connection = self.connection
        lines = versions.lines_of(connection, deck_id, version_id)
        old_pins = {(s.row["section"], s.row["oracle_id"], pool): quantity
                    for s in self.lines[deck_id] for pool, quantity in self.pins.get(s.row["line_id"], {}).items()}
        connection.execute("DELETE FROM deck_lines WHERE deck_id = ?", (deck_id,))
        for position, line in enumerate(lines, start=1):
            line_id = connection.execute(
                "INSERT INTO deck_lines (deck_id, position, section, quantity, name, oracle_id, scryfall_id, "
                "requested_set, requested_number, requested_finish) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (deck_id, position, *(line[f] for f in versions.LINE_FIELDS))).lastrowid
            for (section, oracle_id, pool), quantity in old_pins.items():
                if section == line["section"] and oracle_id == line["oracle_id"]:
                    connection.execute("INSERT OR REPLACE INTO deck_pins VALUES (?, ?, ?)", (line_id, pool, quantity))
        connection.execute("UPDATE decks SET updated_at = ? WHERE deck_id = ?", (self._now(), deck_id))
        connection.commit()
        self.reload()
        versions.record(self, deck_id, "restore")

    def policy_preview(self, deck_id):
        """What each copy policy would give this deck, other decks unchanged."""
        deck = self.decks[deck_id]
        current = deck.get("copy_policy") or "default"

        def snapshot():
            return {s.row["line_id"]: [(pool, quantity) for pool, quantity, _ in s.allocations]
                    for s in self.lines[deck_id]}

        def value(allocation):
            return sum((self.pools[pool][0].price_usd or 0) * quantity
                       for pools in allocation.values() for pool, quantity in pools)

        results = {}
        try:
            for policy in COPY_POLICIES:
                deck["copy_policy"] = policy
                self.allocate()
                results[policy] = snapshot()
        finally:
            deck["copy_policy"] = current
            self.allocate()
        base = results[current]
        names = {s.row["line_id"]: s.row["name"] for s in self.lines[deck_id]}
        options = {}
        for policy, allocation in results.items():
            changes = []
            for line_id, pools in allocation.items():
                before, after = base.get(line_id, []), pools
                if sorted(before) != sorted(after) and before and after:
                    changes.append({"line_id": line_id, "name": names[line_id],
                                    "from": self.pool_info(before[0][0]), "to": self.pool_info(after[0][0])})
            options[policy] = {"value_usd": round(value(allocation), 2),
                               "value_change_usd": round(value(allocation) - value(base), 2),
                               "changes": sorted(changes, key=lambda c: c["name"])}
        return {"current": current, "options": options}

    @all_or_nothing
    def update(self, deck_id, changes):
        connection = self.connection
        fields = {k: changes[k] for k in ("name", "format", "status", "priority", "notes", "copy_policy")
                  if k in changes}
        if "status" in fields and fields["status"] not in ("active", "inactive"):
            raise ValueError("status must be active or inactive")
        if "copy_policy" in fields and fields["copy_policy"] not in COPY_POLICIES:
            raise ValueError("copy_policy must be default, budget or bling")
        if fields:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            connection.execute(f"UPDATE decks SET {assignments}, updated_at = ? WHERE deck_id = ?",
                               (*fields.values(), self._now(), deck_id))
        if "commanders" in changes:
            chosen = set(changes["commanders"])
            for state in self.lines[deck_id]:
                row = state.row
                if row["oracle_id"] in chosen and row["section"] != "commander":
                    connection.execute("UPDATE deck_lines SET section = 'commander' WHERE line_id = ?", (row["line_id"],))
                elif row["section"] == "commander" and row["oracle_id"] not in chosen:
                    connection.execute("UPDATE deck_lines SET section = 'main' WHERE line_id = ?", (row["line_id"],))
        connection.commit()
        self.reload()
        if "commanders" in changes:
            versions.record(self, deck_id, "commander")

    # -- editing one line (docs/DECKS.md, "Endpoints") --

    @all_or_nothing
    def add_text(self, deck_id, text, section=None):
        """Quick add: decklist lines ("2 Sol Ring", "1 Sol Ring (C21) 263 *F*"), each added to the deck
        or to the quantity of the same line already there. `section` applies to lines the text doesn't
        put in a section of its own. Returns {"added": [{name, quantity, section}], "warnings"}."""
        if section is not None and section not in SECTIONS:
            raise ValueError(f"section must be one of {', '.join(SECTIONS)}")
        parsed, _ = parse_decklist(text or "")
        if not parsed:
            raise ValueError("No cards in that: type lines like 2 Sol Ring")
        connection = self.connection
        name = self.decks[deck_id]["name"]
        added, warnings, fetch = [], [], set()
        position = max((s.row["position"] for s in self.lines[deck_id]), default=0)
        for line in parsed:
            target = line.section if line.section != "main" else (section or "main")
            oracle_id, scryfall_id, card_name, warning = self.resolver.resolve(line)
            if warning:
                warnings.append({"deck": name, "line": line.raw or line.name, "message": warning})
            if oracle_id is None:
                continue
            requested = scryfall_id if line.set_code else None
            same = connection.execute(
                "SELECT line_id FROM deck_lines WHERE deck_id = ? AND section = ? AND oracle_id = ? "
                "AND (CASE WHEN requested_set IS NULL THEN NULL ELSE scryfall_id END) IS ? AND requested_finish IS ?",
                (deck_id, target, oracle_id, requested, line.finish)).fetchone()
            if same:
                connection.execute("UPDATE deck_lines SET quantity = quantity + ? WHERE line_id = ?", (line.quantity, same[0]))
            else:
                position += 1
                connection.execute(
                    "INSERT INTO deck_lines (deck_id, position, section, quantity, name, oracle_id, scryfall_id, "
                    "requested_set, requested_number, requested_finish) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (deck_id, position, target, line.quantity, card_name, oracle_id, scryfall_id,
                     line.set_code if line.set_code else None, line.number if line.set_code else None, line.finish))
                if scryfall_id and scryfall_id not in self.collection.by_scryfall_id:
                    fetch.add(scryfall_id)
            added.append({"name": card_name, "quantity": line.quantity, "section": target})
        if not added:
            connection.rollback()
            raise ValueError(warnings[0]["message"] if warnings else "No cards in that")
        self._fetch_missing_cards(fetch)
        self._edited(deck_id)
        return {"added": added, "warnings": warnings}

    @all_or_nothing
    def add_line(self, deck_id, body):
        """Add a card by oracle_id (any printing), scryfall_id (that printing) or name. Adding a
        card already in that section raises its quantity. Returns the line_id."""
        connection = self.connection
        section = body.get("section") or "main"
        if section not in SECTIONS:
            raise ValueError(f"section must be one of {', '.join(SECTIONS)}")
        quantity = int(body.get("quantity", 1))
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        scryfall_id = body.get("scryfall_id") or None
        name = (body.get("name") or "").strip()
        if body.get("oracle_id") and not scryfall_id:
            if body["oracle_id"] not in self.resolver.representative:
                raise ValueError("unknown card")
            name = self.resolver.representative[body["oracle_id"]][1]
        if not name and not scryfall_id:
            raise ValueError("Choose a card")
        oracle_id, shown_id, name, warning = self.resolver.resolve(
            SimpleNamespace(name=name, scryfall_id=scryfall_id, set_code=None, number=None))
        if oracle_id is None:
            raise ValueError(warning or f"No card named {name}")
        requested = None
        if scryfall_id:
            requested = connection.execute("SELECT set_code, collector_number FROM printings WHERE scryfall_id = ?",
                                           (scryfall_id,)).fetchone()
        same = next((st for st in self.lines[deck_id] if st.row["section"] == section and st.row["oracle_id"] == oracle_id
                     and (st.row["scryfall_id"] == scryfall_id if requested else not st.row["requested_set"])), None)
        if same:
            line_id = same.row["line_id"]
            connection.execute("UPDATE deck_lines SET quantity = quantity + ? WHERE line_id = ?", (quantity, line_id))
        else:
            position = 1 + max((st.row["position"] for st in self.lines[deck_id]), default=0)
            line_id = connection.execute(
                "INSERT INTO deck_lines (deck_id, position, section, quantity, name, oracle_id, scryfall_id, "
                "requested_set, requested_number, requested_finish) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (deck_id, position, section, quantity, name, oracle_id, shown_id,
                 requested["set_code"] if requested else None,
                 requested["collector_number"] if requested else None)).lastrowid
            if shown_id not in self.collection.by_scryfall_id:
                self._fetch_missing_cards({shown_id})
        self._edited(deck_id)
        return line_id

    @all_or_nothing
    def edit_line(self, line_id, body):
        """Change a line's quantity (0 removes it) or move it to another section."""
        if line_id not in self.line_by_id:
            raise KeyError(f"no line {line_id}")
        connection = self.connection
        deck_id = self.line_by_id[line_id].row["deck_id"]
        if "section" in body and body["section"] not in SECTIONS:
            raise ValueError(f"section must be one of {', '.join(SECTIONS)}")
        if "quantity" in body:
            quantity = int(body["quantity"])
            if quantity < 0:
                raise ValueError("quantity can't be negative")
            if quantity == 0:
                connection.execute("DELETE FROM deck_lines WHERE line_id = ?", (line_id,))
                self._edited(deck_id)
                return
            connection.execute("UPDATE deck_lines SET quantity = ? WHERE line_id = ?", (quantity, line_id))
            # A pin can't hold more copies than the line now asks for.
            connection.execute("UPDATE deck_pins SET quantity = ? WHERE line_id = ? AND quantity > ?",
                               (quantity, line_id, quantity))
        if "section" in body:
            connection.execute("UPDATE deck_lines SET section = ? WHERE line_id = ?", (body["section"], line_id))
        self._edited(deck_id)

    def _edited(self, deck_id):
        self.connection.execute("UPDATE decks SET updated_at = ? WHERE deck_id = ?", (self._now(), deck_id))
        self.connection.commit()
        self.reload()
        versions.record(self, deck_id, "edit")

    @all_or_nothing
    def delete(self, deck_id):
        # The foreign keys cascade these too, but only on a connection with foreign_keys on; a new
        # deck can reuse the id, so nothing of the old one may be left behind either way.
        connection = self.connection
        connection.execute("DELETE FROM deck_pins WHERE line_id IN (SELECT line_id FROM deck_lines WHERE deck_id = ?)", (deck_id,))
        for table in ("deck_lines", "deck_versions", "decks"):
            connection.execute(f"DELETE FROM {table} WHERE deck_id = ?", (deck_id,))
        connection.commit()
        self.reload()

    @all_or_nothing
    def pin(self, line_id, pool, quantity):
        if line_id not in self.line_by_id:
            raise KeyError(line_id)
        if quantity <= 0:
            self.connection.execute("DELETE FROM deck_pins WHERE line_id = ? AND pool = ?", (line_id, pool))
        else:
            if pool not in self.pools:
                raise ValueError("You don't own that printing in that finish")
            self.connection.execute("INSERT OR REPLACE INTO deck_pins VALUES (?, ?, ?)", (line_id, pool, quantity))
        deck_id = self.line_by_id[line_id].row["deck_id"]
        self.connection.execute("UPDATE decks SET updated_at = ? WHERE deck_id = ?", (self._now(), deck_id))
        self.connection.commit()
        self.reload()
        return self.line_by_id[line_id]
