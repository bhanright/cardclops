# Cardclops

A local gallery for a Magic: The Gathering collection exported from ManaBox, with
Scryfall's card data, Scryfall-syntax search across your own cards, price history,
and a few tools the big collection sites don't have.

Everything runs on this computer. Card data comes from Scryfall's daily bulk files,
prices from Scryfall and MTGJSON, images from Scryfall's image CDN the first time a
card scrolls into view (then from the image cache).

## Installing (for anyone)

`installer\Output\Cardclops-Setup-<version>.exe` installs the gallery like any Windows
program: for the current user by default (no administrator prompt), or for everyone under Program
Files. The first time it opens, a setup wizard asks before downloading Scryfall's card data (and,
optionally, 90 days of price history), then imports a collection CSV from ManaBox, Moxfield,
Archidekt, Deckbox, TCGplayer, Dragon Shield, Delver Lens or Helvault, or starts empty. Daily
updates and price-alert notifications stay off unless the user turns them on. Uninstalling keeps
the user's data in Documents\Cardclops.

Build it with `scripts\build_installer.ps1` (needs `pip install pyinstaller certifi` and Inno Setup 6).

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
gallery/          Python package (stdlib only, plus certifi)
  ingest.py       ManaBox import, Scryfall refresh
  history.py      MTGJSON price history
  collection.py   the collection in memory
  query.py        Scryfall-syntax search engine
  pricebook.py    price changes, portfolio, chart series
  similar.py      "similar cards you own" (TF-IDF over rules text and function tags)
  deckcheck.py    decklist vs collection
  ask.py          English → query via the claude CLI
  server.py       HTTP server and JSON API (docs/API.md)
static/           the web page
scripts/          notify.ps1, the Windows notification for price alerts
```

## Settings

Tools → Settings chooses a theme (Neon, the original; Light; Dark, or Light/Dark following the
device; and under More themes: Lollipop, Astronaut and Necronomicon), text size,
reduced motion, the foil shimmer and hover previews. These are saved in the browser, per device.
It also shows the card data's date and where your data lives, and gathers the housekeeping actions.

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
