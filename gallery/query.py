"""Scryfall's search syntax, evaluated against the collection's entries.

    q = compile_query('c:b mv<3 otag:removal -t:land', collection.tag_index)
    hits = [entry for entry in collection.entries if q.matches(entry)]

The grammar follows https://scryfall.com/docs/syntax: terms are joined by an
implicit AND, `or` binds looser than AND, parentheses group, a leading `-`
negates a term or a group, bare words and quoted strings search the name, and
`!name` is an exact name.  Keywords take `:` `=` `!=` `<` `<=` `>` `>=` where
the comparison makes sense.

Each entry is one printing in one finish, so print-level keywords (set,
rarity, prices, is:foil, year) test the printing the collection holds, where
Scryfall tests every printing of the card.  A few keywords are ours rather
than Scryfall's: qty, cond, added, paid and gain read the holding itself.

Display keywords (order:, direction:, unique:, prefer:, display:) are not
filters; they come back on the Query for the caller to apply.
"""
import difflib
import operator
import re
import unicodedata
import weakref
from collections import Counter
from datetime import date

__all__ = ["QueryError", "Query", "compile_query", "prepare"]


class QueryError(ValueError):
    """A query that cannot be understood; the message is written for the person who typed it."""

    def __init__(self, message, position=None):
        self.position = position
        if position is not None:
            message = f"{message} (at character {position + 1})"
        super().__init__(message)


# ---------------------------------------------------------------------------
# Vocabulary

COLOR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}

COLOR_WORDS = {
    "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G",
    # guilds
    "azorius": "WU", "dimir": "UB", "rakdos": "BR", "gruul": "RG", "selesnya": "GW",
    "orzhov": "WB", "izzet": "UR", "golgari": "BG", "boros": "RW", "simic": "GU",
    # shards
    "bant": "GWU", "esper": "WUB", "grixis": "UBR", "jund": "BRG", "naya": "RGW",
    # wedges
    "abzan": "WBG", "jeskai": "URW", "sultai": "BGU", "mardu": "RWB", "temur": "GUR",
    # Strixhaven colleges
    "lorehold": "RW", "prismari": "UR", "quandrix": "GU", "silverquill": "WB", "witherbloom": "BG",
    # four colors, named for what they leave out
    "chaos": "UBRG", "aggression": "WBRG", "altruism": "WURG", "growth": "WUBG", "artifice": "WUBR",
    "glint": "UBRG", "dune": "WBRG", "ink": "WURG", "witch": "WUBG", "yore": "WUBR",
    "rainbow": "WUBRG", "fivecolor": "WUBRG",
}

RARITY_ORDER = {"common": 0, "uncommon": 1, "rare": 2, "special": 3, "mythic": 4, "bonus": 5}
RARITY_ABBREVIATIONS = {"c": "common", "u": "uncommon", "r": "rare", "s": "special", "m": "mythic", "b": "bonus"}

FORMATS = (
    "standard", "future", "historic", "timeless", "gladiator", "pioneer", "modern", "legacy",
    "pauper", "vintage", "penny", "commander", "oathbreaker", "standardbrawl", "brawl",
    "competitivebrawl", "alchemy", "paupercommander", "duel", "oldschool", "premodern", "predh", "tlr",
)
FORMAT_ALIASES = {"edh": "commander", "pdh": "paupercommander", "duelcommander": "duel",
                  "pennydreadful": "penny", "historicbrawl": "brawl"}

# ManaBox (Cardmarket) grades from worst to best, with the usual abbreviations.
CONDITION_ORDER = ("poor", "played", "light_played", "good", "excellent", "near_mint", "mint")
CONDITION_ALIASES = {
    "m": "mint", "mt": "mint", "nm": "near_mint", "nearmint": "near_mint", "ex": "excellent",
    "gd": "good", "lp": "light_played", "lightplayed": "light_played", "lightlyplayed": "light_played",
    "pl": "played", "mp": "played", "hp": "played", "po": "poor", "dmg": "poor", "damaged": "poor",
}

LANGUAGES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de", "italian": "it",
    "portuguese": "pt", "japanese": "ja", "korean": "ko", "russian": "ru", "chinese": "zhs",
    "simplifiedchinese": "zhs", "traditionalchinese": "zht", "hebrew": "he", "latin": "la",
    "greek": "grc", "arabic": "ar", "sanskrit": "sa", "phyrexian": "ph", "quenya": "qya",
}

PERMANENT_TYPES = frozenset({"artifact", "creature", "enchantment", "land", "planeswalker", "battle"})
SPELL_TYPES = frozenset({"artifact", "creature", "enchantment", "planeswalker", "battle",
                         "instant", "sorcery", "kindred", "tribal"})
TOKEN_LAYOUTS = frozenset({"token", "double_faced_token", "emblem"})

# Self-references in current Oracle wording ("this creature") stand in for ~ as well as the name.
SELF_REFERENCE = re.compile(
    r"\bthis (?:creature|artifact|enchantment|land|planeswalker|battle|spell|card|permanent|aura|"
    r"equipment|vehicle|saga|class|case|siege|room|token|kindred|spacecraft|planet|attraction|"
    r"contraption|dungeon|plane|scheme|phenomenon|conspiracy|siege|background|role|food|clue|treasure)\b")

# promo_types and frame_effects Scryfall exposes as is: flags (compared without underscores).
PROMO_TYPES = frozenset({
    "alchemy", "arenaleague", "beginnerbox", "boosterfun", "boxtopper", "brawldeck", "bringafriend",
    "bundle", "buyabox", "commanderparty", "concept", "confettifoil", "convention", "datestamped",
    "dossier", "doublerainbow", "draculaseries", "draftweekend", "duels", "embossed", "event",
    "ffi", "ffii", "ffiii", "ffiv", "ffv", "ffvi", "ffvii", "ffviii", "ffix", "ffx", "ffxi", "ffxii",
    "ffxiii", "ffxiv", "ffxv", "ffxvi", "firstplacefoil", "fnm", "fracturefoil", "galaxyfoil",
    "gameday", "giftbox", "gilded", "glossy", "godzillaseries", "halofoil", "imagine", "instore",
    "intropack", "invisibleink", "jpwalker", "judgegift", "league", "magnified", "manafoil",
    "mediainsert", "moonlitland", "neonink", "oilslick", "openhouse", "planeswalkerdeck",
    "playerrewards", "playpromo", "playtest", "portrait", "poster", "premiereshop", "prerelease",
    "promopack", "rainbowfoil", "raisedfoil", "ravnicacity", "rebalanced", "release", "resale",
    "ripplefoil", "schinesealtart", "scroll", "serialized", "setextension", "setpromo", "silverfoil",
    "sldbonus", "sourcematerial", "stamped", "startercollection", "starterdeck", "stepandcompleat",
    "storechampionship", "surgefoil", "textured", "themepack", "thick", "tourney", "universesbeyond",
    "upsidedown", "vault", "wizardsplaynetwork",
})
FRAME_EFFECTS = frozenset({
    "legendary", "miracle", "enchantment", "draft", "devoid", "tombstone", "colorshifted",
    "inverted", "sunmoondfc", "compasslanddfc", "originpwdfc", "mooneldrazidfc", "waxingandwaningmoondfc",
    "showcase", "extendedart", "companion", "etched", "snow", "lesson", "shatteredglass",
    "convertdfc", "fandfc", "upsidedowndfc", "spree", "fullart",
})
FRAMES = frozenset({"1993", "1997", "2003", "2015", "future"})

UNIQUE_MODES = frozenset({"cards", "prints", "art"})
ORDER_FIELDS = frozenset({
    "artist", "cmc", "mv", "manavalue", "power", "toughness", "set", "name", "usd", "tix", "eur",
    "rarity", "color", "released", "spoiled", "edhrec", "penny", "review", "imageupdated",
    # ours
    "qty", "quantity", "added", "paid", "gain", "value", "condition", "number", "used", "spare",
})
DESCENDING_BY_DEFAULT = frozenset({"usd", "eur", "tix", "released", "spoiled", "rarity",
                                   "added", "gain", "value", "qty", "quantity", "paid", "used", "spare"})

# Real Scryfall keywords this engine does not evaluate: they are dropped with a warning.
UNSUPPORTED_KEYS = frozenset({
    "devotion", "cube", "in", "new", "b", "block", "g", "group", "prints", "sets", "paperprints",
    "papersets", "cheapest", "art", "atag", "arttag", "stamp", "illustrations", "artists",
    "penny", "pennyrank", "flavorname",
})


