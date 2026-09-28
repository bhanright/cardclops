"""Build the card pack for the browser edition (gallery/pack.py, docs/PUBLIC_EDITION_PLAN.md).

    python scripts/build_pack.py OUT_DIR                  # from this machine's card cache
    python scripts/build_pack.py OUT_DIR --cache FILE     # from another cache database

Run it after a refresh (the server does both daily). The cache must come from a refresh by this
version or later, which records the catalog's legalities.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--cache", type=Path, help="the cache database (default: this machine's)")
    args = parser.parse_args()
    from gallery import pack
    from gallery.paths import CACHE_DATABASE_PATH, CACHE_DIR
    pack.build(args.cache or CACHE_DATABASE_PATH, args.out_dir, rules_dir=CACHE_DIR / "rules")


if __name__ == "__main__":
    main()
