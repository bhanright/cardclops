"""Mana-base fixer: land swaps from spare copies that make a deck's spells easier to cast on curve.

The rules are in docs/TOOLS.md ("Mana-base fixer"). In short: the castability score is the mean,
over the deck's nonland cards weighted by quantity, of the chance of having every colored
requirement by the turn equal to the card's mana value. Swaps are chosen greedily, one land out
and one spare land in per step, so the land count never changes.

A swap never changes the library size and never touches the spells, so the score depends only on
how many sources of each color the library holds. Each candidate is scored from source counts
rather than by recomputing DeckStats; the counts come from the same helpers DeckStats uses
(`land_source_keys`, `nonland_source_keys`), so "before" matches the deck page exactly.
"""
import re
from collections import Counter
from dataclasses import dataclass

from .deckstats import (COLORS, PLAYING_SECTIONS, card_name, card_pips, cards_seen_on_the_play,
                        front_face, hypergeometric_at_least, is_basic_land, is_land, is_nonland_source,
                        land_back_face, land_source_keys, mana_value, nonland_source_keys, produced_colors)

MINIMUM_GAIN = 0.002
TIE = 1e-9
SOURCE_KEYS = tuple(COLORS) + ("C", "any")
COLOR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green", "C": "colorless"}
# Tagger's `ramp` tag and its descendants, for callers that have no TagIndex at hand.
DEFAULT_RAMP_TAGS = frozenset({
    "combat-ramp", "cycle-lea-moxen", "extra-land", "firebend-like", "gains-firebending", "gives-firebending",
    "land-or-hand", "land-ramp", "mana-dork", "mana-dork-egg", "mana-increaser", "mana-producer", "mana-rock",
    "mana-rock-with-set-s-mechanic", "moxen", "multi-land-ramp", "play-additional-land", "ramp",
    "ramp-with-set-s-mechanic", "repeatable-landers", "ritual", "sneak-land", "utility-mana-rock"})
# A sentence that makes the land enter tapped with no condition. "This land enters tapped unless ...",
# "If you don't, it enters tapped." and "If you control two or more other lands, ..." are conditional.
UNCONDITIONAL_TAPPED = re.compile(
    r"^(?!If |When |Whenever |As )[^.]*\benters(?: the battlefield)? tapped(?: with [^.]*)?\.$")
SENTENCE_BREAK = re.compile(r"(?<=\.)\s+|\n")


@dataclass
class SpareLand:
    pool: str               # "<scryfall_id>|<finish>"
    card: dict              # Scryfall card
    oracle_id: str
    tags: frozenset
    spare: int              # spare copies of this pool
    price_usd: float | None


def enters_tapped(card):
    """True for lands that always enter tapped (guildgates, bounce lands, gain lands, tri-lands)."""
    text = front_face(card).get("oracle_text") or ""
    return any(UNCONDITIONAL_TAPPED.match(sentence.strip()) for sentence in SENTENCE_BREAK.split(text))


class _Land:
    """One distinct land card in the mana base, with how it counts."""

    def __init__(self, card):
        self.card = card
        self.name = card_name(card)
        self.is_land = is_land(card)             # False for a modal DFC counted by its land face
        self.basic = is_basic_land(card)
        self.tapped = self.is_land and enters_tapped(card)
        self.basic_colors = frozenset(c for c in produced_colors(card) if c in COLORS) if self.basic else frozenset()
        self._keys = {}

    def keys(self, basic_colors, identity):
        """Source keys under a given set of basic colors (a "basic land card" fetch depends on it)."""
        cached = self._keys.get(basic_colors)
        if cached is None:
            cached = frozenset(land_source_keys(self.card, basic_colors, identity)[0])
            self._keys[basic_colors] = cached
        return cached


