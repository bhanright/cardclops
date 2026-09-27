# Reprint radar, mana-base fixer, and "what can I build?"

Extends `docs/API.md` and `docs/DECKS.md`. CardSummary, DeckStats and DeckSummary
are defined there. "Spare" always means copies not reserved by an active deck.

## Reprint radar

Scryfall's bulk data includes cards previewed for sets that are not out yet.
A reprint adds supply, so the price of the printings you hold tends to fall
around release; the radar lists what you own that is about to be reprinted,
and what was reprinted recently, most valuable first.

**Impact** of a reprint, from the new printing's set: `wide` for sets opened in
quantity (expansion, core, masters, draft_innovation, commander, starter,
duel_deck, planechase, archenemy, funny, eternal) and `limited` for everything else (Secret
Lair drops, promos, masterpieces, The List). Wide reprints are the ones that move prices.

`GET /api/reprints?min_value=1` →
```json
{
  "as_of": "2026-09-26",
  "upcoming": [RadarCard],          // new printing releases after as_of
  "recent": [RadarCard],            // new printing released within the last 90 days
  "totals": {"upcoming_cards": 120, "upcoming_value_usd": 2450.1, "upcoming_wide_value_usd": 1810.0,
             "recent_cards": 40, "recent_value_usd": 610.0}
}
```
**RadarCard**
```json
{
  "card": CardSummary,              // your most valuable printing; quantity = copies of the card you hold
  "held_copies": 3, "held_value_usd": 64.2, "used": 1, "spare": 2,
  "impact": "wide",                 // the strongest impact among its new printings
  "days_until": 6,                  // upcoming only; negative for recent (days since release)
  "reprints": [{"scryfall_id", "set_code", "set_name", "set_type", "released_at", "collector_number",
                "rarity", "impact", "image": "/img/<id>/front/normal"}],
  "change_since_preview_pct": -8.4  // your lead printing's price now vs. the day its first new printing was
                                    // previewed (or 30 days before release when unknown); null without history
}
```
`/img/<id>/…` serves new printings too. `min_value` filters on `held_value_usd`.
`GET /api/summary` gains `"reprints": {"upcoming_cards", "upcoming_value_usd", "next_release": "2026-10-02"}`.

## Mana-base fixer

For one deck: land swaps, using only lands you have spare, that make the deck's
spells easier to cast on curve without changing its land count.

**Castability score** (0–1): the mean, over the deck's nonland cards (commander
included, weighted by quantity), of the probability of having every colored
requirement of the card by the turn equal to its mana value (on the play,
7 + turn − 1 cards seen; per card, the minimum over its colors of the
hypergeometric probability in DeckStats' castability rules). Enters-tapped lands
are counted and reported but not scored, except as a tie-breaker (fewer is better).

Swaps are found greedily: each step tries every (removable land in the deck,
spare land) pair and keeps the one that raises the score most, until no swap
gains at least 0.002 or `max_swaps` is reached. **Removable**: basic lands, and
lands that unconditionally enter tapped. **Addable**: lands you hold spare
copies of, within the deck's color identity, legal in its format, producing at
least one of the deck's colors (fetches count by what they fetch), not already
in the deck, and basic lands of the deck's colors (as many as you have spare).

`GET /api/decks/<id>/manafix?max_swaps=6` →
```json
{
  "before": {"score": 0.612, "by_color": {"U": {"1": 0.63, "2": 0.41, "3": 0.22}, …},
             "sources": {"W": 17, …}, "tapped_lands": 6},
  "after":  {same shape, after every swap},
  "swaps": [{
    "remove": {"line_id": 17, "name": "Forest", "scryfall_id": "…", "image": "/img/…", "why": "basic"},
    "add":    {"name": "Breeding Pool", "scryfall_id": "…", "pool": "<scryfall_id>|<finish>", "finish": "normal",
               "image": "/img/…", "price_usd": 12.1, "spare": 1},
    "score_after": 0.634,             // cumulative, after this swap
    "note": "Adds a blue source; replaces a Forest (green stays at 18 sources)"
  }],
  "considered": {"removable": 14, "spare_lands": 57},
  "message": null                     // e.g. "No spare lands in these colors" when swaps is empty
}
```
`POST /api/decks/<id>/manafix/apply` body `{"swaps": [{"remove_line_id": 17, "add_pool": "<id>|normal"}]}`
→ DeckSummary. Each swap takes one copy off the removed line (deleting it at 0) and adds a
main-deck line for the added card, pinned to that pool. A deck synced from Archidekt will be
overwritten by its next sync; the response then carries `"warning"`.

## What can I build?

Every commander you own, ranked by how much of a Commander deck your **spare**
cards could fill. The pool for a commander is every card you hold at least one
spare copy of, within its color identity and legal in Commander (one copy per
name, basics unlimited as you hold them).

**Coverage** fills a 99-card template; each part is capped at its target, and
coverage is total filled / 99:

| part | target | counts |
|---|---|---|
| lands | 36 | nonbasic lands producing the identity's colors, then spare basics |
| ramp | 10 | Tagger `ramp` (nonland) |
| draw | 10 | Tagger `draw`, `card-advantage` |
| removal | 8 | Tagger `removal` minus sweepers |
| wipes | 3 | Tagger `sweeper` |
| synergy | 32 | cards most similar to the commander (gallery/similar.py) with score ≥ 0.08, then any other playable card |

A card fills only one part (the first in the table's order that it fits, lands first).

`GET /api/build/commanders?limit=60&colors=WUB&q=` → `colors` limits to identities within those colors;
`q` filters by name →
```json
{"commanders": [{
   "card": CardSummary, "color_identity": ["W", "U", "B"],
   "coverage": 0.87,
   "parts": {"lands": {"have": 36, "target": 36}, "ramp": {"have": 7, "target": 10}, …},
   "pool_size": 412,                  // playable spare cards in the identity
   "synergy": ["Doubling Season", "…"],   // up to 6 of the most similar spare cards
   "edhrec_rank": 1234},
 …],
 "computed_in_ms": 2100}
```
Ranked by coverage, then by the mean similarity of the synergy part (a deck that
fits the commander beats a pile of generic cards).

`GET /api/build/draft?commander=<oracle_id>[&partner=<oracle_id>]` → a proposed 100-card list from spare copies:
```json
{
  "commander": CardSummary,
  "lines": [{"role": "ramp", "category": "Artifact", "quantity": 1, "card": CardSummary,
             "reason": "ramp · EDHREC #12"}],        // roles: commander, lands, ramp, draw, removal, wipes, synergy, filler
  "stats": DeckStats,
  "shortfalls": {"ramp": 3},                          // parts below target
  "text": "Commander\n1 Atraxa, Praetors' Voice (2XM) 353\n\nDeck\n1 Sol Ring (C21) 263 *F*\n…",
  "notes": ["Only 31 nonbasic lands spare; filled with 5 basics"]
}
```
Within each part, cards are picked by similarity to the commander, then by
Scryfall's `edhrec_rank` (lower = more played), avoiding a curve top-heavier
than 12 cards at mana value 6+. Lands: nonbasics that make the most needed
colors first, then basics split by the pips of the chosen spells, taken from the
printings you have the most spare copies of, counting a printing split across holding rows as one
pile (then unpriced basics, then basics over $1, cheapest first); the page shows one row per basic. Saving is the
existing `POST /api/decks/import` with `text` and `status: "inactive"` (so it
reserves nothing until you activate it).
