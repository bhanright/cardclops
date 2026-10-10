"""Build the public, browser-only edition into dist-static/ (docs/PUBLIC_EDITION_PLAN.md).

    python scripts/build_static.py                 # the site
    python scripts/build_static.py --devdata U C   # plus databases for ?devdata=1 (testing only)
    python scripts/build_static.py --pack DIR      # plus a card pack folder served at /pack/ (testing)

The site is the normal web page (static/), the service worker and engine loader (web/), and the
engine's Python files, which the browser runs with Pyodide. Nothing here needs a server. The card
pack and price files (scripts/build_pack.py, build_prices.py) are published separately, daily, at
--data-url; for testing, --pack copies a pack folder into the site at /pack/.
"""
import argparse
import json
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist-static"


def build(devdata=None, pack=None, data_url="/pack/"):
    sys.path.insert(0, str(ROOT))
    from gallery import __version__
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "static", OUT)
    shutil.copy(ROOT / "web" / "sw.js", OUT / "sw.js")
    engine = OUT / "engine"
    engine.mkdir()
    for name in ("worker.js", "boot.js"):
        shutil.copy(ROOT / "web" / "engine" / name, engine / name)
    py = engine / "py" / "gallery"
    py.mkdir(parents=True)
    files = sorted(p.name for p in (ROOT / "gallery").glob("*.py"))
    for name in files:
        shutil.copy(ROOT / "gallery" / name, py / name)
    (engine / "manifest.json").write_text(json.dumps({"version": __version__, "files": files, "data_url": data_url,
                                                      "pyodide_url": "/pyodide/"}), encoding="utf-8")
    # Pyodide, served with the site rather than from a CDN (scripts/fetch_pyodide.py checks every file).
    sys.path.insert(0, str(ROOT / "scripts"))
    from fetch_pyodide import fetch
    shutil.copytree(fetch(), OUT / "pyodide")

    # The page starts through the engine loader instead of straight into app.js.
    index = OUT / "index.html"
    html = index.read_text(encoding="utf-8")
    html, swapped = re.subn(r'<script type="module" src="app\.js"></script>', '<script type="module" src="engine/boot.js"></script>', html)
    if swapped != 1:
        raise SystemExit("index.html: couldn't find the app.js script tag")
    html = html.replace("</head>", '  <link rel="stylesheet" href="engine/boot.css">\n</head>', 1)
    html = for_search_engines(html)
    index.write_text(html, encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE}sitemap.xml\n", encoding="utf-8")
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE}</loc><changefreq>weekly</changefreq></url>\n</urlset>\n", encoding="utf-8")
    shutil.copy(ROOT / "web" / "engine" / "boot.css", engine / "boot.css")
    (OUT / "_headers").write_text(headers(html, data_url), encoding="utf-8")

    if pack:
        shutil.copytree(pack, OUT / "pack")
    if devdata:
        target = OUT / "devdata"
        target.mkdir()
        shutil.copy(devdata[0], target / "cardclops.sqlite")
        shutil.copy(devdata[1], target / "cards.sqlite")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file() and not {"devdata", "pack", "pyodide"} & set(p.parts))
    print(f"Built Cardclops {__version__} (browser edition) in {OUT}: {size / 1e6:.1f} MB, {len(files)} engine files")


