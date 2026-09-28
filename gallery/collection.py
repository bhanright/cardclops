"""The collection in memory: one Entry per holdings row, with its card's data flattened.

Search, stats and every API endpoint work from these entries rather than from
SQL, because 20-odd thousand rows fit comfortably in memory and Python
predicates are far easier to write for Magic's rules than SQL is.
"""
import json
from collections import defaultdict
from dataclasses import dataclass, field

COLOR_ORDER = "WUBRG"


@dataclass
class Entry:
    # From the ManaBox row
    row_id: int
    scryfall_id: str
    finish: str                  # normal | foil | etched
    quantity: int
    condition: str
    language: str
    purchase_price: float | None
    added_at: str
    misprint: bool
    # From the Scryfall card
    card: dict                   # the full Scryfall object
    oracle_id: str
    name: str
    layout: str
    mana_cost: str               # faces joined with " // "
    cmc: float
    type_line: str               # faces joined with " // "
    oracle_text: str             # faces joined with "\n//\n"
    colors: frozenset            # letters from WUBRG; union of faces for double-faced cards
    color_identity: frozenset
    keywords: tuple
    power: str | None
    toughness: str | None
    loyalty: str | None
    rarity: str
    set_code: str
    set_name: str
    set_type: str
    collector_number: str
    released_at: str
    artist: str
    legalities: dict
    tags: frozenset = field(default_factory=frozenset)   # Scryfall Tagger function tags
    price_usd: float | None = None    # for this entry's finish
    price_eur: float | None = None
    price_tix: float | None = None
    source: str = "import"            # import (a collection CSV) | manual (added in Cardclops)
    # Filled in by the deck allocator (gallery/decks.py) after loading:
    used: int = 0                     # copies of this row allocated to active decks
    decks: tuple = ()                 # (deck name, status) for every deck whose list includes this card
    # Filled in by gallery/binders.py: (binder name, copies of this row in it) for each binder.
    binders: tuple = ()

    @property
    def spare(self):
        return self.quantity - self.used

    @property
    def value_usd(self):
        return (self.price_usd or 0.0) * self.quantity

    @property
    def faces(self):
        return self.card.get("card_faces") or [self.card]

    @property
    def is_double_faced(self):
        return "image_uris" not in self.card and len(self.faces) > 1


def _joined(card, key, separator):
    faces = card.get("card_faces")
    if card.get(key) and not faces:
        return card[key]
    if faces:
        parts = [face.get(key, "") for face in faces if face.get(key)]
        if parts:
            return separator.join(parts)
    return card.get(key, "") or ""


def _front(card, key):
    if card.get(key) is not None:
        return card[key]
    faces = card.get("card_faces") or [{}]
    return faces[0].get(key)


def _price(card, finish, currency):
    keys = {
        "USD": {"normal": "usd", "foil": "usd_foil", "etched": "usd_etched"},
        "EUR": {"normal": "eur", "foil": "eur_foil", "etched": "eur_foil"},
        "TIX": {"normal": "tix", "foil": "tix", "etched": "tix"},
    }[currency]
    prices = card.get("prices") or {}
    value = prices.get(keys.get(finish, keys["normal"]))
    if value is None and finish == "etched" and currency == "USD":
        value = prices.get("usd_foil")
    return float(value) if value is not None else None


def _colors(card):
    if card.get("colors") is not None:
        return frozenset(card["colors"])
    return frozenset(c for face in card.get("card_faces", []) for c in face.get("colors", []))


def build_entry(holding, card, tags):
    from .ingest import oracle_id_of
    oracle_id = oracle_id_of(card) or card["id"]
    return Entry(
        row_id=holding["row_id"],
        scryfall_id=holding["scryfall_id"],
        finish=holding["finish"],
        quantity=holding["quantity"],
        condition=holding["condition"] or "",
        language=holding["language"] or card.get("lang", "en"),
        purchase_price=holding["purchase_price"],
        added_at=holding["added_at"] or "",
        misprint=bool(holding["misprint"]),
        source=(holding["source"] if "source" in holding.keys() else "import") or "import",
        card=card,
        oracle_id=oracle_id,
        name=card["name"],
        layout=card.get("layout", "normal"),
        mana_cost=_joined(card, "mana_cost", " // "),
        cmc=float(_front(card, "cmc") or 0),     # reversible cards keep cmc on their faces
        type_line=_joined(card, "type_line", " // "),
        oracle_text=_joined(card, "oracle_text", "\n//\n"),
        colors=_colors(card),
        color_identity=frozenset(card.get("color_identity", [])),
        keywords=tuple(card.get("keywords", [])),
        power=_front(card, "power"),
        toughness=_front(card, "toughness"),
        loyalty=_front(card, "loyalty"),
        rarity=card.get("rarity", ""),
        set_code=card.get("set", ""),
        set_name=card.get("set_name", ""),
        set_type=card.get("set_type", ""),
        collector_number=card.get("collector_number", ""),
        released_at=card.get("released_at", ""),
        artist=card.get("artist", "") or _front(card, "artist") or "",
        legalities=card.get("legalities", {}),
        tags=tags.get(oracle_id, frozenset()),
        price_usd=_price(card, holding["finish"], "USD"),
        price_eur=_price(card, holding["finish"], "EUR"),
        price_tix=_price(card, holding["finish"], "TIX"),
    )


