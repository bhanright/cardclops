"""Reading collection CSV exports from the common collection apps.

`parse_collection(filename, text, connection)` turns an export from ManaBox, Moxfield, Archidekt,
Deckbox, TCGplayer, Dragon Shield, Delver Lens or Helvault, or any CSV that names its cards by
Scryfall ID, set and collector number, or just name, into rows for the holdings table. Each row is
resolved to a Scryfall printing through the card cache (printings, oracle_cards, sets and cards).
Nothing is written to the database; the caller decides what to do with the result.

The formats, as checked against sample exports in September 2026:

- ManaBox:  Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,
            Purchase price,Misprint,Altered,Signed,Condition,Language,Proxy,Purchase price currency,Added
            (conditions mint … poor with underscores, languages en, de, zh_CN, zh_TW …)
- Moxfield: Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,
            Collector Number,Alter,Proxy,Purchase Price  (Edition is a Scryfall set code; Foil is
            foil, etched or empty; languages are English names)
- Archidekt: Quantity,Name,Finish,Condition,Date Added,Language,Purchase Price,Tags,Edition Name,
            Edition Code,Multiverse Id,Scryfall ID,Collector Number  (Finish Normal/Foil/Etched,
            conditions NM/LP/MP/HP/D, languages EN, JP, KR, CS, CT …)
- Deckbox:  Count,Tradelist Count,Name,Edition,Edition Code,Card Number,Condition,Language,Foil,
            Signed,Artist Proof,Altered Art,Misprint,Promo,Textless,Printing Id,Printing Note,Tags,
            My Price,Cost,Rarity,Price,TcgPlayer ID,Scryfall ID  (older exports stop at My Price;
            Edition is a set *name* such as "Extras: Modern Horizons 2"; Deckbox renumbers some
            special products, so its Scryfall ID wins when present)
- TCGplayer app: Quantity,Name,Simple Name,Set,Card Number,Set Code,Printing,Condition,Language,
            Rarity,Product ID,SKU  (Name is decorated, "Plains (267) - Full Art"; Simple Name is not)
  TCGplayer seller inventory: TCGplayer Id,Product Line,Set Name,Product Name,Title,Number,Rarity,
            Condition,Printing,…,Total Quantity,…
- Dragon Shield: Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,
            Condition,Printing,Language,Price Bought,Date Bought[,LOW,MID,MARKET]  (conditions run
            together, NearMint/LightPlayed; tokens are named "Clue Token"; may start with "sep=,")
- Delver Lens: columns are chosen by the user. Seen: Name, Edition, Edition code, Collector's number,
            QuantityX ("2x"), Quantity, Reg Qty + Foil Qty, Foil ("Foil" or empty), Scryfall ID,
            Acquired Price ("$13.94"), List name.
- Helvault: collector_number,estimated_price,extras,language,name,oracle_id,quantity,rarity,
            scryfall_id,set_code,set_name  (extras is a "/"-separated list: foil, etchedFoil)
"""
import csv
import io
import re
import unicodedata
from datetime import datetime

FINISHES = ("normal", "foil", "etched")

# ---- small value parsers -----------------------------------------------------------------------


