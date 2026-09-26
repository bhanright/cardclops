"""Start the gallery from any working directory: python path/to/serve.py [--port 8765]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gallery.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main(["serve", *sys.argv[1:]])
