# Collector view, first-run setup, and the installed app

Extends `docs/API.md`, `docs/DECKS.md`, `docs/TOOLS.md`, `docs/TOOLS2.md`.

## Collector view

Every set Scryfall knows, each with every card in it, marked with what you own —
like ManaBox's Collector filter. Card data for sets comes from the cache
(`set_cards`, rebuilt from each refresh's bulk file), so unowned cards have
images, rarity and prices too.

**Completion** counts distinct cards in the set:
- `mode=printing` (default): you own that exact printing (any finish unless `finish` says otherwise).
- `mode=card`: you own the card in any printing from any set.
- `finish=any|nonfoil|foil` (foil includes etched).
- `variants=0` (default) counts only the main set: collector numbers up to the set's printed size
  (Scryfall's `printed_size`, else `card_count`) that aren't variations or promos; `variants=1`
  counts everything Scryfall lists in the set (showcase, borderless, extended art, promos…).

`GET /api/sets?q=&types=expansion,core&owned=1&sort=released|name|completion|owned_value` →
```json
{"sets": [{"code": "ltr", "name": "The Lord of the Rings: Tales of Middle-earth", "set_type": "draft_innovation",
           "released_at": "2023-06-23", "icon": "https://svgs.scryfall.io/…", "parent_set": null,
           "digital": false,
           "cards": 281,                       // main-set size (variants=0 rule)
           "cards_with_variants": 854,
           "owned": 212, "completion": 0.754,  // main set, printing mode, any finish
           "owned_copies": 682, "owned_value_usd": 956.65}],
 "set_types": ["expansion", "core", "masters", …]}
```
`owned=1` lists only sets you own something from. Digital-only sets are excluded.

`GET /api/sets/<code>?mode=printing&finish=any&variants=0` →
```json
{
  "set": {same fields as above},
  "totals": {"cards": 281, "owned": 212, "missing": 69, "completion": 0.754,
             "by_rarity": {"common": {"owned": 101, "cards": 101}, "uncommon": {…}, "rare": {…}, "mythic": {…}},
             "owned_value_usd": 956.65,
             "cost_to_complete_usd": 412.3},   // missing cards at this printing's price (nonfoil unless finish=foil)
  "cards": [{"scryfall_id", "collector_number", "name", "rarity", "type_line", "mana_cost", "colors",
             "image": "/img/<id>/front/normal", "image_back": "/img/<id>/back/normal" | null,
             "prices": {"usd", "usd_foil", "usd_etched"}, "finishes": ["nonfoil", "foil"],
             "variant": false,                  // outside the main-set rule
             "owned": 2, "owned_nonfoil": 1, "owned_foil": 1,   // copies of this printing
             "owned_elsewhere": 3,              // copies of the same card in other printings
             "counts": true}]                   // whether it counts toward completion under the current filters
}
```
Cards are ordered by collector number (numeric part first). `/img/<id>/…` works for every card in
`set_cards`. `GET /api/sets/<code>/missing.txt?…same filters` → plain text `1 Name (SET) 123` lines of
the missing cards (for buying lists).

## First-run setup

The gallery starts even with an empty database; the page then shows the setup wizard.
Nothing is downloaded, scheduled or turned on without the user choosing it.

`GET /api/setup/status` →
```json
{"needs_setup": true,               // no card data or no completed setup
 "has_card_data": false, "card_data_date": null,
 "has_collection": false, "collection_rows": 0,
 "has_price_history": false,
 "data_dir": "C:\\Users\\…\\Documents\\Cardclops", "cache_dir": "C:\\Users\\…\\AppData\\Local\\Cardclops",
 "downloads": {"scryfall_mb": 85, "history_mb": 62},        // approximate sizes shown for consent
 "daily_refresh_scheduled": false, "platform": "win32",
 "ask_available": false,             // the local `claude` CLI, for the Ask box
 "installed": true}                  // running from the installer build (not from source)
```

**Jobs.** Long steps run in the background; one at a time.
- `POST /api/setup/download` body `{"price_history": true}` → `{"job": "download"}` — Scryfall card data
  (required), and MTGJSON's 90-day price history if chosen.
- `POST /api/setup/import` body `{"filename": "Full Collection.csv", "text": "<csv contents>"}` →
  `{"job": "import"}` — replaces the collection; requires card data. Accepts ManaBox's CSV and the
  collection exports of Moxfield, Archidekt, Deckbox, TCGplayer, Dragon Shield, Delver Lens and
  generic CSVs with a Scryfall ID, set + collector number, or name column (see `gallery/importers.py`).
  The same endpoint serves "Update my collection" later.
- `GET /api/setup/progress` → `{"job": "import", "stage": "Matching cards", "message": "12,000 of 20,826 rows",
  "percent": 57, "done": false, "error": null, "result": null}`; when done, `result` holds e.g.
  `{"rows": 20826, "copies": 35554, "matched": 20820, "unmatched": [{"line": 14, "text": "…", "reason": "…"}]}`
  (at most 200 unmatched rows listed).
- `POST /api/setup/options` body `{"daily_refresh": false}` → `{"daily_refresh_scheduled": false}` —
  creates or removes the Windows scheduled task (7:30 AM daily, runs only when signed in).
- `POST /api/setup/complete` → `{"needs_setup": false}`.

Price alerts are **off by default**: every automatic alert kind and Windows notifications start off,
and the wizard only offers them as unchecked options (they are set through `PUT /api/alert-settings`).
Watchlist target alerts only exist if the user adds a watch item.

**Refresh on launch.** When the gallery starts and card data is more than 20 hours old, it refreshes
in the background (as the `refresh` job); `GET /api/setup/progress` reports it, and the page shows a
small "Updating card data and prices…" notice. `POST /api/refresh` starts one on demand.

## The installed app

- `POST /api/quit` → `{"quitting": true}` — stops the server (the installed app has no console window;
  the Tools menu has **Quit Cardclops**). Same local-only rules as other writes.
- `GET /api/summary` gains `"app": {"version": "0.1.0", "installed": true}`.