class _Model:
    """The deck reduced to what castability depends on."""

    def __init__(self, playing, identity, ramp_tags):
        self.identity = set(identity)
        self.deck_size = sum(line.quantity for line in playing if line.section == "main")
        # Requirements: (turn, ((color, needed), ...)) -> copies. Cards with none are certain.
        self.requirements = Counter()
        self.nonland_copies = 0
        self.pips = Counter()
        for line in playing:
            if is_land(line.card):
                continue
            self.nonland_copies += line.quantity
            pips, strict = card_pips(line.card)
            for color, amount in pips.items():
                self.pips[color] += amount * line.quantity
            turn = max(int(mana_value(line.card)), 1)
            self.requirements[(turn, tuple(sorted(strict.items())))] += line.quantity
        # Rocks and dorks in the library never change with a swap.
        self.nonland_sources = Counter()
        for line in playing:
            if line.section == "main" and is_nonland_source(line.card, line.tags, ramp_tags):
                for key in nonland_source_keys(line.card, self.identity):
                    self.nonland_sources[key] += line.quantity
        self.lands = {}                 # id(card) -> _Land
        self.library = Counter()        # id(card) -> copies in the library (main)
        self.outside = Counter()        # lands in the command zone or companion slot (counted, never drawn)
        for line in playing:
            if not (is_land(line.card) or land_back_face(line.card) is not None):
                continue
            land = self.lands.setdefault(id(line.card), _Land(line.card))
            (self.library if line.section == "main" else self.outside)[id(line.card)] += line.quantity

    def land(self, card):
        return self.lands.setdefault(id(card), _Land(card))

    def basic_colors(self, library):
        found = set()
        for counts in (library, self.outside):
            for key, copies in counts.items():
                if copies > 0:
                    found |= self.lands[key].basic_colors
        return frozenset(found)

    def land_sources(self, library, include_outside=False):
        basics = self.basic_colors(library)
        sources = Counter()
        for counts in ((library, self.outside) if include_outside else (library,)):
            for key, copies in counts.items():
                if copies > 0:
                    for source in self.lands[key].keys(basics, self.identity):
                        sources[source] += copies
        return sources

    def score(self, library):
        land = self.land_sources(library)
        drawable = {color: land.get(color, 0) + self.nonland_sources.get(color, 0) for color in COLORS + "C"}
        if not self.nonland_copies:
            return 1.0, drawable
        total = 0.0
        for (turn, needs), copies in self.requirements.items():
            probability = 1.0
            for color, needed in needs:
                probability = min(probability, hypergeometric_at_least(
                    self.deck_size, drawable[color], cards_seen_on_the_play(turn), needed))
            total += probability * copies
        return total / self.nonland_copies, drawable

    def tapped(self, library):
        return sum(copies for key, copies in library.items() if self.lands[key].tapped)

    def summary(self, library):
        score, drawable = self.score(library)
        by_color = {}
        for color in COLORS + "C":
            if self.pips.get(color, 0) > 0:
                by_color[color] = {str(need): round(hypergeometric_at_least(
                    self.deck_size, drawable[color], cards_seen_on_the_play(need), need), 4) for need in (1, 2, 3)}
        sources = self.land_sources(library, include_outside=True)
        tapped = self.tapped(library) + sum(c for k, c in self.outside.items() if self.lands[k].tapped)
        return {"score": round(score, 4), "by_color": by_color,
                "sources": {key: sources.get(key, 0) for key in SOURCE_KEYS}, "tapped_lands": tapped}


def _legal(card, deck_format):
    legalities = card.get("legalities") or {}
    if not deck_format or deck_format not in legalities:
        return True                      # casual or unknown formats have no card pool to check
    return legalities[deck_format] in ("legal", "restricted")


def _color_list(colors):
    names = [COLOR_NAMES[c] for c in COLORS + "C" if c in colors]
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _article(name):
    return "an" if name[:1].lower() in "aeiou" else "a"


def _note(removed, added, removed_keys, added_keys, sources_after, identity):
    gained = {c for c in added_keys if c in COLORS or c == "C"} - removed_keys
    lost = {c for c in removed_keys if c in COLORS or c == "C"} - added_keys
    kept = {c for c in removed_keys if c in COLORS or c == "C"} & added_keys
    if "any" in added_keys and len(gained) > 1:
        parts = [f"Adds a source of every color ({_color_list(set(identity))})"]
    elif len(gained) == 1:
        parts = [f"Adds a {_color_list(gained)} source"]
    elif gained:
        parts = [f"Adds {_color_list(gained)} sources"]
    else:
        parts = ["Keeps the same colors"]
    detail = [f"{COLOR_NAMES[c]} drops to {sources_after.get(c, 0)} sources" for c in COLORS + "C" if c in lost]
    detail += [f"{COLOR_NAMES[c]} stays at {sources_after.get(c, 0)} sources" for c in COLORS + "C" if c in kept]
    replace = f"replaces {_article(removed.name)} {removed.name}"
    if detail:
        replace += f" ({'; '.join(detail)})"
    parts.append(replace)
    if removed.tapped and not added.tapped:
        parts.append("one fewer land enters tapped")
    elif added.tapped and not removed.tapped:
        parts.append(f"{added.name} enters tapped")
    return "; ".join(parts)