# ---------------------------------------------------------------------------
# Text helpers

def _fold(text):
    """Lower case, accents removed, curly quotes straightened: how names and text are compared."""
    if not text:
        return ""
    if not text.isascii():
        text = text.replace("\u2019", "'").replace("\u2018", "'")
        text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return text.lower()


_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_REMINDER = re.compile(r"\([^()]*\)")
_MANA_SYMBOL = re.compile(r"\{([^}]+)\}")
_WORD = re.compile(r"[a-z']+")


def _loose(text):
    """Name with punctuation dropped, so "urzas saga" still finds Urza's Saga."""
    return " ".join(_PUNCTUATION.sub("", text).split())


def _with_cleave_variants(text):
    """Cleave text ("Destroy target [attacking] creature") is searchable with the bracketed
    words kept or dropped, as on Scryfall: both readings are appended to the text."""
    if "[" not in text:
        return text
    kept = text.replace("[", "").replace("]", "")
    dropped = re.sub(r" ?\[[^\]]*\]", "", text)
    return f"{kept}\n{dropped}"


def _strip_reminder(text):
    previous = None
    while previous != text:
        previous, text = text, _REMINDER.sub("", text)
    return text


def _normalize_symbol(symbol):
    symbol = symbol.upper()
    if "/" in symbol:
        return "/".join(sorted(symbol.split("/")))
    return symbol


def _mana_counter(cost):
    """{2}{W}{W} -> {'#': 2, 'W': 2}; generic mana counts under '#'."""
    counts = Counter()
    for symbol in _MANA_SYMBOL.findall(cost or ""):
        if symbol.isdigit():
            counts["#"] += int(symbol)
        else:
            counts[_normalize_symbol(symbol)] += 1
    return dict(counts)            # {0} stays as {'#': 0}: a cost, just an empty one


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# A per-printing view of what searching needs, computed lazily and cached

class _View:
    """Folded and parsed forms of one card's data. Attributes are computed on first use."""

    def __init__(self, entry):
        # Copies of what the lazy fields read, not the entry itself, so the cache
        # never keeps an entry alive.
        self.card = entry.card
        self.raw_name = entry.name
        self.raw_oracle = entry.oracle_text
        self.raw_type = entry.type_line
        self.raw_mana = entry.mana_cost
        self.raw_artist = entry.artist
        self.raw_keywords = entry.keywords
        self.raw_colors = entry.colors

    def __getattr__(self, name):
        compute = getattr(type(self), "_" + name, None)
        if compute is None:
            raise AttributeError(name)
        value = compute(self)
        setattr(self, name, value)
        return value

    def _faces(self):
        return self.card.get("card_faces") or [self.card]

    def _name(self):
        return _fold(self.raw_name)

    def _names(self):
        names = {self.name}
        names.update(_fold(face.get("name", "")) for face in self.faces if face.get("name"))
        return names

    def _name_loose(self):
        return _loose(self.name)

    def _full_oracle(self):
        return _with_cleave_variants(_fold(self.raw_oracle))

    def _oracle(self):
        return _strip_reminder(self.full_oracle)

    def _self_names(self):
        # Longest first so "Fire // Ice" is replaced before "Fire".
        names = set(self.names)
        for name in list(names):
            if "," in name:                     # legendary short names: "Jace, the Mind Sculptor" -> "Jace"
                names.add(name.split(",")[0])
        return sorted((n for n in names if n), key=len, reverse=True)

    def _tilde(self, text):
        for name in self.self_names:
            text = text.replace(name, "~")
        return SELF_REFERENCE.sub("~", text)

    def _oracle_tilde(self):
        return self._tilde(self.oracle)

    def _full_oracle_tilde(self):
        return self._tilde(self.full_oracle)

    def _front_oracle(self):
        """The front face's rules text, reminder text removed."""
        return _strip_reminder(_fold(self.faces[0].get("oracle_text") or ""))

    def _type(self):
        return _fold(self.raw_type)

    def _face_types(self):
        """Per face, the set of type words (supertypes, types and subtypes)."""
        lines = [face.get("type_line") for face in self.faces if face.get("type_line")]
        if not lines:
            lines = self.raw_type.split(" // ")
        return [frozenset(_WORD.findall(_fold(line).replace("\u2014", " "))) for line in lines]

    def _color_sets(self):
        """Colors as Scryfall's c: sees them: the card's own, or else each face's separately
        (so a white card that transforms into a red one is c:w and c:r but not c>=2)."""
        if self.card.get("colors") is not None:
            return (frozenset(self.card["colors"]),)
        faces = self.card.get("card_faces") or ()
        sets = tuple(frozenset(face["colors"]) for face in faces if face.get("colors") is not None)
        return sets or (self.raw_colors,)

    def _front_types(self):
        return self.face_types[0] if self.face_types else frozenset()

    def _all_types(self):
        return frozenset().union(*self.face_types) if self.face_types else frozenset()

    def _mana_costs(self):
        """Symbol counts per face that has a cost; faces are never summed (Scryfall doesn't), and
        a card with no cost at all (a land, a transformed back face) never matches m:."""
        costs = [face.get("mana_cost") for face in self.faces if face.get("mana_cost")] or [self.raw_mana]
        return [counter for counter in map(_mana_counter, costs) if counter]

    def _symbols(self):
        return set(_MANA_SYMBOL.findall((self.raw_mana or "").upper()))

    def _text_symbols(self):
        return set(_MANA_SYMBOL.findall(self.full_oracle.upper()))

    def _flavor(self):
        texts = [face.get("flavor_text", "") for face in self.faces]
        if self.card.get("flavor_text") and self.faces[0] is not self.card:
            texts.append(self.card["flavor_text"])
        return _fold("\n".join(t for t in texts if t))

    def _artist(self):
        artists = [self.raw_artist] + [face.get("artist", "") for face in self.faces]
        return _fold(" & ".join(dict.fromkeys(a for a in artists if a)))

    def _keywords(self):
        return frozenset(_fold(k) for k in self.raw_keywords)

    def _watermarks(self):
        marks = {self.card.get("watermark")} | {face.get("watermark") for face in self.faces}
        return frozenset(_fold(m) for m in marks if m)

    def _promo_types(self):
        return frozenset(p.replace("_", "") for p in self.card.get("promo_types") or ())

    def _frame_effects(self):
        return frozenset(f.replace("_", "") for f in self.card.get("frame_effects") or ())

    def _produced(self):
        return frozenset(self.card.get("produced_mana") or ())

    def _lore(self):
        return "\n".join((self.name, self.type, self.full_oracle, self.flavor))


_VIEWS = {}     # id(entry) -> (weak reference to the entry, its view)


def _view(entry):
    """The entry's cached view. Entries are unhashable dataclasses, so the cache is keyed by
    identity and each record drops out when its entry is garbage collected."""
    key = id(entry)
    hit = _VIEWS.get(key)
    if hit is not None and hit[0]() is entry:
        return hit[1]
    view = _View(entry)
    _VIEWS[key] = (weakref.ref(entry, lambda _ref, key=key: _VIEWS.pop(key, None)), view)
    return view


# ---------------------------------------------------------------------------
# Lexer

_KEY_OP = re.compile(r"([A-Za-z_]+)(!=|<=|>=|:|=|<|>)")


class _Token:
    __slots__ = ("kind", "pos", "negated", "exact", "key", "op", "value", "value_kind", "raw")

    def __init__(self, kind, pos, **fields):
        self.kind = kind            # LPAREN RPAREN NEG OR AND TERM
        self.pos = pos
        self.negated = fields.get("negated", False)
        self.exact = fields.get("exact", False)
        self.key = fields.get("key")
        self.op = fields.get("op")
        self.value = fields.get("value")
        self.value_kind = fields.get("value_kind")   # word | quoted | regex
        self.raw = fields.get("raw")

    def __repr__(self):
        return f"<{self.kind} {self.raw!r}>"


