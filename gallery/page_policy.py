"""The page's Content-Security-Policy, shared by every edition: the public site's _headers file
(scripts/build_static.py) and the page the apps and the private server send (gallery/server.py).

Only the page's own scripts run, plus the inline ones in index.html by their hash. So a link or text
that came from outside (an imported decks file, a card name) can never run as script, not even as a
javascript: address. Card images come from Scryfall's image server and set symbols from its SVG server.
"""
import base64
import hashlib
import re


def inline_script_hashes(html):
    """The CSP hash of each inline <script> in the page."""
    return [f"'sha256-{base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()}'"
            for script in re.findall(r"<script>(.*?)</script>", html, re.S)]


def content_security_policy(html, connect=(), webassembly=False):
    """The policy for `html`. `connect` lists the other origins the page may call (the browser
    edition's card data, Scryfall's API, Anthropic's); `webassembly` lets Pyodide compile."""
    scripts = ["'self'"] + (["'wasm-unsafe-eval'"] if webassembly else []) + inline_script_hashes(html)
    return "; ".join([
        "default-src 'self'",
        "script-src " + " ".join(scripts),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https://cards.scryfall.io https://svgs.scryfall.io",
        "font-src 'self'",
        "connect-src " + " ".join(["'self'", *[origin for origin in connect if origin]]),
        "worker-src 'self'",
        "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
    ])
