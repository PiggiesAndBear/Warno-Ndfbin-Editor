"""Read and edit WARNO ``.ndfbin`` binaries — Weapon, Unit, Deck and the rest.

The editor that ships from this checkout is :mod:`ndfbin_decks.ndfedit`; the
older deck-only app under :mod:`ndfbin_decks.app` is still present but is not
what :file:`build_exe.py` packages.
"""

#: Single source of truth for the release number.  ``build_exe.py`` greps this
#: line to name the artefact and ``main.py`` logs it at startup, so the file
#: name, the version resource and the runtime report cannot disagree.
#:
#: 0.1 is the first release of the Ndfbin editor under its own name.  The
#: numbering restarts here on purpose: the 0.4 that preceded it belonged to the
#: deck-only editor, and carrying it over would imply a history this build does
#: not have.
#:
#: 0.2 adds the second dedicated page (Army General 牌组属性) and the class-name
#: filter.  The AG page was validated in game between the two releases, which is
#: why it lands as a version rather than an increment on 0.1's notes.
__version__ = "0.2"

#: The product, as it appears on the EXE, in its Properties dialog and in the
#: log header.  Deliberately ASCII: this string becomes a file name that users
#: will type, quote in a launcher and pass through shells.
APP_NAME = "Warno Ndfbin Editor"

#: What the window itself is called — the title bar and the taskbar hint.
#: Chinese, because that is the language the interface is written in.
APP_TITLE = "Warno NDFBIN 编辑器"

#: What the Properties sheet shows under Comments: the one place a tagline can
#: explain itself without a hover to carry it.
APP_TAGLINE = "Edit Any Ndfbin In Any Mod"
