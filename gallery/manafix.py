"""Mana-base fixer: land swaps from spare copies that make a deck's spells easier to cast on curve.

The rules are in docs/TOOLS.md ("Mana-base fixer"). Swaps are chosen greedily, one land out and one
spare land in per step, so the land count never changes. This module adds a model of *when* each
land makes its colors, because counting every land that can ever make a color as a full source
made slow lands look like free perfect duals.

When a land's color is usable (the "timing" of a source):
- full: basics, untapped duals (shock, check, pain, fast, pathway, filter, Verge, battlebond,
  any-color lands such as Command Tower or Exotic Orchard) and untapped fetches (Verdant Catacombs,
  Prismatic Vista). The color is there the turn the land is played.
- delayed: lands that enter tapped (gain lands, temples, tri-lands) and slow fetches whose land
  arrives tapped (Evolving Wilds, Fabled Passage, the Streets of New Capenna "Theaters"). A card
  drawn on the turn you need it cannot help, so for a spell on turn T the land counts only if it
  was among the first 7 + (T - 2) cards: weight (T + 5) / (T + 6) from turn 2 on, 0 on turn 1.
- costly: lands that tap for colorless and fix only by paying mana and sacrificing themselves
  (Terminal Moraine, Shire Terrace, Promising Vein, Myriad Landscape, landcycling such as Ash
  Barrens). Play it, pay for it a turn later, and the basic arrives tapped: two turns and two
  mana for one basic of one color. It counts from turn 3, as if drawn two turns earlier
  ((T + 4) / (T + 6)), and only half, because it makes one color and spends mana a spell wanted.
Sources are then added up per color with those weights, and the hypergeometric odds use the
fractional count (interpolated between the two whole counts around it).

Tempo: each land that is slow on the turn it arrives costs the deck, whatever its colors. The
objective is castability minus TEMPO_WEIGHT times the expected number of slow lands among the
first ten cards (turn four on the play); a costly fetch counts twice, since it spends a mana too.

What a player would cut: basics (the model picks the one whose color is most over-served), and a
tapped land only for an untapped land that makes every color it made. Lands with an ability
beyond mana (Emeria, Dakmor Salvage, manlands, bounce lands, cycling lands) are never removed.
At most one costly fetch in the deck, counting any it already plays, and it must clearly win.
"""
import re
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from .deckstats import (COLORS, PLAYING_SECTIONS, REMINDER_TEXT, card_name, card_pips, front_face,
                        hypergeometric_at_least, is_basic_land, is_land, is_nonland_source, land_back_face,
                        land_source_keys, mana_value, nonland_source_keys, produced_colors)

MINIMUM_GAIN = 0.01             # one percentage point of the objective per swap
COSTLY_MINIMUM_GAIN = 0.02      # a costly fetch must clearly beat keeping the basic
LOOKAHEAD = 3                   # swaps of one kind judged together when the first alone gains little
MAX_COSTLY_FETCHES = 1          # per deck, counting the ones it already plays
TEMPO_WEIGHT = 0.10             # objective lost per expected slow land in the first ten cards
TEMPO_CARDS = 10                # cards seen by turn four on the play
COSTLY_SHARE = 0.5              # a costly fetch makes one color, and spends mana a spell wanted
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
# "Enters tapped unless ..." conditions a normal deck meets from its first turns or soon after:
# check lands, Castles, battle lands, fast lands, slow lands, Commander's battlebond lands.
# Others ("unless a player has 13 or less life", "... a legendary creature", "... a Dragon",
# "three or more other Islands") leave the land tapped when it matters, so it counts as tapped.
_BASIC_TYPE = r"(?:Plains|Island|Swamp|Mountain|Forest)"
EASY_UNTAPPED = re.compile(
    r"tapped unless (?:you have two or more opponents"
    rf"|you control (?:a|an) {_BASIC_TYPE}(?: or (?:a|an) {_BASIC_TYPE})?"
    r"|you control two or more basic lands|you control a basic land"
    r"|you control two or (?:fewer|more) other lands|it's your first, second, or third turn of the game)\.")