def _read_value(text, start, allow_regex):
    """Read a value at `start`; returns (value, kind, end)."""
    n = len(text)
    if start < n and text[start] == '"':
        out, i = [], start + 1
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n and text[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            if ch == '"':
                return "".join(out), "quoted", i + 1
            out.append(ch)
            i += 1
        raise QueryError("This quoted text is missing its closing quote", start)
    if allow_regex and start < n and text[start] == "/":
        out, i = [], start + 1
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                out.append("/" if text[i + 1] == "/" else text[i:i + 2])
                i += 2
                continue
            if ch == "/":
                return "".join(out), "regex", i + 1
            out.append(ch)
            i += 1
        raise QueryError("This regular expression is missing its closing /", start)
    i = start
    while i < n and not text[i].isspace() and text[i] != ")":
        i += 1
    return text[start:i], "word", i


def _tokenize(text):
    tokens, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(_Token("LPAREN", i))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token("RPAREN", i))
            i += 1
            continue
        start, negated, exact = i, False, False
        if ch == "-":
            if i + 1 >= n or text[i + 1].isspace():
                i += 1                      # a lone hyphen means nothing
                continue
            if text[i + 1] == "(":
                tokens.append(_Token("NEG", i))
                i += 1
                continue
            negated = True
            i += 1
        if text[i] == "!" and i + 1 < n and not text[i + 1].isspace() and text[i + 1] != "=":
            exact = True
            i += 1
        match = None if exact else _KEY_OP.match(text, i)
        if match:
            key, op = match.group(1).lower(), match.group(2)
            value, kind, end = _read_value(text, match.end(), allow_regex=True)
            if not value and kind == "word" and key not in KNOWN_KEYS:
                # "Circle of Protection: Red" -- a colon ending a name word, not a keyword.
                tokens.append(_Token("TERM", start, negated=negated, value=match.group(1),
                                     value_kind="word", raw=text[start:end]))
            else:
                tokens.append(_Token("TERM", start, negated=negated, key=key, op=op, value=value,
                                     value_kind=kind, raw=text[start:end]))
            i = end
            continue
        bare_regex = text.startswith("/", i) and not text.startswith("//", i)
        value, kind, end = _read_value(text, i, allow_regex=bare_regex)
        if not value and kind == "word":
            raise QueryError(f"Unexpected “{text[i]}”", i)
        if kind == "word" and not negated and not exact and value.lower() in ("or", "and"):
            tokens.append(_Token(value.upper(), start))
        else:
            tokens.append(_Token("TERM", start, negated=negated, exact=exact, value=value,
                                 value_kind=kind, raw=text[start:end]))
        i = end
    return tokens


# ---------------------------------------------------------------------------
# Syntax tree

class _Node:
    ignorable = False
    cost = 1


def _cheapest_first(children):
    """AND and OR give the same answer in any order, so test the cheap terms first and let
    short-circuiting skip the regexes and per-face work for most entries. Stable for ties."""
    return sorted(children, key=lambda child: child.cost)


class _Pred(_Node):
    def __init__(self, fn, desc, neg_desc=None, cost=1):
        self.fn, self.desc, self.neg_desc, self.cost = fn, desc, neg_desc, cost

    def compile(self):
        return self.fn

    def describe(self, negated=False):
        if not negated:
            return self.desc
        return self.neg_desc or f"it is not true that {self.desc}"


class _Not(_Node):
    def __init__(self, child):
        self.child = child

    @property
    def cost(self):
        return self.child.cost

    def compile(self):
        inner = self.child.compile()
        return lambda entry: not inner(entry)

    def describe(self, negated=False):
        return self.child.describe(not negated)


class _And(_Node):
    def __init__(self, children):
        self.children = children

    @property
    def cost(self):
        return sum(child.cost for child in self.children)

    def compile(self):
        fns = [child.compile() for child in _cheapest_first(self.children)]
        if not fns:
            return lambda entry: True
        result = fns[-1]
        for fn in reversed(fns[:-1]):
            result = (lambda a, b: lambda entry: a(entry) and b(entry))(fn, result)
        return result

    def describe(self, negated=False):
        if not self.children:
            return "any card" if not negated else "no card"
        parts = [_wrap(child, negated) for child in self.children]
        return (" or " if negated else " and ").join(parts)


class _Or(_Node):
    def __init__(self, children):
        self.children = children

    @property
    def cost(self):
        return sum(child.cost for child in self.children)

    def compile(self):
        fns = [child.compile() for child in _cheapest_first(self.children)]
        result = fns[-1]
        for fn in reversed(fns[:-1]):
            result = (lambda a, b: lambda entry: a(entry) or b(entry))(fn, result)
        return result

    def describe(self, negated=False):
        parts = [_wrap(child, negated) for child in self.children]
        return (" and " if negated else " or ").join(parts)


def _wrap(node, negated):
    """Describe a child, parenthesized when its own connective would read ambiguously."""
    text = node.describe(negated)
    if isinstance(node, (_And, _Or)) and len(node.children) > 1:
        return f"({text})"
    return text


class _Ignored(_Node):
    """A term that does not filter (display keywords, unsupported keywords)."""
    ignorable = True


# ---------------------------------------------------------------------------
# Comparison helpers

_OPS = {
    ":": operator.eq, "=": operator.eq, "!=": operator.ne,
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}
_OP_WORDS = {":": "is", "=": "is", "!=": "is not", "<": "is less than", "<=": "is at most",
             ">": "is greater than", ">=": "is at least"}
_OP_INVERSE = {":": "!=", "=": "!=", "!=": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}


def _cmp_desc(subject, op, value):
    return (f"{subject} {_OP_WORDS[op]} {value}", f"{subject} {_OP_WORDS[_OP_INVERSE[op]]} {value}")


def _quote(text):
    return f"“{text}”"


def _set_test(want, op):
    """A one-argument version of _set_compare, specialized for speed."""
    if op in (":", ">="):
        return want.issubset
    if op == ">":
        return lambda have: want < have
    if op == "<=":
        return want.issuperset
    if op == "<":
        return lambda have: have < want
    if op == "=":
        return want.__eq__
    return want.__ne__


def _set_compare(have, want, op):
    """Set comparison with Scryfall's color semantics: >= superset, <= subset, = equal."""
    if op in (":", ">="):
        return want <= have
    if op == ">":
        return want < have
    if op == "<=":
        return have <= want
    if op == "<":
        return have < want
    if op == "=":
        return have == want
    return have != want         # !=


def _counter_contains(big, small):
    get = big.get
    for key, count in small.items():
        if get(key, 0) < count:
            return False
    return True


def _mana_compare(have, want, op):
    if op in (":", ">="):
        return _counter_contains(have, want)
    if op == ">":
        return _counter_contains(have, want) and have != want
    if op == "<=":
        return _counter_contains(want, have)
    if op == "<":
        return _counter_contains(want, have) and have != want
    if op == "=":
        return have == want
    return have != want


# ---------------------------------------------------------------------------
# Term compilation

class _Context:
    def __init__(self, tag_index):
        self.tag_index = tag_index
        self.warnings = []
        self.order_field = None
        self.direction = None
        self.unique = None
        self.prefer = None
        self.display = None
        self.include_extras = False

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)


def _need_op(token, allowed):
    if token.op not in allowed:
        ops = " ".join(sorted(allowed))
        raise QueryError(f"“{token.key}” does not support “{token.op}” (use one of: {ops})", token.pos)


def _compile_regex(token, pattern):
    # Scryfall shorthands: \sm a mana symbol, \smm a run of them, \spt a P/T, \spp a +X/+X boost.
    pattern = (pattern.replace(r"\spp", r"[+-][\dxX*]+/[+-][\dxX*]+")
               .replace(r"\spt", r"[+-]?[\dxX*]+/[+-]?[\dxX*]+")
               .replace(r"\smm", r"(?:\{[^}]+\})+")
               .replace(r"\sm", r"\{[^}]+\}")
               .replace(r"\ss", r"\{[^}]+\}"))
    try:
        return re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as error:
        raise QueryError(f"Invalid regular expression /{pattern}/: {error}", token.pos) from None


def _text_term(token, subject, getter, tilde_getter=None, cache_key=None):
    """Contains / regex over a text field. `getter(view)` returns folded text."""
    _need_op(token, {":", "=", "!="})
    if token.value_kind == "regex":
        uses_tilde = tilde_getter is not None and "~" in token.value
        rx = _compile_regex(token, token.value)
        text_of = tilde_getter if uses_tilde else getter
        search = rx.search
        fn = lambda entry: search(text_of(_view(entry))) is not None
        desc = f"{subject} matches /{token.value}/"
        neg = f"{subject} doesn't match /{token.value}/"
    else:
        needle = _fold(token.value)
        if not needle:
            raise QueryError(f"“{token.key}” needs something to search for", token.pos)
        text_of = tilde_getter if tilde_getter is not None and "~" in needle else getter
        fn = lambda entry: needle in text_of(_view(entry))
        desc = f"{subject} includes {_quote(token.value)}"
        neg = f"{subject} doesn't include {_quote(token.value)}"
    if cache_key is not None:
        fn = _cached_by(cache_key, fn)
    node = _Pred(fn, desc, neg)
    return _Not(node) if token.op == "!=" else node


