"""Portable entry point. Requires Python 3.12+; no pip install needed."""
from pathlib import Path
import sys

if sys.version_info < (3, 12):
    raise SystemExit('Python 3.12 or newer is required.')
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from codesprint.server import main

if __name__ == '__main__':
    raise SystemExit(main())