BASIC_TYPES = {"Plains", "Island", "Swamp", "Mountain", "Forest"}
REVEAL_TO_UNTAP = re.compile(r"reveal (?:a|an) ([A-Z][\w-]*)(?: or (?:a|an) ([A-Z][\w-]*))? card from your hand")
RAMP_FETCH = re.compile(r"search your library for (?:up to two|a \w+ card and a)", re.IGNORECASE)
CONDITIONAL_TAPPED = re.compile(r"tapped unless |^If you control [^.]*, [^.]*enters tapped\.", re.MULTILINE)
# Cards that reward playing, sacrificing or recurring lands; with several of them, a deck's
# self-sacrificing fetch lands are there on purpose (a landfall trigger each, or a Crucible loop).
LANDS_MATTER = re.compile(
    r"\blandfall\b|whenever a land (?:you control )?enters|whenever one or more land|land cards? from your graveyard"
    r"|lands? from your graveyard|play lands from|land cards? (?:is|are) put into your graveyard", re.IGNORECASE)
LANDS_MATTER_CARDS = 3
LANDCYCLING = re.compile(r"\b(?:basic )?(?:land|plains|island|swamp|mountain|forest)cycling\b", re.IGNORECASE)
LAND_SEARCH_PARAGRAPH = re.compile(r"search your library for [^.]*\b(?:land|plains|island|swamp|mountain|forest)",
                                   re.IGNORECASE)
GENERIC_MANA = re.compile(r"\{\d+\}")
# Paragraphs a plain mana land carries; anything else makes it a utility land a player chose on purpose.
PLAIN_PARAGRAPHS = (
    re.compile(r"^\{[^:]*: Add "),                                        # a mana ability
    re.compile(r"enters(?: the battlefield)? tapped"),                    # tapped, or tapped unless ...
    re.compile(r"^When this land enters, (?:you gain 1 life|scry 1|surveil 1)\.$"),
    LAND_SEARCH_PARAGRAPH,                                                # a fetch
)


@dataclass
class SpareLand:
    pool: str               # "<scryfall_id>|<finish>"
    card: dict              # Scryfall card
    oracle_id: str
    tags: frozenset
    spare: int              # spare copies of this pool
    price_usd: float | None


def enters_tapped(card):
    """True for lands that always enter tapped (guildgates, gain lands, tri-lands)."""
    text = _land_face(card).get("oracle_text") or ""
    return any(UNCONDITIONAL_TAPPED.match(sentence.strip()) for sentence in SENTENCE_BREAK.split(text))


def comes_in_tapped(card):
    """Tapped when it matters: always, or unless a condition a normal deck rarely meets early."""
    if enters_tapped(card):
        return True
    text = REMINDER_TEXT.sub("", _land_face(card).get("oracle_text") or "")
    for match in REVEAL_TO_UNTAP.finditer(text):      # snarls reveal basic types; Wanderwine Hub a Merfolk
        if any(kind and kind not in BASIC_TYPES for kind in match.groups()):
            return True
    return bool(CONDITIONAL_TAPPED.search(text)) and not EASY_UNTAPPED.search(text)


def _land_face(card):
    back = land_back_face(card)
    return back if back is not None else front_face(card)


def _plain_text(card):
    face = _land_face(card)
    name = face.get("name") or card_name(card)
    text = REMINDER_TEXT.sub("", face.get("oracle_text") or "").replace(name, "this land")
    return [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]


def is_utility_land(card):
    """A land with an ability beyond making or fetching mana: a player chose it on purpose."""
    if is_basic_land(card):
        return False
    text = _land_face(card).get("oracle_text") or ""
    if LANDCYCLING.search(text) or re.search(r"^Cycling ", text, re.MULTILINE):
        return True
    for paragraph in _plain_text(card):
        if not any(pattern.search(paragraph) for pattern in PLAIN_PARAGRAPHS):
            return True
    return False


def fetch_kind(card):
    """How a land's fetched colors arrive: "fetch" (untapped land), "slow" (tapped), "costly", or None."""
    text = _land_face(card).get("oracle_text") or ""
    if LANDCYCLING.search(text):
        return "costly"
    for paragraph in REMINDER_TEXT.sub("", text).split("\n"):
        if not LAND_SEARCH_PARAGRAPH.search(paragraph):
            continue
        cost = paragraph.split(":", 1)[0] if paragraph.startswith("{") else ""
        if GENERIC_MANA.search(cost):
            return "costly"
        if "into your hand" in paragraph:
            return "costly"
        if "battlefield tapped" in paragraph:
            return "slow"
        return "fetch"
    return None


