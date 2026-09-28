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
User-Agent (browsers keep their own). So the public site does **not** host a copy of Scryfall data:
each browser downloads Scryfall's bulk JSONL files directly (data.scryfall.io allows it: CORS `*`), as
the desktop app does, and keeps only what the engine needs as it streams (printings index, oracle
cards, the held printings' card objects without image and store URLs, which are rebuilt from ids;
function tags for held cards). MTGJSON (CORS `*`) for price history, optional. Card images load
straight from Scryfall's image CDN.

## Phases

1. **Transport-free engine.** Move the request routing out of `server.Handler` into a
   `dispatch(method, path, params, body) -> (status, json | file)` that both the HTTP server and the
   browser call. The desktop, Android and server editions keep working unchanged on top of it.
2. **Browser runtime.** Pyodide in a Web Worker (the page stays responsive); `static/js/api.js`
   sends requests to the worker instead of fetch() in the public edition. The user database is a
   SQLite file saved to the browser's storage (OPFS/IndexedDB) after writes and on leaving the page;
   `navigator.storage.persist()` asked for. The card cache is a second, rebuildable file.
3. **Data in the browser.** The first-run wizard downloads and stream-parses Scryfall's bulk files
   (default cards ~75 MB compressed: Wi-Fi advised, as on Android), the tags, and optionally MTGJSON's
   90 days. "Refresh" on open when a day old. Rulings and the Comprehensive Rules fetched directly
   (check Wizards' file allows CORS; if not, the rules page links out).
4. **Server-only features off:** the Ask box (Claude CLI), background daily refresh and alerts (they
   run on open instead), Windows notifications, Quit, the scheduled task, Archidekt import by link
   (paste lists instead, unless Archidekt allows CORS). Settings explains each.
5. **Keeping data safe:** browser storage can be cleared, so: Settings → "Back up everything" (the
   user database as a file) and "Restore", plus the existing decks file and CSV export; a gentle
   reminder when there's no recent backup.
6. **Build and launch:** `scripts/build_static.ps1` assembles static/ + the engine + the worker into
   `dist-static/`; Cloudflare Pages deploys it. Move the private server to a private subdomain (Tunnel
   hostname + Access application), point cardclops.com at Pages, audit and publish the repo.

Estimate: 6–10 sessions; phase 1 is the risky refactor, phases 2–3 the bulk of the work.
