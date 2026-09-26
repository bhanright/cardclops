"""Command line: python -m gallery <command>

  import <export.csv>   replace the collection with a ManaBox CSV export
  refresh               pull today's Scryfall data and record today's prices (run daily)
  history               backfill the last 90 days of prices from MTGJSON (see gallery/history.py)
  serve [--port 8765]   run the gallery at http://localhost:8765

Your data lives in the Cardclops folder in Documents, and the card cache in
Cardclops under %LOCALAPPDATA% (see gallery/paths.py).
"""
import argparse
import sys
from datetime import datetime

from . import db, ingest


def check_alerts(connection):
    """After the daily refresh: run the price alerts and show a notification for anything new."""
    from .alerts import Alerts
    from .collection import Collection
    from .pricebook import PriceBook
    from .radar import Radar
    collection = Collection(connection)
    alerts = Alerts(connection)
    new = alerts.check(collection, PriceBook(connection, collection), Radar(connection, collection))
    print(f"Alerts: {len(new)} new")
    alerts.notify(new)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m gallery", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="import a ManaBox CSV export")
    importer.add_argument("csv_path")
    importer.add_argument("--no-refresh", action="store_true", help="skip the Scryfall refresh")
    refresher = commands.add_parser("refresh", help="refresh Scryfall data and today's prices")
    refresher.add_argument("--log-file", help="append output here (the scheduled task runs without a console)")
    refresher.add_argument("--log", action="store_true", help="append output to the standard log file")
    commands.add_parser("history", help="load past price history")
    server = commands.add_parser("serve", help="run the web gallery")
    server.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if getattr(args, "log", False) and not args.log_file:
        from .paths import LOG_DIR
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        args.log_file = str(LOG_DIR / "refresh.log")
    if getattr(args, "log_file", None):
        log = open(args.log_file, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"===== {datetime.now():%Y-%m-%d %H:%M:%S}")

    connection = db.connect()
    if args.command == "import":
        ingest.import_manabox(connection, args.csv_path)
        if not args.no_refresh:
            from . import history
            ingest.refresh_scryfall(connection)
            history.load_today(connection)
    elif args.command == "refresh":
        from . import history
        ingest.refresh_scryfall(connection)
        history.load_today(connection)
        check_alerts(connection)
    elif args.command == "history":
        from . import history
        history.load(connection)
    elif args.command == "serve":
        from . import server as web
        web.serve(connection, args.port)
        return
    # Tell a running gallery the data changed; it reloads within a minute.
    db.set_meta(connection, "data_version", datetime.now().isoformat(timespec="seconds"))
    connection.commit()
    print(f"Backed up your data to {db.backup(connection)}")


if __name__ == "__main__":
    sys.exit(main())
