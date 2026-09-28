# Gallery HTTP API

Served by `gallery/server.py` at `http://localhost:8765`. All JSON. Prices are USD
unless a field says otherwise. Days are `YYYY-MM-DD`.

The routing lives in gallery/api.py (`Api(gallery).handle(method, path, params, body)` returns a
`Response`), so the same endpoints can be served over HTTP (gallery/server.py) or called directly, as
the browser edition will. The server adds the security checks, static files and `/img/` card images.

## Shared shapes

**CardSummary** — one tile in the gallery.
```json
{
  "row_id": 123,                  // holdings row; null when results are grouped by card
  "scryfall_id": "…", "oracle_id": "…",
  "name": "Flaxen Intruder // Welcome Home",
  "set_code": "eld", "set_name": "Throne of Eldraine", "collector_number": "297",
  "rarity": "uncommon", "finish": "normal",       // normal | foil | etched | "mixed" when grouped
  "quantity": 2,                                  // copies (summed when grouped)
  "mana_cost": "{G} // {5}{G}{G}", "type_line": "…", "cmc": 1.0,
  "colors": ["G"], "color_identity": ["G"],
  "layout": "adventure", "double_faced": false,
  "price_usd": 0.29,                              // per copy, for this finish
  "value_usd": 0.58,                              // price × quantity
  "purchase_price": 0.27,                         // per copy, may be null
  "change": {"d1": 1.2, "d7": -3.4, "d30": null}, // % change of price_usd over 1/7/30 days, null if unknown
  "image": "/img/<scryfall_id>/front/normal",
  "image_back": "/img/<scryfall_id>/back/normal"  // only for double-faced cards
}
```

## Endpoints

Grouped results (`unique=cards`) add `"printings": n` to each CardSummary.

`GET /api/summary` → `{rows, copies, unique_cards, printings, sets, value_usd, purchase_total_usd,
scryfall_updated_at, price_history: {first_day, last_day, days, sources: [{source, label}]}, ask_available, recent_legality_changes: [LegalityChange]}`

