"""'What can I build?': every commander you own, ranked by how much of a Commander deck your
spare cards could fill, and a proposed 100-card list for any one of them.

The rules are in docs/TOOLS.md ("What can I build?"). Decisions this module makes on top of them:

- Coverage is counted per color identity, not per commander. There are only 32 identities, so
  the pool, the lands, and the ramp/draw/removal/wipes parts are worked out once for each, with
  every part taking its cards in EDHREC order. Only the synergy part depends on the commander,
  through similarity; it changes coverage only when the pool is too small to fill it. The draft
  picks within parts by fit first, so its shortfalls can differ slightly from the ranking's.
- The commander itself is left out of its own pool. When it would have filled one of the
  function parts (a legendary mana dork, say), the parts are recounted without it.
- Lands fill only the lands part. A land that makes none of the identity's colors (Reliquary
  Tower in a green deck) counts nowhere, and the draft does not play it. In a colorless deck,
  lands that make {C} count.
- A card whose part is already full falls through to the next part it fits, and in the end to
  synergy, which takes any playable card.
- "Fit" is similarity to the commander times `playedness` (below), so that near-vanilla cards
  nobody plays do not pass for synergy. The synergy threshold applies to fit.
- The draft's synergy part orders cards by fit plus a bonus for EDHREC's top 1,000 (see
  STAPLE_BONUS), not by fit and then EDHREC rank as TOOLS.md reads; function parts take cards
  that fit strongly (STRONG_FIT) first, then EDHREC order.
- The ranking breaks ties in coverage by the mean fit of the synergy part, where a card below
  SYNERGY_THRESHOLD counts as 0 (it is there as a playable card, not because it fits), then by
  the commander's EDHREC rank, then by name.
"""
import re
import time
from collections import Counter, defaultdict

from .deckstats import (can_be_commander, COLORS, DeckCard, card_pips, category_of, compute_deck_stats, front_type_line,
                        is_basic_land, is_land, land_source_keys, mana_value, oracle_text)
from .decks import fold
from .manafix import enters_tapped

PART_TARGETS = {"lands": 36, "ramp": 10, "draw": 10, "removal": 8, "wipes": 3, "synergy": 32}
PART_ORDER = tuple(PART_TARGETS)
FUNCTION_PARTS = ("ramp", "draw", "removal", "wipes")
TEMPLATE_SIZE = 99

# Raw similarity scores (gallery/similar.py: cosine over rules text, types and Tagger tags) run
# high. Over the 1,510 commanders in the owner's collection, the median commander has about 200
# cards at 0.10 or more and 87 at 0.15, and at 0.08 almost every card sharing a keyword passes:
# a French-vanilla commander with Menace "fits" every menace common. The threshold is therefore
# applied to fit (similarity x playedness), where the median commander's 10th-best card scores
# 0.20 and its 30th 0.14. Between 0.12 and 0.18 the cards are still on theme (Meren: Stitch
# Together, Pitiless Plunderer, Black Market; Krenko: Goblin Surprise, Empty the Warrens; The
# Ur-Dragon: Lathliss, Silumgar); below 0.10 they drift. So 0.12.
SYNERGY_THRESHOLD = 0.12
# In the synergy part, a card's fit competes with how much of a staple it is: EDHREC's top 1,000
# cards get up to STAPLE_BONUS (Swiftfoot Boots, #12, gets 0.198; #500 gets 0.1). Pure fit order
# filled Atraxa's 32 slots with keyword creatures and left out Counterspell and Heroic
# Intervention; pure EDHREC order ignores the commander. The blend keeps the best of each.
STAPLE_BONUS = 0.2
STAPLE_RANKS = 1000
# Cards picked for a function part (ramp, draw, removal, wipes) ahead of EDHREC order must fit
# this well. At 0.18, Atraxa's ramp took Skirge Familiar over Cultivate; at 0.25 Krenko still
# gets Siege-Gang Commander and Goblin War Strike as removal.
STRONG_FIT = 0.25
# How many of a commander's most similar cards the fallback path (a similarity object without
# the index internals) asks for; the fast path scores every card.
SIMILAR_LIMIT = 600
# gallery/similar.py skips terms whose posting list is longer than this; the fast path must too.
SIMILAR_MAX_POSTING = 4000