def _cached_by(key, fn):
    """Memoize an oracle-level predicate by the entry's oracle id (text is shared by all prints)."""
    cache = {}

    def cached(entry):
        k = key(entry)
        hit = cache.get(k)
        if hit is None:
            hit = cache[k] = fn(entry)
        return hit
    return cached


_BY_ORACLE = lambda entry: entry.oracle_id


def _name_term(token):
    value = token.value
    if token.exact:
        target = _fold(value)
        return _Pred(lambda entry: target in _view(entry).names,
                     f"the name is {_quote(value)}", f"the name isn't {_quote(value)}")
    needle = _fold(value)
    loose = _loose(needle)

    def fn(entry):
        view = _view(entry)
        return needle in view.name or (bool(loose) and loose in view.name_loose)
    return _Pred(fn, f"the name includes {_quote(value)}", f"the name doesn't include {_quote(value)}", cost=2)


def _parse_colors(token, allow_letters="WUBRG"):
    """Returns ('set', frozenset) | ('count', int) | ('multi', None)."""
    value = _fold(token.value).replace(" ", "")
    if not value:
        raise QueryError(f"“{token.key}” needs colors, like {token.key}:rg or {token.key}:azorius", token.pos)
    if value.isdigit():
        return "count", int(value)
    if value in ("c", "colorless"):
        return "set", frozenset()
    if value in ("m", "multicolor", "multicolored", "multi"):
        return "multi", None
    if value in COLOR_WORDS:
        return "set", frozenset(COLOR_WORDS[value])
    letters = value.upper()
    if all(ch in allow_letters for ch in letters):
        return "set", frozenset(letters)
    suggestion = difflib.get_close_matches(value, list(COLOR_WORDS) + ["colorless", "multicolor"], n=3)
    hint = f" Did you mean {', '.join(suggestion)}?" if suggestion else ""
    raise QueryError(f"“{token.value}” is not a color, color combination or nickname.{hint}", token.pos)


def _color_phrase(colors):
    if not colors:
        return "colorless"
    names = [COLOR_NAMES[c] for c in "WUBRG" if c in colors]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _color_term(token, identity):
    kind, value = _parse_colors(token)
    op = token.op
    subject = "the color identity" if identity else "the colors"
    if identity:
        def over_colors(test):
            return lambda entry: test(entry.color_identity)
    else:
        def over_colors(test):
            # Cards without their own colors (transforming, modal) are tested face by face.
            def fn(entry):
                if entry.card.get("colors") is not None:
                    return test(entry.colors)
                return any(test(colors) for colors in _view(entry).color_sets)
            return fn
    if kind == "count":
        cmp = _OPS[op]
        desc = _cmp_desc(f"the number of {'identity ' if identity else ''}colors", op, value)
        return _Pred(over_colors(lambda colors: cmp(len(colors), value)), *desc)
    if kind == "multi":
        _need_op(token, {":", "=", "!="})
        positive = op != "!="
        return _Pred(over_colors(lambda colors: (len(colors) >= 2) == positive),
                     f"{subject} {'is' if positive else 'is not'} multicolored",
                     f"{subject} {'is not' if positive else 'is'} multicolored")
    if op == ":":
        # c:rg means at least red and green; id:esper means fits in an Esper deck.
        # Colorless is exact either way (c:c means no colors, not "at least nothing").
        op = "=" if not value else ("<=" if identity else ">=")
    phrase = _color_phrase(value)
    plural = not identity           # "the colors include" / "the color identity includes"
    s, are, arent, go = ("", "are", "aren't", "go") if plural else ("s", "is", "isn't", "goes")
    words = {">=": f"include{s} {phrase}", ">": f"include{s} {phrase} and more",
             "<=": f"{are} within {phrase}", "<": f"{are} strictly within {phrase}",
             "=": f"{are} exactly {phrase}", "!=": f"{arent} exactly {phrase}"}
    inverse = {">=": f"do{'es' if s else ''}n't include all of {phrase}", ">": f"{arent} more than {phrase}",
               "<=": f"{go} beyond {phrase}", "<": f"{arent} strictly within {phrase}",
               "=": f"{arent} exactly {phrase}", "!=": f"{are} exactly {phrase}"}
    return _Pred(over_colors(_set_test(value, op)), f"{subject} {words[op]}", f"{subject} {inverse[op]}")


def _parse_mana(token):
    text = token.value.strip()
    counts = Counter()
    for match in re.finditer(r"\{([^}]*)\}|(\d+)|([A-Za-z])|(\S)", text):
        braced, digits, letter, other = match.groups()
        if other is not None:
            raise QueryError(f"“{token.value}” is not a mana cost (use symbols like {{G}}{{G}} or 2WW)", token.pos)
        symbol = braced if braced is not None else (digits or letter)
        if not symbol:
            raise QueryError("Empty mana symbol {}", token.pos)
        if symbol.isdigit():
            counts["#"] += int(symbol)
        else:
            counts[_normalize_symbol(symbol)] += 1
    if not counts:
        raise QueryError(f"“{token.key}” needs a mana cost, like m:{{G}}{{G}}", token.pos)
    return dict(counts)            # {0} stays as {'#': 0}: a cost, just an empty one


def _mana_term(token):
    want = _parse_mana(token)
    op = token.op
    fn = _cached_by(_BY_ORACLE, lambda entry: any(_mana_compare(have, want, op) for have in _view(entry).mana_costs))
    words = {":": "includes", ">=": "includes", ">": "includes more than", "<=": "is within",
             "<": "is strictly within", "=": "is exactly", "!=": "is not exactly"}
    return _Pred(fn, f"the mana cost {words[op]} {token.value}",
                 f"it is not true that the mana cost {words[op]} {token.value}")


def _float_value(token, what="a number"):
    value = _number(token.value)
    if value is None:
        raise QueryError(f"“{token.key}” needs {what}, not “{token.value}”", token.pos)
    return value


def _numeric_term(token, subject, getter):
    """getter(entry) -> float | None; None never matches."""
    value = _float_value(token)
    cmp = _OPS[token.op]

    def fn(entry):
        have = getter(entry)
        return have is not None and cmp(have, value)
    return _Pred(fn, *_cmp_desc(subject, token.op, _fmt_number(token.value)))


def _fmt_number(text):
    number = _number(text)
    return str(int(number)) if number is not None and number == int(number) else text


_STAT_FIELDS = {
    "pow": "power", "power": "power", "tou": "toughness", "toughness": "toughness",
    "loy": "loyalty", "loyalty": "loyalty", "pt": "pt", "powtou": "pt",
    "mv": "cmc", "cmc": "cmc", "manavalue": "cmc", "def": "defense", "defense": "defense",
}
_STAT_SUBJECT = {"power": "the power", "toughness": "the toughness", "loyalty": "the loyalty",
                 "pt": "the total power and toughness", "cmc": "the mana value", "defense": "the defense"}


def _mana_value(entry):
    """Reversible cards keep their mana value on the faces only."""
    if entry.cmc or entry.layout != "reversible_card":
        return entry.cmc
    faces = entry.card.get("card_faces") or [{}]
    return float(faces[0].get("cmc") or 0)


def _face_stat(entry, face, stat):
    if stat == "cmc":
        return _mana_value(entry)
    if stat == "pt":
        power, toughness = _number(face.get("power")), _number(face.get("toughness"))
        return None if power is None or toughness is None else power + toughness
    return _number(face.get(stat))


def _stat_faces(entry):
    card = entry.card
    faces = card.get("card_faces")
    if not faces:
        return (card,)
    if card.get("power") is not None or card.get("loyalty") is not None:
        return (card,) + tuple(faces)
    return faces