def _key(text):
    """Header key: lower case letters and digits only ("Collector's number" -> "collectorsnumber")."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "Æ": "Ae", "æ": "ae", "Œ": "Oe", "œ": "oe"})


def fold(name):
    """Card name key: lower case, accents dropped, "Fire/Ice" and "Fire // Ice" alike."""
    name = unicodedata.normalize("NFKD", name.translate(_QUOTES)).encode("ascii", "ignore").decode()
    name = re.sub(r"\s*/{1,3}\s*", " // ", name.strip().lower())
    return re.sub(r"\s+", " ", name)


def _name_keys(name):
    """Every key a printing answers to: its full name and each face of a split or double-faced card."""
    full = fold(name)
    keys = [full]
    if " // " in full:
        keys += [face for face in full.split(" // ") if face and face not in keys]
    return keys


def _name_variants(name):
    """Keys to try for a name as an app wrote it, most literal first.

    TCGplayer decorates names ("Plains (267) - Full Art", "Orcish Bowmasters (Borderless)") and
    Dragon Shield calls tokens "Clue Token"; the plain name is tried after the literal one.
    """
    keys = [fold(name)]
    plain = re.sub(r"\s+-\s+[^/]*$", "", name)                    # " - Full Art"
    plain = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*", " ", plain).strip()  # "(267)", "[Borderless]"
    for variant in (plain, re.sub(r"\s+token$", "", plain, flags=re.I)):
        key = fold(variant)
        if key and key not in keys:
            keys.append(key)
    if " // " in keys[0]:                              # "Brazen Borrower // Petty Theft" -> front face
        keys.append(keys[0].split(" // ")[0])
    return keys


# Conditions in ManaBox's vocabulary (gallery/query.py CONDITION_ORDER). TCGplayer's scale maps the
# way the gallery's search aliases do: LP -> light_played, MP and HP -> played, damaged -> poor.
CONDITIONS = {
    "mint": "mint", "m": "mint", "mt": "mint",
    "nearmint": "near_mint", "nm": "near_mint", "nmm": "near_mint", "nearmintmint": "near_mint",
    "excellent": "excellent", "ex": "excellent",
    "good": "good", "gd": "good", "goodlightlyplayed": "good",
    "lightplayed": "light_played", "lightlyplayed": "light_played", "lp": "light_played",
    "slightlyplayed": "light_played", "sp": "light_played",
    "played": "played", "pl": "played", "moderatelyplayed": "played", "mp": "played",
    "heavilyplayed": "played", "hp": "played",
    "poor": "poor", "po": "poor", "damaged": "poor", "dmg": "poor", "dm": "poor", "d": "poor",
}

# Scryfall's language codes, which ManaBox also writes (except zh_CN / zh_TW for Chinese).
LANGUAGES = {
    "en": "en", "english": "en",
    "es": "es", "sp": "es", "spanish": "es",
    "fr": "fr", "french": "fr",
    "de": "de", "german": "de",
    "it": "it", "italian": "it",
    "pt": "pt", "ptbr": "pt", "portuguese": "pt", "portuguesebrazil": "pt", "brazilianportuguese": "pt",
    "ja": "ja", "jp": "ja", "japanese": "ja",
    "ko": "ko", "kr": "ko", "korean": "ko",
    "ru": "ru", "russian": "ru",
    "zhs": "zhs", "zhcn": "zhs", "cs": "zhs", "zh": "zhs", "zhhans": "zhs", "chinese": "zhs",
    "simplifiedchinese": "zhs", "chinesesimplified": "zhs",
    "zht": "zht", "zhtw": "zht", "ct": "zht", "zhhant": "zht",
    "traditionalchinese": "zht", "chinesetraditional": "zht",
    "he": "he", "hebrew": "he", "la": "la", "latin": "la", "grc": "grc", "ancientgreek": "grc",
    "greek": "grc", "ar": "ar", "arabic": "ar", "sa": "sa", "sanskrit": "sa", "ph": "ph",
    "phyrexian": "ph", "qya": "qya", "quenya": "qya",
}

RARITIES = {"mythicrare": "mythic", "m": "mythic", "r": "rare", "u": "uncommon", "c": "common",
            "land": "common", "basicland": "common", "l": "common", "s": "special", "t": "common", "token": "common"}

_FALSE = {"", "false", "no", "n", "0", "none", "normal", "nonfoil", "notfoil", "regular", "off"}
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


def _condition(text):
    """(condition in ManaBox's words or None, True when the text also says foil: TCGplayer's "Near Mint Foil")."""
    key = _key(text)
    foil = False
    for suffix in ("holofoil", "foil"):
        if key.endswith(suffix) and key != suffix:
            key, foil = key[: -len(suffix)], True
            break
    return CONDITIONS.get(key), foil


def _language(text):
    key = re.sub(r"[^a-z]", "", (text or "").lower())
    if not key:
        return None
    return LANGUAGES.get(key) or (key if len(key) <= 3 else None)


def _finish(cells):
    """normal | foil | etched from every finish-like (column key, cell) of a row: Foil, Finish, Printing, extras …"""
    found = "normal"
    for column, value in cells:
        text = _key(value)
        if text in _FALSE or text.startswith("non"):
            continue
        if "etched" in text or column == "etched":
            return "etched"
        if "foil" in text or text in ("true", "yes", "y", "1", "x", "f", "premium"):
            found = "foil"
    return found


def _flag(text):
    return 0 if _key(text) in _FALSE else 1


def _price(text):
    """(amount, currency or None) from "0.20", "$13.94", "1,50 €"."""
    text = (text or "").strip()
    if not text:
        return None, None
    currency = next((code for symbol, code in _CURRENCY_SYMBOLS.items() if symbol in text), None)
    number = re.sub(r"[^0-9.,\-]", "", text)
    if "," in number and "." not in number:
        number = number.replace(",", ".")
    number = number.replace(",", "")
    try:
        return float(number), currency
    except ValueError:
        return None, currency


def _quantity(text):
    """Copies from "2", "2x", "2.0"; None when the cell says nothing."""
    text = (text or "").strip().lower().rstrip("x").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _date(text):
    """An ISO date or timestamp; ISO text passes through untouched, "3/28/2021" becomes "2021-03-28"."""
    text = (text or "").strip()
    if not text:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text
    for pattern in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.date().isoformat() if parsed.time() == datetime.min.time() else parsed.isoformat()
    return None


def _rarity(text):
    key = _key(text)
    if not key:
        return None
    return RARITIES.get(key, key)


def _natural(number):
    """Sort key for collector numbers: 2 < 10 < 10a < 10★ < S3."""
    match = re.match(r"(\d+)(.*)", number or "")
    return (0, int(match.group(1)), match.group(2)) if match else (1, 0, number or "")


# ---- columns and formats -----------------------------------------------------------------------

# Field -> header keys that hold it, in order of preference. A row takes the first non-empty one.
COLUMNS = {
    "scryfall_id": ("scryfallid", "scryfalluuid", "scryfallcardid", "scryfall"),
    "name": ("simplename", "name", "cardname", "productname", "card", "cardtitle", "title"),
    "set_code": ("setcode", "editioncode", "setid", "code"),
    "set_name": ("setname", "editionname", "expansion", "expansionname"),
    "set": ("set", "edition"),                   # a code or a name, depending on the app
    "number": ("collectornumber", "collectorsnumber", "cardnumber", "number", "cn", "collectorno", "no"),
    "quantity": ("quantity", "count", "qty", "quantityx", "totalquantity", "amount", "copies", "have"),
    "normal_quantity": ("regqty", "regularqty", "regularquantity", "nonfoilquantity", "nonfoilqty",
                        "normalquantity", "normalqty"),
    "foil_quantity": ("foilqty", "foilquantity", "foilcount"),
    "finish": ("foil", "finish", "printing", "foiletched", "extras", "isfoil", "etched", "foilvariant"),
    "condition": ("condition", "cond"),
    "language": ("language", "lang"),
    "rarity": ("rarity",),
    "purchase_price": ("purchaseprice", "acquiredprice", "acquired", "pricebought", "myprice", "cost",
                       "buyprice", "paid", "costbasis"),
    "currency": ("purchasepricecurrency", "currency"),
    "manabox_id": ("manaboxid",),
    "misprint": ("misprint",),
    "altered": ("altered", "alter", "alteredart"),
    "signed": ("signed",),
    "proxy": ("proxy",),
    "added_at": ("added", "dateadded", "addedat", "addeddate", "datebought", "created", "createdat",
                 "lastmodified", "modified"),
    "product_line": ("productline", "game"),
}

# Each format: (name, test on the set of header keys). The first that passes names the file.
# Every format is read by the same column table above, so a misnamed file still imports.
FORMATS = (
    ("manabox", lambda k: "manaboxid" in k or {"setcode", "setname", "collectornumber", "foil",
                                                "scryfallid", "purchasepricecurrency"} <= k),
    ("helvault", lambda k: {"extras", "scryfallid"} <= k or {"extras", "oracleid", "setcode"} <= k),
    ("dragonshield", lambda k: {"foldername", "tradequantity"} <= k or {"tradequantity", "cardname", "printing"} <= k),
    ("archidekt", lambda k: {"finish", "editioncode"} <= k or {"editionname", "multiverseid"} <= k),
    ("deckbox", lambda k: {"count", "tradelistcount", "edition"} <= k
        and bool(k & {"cardnumber", "artistproof", "textless", "myprice", "editioncode", "printingnote"})),
    ("moxfield", lambda k: {"count", "tradelistcount", "edition"} <= k),
    ("tcgplayer", lambda k: "simplename" in k or {"tcgplayerid", "productline"} <= k
        or {"productname", "totalquantity"} <= k or {"quantity", "name", "setcode", "printing"} <= k),
    ("delverlens", lambda k: bool(k & {"quantityx", "collectorsnumber", "regqty", "foilqty", "listname",
                                       "foiletched", "acquiredprice", "tcgplayerproductid"})),
)

FORMAT_LABELS = {
    "manabox": "ManaBox", "moxfield": "Moxfield", "archidekt": "Archidekt", "deckbox": "Deckbox",
    "tcgplayer": "TCGplayer", "dragonshield": "Dragon Shield", "delverlens": "Delver Lens",
    "helvault": "Helvault", "generic": "CSV",
}


def _header_cells(header):
    if isinstance(header, str):
        header = next(csv.reader([header.lstrip("\ufeff")], delimiter=_delimiter(header)), [])
    return list(header)


def detect_format(header):
    """The app a CSV header came from ("moxfield", …), or "generic". Takes the header row as a list or a line."""
    keys = {_key(cell) for cell in _header_cells(header)}
    for name, test in FORMATS:
        if test(keys):
            return name
    return "generic"


def _delimiter(line):
    counts = {sep: line.count(sep) for sep in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


# ---- the card cache ----------------------------------------------------------------------------

class Catalog:
    """The card cache in memory, for resolving thousands of rows quickly.

    A printing is a tuple (scryfall_id, set_code, collector_number, name). Lookups by Scryfall ID and
    by set and number are built at once; the name and set-name indexes only when a row needs them.
    """

    def __init__(self, connection):
        self.connection = connection
        self.by_id = {}
        self.by_set_number = {}
        for set_code, number, scryfall_id, _oracle_id, name in connection.execute(
                "SELECT set_code, collector_number, scryfall_id, oracle_id, name FROM printings"):
            printing = (scryfall_id, set_code, number, name)
            self.by_id[scryfall_id] = printing
            self.by_set_number[(set_code, number)] = printing
        self._names = None
        self._sets = None
        self._set_memo = {}

    # -- by Scryfall ID
    def printing_by_id(self, scryfall_id):
        printing = self.by_id.get(scryfall_id)
        if printing is None:
            # The cards table also holds printings fetched by ID that the default bulk file leaves
            # out, such as a non-English printing a previous ManaBox import named.
            row = self.connection.execute(
                "SELECT scryfall_id, json_extract(raw, '$.set'), json_extract(raw, '$.collector_number'), name "
                "FROM cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
            if row is not None:
                printing = tuple(row)
                self.by_id[scryfall_id] = printing
        return printing

    # -- sets
    def _load_sets(self):
        codes, released = set(), {}
        exact, relaxed = {}, {}
        rows = self.connection.execute(
            "SELECT code, name, released_at FROM sets ORDER BY "
            "CASE set_type WHEN 'core' THEN 0 WHEN 'expansion' THEN 0 WHEN 'token' THEN 2 ELSE 1 END, released_at").fetchall()
        for code, name, released_at in rows:
            codes.add(code)
            released[code] = released_at or ""
            if name:
                exact.setdefault(_set_key(name), code)
                relaxed.setdefault(_set_key(name, relaxed=True), code)
        codes |= {set_code for set_code, _ in self.by_set_number}
        self._sets = (codes, exact, relaxed, released)

    def set_codes(self, value):
        """Scryfall set codes a cell might mean: it may be a code ("MID", "tmh2") or a name ("Magic 2014 Core Set")."""
        value = (value or "").strip()
        if not value:
            return []
        if value not in self._set_memo:
            self._set_memo[value] = self._set_codes(value)
        return self._set_memo[value]

    def _set_codes(self, value):
        if self._sets is None:
            self._load_sets()
        codes, exact, relaxed, _ = self._sets
        found = []

        def add(code):
            if code and code not in found:
                found.append(code)

        if value.lower() in codes:
            add(value.lower())
        candidates = [value]
        inner = re.search(r"\(([A-Za-z0-9]{2,6})\)\s*$", value)            # "Magic 2014 (M14)"
        if inner:
            if inner.group(1).lower() in codes:
                add(inner.group(1).lower())
            candidates.append(value[: inner.start()])
        extras = re.match(r"extras:\s*(.+)$", value, re.I)                  # Deckbox's token sets
        if extras:
            candidates = [extras.group(1) + " Tokens", extras.group(1)] + candidates
        for candidate in candidates:
            add(exact.get(_set_key(candidate)))
        for candidate in candidates:
            add(relaxed.get(_set_key(candidate, relaxed=True)))
        return found

    # -- names
    def _load_names(self):
        by_name_set = {}      # (name key, set code) -> [printing]
        by_name_any = {}      # name key -> [printing], for cards oracle_cards leaves out (tokens)
        fold_cache = {}
        for printing in self.by_id.values():
            name = printing[3]
            keys = fold_cache.get(name)
            if keys is None:
                keys = fold_cache[name] = _name_keys(name)
            for key in keys:
                by_name_set.setdefault((key, printing[1]), []).append(printing)
                by_name_any.setdefault(key, []).append(printing)
        by_name, by_face = {}, {}      # name key -> representative printing, by whole name or by one face
        for name, scryfall_id in self.connection.execute("SELECT name, scryfall_id FROM oracle_cards"):
            printing = self.by_id.get(scryfall_id) or (scryfall_id, None, None, name)
            keys = _name_keys(name)
            by_name.setdefault(keys[0], printing)
            for key in keys[1:]:
                by_face.setdefault(key, printing)
        # Tokens and other cards oracle_cards leaves out: the newest printing by that whole name.
        for key, printings in by_name_any.items():
            if key not in by_name:
                whole = [p for p in printings if fold_cache[p[3]][0] == key]
                if whole:
                    by_name[key] = max(whole, key=lambda p: (self.released(p[1]), _natural(p[2])))
        for key, printing in by_face.items():          # a whole name wins over another card's face
            by_name.setdefault(key, printing)
        for key, printings in by_name_any.items():
            if key not in by_name:
                by_name[key] = max(printings, key=lambda p: (self.released(p[1]), _natural(p[2])))
        self._names = (by_name_set, by_name_any, by_name)

    def names(self):
        if self._names is None:
            self._load_names()
        return self._names

    def released(self, set_code):
        if self._sets is None:
            self._load_sets()
        return self._sets[3].get(set_code, "")


_SET_WORDS_DROPPED = {"edition", "core", "set", "the", "series", "cards"}


def _set_key(name, relaxed=False):
    words = re.findall(r"[a-z0-9]+", fold(name).replace("&", " and "))
    if relaxed:
        words = [word for word in words if word not in _SET_WORDS_DROPPED]
    return " ".join(words)


def _number_candidates(number):
    number = (number or "").strip()
    if not number:
        return []
    found = [number]
    for variant in (number.lower(), number.lstrip("0") or number, number.replace("*", "★")):
        if variant not in found:
            found.append(variant)
    return found


# ---- resolving a row ---------------------------------------------------------------------------

def _resolve(catalog, name, set_values, number, scryfall_id):
    """(printing, note or None) for a row, or (None, reason). A note marks an approximate match."""
    if scryfall_id:
        printing = catalog.printing_by_id(scryfall_id.strip().lower())
        if printing:
            return printing, None
    codes = []
    for value in set_values:
        for code in catalog.set_codes(value):
            if code not in codes:
                codes.append(code)
    set_text = next((value for value in set_values if value and value.strip()), "")
    if not number and name:
        decorated = re.search(r"\((\d+[a-z★]?)\)", name)       # TCGplayer's "Plains (267) - Full Art"
        number = decorated.group(1) if decorated else None
    numbers = _number_candidates(number)
    wanted = _name_variants(name) if name else []

    other_card = None                        # the printing at the set and number, if it has another name
    for code in codes:
        for candidate in numbers:
            printing = catalog.by_set_number.get((code, candidate))
            if printing is None:
                continue
            if not wanted or set(wanted) & set(_name_keys(printing[3])):
                return printing, None
            other_card = other_card or printing
    if not wanted:
        if codes and numbers:
            return None, f"No printing {set_text} #{number}"
        if set_text and not codes:
            return None, f"Unknown set “{set_text}” and no card name"
        return None, "The row names no card: no name, Scryfall ID, or set and collector number"

    by_name_set, by_name_any, by_name = catalog.names()
    # Name and set: exact when the set has one printing of the card.
    for key in wanted:
        in_set = [p for code in codes for p in by_name_set.get((key, code), ())]
        if in_set:
            # A printing by this whole name before one where it is a face ("Forest" before "Forest // Forest").
            distinct = sorted({p[0]: p for p in in_set}.values(),
                              key=lambda p: (fold(p[3]) != key, _natural(p[2])))
            if len(distinct) == 1:
                return distinct[0], None
            note = (f"{len(distinct)} printings of {distinct[0][3]} in {distinct[0][1].upper()}"
                    + (f"; none numbered {number}" if number else "") + f"; used #{distinct[0][2]}")
            return distinct[0], note
    # Name and collector number in any set: settles a set the app names its own way.
    if numbers:
        for key in wanted:
            matches = [p for p in by_name_any.get(key, ()) if p[2] in numbers]
            if len({p[0] for p in matches}) == 1:
                note = None if not codes else f"{set_text} has no {matches[0][3]}; used {matches[0][1].upper()} #{matches[0][2]}"
                return matches[0], note
    for key in wanted:
        printing = by_name.get(key)
        if printing is not None:
            where = (f"no printing matched {set_text}" + (f" #{number}" if number else "")) if set_text \
                else "no set given"
            return printing, f"{where}; used a representative printing"
    if other_card is not None:
        return other_card, f"{other_card[1].upper()} #{other_card[2]} is {other_card[3]}, not “{name}”"
    return None, f"No card named “{name}”"


# ---- reading the file --------------------------------------------------------------------------

def _rows(text):
    """A csv reader over the text of a CSV file, with its delimiter guessed from the first line."""
    text = text.lstrip("\ufeff")                         # a byte-order mark, as Excel writes
    lines = text.splitlines()
    delimiter = None
    for line in lines[:5]:
        match = re.match(r'^"?sep=(.)"?\s*$', line.strip(), re.I)      # Excel's "sep=," hint
        if match:
            delimiter = match.group(1)
            break
        if line.strip():
            break
    if delimiter is None:
        first = next((line for line in lines if line.strip() and not line.lower().startswith("sep=")), "")
        delimiter = _delimiter(first)
    return csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)


def _find_header(reader):
    """The header cells: the first row that names a column we know, skipping any preamble."""
    known = {key for keys in COLUMNS.values() for key in keys}
    for _ in range(10):
        try:
            cells = next(reader)
        except StopIteration:
            break
        keys = {_key(cell) for cell in cells}
        if (keys & known and len(keys) > 1) or keys & {"name", "cardname", "scryfallid"}:
            return cells
    raise ValueError("This file doesn't look like a collection CSV: no column for the card name, "
                     "set or Scryfall ID")


def _column_index(header):
    """Field -> list of column positions, in the preference order of COLUMNS."""
    positions = {}
    for index, cell in enumerate(header):
        positions.setdefault(_key(cell), index)
    return {field: [positions[key] for key in keys if key in positions] for field, keys in COLUMNS.items()}


def parse_collection(filename, text, connection, progress=None):
    """Read a collection export into holdings rows, matched to Scryfall printings.

    Returns {"format", "rows", "unmatched", "approximate", "total_rows"}: `rows` are dicts with the
    holdings table's columns; `unmatched` lists {"line", "text", "reason"} for rows left out;
    `approximate` lists {"line", "text", "note", "scryfall_id", "name"} for rows included with a
    printing chosen by name. `progress(done, total)` is called every 500 rows. Raises ValueError
    when the text has no header naming a card column. `filename` is not needed to read the file
    (the header says which app wrote it); it is accepted so callers can pass what they have.
    """
    reader = _rows(text)
    header = _find_header(reader)
    fmt = detect_format(header)
    columns = _column_index(header)
    finish_columns = [(index, _key(header[index])) for index in columns["finish"]]
    records = []
    for cells in reader:
        if any(cell.strip() for cell in cells):
            records.append((reader.line_num, cells))
    total = len(records)
    catalog = Catalog(connection)
    delimiter = reader.dialect.delimiter

    def raw(cells):
        out = io.StringIO()
        csv.writer(out, delimiter=delimiter, lineterminator="").writerow(cells)
        return out.getvalue()

    rows, unmatched, approximate = [], [], []
    for done, (line, cells) in enumerate(records, 1):
        if progress and done % 500 == 0:
            progress(done, total)

        def get(field):
            for index in columns[field]:
                if index < len(cells) and cells[index].strip():
                    return cells[index].strip()
            return None

        product_line = get("product_line")
        if product_line and "magic" not in product_line.lower():
            unmatched.append({"line": line, "text": raw(cells), "reason": f"Not a Magic card ({product_line})"})
            continue

        # Copies, by finish: one quantity column, or Delver Lens's separate regular and foil counts.
        condition, condition_foil = _condition(get("condition"))
        finish = _finish([(key, cells[i]) for i, key in finish_columns if i < len(cells)])
        if condition_foil and finish == "normal":
            finish = "foil"
        split = get("normal_quantity") is not None or get("foil_quantity") is not None
        if split:
            counts = [(finish, _quantity(get("normal_quantity")) or 0),
                      ("foil" if finish == "normal" else finish, _quantity(get("foil_quantity")) or 0)]
        else:
            quantity = _quantity(get("quantity"))       # no count column, or an empty cell: one copy
            counts = [(finish, 1 if quantity is None else quantity)]
        counts = [(f, n) for f, n in counts if n > 0]
        if not counts:
            unmatched.append({"line": line, "text": raw(cells), "reason": "Quantity is 0"})
            continue

        name = get("name")
        set_values = [value for value in (get("set_code"), get("set"), get("set_name")) if value]
        printing, note = _resolve(catalog, name, set_values, get("number"), get("scryfall_id"))
        if printing is None:
            unmatched.append({"line": line, "text": raw(cells), "reason": note})
            continue
        if note:
            approximate.append({"line": line, "text": raw(cells), "note": note,
                                "scryfall_id": printing[0], "name": printing[3]})

        price, symbol_currency = _price(get("purchase_price"))
        if fmt == "dragonshield" and price == 0:
            price = None                              # Dragon Shield writes 0 when no price was entered
        # ManaBox names a currency on every row; elsewhere a currency only means something beside a price.
        currency = get("currency") if price is not None or fmt == "manabox" else None
        currency = currency or symbol_currency or ("USD" if price is not None else None)
        for row_finish, quantity in counts:
            rows.append({
                "scryfall_id": printing[0],
                "name": printing[3],
                "set_code": printing[1],
                "collector_number": printing[2],
                "finish": row_finish,
                "rarity": _rarity(get("rarity")),
                "quantity": quantity,
                "manabox_id": get("manabox_id"),
                "purchase_price": price,
                "purchase_currency": currency.upper() if currency else None,
                "misprint": _flag(get("misprint")),
                "altered": _flag(get("altered")),
                "signed": _flag(get("signed")),
                "proxy": _flag(get("proxy")),
                "condition": condition,
                "language": _language(get("language")),
                "added_at": _date(get("added_at")),
            })
    if progress:
        progress(total, total)
    return {"format": fmt, "rows": rows, "unmatched": unmatched, "approximate": approximate,
            "total_rows": total}
