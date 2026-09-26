"""Goldfish simulator: plays a deck alone many times and reports how it runs.

The response is the goldfish object in docs/TOOLS2.md. This is a model, not a
rules engine; every simplification it makes is listed in ASSUMPTIONS, which the
response repeats so the numbers can be read correctly. In short:

- London mulligan with a fixed keep rule; the first mulligan is free in
  multiplayer Commander formats.
- Each turn: untap, draw, play the land that best serves the hand, then cast
  spells greedily (the commander first, then ramp, then the most expensive
  spell that can be paid).
- Payment is exact: colored symbols are matched to mana sources as a bipartite
  matching (checked with Hall's condition), so a spell is cast only when the
  lands and rocks in play can really make its colors.
- Only mana matters. Ramp spells fetch lands or add mana sources, cantrips draw
  a card, and every other effect is ignored.

Randomness comes from one random.Random(seed), so a seed always gives the same
result for the same list.
"""
import random
import re
import statistics
import time
from collections import Counter

from .deckstats import (BASIC_TYPE_COLOR, COLORS, LAND_SEARCH, MANA_SYMBOL, NUMBER_WORDS, card_name, card_types,
                        fetched_colors, front_face, front_type_line, is_basic_land, is_land, is_nonland_source,
                        land_back_face, mana_value, oracle_text, produced_colors, spell_costs)
from .manafix import DEFAULT_RAMP_TAGS, SENTENCE_BREAK, UNCONDITIONAL_TAPPED

# Mana is a bit mask: one bit per color, plus colorless. A source's mask is every color it can make;
# a cost symbol's mask is every color that can pay it.
COLOR_BIT = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16, "C": 32}
FIVE_COLORS = 31
BIT_COUNT = [bin(mask).count("1") for mask in range(64)]

# Formats whose first mulligan is free (multiplayer Commander and Brawl); Duel Commander has none.
FREE_MULLIGAN_FORMATS = {"commander", "paupercommander", "predh", "oathbreaker", "brawl", "standardbrawl"}
# Formats whose 100-card decks use the stricter seven-card keep rule.
HUNDRED_CARD_FORMATS = {"commander", "duel", "paupercommander", "predh", "brawl"}
# Ramp tags that do not make a card worth casting first: a ritual's mana is not modeled.
NOT_PRIORITY_RAMP = {"ritual"}
DEFAULT_LAND_RAMP_TAGS = frozenset({"land-ramp", "multi-land-ramp"})

EXTRA_LAND_DROP = re.compile(r"play an additional land", re.IGNORECASE)
LAND_FROM_HAND = re.compile(r"put an? (?:basic )?land card from your hand onto the battlefield", re.IGNORECASE)
MANA_AMOUNT_WORD = re.compile(r"\b(one|two|three|four|five) mana\b", re.IGNORECASE)
AMOUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
GENERIC_IN_COST = re.compile(r"\{(\d+)\}")
UP_TO_COUNT = re.compile(r"^(?:up to )?(\w+)\b", re.IGNORECASE)
MANA_ABILITY_COST = re.compile(r"^\{(T|\d+|[WUBRGC])\}$")
REMINDER = re.compile(r"\([^)]*\)")