def _stat_term(token):
    stat = _STAT_FIELDS[token.key]
    subject = _STAT_SUBJECT[stat]
    cmp = _OPS[token.op]
    other_key = token.value.lower()
    if other_key in _STAT_FIELDS:
        other = _STAT_FIELDS[other_key]

        # Like Scryfall, any face's value against any face's: Mayor of Avabruck (1/1, back 3/3)
        # has pow>tou.
        def fn(entry):
            faces = _stat_faces(entry)
            left = [v for v in (_face_stat(entry, face, stat) for face in faces) if v is not None]
            right = [v for v in (_face_stat(entry, face, other) for face in faces) if v is not None]
            return any(cmp(a, b) for a in left for b in right)
        return _Pred(_cached_by(_BY_ORACLE, fn), *_cmp_desc(subject, token.op, _STAT_SUBJECT[other]))

    if stat == "cmc":
        parity = token.value.lower()
        if parity in ("even", "odd"):
            _need_op(token, {":", "=", "!="})
            want = 0 if parity == "even" else 1
            positive = token.op != "!="
            def parity_fn(entry):
                mv = _mana_value(entry)
                return (mv == int(mv) and int(mv) % 2 == want) == positive
            return _Pred(parity_fn,
                         f"the mana value {'is' if positive else 'is not'} {parity}",
                         f"the mana value {'is not' if positive else 'is'} {parity}")
        value = _float_value(token, "a number (or even/odd)")
        return _Pred(lambda entry: cmp(_mana_value(entry), value),
                     *_cmp_desc(subject, token.op, _fmt_number(token.value)))

    value = _float_value(token, "a number or another stat (pow, tou, loy, pt, mv)")
    # A stat matches on any face, except the pt total, which Scryfall takes from the front face.
    faces_of = (lambda entry: _stat_faces(entry)[:1]) if stat == "pt" else _stat_faces

    def fn(entry):
        for face in faces_of(entry):
            have = _face_stat(entry, face, stat)
            if have is not None and cmp(have, value):
                return True
        return False
    return _Pred(_cached_by(_BY_ORACLE, fn), *_cmp_desc(subject, token.op, _fmt_number(token.value)))


def _rarity_term(token):
    name = token.value.lower()
    name = RARITY_ABBREVIATIONS.get(name, name)
    if name not in RARITY_ORDER:
        raise QueryError(f"“{token.value}” is not a rarity (common, uncommon, rare, special, mythic, bonus)", token.pos)
    rank = RARITY_ORDER[name]
    cmp = _OPS[token.op]
    return _Pred(lambda entry: cmp(RARITY_ORDER.get(entry.rarity, -1), rank),
                 *_cmp_desc("the rarity", token.op, name))


def _date_value(token):
    value = token.value.lower()
    if value in ("now", "today"):
        return date.today().isoformat()
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value):
        return value
    raise QueryError(f"“{token.value}” is not a date (use yyyy, yyyy-mm or yyyy-mm-dd, or now)", token.pos)


def _date_term(token, subject, attr):
    """Compares at the precision given: added:2025 is any day in 2025, date>=2015-08 from August."""
    value = _date_value(token)
    width = len(value)
    cmp = _OPS[token.op]

    def fn(entry):
        have = getattr(entry, attr)
        return bool(have) and cmp(have[:width], value)
    return _Pred(fn, *_cmp_desc(subject, token.op, value))


def _year_term(token):
    value = token.value.lower()
    year = date.today().year if value in ("now", "today") else _number(value)
    if year is None:
        raise QueryError(f"“year” needs a year, not “{token.value}”", token.pos)
    cmp = _OPS[token.op]

    def fn(entry):
        released = entry.released_at
        return bool(released) and cmp(int(released[:4]), year)
    return _Pred(fn, *_cmp_desc("the release year", token.op, str(int(year))))


def _format_name(token):
    name = token.value.lower().replace("_", "").replace(" ", "")
    name = FORMAT_ALIASES.get(name, name)
    if name not in FORMATS:
        suggestion = difflib.get_close_matches(name, FORMATS, n=3)
        hint = f" Did you mean {', '.join(suggestion)}?" if suggestion else ""
        raise QueryError(f"“{token.value}” is not a format Scryfall tracks.{hint}", token.pos)
    return name


def _legality_term(token, statuses, verb):
    _need_op(token, {":", "=", "!="})
    fmt = _format_name(token)
    positive = token.op != "!="
    fn = lambda entry: (entry.legalities.get(fmt) in statuses) == positive
    return _Pred(fn, f"it {verb} {fmt}" if positive else f"it isn't {verb.replace('is ', '')} {fmt}",
                 f"it isn't {verb.replace('is ', '')} {fmt}" if positive else f"it {verb} {fmt}")


def _keyword_term(token):
    _need_op(token, {":", "=", "!="})
    target = _fold(token.value)
    node = _Pred(lambda entry: target in _view(entry).keywords,
                 f"it has the keyword {_quote(token.value)}", f"it doesn't have the keyword {_quote(token.value)}")
    return _Not(node) if token.op == "!=" else node


def _tag_term(token, ctx):
    _need_op(token, {":", "=", "!="})
    slug = _fold(token.value).strip().replace(" ", "-")
    known = getattr(ctx.tag_index, "descendants", None)
    if ctx.tag_index is None or (known is not None and slug not in known):
        ctx.warn(f"Unknown oracle tag “{token.value}”, so otag:{token.value} matches nothing.")
        tags = frozenset()
    else:
        tags = frozenset(ctx.tag_index.expand(slug))
    node = _Pred(lambda entry: not tags.isdisjoint(entry.tags),
                 f"it is tagged {_quote(slug)}", f"it isn't tagged {_quote(slug)}")
    return _Not(node) if token.op == "!=" else node


def _condition_term(token):
    value = token.value.lower().replace(" ", "_").replace("-", "_")
    value = CONDITION_ALIASES.get(value.replace("_", ""), value)
    if value not in CONDITION_ORDER:
        raise QueryError(f"“{token.value}” is not a condition ({', '.join(reversed(CONDITION_ORDER))}; "
                         "or nm, ex, gd, lp, pl, po)", token.pos)
    rank = CONDITION_ORDER.index(value)
    cmp = _OPS[token.op]

    def fn(entry):
        have = entry.condition.lower().replace(" ", "_")
        return have in CONDITION_ORDER and cmp(CONDITION_ORDER.index(have), rank)
    words = {">": "better than", ">=": "at least", "<": "worse than", "<=": "at most"}
    if token.op in words:
        return _Pred(fn, f"the condition is {words[token.op]} {value.replace('_', ' ')}")
    return _Pred(fn, *_cmp_desc("the condition", token.op, value.replace("_", " ")))


def _language_term(token):
    _need_op(token, {":", "=", "!="})
    value = token.value.lower().replace(" ", "").replace("_", "")
    if value == "any":
        return _Ignored()
    code = LANGUAGES.get(value, value)
    positive = token.op != "!="
    return _Pred(lambda entry: (entry.language.lower() == code) == positive,
                 f"the language {'is' if positive else 'is not'} {code}",
                 f"the language {'is not' if positive else 'is'} {code}")


def _equals_term(token, subject, getter, normalize=lambda s: s.lower()):
    _need_op(token, {":", "=", "!="})
    target = normalize(token.value)
    positive = token.op != "!="
    return _Pred(lambda entry: (normalize(getter(entry) or "") == target) == positive,
                 f"{subject} {'is' if positive else 'is not'} {token.value}",
                 f"{subject} {'is not' if positive else 'is'} {token.value}")


def _collector_number_term(token):
    if token.op in (":", "=", "!="):
        return _equals_term(token, "the collector number", lambda entry: entry.collector_number)
    value = _float_value(token)
    cmp = _OPS[token.op]

    def fn(entry):
        digits = re.match(r"\d+", entry.collector_number or "")
        return digits is not None and cmp(int(digits.group()), value)
    return _Pred(fn, *_cmp_desc("the collector number", token.op, _fmt_number(token.value)))


def _produces_term(token):
    value = token.value.upper()
    if not value or not all(ch in "WUBRGC" for ch in value):
        raise QueryError(f"“produces” takes mana letters from WUBRGC, not “{token.value}”", token.pos)
    want = frozenset(value)
    op = ">=" if token.op == ":" else token.op
    return _Pred(lambda entry: _set_compare(_view(entry).produced, want, op),
                 *_cmp_desc("the mana it produces", op, "".join(sorted(want))))


def _membership_term(token, subject, getter, allowed=None):
    """Value must be in the collection getter(entry) returns (games, watermarks, ...)."""
    _need_op(token, {":", "=", "!="})
    value = _fold(token.value)
    if allowed is not None and value not in allowed:
        raise QueryError(f"“{token.value}” is not a valid {subject} ({', '.join(sorted(allowed))})", token.pos)
    positive = token.op != "!="
    return _Pred(lambda entry: (value in getter(entry)) == positive,
                 f"{subject} {'is' if positive else 'is not'} {value}",
                 f"{subject} {'is not' if positive else 'is'} {value}")


