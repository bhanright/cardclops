# Decks

Decks you have built, matched against the collection: which copies each deck
uses, what is missing, what is double-booked between decks, and statistics
for each list. Extends the API in `docs/API.md`.

## Concepts

- **Sections**: `commander`, `companion`, `main`, `sideboard`, `maybeboard`.
  Every section except `maybeboard` uses real copies.
- **Status**: `active` (built and sleeved; its copies are reserved) or `inactive`
  (an idea or a retired list; shows what you own but reserves nothing).
- **Copy pool**: all holdings of one printing in one finish, keyed
  `"<scryfall_id>|<finish>"`. Decks record which pool they use, never a single
  holdings row, because ManaBox rows are not unique per copy.
- **Allocation**: which copies each deck line uses, recomputed whenever the
  collection or a deck changes. Active decks are allocated in `priority` order
  (lower first, then oldest first). Within a line: copies you pinned first, then
  the exact printing and finish the list names, then the same printing in any
  finish, then the most valuable free copy. A line short of copies is
  `missing`; the reason is `not_owned` (you don't own enough in total) or
  `used_elsewhere` (other active decks hold them).
- **Used / spare** per holdings row: copies allocated to active decks / the rest.

## Shapes

**DeckSummary**
```json
{
  "deck_id": 3, "name": "Atraxa Superfriends", "format": "commander", "status": "active",
  "priority": 0,
  "commanders": [{"oracle_id": "…", "name": "Atraxa, Praetors' Voice", "scryfall_id": "…",
                  "image": "/img/<id>/front/normal", "art": "/img/<id>/front/art_crop"}],
  "color_identity": ["W", "U", "B", "G"],
  "card_count": 100,                 // commander + companion + main
  "sections": {"commander": 1, "main": 99, "sideboard": 0, "maybeboard": 12},
  "owned": 94, "missing": 6,         // copies, over the sections that use copies
  "missing_not_owned": 4, "missing_used_elsewhere": 2,
  "cost_to_complete_usd": 41.2,      // missing copies at each card's cheapest printing
  "value_usd": 812.4,                // the copies this deck uses
  "legal": true,                     // legal in its own format (see stats.legality)
  "source": "paste", "source_url": null,
  "created_at": "2026-09-26T15:00:00", "updated_at": "…",
  "cover": "/img/<id>/front/art_crop"
}
```

**DeckLine**
```json
{
  "line_id": 17, "section": "main", "quantity": 1, "name": "Sol Ring",
  "oracle_id": "…",                  // null when the name matched no card
  "requested": {"set_code": "c21", "collector_number": "263", "finish": "foil"},   // or null
  "card": CardSummary,               // the printing shown: first allocated copy, else the requested printing, else a representative
  "category": "Artifact",            // Creature | Planeswalker | Battle | Instant | Sorcery | Artifact | Enchantment | Land | Other
  "tags": ["ramp", "mana-rock"],     // Tagger slugs
  "allocations": [{"pool": "<scryfall_id>|foil", "scryfall_id": "…", "set_code": "c21",
                   "collector_number": "263", "finish": "foil", "quantity": 1,
                   "pinned": false, "price_usd": 3.1}],
  "owned": 1, "missing": 0, "missing_reason": null,          // not_owned | used_elsewhere
  "used_elsewhere": [{"deck_id": 5, "name": "Breya", "quantity": 1}],
  "alternatives": [{"pool": "…", "scryfall_id": "…", "set_code": "cmm", "collector_number": "400",
                    "finish": "normal", "free": 2, "price_usd": 1.2}]      // other copies you could pin
}
```

**DeckStats** (computed by `gallery/deckstats.py` over the sections that play:
commander, companion and main; sideboard and maybeboard are excluded)
```json
{
  "counts": {"cards": 100, "lands": 37, "nonlands": 63, "creatures": 24, "basic_lands": 12,
             "mdfc_lands": 2, "sideboard": 0},             // MDFCs with a land back count as spells, and as sources
  "types": {"Creature": 24, "Instant": 9, …},             // copies; a card counts under each of its types (front face)
  "average_mv": {"nonland": 3.21, "all": 2.02},
  "curve": {"0": 1, "1": 8, …, "7+": 3},                  // nonland copies by mana value (commander included)
  "curve_by_type": {"Creature": {"1": 2, …}, …},
  "pips": {"W": 18, "U": 22, "B": 15, "R": 0, "G": 20, "C": 1},   // colored symbols in mana costs, see below
  "pip_share": {"W": 0.24, …},                           // of W+U+B+R+G pips
  "sources": {"W": 17, "U": 19, "B": 14, "R": 0, "G": 18, "C": 3, "any": 5},   // lands that can produce each color
  "nonland_sources": {"W": 4, …},                        // rocks and dorks (Tagger ramp tags with produced_mana)
  "source_share": {"W": 0.25, …},
  "land_kinds": {"basic": 12, "mono": 3, "dual": 14, "tri": 2, "five_color": 3, "colorless": 1, "fetch": 2},
  "castability": {                                        // hypergeometric, on the play: 7 + (turn − 1) cards seen
    "by_color": {"U": {"1": 0.63, "2": 0.41, "3": 0.22}},   // P(≥1/2/3 sources of U by the turn you need them), see below
    "hardest": [{"name": "Cryptic Command", "mana_cost": "{1}{U}{U}{U}", "turn": 4, "color": "U",
                 "needed": 3, "probability": 0.71}]          // up to 10 lowest-probability cards
  },
  "opening_hand": {
    "deck_size": 99, "lands": 37,
    "lands_in_seven": {"0": 0.01, …, "7": 0.0},          // P(exactly k lands in a 7-card hand)
    "two_to_four": 0.78,
    "expected_land_drops": {"1": 1.0, "2": 0.99, …}      // P(having hit your Nth land drop by turn N), turns 1-6, on the play
  },
  "functions": {                                          // Tagger tags, descendants included; values are card names
    "ramp": […], "card_draw": […], "removal": […], "board_wipes": […], "counterspells": […],
    "tutors": […], "protection": […], "recursion": […]
  },
  "bracket_signals": {"game_changers": […], "extra_turns": […], "mass_land_denial": […], "tutors": […]},
  "legality": {
    "format": "commander",
    "legal": false,
    "problems": [{"name": "Mana Crypt", "reason": "banned"}, {"name": null, "reason": "99 cards; commander decks need exactly 100"}],
    "warnings": [{"name": "…", "reason": "…"}],          // doubtful but not disqualifying (e.g. an unrecognized commander pairing)
    "other_formats": {"legacy": true, "vintage": true, "modern": false, …}   // card legality only, not deck size
  },
  "rarity": {"common": 30, …},
  "price": {"total_usd": 812.4, "average_usd": 8.12,
            "most_expensive": [{"name": "…", "price_usd": 45.0}]}          // up to 5, by the copies used (or cheapest printing if missing)
}
```

Pips: each `{W}` counts 1; hybrid `{W/U}` counts ½ to each; `{2/W}` counts 1 W;
Phyrexian `{W/P}` counts 1 W; `{C}` counts under C. X and generic count nowhere.
Double-faced cards count the front face; split and adventure cards count every half.

Castability: for each nonland card and each color, `needed` is its pips of that
color and `turn` is max(its mana value, 1). Probability of seeing at least
`needed` of that color's sources (lands plus nonland sources) among the first
`7 + turn - 1` cards of a `deck_size` library holding `sources` of them
(hypergeometric). The library is the main deck only: commanders and companions start outside it. `by_color` gives, for needed = 1, 2, 3, the probability by the
turn equal to that need (a one-pip card on turn 1, two pips on turn 2, three pips on turn 3).

## Endpoints

`GET /api/decks` → `{"decks": [DeckSummary], "totals": {"active": 7, "inactive": 3,
"copies_in_decks": 612, "value_in_decks_usd": 4120.5, "conflicts": 4}}`
(`conflicts` = lines missing copies because another active deck holds them.)

`POST /api/decks/import` body `{"decks": [{"name": "…", "text": "…", "format": "commander" | null,
"status": "active", "source": "paste" | "file", "source_url": null}]}` → `{"imported": [DeckSummary],
"warnings": [{"deck": "…", "line": "1 Notacard", "message": "No card named “Notacard”"}]}`.
The format is guessed when null (a commander section or 100 singleton cards → commander;
60+ cards with a 15-card sideboard → the most restrictive format every card is legal in, else `casual`).
Text shapes accepted: MTGO/Arena/Moxfield/Archidekt exports — `4 Name`, `4x Name`,
`1 Name (SET) 123`, `*F*` / `*E*` finish markers, Archidekt `[Category]` / `[Commander{top}]` tags,
section headers (`Commander`, `Deck`, `Sideboard`, `SIDEBOARD:`, `Maybeboard`, `Companion`, `//Sideboard`),
and MTGO's convention of a sideboard after a blank line.

`POST /api/decks` body `{"name": "…", "format": "commander", "commanders": [oracle_id], "status": "active",
"folder_id": 3}` (all optional) → DeckSummary: a new, empty deck with its commander(s) (Decks → New deck;
the deck page adds the cards). Its history starts with a `create` version.

**Folders.** A deck is in one folder or none (DeckSummary `folder_id`, `folder`); `GET /api/decks` and
`GET /api/decks/<id>` carry `folders` [{folder_id, name, decks}]. `POST /api/decks/folders` `{"name"}`
makes one (→ `folder_id` + `folders`), `PATCH /api/decks/folders` `{"folder_id", "name"}` renames it,
`DELETE /api/decks/folders` `{"folder_id"}` deletes it (its decks stay, in no folder), and
`PATCH /api/decks/<id>` `{"folder_id": 3 | null}` files a deck. The decks file carries each deck's
folder by name, made on import if missing. Deck search: `folder:cube`, `folder:none`.

`GET /api/archidekt/decks?username=<name>` → `{"decks": [{"id", "name", "size", "format", "updated_at",
"featured", "url"}]}` — that user's public and unlisted decks (private ones can't be read).