MAX_TOP_END = 12              # nonland cards at mana value 6+ (commanders included)
# Land count: 36, one fewer when the chosen spells average under 2.75 mana, one more at 3.5 or over
# (a typical Commander deck's nonland average is about 3.0-3.3).
LAND_TARGET = PART_TARGETS["lands"]
LOW_CURVE, HIGH_CURVE = 2.75, 3.5
# Lands: the share of the land count kept for basics (fetches, Cultivate and Farseek need them),
# at most this many one-color utility lands, at most one fetch per this many basics, and what a
# land that enters tapped is worth next to an untapped one.
MIN_BASICS_SHARE = {"mono": 0.5, "multi": 0.25}
MAX_UTILITY_LANDS = 5
BASICS_PER_FETCH = 4
TAPPED_LAND_VALUE = 0.5

UNRANKED = 10 ** 7            # cards Scryfall has no EDHREC rank for sort after every ranked card
# Tagger tags ramp that helps an opponent (Path to Exile gives them a basic); that is not ramp.
OPPONENT_RAMP_TAGS = frozenset({"donate-rampant-growth"})
NOT_PLAYABLE_LAYOUTS = {"token", "double_faced_token", "emblem", "art_series", "planar", "scheme",
                        "vanguard", "augment", "host"}
BASIC_NAMES = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest", "C": "Wastes"}
ROLE_ORDER = ("commander", "ramp", "draw", "removal", "wipes", "synergy", "filler", "lands")


COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
REWARDS_COLOR = re.compile(r"\b(white|blue|black|red|green) (?:creature |instant |sorcery |permanent )?spells? you cast")


def playedness(edhrec_rank):
    """How much a card's similarity counts, from how widely it is played: 1 for EDHREC's top
    5,000, falling to 0.5 at 30,000 and below; 0.7 for cards EDHREC has no rank for (often new).

    Similarity alone favors near-vanilla cards: a French-vanilla commander such as Zhao, the
    Seething Flame (just Menace) scores 0.7 with every unplayed common that has menace, which
    put such commanders at the top of the ranking and filled their drafts with those commons.
    Weighting by playedness keeps the fit that matters (Krenko's Goblins, Atraxa's proliferate
    cards) and drops the noise.
    """
    if edhrec_rank >= UNRANKED:
        return 0.7
    if edhrec_rank <= 5000:
        return 1.0
    return max(0.5, 1.0 - 0.5 * (edhrec_rank - 5000) / 25000)


def staple_bonus(edhrec_rank):
    return STAPLE_BONUS * max(0.0, 1.0 - edhrec_rank / STAPLE_RANKS)


def _ordered(colors):
    return [c for c in COLORS if c in colors]


class _Card:
    """One distinct card (oracle id) you own, with the facts the builder needs."""

    def __init__(self, entries, tag_sets):
        self.entries = entries
        lead = max(entries, key=lambda e: ((e.price_usd or 0), e.quantity))
        card = lead.card
        self.card = card
        self.oracle_id = lead.oracle_id
        self.name = lead.name
        self.tags = lead.tags
        self.identity = frozenset(lead.color_identity)
        self.spare = sum(max(e.spare, 0) for e in entries)
        self.owned = sum(e.quantity for e in entries)
        self.land = is_land(card)
        self.basic = is_basic_land(card)
        self.mv = mana_value(card)
        self.edhrec = card.get("edhrec_rank") or UNRANKED
        self.playedness = playedness(self.edhrec)
        self.legal = (lead.legalities or {}).get("commander") == "legal"
        type_line = front_type_line(card)
        main_types = type_line.split("—")[0].split()
        self.playable = (self.legal and lead.layout not in NOT_PLAYABLE_LAYOUTS
                         and "Token" not in main_types and "Emblem" not in main_types)
        # Backgrounds lead only alongside a partner, so they aren't ranked on their own.
        self.can_lead = self.legal and can_be_commander(card) and "Background" not in type_line
        self.background = "Background" in type_line
        # Colors whose spells the card rewards ("Black spells you cast cost {1} less"): Jet
        # Medallion is colorless, so it passes the identity check, but does nothing in a red deck.
        self.rewards_colors = frozenset(COLOR_WORDS[word] for word in REWARDS_COLOR.findall(oracle_text(card).lower()))
        self.tapped = self.land and enters_tapped(card)
        ramp, draw, removal, sweeper = tag_sets
        tags = self.tags
        self.parts = set()
        if not self.land:
            if tags & ramp and not tags & OPPONENT_RAMP_TAGS:
                self.parts.add("ramp")
            if tags & draw:
                self.parts.add("draw")
            if tags & removal and not tags & sweeper:
                self.parts.add("removal")
            if tags & sweeper:
                self.parts.add("wipes")
        self._land_info = {}

    def land_info(self, identity):
        """(colors of the identity it makes or fetches, source keys, is a fetch, makes any color)
        for a land in a deck of that identity."""
        info = self._land_info.get(identity)
        if info is None:
            keys, colors, fetches = land_source_keys(self.card, set(identity), set(identity))
            info = (frozenset(colors) & identity, frozenset(keys), fetches, len(colors) == 5)
            self._land_info[identity] = info
        return info

    def makes_identity_mana(self, identity):
        colors, keys, _, _ = self.land_info(identity)
        return bool(colors) if identity else "C" in keys

    def spare_entries(self):
        return [e for e in self.entries if e.spare > 0]


