"""Serve dist-static/ locally the way Cloudflare Pages will: with the headers in its _headers file
(the security policy above all), so the browser edition can be tested under them.

    python scripts/serve_static.py [PORT]        # default 8142

For testing only; Pages itself serves the site. `_headers` rules are a path, or a path ending in *,
followed by indented "Name: value" lines; every matching rule applies.
"""
import http.server
import sys
from functools import partial
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "dist-static"


def read_rules(path):
    rules, current = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            current = (line.strip(), [])
            rules.append(current)
        elif current and ":" in line:
            name, value = line.strip().split(":", 1)
            current[1].append((name.strip(), value.strip()))
    return rules


def matches(pattern, path):
    return path.startswith(pattern[:-1]) if pattern.endswith("*") else path == pattern


class Handler(http.server.SimpleHTTPRequestHandler):
    rules = []

    def end_headers(self):
        path = self.path.split("?", 1)[0]
        for pattern, headers in self.rules:
            if matches(pattern, path):
                for name, value in headers:
                    self.send_header(name, value)
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8142
    Handler.rules = read_rules(SITE / "_headers")
    Handler.extensions_map.update({".wasm": "application/wasm", ".mjs": "text/javascript"})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(SITE)))
    print(f"Serving {SITE} with its _headers at http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
