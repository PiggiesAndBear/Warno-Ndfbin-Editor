# -*- coding: utf-8 -*-
"""T0.1 acceptance: the release is internally consistent, and stays that way.

Everything here exists because the corresponding mistake is silent.  A build
labelled with a version the code does not report, a window whose title the
taskbar cannot show, an icon whose entries PyInstaller will not accept, a
``--windowed`` build that dies in the dark — none of them throw.  They just
ship.

Run:  python -m tests.test_release
"""
from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402
from _env import ROOT                                    # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []
PASSES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSES
    if condition:
        PASSES += 1
        print(f"  OK   {label}" + (f"  [{detail}]" if detail else ""))
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}" + (f"  [{detail}]" if detail else ""))


def main() -> int:
    import ndfbin_decks
    from ndfbin_decks import APP_NAME, APP_TAGLINE, APP_TITLE, __version__
    from ndfbin_decks import paths

    print("=== 1. one source for the version ===")
    parts = __version__.split(".")
    check("the version is dotted numeric",
          len(parts) >= 2 and all(p.isdigit() for p in parts), __version__)
    check("the product name and tagline are filled in",
          bool(APP_NAME.strip()) and bool(APP_TAGLINE.strip()),
          f"{APP_NAME!r} / {APP_TAGLINE!r}")
    # build_exe.py names the artefact from this same string; if the regex it
    # greps ever stops matching, the build silently falls back to nothing.
    import build_exe
    check("build_exe.read_version() agrees with the package",
          build_exe.read_version() == __version__,
          build_exe.read_version())
    exe_name = f"{APP_NAME} v{__version__}.exe"
    # Not compared against a literal like "…v0.1.exe": that is a version bump
    # waiting to be forgotten, and the line above already pins the artefact to
    # the number in the code.  What is worth freezing is the *name*, which is
    # the half that is not supposed to move.
    check("the artefact is labelled with the product name",
          APP_NAME == "Warno Ndfbin Editor" and exe_name.startswith(
              APP_NAME) and exe_name.endswith(f"v{__version__}.exe"),
          exe_name)
    # The name is not just cosmetic: it is what separates this build from the
    # deck editor's, and the two share a package.  A release that still called
    # itself "Warno Ndfbin Decks Editor" would overwrite that product's EXE.
    check("and it is not the deck editor's name",
          "Decks" not in APP_NAME and "Deck" not in APP_NAME, APP_NAME)

    print()
    print("=== 2. the icon is one PyInstaller and the shell will both take ===")
    icon = paths.icon_path()
    check("the icon ships with the source", icon.is_file(), str(icon))
    # Parsed here rather than solely through PyInstaller's reader, for two
    # reasons: PyInstaller is a *build* dependency, so relying on it would make
    # this test fail for anyone who installed only requirements.txt; and an
    # independent reader is better evidence than handing the file back to one
    # of the two libraries that produced it.
    raw = icon.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", raw, 0)
    check("it is a well-formed ICO container",
          reserved == 0 and kind == 1 and count >= 4,
          f"reserved={reserved} type={kind} entries={count}")
    sizes: list[int] = []
    dibs = pngs = malformed = 0
    for i in range(count):
        w, h, _colours, _r, _planes, _bits, nbytes, offset = struct.unpack_from(
            "<BBBBHHII", raw, 6 + 16 * i)
        # The width/height byte is 0 for 256 — the field is one byte wide.
        side = w or 256
        sizes.append(side)
        blob = raw[offset:offset + nbytes]
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            pngs += 1
            continue
        if len(blob) < 40:
            malformed += 1
            continue
        bi_size, bi_w, bi_h, bi_planes, bi_bits = struct.unpack_from(
            "<IiiHH", blob, 0)
        if (bi_size == 40 and bi_w == side and bi_h == 2 * side
                and bi_planes == 1 and bi_bits == 32):
            dibs += 1
        else:
            malformed += 1
    check("it carries every size the shell asks for",
          {16, 32, 48, 256}.issubset(set(sizes)), str(sorted(sizes)))
    # PyInstaller copies each entry into RT_ICON byte for byte, so a
    # PNG-compressed entry would ship as a PNG resource.  All of ours must be
    # BITMAPINFOHEADER-led 32-bpp DIBs; ``biHeight`` is doubled because the
    # colour bitmap and the AND mask share one image.
    check("every entry is a 32-bpp DIB, not a PNG",
          dibs == count and pngs == 0 and malformed == 0,
          f"{dibs} DIB / {pngs} PNG / {malformed} malformed of {count}")
    # And when the build tooling happens to be present, that PyInstaller's own
    # reader agrees — it is the component that will actually copy these bytes
    # into the EXE, so its opinion carries the weight.
    try:
        from PyInstaller.utils.win32.icon import IconFile
    except ImportError:
        print("  --    PyInstaller not installed, so its reader was not "
              "consulted (pip install -r requirements-build.txt to include it)")
    else:
        parsed = IconFile(str(icon))
        check("and PyInstaller's own reader accepts every entry",
              len(parsed.images) == len(parsed.entries) == count,
              f"{len(parsed.images)} images")

    print()
    print("=== 3. the title is what the taskbar hint shows ===")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    app = _env.qt_app()
    from ndfbin_decks.ndfedit.gui import MainWindow
    # gui.main() installs the icon at the application level and the window
    # inherits it; doing the same here keeps this the real behaviour rather
    # than an artefact of the test constructing the window on its own.
    app.setWindowIcon(QIcon(str(paths.icon_path())))
    win = MainWindow()
    check("the window title is APP_TITLE, verbatim",
          win.windowTitle() == APP_TITLE, repr(win.windowTitle()))
    check("and it is not the deck editor's window",
          "Deck" not in win.windowTitle(), repr(win.windowTitle()))
    check("the window carries the icon", not win.windowIcon().isNull())
    # Qt appends the application display name to window titles when one is set,
    # so setting it would turn the title into "<APP_TITLE> — <name>".  The
    # default is derived from the executable and cannot be suppressed, so the
    # real check is the native title, which verify_exe.py measures against a
    # running build.  Here we only guard that nobody has *set* one.
    check("no display name is set on the application",
          QApplication.applicationDisplayName() in ("python", "python.exe", "")
          or QApplication.applicationDisplayName() == APP_NAME,
          f"{QApplication.applicationDisplayName()!r} (default, not set by us)")

    print()
    print("=== 4. read-only payload vs the user's own directory ===")
    check("resource_path finds the icon", paths.resource_path(
        "assets", "app.ico").is_file())
    before = list(paths.bundle_root().glob("ndfbin_decks/*"))
    paths.resource_path("assets", "app.ico").read_bytes()
    check("reading a resource writes nothing into the bundle",
          before == list(paths.bundle_root().glob("ndfbin_decks/*")))
    with tempfile.TemporaryDirectory() as tmp:
        os.environ[paths.HOME_ENV] = tmp
        try:
            where = paths.data_dir()
            check("HOME_ENV relocates the writable directory",
                  where == Path(tmp), str(where))
            check("and it is created on demand", where.is_dir())
        finally:
            os.environ.pop(paths.HOME_ENV, None)
    check("without the override it lands in the user profile",
          paths.data_dir().name == paths.APP_DIRNAME, str(paths.data_dir()))

    print()
    print("=== 5. a windowed run is never silent ===")
    import main as entry
    with tempfile.TemporaryDirectory() as tmp:
        os.environ[paths.HOME_ENV] = tmp
        try:
            log = entry.setup_logging()
            check("logging starts when asked", log is not None)
            text = log.read_text(encoding="utf-8")
            check("the log names the version that is in the code",
                  f"v{__version__}" in text, text.strip().splitlines()[-1][:70])
            check("and it is the product that is starting",
                  f"Starting {APP_NAME}" in text)

            # The hook has to leave evidence and must not raise, even with no
            # console to write to.
            from PySide6.QtWidgets import QMessageBox
            shown = []
            real = QMessageBox.critical
            QMessageBox.critical = staticmethod(
                lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
            quiet = sys.stderr
            try:
                entry.install_excepthook(log)
                # The hook mirrors the traceback to stderr.  That is the right
                # behaviour and it is noise here, so it is swallowed for the
                # duration rather than removed.
                sys.stderr = io.StringIO()
                try:
                    raise ValueError("boom from the test")
                except ValueError:
                    sys.excepthook(*sys.exc_info())
            finally:
                sys.stderr = quiet
                QMessageBox.critical = real
            crash = log.with_name("crash.log")
            check("a crash leaves a crash.log", crash.is_file())
            if crash.is_file():
                body = crash.read_text(encoding="utf-8")
                check("with the traceback in it",
                      "ValueError" in body and "boom from the test" in body)
            check("and the user is told where to look",
                  bool(shown) and "crash.log" in shown[0],
                  str(shown[:1])[:80])
        finally:
            os.environ.pop(paths.HOME_ENV, None)

    print()
    print("=== 6. the build tooling will not eat the released binary ===")
    source = (ROOT / "build_exe.py").read_text(encoding="utf-8")
    # ``tail`` is the build runner; the helpers -- and therefore the bundle
    # inspection -- are defined above it, so content checks look at the whole
    # file while "what does the build itself do" looks only here.
    tail = source.split("def main()", 1)[1]
    check("exactly one rmtree, over the two versioned directories",
          source.count("rmtree") == 1
          and "for d in (build_dir, dist_dir):" in source,
          f"{source.count('rmtree')} call(s)")
    check("the versioned directories are the only ones it names",
          'ROOT / f"build_{ver}"' in tail and 'ROOT / f"dist_{ver}"' in tail)
    check("nothing anywhere removes dist/ itself",
          'rmtree(ROOT / "dist")' not in source
          and 'rmtree("dist")' not in source)
    check("it fails when the artefact is missing",
          "if not exe.is_file():" in tail)
    check("it inspects the bundle rather than trusting the exit code",
          "check_bundled" in tail and "CArchiveReader" in source)
    check("it looks inside the nested PYZ for frozen modules",
          "open_embedded_archive" in source)
    check("paths passed to PyInstaller are absolute",
          "str(ROOT /" in tail and '--add-data", f"{icon}' in tail)
    # The picker offers only languages that have a table, and the build ships
    # one --add-data per i18n/*.json.  If those two ever disagree, the EXE
    # either offers a language it cannot deliver — the user switches and gets
    # Chinese back — or silently leaves a finished table out of the bundle.
    from ndfbin_decks import i18n
    tables = sorted(p.stem for p in (ROOT / "ndfbin_decks" / "i18n").glob("*.json"))
    offered = sorted(c for c, _ in i18n.available() if c != i18n.SOURCE_LANG)
    check("every language the picker offers ships a table, and vice versa",
          offered == tables, f"offered={offered} tables={tables}")

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} of {PASSES + len(FAILURES)} checks "
              f"failed:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"PASS — {PASSES} checks: the release is consistent end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
