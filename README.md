# Cardclops
<img width="1280" height="640" alt="cardclops-social" src="https://github.com/user-attachments/assets/6848b21d-c738-485f-a64e-5bfff65c2a4c" />


A local gallery for a Magic: The Gathering collection exported from ManaBox, with
Scryfall's card data, Scryfall-syntax search across your own cards, price history,
and a few tools the big collection sites don't have.

Everything runs on this computer. Card data comes from Scryfall's daily bulk files,
prices from Scryfall and MTGJSON, images from Scryfall's image CDN the first time a
card scrolls into view (then from the image cache).

There's also a version that runs entirely in a web browser, with no install and no account:
[cardclops.com](https://cardclops.com) (docs/PUBLIC_EDITION_PLAN.md explains how it works).

## Installing (for anyone)

`installer\Output\Cardclops-Setup-<version>.exe` installs the gallery like any Windows
program: for the current user by default (no administrator prompt), or for everyone under Program
Files. The first time it opens, a setup wizard asks before downloading Scryfall's card data (and,
optionally, 90 days of price history), then imports a collection CSV from ManaBox, Moxfield,
Archidekt, Deckbox, TCGplayer, Dragon Shield, Delver Lens or Helvault, or starts empty. Daily
updates and price-alert notifications stay off unless the user turns them on. Uninstalling keeps
the user's data in Documents\Cardclops.