ASSUMPTIONS = [
    "London mulligan: draw 7 and bottom one card per mulligan; keep 7 cards with 2–5 lands (100-card Commander "
    "decks: 3–5, or 2 with a ramp spell of mana value 3 or less or two spells of mana value 2 or less), "
    "keep 6 with 2–4, always keep 5. The kept hand is "
    "judged after bottoming; modal double-faced cards with a land back count as lands for this.",
    "Bottoming puts back excess lands (beyond 3, the least useful first), then the most expensive spells.",
    "The first mulligan is free in multiplayer Commander and Brawl (draw 7 again, bottom nothing).",
    "Each turn: draw (not on turn 1 on the play), play one land, then cast spells greedily: the commander when "
    "it can be paid, then ramp (Tagger 'ramp' and its descendants, except rituals), then the most expensive "
    "spell that can be paid, the lower mana value first on ties. The hand is discarded down to 7 at end of turn.",
    "The land played is the one that adds colors the hand needs; an untapped land is preferred when a spell "
    "could use the mana this turn, a tapped one otherwise. Modal double-faced cards are played as their land "
    "face only when no other land is in hand.",
    "Lands whose text makes them enter tapped with no condition enter tapped; conditional ones enter untapped.",
    "Every land makes one mana (bounce lands and Ancient Tomb are undercounted).",
    "Fetch lands are sacrificed at once for the land in the library that best fits the hand (a basic, or any "
    "land of a named basic type), tapped if the fetch says so or the found land always enters tapped.",
    "Mana rocks and dorks (a mana-producing permanent tagged as ramp) add their mana as soon as they resolve; "
    "creatures and rocks that enter tapped wait a turn. Only abilities that cost tapping and mana count, net "
    "of the mana they cost: a Signet makes one mana of its colors, Sol Ring two. Mana from sacrifices, energy, "
    "Treasures and other tokens is ignored.",
    "Land-ramp spells (Tagger 'land-ramp') fetch the best-fitting land from the library: onto the battlefield "
    "(tapped when the text says so), with the second of two to hand when the text says 'the other into your "
    "hand'. 'Play an additional land' and 'put a land card from your hand onto the battlefield' are modeled. "
    "These count only on an instant or sorcery or in a 'when this enters' trigger, and happen once.",
    "Colored costs must be paid from sources that make those colors: hybrid symbols by either color, "
    "Phyrexian symbols by their color or one generic mana, {2/W} by W or two generic, {C} only by colorless "
    "mana, snow by any mana, and X as 1.",
    "Split and adventure cards can be cast as either half; a card with no mana cost is never cast.",
    "Cantrips (Tagger 'cantrip') draw one card; all other card draw, and every other effect, is ignored. The "
    "commander is cast at most once (no removal, so no commander tax); companions are not cast.",
    "Mana available counts untapped lands and usable rocks after the land drop, plus mana from rocks cast "
    "that turn; efficiency is total mana spent over total mana available.",
    "Screw: 2 or fewer lands in play at the end of turn 4. Flood: 7 or more lands in play and at most 1 card in "
    "hand at the end of turn 7. Color screw: at the end of turn 4, a spell in hand (or the uncast commander) "
    "costs no more than that turn's mana but its colors cannot be paid.",
]


# ---- Reading costs ----------------------------------------------------------------------------

def _symbol(symbol):
    """One cost symbol as ("generic", amount), ("color", mask, alternative generic or None), or None."""
    symbol = symbol.upper()
    if symbol.isdigit():
        return ("generic", int(symbol))
    if symbol in COLOR_BIT:
        return ("color", COLOR_BIT[symbol], None)
    if symbol in ("X", "Y", "Z", "S"):          # X is cast as 1; snow mana is modeled as any mana
        return ("generic", 1)
    if "/" in symbol:
        parts = symbol.split("/")
        mask = 0
        for part in parts:
            mask |= COLOR_BIT.get(part, 0)
        if not mask:
            return None
        if "P" in parts:                         # Phyrexian: its color, or 2 life, modeled as one generic
            return ("color", mask, 1)
        digits = [int(part) for part in parts if part.isdigit()]
        return ("color", mask, digits[0] if digits else None)
    return None                                  # half mana and anything else count for nothing


def _hall_constraints(symbols):
    """(union of masks, demand) for every set of distinct symbol masks: Hall's condition for the matching."""
    groups = Counter(symbols)
    masks = list(groups)
    constraints = []
    for subset in range(1, 1 << len(masks)):
        union = demand = 0
        for index, mask in enumerate(masks):
            if subset >> index & 1:
                union |= mask
                demand += groups[mask]
        constraints.append((union, demand))
    # The largest unions first: they fail most often, so an unpayable cost is rejected sooner.
    constraints.sort(key=lambda item: -item[1])
    return tuple(constraints)


