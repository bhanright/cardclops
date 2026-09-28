"""Serve the browser edition's data folder (scripts/publish_data.py) on the server, behind Cloudflare.

    python scripts/serve_data.py FOLDER [--port 8766] [--origin https://cardclops.com]

Files only (no folder listings), GET and HEAD only, on the loopback address; the tunnel carries it
out. The site at --origin may read the files (CORS). Cloudflare caches by these Cache-Control
headers: a pack file is named by its build time and never changes, so it's kept a year; the manifest
and price files are replaced daily, so they're kept five minutes; the rules file a day.
"""
import argparse
import http.server
from functools import partial

LONG = "public, max-age=31536000, immutable"
SHORT = "public, max-age=300"
DAY = "public, max-age=86400"


class Handler(http.server.SimpleHTTPRequestHandler):
    origin = "https://cardclops.com"

    def list_directory(self, path):
        self.send_error(404)
        return None

    def end_headers(self):
        path = self.path.split("?", 1)[0]
        self.send_header("Access-Control-Allow-Origin", self.origin)
        self.send_header("Vary", "Origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        if path.startswith("/cards-") and path.endswith(".sqlite.gz"):
            self.send_header("Cache-Control", LONG)
        elif path.startswith("/rules/"):
            self.send_header("Cache-Control", DAY)
        else:
            self.send_header("Cache-Control", SHORT)
        super().end_headers()

    def do_POST(self):
        self.send_error(405)

    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, format, *args):             # the journal needn't hold every request
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--origin", default="https://cardclops.com")
    args = parser.parse_args()
    Handler.origin = args.origin
    # A .gz file is sent as it is (the page unpacks it), never as a compressed text response.
    Handler.extensions_map.update({".gz": "application/gzip", ".json": "application/json", ".txt": "text/plain; charset=utf-8"})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=args.folder))
    print(f"Serving {args.folder} at http://127.0.0.1:{args.port} for {args.origin}")
    server.serve_forever()


if __name__ == "__main__":
    main()