SITE = "https://cardclops.com/"
# The public site's title in search results: what Cardclops is, in the words people search with.
SEARCH_TITLE = "Cardclops: Free MTG Collection, Price and Deck Tracker"
# Tells search engines this is a free web app (schema.org), for richer results.
STRUCTURED_DATA = {
    "@context": "https://schema.org", "@type": "WebApplication", "name": "Cardclops", "url": SITE,
    "description": "A free Magic: The Gathering collection manager: search your cards like Scryfall, track prices, "
                   "build and check decks. No account; your collection stays on your device.",
    "applicationCategory": "UtilitiesApplication", "operatingSystem": "Web browser, Windows, Android",
    "image": SITE + "images/social-preview.png", "isAccessibleForFree": True,
    "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
}
# What Cardclops does, as plain text in the page itself. The app starts in the browser, so without
# this a search engine reading the page finds little but menu labels. Visitors see the loading screen
# over it, and engine/boot.js removes it once the app is running.
INTRO = """<section id="intro" class="site-intro" aria-labelledby="intro-title">
      <h1 id="intro-title">Cardclops: a free Magic: The Gathering collection tracker</h1>
      <p>Cardclops keeps your Magic: The Gathering collection searchable, priced and ready for deckbuilding.
        Import your collection from ManaBox and it runs right in your browser, with no account; your cards stay on your device.
        It is also available as a free Windows and Android app.</p>
      <h2>Search your own cards</h2>
      <p>Search the cards you own with Scryfall-style syntax, browse them as a gallery or a list, and see set completion for every set.</p>
      <h2>Track prices and value</h2>
      <p>See what your collection is worth with daily TCGplayer, Card Kingdom, Cardmarket and ManaPool prices from Scryfall and MTGJSON,
        follow its value over time, find your biggest winners and losers, and set price alerts on cards you want to buy or sell.</p>
      <h2>Build and check decks</h2>
      <p>Paste a deck list or import one from Archidekt to see which cards you already own, which are missing and what it costs to finish.
        Commander decks get a deck-shape check and a bracket estimate, with combos from Commander Spellbook.
        Find decks you can build from your collection, a trade binder of spare copies, and your cards in upcoming reprints.</p>
    </section>
    """


def for_search_engines(html):
    """The public page as search engines should read it: its title, its one address, what it is,
    and a description in words."""
    html, titled = re.subn(r"<title>Cardclops</title>", f"<title>{SEARCH_TITLE}</title>", html)
    main, found = re.subn(r'(<main id="main">\s*)', lambda m: m.group(1) + INTRO, html)
    if titled != 1 or found != 1:
        raise SystemExit("index.html: couldn't find the title or the main element")
    head = (f'  <link rel="canonical" href="{SITE}">\n'
            f'  <script type="application/ld+json">{json.dumps(STRUCTURED_DATA, separators=(",", ":"))}</script>\n</head>')
    return main.replace("</head>", head, 1)


def headers(html, data_url):
    """Cloudflare Pages' _headers file: the security policy for every file, and revalidation for the
    files a release replaces."""
    from gallery.page_policy import content_security_policy
    data_origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(data_url)) if "://" in data_url else ""
    # Only the site's own scripts run (a visitor's Anthropic API key can be in the page), plus the
    # cookieless visit counter Cloudflare adds to the site (Web Analytics), and the page talks only to
    # itself, the card data, Scryfall's API, Anthropic's and that counter. Pyodide compiles WebAssembly.
    policy = content_security_policy(html, connect=(data_origin, "https://api.scryfall.com", "https://api.anthropic.com",
                                                    "https://cloudflareinsights.com"),
                                     webassembly=True, scripts_from=("https://static.cloudflareinsights.com",))
    lines = ["/*", f"  Content-Security-Policy: {policy}", "  X-Content-Type-Options: nosniff",
             "  Referrer-Policy: strict-origin-when-cross-origin"]
    # The service worker, the engine and the page's own scripts are always revalidated, so a release
    # never runs next to stale files; Pyodide's are named by version and can be kept.
    for path in ("/sw.js", "/engine/*", "/app.js", "/js/*", "/styles.css", "/themes.css", "/index.html", "/",
                 "/pack/manifest.json", "/pack/prices/*"):
        lines += [path, "  Cache-Control: no-cache"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--devdata", nargs=2, metavar=("USER_DB", "CACHE_DB"), help="databases to seed for ?devdata=1")
    parser.add_argument("--pack", type=Path, help="a pack folder to serve at /pack/ (testing)")
    parser.add_argument("--data-url", default="/pack/", help="where the card pack is published (default /pack/)")
    args = parser.parse_args()
    build(args.devdata, args.pack, args.data_url)
