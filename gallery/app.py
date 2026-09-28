"""The installed app's entry point (Cardclops.exe).

  Cardclops.exe                 start the gallery in its own window (Windows' WebView2,
                                        no browser controls); closing the window quits. If it
                                        is already running, bring its window forward. Without
                                        WebView2, or with --browser, it opens in the browser.
  Cardclops.exe --refresh       the daily refresh (what the scheduled task runs)
  Cardclops.exe --quit          stop a running gallery (the uninstaller uses this)
  Cardclops.exe --remove-task   remove the daily-refresh task (the uninstaller uses this)

The exe has no console window, so output goes to logs in the cache folder.
Set CARDCLOPS_PORT to use a port other than 8765, and CARDCLOPS_NO_BROWSER=1 to
start without opening a browser (for testing).
"""
import json
import os
import sys
import threading
import urllib.request
import webbrowser

PORT = int(os.environ.get("CARDCLOPS_PORT", "8765"))
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


def _message(text, title="Cardclops"):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)


def _running():
    """Is a Cardclops already answering on our port?"""
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
        try:
            if _post("/api/app/show").get("shown"):
                return 0
        except Exception:
            pass
        webbrowser.open(f"http://localhost:{PORT}/")
        return 0
    _log_to_file("app.log")
    from . import db
    from .server import serve
    if "--browser" not in argv and not os.environ.get("CARDCLOPS_NO_BROWSER"):
        try:
            if _run_in_window(db):
                return 0
        except OSError as error:
            _message(f"Cardclops couldn't start: port {PORT} is in use by another program.\n\n{error}")
            return 1
    try:
        serve(db.connect(), PORT, open_browser=not os.environ.get("CARDCLOPS_NO_BROWSER"))
    except OSError as error:
        _message(f"Cardclops couldn't start: port {PORT} is in use by another program.\n\n{error}")
        return 1
    return 0


class _Window:
    """The app window as the server sees it: bring it forward, or close it (which ends the app)."""

    def __init__(self, window):
        self.window = window

    def show(self):
        self.window.restore()
        self.window.show()
        self.window.on_top = True            # Windows won't let another process steal focus;
        self.window.on_top = False           # a moment on top brings it forward instead

    def close(self):
        self.window.destroy()


# Shown the moment the window opens, while the engine loads the collection (seconds, for a big one).
SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; height: 100%; background: #140a24; color: #fff6e8; font: 600 16px/1.4 'Segoe UI', system-ui, sans-serif; }
body { display: grid; place-content: center; justify-items: center; gap: 18px; }
svg { width: 96px; height: 96px; animation: bob 2.4s ease-in-out infinite; }
@keyframes bob { 50% { transform: translateY(-6px) rotate(-4deg); } }
.bar { width: 180px; height: 10px; border: 3px solid #000; border-radius: 99px; background: #2a1650; overflow: hidden; }
.bar span { display: block; height: 100%; width: 40%; background: #ff3fa4; animation: slide 1.1s ease-in-out infinite alternate; }
@keyframes slide { to { transform: translateX(150%); } }
</style></head><body>
<svg viewBox="0 0 64 64"><path d="M32 4C50 4 60 16 58 32S46 60 30 60 4 50 6 32 16 4 32 4Z" fill="#ff3fa4" stroke="#000" stroke-width="5"/>
<path d="M14 20c6-4 10-3 13 1M50 20c-5-4-9-3-12 1" fill="none" stroke="#000" stroke-width="4" stroke-linecap="round"/>
<ellipse cx="32" cy="35" rx="15" ry="12" fill="#fff6e8" stroke="#000" stroke-width="4"/><circle cx="34" cy="36" r="6.5" fill="#000"/>
<circle cx="36.5" cy="33.5" r="2" fill="#fff6e8"/></svg>
<div>Starting Cardclops…</div><div class="bar"><span></span></div></body></html>"""


def _port_is_free(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))             # raises OSError when another program has it


def _run_in_window(db):
    """Show a native window at once and the page in it once the engine is up; returns when the
    window closes (and ends the process). False when WebView2 can't be used (it is part of
    Windows 11, and of Windows 10 through Edge's updates), so the caller falls back to the
    browser; OSError when the port is taken."""
    try:
        import webview
    except Exception as error:
        print(f"No app window ({error}); opening the browser instead")
        return False
    from . import server
    from .paths import CACHE_DIR, USER_DIR
    _port_is_free(PORT)
    state_path = USER_DIR / "window.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    window = webview.create_window(
        "Cardclops", html=SPLASH, width=state.get("width", 1280), height=state.get("height", 860),
        x=state.get("x"), y=state.get("y"), min_size=(420, 520), maximized=bool(state.get("maximized")),
        background_color="#140a24", text_select=True)
    running = {}

    def boot():
        """On pywebview's worker thread once the window is up: start the engine, then show the page."""
        try:
            running["http"], port = server.start_in_background(db.connect(), PORT)
        except Exception as error:
            print(f"Cardclops couldn't start: {error}")
            window.load_html(f"<body style='background:#140a24;color:#fff6e8;font:16px Segoe UI;padding:40px'>"
                             f"<h2>Cardclops couldn't start</h2><p>{error}</p></body>")
            return
        window.load_url(f"http://127.0.0.1:{port}/")

    def remember_size():
        try:
            state_path.write_text(json.dumps({"width": window.width, "height": window.height, "x": window.x,
                                              "y": window.y, "maximized": bool(state.get("maximized"))}), encoding="utf-8")
        except Exception:
            pass

    def maximized():
        state["maximized"] = True

    def restored():
        state["maximized"] = False

    window.events.closing += remember_size
    window.events.maximized += maximized
    window.events.restored += restored
    server.Handler.app_window = _Window(window)
    webview.settings["ALLOW_DOWNLOADS"] = True     # Export all decks saves a file (a save dialog)
    try:
        # The page's own storage (theme, remembered choices) must survive a restart: not private.
        webview.start(boot, gui="edgechromium", private_mode=False, storage_path=str(CACHE_DIR / "webview"))
    except Exception as error:
        print(f"The app window failed ({error}); opening the browser instead")
        server.Handler.app_window = None
        if "http" not in running:
            running["http"], _ = server.start_in_background(db.connect(), PORT)
        webbrowser.open(f"http://localhost:{PORT}/")
        threading.Event().wait()               # keep serving until the process is ended
    if "http" in running:
        running["http"].shutdown()
    print("App window closed")
    sys.stdout.flush()
    # End at once. Left to itself the process lingers for many seconds while .NET winds down, and
    # holds WebView2's data folder meanwhile, so starting Cardclops again right away would fail.
    # Nothing is lost: every write has committed or rolls back, and background jobs restart.
    os._exit(0)

if __name__ == "__main__":
    sys.exit(main())
