"""Magic: Redux, a community format Scryfall doesn't track (magicredux.themangarat.com/format/,
read 2026-09-28).

A card is legal when it has a printing in one of the format's sets, or is one of the cards the
format adds as replacements for the banned power (Brainstorm for Ancestral Recall, the Talismans
for the Moxes...), the three 1994 promos, or a shockland. Some of those are banned or restricted.
Decks are constructed: at least 60 cards, a 15-card sideboard, four copies of a card, and a dual
land and its shockland count together toward four.

Cardclops treats "redux" like Scryfall's formats: `load` works out every card's status from the
catalog when a collection is built, and `annotate` adds it to a card's `legalities`, so search
(f:redux), the "Legal in" counts, card details and deck checks read it like any other. The lists
below are the format's; change them here when it changes.
"""
from .ingest import oracle_id_of

FORMAT = "redux"

# Alpha, Beta, Unlimited, Arabian Nights, Antiquities, Legends, The Dark, Fallen Empires.
SETS = ("lea", "leb", "2ed", "arn", "atq", "leg", "drk", "fem")

# Dual lands and the shockland of the same colors: up to four of each pair, in any mix.
DUAL_SHOCK_PAIRS = {
    "Tundra": "Hallowed Fountain", "Underground Sea": "Watery Grave", "Badlands": "Blood Crypt",
    "Taiga": "Stomping Ground", "Savannah": "Temple Garden", "Scrubland": "Godless Shrine",
    "Bayou": "Overgrown Tomb", "Tropical Island": "Breeding Pool", "Volcanic Island": "Steam Vents",
    "Plateau": "Sacred Foundry",
}

# Legal although not from the sets above.
ADDITIONS = {
    "Brainstorm", "Lotus Petal", "Wasteland",                       # for Ancestral Recall, Black Lotus, Strip Mine
    "Talisman of Dominance", "Talisman of Impulse", "Talisman of Indulgence",   # for the Moxes
    "Talisman of Progress", "Talisman of Unity",
    "Arena", "Nalathni Dragon", "Sewers of Estark",                 # the 1994 promos
    *DUAL_SHOCK_PAIRS.values(),
}

# The format bans every ante card; these are the nine Wizards printed.
ANTE_CARDS = {"Amulet of Quoz", "Bronze Tablet", "Contract from Below", "Darkpact", "Demonic Attorney",
              "Jeweled Bird", "Rebirth", "Tempest Efreet", "Timmerian Fiends"}

BANNED = ANTE_CARDS | {
    "Ancestral Recall", "Black Lotus", "Chaos Orb", "City in a Bottle", "Conversion", "Deathgrip",
    "Falling Star", "Gloom", "Lifeforce", "Power Artifact", "Shahrazad", "Strip Mine",
    "Circle of Protection: Black", "Circle of Protection: Blue", "Circle of Protection: Green",
    "Circle of Protection: Red", "Circle of Protection: White",
    "Mox Emerald", "Mox Jet", "Mox Pearl", "Mox Ruby", "Mox Sapphire",
}

RESTRICTED = {
    "Balance", "Black Vise", "Berserk", "Candelabra of Tawnos", "Channel", "Dark Ritual", "Demonic Tutor",
    "Fork", "Ivory Tower", "Library of Alexandria", "Mana Drain", "Mana Vault", "Maze of Ith", "Mind Twist",
    "Mishra's Workshop", "Recall", "Regrowth", "Sol Ring", "Time Vault", "Time Walk", "Timetwister",
    "Wheel of Fortune",
}

_status = {}                  # oracle id -> legal | restricted | banned (absent: not legal)
_loaded = False


def status_for(name, in_sets):
    """A card's Redux status from its name and whether it has a printing in the format's sets."""
    if name in BANNED:
        return "banned"
    if not in_sets and name not in ADDITIONS:
        return "not_legal"
    return "restricted" if name in RESTRICTED else "legal"


def load(connection):
    """Every card's status, from the catalog (every printing of every card, and the cards' names)."""
    global _status, _loaded
    marks = ", ".join("?" * len(SETS))
    in_sets = {row[0] for row in connection.execute(
        f"SELECT DISTINCT oracle_id FROM set_cards WHERE set_code IN ({marks}) AND oracle_id IS NOT NULL", SETS)}
    named = BANNED | ADDITIONS | RESTRICTED
    status = {}
    for oracle_id, name in connection.execute("SELECT oracle_id, name FROM oracle_cards"):
        if oracle_id in in_sets or name in named:
            status[oracle_id] = status_for(name, oracle_id in in_sets)
    _status = {oracle_id: value for oracle_id, value in status.items() if value != "not_legal"}
    _loaded = bool(in_sets)                  # no catalog yet (before the first download): say nothing
    return _status


def annotate(card):
    """Add the card's Redux status to its `legalities` (once the statuses are loaded)."""
    if _loaded and isinstance(card, dict):
        card.setdefault("legalities", {})[FORMAT] = _status.get(oracle_id_of(card), "not_legal")
    return card


def pair_problems(copies):
    """{name: count} of a deck (main and sideboard) -> problems where a dual land and its shockland
    together pass four."""
    problems = []
    for dual, shock in DUAL_SHOCK_PAIRS.items():
        together = copies.get(dual, 0) + copies.get(shock, 0)
        if copies.get(dual) and copies.get(shock) and together > 4:
            problems.append({"name": f"{dual} and {shock}",
                             "reason": f"{together} copies together; a dual land and its shockland share four"})
    return problems
