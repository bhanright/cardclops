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
import base64
import hashlib
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
    index.write_text(html, encoding="utf-8")
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


def headers(html, data_url):
    """Cloudflare Pages' _headers file: the security policy for every file, and revalidation for the
    files a release replaces."""
    inline = re.findall(r"<script>(.*?)</script>", html, re.S)
    hashes = " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()}'" for s in inline)
    data_origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(data_url)) if "://" in data_url else ""
    # Only the site's own scripts run (a visitor's Anthropic API key can be in the page), and it talks
    # only to itself, the card data, Scryfall's API and Anthropic's. Card images come from Scryfall's
    # image server and set symbols from its SVG server. Pyodide compiles WebAssembly ('wasm-unsafe-eval').
    policy = "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'wasm-unsafe-eval' {hashes}".strip(),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https://cards.scryfall.io https://svgs.scryfall.io",
        "font-src 'self'",
        f"connect-src 'self' {data_origin} https://api.scryfall.com https://api.anthropic.com".replace("  ", " "),
        "worker-src 'self'",
        "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
    ])
    lines = ["/*", f"  Content-Security-Policy: {policy}", "  X-Content-Type-Options: nosniff",
             "  Referrer-Policy: strict-origin-when-cross-origin"]
    # The service worker, the engine and the page's own scripts are always revalidated, so a release
    # never runs next to stale files; Pyodide's are named by version and can be kept.
    for path in ("/sw.js", "/engine/*", "/app.js", "/js/*", "/styles.css", "/index.html", "/",
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