def _delayed_weight(turn):
    return (turn + 5) / (turn + 6) if turn >= 2 else 0.0


def _costly_weight(turn):
    return COSTLY_SHARE * (turn + 4) / (turn + 6) if turn >= 3 else 0.0


def _probability(deck_size, sources, drawn, needed):
    """Hypergeometric P(at least `needed`) for a fractional source count, interpolated."""
    whole = int(sources)
    part = sources - whole
    low = hypergeometric_at_least(deck_size, whole, drawn, needed)
    if part < 1e-9:
        return low
    return low + part * (hypergeometric_at_least(deck_size, whole + 1, drawn, needed) - low)


def _seen(turn):
    return 7 + turn - 1


_CLASSIFIED = {}


def _classify(card):
    """The parts of a land's profile that depend on its text alone, cached across requests."""
    key = (card.get("id"), card.get("name"), card.get("oracle_text"), tuple(card.get("produced_mana") or ()),
           tuple(face.get("oracle_text", "") for face in card.get("card_faces") or ()))
    found = _CLASSIFIED.get(key)
    if found is None:
        land = is_land(card)
        direct = produced_colors(card)
        text = _land_face(card).get("oracle_text") or ""
        found = (card_name(card), land, is_basic_land(card), comes_in_tapped(card), land and is_utility_land(card),
                 frozenset(direct), None if direct & set(COLORS) else fetch_kind(card),
                 bool(RAMP_FETCH.search(text)), "acrifice" in text)
        _CLASSIFIED[key] = found
    return found


class _Land:
    """One distinct land card (or land-backed modal DFC) with how and when it makes its colors."""

    def __init__(self, card, identity, keep_fetches=False):
        self.card = card
        (self.name, self.is_land, self.basic, self.tapped, self.utility, self.direct, self.fetch,
         ramp_fetch, sacrifices) = _classify(card)
        self.basic_colors = frozenset(c for c in self.direct if c in COLORS) if self.basic else frozenset()
        own = "delayed" if self.tapped else "full"
        self.fetch_timing = {"fetch": own, "slow": "delayed", "costly": "costly", None: own}[self.fetch]
        self.own_timing = own
        if self.fetch == "costly":
            self.kind, self.tempo = "costly_fetch", 2
        elif self.fetch == "slow" or (self.fetch == "fetch" and self.tapped):
            self.kind, self.tempo = "slow_fetch", 1
        elif self.fetch == "fetch":
            self.kind, self.tempo = "fetch", 0
        elif self.basic:
            self.kind, self.tempo = "basic", 0
        elif not (self.direct & set(COLORS)):
            self.kind, self.tempo = "colorless", 1 if self.tapped else 0
        else:
            self.kind, self.tempo = ("tapped", 1) if self.tapped else ("untapped", 0)
        if self.fetch is not None and ramp_fetch:
            self.utility = True          # Myriad Landscape, Krosan Verge: ramp, not just fixing
        if keep_fetches and self.fetch is not None and sacrifices:
            self.utility = True          # a landfall or land-recursion deck plays its fetches on purpose
        self.identity = identity
        self._cache = {}

    def profile(self, basic_colors):
        """(source keys as DeckStats counts them, {color: timing}) under a set of basic colors."""
        cached = self._cache.get(basic_colors)
        if cached is None:
            keys = frozenset(land_source_keys(self.card, basic_colors, self.identity)[0])
            timing = {}
            for key in keys:
                if key != "any":
                    timing[key] = self.own_timing if key in self.direct else self.fetch_timing
            cached = (keys, timing)
            self._cache[basic_colors] = cached
        return cached

    def colors(self, basic_colors):
        return {key for key in self.profile(basic_colors)[0] if key in COLORS}


