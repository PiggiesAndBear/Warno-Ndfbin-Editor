# -*- coding: utf-8 -*-
"""The single entry point — used by the frozen EXE and by a source checkout.

    python main.py

Three things happen here rather than in ``gui.py``, because they only start to
matter once the app is a ``--windowed`` binary:

* **``sys.stdout`` and ``sys.stderr`` are ``None``** in a PyInstaller windowed
  build — there is no console to attach to.  Any library that writes to them,
  or any traceback raised on a worker thread, dies with ``AttributeError`` at
  the exact moment something else already went wrong.  Both are pointed at a
  log file instead, which also means the app's own complaints are recoverable.
* **A crash before the window exists is invisible.**  Double-clicking an EXE
  that dies during import shows nothing at all, which reads as "your file is
  broken" and cannot be supported by asking the user to read a console they do
  not have.  The excepthook below writes the traceback to disk and, if Qt can
  still start, puts it on screen.
* **The shell needs to be told who this is.**  See :func:`set_app_id`.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ndfbin_decks import APP_NAME, APP_TAGLINE, __version__      # noqa: E402
from ndfbin_decks import paths                                   # noqa: E402
from ndfbin_decks.i18n import tr

#: Keep the log openable.  One line per launch is nothing; a warning that
#: repeats on every start is not.
LOG_LIMIT = 512 * 1024


def _rotate(log: Path) -> None:
    try:
        if log.is_file() and log.stat().st_size > LOG_LIMIT:
            log.replace(log.with_suffix(".log.1"))
    except OSError:
        pass


def setup_logging() -> Path | None:
    """Start the log, and give a windowed build somewhere to write.

    Returns the log path, or ``None`` when logging is off — which is the case
    for a plain source run, where a console is right there and one stray file
    per experiment would just be litter.  Setting :data:`paths.HOME_ENV` turns
    it on regardless, which is how the packaged smoke test reads the result.
    """
    if not (paths.frozen() or os.environ.get(paths.HOME_ENV)):
        return None
    log = paths.data_dir() / "app.log"
    _rotate(log)
    try:
        stream = open(log, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    # Written to the stream directly rather than through ``print``: in a source
    # run stdout is still the console, so printing would put the line somewhere
    # other than the log this function just opened — and a log with nothing in
    # it is worse than no log, because it looks like the app failed.
    stream.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  Starting {APP_NAME} "
                 f"v{__version__}  [{'frozen' if paths.frozen() else 'source'}"
                 f", python {sys.version.split()[0]}, pid {os.getpid()}, "
                 f"{APP_TAGLINE}]\n")
    if paths.frozen():
        # A windowed build has no console at all; give anything that writes to
        # stdout/stderr somewhere to go instead of an AttributeError.
        sys.stdout = stream
        sys.stderr = stream
    return log


def install_excepthook(log: Path | None) -> None:
    """Turn an unhandled exception into a file and a dialog, never into silence."""

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        where = ""
        if log is not None:
            crash = log.with_name("crash.log")
            try:
                with open(crash, "a", encoding="utf-8") as fh:
                    fh.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} "
                             f"{APP_NAME} v{__version__} =====\n{text}")
                where = str(crash)
            except OSError:
                pass
        try:
            sys.stderr.write(text)
        except Exception:                          # noqa: BLE001
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            QApplication.instance() or QApplication([])
            QMessageBox.critical(
                None, tr("出错了"),
                f"{exc_type.__name__}: {exc}\n\n"
                + (tr("完整信息已写入：\n{where}", where=where) if where else
                   tr("完整信息已写入控制台")))
        except Exception:                          # noqa: BLE001
            pass

    sys.excepthook = hook
    try:
        import threading
        threading.excepthook = lambda a: hook(a.exc_type, a.exc_value,
                                              a.exc_traceback)
    except Exception:                              # noqa: BLE001
        pass


def set_app_id() -> None:
    """Give the process an explicit AppUserModelID.

    Without one Windows groups taskbar buttons by executable path, so the same
    tool launched from two folders, or from a shortcut with its own icon, can
    end up in separate groups with the generic Python icon on one of them.
    With one, the taskbar takes the icon and the name from the EXE itself.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Warno.NdfbinEditor")
    except Exception:                              # noqa: BLE001
        pass


def main() -> int:
    log = setup_logging()
    install_excepthook(log)
    set_app_id()
    from ndfbin_decks.ndfedit.gui import main as gui_main
    # Logged on purpose: importing the GUI pulls in the whole chain — the
    # schema, the reference index, the ammo page, the mod picker, and the codec
    # with its zstandard / lz4 extensions.  Reaching this line in a packaged
    # build is proof that the frozen module graph resolved, which the "process
    # is still running" assertion alone cannot tell you (it would be running a
    # dialog saying the import failed).
    if log is not None:
        print("GUI loaded — every import resolved")
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
