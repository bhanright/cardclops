"""PyInstaller's entry script for CollectionGallery.exe (see gallery/app.py)."""
import sys

from gallery.app import main

if __name__ == "__main__":
    sys.exit(main())
