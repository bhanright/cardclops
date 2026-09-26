"""Statistics for one deck list: curve, colors, mana sources, draw odds, functions, legality, price.

The output shape is the DeckStats object in docs/DECKS.md. Only the sections that
play count (commander, companion, main); the sideboard matters only for
constructed legality, and the maybeboard never does.

Decisions this module makes, so the numbers can be read correctly:

- A card's types and its category come from its front face ("Legendary Artifact
  Creature" is both an Artifact and a Creature).
- A modal double-faced card whose back face is a land (Emeria's Call // Emeria,
  Shattered Skyclave) is a spell for the curve, the types and the opening-hand
  land count, but it is a full mana source for the colors its land face makes,
  because it can be played as that land whenever the color is what you need.
  `counts.mdfc_lands` says how many there are.
- Fetchlands and other lands that search for a land ("a Mountain or Plains
  card", "a basic land card") have no produced_mana on Scryfall. They count as
  sources of every color they can fetch: the basic land types they name, or for
  "basic land" any color among the deck's basic lands.
- Lands that make all five colors (Command Tower, City of Brass) count under
  "any" and under each color of the deck's identity. Sources outside the
  identity are not shown: a Command Tower in a four-color deck is four sources
  and one "any", never a white source in a Sultai deck.
- Castability counts only symbols that demand one color ({U}, {2/U}, {U/P});
  a hybrid symbol can be paid with either color, so it is left out of the odds
  (its halves still count in `pips`).
- The library is the main deck. The commander starts in the command zone and a
  companion starts outside the game, so neither is ever drawn; both still count
  for pips, the curve and the functions.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

COLORS = "WUBRG"
TYPE_ORDER = ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land")
PERMANENT_TYPES = {"Creature", "Planeswalker", "Battle", "Artifact", "Enchantment", "Land"}
PLAYING_SECTIONS = ("commander", "companion", "main")
CURVE_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
# Layouts whose every half is a castable spell from hand; other faced layouts cast their front face.
EVERY_HALF_LAYOUTS = {"split", "adventure", "prepare"}
BASIC_TYPE_COLOR = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}

# Formats with a commander: the exact deck size including the commander(s).
COMMANDER_FORMAT_SIZES = {"commander": 100, "duel": 100, "paupercommander": 100, "predh": 100,
                          "brawl": 100, "standardbrawl": 60, "oathbreaker": 60}
# Constructed formats (60-card minimum, 15-card sideboard, four copies).
CONSTRUCTED_FORMATS = {"standard", "pioneer", "modern", "legacy", "vintage", "pauper", "historic", "timeless",
                       "alchemy", "explorer", "penny", "premodern", "oldschool", "future"}
OTHER_FORMATS = ("standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander", "brawl",
                 "historic", "timeless")

NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12}
MANA_SYMBOL = re.compile(r"\{([^}]+)\}")
ANY_NUMBER = re.compile(r"a deck can have any number of cards named", re.IGNORECASE)
UP_TO_N = re.compile(r"a deck can have up to (\w+) cards named", re.IGNORECASE)
LAND_SEARCH = re.compile(r"search your library for ([^.]*?)(?:,? put|\.|$)", re.IGNORECASE)
MANA_LETTER = re.compile(r"\{([WUBRGC])\}")
BASIC_TYPE_WORD = re.compile(r"\b(Plains|Island|Swamp|Mountain|Forest)s?\b")


@dataclass
class DeckCard:
    section: str             # commander | companion | main | sideboard | maybeboard
    quantity: int
    card: dict               # Scryfall card object for the printing
    oracle_id: str
    tags: frozenset          # Tagger oracle-tag slugs for this card
    price_usd: float | None  # per copy (the copy the deck uses, or the cheapest printing if missing)
    line_id: int | None = None   # the deck line this came from, when there is one


# ---- Reading a Scryfall card ------------------------------------------------------------------

def front_face(card):
    faces = card.get("card_faces")
    return faces[0] if faces else card


def card_name(card):
    """The name deck rules count by; reversible cards repeat their name on both faces."""
    if card.get("layout") == "reversible_card" and card.get("card_faces"):
        return card["card_faces"][0].get("name") or card.get("name", "")
    return card.get("name", "")


def front_type_line(card):
    faces = card.get("card_faces")
    if faces and faces[0].get("type_line"):
        return faces[0]["type_line"]
    return card.get("type_line") or ""


def card_types(card):
    """The card types on the front face, in TYPE_ORDER."""
    main_part = front_type_line(card).split("—")[0]
    words = set(main_part.split())
    return [kind for kind in TYPE_ORDER if kind in words]


def category_of(card):
    """One category per card for DeckLine.category and stacked charts: the first type in TYPE_ORDER."""
    types = card_types(card)
    return types[0] if types else "Other"


def mana_value(card):
    """Reversible cards keep cmc only on their faces; everything else has it on the card."""
    if card.get("cmc") is not None:
        return float(card["cmc"])
    return float(front_face(card).get("cmc") or 0)


def is_land(card):
    return "Land" in card_types(card)


def is_basic_land(card):
    main_part = front_type_line(card).split("—")[0].split()
    return "Basic" in main_part and "Land" in main_part


def land_back_face(card):
    """The back face of a modal DFC when that face is a land and the front is not."""
    faces = card.get("card_faces") or []
    if card.get("layout") == "modal_dfc" and len(faces) > 1 and not is_land(card):
        back_types = (faces[1].get("type_line") or "").split("—")[0].split()
        if "Land" in back_types:
            return faces[1]
    return None


def oracle_text(card):
    faces = card.get("card_faces")
    if faces:
        return "\n".join(face.get("oracle_text") or "" for face in faces)
    return card.get("oracle_text") or ""


def spell_costs(card):
    """The mana costs that count for pips: every half of split-style cards, else the front face."""
    faces = card.get("card_faces")
    if faces and card.get("layout") in EVERY_HALF_LAYOUTS:
        return [face.get("mana_cost") or "" for face in faces]
    if faces:
        return [faces[0].get("mana_cost") or ""]
    return [card.get("mana_cost") or ""]


def display_cost(card):
    return " // ".join(cost for cost in spell_costs(card) if cost)


@lru_cache(maxsize=4096)
def parse_cost(cost):
    """Returns (pips, strict): pips per docs/DECKS.md, and the symbols that demand exactly one color.

    {W} is 1 W; {W/U} is 1/2 to each; {2/W} and {W/P} are 1 W; {G/U/P} is 1/2 to each;
    {C} is 1 C; half mana {HW} is 1/2 W; generic, X, Y, Z and snow count nowhere.
    Only {W}, {2/W}, {W/P} and {C} go into `strict`, which castability uses.
    """
    pips = Counter()
    strict = Counter()
    for symbol in MANA_SYMBOL.findall(cost.upper()):
        if symbol in COLORS or symbol == "C":
            pips[symbol] += 1
            strict[symbol] += 1
        elif "/" in symbol:
            parts = [part for part in symbol.split("/") if part != "P"]
            colors = [part for part in parts if part in COLORS or part == "C"]
            if not colors:
                continue
            if len(colors) == 1:        # twobrid {2/W} or Phyrexian {W/P}
                pips[colors[0]] += 1
                strict[colors[0]] += 1
            else:                        # hybrid, including Phyrexian hybrid {G/U/P}
                for color in colors:
                    pips[color] += 1 / len(colors)
        elif len(symbol) == 2 and symbol[0] == "H" and symbol[1] in COLORS:
            pips[symbol[1]] += 0.5
    return dict(pips), dict(strict)


def card_pips(card):
    pips, strict = Counter(), Counter()
    for cost in spell_costs(card):
        if cost:
            cost_pips, cost_strict = parse_cost(cost)
            pips.update(cost_pips)
            strict.update(cost_strict)
    return pips, strict


def produced_colors(card):
    """Colors (WUBRG plus C) a card can make for any spell, from Scryfall's produced_mana.

    Scryfall lists every color a card could ever make, so Cavern of Souls, Aether Hub and
    Unknown Shores all read as five-color. Only mana abilities that work for any spell count:
    - not mana that can only be spent on some spells ("Spend this mana only to cast a Dragon spell"),
    - on a land, not mana that depends on other permanents or cards ("any color that a Gate you
      control could produce", "among legendary creature cards in your graveyard", "Activate only
      if ..."), nor an ability whose cost is more than tapping, life and colored mana: filters that
      cost generic mana ("{1}, {T}"), energy, counters, a sacrifice or tapping a creature.
      Rocks and dorks keep such abilities: a Signet's "{1}, {T}: Add {W}{B}" is how a rock works.
    """
    # Keyed by what the answer depends on, not the id alone: synthetic cards in tests
    # reuse ids, and a cache keyed by id would hand one card another's colors.
    key = (card.get("id"), card.get("oracle_text"), tuple(card.get("produced_mana") or ()),
           tuple(face.get("oracle_text", "") for face in card.get("card_faces") or ()))
    cached = _PRODUCED_CACHE.get(key)
    if cached is not None:
        return set(cached)
    produced = card.get("produced_mana")
    if produced is None:
        produced = [color for face in card.get("card_faces") or [] for color in face.get("produced_mana") or []]
    produced = {color for color in produced if color in COLORS or color == "C"}
    if produced:
        usable = _usable_mana_colors(card)
        if usable is not None:
            produced &= usable
    _PRODUCED_CACHE[key] = frozenset(produced)
    return produced


_PRODUCED_CACHE = {}          # (id, text, produced_mana) -> produced colors
RESTRICTED_MANA = re.compile(r"spend this mana only", re.IGNORECASE)
CONDITIONAL_MANA = re.compile(r"activate only|could produce|\bamong\b|exiled", re.IGNORECASE)
REMINDER_TEXT = re.compile(r"\([^)]*\)")
PLAIN_LAND_COST = re.compile(r"^(\{T\}|Pay \d+ life|(\{[WUBRG](/[WUBRG])?\})+)$")


def _usable_mana_colors(card):
    """Colors from the card's own unconditional mana abilities, or None when its text shows none."""
    land = is_land(card) or land_back_face(card) is not None
    found_ability = False
    colors = set()
    for paragraph in oracle_text(card).splitlines():
        cut = paragraph.find(": Add ")
        if not paragraph.startswith("{") or cut < 0 or '"' in paragraph[:cut]:
            continue                      # not an ability of this card (granted abilities are quoted)
        found_ability = True
        cost, effect = paragraph[:cut], REMINDER_TEXT.sub("", paragraph[cut + len(": Add "):])
        if RESTRICTED_MANA.search(effect):
            continue
        if land and (CONDITIONAL_MANA.search(effect)
                     or not all(PLAIN_LAND_COST.match(part.strip()) for part in cost.split(","))):
            continue
        colors |= set(MANA_LETTER.findall(effect))
        if "color" in effect:             # any color, the chosen color, your commander's color identity
            colors |= set(COLORS)
    return colors if found_ability else None


def fetched_colors(card, basic_colors):
    """Colors a land can find with "search your library for ...", or None if it does not search.

    A named basic land type ("a Mountain or Plains card") gives that color; "a basic land card"
    gives any color among the deck's basic lands.
    """
    text = oracle_text(card)
    found = set()
    searches = False
    for match in LAND_SEARCH.finditer(text):
        target = match.group(1)
        if "land" not in target.lower() and not BASIC_TYPE_WORD.search(target):
            continue
        searches = True
        named = {BASIC_TYPE_COLOR[word] for word in BASIC_TYPE_WORD.findall(target)}
        if named:
            found |= named
        elif "basic land" in target.lower() or "land card" in target.lower():
            found |= basic_colors
    return found if searches else None


def land_source_keys(card, basic_colors, identity):
    """What a land (or a land-backed modal DFC) counts toward in `sources`.

    Returns (keys, colors, fetches): the source keys it adds to (colors within the identity,
    "C", and "any" for all five colors), every color it can make or fetch, and whether it is a fetch.
    The mana-base fixer uses this too, so its source counts always match DeckStats.
    """
    produced = produced_colors(card)
    colors = {color for color in produced if color in COLORS}
    fetched = fetched_colors(card, basic_colors) if (is_land(card) and not colors) else None
    if fetched is not None:
        colors = fetched
    keys = colors & identity
    if "C" in produced:
        keys.add("C")
    if len(colors) == 5:
        keys.add("any")
    return keys, colors, fetched is not None


def nonland_source_keys(card, identity):
    """What a mana rock or dork counts toward in `nonland_sources` (the caller checks its ramp tag)."""
    produced = produced_colors(card)
    keys = {color for color in produced if color in identity}
    if "C" in produced:
        keys.add("C")
    if len(produced & set(COLORS)) == 5:
        keys.add("any")
    return keys


def is_nonland_source(card, tags, ramp_tags):
    """A permanent spell that makes mana and is tagged as ramp; land-backed MDFCs count as lands instead."""
    return (not is_land(card) and land_back_face(card) is None and bool(produced_colors(card))
            and bool(PERMANENT_TYPES & set(card_types(card))) and bool(tags & ramp_tags))


def copy_limit(card, singleton):
    """How many copies of this name a deck may hold; None for no limit."""
    if is_basic_land(card):
        return None
    text = oracle_text(card)
    if ANY_NUMBER.search(text):
        return None
    match = UP_TO_N.search(text)
    if match:
        word = match.group(1).lower()
        return NUMBER_WORDS.get(word) or (int(word) if word.isdigit() else None)
    return 1 if singleton else 4


# ---- Probability ------------------------------------------------------------------------------

@lru_cache(maxsize=8192)
def hypergeometric_exactly(deck_size, successes, drawn, wanted):
    """P(exactly `wanted` successes among `drawn` cards of a `deck_size` library holding `successes`)."""
    if wanted < 0 or wanted > drawn or wanted > successes or drawn - wanted > deck_size - successes:
        return 0.0
    return (math.comb(successes, wanted) * math.comb(deck_size - successes, drawn - wanted)
            / math.comb(deck_size, drawn))


@lru_cache(maxsize=8192)
def hypergeometric_at_least(deck_size, successes, drawn, wanted):
    """P(at least `wanted` successes); draws are capped at the library size."""
    if wanted <= 0:
        return 1.0
    if deck_size <= 0:
        return 0.0
    drawn = min(drawn, deck_size)
    successes = min(successes, deck_size)
    below = sum(hypergeometric_exactly(deck_size, successes, drawn, k) for k in range(wanted))
    return max(0.0, min(1.0, 1.0 - below))


def cards_seen_on_the_play(turn):
    return 7 + turn - 1


# ---- The statistics ---------------------------------------------------------------------------

def _expand(tag_index, slug):
    return tag_index.expand(slug) if tag_index is not None else frozenset({slug})


# Tutor tags that say where a found card goes or how it is chosen, not what kind of card it is:
# a land tutor such as Cultivate carries tutor-to-hand too, so these never make a card a tutor alone.
TUTOR_MODIFIER_TAGS = {"tutor-mv", "tutor-self", "tutor-cast", "take-the-initiative"}


def _is_nonland_tutor_tag(tag):
    """Commander brackets mean tutors for spells; fetchlands and land searches (ramp) are not tutors."""
    if tag.startswith("tutor-to") or tag in TUTOR_MODIFIER_TAGS:
        return False
    return not any(word in tag for word in ("land", "fetchland", "panorama", "landscape"))


def _share(values):
    total = sum(values.get(color, 0) for color in COLORS)
    return {color: round(values.get(color, 0) / total, 4) if total else 0.0 for color in COLORS}


def _round(value):
    return round(value, 4)


def _curve_key(value):
    bucket = int(value)
    return "7+" if bucket >= 7 else str(bucket)


def compute_deck_stats(cards, deck_format, tag_index):
    """DeckStats for a list of DeckCard (every section; only commander, companion and main play)."""
    deck_format = (deck_format or "").lower() or None
    playing = [line for line in cards if line.section in PLAYING_SECTIONS and line.quantity > 0]
    sideboard = [line for line in cards if line.section == "sideboard" and line.quantity > 0]
    commanders = [line for line in playing if line.section == "commander"]
    library = [line for line in playing if line.section == "main"]

    # Per-card facts, computed once.
    facts = {}
    for line in cards:
        card = line.card
        if id(card) in facts:
            continue
        facts[id(card)] = {
            "name": card_name(card), "types": card_types(card), "land": is_land(card),
            "basic": is_basic_land(card), "mv": mana_value(card), "pips": card_pips(card),
            "produced": produced_colors(card), "land_back": land_back_face(card) is not None,
        }

    # The deck's colors: the commanders' identity in commander formats, else every playing card's.
    identity_lines = commanders if (deck_format in COMMANDER_FORMAT_SIZES and commanders) else playing
    identity = {color for line in identity_lines for color in line.card.get("color_identity") or ()}

    # ---- Counts, types, curve
    counts = Counter()
    types = Counter()
    curve = {key: 0 for key in CURVE_KEYS}
    curve_by_type = {}
    rarity = Counter()
    mv_all = mv_nonland = 0.0
    for line in playing:
        fact, quantity = facts[id(line.card)], line.quantity
        counts["cards"] += quantity
        rarity[line.card.get("rarity") or "unknown"] += quantity
        for kind in fact["types"] or ["Other"]:
            types[kind] += quantity
        if "Creature" in fact["types"]:
            counts["creatures"] += quantity
        mv_all += fact["mv"] * quantity
        if fact["land"]:
            counts["lands"] += quantity
            if fact["basic"]:
                counts["basic_lands"] += quantity
            continue
        counts["nonlands"] += quantity
        if fact["land_back"]:
            counts["mdfc_lands"] += quantity
        mv_nonland += fact["mv"] * quantity
        key = _curve_key(fact["mv"])
        curve[key] += quantity
        category = fact["types"][0] if fact["types"] else "Other"
        curve_by_type.setdefault(category, {k: 0 for k in CURVE_KEYS})[key] += quantity
    counts["sideboard"] = sum(line.quantity for line in sideboard)
    count_keys = ("cards", "lands", "nonlands", "creatures", "basic_lands", "mdfc_lands", "sideboard")

    # ---- Pips
    pips = Counter()
    for line in playing:
        for color, amount in facts[id(line.card)]["pips"][0].items():
            pips[color] += amount * line.quantity
    pips_out = {color: _round(pips.get(color, 0)) for color in COLORS + "C"}

    # ---- Mana sources
    basic_colors = {color for line in playing if facts[id(line.card)]["basic"]
                    for color in facts[id(line.card)]["produced"] if color in COLORS}
    sources = Counter()
    library_sources = Counter()      # the same, over the library only, for the draw odds
    land_kinds = Counter({key: 0 for key in ("basic", "mono", "dual", "tri", "five_color", "colorless", "fetch")})
    for line in playing:
        fact, quantity = facts[id(line.card)], line.quantity
        if not (fact["land"] or fact["land_back"]):
            continue
        counted, colors, fetches = land_source_keys(line.card, basic_colors, identity)
        for key in counted:
            sources[key] += quantity
            if line.section == "main":
                library_sources[key] += quantity
        if fact["land"]:
            if fact["basic"]:
                land_kinds["basic"] += quantity
            elif fetches:
                land_kinds["fetch"] += quantity
            else:
                land_kinds[{0: "colorless", 1: "mono", 2: "dual", 3: "tri"}.get(len(colors), "five_color")] += quantity

    ramp_tags = _expand(tag_index, "ramp")
    nonland_sources = Counter()
    library_nonland_sources = Counter()
    for line in playing:
        if line.section == "commander" or not is_nonland_source(line.card, line.tags, ramp_tags):
            continue
        for key in nonland_source_keys(line.card, identity):
            nonland_sources[key] += line.quantity
            if line.section == "main":
                library_nonland_sources[key] += line.quantity

    source_keys = tuple(COLORS) + ("C", "any")
    sources_out = {key: sources.get(key, 0) for key in source_keys}
    nonland_out = {key: nonland_sources.get(key, 0) for key in source_keys}
    combined = {color: sources.get(color, 0) + nonland_sources.get(color, 0) for color in COLORS}
    draw_sources = {color: library_sources.get(color, 0) + library_nonland_sources.get(color, 0)
                    for color in COLORS + "C"}

    # ---- Castability and the opening hand
    deck_size = sum(line.quantity for line in library)
    library_lands = sum(line.quantity for line in library if facts[id(line.card)]["land"])

    by_color = {}
    for color in COLORS + "C":
        if pips.get(color, 0) > 0:
            by_color[color] = {str(need): _round(hypergeometric_at_least(
                deck_size, draw_sources[color], cards_seen_on_the_play(need), need)) for need in (1, 2, 3)}

    hardest = {}
    for line in playing:
        fact = facts[id(line.card)]
        if fact["land"] or fact["name"] in hardest:
            continue
        turn = max(int(fact["mv"]), 1)
        worst = None
        for color, needed in fact["pips"][1].items():
            probability = hypergeometric_at_least(deck_size, draw_sources[color], cards_seen_on_the_play(turn), needed)
            if worst is None or probability < worst["probability"]:
                worst = {"name": fact["name"], "mana_cost": display_cost(line.card), "turn": turn,
                         "color": color, "needed": needed, "probability": probability}
        if worst:
            hardest[fact["name"]] = worst
    hardest_list = sorted(hardest.values(), key=lambda item: (item["probability"], item["name"]))[:10]
    for item in hardest_list:
        item["probability"] = _round(item["probability"])

    lands_in_seven = {str(k): _round(hypergeometric_exactly(deck_size, library_lands, min(7, deck_size), k))
                      for k in range(8)}
    opening_hand = {
        "deck_size": deck_size,
        "lands": library_lands,
        "lands_in_seven": lands_in_seven,
        "two_to_four": _round(sum(hypergeometric_exactly(deck_size, library_lands, min(7, deck_size), k)
                                  for k in (2, 3, 4))),
        "expected_land_drops": {str(turn): _round(hypergeometric_at_least(
            deck_size, library_lands, cards_seen_on_the_play(turn), turn)) for turn in range(1, 7)},
    }

    # ---- Functions (Tagger tags with descendants)
    sweeper_tags = _expand(tag_index, "sweeper")
    tutor_tags = frozenset(tag for tag in _expand(tag_index, "tutor") if _is_nonland_tutor_tag(tag))
    function_tags = {
        "ramp": ramp_tags,
        "card_draw": _expand(tag_index, "draw") | _expand(tag_index, "card-advantage"),
        "removal": _expand(tag_index, "removal"),
        "board_wipes": sweeper_tags,
        "counterspells": _expand(tag_index, "counterspell"),
        "tutors": tutor_tags,
        "protection": _expand(tag_index, "protection"),
        "recursion": _expand(tag_index, "recursion"),
    }
    functions = {key: set() for key in function_tags}
    extra_turns, mass_land_denial, game_changers = set(), set(), set()
    extra_turn_tags = _expand(tag_index, "extra-turn")
    land_denial_tags = _expand(tag_index, "mass-land-denial")
    for line in playing:
        fact, tags = facts[id(line.card)], line.tags
        for key, wanted in function_tags.items():
            if not (tags & wanted):
                continue
            # Lands that fetch or make mana are mana base, not ramp or tutors; sweepers are board wipes.
            if key in ("ramp", "tutors") and fact["land"]:
                continue
            if key == "removal" and tags & sweeper_tags:
                continue
            functions[key].add(fact["name"])
        if tags & extra_turn_tags:
            extra_turns.add(fact["name"])
        if tags & land_denial_tags:
            mass_land_denial.add(fact["name"])
        if line.card.get("game_changer"):
            game_changers.add(fact["name"])

    # ---- Price
    total = 0.0
    prices = {}
    for line in playing:
        price = line.price_usd or 0.0
        total += price * line.quantity
        name = facts[id(line.card)]["name"]
        if line.price_usd is not None and line.price_usd > prices.get(name, -1):
            prices[name] = line.price_usd
    most_expensive = sorted(prices.items(), key=lambda item: (-item[1], item[0]))[:5]

    return {
        "counts": {key: counts.get(key, 0) for key in count_keys},
        "types": {kind: types[kind] for kind in TYPE_ORDER + ("Other",) if types.get(kind)},
        "average_mv": {
            "nonland": round(mv_nonland / counts["nonlands"], 2) if counts["nonlands"] else 0.0,
            "all": round(mv_all / counts["cards"], 2) if counts["cards"] else 0.0,
        },
        "curve": curve,
        "curve_by_type": {kind: curve_by_type[kind] for kind in TYPE_ORDER + ("Other",) if kind in curve_by_type},
        "pips": pips_out,
        "pip_share": _share(pips),
        "sources": sources_out,
        "nonland_sources": nonland_out,
        "source_share": _share(combined),
        "land_kinds": dict(land_kinds),
        "castability": {"by_color": by_color, "hardest": hardest_list},
        "opening_hand": opening_hand,
        "functions": {key: sorted(names) for key, names in functions.items()},
        "bracket_signals": {
            "game_changers": sorted(game_changers),
            "extra_turns": sorted(extra_turns),
            "mass_land_denial": sorted(mass_land_denial),
            "tutors": sorted(functions["tutors"]),
        },
        "legality": check_legality(playing, sideboard, commanders, deck_format, facts),
        "rarity": dict(rarity.most_common()),
        "price": {
            "total_usd": round(total, 2),
            "average_usd": round(total / counts["cards"], 2) if counts["cards"] else 0.0,
            "most_expensive": [{"name": name, "price_usd": price} for name, price in most_expensive],
        },
    }


# ---- Legality ---------------------------------------------------------------------------------

def _is_legendary(card):
    return "Legendary" in front_type_line(card).split("—")[0].split()


def _commander_problems(commanders, deck_format, facts):
    """Errors for commanders that plainly cannot lead; warnings where the rules are too varied to be sure."""
    problems, warnings = [], []
    if not commanders:
        problems.append({"name": None, "reason": "no commander"})
        return problems, warnings
    if deck_format == "oathbreaker":        # an oathbreaker plus a signature spell; not checked further
        return problems, warnings
    names = [facts[id(line.card)]["name"] for line in commanders]
    if len(commanders) > 2 or sum(line.quantity for line in commanders) > 2:
        problems.append({"name": None, "reason": f"{len(commanders)} commanders; at most two may lead a deck"})
        return problems, warnings
    for line, name in zip(commanders, names):
        card = line.card
        types = facts[id(card)]["types"]
        text = oracle_text(card).lower()
        if "can be your commander" in text:
            continue
        if len(commanders) == 2 and "Background" in front_type_line(card):
            continue                          # checked with its partner below
        if _is_legendary(card) and ("Creature" in types or (deck_format in ("brawl", "standardbrawl")
                                                            and "Planeswalker" in types)):
            continue
        if _is_legendary(card):
            warnings.append({"name": name, "reason": "legendary but not a creature; check it can be a commander"})
        else:
            problems.append({"name": name, "reason": "not a legendary creature, so it cannot be a commander"})
    if len(commanders) == 2:
        first, second = (line.card for line in commanders)

        def keywords(card):
            return {word.lower() for word in card.get("keywords") or ()}

        def pairs(card, other):
            words, text = keywords(card), oracle_text(card).lower()
            other_words = keywords(other)
            if any(word.startswith("partner") for word in words) and any(w.startswith("partner") for w in other_words):
                return True
            if "friends forever" in words and "friends forever" in other_words:
                return True
            if "choose a background" in words or "choose a background" in text:
                return "Background" in front_type_line(other)
            if "doctor's companion" in words or "doctor's companion" in text:
                return "Doctor" in front_type_line(other)
            return False

        if not (pairs(first, second) or pairs(second, first)):
            warnings.append({"name": " and ".join(names),
                             "reason": "no partner, background or companion pairing found between the two commanders"})
    return problems, warnings


def check_legality(playing, sideboard, commanders, deck_format, facts):
    other_formats = {}
    for fmt in OTHER_FORMATS:
        other_formats[fmt] = all((line.card.get("legalities") or {}).get(fmt) in ("legal", "restricted")
                                 for line in playing)
    result = {"format": deck_format, "legal": True, "problems": [], "warnings": [], "other_formats": other_formats}
    commander_format = deck_format in COMMANDER_FORMAT_SIZES
    if not (commander_format or deck_format in CONSTRUCTED_FORMATS):
        return result                         # casual or unknown: no deck rules to check

    problems, warnings = [], []
    checked = playing + (sideboard if not commander_format else [])
    copies = Counter()
    first_line = {}
    for line in checked:
        name = facts[id(line.card)]["name"]
        copies[name] += line.quantity
        first_line.setdefault(name, line)

    # Card legality in this format
    for name, line in first_line.items():
        status = (line.card.get("legalities") or {}).get(deck_format)
        if status == "banned":
            problems.append({"name": name, "reason": "banned"})
        elif status == "not_legal":
            problems.append({"name": name, "reason": f"not legal in {deck_format}"})
        elif status == "restricted" and copies[name] > 1:
            problems.append({"name": name, "reason": f"restricted; {copies[name]} copies, 1 allowed"})

    # Deck size
    if commander_format:
        size = sum(line.quantity for line in playing if line.section in ("commander", "main"))
        needed = COMMANDER_FORMAT_SIZES[deck_format]
        if size != needed:
            problems.append({"name": None, "reason": f"{size} cards; {deck_format} decks need exactly {needed}"})
    else:
        main_size = sum(line.quantity for line in playing if line.section in ("main", "commander"))
        side_size = sum(line.quantity for line in sideboard) + sum(
            line.quantity for line in playing if line.section == "companion")   # a companion takes a sideboard slot
        if main_size < 60:
            problems.append({"name": None, "reason": f"{main_size} cards; {deck_format} decks need at least 60"})
        if side_size > 15:
            problems.append({"name": None, "reason": f"{side_size}-card sideboard; at most 15 allowed"})

    # Copy limits
    already = {problem["name"] for problem in problems if problem["reason"].startswith("restricted")}
    for name, count in copies.items():
        limit = copy_limit(first_line[name].card, singleton=commander_format)
        if limit is not None and count > limit and name not in already:
            problems.append({"name": name, "reason": f"{count} copies; {limit} allowed"})

    # Commander rules: eligibility and color identity
    if commander_format:
        commander_errors, commander_warnings = _commander_problems(commanders, deck_format, facts)
        problems += commander_errors
        warnings += commander_warnings
        if commanders:
            identity = {color for line in commanders for color in line.card.get("color_identity") or ()}
            outside = set()
            for line in playing:
                if line.section == "commander":
                    continue
                if not set(line.card.get("color_identity") or ()) <= identity:
                    outside.add(facts[id(line.card)]["name"])
            for name in sorted(outside):
                problems.append({"name": name, "reason": "outside the commander's color identity"})

    result["problems"] = problems
    result["warnings"] = warnings
    result["legal"] = not problems
    return result
