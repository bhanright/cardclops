# Plan: the public edition (a static site on cardclops.com)

Decided 2026-09-28. One repo, two editions:

- **Private edition** (today's app): the engine on the owner's server, moving from cardclops.com to a
  subdomain (a private subdomain) behind Cloudflare Access; also the Windows and Android apps.
- **Public edition**: a static site on cardclops.com (Cloudflare Pages, free). The same Python engine
  runs in each visitor's browser through Pyodide; each visitor's collection lives in their browser.
  No accounts, no server holding anyone's data, nothing to secure but static files.

A build step produces the public site from the shared code; features that need a server switch off.
The repo (bhanright/cardclops) goes public at launch, after an audit for personal details.

## Prototype result (2026-09-28)

The real engine (gallery/*.py, 580 KB) in Pyodide 0.28.3 with the owner's collection (20,826 rows,
35,554 copies, a 58 MB trimmed database):

| | PC (Chrome) | phone (Chrome) |
|---|---|---|
| Python ready | 1.7 s | 3.3 s |
| Collection built | 1.8 s | 1.4 s |
| Search fields prepared | 2.2 s | 1.3 s |
| A search (`c:u mv<3 otag:removal` etc.) | 21–33 ms | 12–23 ms |
| Memory | ~270 MB Python heap | ~270 MB Python heap |

Viable on a phone with a very large collection; most collections are far smaller.

## Data, and Scryfall's terms

Scryfall's API rules: data is free for Magic software that adds value; no paywalls; don't "simply
repackage, republish, or proxy Scryfall data"; don't crop the artist or copyright off images; accurate
User-Agent (browsers keep their own). Caching Scryfall data to run our own software is allowed;
a bare mirror is not.

**The card pack (decided 2026-09-28, revised from "every browser downloads Scryfall's bulk files").**
Once a day the server, which already downloads Scryfall's bulk file for the private edition, builds a
compact, app-specific catalog from it (`scripts/build_pack.py`): the tables every user shares (every
printing's set, number, name, type, cost, colors, rarity, finishes, today's prices, image URLs on
cards.scryfall.io; oracle cards with their legalities; sets; function tags; recent reprints). The
public site downloads that one file (26 MB compressed, 68 MB unpacked, September 2026) instead of
streaming and parsing Scryfall's 75 MB compressed, ~500 MB of JSON, in every visitor's browser.
Most of the pack is card ids, which are random and don't compress; the pack stores each only as
often as its lookups need (tables keyed by id, no image URLs, `printings` a view of `set_cards`). It exists only to run Cardclops, is credited to
Scryfall, and is one download a day for Scryfall instead of one per visitor. Each visitor's own
cards still come whole from Scryfall's API (`/cards/collection`), and images load unaltered from
Scryfall's image server. the owner is asking Scryfall to confirm this is fine; if they object,
the engine switches to direct bulk downloads.

**Price history.** MTGJSON (MIT licensed, notice kept) is downloaded once a day by the same job and
published as 256 files split by Scryfall id prefix (`gallery/price_files.py`; 58 MB, about 6 MB
compressed). They hold TCGplayer's market price only, the series the collection views read
(pricebook's TRACKED_SOURCES); the other vendors' lines in a card's chart fill in from each day's
prices. Browsers fetch the files their cards fall in after an import, again only for new cards,
and all of them again after a week away. The job keeps every day it sees, so history grows past
MTGJSON's rolling 90 days. Each day's prices come from the pack (Scryfall's usd is TCGplayer's
market price), recorded for your cards as the apps record them from the bulk file.

**Storage.** The browser keeps three files: yours (collection, decks, binders, settings), the pack
(replaced whole when a newer one is out), and a per-browser cache (your cards' Scryfall objects,
price history, rulings), which can be rebuilt.

## Phases

1. **Transport-free engine.** *Done 2026-09-28: gallery/api.py (`Api(gallery).handle(method, path, params, body)` → `Response`); the server keeps the security checks, static files and card images.* Move the request routing out of `server.Handler` into a
   `dispatch(method, path, params, body) -> (status, json | file)` that both the HTTP server and the
   browser call. The desktop, Android and server editions keep working unchanged on top of it.
2. **Browser runtime.** *Done 2026-09-28. The page is unchanged: a service worker (web/sw.js) answers
   `/api/` by passing each request to the engine, which is Pyodide in a Web Worker
   (web/engine/worker.js, running gallery/browser.py), and redirects `/img/` to Scryfall's image
   server. The databases live in OPFS (mounted at /data, `syncfs()` 400 ms after a write and when
   the tab is hidden). A Web Lock lets only one tab run the engine. `scripts/build_static.py` builds
   dist-static/ (`--devdata` seeds test databases for `?devdata=1`). With the full collection: 9 s
   to ready on the PC, 8–12 s cold on the phone.* Still to do in later phases: jobs (setup, refresh)
   start threads, which Pyodide can't; Python's network calls (rulings, the rules file, set cards,
   Archidekt) fail without `ssl`; the cache is 187 MB and needs slimming; `price_series` lives in
   the user database (56 MB), so every save rewrites it.
3. **Data in the browser.** In order:
   a. *Network:* one seam (`gallery/net.py`) for every download; the browser edition answers it
      with the browser's own fetch (no `ssl` in Pyodide). Rulings, sets and single cards from
      Scryfall's API; the Comprehensive Rules if Wizards' file allows CORS, else the page links out.
   b. *The card pack:* `scripts/build_pack.py` builds it from a refreshed cache database; the
      browser downloads it and attaches it as a third database.
   c. *Jobs without threads:* in the browser, setup, import and refresh are coroutines on
      Pyodide's event loop, so the page stays answered while they run. Import fetches the new
      cards' objects from `/cards/collection` (75 per request, spaced as Scryfall asks).
   d. *Price history:* `scripts/build_prices.py` (shards and daily files) and the browser's loader.
   e. *Refresh on open* when the pack is a day old; the wizard's wording for the browser.
   f. *Slimmer storage:* the per-browser cache without fields the engine never reads.
4. **Server-only features off:** the Ask box (Claude CLI), background daily refresh and alerts (they
   run on open instead), Windows notifications, Quit, the scheduled task, Archidekt import by link
   (paste lists instead, unless Archidekt allows CORS). Settings explains each.
5. **Keeping data safe:** browser storage can be cleared, so: Settings → "Back up everything" (the
   user database as a file) and "Restore", plus the existing decks file and CSV export; a gentle
   reminder when there's no recent backup.
6. **Build and launch:** `scripts/build_static.py` assembles static/ + the engine + the worker into
   `dist-static/`; Cloudflare Pages deploys it. The server's daily job builds the pack and price
   files and publishes them (a data subdomain cached by Cloudflare, or R2). Move the private server to a private subdomain (Tunnel
   hostname + Access application), point cardclops.com at Pages, audit and publish the repo.

Estimate: 6–10 sessions; phase 1 is the risky refactor, phases 2–3 the bulk of the work.
