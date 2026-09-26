"""Where the gallery keeps its files.

Two places, as Windows programs usually split them:

- Documents\\Cardclops — what is yours and can't be downloaded again:
  your collection, decks and their history, the price history the gallery has
  recorded, watchlist and alerts (cardclops.sqlite), and daily backups of it.
  Back this folder up.
- %LOCALAPPDATA%\\Cardclops — cache that rebuilds itself: Scryfall's card
  data (cards.sqlite), downloaded bulk files, card images and logs.

The program's own files (this code) are separate again; an installed copy would
live under Program Files. Set CARDCLOPS_HOME or CARDCLOPS_CACHE
to put either folder somewhere else. On macOS and Linux the same split uses
~/Documents and ~/Library/Caches or $XDG_CACHE_HOME.
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _documents_folder():
    """The user's Documents folder, wherever Windows has redirected it."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        CSIDL_PERSONAL = 5                        # "My Documents"
        if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, 0, buffer) == 0:
            return Path(buffer.value)
    return Path.home() / "Documents"


def _cache_folder():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Cardclops"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Cardclops"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "cardclops"


USER_DIR = Path(os.environ.get("CARDCLOPS_HOME") or _documents_folder() / "Cardclops")
CACHE_DIR = Path(os.environ.get("CARDCLOPS_CACHE") or _cache_folder())

DATABASE_PATH = USER_DIR / "cardclops.sqlite"          # yours: collection, decks, prices, alerts
BACKUP_DIR = USER_DIR / "Backups"
CACHE_DATABASE_PATH = CACHE_DIR / "cards.sqlite"     # Scryfall's card data; rebuilt by a refresh
RAW_DIR = CACHE_DIR / "downloads"                    # bulk files, kept so imports can re-run offline
IMAGE_CACHE_DIR = CACHE_DIR / "images"               # card images, fetched the first time the browser asks
LOG_DIR = CACHE_DIR / "logs"

# Before September 2026 everything lived in the project's data/ folder.
LEGACY_DATA_DIR = PROJECT_ROOT / "data"
# The app was called Collection Gallery until version 0.1.1; its folders move to the new names.
LEGACY_USER_DIR = _documents_folder() / "Collection Gallery"
LEGACY_CACHE_DIR = _cache_folder().with_name("CollectionGallery")

# Scryfall asks every client to identify itself.
USER_AGENT = "Cardclops/0.1 (personal collection viewer)"

README_TEXT = """Cardclops keeps your own data here:

  cardclops.sqlite   your collection, decks and their history, recorded price history,
                     watchlist, alerts and settings. Back this file up: price history
                     older than 90 days cannot be downloaded again.
  Backups\\           a copy of cardclops.sqlite made after each daily refresh (the last 14 are kept).

Card data, images and downloads are cache and live in
  {cache}
Deleting that folder is safe; the next refresh rebuilds it.
"""


def ensure_dirs():
    _move_renamed_folders()
    for directory in (USER_DIR, BACKUP_DIR, CACHE_DIR, RAW_DIR, IMAGE_CACHE_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    readme = USER_DIR / "README.txt"
    if not readme.exists() or "Collection Gallery" in readme.read_text(encoding="utf-8", errors="replace"):
        readme.write_text(README_TEXT.format(cache=CACHE_DIR), encoding="utf-8")


def _move_renamed_folders():
    """Folders named for Collection Gallery become Cardclops's (only for the default locations, and
    only when the new folder doesn't exist yet), with the database and backups renamed inside."""
    if not os.environ.get("CARDCLOPS_HOME") and LEGACY_USER_DIR.exists() and not USER_DIR.exists():
        LEGACY_USER_DIR.rename(USER_DIR)
        for suffix in ("", "-wal", "-shm"):
            old = USER_DIR / f"gallery.sqlite{suffix}"
            if old.exists():
                old.rename(USER_DIR / f"cardclops.sqlite{suffix}")
        for backup in (USER_DIR / "Backups").glob("gallery-*.sqlite"):
            backup.rename(backup.with_name("cardclops-" + backup.name[len("gallery-"):]))
    if not os.environ.get("CARDCLOPS_CACHE") and LEGACY_CACHE_DIR.exists() and not CACHE_DIR.exists():
        LEGACY_CACHE_DIR.rename(CACHE_DIR)


def ssl_context():
    """Certificates for downloads. Windows' own store can reject Let's Encrypt's
    newer chains (it did for Scryfall's image CDN in September 2026), so use
    certifi's bundle when it is installed."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