class _Variant:
    """One way to pay a cost: `generic` mana of any kind plus one mana matching each of `symbols`."""
    __slots__ = ("total", "generic", "symbols", "constraints")

    def __init__(self, generic, symbols):
        self.symbols = tuple(sorted(symbols, key=lambda mask: (BIT_COUNT[mask], mask)))   # least flexible first
        self.generic = generic
        self.total = generic + len(self.symbols)
        self.constraints = _hall_constraints(self.symbols)


def cost_variants(costs):
    """Every way to pay any of the given mana cost strings, cheapest first; [] when none can be cast."""
    variants = []
    for cost in costs:
        if not cost:
            continue
        generic = 0
        fixed, optional = [], []
        for text in MANA_SYMBOL.findall(cost):
            parsed = _symbol(text)
            if parsed is None:
                continue
            if parsed[0] == "generic":
                generic += parsed[1]
            elif parsed[2] is None:
                fixed.append(parsed[1])
            else:
                optional.append((parsed[1], parsed[2]))
        for choice in range(1 << len(optional)):
            symbols, extra = list(fixed), 0
            for index, (mask, alternative) in enumerate(optional):
                if choice >> index & 1:
                    extra += alternative
                else:
                    symbols.append(mask)
            variants.append(_Variant(generic + extra, symbols))
    variants.sort(key=lambda variant: (variant.total, -len(variant.symbols)))
    return variants


# ---- Paying -----------------------------------------------------------------------------------

def can_pay(pool, pool_total, variant):
    """True when the mana in `pool` (mask -> units) pays `variant`."""
    if pool_total < variant.total:
        return False
    for union, demand in variant.constraints:
        supply = 0
        for mask, count in pool.items():
            if mask & union:
                supply += count
        if supply < demand:
            return False
    return True


def _matchable(pool, symbols):
    return can_pay(pool, sum(pool.values()), _Variant(0, symbols)) if symbols else True


def pay(pool, variant, keep_mask=0):
    """Takes `variant`'s mana out of `pool` (which must be able to pay it), keeping the most useful mana.

    Each colored symbol takes the least flexible unit that still leaves the rest payable; generic mana
    comes from units whose colors the rest of the hand (`keep_mask`) needs least.
    """
    symbols = variant.symbols
    for index, symbol in enumerate(symbols):
        rest = symbols[index + 1:]
        options = sorted((mask for mask, count in pool.items() if count and mask & symbol),
                         key=lambda mask: (BIT_COUNT[mask], BIT_COUNT[mask & keep_mask], mask))
        for mask in options:
            pool[mask] -= 1
            if _matchable(pool, rest):
                break
            pool[mask] += 1
        else:
            raise ValueError("the pool cannot pay this cost")
    need = variant.generic
    while need:
        mask = min((mask for mask, count in pool.items() if count),
                   key=lambda mask: (BIT_COUNT[mask & keep_mask], BIT_COUNT[mask], mask))
        taken = min(need, pool[mask])
        pool[mask] -= taken
        need -= taken


# ---- Per-card facts ---------------------------------------------------------------------------

def _mask(colors):
    mask = 0
    for color in colors:
        mask |= COLOR_BIT.get(color, 0)
    return mask


def _always_tapped(text):
    return any(UNCONDITIONAL_TAPPED.match(sentence.strip()) for sentence in SENTENCE_BREAK.split(text or ""))


def _basic_type_mask(type_line):
    subtypes = (type_line or "").split("—")[1].split() if "—" in (type_line or "") else []
    return _mask(BASIC_TYPE_COLOR[word] for word in subtypes if word in BASIC_TYPE_COLOR)


def _search_spec(text):
    """What a "search your library for ..." land search finds: (count, basic-type mask or None, basic only)."""
    for match in LAND_SEARCH.finditer(text):
        target = match.group(1)
        lowered = target.lower()
        types = _mask(BASIC_TYPE_COLOR[word] for word in re.findall(r"\b(Plains|Island|Swamp|Mountain|Forest)",
                                                                     target))
        if "land" not in lowered and not types:
            continue
        count_word = UP_TO_COUNT.match(lowered)
        count = NUMBER_WORDS.get(count_word.group(1), 1) if count_word else 1
        return count, (types or None), "basic" in lowered
    return None


