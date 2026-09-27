"""Cardclops inside the Android app (see android/ in this repository).

The app's Kotlin side calls these through Chaquopy:
  start(home, cache, token, refresh_allowed, port) -> port
                                      start the engine on `port` (the one it had last time), or a
                                      free one if that is taken; the WebView then loads
                                      http://127.0.0.1:<port>/ with the cardclops_token cookie
  refresh()                           the daily refresh, for a background job
Nothing here runs on the desktop or server.
"""
import os

_server = None


def start(home, cache, token, refresh_allowed=True, port=0):
    """Point Cardclops at the app's private folders, require the token, and start serving.
    `refresh_allowed` is False on metered networks, so a stale cache doesn't download on mobile data.
    The WebView keeps the page's saved settings per origin, port included, so the app asks for the
    same port every launch; if another app holds it, any free port will do."""
    global _server
    if _server is not None:
        return _server.server_address[1]
    os.environ["CARDCLOPS_HOME"] = home
    os.environ["CARDCLOPS_CACHE"] = cache
    os.environ["CARDCLOPS_ACCESS_TOKEN"] = token
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    from . import db, server     # imported only now: paths and the token are read at import
    connection = db.connect()
    try:
        _server, port = server.start_in_background(connection, int(port), refresh_if_stale=refresh_allowed)
    except OSError:
        _server, port = server.start_in_background(connection, 0, refresh_if_stale=refresh_allowed)
    return port


def refresh():
    """Card data, prices, alerts and a backup, as the desktop's daily task does."""
    from .__main__ import main
    return main(["refresh", "--log"])
