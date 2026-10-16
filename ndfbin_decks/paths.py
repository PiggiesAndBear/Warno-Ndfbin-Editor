"""Where the pieces live: in a source checkout, and inside a one-file bundle.

A ``--onefile`` build unpacks itself into a **fresh temporary directory** on
every launch (``sys._MEIPASS``) and deletes it on exit, so anything read from
there is effectively read-only: a file the user edits is gone the next time the
app starts.  Two locations therefore exist, and keeping them apart is the whole
point of this module.

``resource_path()``
    Payload shipped inside the bundle — the window icon today, a seed config or
    a bundled dictionary if one is ever added.  Read freely, **never write**.

``data_dir()``
    The user's own directory.  Logs and crash reports go here, and it is the
    only place an edit made by the user can survive a restart.

``HOME_ENV`` points the second one somewhere else, which is what the packaging
smoke test uses to keep a run from touching the real user profile.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: Set this to relocate :func:`data_dir` — used by tests and by the smoke test
#: that runs the built EXE in an isolated directory.  Named after *this*
#: product: the deck-only editor this checkout grew out of kept its logs under
#: ``NDFDECKS_HOME``, and sharing the variable would have the two builds write
#: over each other's logs and crash reports.
HOME_ENV = "NDFBIN_EDITOR_HOME"

#: Folder name under ``%LOCALAPPDATA%``.
APP_DIRNAME = "WarnoNdfbinEditor"


def frozen() -> bool:
    """True when running from a PyInstaller build rather than a checkout."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """The root that :func:`resource_path` resolves against.

    ``sys._MEIPASS`` is where one-file and one-dir builds put their payload;
    next to the package is where a checkout keeps it.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    """A shipped, read-only file.  Do not write to the result."""
    return bundle_root().joinpath(*parts)


def data_dir() -> Path:
    """The user's writable directory, created on first use.

    ``%LOCALAPPDATA%`` can be set but empty, which ``os.environ.get`` would
    return as ``""`` and ``Path("")`` would turn into the current directory —
    hence the ``or`` rather than a default argument.
    """
    override = os.environ.get(HOME_ENV)
    base = (Path(override) if override
            else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_DIRNAME)
    base.mkdir(parents=True, exist_ok=True)
    return base


def icon_path() -> Path:
    """The application icon, whichever shape of build this is."""
    return resource_path("assets", "app.ico")
