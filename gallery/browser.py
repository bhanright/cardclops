"""Cardclops in a web browser: the public edition's engine (docs/PUBLIC_EDITION_PLAN.md).

web/engine/worker.js runs this in Pyodide inside a Web Worker: start() once, then handle() for every
request the page's service worker (web/sw.js) passes on, so the web page talks to it exactly as it
talks to the server. The databases live in the browser's private storage (OPFS), which the worker
mounts at /data and saves after every write.
"""
import json
import os

_api = None


def start(home="/data/user", cache="/data/cache"):
    """Open the databases and build the engine. The environment is set before any other gallery
    module is imported, since gallery/paths.py reads it once."""
    global _api
    os.environ.update(CARDCLOPS_HOME=home, CARDCLOPS_CACHE=cache, CARDCLOPS_JOURNAL="MEMORY",
                      CARDCLOPS_EDITION="browser")
    from . import db
    from .api import Api
    from .server import Gallery
    _api = Api(Gallery(db.connect()))
    return {"rows": len(_api.gallery.collection.entries)}


def handle(method, path, params_json="", body_json=""):
    """One request, as the service worker saw it. Returns a dict for the worker: status, kind,
    content_type, filename, body (text, or bytes for a file) and whether it may have written."""
    params = json.loads(params_json) if params_json else {}
    body = json.loads(body_json) if body_json else {}
    response = _api.handle(method, path, params, body)
    content_type, text = response.content_type, response.body
    if response.kind == "json":
        content_type, text = "application/json; charset=utf-8", json.dumps(response.body, separators=(",", ":"))
    elif response.kind == "download":
        content_type, text = "application/json; charset=utf-8", json.dumps(response.body, indent=1, ensure_ascii=False)
    elif response.kind == "text":
        content_type = "text/plain; charset=utf-8"
    return {"status": int(response.status), "kind": response.kind, "content_type": content_type,
            "filename": response.filename, "body": text, "wrote": method != "GET"}
