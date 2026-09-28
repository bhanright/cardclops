"""First-run setup and background jobs (docs/SETUP.md).

The gallery starts with an empty database; the page runs the setup wizard.
Downloads, imports and refreshes run one at a time in a background thread and
report progress. Nothing is downloaded, scheduled or switched on unless the
user asks for it in the wizard or the More menu.
"""
import os
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timedelta

from . import __version__
from . import db
from .db import backup, get_meta, set_meta
from .paths import CACHE_DIR, LOG_DIR, PROJECT_ROOT, USER_DIR

TASK_NAME = "Cardclops daily refresh"
LEGACY_TASK_NAME = "Collection Gallery daily refresh"      # before 0.1.1
# Approximate download sizes shown for consent: Scryfall's default cards and
# function tags; MTGJSON's 90-day prices and identifiers.
DOWNLOAD_MB = {"scryfall": 85, "history": 62}
STALE_AFTER = timedelta(hours=20)


def installed():
    """True in the PyInstaller build the installer ships."""
    return bool(getattr(sys, "frozen", False))


class Jobs:
    def __init__(self, gallery):
        self.gallery = gallery
        self.lock = threading.Lock()
        self.state = {"job": None, "stage": "", "message": "", "percent": 0, "done": True, "error": None, "result": None}

    # ---- status ----

    def status(self):
        connection = self.gallery.connection
        with self.gallery.lock:
            card_rows = connection.execute("SELECT COUNT(*) FROM printings").fetchone()[0]
            rows = connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
            history = connection.execute("SELECT COUNT(*) FROM price_series WHERE source != 'scryfall'").fetchone()[0]
            completed = get_meta(connection, "setup_complete")
            started = get_meta(connection, "setup_started")
            updated = get_meta(connection, "scryfall_updated_at")
        # Installs from before the wizard existed (data, but no wizard ever started) are complete;
        # a wizard in progress stays in progress until its last step, even after a reload.
        done = completed == "yes" or (not started and card_rows > 0 and rows > 0)
        return {
            "needs_setup": not done, "has_card_data": card_rows > 0, "card_data_date": (updated or "")[:10] or None,
            "has_collection": rows > 0, "collection_rows": rows, "has_price_history": history > 0,
            "data_dir": str(USER_DIR), "cache_dir": str(CACHE_DIR),
            "downloads": {"scryfall_mb": DOWNLOAD_MB["scryfall"], "history_mb": DOWNLOAD_MB["history"]},
            "daily_refresh_scheduled": task_exists(), "platform": sys.platform,
            "ask_available": shutil.which("claude") is not None, "installed": installed(),
            "version": __version__,
        }

    def progress(self):
        with self.lock:
            return dict(self.state)

    # ---- running ----

    def start(self, name, work):
        with self.lock:
            if not self.state["done"]:
                raise RuntimeError(f"Another job is running ({self.state['job']}); wait for it to finish.")
            self.state = {"job": name, "stage": "Starting", "message": "", "percent": 0, "done": False,
                          "error": None, "result": None}
        threading.Thread(target=self._run, args=(work,), name=f"job-{name}", daemon=True).start()
        return {"job": name}

    def _run(self, work):
        try:
            result = work()
            self._update(stage="Done", percent=100, done=True, result=result)
        except Exception as error:
            traceback.print_exc()
            self._update(error=f"{type(error).__name__}: {error}", done=True)

    def _update(self, **fields):
        with self.lock:
            self.state.update(fields)

    def _logger(self, low, high):
        """A log function for ingest/history that moves the bar between `low` and `high` percent."""
        def log(message):
            self._update(message=message.strip())
        def progress(fraction):
            self._update(percent=round(low + (high - low) * fraction))
        return log, progress

    # ---- jobs ----

    def download(self, price_history):
        """First run: Scryfall's card data, and optionally MTGJSON's 90-day price files."""
        with self.gallery.lock:
            if get_meta(self.gallery.connection, "setup_complete") != "yes":
                set_meta(self.gallery.connection, "setup_started", datetime.now().isoformat(timespec="seconds"))
                self.gallery.connection.commit()

        def work():
            from . import history, ingest
            connection = db.connect()          # its own: long writes must not block the page
            try:
                log, progress = self._logger(0, 75)
                self._update(stage="Downloading Scryfall card data")
                ingest.refresh_scryfall(connection, log=log, progress=progress)
                if price_history:
                    self._update(stage="Downloading price history", percent=80)
                    history.prefetch(log=self._logger(80, 95)[0])
                    set_meta(connection, "setup_price_history", "yes")
                    connection.commit()
                date = (get_meta(connection, "scryfall_updated_at") or "")[:10]
            finally:
                connection.close()
            self._update(stage="Loading", percent=96)
            self.gallery.load()
            return {"card_data_date": date}
        return self.start("download", work)

    def import_collection(self, filename, text):
        """Replace the collection with an uploaded CSV, then fill in its cards and prices."""
        def work():
            from . import history, importers, ingest
            connection = db.connect()
            try:
                if not connection.execute("SELECT COUNT(*) FROM printings").fetchone()[0]:
                    raise RuntimeError("Download the card data first.")
                self._update(stage="Matching cards", percent=5)
                parsed = importers.parse_collection(
                    filename, text, connection,
                    progress=lambda done, total: self._update(message=f"{done:,} of {total:,} rows",
                                                              percent=5 + round(40 * done / max(total, 1))))
                if not parsed["rows"]:
                    raise RuntimeError("No cards in that file matched. Is it a collection export?")
                self._update(stage="Saving your collection", percent=48)
                reconciled = ingest.store_holdings(connection, parsed["rows"], filename)
                from .binders import BinderBook
                binders_filled = BinderBook(connection).from_import(parsed["rows"])    # ManaBox's Binder Name
                log, progress = self._logger(50, 75)
                self._update(stage="Card details and today's prices")
                ingest.refresh_scryfall(connection, log=log, progress=progress)
                if get_meta(connection, "setup_price_history") == "yes":
                    self._update(stage="Price history for your cards", percent=78)
                    history.load(connection, log=self._logger(78, 95)[0], reuse_downloads=True)
                set_meta(connection, "data_version", datetime.now().isoformat(timespec="seconds"))
                connection.commit()
                backup(connection)
            finally:
                connection.close()
            self._update(stage="Loading", percent=96)
            self.gallery.load()
            copies = sum(r["quantity"] for r in parsed["rows"])
            return {"format": parsed.get("format"), "rows": len(parsed["rows"]), "copies": copies,
                    "matched": len(parsed["rows"]), "approximate": parsed.get("approximate", [])[:200],
                    "unmatched": parsed.get("unmatched", [])[:200], "total_rows": parsed.get("total_rows"),
                    "manual_reconciled": reconciled, "binders_filled": binders_filled}
        return self.start("import", work)

    def refresh(self):
        """The daily refresh, in the background: card data, prices, alerts, backup."""
        def work():
            from . import history, ingest
            from .__main__ import check_alerts
            connection = db.connect()
            try:
                log, progress = self._logger(0, 70)
                self._update(stage="Scryfall card data and prices")
                ingest.refresh_scryfall(connection, log=log, progress=progress)
                if connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]:
                    self._update(stage="Today's prices from MTGJSON", percent=75)
                    history.load_today(connection, log=log)
                    self._update(stage="Price alerts", percent=90)
                    check_alerts(connection)
                set_meta(connection, "data_version", datetime.now().isoformat(timespec="seconds"))
                connection.commit()
                backup(connection)
                date = (get_meta(connection, "scryfall_updated_at") or "")[:10]
            finally:
                connection.close()
            self._update(stage="Loading", percent=96)
            self.gallery.load()
            return {"card_data_date": date}
        return self.start("refresh", work)

    def refresh_if_stale(self):
        """On launch: refresh in the background when card data is more than 20 hours old."""
        status = self.status()
        if status["needs_setup"] or not status["has_card_data"]:
            return False
        updated = get_meta(self.gallery.connection, "scryfall_updated_at")
        try:
            stale = datetime.now().astimezone() - datetime.fromisoformat(updated) > STALE_AFTER
        except (TypeError, ValueError):
            stale = True
        if stale:
            self.refresh()
        return stale

    def complete(self):
        with self.gallery.lock:
            set_meta(self.gallery.connection, "setup_complete", "yes")
            self.gallery.connection.commit()
        return {"needs_setup": False}


