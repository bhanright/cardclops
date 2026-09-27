"""Cardclops inside the Android app (see android/ in this repository).

The app's Kotlin side calls these through Chaquopy:
  start(home, cache, token) -> port   start the engine on a free local port; the WebView then
                                      loads http://127.0.0.1:<port>/ with the cardclops_token cookie
  refresh()                           the daily refresh, for a background job
Nothing here runs on the desktop or server.
"""
import os

_server = None


def start(home, cache, token, refresh_allowed=True):
    """Point Cardclops at the app's private folders, require the token, and start serving.
    `refresh_allowed` is False on metered networks, so a stale cache doesn't download on mobile data."""
    global _server
    if _server is not None:
        return _server.server_address[1]
    os.environ["CARDCLOPS_HOME"] = home
    os.environ["CARDCLOPS_CACHE"] = cache
    os.environ["CARDCLOPS_ACCESS_TOKEN"] = token
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    from . import db, server     # imported only now: paths and the token are read at import
    _server, port = server.start_in_background(db.connect(), 0, refresh_if_stale=refresh_allowed)
    return port


def refresh():
    """Card data, prices, alerts and a backup, as the desktop's daily task does."""
    from .__main__ import main
    return main(["refresh", "--log"])