`POST /api/decks/import-archidekt` body `{"urls": ["https://archidekt.com/decks/123/…", …], "status": "active"}`
(`ids` also accepted) → same shape as `/api/decks/import`. Fetches one deck a second; exact printings,
finishes, commander, companion, sideboard and out-of-deck categories come across. Archidekt's staff
have said publicly that its read API may be used; Moxfield's terms forbid automated access, so Moxfield
decks are pasted ("Copy for Moxfield" keeps printings and finishes; its first card is taken as the
commander of a 100-card list when it can be one, and the second card too when the two are partners:
Partner, Friends forever, a Background for "Choose a Background", or a Doctor's companion).

A name shared by a playable card and an oddity (Pym Particles and the Jumpstart front card named
after it; art cards, tokens) goes to the playable card, and a set and number in the list win over the
name when that printing has the name. Lines an older import matched to an oddity are matched again
when the app starts.

`POST /api/decks/<id>/sync` → re-reads a deck imported from Archidekt (pins survive) → DeckSummary + `warnings`.

`GET /api/decks?q=…` → the same, keeping the decks that match a search (totals stay for all decks), plus
`query`; 400 with `error` for a term it can't read. Terms are separated by spaces, all must match, and a
leading `-` negates one: plain words (deck or commander name), `name:`, `cmd:`, `card:"sol ring"` (a card
in the deck, not the maybeboard), `folder:` (`folder:none` for decks in no folder), `c:`/`id:` color identity (`c:g` includes, `c=ug` exactly, `c<=ug`
within, also `>=`, `<`, `>`; letters, guild/shard names, `c:c` colorless), `f:`/`format:` (`edh` works),
`is:active|inactive|legal|illegal|complete|incomplete|conflict`, and numbers `cards`, `missing`, `value`,
`cost`, `owned`, `priority` with `= != < > <= >=`. Unknown keys are read as plain words
(gallery/decksearch.py).

`GET /api/decks/<id>` → `{"deck": DeckSummary + {"notes": "…"}, "lines": [DeckLine], "stats": DeckStats}`

`PATCH /api/decks/<id>` body any of `{"name", "format", "status", "priority", "notes",
"commanders": [oracle_id]}` → DeckSummary. Setting `commanders` moves those lines into the
commander section.

`PUT /api/decks/<id>/list` body `{"text": "…"}` → replaces the list (pins survive for cards still present) → DeckSummary.

`DELETE /api/decks/<id>` → `{"deleted": 3}`

`GET /api/decks/export` → every deck as a file to save (`Content-Disposition: attachment`,
`cardclops-decks-<date>.json`): `{"format": "cardclops-decks", "version": 1, "app_version", "exported_at",
"decks": [{"name", "format", "status", "priority", "copy_policy", "notes", "source", "source_url",
"lines": [{"section", "quantity", "name", "set_code"?, "number"?, "scryfall_id"?, "finish"?,
"pins"?: [{"pool", "quantity"}]}]}]}`. It moves decks between copies of Cardclops (the phone app,
cardclops.com, the Windows app).

`POST /api/decks/import-file` body `{"file": <that object>, "on_conflict": "replace" | "keep" | "skip"}`
→ `{"imported": [names], "replaced": [names], "skipped": [names], "warnings": […]}`. A deck whose name is
already here is replaced in place (its history stays and gains an `import` version), kept beside the new
one (named "… (imported)"), or left alone. Pins come across for copies this collection holds.

`POST /api/decks/<id>/lines` body `{"oracle_id": "…"}` (any printing), `{"scryfall_id": "…"}` (that
printing) or `{"name": "Sol Ring"}`, plus optional `"section"` (default `main`) and `"quantity"`
(default 1) → DeckSummary + `line_id`. A card already in that section, with no printing asked for,
gets its quantity raised instead of a second line.

`POST /api/decks/<id>/lines` body `{"text": "2 Sol Ring
1 Sol Ring (C21) 263 *F*", "section": "main"}` is quick add:
decklist lines, each added (or added to the same line already there); `section` applies to lines the text
doesn't put in a section itself → DeckSummary + `added` [{name, quantity, section}] + `warnings`.

`PATCH /api/decks/<id>/lines/<line_id>` body any of `{"quantity": 2, "section": "sideboard"}`
(quantity 0 removes the line; pins shrink to fit) → DeckSummary + `line_id`.
`DELETE /api/decks/<id>/lines/<line_id>` removes the line. These edits are versions with reason
`edit`; on a deck imported from Archidekt the answer carries a `warning` that the next sync replaces them.

DeckLine carries `pairs_with_commander`: true for a main-deck card that could join the deck's single
commander as its partner or Background (the deck page offers it under the commander).

`POST /api/decks/<id>/pin` body `{"line_id": 17, "pool": "<scryfall_id>|foil", "quantity": 1}` → DeckLine
(quantity 0 removes the pin).

`GET /api/decks/<id>/value-history` → `{"points": [["2026-08-31", 790.1], …], "source_label": "…"}`
(the copies the deck uses now, at each day's prices; same monthly-then-daily spacing as other charts).

`GET /api/decks/<id>/suggestions` → `{"cards": [CardSummary + {"score": 0.42, "spare": 2,
"because": ["Sol Ring", "Arcane Signet"]}]}` — cards you own with spare copies, not in the deck,
within its color identity and legal in its format, most similar to what the deck already plays.

## Collection changes

- CardSummary gains `"used": 1` (copies of this row/card in active decks) and `"spare": 3`.
- `GET /api/card/<id>` gains `"decks": [{"deck_id", "name", "status", "section", "quantity",
  "copies": [{"set_code", "collector_number", "finish", "quantity"}]}]` for every deck
  (active or not) whose list includes the card.
- `GET /api/summary` gains `"decks": {"active": n, "inactive": n, "copies_in_decks": n, "conflicts": n}`.
- `GET /api/extras` counts only spare copies: extra = owned − max(used, keep).
- Search gains `deck:<name>` (in a deck whose name contains the text; `deck:any` for any active deck),
  `used` and `spare` (numeric, copies in active decks / not).
