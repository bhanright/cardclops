"""Build everything the browser edition downloads, into one folder: the card pack, the rules file
beside it, the price files and Commander Spellbook's combos (which the apps download too) (docs/PUBLIC_EDITION_PLAN.md). The server runs it after each daily
refresh; scripts/serve_data.py serves the folder.

    python scripts/publish_data.py OUT_DIR --store STORE.sqlite

Uses this machine's card cache (CARDCLOPS_CACHE), which the refresh has just rebuilt, and its
MTGJSON downloads. STORE is the price store kept between runs (gallery/price_files.py).
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RULES_CHECK_EVERY = timedelta(days=7)
COMBOS_EVERY = timedelta(days=7)          # Spellbook's file is ~700 MB; combos change slowly


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    from gallery import combos, db, pack, price_files
    from gallery.paths import CACHE_DATABASE_PATH, CACHE_DIR
    from gallery.rules import RuleBook

    # The rules: fetched from Wizards the first time and then weekly (a new file only when they've
    # published a new edition), so the pack always has a current copy beside it.
    book = RuleBook(CACHE_DIR / "rules")
    checked = (book.data or {}).get("checked_at")
    if not checked or datetime.now() - datetime.fromisoformat(checked) > RULES_CHECK_EVERY:
        try:
            book.download()
        except Exception as error:                  # an old copy is still worth publishing
            print(f"Rules check failed: {error}")

    pack.build(CACHE_DATABASE_PATH, args.out_dir, rules_dir=CACHE_DIR / "rules")
    price_files.update_store(args.store)
    entry = price_files.publish(args.store, args.out_dir)
    manifest_path = args.out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["prices"] = entry
    published = manifest.get("combos")
    if not published or pack._age(published["built_at"]) > COMBOS_EVERY or published.get("format") != combos.COMBO_FORMAT:
        try:
            manifest["combos"] = published = combos.publish(args.out_dir, args.out_dir.parent / f"{args.out_dir.name}-work")
        except Exception as error:              # last week's combos are still worth serving
            print(f"Combos not updated: {error}")
    if published:
        # This machine's own edition takes them straight from here, not the next day from the host.
        connection = db.connect()
        try:
            if combos.wanted(connection, manifest):
                combos.install(connection, (args.out_dir / published["file"]).read_bytes())
        finally:
            connection.close()
    partial = manifest_path.with_suffix(".part")
    partial.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    partial.replace(manifest_path)
    print(f"Published to {args.out_dir}: pack {manifest['pack']['file']}, prices to {entry['newest_day']}")


if __name__ == "__main__":
    main()
