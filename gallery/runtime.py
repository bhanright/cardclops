"""What differs when the engine runs in a web browser (the public edition, gallery/browser.py).

Pyodide runs Python in one thread of a Web Worker: there are no other threads to start, no sockets,
and no ssl. Everywhere else (the Windows and Android apps, the server) this module does nothing
special.
"""
import sys
import threading

IN_BROWSER = sys.platform == "emscripten"


def in_background(work, name):
    """Run `work()` without making the caller wait. On a thread normally; in the browser, on the
    worker's event loop just after the current request has been answered (it still runs on the
    only thread there is, so keep it short)."""
    if IN_BROWSER:
        import asyncio
        asyncio.get_event_loop().call_soon(work)
        return
    threading.Thread(target=work, name=name, daemon=True).start()


def request_save():
    """In the browser: ask the worker to write the databases to the browser's storage soon (it
    already does after every request that writes; jobs write between requests)."""
    if IN_BROWSER:
        import js
        js.requestSave()
