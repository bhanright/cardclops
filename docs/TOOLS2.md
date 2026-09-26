# Budget vs. bling, price alerts, goldfish simulator, deck changelog

Extends `docs/API.md`, `docs/DECKS.md` and `docs/TOOLS.md`.

## Budget vs. bling

Each deck has a **copy policy** that decides which of your copies the allocator
gives it. Pins always win.

- `default`: the printing the list names, if you own it; otherwise the most valuable
  free copy (basic lands: the cheapest).
- `budget`: the cheapest free copy, so valuable printings stay in the binder.
- `bling`: the fanciest free copy — foil or etched first, then special treatments
  (borderless, full art, showcase, extended art, etched frame), then price.

`budget` and `bling` ignore the printing a list names, since deck sites record a
printing whether or not it is the copy you sleeved.

DeckSummary gains `"copy_policy": "default"`. `PATCH /api/decks/<id>` accepts `copy_policy`.

`GET /api/decks/<id>/copy-policies` → a preview of each policy, other decks unchanged:
```json
{
  "current": "default",
  "options": {
    "budget": {"value_usd": 212.4, "value_change_usd": -380.2,
               "changes": [{"line_id": 17, "name": "Sol Ring",
                            "from": {"pool", "set_code", "collector_number", "finish", "price_usd"},
                            "to":   {"pool", "set_code", "collector_number", "finish", "price_usd"}}]},
    "bling": {…}, "default": {…}
  }
}
```

## Price alerts

A **watchlist** of printings (owned or not) with target prices, plus automatic
alerts on the collection. The daily refresh checks everything and, when an alert
fires, shows a Windows notification.

Alert kinds:
- `target`: a watched printing crossed its target (below or above).
- `move`: a card you hold, worth at least `held_min_value_usd` (per copy × copies), moved at
  least `held_move_pct` percent over `held_move_window` days (1, 7 or 30).
- `reprint`: a wide reprint of a card you hold worth at least `held_min_value_usd` was previewed
  (first seen on the reprint radar); once per card and set. The first check only records the
  reprints already announced, so alerts start with the next new one.
- `legality`: a ban, unban or rotation touched a card you hold (from the legality-change feed).

Each alert fires once per card per kind per day; a `target` alert fires again only after the price
has gone back across the target.

**WatchItem**
```json
{"watch_id": 4, "scryfall_id": "…", "oracle_id": "…", "name": "The One Ring", "set_code": "ltr",
 "collector_number": "380", "finish": "normal", "target_usd": 60.0, "direction": "below",
 "note": "for the Frodo deck", "created_at": "…",
 "price_usd": 71.2, "change": {"d1": -1.2, "d7": -4.0, "d30": null},
 "distance_pct": 18.7,                    // how far the price is from the target, as % of the target
 "triggered": false, "owned": 1, "image": "/img/<id>/front/normal"}
```

