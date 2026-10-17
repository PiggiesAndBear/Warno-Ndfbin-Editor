# -*- coding: utf-8 -*-
"""Where the tests get their data, and what happens when there is none.

Importing this module also puts the repository root on ``sys.path``, so a test
only needs::

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _env import TEST, deck        # noqa: E402

It exists for two problems that only show up on somebody else's computer:

**No path may be baked in.**  The suite runs against real WARNO files — that is
the entire point of it — but a literal ``C:\\Users\\<name>\\Saved Games\\...``
both makes the repository work on exactly one machine and publishes that
machine's user name to everyone who clones it.  Every location is therefore
*discovered*, by asking :mod:`ndfbin_decks.io.modlocator` — the same code that
finds mods for the editor, so the tests exercise the real lookup rather than a
second implementation of it.

**A missing game has to read as a skip, not a crash.**  These tests are the
project's evidence that the editor is correct, but on a machine without WARNO
there is nothing for them to be correct about.  A traceback raised from an
import line says "this project is broken", which is the worst first impression
a fresh clone can make.  :func:`skip` prints one line and exits 0, which is
what the ``PASS`` / ``FAIL`` convention the rest of the suite uses extends to
naturally.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: Line-buffer stdout.  These tests print a hundred lines of progress and then
#: raise; when stdout is a pipe (``python -m tests.test_gui | tail``) Python
#: block-buffers it while stderr stays unbuffered, so the traceback arrives
#: *before* the lines it belongs to and ``tail`` shows a section header as the
#: last thing on screen.  That is exactly how a variable shadowing bug got read
#: as "the test binary crashes silently" for twenty minutes.
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):        # stdout replaced or closed
    pass

#: Repository root — and therefore the directory ``ndfbin_decks`` lives in.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Mod directory names the suite is written against.  ``NORTHERN_THUNDER`` is a
#: Steam Workshop mod and is named by its published id, because that is what
#: its folder is called.
TEST = "Test"
BERLIN = "WarnoAGF_berlin_1989"
NORTHERN_THUNDER = "3816950018"

#: The Workshop mod most of the suite loads its documents from — a complete AGF
#: mod carrying Weapon / Unit / Deck / Division, so one sample covers the
#: scalar, reference and list-reference paths alike.  Named by its published id
#: for the same reason as ``NORTHERN_THUNDER``.
SAMPLE = "3388575848"


def skip(why: str) -> None:
    """Report that this test cannot run here, and stop without failing."""
    print(f"SKIP — {why}")
    raise SystemExit(0)


_CACHE: dict | None = None


def mods() -> dict:
    """Every mod the editor can see, keyed by directory name.

    Empty when the machine has no WARNO workspace at all, so callers do not
    have to tell "no game" apart from "no workspace" — both mean there is
    nothing to test.  Cached because discovery walks the Steam library files
    and several tests ask for it more than once.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    from ndfbin_decks.io import modlocator
    try:
        _CACHE = {m.name: m for m in modlocator.discover()}
    except (modlocator.ModNotFound, OSError):
        _CACHE = {}
    return _CACHE


def _mod(name: str):
    """One :class:`ModPaths`, or ``SKIP`` naming every mod that *is* visible."""
    found = mods()
    if not found:
        skip("no WARNO mod workspace on this machine — the game is either not "
             "installed or its mods live somewhere unusual")
    mod = found.get(name)
    if mod is None:
        seen = ", ".join(sorted(found)[:8])
        skip(f"no mod named {name!r} — this machine has {len(found)}: {seen}"
             + ("…" if len(found) > 8 else ""))
    return mod


def mod_dir(name: str) -> Path:
    """The deployed directory of one mod."""
    mod = _mod(name)
    try:
        root = Path(mod.deploy or mod.project)
    except Exception as exc:                           # noqa: BLE001
        skip(f"mod {name!r} has no usable directory ({type(exc).__name__})")
    return root


def opt_mod_dir(name: str) -> Path | None:
    """Like :func:`mod_dir` but ``None`` instead of skipping.

    For the tests where a workshop mod adds coverage rather than defining it:
    requiring a third-party mod to be subscribed would mean those checks never
    run for anyone else.
    """
    mod = mods().get(name)
    if mod is None:
        return None
    try:
        root = Path(mod.deploy or mod.project)
    except Exception:                                  # noqa: BLE001
        return None
    return root if root.is_dir() else None


def gfx(name: str = SAMPLE) -> Path:
    """``<mod>/Gen/NDF/GFX`` — the directory a mod's compiled NDF lives in.

    Most of the suite loads its documents from here.  The path used to be a
    literal ``D:\\Steam\\...`` repeated across ten files, which pinned the
    repository to one machine's Steam library and published that layout to
    everyone who cloned it.  Routing through :func:`mod_dir` also means a
    machine without this mod gets a ``SKIP`` naming the mods it *does* have,
    rather than a ``FileNotFoundError`` raised from an import line.
    """
    return mod_dir(name) / "Gen" / "NDF" / "GFX"


