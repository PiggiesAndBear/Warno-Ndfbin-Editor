# -*- coding: utf-8 -*-
"""Headless test for the GUI and the two gates that protect the file.

Builds the real ``MainWindow`` against the real ``Test`` mod, drives every
action through its code path with the modal dialogs stubbed out, and checks the
graph afterwards.  This catches wiring errors — missing imports, wrong ids,
buttons that do nothing — without a human staring at a window.

Two properties are checked because they are the ones that silently corrupt a
mod rather than crash:

* the equipment picker may only offer packs **this file** contains (no
  cross-mod references the ``IMPR`` trie could not resolve);
* and only packs an **Army General deck** fields — the 2483 PvP-only packs in
  the same file share the object type but have never been through the AG code
  path.

Run with a Qt offscreen platform:  ``QT_QPA_PLATFORM=offscreen``
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog
from PySide6.QtTest import QTest

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(label)
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    # Guarded up front: without the Test mod the window opens but nothing is
    # selectable, and every later assertion would fail on a None session —
    # which reads as "the GUI is broken" rather than "install the mod".
    # Called module-qualified because this function assigns a local ``deck``.
    _env.deck(_env.TEST)
    from ndfbin_decks.app.gui import MainWindow

    app = QApplication.instance() or QApplication([])

    # stub the modal dialogs so the test never blocks
    shown: list[tuple[str, str]] = []
    def _record(kind):
        def hook(*a, **k):
            shown.append((kind, str(a[2]) if len(a) > 2 else ""))
        return hook
    QMessageBox.information = _record("info")
    QMessageBox.warning = _record("warn")
    QMessageBox.critical = _record("error")

    # Modal pickers block forever under offscreen; drive them by patching
    # ``exec`` and having the dialog answer with the current pack, which makes
    # the edit a legal no-op.
    import ndfbin_decks.app.gui as gui_mod
    from PySide6.QtWidgets import QDialog

    class _AutoPick:
        calls = 0
        chosen_key = None

        def __init__(self, session, current=None, parent=None):
            self.session = session
            self._current = current

        def setWindowTitle(self, *_a):
            pass

        def exec(self):
            _AutoPick.calls += 1
            _AutoPick.chosen_key = self._current
            return QDialog.Accepted

        def chosen(self):
            return _AutoPick.chosen_key

    gui_mod.SwapDialog = _AutoPick

    print("=== 1. window builds and lists mods from both deployment roots ===")
    win = MainWindow()
    check("mod combo has a placeholder plus the mods",
          win.mod_box.count() >= 3, f"{win.mod_box.count()} items")
    check("placeholder holds no mod name",
          win.mod_box.itemData(0) is None)
    test_idx = win.mod_box.findData("Test")
    check("Test is selectable", test_idx > 0, str(test_idx))
    broken = [i for i in range(win.mod_box.count())
              if not win.mod_box.model().item(i).isEnabled()]
    check("a mod without Deck.ndfbin is unselectable",
          len(broken) >= 1 and all(win.mod_box.itemData(i) is None
                                   for i in broken), f"{broken}")

    # A workshop subscription is loaded straight from the Steam folder — it is
    # never copied into the Saved Games workspace — so without a second scan
    # root the editor can only ever see the user's own mods.
    from ndfbin_decks.io import modlocator
    ws_mods = [m for m in modlocator.discover()
               if m.source == modlocator.SOURCE_WORKSHOP]
    loadable = []
    for m in ws_mods:
        try:
            if Path(m.deck_bin).is_file():
                loadable.append(m)
        except Exception:                                  # noqa: BLE001
            pass
    labels = [win.mod_box.itemText(i) for i in range(win.mod_box.count())]
    if ws_mods:
        check("workshop content is scanned too",
              len(ws_mods) >= 1, f"{len(ws_mods)} workshop mods")
        check("every loadable workshop mod is offered",
              all(any(m.label in lab for lab in labels) for m in loadable),
              f"{len(loadable)} loadable")
        check("the source is visible in the label",
              any("创意工坊" in lab for lab in labels),
              str([lab for lab in labels if "创意工坊" in lab][:2]))
    else:
        print("  (no workshop content on this machine — skipping)")

    print()
    print("=== 1b. 「打开 mod 文件夹」 is the fallback for anything else ===")
    import shutil
    import tempfile
    from PySide6.QtWidgets import QFileDialog
    test_mod = next((m for m in modlocator.discover() if m.name == "Test"
                     and Path(m.deck_bin).is_file()), None)
    real_picker = QFileDialog.getExistingDirectory
    before_n = win.mod_box.count()
    tmp_root = None
    try:
        QFileDialog.getExistingDirectory = lambda *a, **k: ""
        win.on_open_folder()
        app.processEvents()
        check("cancelling changes nothing", win.mod_box.count() == before_n)

        QFileDialog.getExistingDirectory = (
            lambda *a, **k: r"D:\definitely\not\here")
        win.on_open_folder()
        app.processEvents()
        check("a folder with no mod says so",
              any("里面没有 mod" in m for _, m in shown[-1:]),
              str(shown[-1]) if shown else "no dialog")

        if test_mod is not None:
            tmp_root = Path(tempfile.mkdtemp())
            hand = tmp_root / "HandPickedMod"
            (hand / "Gen" / "NDF" / "GFX").mkdir(parents=True)
            shutil.copy(Path(test_mod.deck_bin),
                        hand / "Gen" / "NDF" / "GFX" / "Deck.ndfbin")
            QFileDialog.getExistingDirectory = lambda *a, **k: str(hand)
            win.on_open_folder()
            app.processEvents()
            check("a hand-picked mod folder is added",
                  win.mod_box.count() == before_n + 1,
                  f"{before_n} -> {win.mod_box.count()}")
            picked = win.mod_box.itemData(win.mod_box.count() - 1)
            check("its item data is the folder name",
                  picked == "HandPickedMod", str(picked))
            check("it is selectable", win.mod_box.model()
                  .item(win.mod_box.count() - 1).isEnabled())
            check("and it opens straight away",
                  win.session is not None
                  and "HandPickedMod" in str(win.session.path),
                  str(getattr(win.session, "path", None)))
        else:
            print("  (no local mod to copy — skipping the add check)")
    finally:
        QFileDialog.getExistingDirectory = real_picker
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)
        win.mod_box.setCurrentIndex(0)
        app.processEvents()

    print()
    print("=== 2. opening a mod builds the 师/营/连/排 tree ===")
    win.mod_box.setCurrentIndex(test_idx)
    app.processEvents()
    s = win.session
    check("session loaded", s is not None)
    # 233 divisions plus the "全部师" entry that index 0 now carries.
    check("233 divisions discovered", win.div_box.count() == 234,
          str(win.div_box.count()))
    check("index 0 is the all-divisions entry, not a division",
          win.div_box.itemData(0) is None, str(win.div_box.itemData(0)))
    check("2208 battalions in scope", len(s.battalions()) == 2208,
          str(len(s.battalions())))

    # The shipped file's catalogue numbers are pinned *here*, before anything
    # edits it.  Section 11 checks the same thing several hundred edits later,
    # which was always a latent fragility: a swap can bring a pack the AG decks
    # never used into reach, and then "2690" is no longer a fact about the file
    # in front of it.
    cat0 = s.get_catalogue()
    base_ag, base_pvp = len(cat0), cat0.stats.skirmish_only
    base_total = cat0.stats.total_packs
    check("the AG gate reports the shipped file's numbers",
          (base_ag, base_pvp) == (2690, 2483), f"{base_ag} / {base_pvp}")
    check("and puts every pack in one of its three buckets",
          base_ag + base_pvp + cat0.stats.unreferenced == base_total,
          f"{base_ag} + {base_pvp} + {cat0.stats.unreferenced} "
          f"vs {base_total}")

    root = win.tree.topLevelItem(0)
    check("one root, labelled 师Div", root.text(0).startswith("师Div"),
          root.text(0))
    check("root role is a division",
          root.data(0, 256).kind == "div", str(root.data(0, 256)))
    bn = root.child(0)
    check("battalion child labelled 营Bn", bn.text(0).startswith("营Bn"),
          bn.text(0))
    check("battalion role carries the deck id",
          bn.data(0, 256).kind == "bn"
          and bn.data(0, 256).deck_id is not None)
    co = bn.child(0)
    check("company child labelled 连Co", co.text(0).startswith("连Co"),
          co.text(0))
    pl = co.child(0)
    check("platoon child labelled 排Pl", pl.text(0).startswith("排Pl"),
          pl.text(0))
    check("entry children exist", pl.childCount() >= 1,
          pl.child(0).text(0) if pl.childCount() else "")

    # The composition fallback is what makes an unlabelled mod navigable: Test
    # ships 6 dictionary entries for 42258 platoons.
    check("company shows what it fields, not a raw hash",
          "⟨未知" not in co.text(0), co.text(0)[:60])

    print()
    print("=== 3. the division filter narrows the battalions ===")
    before = root.childCount()
    # Index 0 is the "全部师" entry; a division is index 1 and up, and the test
    # must not park the tree on all 2208 battalions.
    win.div_box.setCurrentIndex(2)
    app.processEvents()
    after = win.tree.topLevelItem(0).childCount()
    check("switching division changes the battalion set",
          0 < after < 200 and after != before, f"{before} -> {after}")
    win.div_box.setCurrentIndex(1)
    app.processEvents()

    print()
    print("=== 3b. typing in the 师 box narrows its list ===")
    check("the division combo is searchable", win.div_box.isEditable())
    full = win.div_box.matches()
    check("all rows listed before typing", full == win.div_box.count(),
          f"{full}")
    check("there is an inline completer for it",
          win.div_box.completer() is not None)

    # Type it the way a user does — real key events into the line edit.  This
    # is the path that was broken: ``_on_typed`` used to move ``currentIndex``,
    # which rewrote the edit text, so the next character appended to the old
    # label and the filter matched nothing.
    before_tree = win.tree.topLevelItem(0).text(0)
    check("the box starts empty, with the choice shown as a hint",
          win.div_box.lineEdit().text() == ""
          and "RFA_TK_Nord" in win.div_box.lineEdit().placeholderText(),
          f"{win.div_box.lineEdit().text()!r} / "
          f"{win.div_box.lineEdit().placeholderText()[:24]!r}")
    win.div_box.setFocus()
    app.processEvents()
    for ch in "SOV":
        QTest.keyClicks(win.div_box.lineEdit(), ch)
        app.processEvents()
    check("the typed text is exactly what was typed",
          win.div_box.lineEdit().text() == "SOV",
          repr(win.div_box.lineEdit().text()))
    narrowed = win.div_box.matches()
    check("typing narrows the list", 0 < narrowed < full,
          f"{full} -> {narrowed}")
    check("every surviving row contains the needle",
          all("SOV" in win.div_box.itemText(i).upper()
              for i in range(win.div_box.count())),
          str([win.div_box.itemText(i)[:22]
               for i in range(min(3, win.div_box.count()))]))
    check("typing alone does not move the tree — the user has not chosen yet",
          win.tree.topLevelItem(0).text(0) == before_tree,
          win.tree.topLevelItem(0).text(0)[:40])

    # Enter is the commit, and it must work: NoInsert on its own would make it
    # silently do nothing, which reads as the box being dead.
    win.div_box.lineEdit().returnPressed.emit()
    app.processEvents()
    check("Enter commits the first surviving row",
          "SOV" in win.div_box.current_label().upper(),
          win.div_box.current_label())
    check("the tree follows the committed choice",
          "SOV" in win.tree.topLevelItem(0).text(0).upper(),
          win.tree.topLevelItem(0).text(0)[:40])
    check("and the filter is lifted so the list is whole again",
          win.div_box.matches() == full, str(win.div_box.matches()))
    check("the box is empty again, hinting the new choice",
          win.div_box.lineEdit().text() == ""
          and "SOV" in win.div_box.lineEdit().placeholderText(),
          f"{win.div_box.lineEdit().text()!r} / "
          f"{win.div_box.lineEdit().placeholderText()[:24]!r}")

    committed = win.tree.topLevelItem(0).text(0)
    committed_label = win.div_box.current_label()
    win.div_box.lineEdit().setText("zzz-no-such-division")
    win.div_box._on_typed("zzz-no-such-division")
    app.processEvents()
    check("a filter matching nothing shows nothing",
          win.div_box.matches() == 0)
    check("but does not widen the tree to every battalion",
          win.tree.topLevelItem(0).text(0) == committed,
          win.tree.topLevelItem(0).text(0)[:40])
    check("and the last real choice survives", win.div_box.current_path(),
          str(win.div_box.current_path()))
    ev = __import__("PySide6.QtGui", fromlist=["QFocusEvent"]).QFocusEvent(
        __import__("PySide6.QtCore", fromlist=["QEvent"]).QEvent.FocusOut)
    win.div_box.focusOutEvent(ev)
    app.processEvents()
    check("abandoning the search snaps back to the committed row",
          win.div_box.matches() == full
          and win.div_box.lineEdit().text() == ""
          and win.div_box.current_label() == committed_label,
          f"{win.div_box.matches()} / "
          f"{win.div_box.current_label()[:24]!r} / "
          f"{win.div_box.lineEdit().placeholderText()[:24]!r}")

    win.div_box.lineEdit().setText("")
    win.div_box._on_typed("")
    app.processEvents()
    check("clearing restores the full list",
          win.div_box.matches() == full, str(win.div_box.matches()))

    print()
    print("=== 3c. the 国家 picker re-scopes the 师 list ===")
    from ndfbin_decks.io import divisions as divmod
    countries = s.countries()
    check("every division carries a country",
          all(d.country for d in s.divisions()),
          str([d.label for d in s.divisions() if not d.country][:3]))
    check("the labels group into a readable number of countries",
          3 <= len(countries) <= 30, f"{len(countries)} countries")
    check("SOV is one of them and is the biggest contingent",
          countries[0].code == "SOV", countries[0].code)
    check("Germany is RDA in the division exports, DDR in the units",
          divmod.country_code("$/GFX/Division/Descriptor_Deck_Division_RDA_x_solo")
          == "RDA"
          and "DDR" in divmod.country_label("RDA", 18),
          divmod.country_label("RDA", 18))

    check("the country combo lists all-plus-each",
          win.country_box.count() == len(countries) + 1,
          f"{win.country_box.count()} vs {len(countries)} + 1")
    check("and opens on 全部国家", win.country_box.currentData() is None,
          str(win.country_box.currentData()))
    check("with every division in the 师 list",
          win.div_box.count() == win.div_box.matches() == 234,
          str(win.div_box.count()))

    wide = win.div_box.count()
    sov_idx = win.country_box.findData("SOV")
    win.country_box.setCurrentIndex(sov_idx)
    app.processEvents()
    check("choosing SOV narrows the 师 list to that country",
          win.div_box.count() == len(countries[0].divisions) + 1 < wide,
          f"{wide} -> {win.div_box.count()}")
    check("every division offered is Soviet",
          all(r.startswith("SOV_")
              for r in (win.div_box.itemText(i).split("  (")[0]
                        for i in range(1, win.div_box.count()))),
          str([win.div_box.itemText(i)[:18]
               for i in range(1, min(4, win.div_box.count()))]))
    check("it still opens on a division, not on all 515 battalions",
          win.div_box.currentIndex() == 1,
          str(win.div_box.currentIndex()))
    check("the tree shows a Soviet division",
          win.tree.topLevelItem(0).text(0).startswith("师Div SOV_"),
          win.tree.topLevelItem(0).text(0)[:34])

    win.div_box.setCurrentIndex(0)
    app.processEvents()
    check("the 全部师 entry selects the whole country",
          win.div_box.current_path() is None)
    root = win.tree.topLevelItem(0)
    check("and the tree covers every Soviet battalion",
          root.childCount() == len(countries[0].battalions),
          f"{root.childCount()} vs {len(countries[0].battalions)}")
    check("the root says which country it is, in both spellings",
          "SOV" in root.text(0) and "苏联" in root.text(0), root.text(0)[:44])

    # Typing narrows *within* the country — the two filters compose.
    win.div_box.setFocus()
    app.processEvents()
    for ch in "GTkD":
        QTest.keyClicks(win.div_box.lineEdit(), ch)
        app.processEvents()
    check("typing narrows within the country too",
          0 < win.div_box.matches() < len(countries[0].divisions),
          f"{win.div_box.matches()} of {len(countries[0].divisions)}")
    win.div_box.lineEdit().returnPressed.emit()
    app.processEvents()
    check("and the committed division is still Soviet",
          win.div_box.current_label().startswith("SOV_"),
          win.div_box.current_label())

    win.country_box.setCurrentIndex(0)
    app.processEvents()
    check("going back to 全部国家 restores the 师 list",
          win.div_box.count() == wide, str(win.div_box.count()))

    print()
    print("=== 3d. the filter chain is pinned: no edit may move it ===")
    # Reported symptom: pick SOV, pick 20GMRD, batch replace -- and the tree is
    # back on 27GMRD, the country's first division.  The 师 box had moved on
    # its own: choosing a row from the popup list moved ``currentIndex`` but
    # not ``_last_data``, so the next focus-out (the modal replace dialog
    # taking focus) restored the row ``_fill_division_box`` had defaulted to.
    # The tree only revealed the move at the next refresh — which an edit is.
    from PySide6.QtCore import QEvent as _QEvent
    from PySide6.QtGui import QFocusEvent as _QFocus

    def scope_now():
        return (win.country_box.current_path(), win.div_box.current_path(),
                win.bn_search.text())

    win.country_box.setCurrentIndex(sov_idx)
    app.processEvents()
    r20 = next(r for r in range(win.div_box.count())
               if "SOV_20GMRD" in win.div_box.itemText(r))
    win.div_box.setCurrentIndex(r20)          # the popup-list path
    app.processEvents()
    committed = win.div_box.current_path()
    check("picking from the popup list counts as the committed choice",
          win.div_box._last_data == committed, str(win.div_box._last_data))
    win.div_box.focusOutEvent(_QFocus(_QEvent.FocusOut))
    app.processEvents()
    check("focus-out on an untouched box moves nothing",
          win.div_box.current_path() == committed, win.div_box.current_label())
    check("and the placeholder still names it",
          "SOV_20GMRD" in win.div_box.lineEdit().placeholderText(),
          win.div_box.lineEdit().placeholderText()[:22])

    root20 = win.tree.topLevelItem(0)
    bn20 = root20.child(0)
    win.tree.setCurrentItem(bn20)
    app.processEvents()
    did20 = bn20.data(0, 256).deck_id

    def act(label, fn):
        """Run one edit and require the filter chain and tree to survive it."""
        filters = scope_now()
        tree = win.tree.topLevelItem(0).text(0)
        fn()
        app.processEvents()
        check(f"{label}: the filters did not move", scope_now() == filters,
              f"{filters[1]} -> {scope_now()[1]}")
        check(f"{label}: the tree stayed on the same 师",
              win.tree.topLevelItem(0).text(0) == tree,
              win.tree.topLevelItem(0).text(0)[:40])

    check("the tree is standing on the picked division, not the default one",
          "SOV_20GMRD" in root20.text(0), root20.text(0)[:40])
    n_bn20 = root20.childCount()

    # 1. 改件数 — the first entry of the first platoon of that battalion.
    pl20 = win._node_for(did20, 0, 0)
    check("the battalion has a platoon to aim at", pl20 is not None)
    if pl20 is not None:
        win.tree.setCurrentItem(pl20)
        app.processEvents()
        QInputDialog.getInt = staticmethod(lambda *a, **k: (2, True))
        act("改件数", win.on_set_count)

    # 2. 换装备 — through the real dialog path, with a genuinely different pack
    #    so the edit is not a no-op.
    from ndfbin_decks.app.catalog import PackCatalogue        # noqa: E402,F401
    cat20 = s.get_catalogue()
    pool20 = s.retiler.pool(did20)
    from_pack = next((p for p in pool20 if cat20.by_id(p) is not None), None)
    other = None
    if from_pack is not None:
        cur_unit = s.packs.pack_key(from_pack).unit
        other = next((c for c in cat20.items if c.unit != cur_unit), None)

    class _SwapTo:
        key = None

        def __init__(self, session, current=None, parent=None):
            pass

        def setWindowTitle(self, *_a):
            pass

        def exec(self):
            return QDialog.Accepted

        def chosen(self):
            return _SwapTo.key

    if other is not None and win.table.rowCount():
        _SwapTo.key = other.key
        gui_mod.SwapDialog = _SwapTo
        win.table.select_entry(0, 0, 0)
        act("换装备", win.on_swap)
        gui_mod.SwapDialog = _AutoPick
    else:
        print("  (no distinct target pack available — skipping 换装备)")

    # 3. 插入 / 删除条目
    pl20 = win._node_for(did20, 0, 0)
    if pl20 is not None:
        win.tree.setCurrentItem(pl20)
        app.processEvents()
        QInputDialog.getInt = staticmethod(lambda *a, **k: (1, True))
        act("插入条目", win.on_insert)
        act("删除条目", win.on_remove)

    # 4. 批量替换 — the action the user was performing when it jumped.
    import ndfbin_decks.app.gui as _gm
    from ndfbin_decks.edit.bulk import Scope as _Scope
    import ndfbin_decks.io.divisions as _dvm

    class _FakeReplace:
        payload = None

        def __init__(self, session, scope, display, parent=None):
            self._scope = scope

        def exec(self):
            return QDialog.Accepted

        def result(self):
            return _FakeReplace.payload

    bn20 = win._node_for(did20)
    if bn20 is not None:
        win.tree.setCurrentItem(bn20)
        app.processEvents()
        role20 = win._current()
        pool20 = s.retiler.pool(did20)
        from_pack = next((p for p in pool20 if cat20.by_id(p) is not None), None)
        if from_pack is not None and other is not None:
            _FakeReplace.payload = (
                _Scope(kind="division", division=role20.division,
                       deck_id=did20, ci=role20.ci, si=role20.si,
                       country=_dvm.country_code(role20.division)),
                from_pack, other.key)
            real_replace = _gm.ReplaceDialog
            try:
                _gm.ReplaceDialog = _FakeReplace
                act("批量替换（本师）", win.on_replace)
            finally:
                _gm.ReplaceDialog = real_replace
            check("the batch replace really did run",
                  s.dirty and any("批量替换" in line for line in s.log),
                  str(s.log[-1:]))
        else:
            print("  (no usable pack pair — skipping 批量替换)")

    check("the battalion list is the same set of battalions",
          win.tree.topLevelItem(0).childCount() == n_bn20,
          f"{n_bn20} -> {win.tree.topLevelItem(0).childCount()}")
    check("and the division in scope is still 20GMRD",
          "SOV_20GMRD" in str(win.div_box.current_path()),
          str(win.div_box.current_path()))

    # Back to a neutral filter chain for the sections that follow.
    win.country_box.setCurrentIndex(0)
    app.processEvents()

    print()
    print("=== 4. battalion search narrows the tree ===")
    bn = win.tree.topLevelItem(0).child(0)
    token = bn.text(0).replace("营Bn ", "")[:6]
    win.bn_search.setText(token)
    app.processEvents()
    hit = win.tree.topLevelItem(0).childCount()
    check("search leaves fewer battalions", 0 < hit <= before,
          f"'{token}' -> {hit} of {before}")
    win.bn_search.setText("zzz-不可能存在-zzz")
    app.processEvents()
    check("a hopeless search empties the tree",
          win.tree.topLevelItem(0).childCount() == 0)
    win.bn_search.setText("")
    app.processEvents()

    print()
    print("=== 5. the table lists entries, not raw slots ===")
    bn = win.tree.topLevelItem(0).child(0)
    win.tree.setCurrentItem(bn)
    app.processEvents()
    deck0 = win._deck_of(bn.data(0, 256))
    n_windows = sum(len(p.windows) for c in deck0.companies for p in c.platoons)
    n_slots = len(s.retiler.pool(deck0.deck_object_id))
    check("one row per entry (window)", win.table.rowCount() == n_windows,
          f"{win.table.rowCount()} rows vs {n_windows} windows "
          f"({n_slots} slots)")
    check("entries are fewer than slots, so a window cannot be "
          "edited piecewise", win.table.rowCount() < n_slots,
          f"{win.table.rowCount()} rows vs {n_slots} slots")
    check("first column names the entry", win.table.item(0, 0).text() == "#1",
          win.table.item(0, 0).text())
    check("unit name resolved", bool(win.table.item(0, 3).text()),
          win.table.item(0, 3).text())
    headers = [win.table.horizontalHeaderItem(i).text()
               for i in range(win.table.columnCount())]
    check("headers put the entry first and the slot range as info",
          headers[0] == "条目" and "槽位" in headers,
          str(headers))
    check("selecting a row yields an entry address",
          win.table.select_entry(0, 0, 0)
          and win.table.selected_entry() == (0, 0, 0),
          str(win.table.selected_entry()))

    print()
    print("=== 6. selecting a platoon highlights its entries ===")
    pl = bn.child(0).child(0)
    win.tree.setCurrentItem(pl)
    app.processEvents()
    lit = [r for r in range(win.table.rowCount())
           if win.table.item(r, 0).background().color().name() != "#000000"]
    check("some rows are highlighted", len(lit) >= 1, f"{len(lit)} rows")
    role_pl = pl.data(0, 256)
    platoon = deck0.companies[role_pl.ci].platoons[role_pl.si]
    check("exactly that platoon's entries are lit",
          len(lit) == len(platoon.windows),
          f"{len(lit)} lit vs {len(platoon.windows)} windows")

    print()
    print("=== 7. set_count path works end to end ===")
    role = pl.data(0, 256)
    did = role.deck_id
    before_pool = len(s.retiler.pool(did))
    ci, si = role.ci, role.si
    deck = next(b for b in s.battalions() if b.deck_object_id == did)
    cur = deck.companies[ci].platoons[si].windows[0].count
    QInputDialog.getInt = staticmethod(lambda *a, **k: (cur + 2, True))
    win.tree.setCurrentItem(pl)
    win.on_set_count()
    after_pool = len(s.retiler.pool(did))
    check("pool grew by exactly the added units", after_pool == before_pool + 2,
          f"{before_pool} -> {after_pool}")
    deck = next(b for b in s.battalions() if b.deck_object_id == did)
    check("W1 (ΣNumber == len(pool)) still holds",
          after_pool == deck.window_sum, f"pool={after_pool} sum={deck.window_sum}")
    check("session marked dirty", s.dirty)

    print()
    print("=== 8. insert / remove paths ===")

    # Every edit rebuilds the tree, so the node must be re-located each time
    # rather than reused — a stale QTreeWidgetItem is a hard crash.
    def node(did, ci=-1, si=-1):
        n = win._node_for(did, ci, si)
        assert n is not None, f"node {did}/{ci}/{si} vanished after the edit"
        return n

    check("_reload keeps the selection",
          win._current() is not None
          and win._current().deck_id == did,
          str(win._current()))

    n_before = len(deck.companies[ci].platoons[si].windows)
    QInputDialog.getInt = staticmethod(lambda *a, **k: (1, True))
    pl = node(did, ci, si)
    win.tree.setCurrentItem(pl)
    win.on_insert()
    deck = next(b for b in s.battalions() if b.deck_object_id == did)
    n_after = len(deck.companies[ci].platoons[si].windows)
    check("insert added a window", n_after == n_before + 1,
          f"{n_before} -> {n_after}")
    win.on_remove()
    deck = next(b for b in s.battalions() if b.deck_object_id == did)
    n_final = len(deck.companies[ci].platoons[si].windows)
    check("remove took it back", n_final == n_before, f"{n_after} -> {n_final}")
    check("W1 holds after insert+remove",
          deck.window_sum == len(s.retiler.pool(did)))

    print()
    print("=== 9. batch replace: scope widens, counts never move ===")
    from ndfbin_decks.edit.bulk import ScopedReplace, Scope
    b0 = next(b for b in s.battalions() if b.deck_object_id == did)
    cat = s.get_catalogue()
    # a pack this battalion actually fields
    pool0 = s.retiler.pool(did)
    from_pack = next(p for p in pool0
                     if cat.by_id(p) is not None)
    target = next(c for c in cat.items
                  if c.pack_object_id != from_pack
                  and c.unit != s.packs.pack_key(from_pack).unit)

    def scope_of(kind):
        return Scope(kind=kind, division=b0.division, deck_id=did,
                     ci=ci, si=si)

    surveys = {}
    for kind in ("platoon", "company", "battalion", "division"):
        surveys[kind] = s.scoped_replace(
            scope_of(kind), from_pack).survey()
    print("       " + " | ".join(
        f"{k[:4]}:{surveys[k].entries}条/{surveys[k].units}件"
        for k in surveys))
    check("scope widens monotonically",
          surveys["platoon"].entries <= surveys["company"].entries
          <= surveys["battalion"].entries <= surveys["division"].entries,
          str([surveys[k].entries for k in
               ("platoon", "company", "battalion", "division")]))
    check("division scope reaches past one battalion",
          len(surveys["division"].decks) >= 1)

    total_before = sum(len(s.retiler.pool(b.deck_object_id))
                       for b in s.battalions())
    sum_before = sum(b.window_sum for b in s.battalions())
    res = s.scoped_replace(scope_of("division"), from_pack).apply(target.key)
    check("replace applied", res.ok, res.note or res.summary())
    s.note_change(res, "批量替换")
    total_after = sum(len(s.retiler.pool(b.deck_object_id))
                      for b in s.battalions())
    sum_after = sum(b.window_sum for b in s.battalions())
    check("pool size unchanged by a replace",
          total_before == total_after, f"{total_before} -> {total_after}")
    check("Σ件数 unchanged by a replace",
          sum_before == sum_after, f"{sum_before} -> {sum_after}")
    check("W1 still holds file-wide", total_after == sum_after)
    v = s.validate()
    check("no hard errors after a batch replace", not v.errors,
          f"{len(v.errors)} errors")

    print()
    print("=== 10. the replace dialog lists what the scope fields ===")
    import ndfbin_decks.app.replace_dialog as rmod
    rmod.SwapDialog = _AutoPick
    dlg = rmod.ReplaceDialog(s, scope_of("battalion"), s.display, win)
    dlg.show()
    app.processEvents()
    check("dialog offers every level including 国家",
          [b.text() for b in dlg._level_btns.values()]
          == ["本排 Pl", "本连 Co", "本营 Bn", "本师 Div", "本国 Nat"],
          str([b.text() for b in dlg._level_btns.values()]))
    check("the from list is populated", dlg.from_list.count() > 0,
          f"{dlg.from_list.count()} kinds")
    n_bn = dlg.from_list.count()
    dlg._apply_level("division")
    app.processEvents()
    check("widening to the division shows more equipment",
          dlg.from_list.count() >= n_bn, f"{n_bn} -> {dlg.from_list.count()}")

    # The country level: one press covers every division of the same country.
    # The Scope carries the country, exactly as ``on_replace`` builds it — a
    # scope without one has nothing to widen to, and the button says so.
    from ndfbin_decks.io.divisions import country_code
    from ndfbin_decks.edit.bulk import Scope as _S
    role_c = win._current()
    code = country_code(role_c.division)
    check("the starting battalion has a country to widen to", bool(code), code)
    check("a scope built without one has the level disabled",
          not dlg._level_btns["country"].isEnabled(),
          dlg._level_btns["country"].toolTip()[:44])

    dlg_c = rmod.ReplaceDialog(
        s, _S(kind="battalion", division=role_c.division,
              deck_id=role_c.deck_id, country=code),
        s.display, win)
    country_btn = dlg_c._level_btns["country"]
    check("with one, the 本国 button is enabled", country_btn.isEnabled())
    check("and its tooltip names the country and its size",
          code in country_btn.toolTip(), country_btn.toolTip()[:60])
    n_div = dlg_c.from_list.count()
    dlg_c._apply_level("country")
    app.processEvents()
    check("widening to the country reaches at least as far as the division",
          dlg_c.from_list.count() >= n_div > 0,
          f"{n_div} -> {dlg_c.from_list.count()}")
    check("the scope label says so",
          dlg_c.scope_label.text() == "当前：本国 Nat",
          dlg_c.scope_label.text())
    check("and the country scope really is that country",
          dlg_c._scope_for("country").country == code,
          str(dlg_c._scope_for("country").country))
    sov = _S(kind="country", country="SOV")
    check("a SOV scope accepts a SOV division and refuses an RFA one",
          sov.contains("$/GFX/Division/Descriptor_Deck_Division_SOV_27GMRD_solo",
                       1, 0, 0)
          and not sov.contains(
              "$/GFX/Division/Descriptor_Deck_Division_RFA_TK_Nord_solo",
              1, 0, 0))

    print()
    print("=== 10b. many-to-one: several sources, one target ===")
    # "Make every T-72M, T-72M1 and T-72B a T-72B" is one request.  It has to
    # cost the same as a single replace — one relay per battalion, not one per
    # source — and it must not touch a window that fields something else.
    scope_div = scope_of("division")
    usage = s.scoped_replace(scope_div, -1).usage()
    check("the scope fields several kinds of equipment", len(usage) >= 3,
          f"{len(usage)} kinds")
    sources = [r[0] for r in usage[:3]]
    src_keys = {s.packs.pack_key(p) for p in sources}
    # By content, not by id: the file carries two pack objects with the same
    # signature, so "not this id" is not the same as "not this equipment".
    tgt = next(r[0] for r in usage if s.packs.pack_key(r[0]) not in src_keys)
    tgt_key = s.packs.pack_key(tgt)

    singles = [s.scoped_replace(scope_div, p).survey() for p in sources]
    many = s.scoped_replace(scope_div, sources).survey()
    union_decks = set().union(*[x.decks for x in singles])
    check("a multi-source survey is the sum of the single-source ones",
          many.entries == sum(x.entries for x in singles)
          and many.units == sum(x.units for x in singles)
          and many.decks == union_decks,
          f"{many.entries} 条 / {many.units} 件 / {len(many.decks)} 营 vs "
          f"{sum(x.entries for x in singles)} / "
          f"{sum(x.units for x in singles)} / {len(union_decks)}")
    check("and it reports how many sources it hit",
          many.sources == len(sources), str(many.sources))

    # The target among its own sources means "fold the others into it", not
    # "do nothing"; and when the target is the *only* source there is genuinely
    # nothing to do, which has to be said rather than passed off as a success.
    #
    # These run before the main replace because they write: the first one
    # really does move a pack, and the main measurement below re-reads the
    # usage list afterwards so it cannot be measured against a stale one.
    res_ex = s.scoped_replace(scope_div, [tgt, sources[0]]).apply(tgt_key)
    check("a target that is also a source is dropped, not refused",
          res_ex.ok and res_ex.target_also_a_source and res_ex.sources == 1,
          res_ex.note or res_ex.summary())
    res_same = s.scoped_replace(scope_div, [tgt]).apply(tgt_key)
    check("the target as the only source is refused with a reason",
          not res_same.ok and "相同" in (res_same.note or ""),
          res_same.note or "ok?")

    def tally(scope):
        """``pack_id -> entries`` for every window the scope covers."""
        out: dict[int, int] = {}
        for division, did2, ci2, si2, _ei, pid, _c in (
                s.scoped_replace(scope, -1)._windows()):
            if not scope.contains(division, did2, ci2, si2):
                continue
            out[pid] = out.get(pid, 0) + 1
        return out

    sources = [r[0] for r in s.scoped_replace(scope_div, -1).usage()
               if s.packs.pack_key(r[0]) != tgt_key][:3]
    before = tally(scope_div)
    moved = sum(before.get(p, 0) for p in sources)
    res = s.scoped_replace(scope_div, sources).apply(tgt_key)
    check("many-to-one applied", res.ok, res.note or res.summary())
    check("every source actually moved something", res.sources == len(sources),
          str(res.sources))
    s.note_change(res, "批量替换")
    after = tally(scope_div)
    check("no source survives in the scope",
          all(after.get(p, 0) == 0 for p in sources),
          str({p: after.get(p, 0) for p in sources}))
    # Measured on the pack the target resolved to, not on the one the picker
    # listed: a file can carry two pack objects with the same signature and
    # ensure_pack is free to pick either, so "the target" is a content, not an
    # id.  That is also why the exclusion above is compared by key.
    check("the target resolved to a pack offering that key",
          s.packs.pack_key(res.to_pack_id) == tgt_key, str(res.to_pack_id))
    gained = after.get(res.to_pack_id, 0) - before.get(res.to_pack_id, 0)
    check("the target absorbed exactly what the sources held",
          gained == moved, f"+{gained} vs {moved}")
    others = [p for p in before
              if p not in set(sources) | {tgt, res.to_pack_id}]
    check("every other pack is untouched",
          all(before[p] == after.get(p, 0) for p in others),
          str([p for p in others if before[p] != after.get(p, 0)][:5]))
    check("the window count did not move either",
          sum(before.values()) == sum(after.values()),
          f"{sum(before.values())} -> {sum(after.values())}")
    v = s.validate()
    check("no hard errors after a many-to-one replace", not v.errors,
          f"{len(v.errors)} errors")

    print("=== 10c. the replace dialog takes several sources ===")
    dlg_m = rmod.ReplaceDialog(s, scope_of("division"), s.display, win)
    dlg_m.show()
    app.processEvents()
    take = min(3, dlg_m.from_list.count())
    for r in range(take):
        dlg_m.from_list.item(r).setCheckState(Qt.Checked)
    app.processEvents()
    check("ticking several rows selects several kinds",
          dlg_m._ticked_rows() == take, str(dlg_m._ticked_rows()))
    check("and every pack object those rows stand for",
          len(dlg_m._selected()) >= take, str(dlg_m._selected()))
    check("the counter says how many are ticked",
          f"已勾选 {take}" in dlg_m.from_count.text(),
          dlg_m.from_count.text())
    check("two rows never name the same equipment",
          len({r["key"] for r in dlg_m._rows}) == len(dlg_m._rows),
          f"{len(dlg_m._rows)} rows")
    dlg_m._to_key = tgt_key
    dlg_m._refresh_summary()
    check("with several sources the button is live",
          dlg_m.ok_btn.isEnabled(), dlg_m.summary.text()[:60])
    picked = dlg_m.result()
    check("the result carries them all",
          picked is not None and picked[1] == dlg_m._selected(),
          str(picked[1] if picked else None))
    dlg_m._set_all(False)
    app.processEvents()
    check("清空 unticks everything", dlg_m._selected() == [],
          str(dlg_m._selected()))
    # The buttons have to record the ticks themselves: the itemChanged signal
    # is muted while they repaint, so a 全选 that only set the check state
    # would leave a fully-ticked list reporting "已勾选 0 种".
    dlg_m._set_all(True)
    app.processEvents()
    check("全选 ticks and records every row",
          dlg_m._ticked_rows() == dlg_m.from_list.count(),
          f"{dlg_m._ticked_rows()} of {dlg_m.from_list.count()}")
    dlg_m._set_all(False)
    app.processEvents()

    # Target among the ticked sources: the dialog says so and waits, rather
    # than offering to replace a pack with itself.
    own = dlg_m._rows[0]["ids"][0]
    dlg_m._checked = set(dlg_m._rows[0]["ids"])
    dlg_m._to_key = s.packs.pack_key(own)
    dlg_m._refresh_summary()
    check("the target ticked alone disables the button",
          not dlg_m.ok_btn.isEnabled(), dlg_m.summary.text()[:50])
    dlg_m._checked = set(dlg_m._rows[0]["ids"]) | set(dlg_m._rows[1]["ids"])
    dlg_m._refresh_summary()
    check("with a second source it says the target was excluded",
          dlg_m.ok_btn.isEnabled() and "剔除" in dlg_m.summary.text(),
          dlg_m.summary.text()[:70])
    dlg_m._checked = set()

    print()
    print("=== 10d. the search box narrows the list without touching the ticks ===")
    dlg_s = rmod.ReplaceDialog(s, scope_of("division"), s.display, win)
    dlg_s.show()
    app.processEvents()
    total_rows = dlg_s.from_list.count()
    check("the search box starts empty and hides nothing",
          dlg_s.from_search.text() == "" and dlg_s._visible_rows() == total_rows,
          f"{dlg_s._visible_rows()}/{total_rows} visible")
    needle = dlg_s.from_list.item(0).text()[:12]
    dlg_s.from_search.setText(needle)
    app.processEvents()
    # Deliberately *not* named ``shown``: this function already has one of
    # those — the list of dialogs the stubs recorded — and shadowing it here
    # turned a passing section into an AttributeError three sections later.
    matched = dlg_s._visible_rows()
    check("typing hides the rows that do not match",
          0 < matched < total_rows, f"{matched}/{total_rows} for {needle!r}")
    check("and every row still visible really contains it",
          all(needle.lower() in dlg_s.from_list.item(r).text().lower()
              for r in range(total_rows)
              if not dlg_s.from_list.item(r).isHidden()),
          f"{matched} visible")
    check("the counter owns up to the filter",
          f"筛选出 {matched} / {total_rows}" in dlg_s.from_count.text(),
          dlg_s.from_count.text())
    # Tick a row, then filter it away.  The tick has to survive: if a filter
    # dropped ticks, typing a letter would silently shrink a half-built
    # request and the blast-radius figure would follow it down.  The *last*
    # row is used on purpose — it is the one the needle does not match, so the
    # tick genuinely ends up on a hidden row.
    dlg_s.from_search.setText("")
    app.processEvents()
    last = total_rows - 1
    dlg_s.from_list.item(last).setCheckState(Qt.Checked)
    app.processEvents()
    picked_before = list(dlg_s._selected())
    dlg_s.from_search.setText(needle)
    app.processEvents()
    hidden_last = dlg_s.from_list.item(last).isHidden()
    check("a tick on a filtered-away row survives, and is still counted",
          hidden_last and dlg_s._selected() == picked_before
          and dlg_s._ticked_rows() == 1,
          f"{dlg_s._ticked_rows()} ticked, last hidden={hidden_last}")
    check("the counter shows the hidden tick rather than dropping it",
          "已勾选 1 种" in dlg_s.from_count.text(), dlg_s.from_count.text())
    # 全选 follows the filter ...
    dlg_s._set_all(True)
    app.processEvents()
    check("全选 takes every row the filter is showing",
          all(dlg_s.from_list.item(r).checkState() == Qt.Checked
              for r in range(total_rows)
              if not dlg_s.from_list.item(r).isHidden()),
          f"{dlg_s._visible_rows()} visible, all ticked")
    check("and leaves the filtered-away tick alone",
          dlg_s._ticked_rows() == dlg_s._visible_rows() + 1,
          f"{dlg_s._ticked_rows()} ticked / {dlg_s._visible_rows()} visible")
    # ... 清空 does not, so there is always one way back to a clean slate.
    dlg_s._set_all(False)
    app.processEvents()
    check("清空 clears even the rows the search is hiding",
          dlg_s._selected() == [], str(dlg_s._selected()))
    dlg_s.from_search.setText("")
    app.processEvents()
    check("clearing the search brings every row back",
          dlg_s._visible_rows() == total_rows, str(dlg_s._visible_rows()))

    print()
    print("=== 11. the picker only offers AG-usable packs of this file ===")
    import importlib
    real_swap = importlib.import_module("ndfbin_decks.app.swap_dialog")
    gui_mod.SwapDialog = real_swap.SwapDialog
    SwapDialog = real_swap.SwapDialog
    check("catalogue came from the open file",
          "Test" in cat.source_path, cat.source_path)
    # Not the shipped numbers — those are pinned in section 2.  What has to
    # hold after several hundred edits is the *relation*: the gate still puts
    # every pack in one of its three buckets, the pool it draws from has not
    # grown, and the baseline keeps an option alive even when no AG deck
    # references it any more.
    check("AG gate still accounts for every pack",
          len(cat) + cat.stats.skirmish_only + cat.stats.unreferenced
          == base_total
          and len(cat) >= base_ag,
          f"{len(cat)} + {cat.stats.skirmish_only} + "
          f"{cat.stats.unreferenced} vs {base_total}; "
          f"was {base_ag} AG packs")
    check("no-xp packs are kept, not banned",
          cat.stats.ag_without_xp >= 300, str(cat.stats.ag_without_xp))

    dlg2 = SwapDialog(s, None)
    dlg2.show()
    app.processEvents()
    check("picker lists many packs", dlg2.list.count() > 100,
          f"{dlg2.list.count()} shown")
    labels = [dlg2.list.item(i).text() for i in range(min(3, dlg2.list.count()))]
    check("labels carry names and ids", all("#" in l for l in labels),
          str(labels[:1]))

    dlg2.search.setText("MiG_29")
    app.processEvents()
    check("search narrows the list", 0 < dlg2.list.count() < 500,
          str(dlg2.list.count()))
    dlg2.search.setText("zzz-不可能存在-zzz")
    app.processEvents()
    check("search can reach zero", dlg2.list.count() == 0)

    dlg2.search.setText("")
    app.processEvents()

    print()
    print("=== 11b. the lower half builds a combination the file lacks ===")
    from ndfbin_decks.app.swap_dialog import NO_TRANSPORT
    from ndfbin_decks.core.deckmodel import unit_leaf
    # Derived from the catalogue rather than hard-coded: earlier sections of
    # this test have already edited the file, so "which combination exists" is
    # a fact about the mutated graph, not about Test as shipped.
    sample = next((c for c in cat.items
                   if c.transporter and c.experience is not None), cat.items[0])
    print(f"  (probe combination: {sample.label()})")
    dlg2._fill_fields(sample.key)
    dlg2._manual = True
    app.processEvents()
    key, state, msg = dlg2.evaluate()
    check("a combination the mod already fields is offered for reuse",
          state == "reuse", f"{state}: {msg.splitlines()[0][:52]}")
    check("and its key resolves to a real pack object",
          key is not None and s.packs.index.find(key) is not None,
          str(s.packs.index.find(key) if key else None))

    # Nudge the experience until the combination is one the file never had; an
    # unseen combination of already-AG-proven paths is a plain create.
    created = None
    for level in (None, 1, 2, 3):
        dlg2.xp_box.setCurrentIndex(dlg2.xp_box.findData(level))
        app.processEvents()
        key2, state2, msg2 = dlg2.evaluate()
        if key2 is not None and s.packs.index.find(key2) is None:
            created = (key2, state2, msg2)
            break
    if created is None:
        print("  (every experience variant already exists — skipping)")
    else:
        key2, state2, msg2 = created
        check("an unseen combination of proven paths is a plain create",
              state2 == "create", f"{state2}: {msg2.splitlines()[0][:52]}")
        check("and it pairs a real unit with a real pack key",
              key2.unit == sample.unit
              and key2.transporter == sample.transporter,
              key2.describe())

    dlg2.unit_box.setCurrentText("ZZZ_NOT_A_UNIT")
    app.processEvents()
    key3, state3, msg3 = dlg2.evaluate()
    check("a name no definition file knows demands confirmation",
          state3 == "confirm", f"{state3}: {msg3.splitlines()[0][:52]}")
    check("and the prompt lists every source that was checked",
          "Unit.ndfbin" in msg3 and "base.zip" in msg3, msg3[:90])
    check("it still yields a usable key, so the workflow is reachable",
          key3 is not None and key3.unit.endswith("ZZZ_NOT_A_UNIT"))
    check("the field is still editable, not a closed list",
          dlg2.unit_box.isEditable() and dlg2.trans_box.isEditable())
    offered = [dlg2.unit_box.itemData(k)
               for k in range(dlg2.unit_box.count())]
    check("the dropdown offers the sample unit",
          sample.unit in offered, f"{dlg2.unit_box.count()} units offered")

    print()
    print("=== 11c. the list comes from the definition files, not the packs ===")
    universe = s.get_units()
    check("a unit universe was resolved for this mod",
          len(universe) >= len(universe.vanilla) > 1000, universe.summary())
    sus = [p for p in universe.all if p.endswith("Descriptor_Unit_Su_27K_SOV")]
    check("Su_27K_SOV is known", bool(sus))
    if sus:
        su = sus[0]
        check("and it is classified as vanilla",
              universe.origin(su) == "原版", universe.origin(su))
        check("it is now offered in the dropdown, though no AG deck fields it",
              su in offered and su not in cat.units(),
              f"in dropdown={su in offered}, in AG gate={su in cat.units()}")
        # The point of the change: a vanilla unit the AG decks never used is a
        # plain create, not a confirmation prompt.
        dlg2._manual = True
        dlg2._select_path(dlg2.unit_box, su)
        dlg2._select_path(dlg2.trans_box, None)
        dlg2.xp_box.setCurrentIndex(dlg2.xp_box.findData(None))
        app.processEvents()
        key_su, state_su, msg_su = dlg2.evaluate()
        check("picking it is a plain create, not a confirmation",
              state_su == "create" and key_su is not None
              and key_su.unit == su,
              f"{state_su}: {msg_su.splitlines()[0][:56]}")
        check("and the status says where it came from",
              "原版" in msg_su, msg_su.splitlines()[0][:56])

    check("every offered unit is a real $/GFX/Unit path",
          all(isinstance(x, str) and x.startswith("$/GFX/Unit/Descriptor_Unit_")
              for x in offered if x),
          str([x for x in offered if x and not x.startswith("$/")][:2]))
    check("the dropdown is longer than the AG gate's own list",
          len([x for x in offered if x]) > len(cat.units()),
          f"{len([x for x in offered if x])} vs {len(cat.units())}")

    print()
    print("  -- the 「只列出已引用过的」 escape hatch --")
    wide = dlg2.unit_box.count()
    dlg2.only_proven.setChecked(True)
    app.processEvents()
    narrow = dlg2.unit_box.count()
    check("checking it narrows the list to the AG-proven set",
          narrow == len(cat.units()) < wide, f"{wide} -> {narrow}")
    dlg2.only_proven.setChecked(False)
    app.processEvents()
    check("unchecking restores the full list",
          dlg2.unit_box.count() == wide, str(dlg2.unit_box.count()))

    check("the transport dropdown carries a no-transport entry",
          dlg2.trans_box.itemText(0) == NO_TRANSPORT,
          dlg2.trans_box.itemText(0))
    check("and the same full unit list behind it",
          dlg2.trans_box.count() == dlg2.unit_box.count() + 1,
          f"{dlg2.trans_box.count()} vs {dlg2.unit_box.count()}")

    # The confirmation is the whole safety story for that path, so it has to
    # actually gate: answering No must leave the dialog un-accepted.  The unit
    # is set back to the unknown name first — section 11c left a legal vanilla
    # unit in the box, which correctly never prompts.
    dlg2._manual = True
    dlg2.unit_box.setCurrentText("ZZZ_NOT_A_UNIT")
    app.processEvents()
    _probe = dlg2.evaluate()
    check("the box is back on the unknown name before the gate test",
          _probe[1] == "confirm", f"{_probe[1]}")
    from PySide6.QtWidgets import QMessageBox as _MB
    prior_warn = _MB.warning
    seen: list[str] = []
    try:
        _MB.warning = staticmethod(
            lambda *a, **k: (seen.append(str(a[2])), _MB.No)[1])
        dlg2.accept()
        app.processEvents()
        check("declining the confirmation keeps the dialog open",
              dlg2.result() != QDialog.Accepted and dlg2.chosen() is None,
              f"result={dlg2.result()}")
        _MB.warning = staticmethod(
            lambda *a, **k: (seen.append(str(a[2])), _MB.Yes)[1])
        dlg2.accept()
        app.processEvents()
        chosen = dlg2.chosen()
        check("accepting it lets the build through",
              chosen is not None and chosen.unit.endswith("ZZZ_NOT_A_UNIT"),
              str(chosen))
    finally:
        _MB.warning = prior_warn

    dlg2._fill_fields(sample.key)
    dlg2._manual = False
    app.processEvents()

    check("owns() rejects a foreign id", cat.owns(999999) is None)
    check("owns() accepts an id from this file",
          cat.owns(cat.items[0].pack_object_id) is not None)
    # A PvP-only pack must not be reachable at all.
    from ndfbin_decks.app.catalog import pack_ids_by_kind
    ag_ids, pvp_ids = pack_ids_by_kind(s.deck)
    only_pvp = sorted(pvp_ids - ag_ids)
    check("PvP-only packs exist to test against", len(only_pvp) > 1000,
          str(len(only_pvp)))
    check("the gate hides every PvP-only pack",
          all(cat.owns(p) is None for p in only_pvp[:200]),
          f"{len(only_pvp)} PvP-only packs")

    print()
    print("=== 12. save writes a loadable file ===")
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "Deck.ndfbin"
    try:
        data = s.save(tmp)
        check("bytes written", len(data) > 0, f"{len(data):,} B")
        from ndfbin_decks.app.session import Session
        s2 = Session.load(tmp, resolve_names=False)
        check("reloads cleanly", len(s2.battalions()) == 2208,
              str(len(s2.battalions())))
        v2 = s2.validate()
        check("no hard errors after all the edits", not v2.errors,
              f"{len(v2.errors)} errors")
    except Exception as exc:                            # noqa: BLE001
        check("save succeeded", False, repr(exc))

    print()
    print("=== 12b. a refused save names the deck, and says how many ===")
    # A user reported this dialog and could not act on it: it listed bare
    # messages, so it said *what* was wrong without saying *which deck* — the
    # one thing needed to go and look.  Damaged in memory, never written.
    deck = win.session.deck
    NAME = "Descriptor_Deck_pion_SOV_1GTA_595Reco"
    target = deck.find(NAME)
    props = {p["property_name"]: p["value"]
             for p in deck._by_id[target.deck_object_id]["properties"]}
    props["DeckPackList"]["items"].insert(3, dict(props["DeckPackList"]["items"][3]))
    ids = {pl.object_id for c in target.companies for pl in c.platoons}
    for o in deck.info["objects"]:
        if o["id"] not in ids:
            continue
        pr = {p["property_name"]: p["value"] for p in o["properties"]}
        for item in pr.get("PackIndexUnitNumberList", {}).get("items", []):
            key = item.get("key", {})
            if isinstance(key.get("value"), int) and key["value"] >= 3:
                key["value"] += 1
    deck.invalidate()

    shown.clear()
    win.on_save()
    kind, body = shown[-1] if shown else ("", "")
    check("an unsaveable file is refused", kind == "error", str(shown[-1:]))
    check("and the dialog names the deck that needs repair",
          "pion_SOV_1GTA_595Reco" in body, body[:100])
    check("and it says where inside the deck",
          "连0/排1" in body, body[:200])
    check("and it counts the errors and the decks",
          "共 1 条硬错误" in body and "涉及 1 个营" in body, body[-160:])

    # The remedy the dialog is implicitly asking for: any relay-routed edit
    # re-lays the deck into the one legal layout.
    b = deck.find(NAME)
    pl = b.companies[-1].platoons[-1]
    win.session.retiler.set_count(b.deck_object_id, len(b.companies) - 1,
                                  len(b.companies[-1].platoons) - 1,
                                  len(pl.windows) - 1, pl.windows[-1].count)
    deck.invalidate()
    check("and a relay edit on that deck clears it, so saving can proceed",
          not win.session.validate().errors,
          str([i.code for i in win.session.validate().errors]))

    print()
    print("=== 13. guards in the UI layer ===")
    # back to the stub: a real modal would block headless forever
    gui_mod.SwapDialog = _AutoPick
    shown.clear()
    calls_before = _AutoPick.calls
    win.table.clearSelection()
    win.table.setCurrentCell(-1, -1)
    win.on_swap()          # no table row selected -> must warn, not crash
    check("swap without a row is blocked",
          any("条目" in m for _, m in shown),
          str(shown[-1]) if shown else "no dialog")
    check("and no picker was opened for it",
          _AutoPick.calls == calls_before,
          f"exec() called {_AutoPick.calls - calls_before} time(s)")

    shown.clear()
    win.tree.setCurrentItem(win.tree.topLevelItem(0))   # the division node
    win.on_set_count()
    check("set_count on a division node is blocked",
          any("请先选中" in m for _, m in shown),
          str(shown[-1]) if shown else "no dialog")

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("PASS — GUI wired correctly, AG gate holds, batch replace is sound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
