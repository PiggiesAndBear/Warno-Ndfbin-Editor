"""Translating the editor's *own* interface.

This is nothing to do with localising the game: WARNO keeps its strings in
``.dic`` dictionaries and that is :mod:`ndfbin_decks.io.tradic`'s job.  This
module translates the buttons, labels, dialogs and messages of this program.

Why not Qt Linguist
-------------------

Linguist's pipeline is built around ``.ui`` files and the ``retranslateUi()``
it generates from them.  This project has no ``.ui`` files — every widget is
constructed in Python — so nothing would be generated, and every string would
have to be wrapped by hand anyway.  On top of that, half the strings that need
translating live in :mod:`ndfbin_decks.edit` and :mod:`ndfbin_decks.io`, which
are plain modules with no ``QObject`` in sight; ``QCoreApplication.translate``
would need a context string invented for every one of them.

So: a small ``tr()`` and one JSON file per language.

Why the key is the Chinese source text
--------------------------------------

Chinese is the language the program was written in, so it is the source of
truth.  Using it as the key buys three things:

* **zh_CN needs no table at all** — a missing table is not a broken UI.
* **A missing translation falls back to Chinese**, never to a blank label or a
  bare key.  A half-translated release degrades instead of breaking.
* **Adding a string cannot silently ship untranslated**: :mod:`tools.i18n_sync`
  diffs the code's keys against every table and exits non-zero on a gap.

Qt's own widgets (``QDialogButtonBox``'s Ok/Cancel, the standard file dialog,
the colour dialog) are *not* in these tables — they come from the ``qt_<lang>.qm``
files shipped with PySide6, installed once at startup by :mod:`.qt_builtin`.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The language the source is written in.  Served straight through, with no
#: table, so it can never fall out of sync with the code.
SOURCE_LANG = "zh_CN"

#: Shipped languages, in the order the picker offers them.  ``(code, label)``
#: with the label written in that language — a language list that is itself
#: translated is one fewer thing for a non-English speaker to decode.
#: Adding a language is two steps: a line here (the label is written in that
#: language, so a list of languages is itself translated) and a ``<code>.json``
#: next to this file.  Nothing else changes.  Deliberately *not* listing every
#: language Qt happens to ship: an entry with no table is a promise the program
#: cannot keep, and :func:`available` would hide it anyway — so the list here is
#: the languages this build actually speaks.
LANGUAGES: tuple[tuple[str, str], ...] = (
    ("zh_CN", "简体中文"),
    ("en", "English"),
)

#: Codes that have a JSON table next to this file.  zh_CN deliberately does not.
TABLE_CODES: tuple[str, ...] = tuple(c for c, _ in LANGUAGES if c != SOURCE_LANG)

_HERE = Path(__file__).resolve().parent
_current: str = SOURCE_LANG
_tables: dict[str, dict[str, str]] = {}


def language() -> str:
    """The code the interface is currently rendered in."""
    return _current


#: ``(namespace, name, factory)`` for every module-level constant that holds
#: translated text — see :func:`live`.
_LIVE: list[tuple[dict, str, object]] = []


def live(namespace: dict, name: str, factory):
    """Bind a module-level constant that *stays* translated.

    ``LEVEL_LABELS = {"platoon": "本排 Pl", ...}`` is evaluated once, at import
    time.  Wrapping it in ``tr()`` would therefore freeze it in whichever
    language happened to be active then, and switching later would move every
    button except those built from it — the kind of half-translated UI that is
    harder to notice than none at all.

    ``LEVEL_LABELS = live(globals(), "LEVEL_LABELS", lambda: {...})`` binds the
    same value but re-runs the factory whenever the language changes, so the
    constant is as live as anything built inside a method.
    """
    namespace[name] = factory()
    _LIVE.append((namespace, name, factory))
    return namespace[name]


def set_language(code: str) -> str:
    """Switch the interface language, returning what is actually in force.

    Everything built after this point — and every constant registered with
    :func:`live` — comes out in the new language.  Widgets already on screen are
    the caller's to retranslate; see ``MainWindow.retranslate()``.
    """
    global _current
    _current = code if code in {c for c, _ in LANGUAGES} else SOURCE_LANG
    for namespace, name, factory in _LIVE:
        namespace[name] = factory()
    return _current


def load(code: str) -> dict[str, str]:
    """The table for ``code``, cached.  Empty for zh_CN and for unknown codes."""
    if code == SOURCE_LANG:
        return {}
    if code not in _tables:
        path = _HERE / f"{code}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        # A table is allowed to be a flat ``{"原文": "译文"}``; anything that is
        # not a string-to-string mapping would blow up at render time, so drop
        # those entries here rather than in the middle of a dialog.
        _tables[code] = {k: v for k, v in data.items()
                         if isinstance(k, str) and isinstance(v, str)}
    return _tables[code]


def tr(text: str, **fields) -> str:
    """Translate ``text``, then fill in its ``{placeholders}``.

    ``tr("共 {n} 个营", n=3)``.  The placeholders are named, never positional:
    a translator is free to reorder them, and a translation that drops one
    fails loudly here instead of rendering a stray ``{}``.
    """
    out = load(_current).get(text, text)
    if fields:
        out = out.format(**fields)
    return out


def has_table(code: str) -> bool:
    """Whether a table file exists for ``code``."""
    return code == SOURCE_LANG or (_HERE / f"{code}.json").is_file()


def available() -> tuple[tuple[str, str], ...]:
    """The languages worth offering: the source plus those with a table.

    Listing a language whose table is missing would let the user pick it and get
    Chinese back, which reads as a broken program rather than as unfinished
    work.  Adding the JSON file is what puts it on the list.
    """
    return tuple(pair for pair in LANGUAGES if has_table(pair[0]))


def coverage(code: str, keys) -> tuple[int, int]:
    """``(translated, total)`` for ``keys`` — what :mod:`tools.i18n_sync` reports."""
    table = load(code)
    keys = list(keys)
    return sum(1 for k in keys if table.get(k)), len(keys)