def suggest_swaps(cards, spare_lands, deck_format, color_identity, max_swaps=6, tag_index=None):
    """Greedy land swaps from spare copies, as the manafix JSON in docs/TOOLS.md (without images)."""
    deck_format = (deck_format or "").lower() or None
    identity = {c for c in color_identity if c in COLORS}
    ramp_tags = tag_index.expand("ramp") if tag_index is not None else DEFAULT_RAMP_TAGS
    playing = [line for line in cards if line.section in PLAYING_SECTIONS and line.quantity > 0]
    model = _Model(playing, identity, ramp_tags)
    library = Counter(model.library)
    in_deck = {line.oracle_id for line in cards if line.section != "maybeboard"}

    # Removable: basics and lands that always enter tapped, one entry per main-deck line.
    removable = []                  # [line, _Land, copies left]
    for line in playing:
        if line.section != "main" or not is_land(line.card):
            continue
        land = model.lands[id(line.card)]
        if land.basic or land.tapped:
            removable.append([line, land, line.quantity])

    # Addable: spare lands in the identity, legal, making a deck color, new to the deck (basics excepted).
    addable = []                    # [SpareLand, _Land, copies left]
    for spare in spare_lands:
        card = spare.card
        if spare.spare <= 0 or not is_land(card):
            continue
        land = model.land(card)
        if not land.basic and spare.oracle_id in in_deck:
            continue
        if not set(card.get("color_identity") or ()) <= identity or not _legal(card, deck_format):
            continue
        makes = land_source_keys(card, model.basic_colors(library) | land.basic_colors, identity)[1]
        if not (makes & identity):
            continue
        addable.append([spare, land, spare.spare])

    before = model.summary(library)
    current_score = model.score(library)[0]
    current_tapped = model.tapped(library)
    swaps = []
    added_names = set()
    while len(swaps) < max_swaps:
        best = None
        # Removals and additions that behave the same are scored once.
        removal_groups = {}
        for index, (line, land, left) in enumerate(removable):
            if left > 0:
                removal_groups.setdefault(id(line.card), index)
        addition_groups = {}
        for index, (spare, land, left) in enumerate(addable):
            if left <= 0 or (not land.basic and card_name(spare.card) in added_names):
                continue
            group = (land.keys(model.basic_colors(library) | land.basic_colors, identity),
                     land.tapped, land.basic_colors)
            incumbent = addition_groups.get(group)
            if incumbent is None or _prefer(spare, addable[incumbent][0]):
                addition_groups[group] = index
        for remove_index in removal_groups.values():
            removed = removable[remove_index][1]
            for add_index in addition_groups.values():
                added = addable[add_index][1]
                if added.card is removed.card:
                    continue
                trial = Counter(library)
                trial[id(removed.card)] -= 1
                trial[id(added.card)] += 1
                model.lands.setdefault(id(added.card), added)
                score = model.score(trial)[0]
                tapped = current_tapped - removed.tapped + added.tapped
                rank = (round(score, 9), -tapped)
                if best is None or rank > best[0]:
                    best = (rank, remove_index, add_index, score, tapped, trial)
        if best is None or best[3] - current_score < MINIMUM_GAIN - TIE:
            break
        _, remove_index, add_index, score, tapped, trial = best
        line, removed, _ = removable[remove_index]
        spare, added, _ = addable[add_index]
        basics_before = model.basic_colors(library)
        removed_keys = removed.keys(basics_before, identity)
        library = trial
        added_keys = added.keys(model.basic_colors(library), identity)
        removable[remove_index][2] -= 1
        addable[add_index][2] -= 1
        added_names.add(card_name(spare.card))
        current_score, current_tapped = score, tapped
        sources_after = model.land_sources(library, include_outside=True)
        swaps.append({
            "remove": {"line_id": line.line_id, "name": removed.name, "scryfall_id": line.card.get("id"),
                       "why": "basic" if removed.basic else "enters tapped"},
            "add": {"name": added.name, "scryfall_id": spare.card.get("id"), "pool": spare.pool,
                    "finish": spare.pool.split("|", 1)[1] if "|" in spare.pool else "normal",
                    "price_usd": spare.price_usd, "spare": spare.spare},
            "score_after": round(score, 4),
            "note": _note(removed, added, set(removed_keys), set(added_keys), sources_after, identity),
        })

    message = None
    if not swaps:
        if not addable:
            message = "No spare lands in these colors" + (f" legal in {deck_format}" if deck_format else "")
        elif not removable:
            message = "No basic or enters-tapped lands to replace"
        else:
            message = "None of your spare lands would make these spells noticeably easier to cast"
    return {
        "before": before,
        "after": model.summary(library),
        "swaps": swaps,
        "considered": {"removable": sum(line.quantity for line, _, _ in removable), "spare_lands": len(addable)},
        "message": message,
    }


def _prefer(candidate, incumbent):
    """Between two spare lands that do the same job, use the cheaper one, then the one with more spare."""
    def key(spare):
        return (spare.price_usd if spare.price_usd is not None else float("inf"), -spare.spare, card_name(spare.card))
    return key(candidate) < key(incumbent)
