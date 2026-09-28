"""Importing decks from Archidekt.

Archidekt's read API is public: its staff have said third parties are "more
than welcome to use our API" (forum thread 2832338, 2022), with the caveat of
their rate limiter. Only public and unlisted decks are visible; private ones
need exporting by hand. Every call here is one the user asked for, spaced a
second apart.

Moxfield is deliberately absent: its terms forbid automated access without
written approval, so Moxfield decks come in as pasted "Copy for Moxfield" text.
"""
import re
import time
import urllib.parse

from . import net
from .decks import ParsedLine
from .runtime import IN_BROWSER

API = "https://archidekt.com/api"
SECONDS_BETWEEN_CALLS = 1.0
DECK_URL = re.compile(r"archidekt\.com/(?:api/)?decks/(\d+)", re.I)
# Archidekt's numeric formats. Only Commander is confirmed from live data; the
# rest are left to the gallery's own guess from the cards.
FORMATS = {3: "commander"}
FINISHES = {"foil": "foil", "etched": "etched"}

_last_call = 0.0


# Archidekt's API answers only its own pages, so a web page (the browser edition) can't read it.
IN_BROWSER_MESSAGE = ("Archidekt doesn't let other websites read its decks. Paste the list instead: "
                      "use the deck's Export option on Archidekt to copy its list as text.")


def _get(url):
    global _last_call
    if IN_BROWSER:
        raise net.NetError(url, 0, IN_BROWSER_MESSAGE)
    wait = SECONDS_BETWEEN_CALLS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    return net.get_json(url)


def deck_id_from(text):
    text = text.strip()
    if text.isdigit():
        return int(text)
    match = DECK_URL.search(text)
    if not match:
        raise ValueError("That doesn't look like an Archidekt deck link (archidekt.com/decks/<number>/…)")
    return int(match.group(1))


def list_decks(username):
    """A user's public decks: [{id, name, size, format, updated_at, featured}]."""
    url = f"{API}/decks/v3/?ownerUsername={urllib.parse.quote(username.strip())}&pageSize=100"
    decks = []
    while url and len(decks) < 1000:
        page = _get(url)
        for deck in page.get("results", []):
            if deck.get("private"):
                continue
            decks.append({"id": deck["id"], "name": deck.get("name", ""), "size": deck.get("size"),
                          "format": FORMATS.get(deck.get("deckFormat")), "updated_at": deck.get("updatedAt"),
                          "featured": deck.get("featured"),
                          "url": f"https://archidekt.com/decks/{deck['id']}"})
        url = page.get("next")
    return decks


def fetch_deck(deck_id):
    """(name, format or None, [ParsedLine], text for the gallery's own copy of the list)."""
    data = _get(f"{API}/decks/{deck_id}/")
    categories = {c["name"]: c for c in data.get("categories", [])}
    lines = []
    for item in data.get("cards", []):
        if item.get("deletedAt"):
            continue
        card = item.get("card") or {}
        names = item.get("categories") or []
        first = categories.get(names[0], {}) if names else {}
        if any(n.lower() == "sideboard" for n in names):
            section = "sideboard"
        elif any(categories.get(n, {}).get("isPremier") for n in names):
            section = "commander"
        elif item.get("companion"):
            section = "companion"
        elif first and first.get("includedInDeck") is False:
            section = "maybeboard"
        else:
            section = "main"
        edition = (card.get("edition") or {}).get("editioncode")
        lines.append(ParsedLine(
            section=section, quantity=int(item.get("quantity") or 1),
            name=(card.get("oracleCard") or {}).get("name") or card.get("displayName") or "?",
            set_code=edition.lower() if edition else None, number=card.get("collectorNumber"),
            finish=FINISHES.get((item.get("modifier") or "").lower()), scryfall_id=card.get("uid"),
            raw=(card.get("oracleCard") or {}).get("name", "")))
    return data.get("name") or f"Archidekt {deck_id}", FORMATS.get(data.get("deckFormat")), lines, to_text(lines)


def to_text(lines):
    """The list in Archidekt's own text shape, so "Replace list" starts from something readable."""
    out = []
    for line in lines:
        printing = f" ({line.set_code}) {line.number}" if line.set_code and line.number else ""
        finish = {"foil": " *F*", "etched": " *E*"}.get(line.finish, "")
        category = {"commander": " [Commander{top}]", "companion": " [Companion]", "sideboard": " [Sideboard]",
                    "maybeboard": " [Maybeboard{noDeck}{noPrice}]"}.get(line.section, "")
        out.append(f"{line.quantity}x {line.name}{printing}{finish}{category}")
    return "\n".join(out)