def _mana_ability(card):
    """(colors mask, mana per turn) from a rock's or dork's own repeatable mana abilities, or None.

    Only abilities whose cost is tapping and mana count; the mana is net of any generic mana the
    ability costs, so a Signet makes one. Abilities that cost energy, life, a sacrifice or a discard,
    and mana from tokens the card makes, are left out.
    """
    mask, best = 0, 0
    for paragraph in oracle_text(card).splitlines():
        cut = paragraph.find(": Add ")
        if not paragraph.startswith("{") or cut < 0 or '"' in paragraph[:cut]:
            continue
        cost, effect = paragraph[:cut], paragraph[cut + len(": Add "):]
        if not all(MANA_ABILITY_COST.match(part.strip()) for part in cost.split(",")):
            continue
        effect = REMINDER.sub("", effect).split(".")[0]
        if "spend this mana only" in paragraph.lower():
            continue
        word = MANA_AMOUNT_WORD.search(effect)
        if word:
            made = AMOUNT_WORDS[word.group(1).lower()]
        else:
            made = max(len(re.findall(r"\{[WUBRGC]\}", option)) for option in re.split(r",| or ", effect))
        paid = sum(int(amount) for amount in GENERIC_IN_COST.findall(cost))
        if made - paid < 1:
            continue
        best = max(best, made - paid)
        mask |= _mask(re.findall(r"\{([WUBRGC])\}", effect))
        if "color" in effect:                  # any color, any one color, your commander's color identity
            mask |= FIVE_COLORS
    return (mask, best) if best else None


def _on_arrival_text(card, types):
    """The text that happens once when the card is cast: all of an instant or sorcery, else "When ... enters"."""
    text = oracle_text(card)
    if "Instant" in types or "Sorcery" in types:
        return text
    return "\n".join(paragraph for paragraph in text.splitlines()
                     if paragraph.startswith("When ") and " enters" in paragraph)


class _Kind:
    """One distinct card of the deck, with everything the simulation needs precomputed."""
    __slots__ = ("name", "mv", "playable_land", "true_land", "mdfc", "basic", "subtypes", "land_mask",
                 "color_mask", "tapped", "fetch", "variants", "cheapest", "want", "ramp", "rock", "land_ramp",
                 "extra_drop", "land_from_hand", "cantrip", "priority", "commander", "name_index")