def deck(name: str) -> Path:
    """``Deck.ndfbin`` of one mod, or ``SKIP`` with the reason."""
    mod = _mod(name)
    try:
        path = Path(mod.deck_bin)
    except Exception as exc:                           # noqa: BLE001
        skip(f"mod {name!r} ships no compiled deck ({type(exc).__name__}: {exc})")
    if not path.is_file():
        skip(f"mod {name!r} has no Gen/NDF/GFX/Deck.ndfbin ({path})")
    return path


def decks(*names: str) -> list[tuple[str, Path]]:
    """``[(name, Deck.ndfbin), ...]`` for several mods, or ``SKIP``.

    Checks all of them before deciding, so one run reports everything that is
    missing instead of stopping at the first.
    """
    found = mods()
    if not found:
        skip("no WARNO mod workspace on this machine — the game is either not "
             "installed or its mods live somewhere unusual")
    missing: list[str] = []
    for name in names:
        mod = found.get(name)
        if mod is None:
            missing.append(name)
            continue
        try:
            if not Path(mod.deck_bin).is_file():
                missing.append(f"{name} (no Deck.ndfbin)")
        except Exception:                              # noqa: BLE001
            missing.append(f"{name} (unreadable)")
    if missing:
        seen = ", ".join(sorted(found)[:8])
        skip(f"missing mod(s): {', '.join(missing)} — this machine has "
             f"{len(found)}: {seen}" + ("…" if len(found) > 8 else ""))
    return [(name, deck(name)) for name in names]


def workshop_mods() -> list:
    """Discovered Steam Workshop mods, for the tests that need one."""
    from ndfbin_decks.io import modlocator
    try:
        return [m for m in modlocator.discover()
                if m.source == modlocator.SOURCE_WORKSHOP]
    except (modlocator.ModNotFound, OSError):
        return []


#: Suffixes worth opening.  ``Gen/`` holds tens of thousands of files and only
#: a handful of names are ever CNDF containers, so filtering by suffix first
#: turns a multi-minute walk into a fast one.  ``.ndfbin`` and ``.tag`` are the
#: common ones; the rest show up beside them and cost nothing to include.
_CNDF_SUFFIXES = (
    ".ndfbin", ".tag", ".cat", ".stickers", ".shoot", ".neu3", ".tgi",
)


def cndf_files(*, limit: int | None = None) -> list:
    """Every CNDF (``EUG0``/``CNDF``) file this machine can see.

    Used by the round-trip tests, which are only meaningful against real
    files.  Sources are the discovered workshop mods and the WARNO install's
    ``Gen/`` tree -- both *discovered*, never hard-coded, for the reason given
    in this module's docstring.

    Only the first 44 bytes of each candidate are read, so a 5 MB ``.neu3``
    costs the same as a 200-byte one.  Returns an empty list when nothing is
    found; the caller decides whether that is a skip.
    """
    roots = []
    for mod in workshop_mods():
        try:
            roots.append(Path(mod.path))
        except (AttributeError, TypeError):
            continue
    try:
        from ndfbin_decks.io import modlocator
        roots.append(modlocator.warno_root())
    except Exception:
        pass

    seen = set()
    found = []
    for root in roots:
        if not root or not Path(root).is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(root)):
            # Keep the walk away from trees that never hold game data and are
            # large enough to dominate the runtime.
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in ("crashsender", "logs",
                                                "digital_guides", "steamapps",
                                                "__pycache__", ".git")]
            for fn in filenames:
                if not fn.lower().endswith(_CNDF_SUFFIXES):
                    continue
                p = Path(dirpath) / fn
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    with open(p, "rb") as fh:
                        head = fh.read(44)
                except OSError:
                    continue
                if len(head) >= 44 and head[:4] == b"EUG0" and head[8:12] == b"CNDF":
                    found.append(p)
                    if limit is not None and len(found) >= limit:
                        return sorted(found)
    return sorted(found)


# ---------------------------------------------------------------------------
# Qt
# ---------------------------------------------------------------------------

_APP = None


def qt_app():
    """一个**活到进程结束**的 ``QApplication``。

    Qt 只允许存在一个实例，所以重复调用返回同一个。

    为什么不能像普通变量那样写在函数里：``app = QApplication([])`` 作为
    局部变量，函数一返回就被回收；之后 Python 再清理窗口对象时，会碰到
    一个已经销毁的 ``QApplication``，直接段错误。症状很难查 ——
    **测试全部打印「通过」，退出码却是 139**（或者是 127 这种看起来像
    「命令不存在」的码），而且偶发：释放顺序每次都可能不同。

    也没有别的地方能持有它：测试函数之间不共享作用域，只有模块级变量
    能活到最后。
    """
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP
