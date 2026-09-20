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

# --- language pin -----------------------------------------------------------
#
# The behavioural tests in this directory assert on real strings the tool shows
# and writes -- the demo banner, the workbook sheet names, the caveats a
# synthetic session carries. Those are all translated at display time now, so
# each test runs with the language it was written against, and
# tests/test_i18n.py is what covers the Chinese default. Without this pin a
# translation edit would look like a functional regression.

import pytest  # noqa: E402

from pc_monitor import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def _default_language_for_behaviour_tests():
    previous = i18n.language()
    i18n.set_language("en")
    i18n.reset_missed()
    yield
    i18n.set_language(previous)
    i18n.reset_missed()