class _Model:
    def __init__(self, cards, deck_format, tag_index):
        ramp_tags = tag_index.expand("ramp") if tag_index is not None else DEFAULT_RAMP_TAGS
        land_ramp_tags = tag_index.expand("land-ramp") if tag_index is not None else DEFAULT_LAND_RAMP_TAGS
        priority_ramp = frozenset(ramp_tags) - NOT_PRIORITY_RAMP
        lines = [line for line in cards if line.quantity > 0 and line.section in ("commander", "main")]
        basic_colors = {color for line in lines if line.section == "main" and is_basic_land(line.card)
                        for color in produced_colors(line.card) if color in COLORS}

        self.kinds = []
        self.names = []                  # distinct names of nonland cards, for the per-card table
        self.name_mv = []
        name_index = {}
        self.library = []
        self.commanders = []
        for line in lines:
            kind = self._kind(line, basic_colors, ramp_tags, priority_ramp, land_ramp_tags)
            if not kind.true_land:
                if kind.name not in name_index:
                    name_index[kind.name] = len(self.names)
                    self.names.append(kind.name)
                    self.name_mv.append(kind.mv)
                kind.name_index = name_index[kind.name]
            index = len(self.kinds)
            self.kinds.append(kind)
            if line.section == "commander":
                kind.commander = True
                kind.priority = (0, 0, 0)
                self.commanders.append(index)
            else:
                self.library.extend([index] * line.quantity)
        self.hundred_card = deck_format in HUNDRED_CARD_FORMATS and len(self.library) >= 90
        self.keep_sizes = (7, 7, 6, 5) if deck_format in FREE_MULLIGAN_FORMATS else (7, 6, 5)

    @staticmethod
    def _kind(line, basic_colors, ramp_tags, priority_ramp, land_ramp_tags):
        card, tags = line.card, line.tags or frozenset()
        kind = _Kind()
        kind.name = card_name(card)
        kind.mv = mana_value(card)
        kind.commander = False
        kind.name_index = -1
        types = card_types(card)
        back = land_back_face(card)
        kind.true_land = is_land(card)
        kind.mdfc = back is not None
        kind.playable_land = kind.true_land or kind.mdfc
        kind.basic = is_basic_land(card)
        land_face = back if back is not None else front_face(card)
        kind.subtypes = _basic_type_mask(front_type_line(card) if kind.true_land else land_face.get("type_line"))
        kind.land_mask = _mask(produced_colors(card)) if kind.playable_land else 0
        kind.color_mask = kind.land_mask
        kind.tapped = kind.playable_land and _always_tapped(land_face.get("oracle_text"))
        kind.fetch = None
        if kind.true_land and not kind.land_mask:       # a land that makes mana itself is played for its mana
            fetched = fetched_colors(card, basic_colors)
            spec = _search_spec(oracle_text(card)) if fetched is not None else None
            if spec is not None:
                kind.fetch = (spec[1], spec[2], "onto the battlefield tapped" in oracle_text(card))
                kind.color_mask = _mask(fetched)
        kind.variants = [] if kind.true_land else cost_variants(spell_costs(card))
        kind.cheapest = kind.variants[0].total if kind.variants else 99
        kind.want = 0
        if kind.variants:
            for symbol in max(kind.variants, key=lambda variant: len(variant.symbols)).symbols:
                kind.want |= symbol
        kind.ramp = not kind.true_land and bool(tags & priority_ramp)
        kind.rock = None
        ability = _mana_ability(card) if not kind.true_land and is_nonland_source(card, tags, ramp_tags) else None
        if ability:
            ready_now = "Creature" not in types and not _always_tapped(front_face(card).get("oracle_text"))
            kind.rock = (ability[0] or _mask(produced_colors(card)), ability[1], ready_now)
        kind.land_ramp = None
        text = _on_arrival_text(card, types)
        if not kind.true_land and tags & land_ramp_tags:
            spec = _search_spec(text)
            if spec is not None:
                count, types_mask, basic_only = spec
                to_hand = count - 1 if "the other into your hand" in text or "the other card into your hand" in text \
                    else (count if "onto the battlefield" not in text else 0)
                kind.land_ramp = (count - to_hand, to_hand, types_mask, basic_only,
                                  "onto the battlefield tapped" in text)
        kind.extra_drop = not kind.true_land and bool(EXTRA_LAND_DROP.search(text))
        kind.land_from_hand = not kind.true_land and bool(LAND_FROM_HAND.search(text))
        kind.cantrip = not kind.true_land and "cantrip" in tags
        if kind.ramp:
            kind.priority = (1, kind.cheapest, kind.mv)
        else:
            kind.priority = (2, -kind.cheapest, kind.mv)
        return kind


# ---- One game ---------------------------------------------------------------------------------