Cardclops opens in its own window (Windows' built-in WebView2, with no browser controls); closing
the window quits it, and starting it again while it runs brings the window forward. Where WebView2
is missing, or with `Cardclops.exe --browser`, it opens in the default browser instead.

Build it with `scripts\build_installer.ps1` (needs `pip install pyinstaller certifi pywebview` and
Inno Setup 6).

## Setup (from source)

Python 3.12+ and the `certifi` package (Windows' certificate store rejects the
certificate chain on Scryfall's image server; certifi's bundle accepts it):

```
python -m pip install certifi
```

Then, from this folder:

```
python -m gallery import "path\to\your ManaBox export.csv"
python -m gallery history
python -m gallery serve
```

and open http://localhost:8765.

- `import` replaces the collection with a ManaBox export (ManaBox → Collection → Export → CSV) and
  pulls today's Scryfall data. Re-run it whenever the collection changes.
- `history` backfills the last 90 days of daily prices from MTGJSON (about 4 minutes, 47 MB).
- `refresh` pulls today's card data and prices. A Windows scheduled task, **Cardclops
  daily refresh**, runs it every day at 7:30 AM (after Scryfall's and MTGJSON's morning
  rebuilds), catches up at the next start if the PC was off, and retries three times if the
  network is down. Its output goes to `%LOCALAPPDATA%\Cardclops\logs\refresh.log`. A running gallery notices the new
  data within a minute and reloads. If the PC is off for a stretch, running `history` again
  fills any gap shorter than 90 days.

  To change or remove the task: Task Scheduler → Task Scheduler Library, or
  `Unregister-ScheduledTask -TaskName "Cardclops daily refresh"` in PowerShell.

## How much price history is kept

The last 30 days are kept daily. Anything older is thinned to one price per calendar month,
the last recorded day of that month (its closing price), so month-to-month changes stay exact
while each card's history grows by about 12 points a year instead of 365. The thinning happens
every time prices are written (`compact_points` in `gallery/db.py`; `DAILY_PRICE_DAYS` sets
the 30).

## Why there's no five-year price history

No free source offers daily per-printing prices further back than MTGJSON's rolling 90 days.
TCGplayer and Cardmarket have closed their APIs to new developers, and the sites that show
long histories (MTGGoldfish, MTGStocks, EchoMTG) either have no API or forbid scraping it.
So the gallery starts with MTGJSON's 90 days and records every day after that; the charts'
1Y and 5Y ranges fill in, month by month, as the history accumulates.

## Sets

The Sets view lists every paper set with how much of it you own, and each set page shows every
card in collector-number order, dimming the ones you don't have — by exact printing or by any
printing of the card, any finish or foil only, main set or with variants. "Main set" means
the cards that come in the set's boosters (for sets sold in boosters), without promos or special
treatments; it matches the printed set size for most sets and is within a few cards for the rest.
It can copy the missing cards as a buying list.

## Search

The search box takes Scryfall's syntax (https://scryfall.com/docs/syntax), run against your
own cards, including Scryfall Tagger function tags (`otag:removal`) and a few collection fields
of our own: `qty`, `cond`, `lang`, `added`, `paid`, `gain`. The Ask button turns an English
question into a query with the local `claude` command line tool; the gallery's own engine then
runs the query, so every count comes from your data.

Example: *how many black cards with mana value under 3 destroy nonland permanents, and how many
of those are Pauper-legal?*

```
c:b mv<3 o:/destroy (target|all|each) nonland permanent/
```

The answer strip above the results shows the counts, and the format chips show how many of
those are legal in Pauper and every other format.

## Layout

```
gallery/            the engine: one Python package (stdlib, plus certifi) every edition runs
  api.py            the JSON API, the same in every edition (docs/API.md)
  server.py         the local HTTP server and what the handlers share
  app.py            the Windows app: its window (pywebview), port and logs
  android.py        the Android app's engine side (a token-protected local server)
  browser.py        the browser edition's engine, in Pyodide (web/engine/worker.js runs it)
  browser_jobs.py   setup, import and refresh in the browser edition
  setup.py          setup, import and refresh in the apps; the daily task
  db.py, paths.py   the databases and where files live
  ingest.py         imports and the Scryfall refresh
  importers.py      reading collection CSVs (ManaBox, Moxfield, Archidekt, Deckbox...)
  scryfall.py       Scryfall's bulk files and API, paced to its limits
  net.py, runtime.py  the network and the browser's differences
  collection.py     the collection in memory
  query.py          Scryfall-syntax card search
  pricebook.py      price changes, portfolio, chart series
  history.py, price_files.py  MTGJSON price history (apps; the browser edition's files)
  pack.py           the card pack the server publishes for the browser edition
  decks.py          decks, folders, copy allocation (docs/DECKS.md)
  deckstats.py      deck statistics, shape and bracket estimate
  combos.py         Commander Spellbook's combos
  decksearch.py     the Decks page's search
  deckcheck.py      a pasted list against the collection
  builder.py, manafix.py, goldfish.py, versions.py  Build, mana fixing, goldfish, deck history
  binders.py, manual.py  binders, and cards added by hand
  alerts.py, radar.py  price alerts, the reprint radar
  sets.py, rules.py, redux.py, similar.py  sets, the rules and rulings, the Redux format, similar cards
  ask.py            English → search, with Claude
  backups.py        backups and restore
  page_policy.py    the page's Content-Security-Policy
static/             the page: index.html, app.js, js/, styles.css
web/                the browser edition's engine worker and service worker
scripts/            builds (installer, Android, static site, card pack), the server's publish job,
                    notify.ps1 (Windows notifications for price alerts)
android/            the Android app's Kotlin shell
docs/               design notes: API, decks, setup, the public edition plan
```

## Settings

Settings (the ⚙ button, top right) chooses a theme (Neon, the original; Light; Dark, or Light/Dark following the
device; and under More themes: Lollipop, Astronaut, Necronomicon, Stovepipe, Gamma, Galactus, Unicorn,
Lemonhead, Olympic, SPQR and Sasquatch), text size,
reduced motion, the foil shimmer and hover previews. These are saved in the browser, per device.
It also shows the card data's date and where your data lives, and gathers the housekeeping actions.
In the Windows app, Settings → App sets the port it serves on (8765 unless another program needs it;
if one already has it when Cardclops starts, Cardclops moves to a free port and says so).

## Where your data lives

- **Documents\Cardclops** is yours: `cardclops.sqlite` holds your collection, decks and
  their history, the price history the gallery has recorded, your watchlist, alerts and settings.
  None of it can be downloaded again, so every refresh copies it into `Backups\` (the newest 14
  are kept). This is the folder to back up.
- **%LOCALAPPDATA%\Cardclops** is cache: `cards.sqlite` (Scryfall's card data), the
  downloaded bulk files, card images and logs. Deleting it is safe; the next refresh rebuilds it.

Set `CARDCLOPS_HOME` or `CARDCLOPS_CACHE` to move either folder. Before
September 2026 everything lived in this project's `data/` folder; the first run after the change
moved it and renamed the old folder `data.migrated-<date>`.

## License

Cardclops is under the MIT License (`LICENSE`). The data and software it uses come under their own
terms: card data and images from Scryfall, price history from MTGJSON (MIT), the fonts under the SIL
Open Font License, and the browser edition's Python, Pyodide, under the Mozilla Public License 2.0.
The app's About page (More → About & credits) lists them.

Cardclops is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by
Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.