`GET /api/search?q=&sort=&dir=&unique=&offset=&limit=`
- `q`: Scryfall syntax (see `docs/SEARCH.md` once written; the query engine follows https://scryfall.com/docs/syntax plus `qty`, `cond`, `lang`, `added`, `paid`, `gain`). `order:`/`unique:` inside `q` override the params.
- `sort`: `name | usd | value | mv | added | set | rarity | qty | gain | change1 | change7 | change30 | color | released` (default `name`), `dir`: `asc | desc`.
- `unique`: `prints` (one tile per holdings row, default) or `cards` (one tile per distinct card, quantities summed).
- `offset`, `limit` (default 0, 120; max 500).

→
```json
{
  "query": {"text": "…", "description": "cards where …", "warnings": []},
  "error": null,                                  // string when the query did not parse; other fields then empty
  "totals": {"rows": 10, "copies": 14, "unique_cards": 9, "printings": 10, "value_usd": 12.5,
             "results": 10},                     // tiles in the current `unique` mode
  "legality": {"pauper": {"cards": 6, "copies": 8}, "commander": {…}, …},   // legal or restricted
  "breakdown": {
    "colors": {"W": 3, "U": 0, "B": 14, "R": 0, "G": 0, "C": 0, "M": 2},  // copies; M = multicolor
    "curve": {"0": 1, "1": 4, "2": 9, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0},
    "types": {"Creature": 5, "Instant": 7, …},
    "rarity": {"common": 8, …}
  },
  "results": [CardSummary], "offset": 0, "limit": 120
}
```

`GET /api/card/<scryfall_id>[?holdings=card]` (with `holdings=card`, `holdings` lists every
printing of the card you hold, each with `scryfall_id`, `set_code`, `collector_number`) →
```json
{
  "card": {"scryfall_id", "oracle_id", "name", "mana_cost", "type_line", "oracle_text", "flavor_text",
           "power", "toughness", "loyalty", "rarity", "set_code", "set_name", "collector_number",
           "artist", "released_at", "layout", "legalities": {fmt: status},
           "prices": {usd, usd_foil, usd_etched, eur, eur_foil, tix},
           "scryfall_uri", "purchase_uris": {…}, "edhrec_rank", "reserved", "game_changer",
           "faces": [{"name", "mana_cost", "type_line", "oracle_text", "flavor_text", "power", "toughness", "loyalty", "image"}],
           "image", "image_back"},
  "tags": [{"slug": "removal-destroy", "description": "…"}],
  "holdings": [{"row_id", "finish", "quantity", "condition", "language", "purchase_price", "added_at", "price_usd", "gain_usd"}],
  "other_printings": [CardSummary],        // same card, other printings you own
  "similar": [CardSummary]                 // up to 12 cards from your collection with similar rules text
}
```

`GET /api/prices/<scryfall_id>` (points are daily for the newest 30 days, then one closing
price per month; x-axes must be date-scaled) →
```json
{"series": [{"source": "scryfall", "label": "TCGplayer market (via Scryfall)", "finish": "normal",
             "currency": "USD", "points": [["2026-09-26", 0.29], …]}],
 "default": 0}                                   // index of the series to show first
```

`GET /api/portfolio` → `{"points": [["2026-09-26", 27952.5], …], "note": "…"}` — value of the current
collection at each day's prices (days where most cards have a price).

`GET /api/movers?window=1|7|30&min_price=1&limit=25` →
`{"window": 7, "from_day", "to_day", "gainers": [Mover], "losers": [Mover]}` where Mover is a CardSummary
plus `{"old_price", "new_price", "change_pct", "change_total_usd"}` (total across copies held).

`GET /api/stats` →
```json
{"sets": [{"code", "name", "icon", "released_at", "set_size", "owned_unique", "copies", "value_usd"}],
 "added_by_month": [["2025-02", copies, value_usd], …],
 "top_value": [CardSummary],
 "gain": {"purchase_total_usd", "current_total_usd", "priced_rows"}}
```
(The color / curve / type / rarity breakdown for the whole collection is `GET /api/search` with an empty `q`.)

`POST /api/deckcheck` body `{"text": "4 Lightning Bolt\n1 Sol Ring\n…"}` (MTGO/Arena/Moxfield export
formats; set codes in parentheses and `*F*` markers ignored; section headers like `Sideboard` kept) →
```json
{"lines": [{"section": "Main", "name": "Lightning Bolt", "wanted": 4, "owned": 2, "missing": 2,
            "status": "partial",               // owned | partial | missing | unknown
            "cheapest_usd": 0.35, "printings": [CardSummary]}],
 "totals": {"wanted": 60, "owned": 51, "missing": 9, "cost_to_complete_usd": 12.4,
            "unpriced_missing": 0}}                // missing copies with no known price
```

`GET /api/extras?keep=4&min_price=0.5` → copies beyond `keep` of each card, most valuable first:
`{"keep": 4, "total_extra_copies", "total_extra_value_usd", "cards": [CardSummary + {"extra": 3, "extra_value_usd": 4.2}]}`

`POST /api/ask` body `{"question": "black cards under 3 mana that destroy nonland permanents"}` →
`{"query": "c:b mv<3 o:/destroy target nonland permanent/", "explanation": "…"}` or `{"error": "…"}`.
Uses the local `claude` CLI; can take 5–20 s. With `"api_key"` in the body (the browser edition sends
the visitor's own, kept in the page's storage) it calls Anthropic's Messages API with that key
instead; the key is used for that call only.

`GET /api/tags?q=remov` → `[{"slug", "description", "count"}]` (count = distinct cards you own with it), up to 30.

`GET /api/legality-changes` → `[LegalityChange]` where LegalityChange is
`{"day", "name", "oracle_id", "format", "old_status", "new_status", "copies"}`.

`GET /img/<scryfall_id>/<front|back>/<small|normal|large|art_crop|png>` → the image, fetched from
Scryfall the first time and served from `data/images/` afterwards. Use `loading="lazy"` or an
IntersectionObserver; do not preload off-screen images.

## Adding cards by hand

Cards can be added to the collection in Cardclops itself, for the times a card never made it
into ManaBox. Each holdings row has a `source`: `import` (from a collection CSV) or `manual`.

- Importing a CSV replaces only `import` rows; `manual` rows stay.
- When an import contains a printing and finish you had added by hand, the imported copies
  take over: the manual row's quantity drops by the imported quantity (and the row goes at 0),
  so nothing is counted twice. The import result lists these as `manual_reconciled`.

`POST /api/collection/add` body `{"scryfall_id", "finish": "normal"|"foil"|"etched", "quantity": 1,
"condition": "near_mint", "language": "en", "purchase_price": null}` → `{"row": ManualRow, "copies": n}`
(`copies` = the collection's total after the change). The printing needn't be in the card cache
yet; the server fetches it from Scryfall. The collection reloads, which takes a few seconds.

`GET /api/collection/manual` → `{"rows": [ManualRow]}`, newest first, where ManualRow is
`{"row_id", "scryfall_id", "name", "set_code", "set_name", "collector_number", "finish", "quantity",
"condition", "language", "purchase_price", "added_at", "price_usd", "image"}`.

`PATCH /api/collection/manual/<row_id>` body any of `{"quantity", "finish", "condition", "language",
"purchase_price"}` → ManualRow · `DELETE /api/collection/manual/<row_id>` → `{"deleted": row_id}`.
Only `manual` rows can be changed here; imported rows change by re-importing.

Printings for the picker come from `GET /api/cards/lookup?q=<name>` or `?oracle_id=` (see TOOLS2.md).
CardSummary and card-detail holdings rows gain `"source": "import" | "manual"`.

## Binders

Binders, boxes and lists you file copies into (gallery/binders.py). A binder holds counts per printing
and finish, not collection rows, so filing survives re-importing the collection. Copies in no binder are
"unsorted". When an import leaves fewer copies than the binders hold, the binders keep their counts and
report the difference as `short`.

`GET /api/binders` → `{"binders": [Binder], "unsorted": {"copies", "value_usd"}}`, where Binder is
`{"binder_id", "name", "kind": "binder"|"box"|"list", "notes", "position", "created_at", "copies",
"printings", "value_usd", "short"}`.

`POST /api/binders` body `{"name", "kind"?, "notes"?}` → Binder. `PATCH /api/binders/<id>` body any of
`{"name", "kind", "notes"}` → Binder. `DELETE /api/binders/<id>` → its copies become unsorted.

`POST /api/binders/<id>/put` body `{"items": [{"scryfall_id", "finish", "quantity"}], "from_binder"?}`
or `{"q": "t:dragon"}` (every copy a gallery search finds) → Binder + `moved`. A null quantity means every
free copy: unsorted ones, or those in `from_binder` when moving. `POST /api/binders/<id>/take` takes copies
out (they become unsorted), with the same body.

`GET /api/binders/<id>/export` → the binder as a CSV file (Binder Name, Name, Set code, Collector
number, Foil, Quantity, Scryfall ID).

Card detail (`GET /api/cards/<id>`) carries `binders`: `[{"binder_id", "name", "kind", "scryfall_id",
"finish", "quantity"}]` for the card's copies. The gallery search reads `binder:"name"` (copies in a
binder whose name contains that), `binder:any` and `binder:none` (copies in no binder). A collection export
with ManaBox's Binder Name column fills those binders on import, each with exactly what the file puts in it.

## Rules and rulings

`GET /api/rules` → `{"downloaded": false}` until the Comprehensive Rules are downloaded, then
`{"downloaded": true, "effective", "source_url", "chapters": [{"number", "title", "sections": [{"number",
"title"}]}], "rule_count", "glossary_count"}`; opening it checks Wizards for a newer edition in the
background once a month. `POST /api/rules/download` fetches the current file (linked from
magic.wizards.com/rules, about 1 MB, into the card cache) and returns the same.

`GET /api/rules/section/<702>` → `{"number", "title", "rules": [Rule]}`, Rule being `{"number": "702.19b",
"section", "text", "examples": [text]}`. `GET /api/rules/search?q=` → `{"rules": [Rule], "glossary":
[{"term", "text"}]}`: a rule number finds that rule and its subrules, words find rules and glossary entries
containing all of them (150 rules at most). `GET /api/rules/glossary` → every glossary entry.

`GET /api/card/<scryfall_id>/rulings?oracle_id=` → `{"rulings": [{"date", "source": "wotc"|"scryfall",
"text"}]}`, newest first, from Scryfall; cached per card for a month (and served stale when offline), with
`error` when Scryfall can't be reached and nothing is cached.
