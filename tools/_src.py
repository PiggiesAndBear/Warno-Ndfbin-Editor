# -*- coding: utf-8 -*-
"""Where the recon scripts find the mod they work against.

Every script in this directory used to carry a literal
``D:\\Steam\\...\\3388575848``.  That made the repository work on exactly one
machine and published that machine's Steam layout to everyone who cloned it.
The path is now *discovered*, through the same
:mod:`ndfbin_decks.io.modlocator` the editor and the test suite use, so there
is one implementation of "where are the mods" rather than three.

Point them at a different mod with::

    set NDFBIN_SAMPLE_MOD=D:\\somewhere\\else\\MyMod

A script run on a machine without WARNO stops with one line naming what it did
find — the same bargain ``tests/_env.py`` makes, for the same reason: a
traceback raised from an import line reads as "this project is broken", which
is the worst first impression a fresh clone can make.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Published Workshop id of the mod these scripts default to.
SAMPLE = "3388575848"


def mod_root(name: str = SAMPLE) -> Path:
    """The deployed directory of one mod, or ``SystemExit`` with the reason."""
    override = os.environ.get("NDFBIN_SAMPLE_MOD")
    if override:
        return Path(override)
    from ndfbin_decks.io import modlocator
    try:
        mods = {m.name: m for m in modlocator.discover()}
    except (modlocator.ModNotFound, OSError) as exc:
        raise SystemExit(f"no WARNO mod workspace on this machine ({exc})")
    mod = mods.get(name)
    if mod is None:
        seen = ", ".join(sorted(mods)[:8]) or "(none)"
        raise SystemExit(f"no mod named {name!r} — this machine has: {seen}")
    root = mod.deploy or mod.project
    if root is None:
        raise SystemExit(f"mod {name!r} resolved to no directory at all")
    return Path(root)


def gfx(name: str = SAMPLE) -> Path:
    """``<mod>/Gen/NDF/GFX`` — the directory a mod's compiled NDF lives in."""
    return mod_root(name) / "Gen" / "NDF" / "GFX"


def vanilla_gfx() -> Path | None:
    """``Gen/NDF/GFX`` inside the WARNO install itself, or ``None``.

    ``None`` rather than an exception because its caller builds a list of
    candidate roots: "the game is not installed here" is an ordinary answer for
    a script that can still do useful work against a mod folder.
    """
    from ndfbin_decks.io import modlocator
    try:
        return modlocator.warno_root() / "Gen" / "NDF" / "GFX"
    except Exception:                                  # noqa: BLE001
        return None