- `GET /api/watchlist` → `{"items": [WatchItem]}`
- `POST /api/watchlist` body `{"scryfall_id", "finish", "target_usd", "direction", "note"}` → WatchItem
  (the printing needn't be owned; the server fetches it from Scryfall and records its price daily from then on).
- `PATCH /api/watchlist/<id>` (target_usd, direction, note, finish) → WatchItem · `DELETE /api/watchlist/<id>`
- `GET /api/cards/lookup?q=<name>` → `{"cards": [{"oracle_id", "name", "printings": [{"scryfall_id", "set_code",
  "set_name", "collector_number", "released_at", "finishes": ["nonfoil","foil"], "prices": {"usd", "usd_foil",
  "usd_etched"}, "image"}]}]}` — any card, for adding to the watchlist (one live Scryfall search per call).
- `GET /api/alerts?unseen=1&limit=100` → `{"alerts": [{"alert_id", "day", "kind", "scryfall_id", "name",
  "message", "price_usd", "seen", "image"}], "unseen": 3}`
- `POST /api/alerts/seen` body `{"ids": [..]}` or `{"all": true}` → `{"unseen": 0}`
- `GET /api/alert-settings` / `PUT /api/alert-settings` → `{"move_alerts": false, "held_move_pct": 20,
  "held_move_window": 7, "held_min_value_usd": 10, "reprint_alerts": false, "legality_alerts": false,
  "windows_notifications": false}` — everything starts off; each automatic kind is opt-in.
- `POST /api/alerts/check` → runs the checks now (the daily refresh does this) → `{"new": 2}`
- `GET /api/summary` gains `"alerts": {"unseen": 3, "watching": 12}`.

## Goldfish simulator

Plays a deck alone many times to see how it runs: mulligans, land drops, mana,
when the commander comes down, and which spells get cast when. A model, not a
rules engine: see `assumptions` in the response.

`GET /api/decks/<id>/goldfish?games=2000&turns=10&seed=1&play=first` →
```json
{
  "games": 2000, "turns": 10, "seed": 1, "on_the_play": true, "elapsed_ms": 900,
  "mulligans": {"rate": 0.14, "average_hand_size": 6.85, "by_size": {"7": 0.86, "6": 0.12, "5": 0.02}},
  "lands_by_turn": {"1": 1.0, …},        // mean lands in play at end of turn
  "mana_by_turn": {"1": 1.0, …},         // mean mana available (lands + rocks/dorks)
  "spent_by_turn": {"1": 0.7, …},        // mean mana spent
  "efficiency": 0.71,                    // total spent / total available, turns 1..turns
  "commander": {"name": "…", "cast_by_turn": {"1": 0, …, "10": 0.97}, "median_turn": 4},   // null without one
  "screw_rate": 0.11,                    // ≤ 2 lands in play at end of turn 4
  "flood_rate": 0.09,                    // ≥ 7 lands in play and ≤ 1 card in hand at end of turn 7
  "color_screw_rate": 0.06,              // turn 4: a spell in hand has enough total mana but not its colors
  "cards": [{"name": "…", "mana_value": 3, "cast_rate": 0.62, "median_turn": 5}],   // nonland cards, by cast_rate
  "sample_game": [{"turn": 1, "hand_size": 7, "land": "Command Tower", "cast": ["Sol Ring"], "mana": 1, "spent": 1}],
  "assumptions": ["London mulligan; keep 7 with 2–5 lands, keep 6 with 2–4, always keep 5", …]
}
```

## Deck changelog

Every change to a deck's list is kept as a version: import, replace, Archidekt
sync, mana-fixer swaps, commander changes. A version stores the list and a
snapshot of its headline statistics, so each entry shows what changed in the
cards and in the numbers.

- `GET /api/decks/<id>/versions` → `{"versions": [Version]}` newest first:
```json
{"version_id": 7, "created_at": "…", "reason": "sync",      // import | replace | sync | manafix | commander | restore
 "card_count": 100,
 "added": [{"name": "Breeding Pool", "quantity": 1, "section": "main"}],
 "removed": [{"name": "Forest", "quantity": 1, "section": "main"}],
 "stats": {"cards": 100, "lands": 37, "average_mv": 3.1, "castability": 0.64,
           "curve": {"0": 1, …}, "pips": {…}, "sources": {…},
           "functions": {"ramp": 11, "card_draw": 9, …}, "game_changers": 2,
           "price_usd": 812.4, "legal": true},
 "diff": {"lands": 0, "average_mv": -0.08, "castability": 0.021, "price_usd": 12.1,
          "sources": {"U": 1, "G": -1}, "functions": {"ramp": 1}, "curve": {"3": -1, "2": 1},
          "legal": null}}                                     // null = unchanged; diff is against the previous version
```
The oldest version has empty `added`/`removed` and a null `diff`.
- `POST /api/decks/<id>/versions/<version_id>/restore` → DeckSummary (records a `restore` version).
