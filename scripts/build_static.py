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
    (engine / "manifest.json").write_text(json.dumps({"version": __version__, "files": files, "data_url": data_url}), encoding="utf-8")

    # The page starts through the engine loader instead of straight into app.js.
    index = OUT / "index.html"
    html = index.read_text(encoding="utf-8")
    html, swapped = re.subn(r'<script type="module" src="app\.js"></script>', '<script type="module" src="engine/boot.js"></script>', html)
    if swapped != 1:
        raise SystemExit("index.html: couldn't find the app.js script tag")
    html = html.replace("</head>", '  <link rel="stylesheet" href="engine/boot.css">\n</head>', 1)
    index.write_text(html, encoding="utf-8")
    shutil.copy(ROOT / "web" / "engine" / "boot.css", engine / "boot.css")
    # Cloudflare Pages: always check the service worker and engine files for updates.
    # Pack files are named by date and never change; the manifest and price files are replaced daily.
    (OUT / "_headers").write_text("/sw.js\n  Cache-Control: no-cache\n/engine/*\n  Cache-Control: no-cache\n"
                                  "/pack/manifest.json\n  Cache-Control: no-cache\n/pack/prices/*\n  Cache-Control: no-cache\n"
                                  # the page's own scripts: always revalidated, so a release never mixes versions
                                  "/app.js\n  Cache-Control: no-cache\n/js/*\n  Cache-Control: no-cache\n"
                                  "/styles.css\n  Cache-Control: no-cache\n",
                                  encoding="utf-8")

    if pack:
        shutil.copytree(pack, OUT / "pack")
    if devdata:
        target = OUT / "devdata"
        target.mkdir()
        shutil.copy(devdata[0], target / "cardclops.sqlite")
        shutil.copy(devdata[1], target / "cards.sqlite")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file() and not {"devdata", "pack"} & set(p.parts))
    print(f"Built Cardclops {__version__} (browser edition) in {OUT}: {size / 1e6:.1f} MB, {len(files)} engine files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--devdata", nargs=2, metavar=("USER_DB", "CACHE_DB"), help="databases to seed for ?devdata=1")
    parser.add_argument("--pack", type=Path, help="a pack folder to serve at /pack/ (testing)")
    parser.add_argument("--data-url", default="/pack/", help="where the card pack is published (default /pack/)")
    args = parser.parse_args()
    build(args.devdata, args.pack, args.data_url)