class _Model:
    """The deck reduced to what castability and tempo depend on."""

    def __init__(self, playing, identity, ramp_tags):
        self.identity = frozenset(identity)
        self.deck_size = sum(line.quantity for line in playing if line.section == "main")
        self.requirements = Counter()    # (turn, ((color, needed), ...)) -> copies
        self.nonland_copies = 0
        self.pips = Counter()
        rocks = []                        # (mana value, keys, copies) for rocks and dorks in the library
        for line in playing:
            if is_land(line.card):
                continue
            self.nonland_copies += line.quantity
            pips, strict = card_pips(line.card)
            for color, amount in pips.items():
                self.pips[color] += amount * line.quantity
            turn = max(int(mana_value(line.card)), 1)
            self.requirements[(turn, tuple(sorted(strict.items())))] += line.quantity
            if line.section == "main" and is_nonland_source(line.card, line.tags, ramp_tags):
                rocks.append((int(mana_value(line.card)), nonland_source_keys(line.card, self.identity), line.quantity))
        self.max_turn = max([turn for turn, _ in self.requirements] + [3])
        self.lands_matter = sum(line.quantity for line in playing if not is_land(line.card)
                                and LANDS_MATTER.search(_all_text(line.card))) >= LANDS_MATTER_CARDS
        # A rock or dork of mana value m helps on turn T if cast earlier: never before turn m + 1.
        self.rock_sources = {}
        for color in COLORS + "C":
            for turn in range(1, self.max_turn + 1):
                total = 0.0
                for value, keys, copies in rocks:
                    if color in keys:
                        total += copies * (1.0 if value == 0 else _delayed_weight(turn) if turn > value else 0.0)
                self.rock_sources[(color, turn)] = total
        self.lands = {}
        self.library = Counter()          # id(card) -> copies in the library
        self.outside = Counter()          # lands in the command zone or companion slot
        for line in playing:
            if is_land(line.card) or land_back_face(line.card) is not None:
                self.land(line.card)
                (self.library if line.section == "main" else self.outside)[id(line.card)] += line.quantity

    def land(self, card):
        land = self.lands.get(id(card))
        if land is None:
            land = self.lands[id(card)] = _Land(card, self.identity, keep_fetches=self.lands_matter)
        return land

    def basic_colors(self, library):
        found = set()
        for counts in (library, self.outside):
            for key, copies in counts.items():
                if copies > 0:
                    found |= self.lands[key].basic_colors
        return frozenset(found)

    def buckets(self, library):
        """{color: [full, delayed, costly]} over the library."""
        basics = self.basic_colors(library)
        counts = {color: [0, 0, 0] for color in COLORS + "C"}
        for key, copies in library.items():
            if copies > 0:
                for color, timing in self.lands[key].profile(basics)[1].items():
                    counts[color][("full", "delayed", "costly").index(timing)] += copies
        return counts

    def effective_sources(self, buckets, color, turn):
        full, delayed, costly = buckets[color]
        return (full + delayed * _delayed_weight(turn) + costly * _costly_weight(turn)
                + self.rock_sources.get((color, min(turn, self.max_turn)), 0.0))

    def castability(self, buckets):
        if not self.nonland_copies:
            return 1.0
        total = 0.0
        for (turn, needs), copies in self.requirements.items():
            probability = 1.0
            for color, needed in needs:
                probability = min(probability, _probability(
                    self.deck_size, self.effective_sources(buckets, color, turn), _seen(turn), needed))
            total += probability * copies
        return total / self.nonland_copies

    def slow_units(self, library):
        return sum(copies * self.lands[key].tempo for key, copies in library.items() if copies > 0)

    def tempo_cost(self, library):
        if not self.deck_size:
            return 0.0
        return TEMPO_WEIGHT * min(1.0, TEMPO_CARDS / self.deck_size) * self.slow_units(library)

    def objective(self, library):
        buckets = self.buckets(library)
        castability = self.castability(buckets)
        return castability - self.tempo_cost(library), castability, buckets

    def color_odds(self, buckets):
        """P(at least `need` usable sources of each color by turn `need`), for colors the spells use."""
        odds = {}
        for color in COLORS + "C":
            if self.pips.get(color, 0) > 0:
                odds[color] = {str(need): _probability(self.deck_size, self.effective_sources(buckets, color, need),
                                                       _seen(need), need) for need in (1, 2, 3)}
        return odds

    def summary(self, library):
        score, castability, buckets = self.objective(library)
        basics = self.basic_colors(library)
        sources = Counter()
        for counts in (library, self.outside):
            for key, copies in counts.items():
                if copies > 0:
                    for source in self.lands[key].profile(basics)[0]:
                        sources[source] += copies
        every = Counter(library) + self.outside
        return {
            "score": round(score, 4),
            "castability": round(castability, 4),
            "tempo_cost": round(self.tempo_cost(library), 4),
            "by_color": {color: {need: round(p, 4) for need, p in odds.items()}
                         for color, odds in self.color_odds(buckets).items()},
            "sources": {key: sources.get(key, 0) for key in SOURCE_KEYS},
            "tapped_lands": sum(c for k, c in every.items()
                                if c > 0 and self.lands[k].is_land and self.lands[k].tapped),
            "slow_lands": sum(c for k, c in every.items() if c > 0 and self.lands[k].tempo > 0),
        }