# Scryfall fields nothing in Cardclops reads (checked across gallery/ and static/). Dropping them
# from the in-memory copy (the database keeps the whole object) saves memory, which matters on
# phones and small servers. A feature that needs one of them should remove it from this list.
UNUSED_FIELDS = frozenset("""
    all_parts arena_id artist_ids attraction_lights booster card_back_id cardmarket_id content_warning
    highres_image illustration_id image_status mtgo_foil_id mtgo_id multiverse_ids object penny_rank
    preview printed_name printed_text printed_type_line prints_search_uri related_uris rulings_uri
    scryfall_set_uri security_stamp set_id set_search_uri set_uri story_spotlight tcgplayer_etched_id
    tcgplayer_id uri variation_of""".split())
# The image sizes the image server hands out; Scryfall lists about a dozen.
IMAGE_SIZES_USED = ("small", "normal", "large", "png", "art_crop", "border_crop")


def slim(card):
    """The card without the fields Cardclops never reads."""
    for key in UNUSED_FIELDS & card.keys():
        del card[key]
    for holder in [card, *(card.get("card_faces") or [])]:
        uris = holder.get("image_uris")
        if uris:
            holder["image_uris"] = {size: uris[size] for size in IMAGE_SIZES_USED if size in uris}
    return card


@dataclass
class TagIndex:
    """Scryfall Tagger tags with their hierarchy, so otag:removal also finds removal-destroy."""
    descendants: dict            # slug -> frozenset of itself and every descendant slug
    labels: dict                 # slug -> description

    def expand(self, slug):
        return self.descendants.get(slug, frozenset({slug}))


class Collection:
    def __init__(self, connection):
        self.connection = connection
        self.reload()

    def reload(self):
        connection = self.connection
        tags_by_oracle = defaultdict(set)
        for row in connection.execute("SELECT slug, oracle_id FROM oracle_taggings"):
            tags_by_oracle[row["oracle_id"]].add(row["slug"])
        tags_by_oracle = {k: frozenset(v) for k, v in tags_by_oracle.items()}

        cards = {row["scryfall_id"]: slim(json.loads(row["raw"]))
                 for row in connection.execute("SELECT scryfall_id, raw FROM cards")}
        self.entries = [
            build_entry(holding, cards[holding["scryfall_id"]], tags_by_oracle)
            for holding in connection.execute("SELECT * FROM holdings ORDER BY row_id")
            if holding["scryfall_id"] in cards
        ]
        self.by_row = {entry.row_id: entry for entry in self.entries}
        self.by_scryfall_id = defaultdict(list)
        self.by_oracle_id = defaultdict(list)
        for entry in self.entries:
            self.by_scryfall_id[entry.scryfall_id].append(entry)
            self.by_oracle_id[entry.oracle_id].append(entry)
        self.tag_index = self._load_tag_index()

    def _load_tag_index(self):
        rows = self.connection.execute("SELECT slug, tag_id, description, child_ids FROM oracle_tags").fetchall()
        slug_by_id = {row["tag_id"]: row["slug"] for row in rows}
        children = {row["slug"]: [slug_by_id[i] for i in json.loads(row["child_ids"]) if i in slug_by_id] for row in rows}
        descendants = {}

        def collect(slug, trail=()):
            if slug in descendants:
                return descendants[slug]
            found = {slug}
            for child in children.get(slug, []):
                if child not in trail:        # Tagger's graph is not guaranteed acyclic
                    found |= collect(child, trail + (slug,))
            descendants[slug] = frozenset(found)
            return descendants[slug]

        for slug in children:
            collect(slug)
        return TagIndex(descendants, {row["slug"]: row["description"] or "" for row in rows})
