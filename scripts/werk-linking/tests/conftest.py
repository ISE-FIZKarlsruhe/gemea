"""Puts scripts/werk-linking and this folder on sys.path, since the hyphenated folder is no package."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]
