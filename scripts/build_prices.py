"""Update the price store from MTGJSON and publish the browser edition's price files
(gallery/price_files.py). The server runs it daily, after the refresh and scripts/build_pack.py.

    python scripts/build_prices.py OUT_DIR --store STORE.sqlite

The first run seeds the store from MTGJSON's 90-day file; later runs add each day. OUT_DIR is the
pack folder: its manifest.json gains the "prices" entry.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--store", type=Path, required=True, help="the price store (kept between runs)")
    parser.add_argument("--no-update", action="store_true", help="publish the store as it is")
    args = parser.parse_args()
    from gallery import price_files
    if not args.no_update:
        price_files.update_store(args.store)
    entry = price_files.publish(args.store, args.out_dir)
    manifest_path = args.out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest["prices"] = entry
    partial = manifest_path.with_suffix(".part")
    partial.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    partial.replace(manifest_path)


if __name__ == "__main__":
    main()