class _Game:
    def __init__(self, model, rng, log):
        self.model = model
        self.kinds = model.kinds
        self.rng = rng
        self.log = log
        self.uncast = list(model.commanders)
        self.base = Counter()        # mana sources usable from the start of each turn: mask -> units
        self.pending = []            # (mask, units) that become usable next turn
        self.pool = Counter()        # what is left to spend this turn
        self.pool_total = 0
        self.available = 0
        self.in_main = False
        self.have = 0                # every color the sources in play can make
        self.lands = 0
        self.first_cast = {}         # name index -> turn
        self.cast_names = []
        self.commander_turn = None

    # -- Mulligans
    def mulligan(self):
        model = self.model
        library = list(model.library)
        sizes = model.keep_sizes
        for attempt, size in enumerate(sizes):
            self.rng.shuffle(library)
            hand = library[-7:]
            rest = library[:-7]
            bottomed = self._bottom(hand, 7 - size) if size < 7 else []
            if attempt == len(sizes) - 1 or self._keeps(hand, size):
                break
        self.hand = hand
        self.library = bottomed + rest       # the top of the library is its end
        return attempt, len(hand)

    def _bottom(self, hand, count):
        kinds = self.kinds
        bottomed = []
        for _ in range(count):
            lands = [k for k in hand if kinds[k].playable_land]
            spells = [k for k in hand if not kinds[k].playable_land]
            if len(lands) > 3 or not spells:
                worst = min(lands, key=lambda k: (BIT_COUNT[kinds[k].color_mask], not kinds[k].tapped, k))
            else:
                worst = max(spells, key=lambda k: (kinds[k].cheapest, kinds[k].mv, k))
            hand.remove(worst)
            bottomed.append(worst)
        return bottomed

    def _keeps(self, hand, size):
        kinds = self.kinds
        lands = sum(1 for k in hand if kinds[k].playable_land)
        if size >= 7:
            if self.model.hundred_card:
                if 3 <= lands <= 5:
                    return True
                # Most players keep a two-lander that can do things early: a ramp spell
                # they can cast by turn 3, or two plays costing 2 or less.
                spells = [kinds[k] for k in hand if not kinds[k].playable_land]
                return lands == 2 and (any(kind.ramp and kind.cheapest <= 3 for kind in spells)
                                       or sum(1 for kind in spells if kind.cheapest <= 2) >= 2)
            return 2 <= lands <= 5
        if size == 6:
            return 2 <= lands <= 4
        return True

    # -- Helpers
    def draw(self):
        if self.library:
            self.hand.append(self.library.pop())

    def wanted(self):
        """Every color the spells in hand and the uncast commander need."""
        kinds = self.kinds
        mask = 0
        for k in self.hand:
            mask |= kinds[k].want
        for k in self.uncast:
            mask |= kinds[k].want
        return mask

    def add_source(self, mask, units, ready_now):
        self.have |= mask
        if not ready_now:
            self.pending.append((mask, units))
            return
        self.base[mask] += units
        if self.in_main:
            self.pool[mask] += units
            self.pool_total += units
            self.available += units

    def search(self, types_mask, basic_only):
        """Takes the land that best fits the hand out of the library, or None."""
        kinds = self.kinds
        wanted = self.wanted()
        missing = wanted & ~self.have
        best = best_score = None
        for position, k in enumerate(self.library):
            kind = kinds[k]
            if not kind.true_land or kind.fetch or (basic_only and not kind.basic):
                continue
            if types_mask is not None and not kind.subtypes & types_mask:
                continue
            mask = kind.land_mask
            score = (BIT_COUNT[mask & missing], BIT_COUNT[mask & wanted], not kind.tapped, BIT_COUNT[mask])
            if best_score is None or score > best_score:
                best, best_score = position, score
        return self.library.pop(best) if best is not None else None

    def put_land(self, k, tapped=False):
        """A land enters the battlefield (a fetch land is sacrificed at once for what it finds)."""
        kind = self.kinds[k]
        self.lands += 1
        if kind.fetch:
            types_mask, basic_only, fetch_tapped = kind.fetch
            found = self.search(types_mask, basic_only)
            if found is None:
                return kind.name
            self.add_source(self.kinds[found].land_mask, 1, not (fetch_tapped or tapped or self.kinds[found].tapped))
            return f"{kind.name} ({self.kinds[found].name})"
        self.add_source(kind.land_mask, 1, not (tapped or kind.tapped))
        return kind.name

    def play_land(self):
        """Plays the land from hand that serves the hand best; returns its log name or None."""
        kinds, hand = self.kinds, self.hand
        candidates = [k for k in dict.fromkeys(hand) if kinds[k].true_land]
        if not candidates:
            candidates = [k for k in dict.fromkeys(hand) if kinds[k].mdfc]
        if not candidates:
            return None
        if len(candidates) > 1:
            wanted = self.wanted()
            missing = wanted & ~self.have
            mana_now = self.pool_total if self.in_main else sum(self.base.values())
            cheapest = min([kinds[k].cheapest for k in hand if not kinds[k].true_land]
                           + [kinds[k].cheapest for k in self.uncast] + [99])
            want_untapped = cheapest <= mana_now + 1

            def score(k):
                kind = kinds[k]
                tapped = kind.tapped or (kind.fetch is not None and kind.fetch[2])
                return (BIT_COUNT[kind.color_mask & missing] * 100 + BIT_COUNT[kind.color_mask & wanted] * 10
                        + BIT_COUNT[kind.color_mask] + (50 if tapped != want_untapped else 0), -k)
            candidates.sort(key=score, reverse=True)
        chosen = candidates[0]
        hand.remove(chosen)
        return self.put_land(chosen)

    # -- Casting
    def cast_spells(self, turn):
        kinds, hand = self.kinds, self.hand
        while True:
            options = [k for k in dict.fromkeys(hand) if kinds[k].variants and not kinds[k].true_land]
            options += self.uncast
            options.sort(key=lambda k: kinds[k].priority)
            for k in options:
                kind = kinds[k]
                if kind.cheapest > self.pool_total:
                    continue
                for variant in kind.variants:
                    if can_pay(self.pool, self.pool_total, variant):
                        break
                else:
                    continue
                self.resolve(k, variant, turn)
                break
            else:
                return

    def resolve(self, k, variant, turn):
        kind = self.kinds[k]
        if kind.commander:
            self.uncast.remove(k)
            if not self.uncast:
                self.commander_turn = turn
        else:
            self.hand.remove(k)
        pay(self.pool, variant, self.wanted())
        self.pool_total -= variant.total
        self.spent += variant.total
        self.first_cast.setdefault(kind.name_index, turn)
        if self.log is not None:
            self.cast_names.append(kind.name)
        if kind.rock:
            mask, units, ready_now = kind.rock
            self.add_source(mask, units, ready_now)
        if kind.land_ramp:
            to_battlefield, to_hand, types_mask, basic_only, tapped = kind.land_ramp
            for _ in range(to_battlefield):
                found = self.search(types_mask, basic_only)
                if found is not None:
                    self.put_land(found, tapped=tapped)
            for _ in range(to_hand):
                found = self.search(types_mask, basic_only)
                if found is not None:
                    self.hand.append(found)
        if (kind.land_from_hand or kind.extra_drop) and any(self.kinds[other].true_land for other in self.hand):
            self.play_land()            # an extra land drop is used at once, or not at all
        if kind.cantrip:
            self.draw()

    def color_screwed(self, full_pool, full_total):
        kinds = self.kinds
        for k in list(dict.fromkeys(self.hand)) + self.uncast:
            kind = kinds[k]
            if kind.true_land or not kind.variants or kind.cheapest > full_total:
                continue
            if not any(can_pay(full_pool, full_total, variant) for variant in kind.variants
                       if variant.total <= full_total):
                return True
        return False

    def discard_to_seven(self):
        kinds, hand = self.kinds, self.hand
        while len(hand) > 7:
            lands = [k for k in hand if kinds[k].true_land]
            if len(lands) >= 2:
                hand.remove(min(lands, key=lambda k: (BIT_COUNT[kinds[k].color_mask], k)))
            else:
                hand.remove(max((k for k in hand if not kinds[k].true_land),
                                key=lambda k: (kinds[k].cheapest, kinds[k].mv, k)))

    def take_turn(self, turn, on_the_play):
        for mask, units in self.pending:
            self.base[mask] += units
        self.pending = []
        if not (turn == 1 and on_the_play):
            self.draw()
        hand_size = len(self.hand)
        self.cast_names = []
        self.in_main = False
        land = self.play_land()
        self.pool = Counter({mask: units for mask, units in self.base.items() if units})
        self.pool_total = self.available = sum(self.pool.values())
        self.spent = 0
        self.in_main = True
        full_pool, full_total = (dict(self.pool), self.pool_total) if turn == 4 else (None, 0)
        self.cast_spells(turn)
        self.in_main = False
        self.discard_to_seven()
        if self.log is not None:
            self.log.append({"turn": turn, "hand_size": hand_size, "land": land, "cast": self.cast_names,
                             "mana": self.available, "spent": self.spent})
        return full_pool, full_total