def _frame_term(token):
    _need_op(token, {":", "=", "!="})
    value = token.value.lower().replace("_", "")
    positive = token.op != "!="
    if value in FRAMES:
        fn = lambda entry: (entry.card.get("frame") == value) == positive
    elif value in FRAME_EFFECTS:
        fn = lambda entry: (value in _view(entry).frame_effects) == positive
    else:
        raise QueryError(f"“{token.value}” is not a frame ({', '.join(sorted(FRAMES | FRAME_EFFECTS))})", token.pos)
    return _Pred(fn, f"the frame {'is' if positive else 'is not'} {value}",
                 f"the frame {'is not' if positive else 'is'} {value}")


def _money_getter(attr):
    return lambda entry: getattr(entry, attr)


def _deck_term(token):
    """deck:<text> -- in a deck (any status) whose name contains the text; deck:any -- in an
    active deck. Reads entry.decks live, so it follows deck edits without a reload."""
    _need_op(token, {":", "=", "!="})
    positive = token.op != "!="
    if token.value.lower() == "any" and token.value_kind == "word":
        fn = lambda entry: any(status == "active" for _name, status in entry.decks)
        desc, neg = "it is in an active deck", "it is in no active deck"
    else:
        needle = _fold(token.value)
        fn = lambda entry: any(needle in _fold(name) for name, _status in entry.decks)
        desc = f"it is in a deck named {_quote(token.value)}"
        neg = f"it is in no deck named {_quote(token.value)}"
    node = _Pred(fn, desc, neg)
    return node if positive else _Not(node)


def _binder_term(token):
    """binder:<text> -- some copies are in a binder whose name contains the text; binder:any -- in
    any binder; binder:none -- some copies are in no binder (unsorted). Reads entry.binders live."""
    _need_op(token, {":", "=", "!="})
    positive = token.op != "!="
    word = token.value.lower() if token.value_kind == "word" else None
    if word == "any":
        fn = lambda entry: bool(entry.binders)
        desc, neg = "some copies are in a binder", "no copies are in a binder"
    elif word in ("none", "unsorted"):
        fn = lambda entry: entry.quantity > sum(copies for _name, copies in entry.binders)
        desc, neg = "some copies are in no binder", "every copy is in a binder"
    else:
        needle = _fold(token.value)
        fn = lambda entry: any(needle in _fold(name) for name, _copies in entry.binders)
        desc = f"some copies are in a binder named {_quote(token.value)}"
        neg = f"no copies are in a binder named {_quote(token.value)}"
    node = _Pred(fn, desc, neg)
    return node if positive else _Not(node)


def _gain(entry):
    if entry.price_usd is None or entry.purchase_price is None:
        return None
    return entry.price_usd - entry.purchase_price


# --- is: / not: / has:

def _front_face(entry):
    faces = entry.card.get("card_faces")
    return faces[0] if faces else entry.card


def _is_commander(entry):
    """Scryfall's reading: a legendary creature (or Vehicle/Spacecraft with P/T), a Background,
    or a card that says it can be your commander. Legality is not considered; add f:commander."""
    view = _view(entry)
    front = view.front_types
    if "token" in front:
        return False
    if "legendary" in front:
        if "creature" in front or "background" in front:
            return True
        if ("vehicle" in front or "spacecraft" in front) and _front_face(entry).get("power") is not None:
            return True
        if "isn't on the battlefield, it's a" in view.oracle and "creature" in view.oracle:
            return True             # Grist, the Hunger Tide
    return "can be your commander" in view.oracle


def _is_permanent(entry):
    """Any face is a permanent card: Scryfall counts Beyeen Veil // Beyeen Coast (instant // land)."""
    if entry.layout in ("emblem", "art_series"):
        return False
    return any(not PERMANENT_TYPES.isdisjoint(types) and "instant" not in types and "sorcery" not in types
               for types in _view(entry).face_types)


def _is_spell(entry):
    if entry.layout in TOKEN_LAYOUTS or entry.layout == "art_series":
        return False
    return any("land" not in types and not SPELL_TYPES.isdisjoint(types) for types in _view(entry).face_types)


def _is_vanilla(entry):
    """A creature whose front face has no rules text (reminder text doesn't count)."""
    view = _view(entry)
    if "creature" not in view.front_types or "land" in view.front_types:
        return False                # Scryfall leaves out land creatures such as the Forest Dryad token
    return not view.front_oracle.strip()


def _is_french_vanilla(entry):
    view = _view(entry)
    if "creature" not in view.front_types or entry.card.get("card_faces") or not view.keywords:
        return False
    text = view.oracle.strip()
    if not text:
        return False
    for line in text.split("\n"):
        if " \u2014 " in line and not re.search(r"\u2014 ?\{", line):
            return False            # an ability word ("Domain \u2014 ...") heads a real ability
        for part in re.split(r"[,;] ", line.strip()):
            part = part.strip().rstrip(".")
            if part and not any(part == k or part.startswith(k + " ") for k in view.keywords):
                return False
    return True


def _has_symbol(entry, test):
    return any(test(symbol) for symbol in _view(entry).symbols)


def _hybrid_symbol(symbol):
    parts = [p for p in symbol.split("/") if p != "P"]
    return "/" in symbol and len(parts) >= 2


def _types_any(*words):
    words = frozenset(words)
    return lambda entry: not words.isdisjoint(_view(entry).all_types)


def _layout_is(*layouts):
    layouts = frozenset(layouts)
    return lambda entry: entry.layout in layouts


def _card_flag(key):
    return lambda entry: bool(entry.card.get(key))