class _IdentityPool:
    """The pool and the identity-level part counts for one color identity."""

    def __init__(self, identity, cards, basics, exclude=frozenset()):
        self.identity = identity
        members = [c for c in cards if c.spare > 0 and c.playable and not c.basic
                   and c.identity <= identity and c.oracle_id not in exclude]
        self.pool_size = len(members)
        self.nonbasic_lands = [c for c in members if c.land and c.makes_identity_mana(identity)]
        self.basic_count = sum(basics.get(color, 0) for color in (_ordered(identity) or ["C"]))
        self.spells = sorted((c for c in members if not c.land), key=lambda c: (c.edhrec, c.name))
        self.assigned = {}
        self.have = {"lands": min(PART_TARGETS["lands"], len(self.nonbasic_lands) + self.basic_count)}
        for part in FUNCTION_PARTS:
            taken = 0
            for card in self.spells:
                if taken >= PART_TARGETS[part]:
                    break
                if part in card.parts and card.oracle_id not in self.assigned:
                    self.assigned[card.oracle_id] = part
                    taken += 1
            self.have[part] = taken
        self.synergy_ids = {c.oracle_id for c in self.spells if c.oracle_id not in self.assigned}
        self.have["synergy"] = min(PART_TARGETS["synergy"], len(self.synergy_ids))
        self.mask = None                 # numpy mask of synergy_ids over the similarity index, on demand


class _FastSimilarity:
    """Scores one card against every card at once, from gallery/similar.py's own index.

    Gives the same scores as SimilarityIndex.similar() (it walks the same postings and skips the
    same over-long ones) but sums them with numpy, so scoring 1,500 commanders takes about a
    second rather than twenty.
    """

    def __init__(self, similarity, numpy, weight_of):
        self.np = numpy
        self.similarity = similarity
        self.oracle_ids = list(similarity.vectors)
        self.index = {oracle_id: i for i, oracle_id in enumerate(self.oracle_ids)}
        self.size = len(self.oracle_ids)
        self.weights = numpy.array([weight_of(oid) for oid in self.oracle_ids], dtype=numpy.float64)
        self._postings = {}

    def _posting(self, term):
        arrays = self._postings.get(term)
        if arrays is None:
            posting = self.similarity.postings.get(term, ())
            if len(posting) > SIMILAR_MAX_POSTING:
                arrays = False
            else:
                np = self.np
                arrays = (np.fromiter((self.index[o] for o, _ in posting), dtype=np.int64, count=len(posting)),
                          np.fromiter((w for _, w in posting), dtype=np.float64, count=len(posting)))
            self._postings[term] = arrays
        return arrays

    def scores(self, oracle_id):
        """Similarity to every card in the index (0 for the card itself), or None if unknown."""
        np = self.np
        vector = self.similarity.vectors.get(oracle_id)
        if not vector:
            return None
        indices, weights = [], []
        for term, weight in vector.items():
            arrays = self._posting(term)
            if arrays is False:
                continue
            indices.append(arrays[0])
            weights.append(arrays[1] * weight)
        if not indices:
            return np.zeros(self.size)
        result = np.bincount(np.concatenate(indices), weights=np.concatenate(weights), minlength=self.size)
        result[self.index[oracle_id]] = 0.0
        return result


