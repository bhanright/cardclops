"""Every download the engine makes goes through here.

The apps and the server use urllib. In the browser (gallery/runtime.py) Python has no sockets or
ssl, so the same calls go through the worker's XMLHttpRequest, which may wait for its answer inside
a Web Worker; the browser sends its own User-Agent, as Scryfall asks of web pages. Jobs that make
many requests use `fetch_async` instead, so the page is answered between them.

Large files streamed to disk (`download`) are for the apps and the server only: the browser gets
its card data from the card pack (docs/PUBLIC_EDITION_PLAN.md).
"""
import json
import urllib.error
import urllib.request

from .paths import USER_AGENT, ssl_context
from .runtime import IN_BROWSER


class NetError(OSError):
    """A request that failed: `status` is the HTTP status, or 0 when there was no answer."""

    def __init__(self, url, status, reason=""):
        super().__init__(f"{url} answered {status} {reason}".strip() if status else f"Couldn't reach {url}: {reason}")
        self.url = url
        self.status = status


def get(url, data=None, headers=None, timeout=60):
    """The body of a GET, or with `data` (bytes) a POST, as bytes."""
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if IN_BROWSER:
        return _browser_get(url, data, headers)
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise NetError(url, error.code, error.reason) from None


def get_json(url, payload=None, timeout=60):
    """GET a JSON document, or with `payload` POST one; returns the parsed answer."""
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    return json.loads(get(url, data, headers, timeout))


def download(url, target, progress=None, total=0, timeout=600):
    """Stream a large file to `target`, through a .part file so a broken download leaves nothing.
    `progress(fraction)` is called as it goes when `total` (bytes) is known."""
    if IN_BROWSER:
        raise RuntimeError("Large downloads aren't available in the browser edition")
    partial = target.with_suffix(target.suffix + ".part")
    done = 0
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(min(done / total, 1.0))
    partial.replace(target)
    return target


def _browser_get(url, data, headers):
    from js import Uint8Array, XMLHttpRequest
    request = XMLHttpRequest.new()
    request.open("POST" if data is not None else "GET", url, False)      # waits: allowed in a worker
    request.responseType = "arraybuffer"
    for name, value in headers.items():
        if name.lower() != "user-agent":            # the browser keeps its own
            request.setRequestHeader(name, value)
    try:
        request.send(data.decode() if data is not None else None)
    except Exception as error:                      # the browser blocked it, or no connection
        raise NetError(url, 0, str(error)) from None
    if request.status == 0:
        raise NetError(url, 0, "no answer (offline, or the site doesn't allow requests from web pages)")
    if request.status >= 400:
        raise NetError(url, request.status, request.statusText)
    return Uint8Array.new(request.response).to_bytes()


async def fetch_async(url, payload=None, progress=None):
    """The browser's fetch, for jobs: bytes of the answer (POSTing `payload` as JSON if given).
    `progress(done_bytes, total_bytes)` is called as it arrives. Browser edition only."""
    import js
    from pyodide.ffi import to_js
    options = {}
    if payload is not None:
        options = {"method": "POST", "body": json.dumps(payload),
                   "headers": {"Accept": "application/json", "Content-Type": "application/json"}}
    try:
        response = await js.fetch(url, to_js(options, dict_converter=js.Object.fromEntries))
    except Exception as error:
        raise NetError(url, 0, str(error)) from None
    if not response.ok:
        raise NetError(url, response.status, response.statusText)
    total = int(response.headers.get("Content-Length") or 0)
    if progress is None or not total:
        return (await response.arrayBuffer()).to_bytes()
    reader = response.body.getReader()
    chunks, done = [], 0
    while True:
        part = await reader.read()
        if part.done:
            break
        chunk = part.value.to_bytes()
        chunks.append(chunk)
        done += len(chunk)
        progress(done, total)
    return b"".join(chunks)
