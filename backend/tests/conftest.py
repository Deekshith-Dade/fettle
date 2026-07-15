"""Make `app` importable when pytest runs from anywhere (repo root, backend/, CI)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