class Builder:
    def __init__(self, collection, similarity, tag_index):
        self.collection = collection
        self.similarity = similarity
        self.tag_index = tag_index
        expand = tag_index.expand if tag_index is not None else (lambda slug: frozenset({slug}))
        tag_sets = (expand("ramp"), expand("draw") | expand("card-advantage"), expand("removal"),
                    expand("sweeper"))
        self.cards = {oracle_id: _Card(entries, tag_sets)
                      for oracle_id, entries in collection.by_oracle_id.items() if entries}
        # Spare basics by the color they make (snow-covered ones included); Wastes under "C".
        self.basics = Counter()
        self.basic_cards = defaultdict(list)
        for card in self.cards.values():
            if card.basic and card.playable:
                colors = _ordered(card.land_info(frozenset(COLORS))[0]) or ["C"]
                self.basics[colors[0]] += card.spare
                self.basic_cards[colors[0]].append(card)
        self.commanders = {oid: c for oid, c in self.cards.items() if c.can_lead and c.owned > 0}
        self._pools = {}
        self._rank_rows = {}
        self._drafts = {}
        self._fast = None
        if hasattr(similarity, "vectors") and hasattr(similarity, "postings"):
            try:
                import numpy
            except ImportError:
                numpy = None
            if numpy is not None:
                self._fast = _FastSimilarity(
                    similarity, numpy, lambda oid: self.cards[oid].playedness if oid in self.cards else 0.7)
        self.timings = {}

    # ---- Pools ------------------------------------------------------------------------------

    def _pool(self, identity, exclude=frozenset()):
        key = (identity, exclude)
        pool = self._pools.get(key)
        if pool is None:
            pool = _IdentityPool(identity, self.cards.values(), self.basics, exclude)
            self._pools[key] = pool
        return pool

    def _commander_pool(self, commander):
        """The identity's pool with the commander left out, recounted only when that matters."""
        base = self._pool(commander.identity)
        if base.assigned.get(commander.oracle_id) in FUNCTION_PARTS:
            return self._pool(commander.identity, frozenset({commander.oracle_id}))
        return base

    # ---- Fit --------------------------------------------------------------------------------

    def fits(self, oracle_id, candidate_ids, minimum=SYNERGY_THRESHOLD, pool=None):
        """[(fit, similarity, oracle_id)] for candidates whose fit is at least `minimum`, best first.

        `pool` (an _IdentityPool whose synergy_ids are the candidates) lets the fast path reuse a
        mask across commanders of the same identity.
        """
        if self._fast is not None:
            fast = self._fast
            np = fast.np
            similarity = fast.scores(oracle_id)
            if similarity is None:
                return []
            fit = similarity * fast.weights
            if pool is not None:
                if pool.mask is None:
                    mask = np.zeros(fast.size, dtype=bool)
                    for oid in pool.synergy_ids:
                        index = fast.index.get(oid)
                        if index is not None:
                            mask[index] = True
                    pool.mask = mask
                fit = np.where(pool.mask, fit, 0.0)
            hits = np.nonzero(fit >= minimum)[0]
            found = [(float(fit[i]), float(similarity[i]), fast.oracle_ids[i]) for i in hits]
            found = [item for item in found if item[2] in candidate_ids]
        else:
            found = []
            for other, score in self.similarity.similar(oracle_id, SIMILAR_LIMIT):
                card = self.cards.get(other)
                if card is None or other not in candidate_ids:
                    continue
                fit = score * card.playedness
                if fit >= minimum:
                    found.append((fit, score, other))
        found = [item for item in found if item[2] != oracle_id]
        found.sort(key=lambda item: (-item[0], self.cards[item[2]].edhrec, item[2]))
        return found

    # ---- Ranking ----------------------------------------------------------------------------

    def _rank_row(self, commander):
        row = self._rank_rows.get(commander.oracle_id)
        if row is not None:
            return row
        pool = self._commander_pool(commander)
        candidates = pool.synergy_ids - {commander.oracle_id}
        have = dict(pool.have)
        have["synergy"] = min(PART_TARGETS["synergy"], len(candidates))
        pool_size = pool.pool_size - (1 if commander.oracle_id in pool.synergy_ids else 0)
        synergy = self.fits(commander.oracle_id, candidates, pool=pool)[:have["synergy"]]
        mean = sum(fit for fit, _, _ in synergy) / have["synergy"] if have["synergy"] else 0.0
        filled = sum(have.values())
        row = {
            "card": self._display_entry(commander),
            "color_identity": _ordered(commander.identity),
            "coverage": round(filled / TEMPLATE_SIZE, 4),
            "parts": {part: {"have": have[part], "target": PART_TARGETS[part]} for part in PART_ORDER},
            "pool_size": pool_size,
            "synergy": [self.cards[oid].name for _, _, oid in synergy[:6]],
            "synergy_score": round(mean, 4),
            "edhrec_rank": commander.card.get("edhrec_rank"),
        }
        self._rank_rows[commander.oracle_id] = (row, (-filled, -mean, commander.edhrec, commander.name))
        return self._rank_rows[commander.oracle_id]

    def rank(self, limit=60, colors=None, name_query=None):
        """The commanders you own, best first (TOOLS.md shape; `card` is the Entry).

        Rows are cached per commander, so later calls with other filters cost milliseconds.
        """
        started = time.perf_counter()
        allowed = None
        if colors:
            letters = colors if isinstance(colors, str) else "".join(colors)
            allowed = frozenset(c for c in letters.upper() if c in COLORS)
        query = fold(name_query) if name_query else None
        rows = []
        for commander in self.commanders.values():
            if allowed is not None and not commander.identity <= allowed:
                continue
            if query and query not in fold(commander.name):
                continue
            rows.append(self._rank_row(commander))
        rows.sort(key=lambda item: item[1])
        self.timings["rank_ms"] = round((time.perf_counter() - started) * 1000)
        chosen = rows[:limit] if limit else rows
        return [dict(row) for row, _ in chosen]

    # ---- Drafting ---------------------------------------------------------------------------

    @staticmethod
    def _display_entry(card):
        """The copy to show and list: your most valuable spare printing, else your most valuable one."""
        spare = card.spare_entries()
        return max(spare or card.entries, key=lambda e: ((e.price_usd or 0), e.spare, e.quantity))

    def draft(self, commander_oracle_id, partner_oracle_id=None):
        """A proposed 100-card list from spare copies (TOOLS.md shape; `card` values are Entries,
        plus `partner`, the partner's Entry or None). Raises ValueError for a card you do not own
        or that cannot lead."""
        key = (commander_oracle_id, partner_oracle_id)
        if key in self._drafts:
            return self._drafts[key]
        started = time.perf_counter()
        commander = self.cards.get(commander_oracle_id)
        if commander is None or not commander.can_lead:
            raise ValueError("Not a commander you own")
        leaders = [commander]
        if partner_oracle_id and partner_oracle_id != commander_oracle_id:
            partner = self.cards.get(partner_oracle_id)
            if partner is None or not (partner.can_lead or (partner.background and partner.legal)):
                raise ValueError("Not a partner or background you own")
            leaders.append(partner)
        result = _Draft(self, leaders).build()
        self.timings["draft_ms"] = round((time.perf_counter() - started) * 1000)
        self._drafts[key] = result
        return result