IS_FLAGS = {
    # the held copy
    "foil": lambda entry: entry.finish == "foil",
    "nonfoil": lambda entry: entry.finish == "normal",
    "etched": lambda entry: entry.finish == "etched",
    "misprint": lambda entry: entry.misprint,
    # deck allocation, filled in by the deck allocator; read live, never cached
    "used": lambda entry: entry.used > 0,
    "spare": lambda entry: entry.spare > 0,
    # faces and layouts
    "dfc": lambda entry: entry.is_double_faced,
    "doublefaced": lambda entry: entry.is_double_faced,
    "mdfc": _layout_is("modal_dfc"),
    "transform": _layout_is("transform"),
    "tdfc": _layout_is("transform"),
    "split": _layout_is("split"),
    "flip": _layout_is("flip"),
    "adventure": _layout_is("adventure"),
    "meld": _layout_is("meld"),
    "leveler": _layout_is("leveler"),
    "saga": _types_any("saga"),
    "class": _layout_is("class"),
    "case": _layout_is("case"),
    "mutate": _layout_is("mutate"),
    "prototype": _layout_is("prototype"),
    "prepare": _layout_is("prepare"),
    "token": lambda entry: entry.layout in ("token", "double_faced_token") or "token" in _view(entry).all_types,
    "emblem": _layout_is("emblem"),
    "artseries": _layout_is("art_series"),
    "reversible": _layout_is("reversible_card"),
    # what the card is
    "permanent": _is_permanent,
    "spell": _is_spell,
    "historic": _types_any("legendary", "artifact", "saga"),
    "party": _types_any("cleric", "rogue", "warrior", "wizard"),
    "outlaw": _types_any("assassin", "mercenary", "pirate", "rogue", "warlock"),
    "commander": _is_commander,
    "vanilla": _is_vanilla,
    "frenchvanilla": _is_french_vanilla,
    "bear": lambda entry: ("creature" in _view(entry).front_types and entry.cmc == 2
                           and entry.power == "2" and entry.toughness == "2"),
    "basic": _types_any("basic"),
    "snow": _types_any("snow"),
    "legendary": _types_any("legendary"),
    "hybrid": lambda entry: _has_symbol(entry, _hybrid_symbol),
    # Scryfall also counts Phyrexian symbols in rules text (Tekuthal's {U/P} activation cost).
    # ({P} alone is a Season's pawprint, not Phyrexian mana.)
    "phyrexian": lambda entry: any("/P" in symbol for symbol in _view(entry).symbols | _view(entry).text_symbols),
    "modal": lambda entry: re.search(r"\bchoose (one|two|three|four|any number|one or more|up to)\b|\+ ?\{",
                                     _view(entry).oracle) is not None,
    "companion": lambda entry: "companion" in _view(entry).keywords,
    "partner": lambda entry: any(k.startswith("partner") or k == "friends forever" for k in _view(entry).keywords),
    # the printing
    "reserved": _card_flag("reserved"),
    "gamechanger": _card_flag("game_changer"),
    "promo": _card_flag("promo"),
    "fullart": _card_flag("full_art"),
    "full": _card_flag("full_art"),
    "reprint": _card_flag("reprint"),
    "digital": _card_flag("digital"),
    "oversized": _card_flag("oversized"),
    "textless": _card_flag("textless"),
    "hires": _card_flag("highres_image"),
    "spotlight": _card_flag("story_spotlight"),
    "booster": _card_flag("booster"),
    "variation": _card_flag("variation"),
    "funny": lambda entry: entry.set_type == "funny",
    "alchemy": lambda entry: entry.set_type == "alchemy" or entry.name.startswith("A-"),
    "universesbeyond": lambda entry: "universesbeyond" in _view(entry).promo_types,
    "ub": lambda entry: "universesbeyond" in _view(entry).promo_types,
    "borderless": lambda entry: entry.card.get("border_color") == "borderless",
    "old": lambda entry: entry.card.get("frame") in ("1993", "1997"),
    "new": lambda entry: entry.card.get("frame") == "2015",
    "masterpiece": lambda entry: entry.set_type == "masterpiece",
}
ORACLE_LEVEL_FLAGS = frozenset({
    "permanent", "spell", "historic", "party", "outlaw", "commander", "vanilla", "frenchvanilla",
    "bear", "basic", "snow", "legendary", "hybrid", "phyrexian", "modal", "companion", "partner", "saga",
})
IS_ALIASES = {"gamechangers": "gamechanger", "doublefacedcard": "dfc", "meldpart": "meld"}
IS_UNSUPPORTED = frozenset({
    "dual", "fetchland", "shockland", "checkland", "painland", "fastland", "slowland", "scryland",
    "surveilland", "gainland", "bounceland", "karoo", "canopyland", "canland", "filterland",
    "storageland", "tangoland", "battleland", "triome", "tricycleland", "trikeland", "triland",
    "bikeland", "cycleland", "bicycleland", "bondland", "crowdland", "shadowland", "snarl", "pathway",
    "creatureland", "manland", "meldresult", "unique", "brawler", "duelcommander", "oathbreaker",
    "default", "atypical", "scryfallpreview", "newart", "firstprint",
})
HAS_FLAGS = {
    "watermark": lambda entry: bool(_view(entry).watermarks),
    "indicator": lambda entry: bool(entry.card.get("color_indicator")) or any(
        face.get("color_indicator") for face in entry.card.get("card_faces") or ()),
    "flavor": lambda entry: bool(_view(entry).flavor),
}


def _is_term(token, ctx):
    _need_op(token, {":", "="})
    value = token.value.lower().replace("_", "").replace("-", "")
    value = IS_ALIASES.get(value, value)
    positive = token.key == "is"
    if token.key == "has":
        fn = HAS_FLAGS.get(value)
        if fn is None:
            ctx.warn(f"has:{token.value} is not supported, so it was ignored.")
            return _Ignored()
        return _Pred(fn, f"it has a {value}", f"it has no {value}")
    fn = IS_FLAGS.get(value)
    if fn is None and value in PROMO_TYPES:
        fn = lambda entry: value in _view(entry).promo_types
    if fn is None and value in FRAME_EFFECTS:
        fn = lambda entry: value in _view(entry).frame_effects
    if fn is None:
        if value not in IS_UNSUPPORTED:
            suggestion = difflib.get_close_matches(value, list(IS_FLAGS), n=3)
            hint = f" (did you mean {', '.join(suggestion)}?)" if suggestion else ""
        else:
            hint = ""
        ctx.warn(f"{token.key}:{token.value} is not supported, so it was ignored{hint}.")
        return _Ignored()
    cost = 1
    if value in ORACLE_LEVEL_FLAGS:
        fn = _cached_by(_BY_ORACLE, fn)
        cost = 3
    node = _Pred(fn, f"it is {value}", f"it isn't {value}", cost)
    return node if positive else _Not(node)


# --- display keywords

def _display_term(token, ctx):
    value = token.value.lower()
    key = token.key
    if key == "unique":
        if value not in UNIQUE_MODES:
            raise QueryError(f"unique: takes cards, prints or art, not “{token.value}”", token.pos)
        ctx.unique = value
    elif key in ("order", "sort"):
        if value not in ORDER_FIELDS:
            suggestion = difflib.get_close_matches(value, ORDER_FIELDS, n=3)
            hint = f" Did you mean {', '.join(suggestion)}?" if suggestion else ""
            raise QueryError(f"Can't order by “{token.value}”.{hint}", token.pos)
        ctx.order_field = {"mv": "cmc", "manavalue": "cmc", "quantity": "qty"}.get(value, value)
    elif key in ("direction", "dir"):
        if value not in ("asc", "desc", "auto"):
            raise QueryError(f"direction: takes asc or desc, not “{token.value}”", token.pos)
        ctx.direction = None if value == "auto" else value
    elif key == "prefer":
        ctx.prefer = value
    elif key == "display":
        ctx.display = value
    elif key == "include":
        ctx.include_extras = value == "extras"
    return _Ignored()


# ---------------------------------------------------------------------------
# Keyword table

def _build_term(token, ctx):
    key = token.key
    if key is None:
        if token.value_kind == "regex":        # a bare /regex/ searches names, like name:/regex/
            token.key, token.op = "name", ":"
            return _HANDLERS["name"](token, ctx)
        return _name_term(token)
    handler = _HANDLERS.get(key)
    if handler is None:
        if key in UNSUPPORTED_KEYS:
            ctx.warn(f"{key}: is not supported here, so “{token.raw}” was ignored.")
            return _Ignored()
        suggestion = difflib.get_close_matches(key, KNOWN_KEYS, n=3, cutoff=0.6)
        hint = f" Did you mean {', '.join(s + ':' for s in suggestion)}?" if suggestion else ""
        raise QueryError(f"Unknown keyword “{key}”.{hint}", token.pos)
    if token.value_kind == "regex" and key not in _REGEX_KEYS:
        raise QueryError(f"“{key}” does not take a regular expression", token.pos)
    if not token.value and token.value_kind == "word":
        raise QueryError(f"“{key}{token.op}” needs a value", token.pos)
    node = handler(token, ctx)
    cost = _COSTS.get(key)
    if cost is not None:
        target = node
        while isinstance(target, _Not):
            target = target.child
        if isinstance(target, _Pred):
            target.cost = cost * (3 if token.value_kind == "regex" else 1)
    return node


def _v(attr):
    return lambda view: getattr(view, attr)


_HANDLERS = {}
_REGEX_KEYS = {"name", "n", "o", "oracle", "fo", "fulloracle", "t", "type", "ft", "flavor",
               "a", "artist", "wm", "watermark", "lore"}


def _register(names, handler):
    for name in names:
        _HANDLERS[name] = handler


# Holding and deck fields (qty, cond, used, spare, deck, is:used...) are read from the entry on
# every match and never go into the cached _View, so deck edits apply without a reload.
_register(("name", "n"), lambda t, c: _text_term(t, "the name", _v("name")))
_register(("o", "oracle"), lambda t, c: _text_term(t, "the text", _v("oracle"), _v("oracle_tilde"), _BY_ORACLE))
_register(("fo", "fulloracle"), lambda t, c: _text_term(
    t, "the full text", _v("full_oracle"), _v("full_oracle_tilde"), _BY_ORACLE))
_register(("t", "type"), lambda t, c: _text_term(t, "the type", _v("type")))
_register(("ft", "flavor"), lambda t, c: _text_term(t, "the flavor text", _v("flavor")))
_register(("a", "artist"), lambda t, c: _text_term(t, "the artist", _v("artist")))
_register(("wm", "watermark"), lambda t, c: (
    _text_term(t, "the watermark", lambda view: " ".join(sorted(view.watermarks)))))
