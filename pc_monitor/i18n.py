"""Language layer for the PC host tool.

Design, and why it is shaped like this rather than a key enum:

* **The English text is the key.** Every user-visible string already exists in the
  code as an English literal, so ``t("Start acquisition")`` needs no identifier
  invented per string and cannot silently point at the wrong message. An
  untranslated string degrades to English, which is the behaviour the tool had
  before this module existed.
* **Chinese is the default.** The people using this tool read Chinese; the
  firmware's own serial protocol, field names and packet types stay English
  everywhere, because those are protocol vocabulary, not UI copy.
* **Tables live next to the module they translate** (``i18n_app.py``,
  ``i18n_widgets.py``, ``i18n_export.py``) and register themselves here. One
  shared dictionary edited by several people is a merge conflict waiting to
  happen; per-module tables are not.
* **``missed()`` records every lookup that had no entry.** The test suite fails
  when it is non-empty after a full GUI build, so a string added to a widget
  without a translation cannot pass unnoticed. That is the whole reason this is a
  registry and not a plain dict.
"""

from __future__ import annotations

import importlib
from typing import Iterable

DEFAULT_LANGUAGE = "zh"
LANGUAGES = ("zh", "en")

#: Table modules imported for their side effect of calling register().
_TABLE_MODULES = ("i18n_app", "i18n_widgets", "i18n_export")

_ZH: dict[str, str] = {}
_ACTIVE = DEFAULT_LANGUAGE
_MISSED: set[str] = set()
_LOADED = False
#: Strings that are protocol or unit notation, deliberately shown as written.
#: Declaring them here is what keeps ``missed()`` a real signal: an entry in the
#: table that translates a string to itself would silence the gate while proving
#: nothing, so the table must never contain one.
_EXEMPT: set[str] = set()


def register(mapping: dict[str, str]) -> None:
    """Add English -> Chinese pairs. Later registrations win, so order is stable."""
    for source, target in mapping.items():
        if not source or not target:
            raise ValueError("empty translation entry: %r" % (source,))
        if source == target:
            raise ValueError(
                "%r is its own translation; declare it with exempt() instead" % (source,)
            )
        _ZH[source] = target


def exempt(strings: Iterable[str]) -> None:
    """Mark notation that is correct in every language, so it is never a miss."""
    for text in strings:
        if text in _ZH:
            raise ValueError("%r is both translated and exempt" % (text,))
        _EXEMPT.add(text)


def is_exempt(text: str) -> bool:
    return text in _EXEMPT


def _load_tables() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    for name in _TABLE_MODULES:
        try:
            importlib.import_module("%s.%s" % (__package__, name))
        except ImportError:
            # A table module that does not exist yet is not an error: it is the
            # state while a module is still being converted. missed() is what
            # reports the gap, and it is asserted in tests.
            continue


def set_language(code: str) -> None:
    """Choose the UI language. Unknown codes are refused rather than guessed."""
    if code not in LANGUAGES:
        raise ValueError("unknown language %r, expected one of %s" % (code, ", ".join(LANGUAGES)))
    global _ACTIVE
    _ACTIVE = code
    _load_tables()


def language() -> str:
    return _ACTIVE


def is_chinese() -> bool:
    return _ACTIVE == "zh"


def t(text: str) -> str:
    """Translate a UI string. Falls back to the input, which is the English text."""
    if _ACTIVE == "en" or not text:
        return text
    _load_tables()
    hit = _ZH.get(text)
    if hit is None:
        if text not in _EXEMPT:
            _MISSED.add(text)
        return text
    return hit


def fmt(text: str, *args: object, **kwargs: object) -> str:
    """Translate a %-template and then interpolate it.

    The placeholders are part of the key, so a translation that drops one fails
    loudly at the call site instead of quietly losing a number.
    """
    return t(text) % args if args or not kwargs else t(text).format(**kwargs)


def missed() -> tuple[str, ...]:
    """Strings looked up while Chinese was active with no entry."""
    return tuple(sorted(_MISSED))


def reset_missed() -> None:
    _MISSED.clear()


def entries() -> Iterable[tuple[str, str]]:
    _load_tables()
    return tuple(sorted(_ZH.items()))
