"""Remember which language the user picked.

Kept apart from the translation tables because it is the only part of ``i18n``
that writes: the choice has to survive a restart, and the only directory that
survives inside a one-file build is :func:`~ndfbin_decks.paths.data_dir`.

The file is deliberately a flat ``{"language": "fr"}`` that any text editor can
fix.  A user who picks a language that renders the picker unreadable needs a way
back that does not involve running the program.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..paths import data_dir
from . import SOURCE_LANG

FILE = "settings.json"
KEY = "language"


def path() -> Path:
    return data_dir() / FILE


def saved_language() -> str:
    """The remembered choice, or the source language when there is none."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SOURCE_LANG
    value = data.get(KEY)
    return value if isinstance(value, str) and value else SOURCE_LANG


def save_language(code: str) -> None:
    """Write the choice.  A read-only directory must not stop the app."""
    data = {}
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[KEY] = code
    try:
        path().write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    except OSError:
        pass