def _all_text(card):
    faces = card.get("card_faces")
    if faces:
        return "\n".join(face.get("oracle_text") or "" for face in faces)
    return card.get("oracle_text") or ""


def _legal(card, deck_format):
    legalities = card.get("legalities") or {}
    if not deck_format or deck_format not in legalities:
        return True                      # casual or unknown formats have no card pool to check
    return legalities[deck_format] in ("legal", "restricted")


def _article(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _slash(colors):
    return "/".join(c for c in COLORS if c in colors)


def describe_land(land, basics):
    colors = land.colors(basics)
    keys = land.profile(basics)[0]
    pair = _slash(colors)
    if land.basic:
        return f"{_article(land.name)} {land.name}"
    if land.kind == "untapped":
        if "any" in keys or len(colors) >= 4:
            return f"an untapped land that makes any of your colors ({land.name})"
        if len(colors) == 1:
            return f"an untapped {COLOR_NAMES[next(iter(colors))]} source ({land.name})"
        return f"an untapped {pair} {'dual' if len(colors) == 2 else 'land'} ({land.name})"
    if land.kind == "fetch":
        return f"a fetch land for {pair} ({land.name})"
    if land.kind == "slow_fetch":
        return f"a slow fetch for {pair} ({land.name}; the land it finds enters tapped)"
    if land.kind == "costly_fetch":
        return f"{land.name}, which pays mana to fetch a basic"
    if land.kind == "tapped":
        return f"a {pair} land that enters tapped ({land.name})"
    return land.name


def _percent(value):
    return f"{round(100 * value)}%"


def _odds_phrase(color, need, before, after, rising):
    name = COLOR_NAMES[color]
    if need == "1":
        subject, verb = f"{name} on turn 1", "goes" if rising else "dips"
    else:
        subject, verb = f"{'two' if need == '2' else 'three'} {name} sources by turn {need}", "go" if rising else "dip"
    return f"{subject} {verb} from {_percent(before)} to {_percent(after)}"


def _note(model, removed, added, basics_before, basics_after, odds_before, odds_after, score_before, score_after):
    lead = describe_land(added, basics_after)
    lead = lead[0].upper() + lead[1:]
    if removed.basic:
        instead = f"instead of {_article(removed.name)} {removed.name}"
    else:
        instead = f"instead of {removed.name}, which enters tapped"
    changes = []
    for color, odds in odds_after.items():
        for need, after in odds.items():
            before = odds_before.get(color, {}).get(need, after)
            changes.append((after - before, color, need, before, after))
    changes.sort(key=lambda item: -item[0])
    parts = []
    if changes and changes[0][0] >= 0.005:
        _, color, need, before, after = changes[0]
        parts.append(_odds_phrase(color, need, before, after, True))
    if changes and changes[-1][0] <= -0.005:
        _, color, need, before, after = changes[-1]
        parts.append(_odds_phrase(color, need, before, after, False))
    if removed.tempo > added.tempo:
        parts.append("one fewer land that comes into play slow")
    elif added.tempo > removed.tempo:
        parts.append("it comes into play slow, which the gain has to pay for")
    if not parts:
        parts.append(f"castability goes from {_percent(score_before)} to {_percent(score_after)}")
    return f"{lead} {instead}: " + "; ".join(parts)


def suggest_swaps(cards, spare_lands, deck_format, color_identity, max_swaps=6, tag_index=None):
    """Greedy land swaps from spare copies, as the manafix JSON in docs/TOOLS.md (without images)."""
    deck_format = (deck_format or "").lower() or None
    identity = frozenset(c for c in color_identity if c in COLORS)
    ramp_tags = tag_index.expand("ramp") if tag_index is not None else DEFAULT_RAMP_TAGS
    playing = [line for line in cards if line.section in PLAYING_SECTIONS and line.quantity > 0]
    model = _Model(playing, identity, ramp_tags)
    library = Counter(model.library)
    in_deck = {line.oracle_id for line in cards if line.section != "maybeboard"}

    # Removable: basics, and plain (non-utility) lands that come into play slow.
    removable = []                  # [line, _Land, copies left]
    for line in playing:
        if line.section != "main" or not is_land(line.card):
            continue
        land = model.lands[id(line.card)]
        if land.basic or (land.tempo > 0 and not land.utility):
            removable.append([line, land, line.quantity])

    # Addable: spare lands in the identity, legal, making a deck color, new to the deck (basics excepted).
    basics_now = model.basic_colors(library)
    addable = []                    # [SpareLand, _Land, copies left]
    for spare in spare_lands:
        card = spare.card
        if spare.spare <= 0 or not is_land(card):
            continue
        if not is_basic_land(card) and spare.oracle_id in in_deck:
            continue
        if not set(card.get("color_identity") or ()) <= identity or not _legal(card, deck_format):
            continue
        land = model.land(card)
        if not land.colors(basics_now | land.basic_colors):
            continue
        addable.append([spare, land, spare.spare])

    before = model.summary(library)
    score, _, buckets = model.objective(library)
    swaps = []
    added_names = set()
    costly_in_deck = sum(c for k, c in (library + model.outside).items() if model.lands[k].kind == "costly_fetch")
    while len(swaps) < max_swaps:
        best = None
        basics = model.basic_colors(library)
        removal_groups = {}
        for index, (line, land, left) in enumerate(removable):
            if left > 0 and library[id(line.card)] > 0:
                removal_groups.setdefault(id(line.card), index)
        addition_groups = {}
        capacity = {}                    # group -> how many lands like it could come in (names for nonbasics)
        for index, (spare, land, left) in enumerate(addable):
            if left <= 0 or (not land.basic and card_name(spare.card) in added_names):
                continue
            if land.kind == "costly_fetch" and costly_in_deck >= MAX_COSTLY_FETCHES:
                continue
            keys, timing = land.profile(basics | land.basic_colors)
            group = (keys, tuple(sorted(timing.items())), land.kind, land.basic_colors)
            capacity.setdefault(group, Counter())[card_name(spare.card)] += left if land.basic else 1
            incumbent = addition_groups.get(group)
            if incumbent is None or _prefer(spare, addable[incumbent][0]):
                addition_groups[group] = index
        source_counts = {c: sum(b) for c, b in buckets.items()}
        for remove_index in removal_groups.values():
            removed = removable[remove_index][1]
            removed_colors = removed.colors(basics)
            for group, add_index in addition_groups.items():
                added = addable[add_index][1]
                if added.card is removed.card:
                    continue
                if not removed.basic:            # a slow land goes only for a strict upgrade
                    if added.tempo > 0 or not removed_colors <= added.colors(basics | added.basic_colors):
                        continue
                trial = Counter(library)
                trial[id(removed.card)] -= 1
                trial[id(added.card)] += 1
                trial_score, trial_castability, trial_buckets = model.objective(trial)
                gain = trial_score - score
                needed = COSTLY_MINIMUM_GAIN if added.kind == "costly_fetch" else MINIMUM_GAIN
                if gain < needed - TIE and added.kind != "costly_fetch":
                    # A deck far short of a color gains little from its first new source and more from
                    # the next ones; judge the swap by the average over up to three like it.
                    most = min(LOOKAHEAD, library[id(removed.card)] if removed.basic else 1,
                               sum(capacity[group].values()))
                    for count in range(2, most + 1):
                        ahead = Counter(library)
                        ahead[id(removed.card)] -= count
                        ahead[id(added.card)] += count
                        gain = max(gain, (model.objective(ahead)[0] - score) / count)
                if gain < needed - TIE:
                    continue
                # Ties: fewer slow lands, then cut the basic whose color has the most sources.
                over_served = max((source_counts.get(c, 0) for c in removed_colors), default=0)
                reliable = "any" not in added.profile(basics | added.basic_colors)[0]
                rank = (round(gain, 9), -model.slow_units(trial), reliable, over_served)
                if best is None or rank > best[0]:
                    best = (rank, remove_index, add_index, trial, trial_score, trial_castability, trial_buckets)
        if best is None:
            break
        _, remove_index, add_index, trial, trial_score, trial_castability, trial_buckets = best
        line, removed, _ = removable[remove_index]
        spare, added, _ = addable[add_index]
        basics_after = model.basic_colors(trial)
        note = _note(model, removed, added, basics, basics_after, model.color_odds(buckets),
                     model.color_odds(trial_buckets), score, trial_score)
        library, score, buckets = trial, trial_score, trial_buckets
        removable[remove_index][2] -= 1
        addable[add_index][2] -= 1
        added_names.add(card_name(spare.card))
        costly_in_deck += added.kind == "costly_fetch"
        swaps.append({
            "remove": {"line_id": line.line_id, "name": removed.name, "scryfall_id": line.card.get("id"),
                       "why": "basic" if removed.basic else "enters tapped", "kind": removed.kind},
            "add": {"name": added.name, "scryfall_id": spare.card.get("id"), "pool": spare.pool,
                    "finish": spare.pool.split("|", 1)[1] if "|" in spare.pool else "normal",
                    "price_usd": spare.price_usd, "spare": spare.spare, "kind": added.kind},
            "score_after": round(trial_score, 4),
            "castability_after": round(trial_castability, 4),
            "note": note,
        })

    message = None
    if not swaps and max_swaps > 0:
        message = _explain_nothing(model, library, removable, addable, deck_format, identity)
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


class _IdealLand:
    """A made-up untapped land for the "what would help" message."""

    def __init__(self, colors, identity):
        self.card = {"id": None, "name": "ideal", "type_line": "Land", "produced_mana": sorted(colors)}
        self.name, self.basic, self.tempo, self.kind, self.is_land = "ideal", False, 0, "untapped", True
        self.basic_colors = frozenset()
        self.identity = identity
        self._colors = frozenset(colors)

    def profile(self, basic_colors):
        keys = frozenset(self._colors & self.identity)
        return keys, {key: "full" for key in keys}


def _explain_nothing(model, library, removable, addable, deck_format, identity):
    if not removable:
        return "Every land in this deck is a nonbasic chosen on purpose, so there is nothing to swap out"
    basics_removable = [land for _, land, left in removable if land.basic and left > 0]
    score = model.objective(library)[0]
    best_gain, best_colors = 0.0, None
    for size in (2, 1):
        for colors in combinations(sorted(identity, key=COLORS.index), size):
            ideal = _IdealLand(colors, identity)
            model.lands[id(ideal)] = ideal
            for land in basics_removable:
                for count in range(1, min(LOOKAHEAD, library[id(land.card)]) + 1):
                    trial = Counter(library)
                    trial[id(land.card)] -= count
                    trial[id(ideal)] += count
                    gain = (model.objective(trial)[0] - score) / count
                    if gain > best_gain + TIE:
                        best_gain, best_colors = gain, colors
            del model.lands[id(ideal)]
    if best_colors is None or best_gain < MINIMUM_GAIN:
        if not addable:
            return "No spare lands fit this deck, and its mana is already good for its colors"
        return "Your spare lands wouldn't improve this deck: its mana is already good for its colors"
    names = "/".join(best_colors)
    if len(best_colors) == 2:
        what = f"untapped {names} duals"
    else:
        what = f"more untapped {COLOR_NAMES[best_colors[0]]} sources"
    return f"The lands that would help ({what}) aren't among your spare copies"