_register(("lore",), lambda t, c: _text_term(t, "the lore", _v("lore")))
_register(("c", "color", "colors"), lambda t, c: _color_term(t, identity=False))
_register(("id", "identity", "ci", "commander"), lambda t, c: _color_term(t, identity=True))
_register(("m", "mana"), lambda t, c: _mana_term(t))
_register(tuple(_STAT_FIELDS), lambda t, c: _stat_term(t))
_register(("r", "rarity"), lambda t, c: _rarity_term(t))
_register(("s", "set", "e", "edition"), lambda t, c: _equals_term(t, "the set", lambda e: e.set_code))
_register(("cn", "number"), lambda t, c: _collector_number_term(t))
_register(("st", "settype"), lambda t, c: _equals_term(
    t, "the set type", lambda e: e.set_type, lambda s: s.lower().replace("_", "").replace(" ", "")))
_register(("year",), lambda t, c: _year_term(t))
_register(("date", "released"), lambda t, c: _date_term(t, "the release date", "released_at"))
_register(("f", "format", "legal"), lambda t, c: _legality_term(t, ("legal", "restricted"), "is legal in"))
_register(("banned",), lambda t, c: _legality_term(t, ("banned",), "is banned in"))
_register(("restricted",), lambda t, c: _legality_term(t, ("restricted",), "is restricted in"))
_register(("k", "kw", "keyword"), lambda t, c: _keyword_term(t))
_register(("otag", "oracletag", "function", "oracle_tag"), _tag_term)
_register(("is", "not", "has"), _is_term)
_register(("qty", "quantity"), lambda t, c: _numeric_term(t, "the quantity held", lambda e: e.quantity))
_register(("cond", "condition"), lambda t, c: _condition_term(t))
_register(("lang", "language"), lambda t, c: _language_term(t))
_register(("deck",), lambda t, c: _deck_term(t))
_register(("binder", "box"), lambda t, c: _binder_term(t))
_register(("used",), lambda t, c: _numeric_term(t, "the number of copies in active decks", lambda e: e.used))
_register(("spare",), lambda t, c: _numeric_term(t, "the number of spare copies (not in active decks)", lambda e: e.spare))
_register(("added",), lambda t, c: _date_term(t, "the date added", "added_at"))
_register(("paid", "purchase", "bought"), lambda t, c: _numeric_term(
    t, "the purchase price", lambda e: e.purchase_price))
_register(("gain", "profit"), lambda t, c: _numeric_term(t, "the gain (USD price minus price paid)", _gain))
_register(("usd",), lambda t, c: _numeric_term(t, "the USD price", _money_getter("price_usd")))
_register(("eur",), lambda t, c: _numeric_term(t, "the EUR price", _money_getter("price_eur")))
_register(("tix",), lambda t, c: _numeric_term(t, "the TIX price", _money_getter("price_tix")))
_register(("edhrec", "edhrecrank"), lambda t, c: _numeric_term(
    t, "the EDHREC rank", lambda e: e.card.get("edhrec_rank")))
_register(("game",), lambda t, c: _membership_term(
    t, "the game", lambda e: e.card.get("games") or (), {"paper", "arena", "mtgo", "astral", "sega"}))
_register(("produces",), lambda t, c: _produces_term(t))
_register(("border",), lambda t, c: _equals_term(t, "the border", lambda e: e.card.get("border_color")))
_register(("frame",), lambda t, c: _frame_term(t))
_register(("unique", "order", "sort", "direction", "dir", "prefer", "display", "include"), _display_term)

KNOWN_KEYS = sorted(set(_HANDLERS) | UNSUPPORTED_KEYS)

# Rough relative cost of evaluating each keyword once, for cheapest-first ordering.
_COSTS = {}
for _names, _cost in (
        (("name", "n", "t", "type", "k", "kw", "keyword", "a", "artist"), 2),
        (("o", "oracle", "fo", "fulloracle", "ft", "flavor", "lore", "wm", "watermark"), 3),
        (("m", "mana", "produces") + tuple(_STAT_FIELDS), 4),
):
    for _name in _names:
        _COSTS[_name] = _cost
_COSTS.update({"mv": 1, "cmc": 1, "manavalue": 1})


# ---------------------------------------------------------------------------
# Parser

class _Parser:
    def __init__(self, tokens, ctx):
        self.tokens, self.index, self.ctx = tokens, 0, ctx

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self):
        token = self.tokens[self.index]
        self.index += 1
        return token

    def parse(self):
        node = self.parse_or()
        token = self.peek()
        if token is not None:
            raise QueryError("This “)” has no matching “(”", token.pos)
        return node

    def parse_or(self):
        branches = [self.parse_and()]
        while self.peek() is not None and self.peek().kind == "OR":
            token = self.take()
            if self.peek() is None or self.peek().kind in ("RPAREN", "OR"):
                raise QueryError("“or” needs a search term after it", token.pos)
            branches.append(self.parse_and())
        return _simplify(_Or, branches)

    def parse_and(self):
        items = []
        start = self.peek()
        while True:
            token = self.peek()
            if token is None or token.kind in ("RPAREN", "OR"):
                break
            if token.kind == "AND":
                self.take()
                continue
            items.append(self.parse_unary())
        if not items:
            position = start.pos if start is not None else None
            if start is not None and start.kind == "OR":
                raise QueryError("“or” needs a search term before it", position)
            if start is not None and start.kind == "RPAREN":
                raise QueryError("Empty parentheses or a “)” with no matching “(”", position)
            raise QueryError("Expected a search term", position)
        return _simplify(_And, items)

    def parse_unary(self):
        token = self.take()
        if token.kind == "NEG":
            opener = self.take()            # the lexer only emits NEG right before "("
            inner = self.parse_group(opener)
            return inner if inner.ignorable else _Not(inner)
        if token.kind == "LPAREN":
            return self.parse_group(token)
        if token.kind == "TERM":
            node = _build_term(token, self.ctx)
            if token.negated and not node.ignorable:
                node = _Not(node)
            return node
        raise QueryError(f"Unexpected “{token.kind.lower()}”", token.pos)

    def parse_group(self, opener):
        if self.peek() is not None and self.peek().kind == "RPAREN":
            raise QueryError("Empty parentheses", opener.pos)
        node = self.parse_or()
        closer = self.peek()
        if closer is None or closer.kind != "RPAREN":
            raise QueryError("This “(” is never closed", opener.pos)
        self.take()
        return node


def _simplify(kind, children):
    children = [child for child in children if not child.ignorable]
    if not children:
        return _Ignored()
    if len(children) == 1:
        return children[0]
    flat = []
    for child in children:
        flat.extend(child.children if isinstance(child, kind) else [child])
    return kind(flat)


# ---------------------------------------------------------------------------
# Public API

class Query:
    """A compiled search. `matches(entry)` is the filter; the rest is for display."""

    def __init__(self, text, root, ctx):
        self.text = text
        self._root = root
        self.warnings = ctx.warnings
        self.unique = ctx.unique
        self.prefer = ctx.prefer
        self.display = ctx.display
        self.include_extras = ctx.include_extras
        if ctx.order_field is None and ctx.direction is None:
            self.order = None
        else:
            field = ctx.order_field or "name"
            direction = ctx.direction or ("desc" if field in DESCENDING_BY_DEFAULT else "asc")
            self.order = (field, direction)
        self.matches = (lambda entry: True) if root.ignorable else root.compile()

    def matches(self, entry):          # replaced per instance by the compiled closure
        raise NotImplementedError

    def filter(self, entries):
        match = self.matches
        return [entry for entry in entries if match(entry)]

    @property
    def matches_everything(self):
        return self._root.ignorable

    def describe(self):
        if self._root.ignorable:
            return "all cards"
        return f"cards where {self._root.describe()}"

    def __repr__(self):
        return f"Query({self.text!r})"


_PREPARED_FIELDS = ("name", "names", "name_loose", "oracle", "full_oracle", "type", "face_types",
                    "front_types", "all_types", "color_sets", "mana_costs", "symbols", "keywords",
                    "produced", "front_oracle", "oracle_tilde", "artist")


def prepare(entries):
    """Compute the search view of every entry now rather than during the first queries.

    Optional: searches work without it, but the first query to touch a field (text, types,
    mana...) pays for computing it across the collection. Call after loading or reloading.
    """
    for entry in entries:
        view = _view(entry)
        for field in _PREPARED_FIELDS:
            getattr(view, field)


def compile_query(text, tag_index=None):
    """Parse Scryfall search syntax. Raises QueryError with a message fit to show the user."""
    text = text or ""
    ctx = _Context(tag_index)
    tokens = _tokenize(text)
    root = _Parser(tokens, ctx).parse() if tokens else _Ignored()
    return Query(text, root, ctx)