# ---- the Windows scheduled task ----

def _task_command():
    """What the task runs: the installed app's own exe, or pythonw with this source tree."""
    if installed():
        return sys.executable, "--refresh", os.path.dirname(sys.executable)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (pythonw if os.path.exists(pythonw) else sys.executable), "-m gallery refresh --log", str(PROJECT_ROOT)


def _powershell(script):
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=60,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def task_exists():
    if sys.platform != "win32":
        return False
    result = _powershell(f"if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) {{ 'yes' }}")
    return result.stdout.strip() == "yes"


def _task_program():
    result = _powershell(f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue).Actions[0].Execute")
    return result.stdout.strip()


def set_daily_refresh(enabled, only_if_ours=False):
    """Create or remove the 7:30 AM task. It runs only while the user is signed in; no password is stored.

    With `only_if_ours` (the uninstaller), the task is removed only if it runs this very program, so
    uninstalling one copy never removes a task another copy (say, a source checkout) set up."""
    if sys.platform != "win32":
        raise RuntimeError("Scheduled refresh is only available on Windows; the gallery refreshes when it starts.")
    if not enabled:
        if only_if_ours and os.path.normcase(_task_program()) != os.path.normcase(_task_command()[0]):
            return task_exists()
        _powershell(f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue")
        return False
    executable, arguments, folder = _task_command()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script = f"""
$action = New-ScheduledTaskAction -Execute '{executable}' -Argument '{arguments}' -WorkingDirectory '{folder}'
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName '{TASK_NAME}' -Description 'Refreshes Cardclops card data and prices.' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Unregister-ScheduledTask -TaskName '{LEGACY_TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue
"""
    result = _powershell(script)
    if result.returncode != 0:
        raise RuntimeError(f"Couldn't create the scheduled task: {result.stderr.strip()[:300]}")
    return True
