"""Fetch the Pyodide files the browser edition serves itself (scripts/build_static.py copies them in).

    python scripts/fetch_pyodide.py

The public site runs only its own scripts: a visitor's Anthropic API key can live in the page (the
Ask box), so nothing may load from a third party. Pyodide is fetched once into vendor/ (not in git)
and every file is checked against the SHA-256 below. Those were taken on 2026-09-28 from jsDelivr
and matched byte for byte against npm's pyodide@0.28.3 package; the sqlite3 wheel's matches the
hash in Pyodide's own lock file. A file that doesn't match is refused.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

VERSION = "0.28.3"
SOURCE = f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/"
TARGET = Path(__file__).resolve().parent.parent / "vendor" / f"pyodide-{VERSION}"
FILES = {
    "pyodide.js": "24a458425dcb4ea9836eb5ce26701d18cb769374e2b79247602ba605bf093278",
    "pyodide.asm.js": "b22e5831eade9ff10e6fe2c811c68688cd91f10154377b4f80debcf5bafa1e56",
    "pyodide.asm.wasm": "5effb6a1a6cc4a1a85bec4622701aa797c031e1de923cbbaf2ad47abdc4ab325",
    "python_stdlib.zip": "71fee17f88a6260ec8c9c7c063533ee59c021fdc88a1ce76247378d3c4a35f4c",
    "pyodide-lock.json": "f6e6f42f451f42affbbcddb00e8c9a3278dcbf399f57aab9f3f568839a7ff4a6",
    # The only package the engine loads (web/engine/worker.js).
    "sqlite3-1.0.0-cp313-cp313-pyodide_2025_0_wasm32.whl": "bc9429a898016f040272b57e44c3beb7cebfa18226cc856d0b4abca88c8ceea7",
}


def fetch():
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        path = TARGET / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        print(f"  {name}")
        request = urllib.request.Request(SOURCE + name, headers={"User-Agent": "Cardclops build"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            sys.exit(f"{name}: SHA-256 {actual} isn't the pinned {expected}; refusing it")
        path.write_bytes(data)
    return TARGET


if __name__ == "__main__":
    print(f"Pyodide {VERSION} in {fetch()}")
