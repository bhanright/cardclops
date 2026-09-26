"""The installed app's entry point (CollectionGallery.exe).

  CollectionGallery.exe                 start the gallery and open it in the browser; if it is
                                        already running, just open the browser
  CollectionGallery.exe --refresh       the daily refresh (what the scheduled task runs)
  CollectionGallery.exe --quit          stop a running gallery (the uninstaller uses this)
  CollectionGallery.exe --remove-task   remove the daily-refresh task (the uninstaller uses this)

The exe has no console window, so output goes to logs in the cache folder.
Set COLLECTION_GALLERY_PORT to use a port other than 8765, and COLLECTION_GALLERY_NO_BROWSER=1 to
start without opening a browser (for testing).
"""
import json
import os
import sys
import urllib.request
import webbrowser

PORT = int(os.environ.get("COLLECTION_GALLERY_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"


def _log_to_file(name):
    """A windowed exe has no stdout or stderr; the HTTP server writes to stderr, so give it a file."""
    from .paths import LOG_DIR
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / name
    if path.exists() and path.stat().st_size > 5_000_000:      # keep the log from growing forever
        path.replace(path.with_suffix(".old.log"))
    log = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


def _message(text, title="Collection Gallery"):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)


def _running():
    """Is a Collection Gallery already answering on our port?"""
    try:
        request = urllib.request.Request(f"{BASE}/api/setup/status", headers={"Host": f"localhost:{PORT}"})
        with urllib.request.urlopen(request, timeout=2) as response:
            return "needs_setup" in json.load(response)
    except Exception:
        return False


def _post(path):
    request = urllib.request.Request(f"{BASE}{path}", data=b"{}", method="POST",
                                     headers={"Host": f"localhost:{PORT}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--refresh" in argv:
        from .__main__ import main as cli
        return cli(["refresh", "--log"])
    if "--quit" in argv:
        if _running():
            _post("/api/quit")
        return 0
    if "--remove-task" in argv:
        from .setup import set_daily_refresh
        set_daily_refresh(False, only_if_ours=True)
        return 0

    if _running():
        webbrowser.open(f"http://localhost:{PORT}/")
        return 0
    _log_to_file("app.log")
    from . import db
    from .server import serve
    try:
        serve(db.connect(), PORT, open_browser=not os.environ.get("COLLECTION_GALLERY_NO_BROWSER"))
    except OSError as error:
        _message(f"Collection Gallery couldn't start: port {PORT} is in use by another program.\n\n{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
