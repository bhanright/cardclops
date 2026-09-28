"""Searching the deck list (Decks page, GET /api/decks?q=...). docs/DECKS.md, "Searching decks".

A query is terms separated by spaces; a deck must match all of them, and a leading "-" negates one.

  atraxa            deck or commander name contains the words
  "Big Game"        a quoted phrase
  name:tokens       deck name contains
  cmd:atraxa        a commander's name contains
  card:"sol ring"   a card in the deck (not the maybeboard) contains that in its name
  c:g  c:simic      color identity includes these colors (c:c or c:colorless: no colors)
  c=ug  c<=ug       exactly these colors; within these colors (also >=, <, >)
  f:commander       format
  folder:cube       the deck's folder name contains (folder:none: in no folder)
  is:active  is:inactive  is:legal  is:illegal  is:complete  is:incomplete  is:conflict
  cards>=100  missing>0  value>200  cost<50  owned>90  priority=1
"""
import re

from .decks import fold
from .query import COLOR_WORDS

TOKEN = re.compile(r'(-?)(?:(\w+)(<=|>=|!=|:|=|<|>))?("[^"]*"|\S+)')
COLOR_KEYS = {"c", "color", "colors", "id", "identity", "ci"}
TEXT_KEYS = {"name": "name", "cmd": "commander", "commander": "commander", "card": "card", "has": "card",
             "folder": "folder"}
NUMBER_KEYS = {"cards": "card_count", "missing": "missing", "value": "value_usd", "cost": "cost_to_complete_usd",
               "owned": "owned", "priority": "priority"}
FORMAT_ALIASES = {"edh": "commander", "cmdr": "commander", "pdh": "paupercommander", "pauperedh": "paupercommander"}
IS_WORDS = {
    "active": lambda d: d["status"] == "active",
    "inactive": lambda d: d["status"] == "inactive",
    "legal": lambda d: d.get("legal") is True,
    "illegal": lambda d: d.get("legal") is False,
    "complete": lambda d: not d["missing"],
    "incomplete": lambda d: bool(d["missing"]),
    "conflict": lambda d: bool(d.get("missing_used_elsewhere")),
}
COMPARE = {":": lambda a, b: a == b, "=": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
           ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}


def _colors(value):
    word = value.lower()
    if word in ("c", "colorless"):
        return frozenset()
    if word in COLOR_WORDS:
        return frozenset(COLOR_WORDS[word])
    if re.fullmatch(r"[wubrg]+", word):
        return frozenset(word.upper())
    raise ValueError(f"“{value}” isn't a color: use letters like ug, or a name like simic")


def parse(query):
    """[(negated, test(deck, card_names) -> bool)]; raises ValueError for a term it can't read."""
    tests = []
    for negated, key, op, value in TOKEN.findall(query or ""):
        value = value[1:-1] if len(value) > 1 and value.startswith('"') and value.endswith('"') else value
        key = (key or "").lower()
        if key and key not in COLOR_KEYS | set(TEXT_KEYS) | set(NUMBER_KEYS) | {"f", "format", "is"}:
            value, key, op = f"{key}{op}{value}", "", ""           # not a known key: plain words
        if not value:
            continue
        tests.append((bool(negated), _test(key, op, value)))
    return tests


def _test(key, op, value):
    if not key:
        words = fold(value).split()
        return lambda d, cards: all(w in fold(d["name"]) or any(w in fold(c["name"]) for c in d["commanders"]) for w in words)
    if key in COLOR_KEYS:
        wanted = _colors(value)
        check = {":": lambda have: wanted <= have if wanted else not have, ">=": lambda have: wanted <= have,
                 "=": lambda have: have == wanted, "<=": lambda have: have <= wanted,
                 "<": lambda have: have < wanted, ">": lambda have: have > wanted, "!=": lambda have: have != wanted}[op]
        return lambda d, cards: check(frozenset(d["color_identity"]))
    if key in TEXT_KEYS:
        needle, field = fold(value), TEXT_KEYS[key]
        if field == "name":
            return lambda d, cards: needle in fold(d["name"])
        if field == "commander":
            return lambda d, cards: any(needle in fold(c["name"]) for c in d["commanders"])
        if field == "folder":
            if needle == "none":
                return lambda d, cards: not d.get("folder")
            return lambda d, cards: needle in fold(d.get("folder") or "")
        return lambda d, cards: any(needle in name for name in cards)
    if key in ("f", "format"):
        wanted = FORMAT_ALIASES.get(fold(value).replace(" ", ""), fold(value).replace(" ", ""))
        return lambda d, cards: (d["format"] or "") == wanted
    if key == "is":
        if value.lower() not in IS_WORDS:
            raise ValueError(f"is:{value} isn't one of {', '.join('is:' + w for w in IS_WORDS)}")
        return lambda d, cards: IS_WORDS[value.lower()](d)
    field = NUMBER_KEYS[key]
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"{key}{op}{value}: {key} needs a number") from None
    compare = COMPARE[op]
    return lambda d, cards: compare(float(d.get(field) or 0), number)


def search(summaries, card_names, query):
    """The summaries matching `query`; card_names maps deck_id to the folded names of its cards."""
    tests = parse(query)
    return [d for d in summaries
            if all(negated != test(d, card_names.get(d["deck_id"], ())) for negated, test in tests)]
