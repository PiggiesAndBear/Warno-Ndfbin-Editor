"""Load Qt's own translations for the widgets we did not write.

``QDialogButtonBox``'s Ok/Cancel, the file dialog, the colour dialog: none of
them are in our tables, and none of them should be — Qt already ships every one
of them translated, in the ``qt_<lang>.qm`` files next to its libraries.  Half
a translated dialog looks worse than none.

Loading them once at startup is enough.  Qt looks the translation up whenever a
standard widget builds its text, so widgets created after the load come out
translated; a widget already on screen keeps what it had, which is why the
switcher retranslates the window afterwards.
"""

from __future__ import annotations

from . import SOURCE_LANG

#: Qt's own codes.  Chinese uses the same ``zh_CN`` spelling; English is the
#: Qt base and has no file of its own, so it is simply not loaded.
QT_CODES = {"zh_CN": "qt_zh_CN", "en": None}


def install(app, code: str) -> bool:
    """Install Qt's translation for ``code`` onto ``app``.

    Returns whether anything was actually loaded, so a caller can say so
    instead of pretending a missing ``.qm`` is a success.  The translator is
    kept alive by being parented to ``app``; letting it be collected would
    silently revert every dialog to English.
    """
    from PySide6.QtCore import QLibraryInfo, QTranslator

    if code == SOURCE_LANG:
        return False
    base = QT_CODES.get(code, None)
    if not base:
        return False
    where = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    # One translator per code, held on the application: install, do not replace.
    holders = getattr(app, "_qt_translators", None)
    if holders is None:
        holders = {}
        app._qt_translators = holders                # noqa: W0212 - kept alive
    if base in holders:
        return True
    translator = QTranslator(app)
    if not translator.load(base, where):
        return False
    app.installTranslator(translator)
    holders[base] = translator
    return True