class _Draft:
    """One draft in progress."""

    def __init__(self, builder, leaders):
        self.builder = builder
        self.leaders = leaders
        self.identity = frozenset().union(*(leader.identity for leader in leaders))
        self.pool = builder._pool(self.identity, frozenset(leader.oracle_id for leader in leaders))
        self.spells = [c for c in self.pool.spells
                       if not c.rewards_colors or c.rewards_colors & self.identity]
        spell_ids = {c.oracle_id for c in self.spells}
        # Fit to the commander(s): the better of the two for a pair.
        self.fit, self.similarity = {}, {}
        for leader in leaders:
            for fit, score, oid in builder.fits(leader.oracle_id, spell_ids, minimum=SYNERGY_THRESHOLD):
                if fit > self.fit.get(oid, 0.0):
                    self.fit[oid], self.similarity[oid] = fit, score
        self.chosen = []                     # (role, _Card) in the order picked
        self.chosen_ids = set()
        self.part_have = {part: 0 for part in FUNCTION_PARTS}
        self.notes = []
        self.top_end = sum(1 for leader in leaders if not leader.land and leader.mv >= 6)
        self.skipped_top_end = set()

    # -- ordering

    def _part_key(self, card):
        """Function parts: cards that fit the commander strongly first, best first; then EDHREC order."""
        fit = self.fit.get(card.oracle_id, 0.0)
        if fit >= STRONG_FIT:
            return (0, -fit, card.edhrec, card.name)
        return (1, 0.0, card.edhrec, card.name)

    def _synergy_key(self, card):
        """Synergy part: fit (counted from SYNERGY_THRESHOLD up) plus the staple bonus; ties by EDHREC."""
        fit = self.fit.get(card.oracle_id, 0.0)
        value = (fit if fit >= SYNERGY_THRESHOLD else 0.0) + staple_bonus(card.edhrec)
        return (-value, card.edhrec, card.name)

    def _reason(self, role, card):
        rank = f"EDHREC #{card.edhrec}" if card.edhrec < UNRANKED else "no EDHREC rank"
        if role != "filler" and self.fit.get(card.oracle_id, 0.0) >= SYNERGY_THRESHOLD:
            return f"{role} · similarity {self.similarity[card.oracle_id]:.2f} · {rank}"
        return f"{role} · {rank}"

    def _take(self, role, card):
        self.chosen.append((role, card))
        self.chosen_ids.add(card.oracle_id)
        if card.mv >= 6:
            self.top_end += 1

    def _fits_curve(self, card):
        if card.mv >= 6 and self.top_end >= MAX_TOP_END:
            self.skipped_top_end.add(card.oracle_id)
            return False
        return True

    # -- spells

    def _pick_spells(self, slots):
        for part in FUNCTION_PARTS:
            ordered = sorted((c for c in self.spells if part in c.parts),
                             key=self._part_key)
            for card in ordered:
                if self.part_have[part] >= PART_TARGETS[part] or len(self.chosen) >= slots:
                    break
                if card.oracle_id not in self.chosen_ids and self._fits_curve(card):
                    self._take(part, card)
                    self.part_have[part] += 1
        self._fill_spells(slots)

    def _fill_spells(self, slots):
        """Synergy, then filler. A card whose every part is already full waits for a second pass,
        so the filler is not a second helping of ramp and draw."""
        ordered = sorted(self.spells, key=self._synergy_key)
        for strict in (True, False):
            for card in ordered:
                if len(self.chosen) >= slots:
                    return
                if card.oracle_id in self.chosen_ids:
                    continue
                if strict and card.parts and all(self.part_have[p] >= PART_TARGETS[p] for p in card.parts):
                    continue
                if not self._fits_curve(card):
                    continue
                role = "synergy" if self.fit.get(card.oracle_id, 0.0) >= SYNERGY_THRESHOLD else "filler"
                self._take(role, card)

    def _drop_last(self):
        """Drops the last pick of the synergy or filler part (the weakest), for a 37th land."""
        for i in range(len(self.chosen) - 1, -1, -1):
            role, card = self.chosen[i]
            if role in ("filler", "synergy"):
                del self.chosen[i]
                self.chosen_ids.discard(card.oracle_id)
                if card.mv >= 6:
                    self.top_end -= 1
                return

    def _pips(self):
        pips = Counter()
        for card in self.leaders + [card for _, card in self.chosen]:
            for color, amount in card_pips(card.card)[0].items():
                if color in COLORS:
                    pips[color] += amount
        return pips

    # -- lands

    def _pick_lands(self, count):
        """Returns ([(card, 1)] nonbasics, Counter of basics by color).

        A share of the lands is kept for basics (MIN_BASICS_SHARE), since fetches and land-search
        ramp need them. The rest, in order: lands making two or more of the identity's colors,
        one at a time, each the land adding the most for the colors still short of sources (the
        spells' pips per source; a land entering tapped counts half); up to MAX_UTILITY_LANDS
        untapped one-color lands; in a multicolor deck, one fetch per BASICS_PER_FETCH basics.
        Then basics (see _split_basics), and only when those run out, whatever is left.
        """
        identity = self.identity
        pips = self._pips()
        total_pips = sum(pips[c] for c in identity) or 1
        need = {c: pips[c] / total_pips for c in identity}
        colors = _ordered(identity) or ["C"]
        available = {c: self.builder.basics.get(c, 0) for c in colors}
        multicolor = len(identity) >= 2
        min_basics = min(sum(available.values()),
                         int(count * MIN_BASICS_SHARE["multi" if multicolor else "mono"]))
        budget = count - min_basics

        fixers, utility, fetches, rest = [], [], [], []
        for card in sorted(self.pool.nonbasic_lands, key=lambda c: (c.edhrec, c.name)):
            made, _, fetch, any_color = card.land_info(identity)
            if fetch:
                fetches.append(card)
            elif multicolor and len(made) >= 2:
                fixers.append(card)
            elif not card.tapped and not any_color:
                # One-color lands earn a slot by doing something else (Castle Embereth, Great
                # Furnace); an any-color land in a one-color deck only fixes, often at a price.
                utility.append(card)
            else:
                rest.append(card)

        chosen = []
        sources = Counter()

        def add(card):
            chosen.append(card)
            sources.update(card.land_info(identity)[0])

        def marginal(card):
            made = card.land_info(identity)[0]
            value = sum(need.get(c, 0) / (1 + sources[c]) for c in made)
            return value * (TAPPED_LAND_VALUE if card.tapped else 1.0)

        def take_best(candidates, limit):
            candidates = list(candidates)
            taken = 0
            while candidates and taken < limit and len(chosen) < budget:
                best = min(candidates, key=lambda c: (-marginal(c), c.tapped, c.edhrec, c.name))
                candidates.remove(best)
                add(best)
                taken += 1

        take_best(fixers, count)
        for card in utility[:MAX_UTILITY_LANDS]:
            if len(chosen) >= budget:
                break
            add(card)
        # A fetch in a one-color deck is a slower basic, so it waits for the basics to run out.
        take_best(fetches, (count - len(chosen)) // BASICS_PER_FETCH if multicolor else 0)

        basics = self._split_basics(count - len(chosen), colors, pips, sources, available)
        left = count - len(chosen) - sum(basics.values())
        if left > 0:                           # out of basics: whatever else makes the colors
            leftovers = [c for c in fixers + utility + fetches + rest if c not in chosen]
            for card in leftovers[:left]:
                add(card)
            self.notes.append(f"Only {sum(available.values())} spare basics in these colors; "
                              f"the lands include {min(left, len(leftovers))} weaker nonbasics")
        for color in colors:
            if identity and pips[color] and not available.get(color, 0):
                self.notes.append(f"No spare {BASIC_NAMES[color]}s, though the spells need "
                                  f"{pips[color]:g} {color} pips")
        return [(card, 1) for card in chosen], basics

    @staticmethod
    def _split_basics(wanted, colors, pips, sources, available):
        """Basics split by the spells' pips: first one of each color with pips (Nature's Lore
        needs its Forest), then each next basic to the color with the most pips per source so far
        (nonbasic sources included), within what is spare. With no pips, the fewest sources."""
        basics = Counter()

        def spare(color):
            return available.get(color, 0) > basics[color]

        for color in sorted((c for c in colors if pips[c] > 0), key=lambda c: (-pips[c], colors.index(c))):
            if sum(basics.values()) < wanted and spare(color):
                basics[color] += 1
        while sum(basics.values()) < wanted:
            open_colors = [c for c in colors if spare(c)]
            if not open_colors:
                break
            if any(pips[c] > 0 for c in open_colors):
                best = max(open_colors, key=lambda c: (pips[c] / (sources[c] + basics[c] + 1), -colors.index(c)))
            else:
                best = min(open_colors, key=lambda c: (sources[c] + basics[c], colors.index(c)))
            basics[best] += 1
        return basics

    # -- the whole deck

    def build(self):
        leaders = self.leaders
        slots = 100 - len(leaders)             # the 99 (98 with a partner)

        # Spells first, for a 36-land deck; then nudge the land count by the curve.
        land_target = LAND_TARGET
        self._pick_spells(slots - land_target)
        spell_mvs = [card.mv for _, card in self.chosen]
        average = sum(spell_mvs) / len(spell_mvs) if spell_mvs else 0.0
        if spell_mvs and average < LOW_CURVE:
            land_target -= 1
            self._fill_spells(slots - land_target)
        elif average >= HIGH_CURVE:
            land_target += 1
            self._drop_last()
        if land_target != LAND_TARGET:
            self.notes.append(f"{land_target} lands: the spells average {average:.2f} mana")

        lands, basics = self._pick_lands(land_target)
        land_total = len(lands) + sum(basics.values())
        if land_total < land_target:
            before = len(self.chosen)
            self._fill_spells(slots - land_total)
            extra = len(self.chosen) - before
            self.notes.append(f"Only {land_total} lands available of {land_target}"
                              + (f"; {extra} more spells fill the gap, so add basics before playing it"
                                 if extra else ""))
        size = len(leaders) + len(self.chosen) + land_total
        if size < 100:
            self.notes.append(f"The pool fills only {size} of 100 cards")
        if self.skipped_top_end:
            self.notes.append(f"Passed over {len(self.skipped_top_end)} cards at mana value 6+ "
                              f"to keep the top end at {MAX_TOP_END}")

        lines = self._lines(lands, basics)
        roles = Counter()
        for line in lines:
            roles[line["role"]] += line["quantity"]
        shortfalls = {part: PART_TARGETS[part] - roles[part] for part in FUNCTION_PARTS
                      if roles[part] < PART_TARGETS[part]}
        if land_total < land_target:
            shortfalls["lands"] = land_target - land_total
        if roles["synergy"] + roles["filler"] < PART_TARGETS["synergy"]:
            shortfalls["synergy"] = PART_TARGETS["synergy"] - roles["synergy"] - roles["filler"]

        deck_cards = [DeckCard(section="commander" if line["role"] == "commander" else "main",
                               quantity=line["quantity"], card=line["card"].card,
                               oracle_id=line["card"].oracle_id, tags=line["card"].tags,
                               price_usd=line["card"].price_usd) for line in lines]
        stats = compute_deck_stats(deck_cards, "commander", self.builder.tag_index)
        changers = stats["bracket_signals"]["game_changers"]
        if changers:
            self.notes.append(f"Plays {len(changers)} Game Changer{'s' if len(changers) != 1 else ''} "
                              f"({', '.join(changers)}), which raises the deck's bracket")

        return {
            "commander": lines[0]["card"],
            "partner": lines[1]["card"] if len(leaders) > 1 else None,
            "lines": lines,
            "stats": stats,
            "shortfalls": shortfalls,
            "text": self._text(lines),
            "notes": self.notes,
        }

    def _lines(self, lands, basics):
        lines = []
        for leader in self.leaders:
            lines.append({"role": "commander", "category": category_of(leader.card), "quantity": 1,
                          "card": Builder._display_entry(leader), "reason": "commander"})
        order = {role: i for i, role in enumerate(ROLE_ORDER)}
        for role, card in sorted(self.chosen, key=lambda item: order[item[0]]):
            lines.append({"role": role, "category": category_of(card.card), "quantity": 1,
                          "card": Builder._display_entry(card), "reason": self._reason(role, card)})
        for card, quantity in lands:
            made, _, fetch, _ = card.land_info(self.identity)
            made = "".join(_ordered(made)) or "C"
            detail = (", fetches" if fetch else "") + (", enters tapped" if card.tapped else "")
            lines.append({"role": "lands", "category": "Land", "quantity": quantity,
                          "card": Builder._display_entry(card), "reason": f"lands · makes {made}{detail}"})
        pips = self._pips()
        for color in _ordered(basics) + (["C"] if basics.get("C") else []):
            wanted = basics[color]
            # The cheapest spare printings first, across every basic of that color (snow included).
            copies = sorted(((e.price_usd or 0, e.row_id, e) for card in self.builder.basic_cards[color]
                             for e in card.spare_entries()), key=lambda item: item[:2])
            reason = f"lands · basic, {pips[color]:g} {color} pips" if color in COLORS else "lands · basic"
            for _, _, entry in copies:
                if wanted <= 0:
                    break
                take = min(wanted, entry.spare)
                lines.append({"role": "lands", "category": "Land", "quantity": take, "card": entry,
                              "reason": reason})
                wanted -= take
        return lines

    @staticmethod
    def _text(lines):
        """Commander and Deck sections, `1 Name (SET) number` with *F* / *E* for foil and etched."""
        def line_text(line):
            entry = line["card"]
            finish = {"foil": " *F*", "etched": " *E*"}.get(entry.finish, "")
            return f"{line['quantity']} {entry.name} ({entry.set_code.upper()}) {entry.collector_number}{finish}"

        commanders = [line_text(line) for line in lines if line["role"] == "commander"]
        rest = [line_text(line) for line in lines if line["role"] != "commander"]
        return "Commander\n" + "\n".join(commanders) + "\n\nDeck\n" + "\n".join(rest) + "\n"