# ---- The simulation ---------------------------------------------------------------------------

def _median_turn(cumulative):
    for turn, share in enumerate(cumulative, start=1):
        if share >= 0.5:
            return turn
    return None


def _number(value):
    return int(value) if float(value).is_integer() else value


def simulate(cards, deck_format, tag_index, games=2000, turns=10, seed=1, on_the_play=True):
    """Goldfishes the deck `games` times for `turns` turns; returns the goldfish object of docs/TOOLS2.md."""
    started = time.perf_counter()
    games = max(1, int(games))
    turns = max(1, int(turns))
    deck_format = (deck_format or "").lower()
    model = _Model(cards, deck_format, tag_index)
    rng = random.Random(seed)

    mulligans = 0
    hand_sizes = Counter()
    lands_sum = [0] * turns
    mana_sum = [0] * turns
    spent_sum = [0] * turns
    commander_turns = Counter()
    screws = floods = color_screws = 0
    cast_turns = [[] for _ in model.names]
    sample = []

    for game_number in range(games):
        game = _Game(model, rng, sample if game_number == 0 else None)
        attempts, kept = game.mulligan()
        mulligans += attempts > 0
        hand_sizes[kept] += 1
        for turn in range(1, turns + 1):
            full_pool, full_total = game.take_turn(turn, on_the_play)
            lands_sum[turn - 1] += game.lands
            mana_sum[turn - 1] += game.available
            spent_sum[turn - 1] += game.spent
            if turn == 4:
                screws += game.lands <= 2
                color_screws += game.color_screwed(full_pool, full_total)
            elif turn == 7:
                floods += game.lands >= 7 and len(game.hand) <= 1
        if model.commanders and game.commander_turn is not None:
            commander_turns[game.commander_turn] += 1
        for name_index, turn in game.first_cast.items():
            cast_turns[name_index].append(turn)

    commander = None
    if model.commanders:
        cumulative, running = [], 0
        for turn in range(1, turns + 1):
            running += commander_turns.get(turn, 0)
            cumulative.append(running / games)
        commander = {
            "name": " and ".join(model.kinds[k].name for k in model.commanders),
            "cast_by_turn": {str(turn): round(share, 4) for turn, share in enumerate(cumulative, start=1)},
            "median_turn": _median_turn(cumulative),
        }

    card_rows = [{"name": name, "mana_value": _number(model.name_mv[index]),
                  "cast_rate": round(len(cast_turns[index]) / games, 4),
                  "median_turn": statistics.median_low(cast_turns[index]) if cast_turns[index] else None}
                 for index, name in enumerate(model.names)]
    card_rows.sort(key=lambda row: (-row["cast_rate"], row["median_turn"] or 99, row["name"]))

    total_mana, total_spent = sum(mana_sum), sum(spent_sum)
    return {
        "games": games, "turns": turns, "seed": seed, "on_the_play": bool(on_the_play),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "mulligans": {
            "rate": round(mulligans / games, 4),
            "average_hand_size": round(sum(size * count for size, count in hand_sizes.items()) / games, 2),
            "by_size": {str(size): round(hand_sizes.get(size, 0) / games, 4) for size in (7, 6, 5)},
        },
        "lands_by_turn": {str(turn + 1): round(value / games, 2) for turn, value in enumerate(lands_sum)},
        "mana_by_turn": {str(turn + 1): round(value / games, 2) for turn, value in enumerate(mana_sum)},
        "spent_by_turn": {str(turn + 1): round(value / games, 2) for turn, value in enumerate(spent_sum)},
        "efficiency": round(total_spent / total_mana, 4) if total_mana else 0.0,
        "commander": commander,
        "screw_rate": round(screws / games, 4) if turns >= 4 else None,
        "flood_rate": round(floods / games, 4) if turns >= 7 else None,
        "color_screw_rate": round(color_screws / games, 4) if turns >= 4 else None,
        "cards": card_rows,
        "sample_game": sample,
        "assumptions": list(ASSUMPTIONS),
    }
