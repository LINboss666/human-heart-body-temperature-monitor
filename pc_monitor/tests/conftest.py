"""Make `pc_monitor` importable when pytest runs from inside pc_monitor/.

``python -m pytest`` from the repository root imports the package normally; this
only covers ``cd pc_monitor && python -m pytest``, where the parent directory is
not on ``sys.path``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PARENT = Path(__file__).resolve().parents[2]
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

REPO = PARENT
ROOT = Path(__file__).resolve().parents[1]
