"""The editor window: pick a mod, walk 师/营/连/排, edit what a deck fields.

Layout and vocabulary follow the Army General editor the modder already knows:
a filter chain on top (mod → 师 → 营), the organisation tree on the left, the
entry table for the selected battalion on the right, and the actions that are
proven safe at the bottom.

**Where mods come from.**  Two roots, because a downloaded mod is *not* copied
into the Saved Games workspace — the game loads it from the Steam folder:

=================  ==========================================================
``deploy``         ``Saved Games/EugenSystems/WARNO/mod/<Name>/``
``workshop``       ``<steamapps>/workshop/content/1611600/<published_id>/``
=================  ==========================================================

``libraryfolders.vdf`` is parsed rather than guessing at install paths, so a
library on another drive is found.  When automatic discovery still misses one —
an extracted archive, a USB stick, a second Windows account — the
「打开 mod 文件夹」 button takes any directory and scans it, which is the
fallback that needs no knowledge of the machine at all.

**The organisation is 师 Div / 营 Bn / 连 Co / 排 Pl.**  That is what the game
calls these levels: an AG "pawn" on the campaign map is a battalion, a combat
group is a company, a smart group is a platoon.  The 师 level is rebuilt from
``DeckDivision``, which is present on every AG deck (2208/2208 on Test), so the
tree shows the real hierarchy rather than a guess from name prefixes.

**Why the filter chain comes first.**  Test ships 2208 battalions in 233
divisions; a flat list of them is unnavigable.  Both filters are searchable: the
division combo narrows its own list as you type (``SearchableComboBox``), and
the battalion box narrows the tree.  A 233-row popup is a wall of text; typing
``pion`` should turn it into three rows, not make the user scroll.

**Why platoons are labelled by what they field.**  Most mods ship no
PLATOONS/COMPANIES dictionary — Test has 6 entries, Berlin 865 — so an
unresolved name is the common case.  A modder choosing a platoon to edit cares
what is inside it, and that is always answerable, so the composition
(``T72M1 ×4 · BMP-2 ×2``) is the label and the dictionary name is the tooltip.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtGui import (QBrush, QColor, QIcon, QStandardItem,
                           QStandardItemModel)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCompleter, QDialog,
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, APP_TAGLINE, __version__
from .. import paths
from ..io import divisions, modlocator
from .replace_dialog import ReplaceDialog
from .session import Session, open_mod
from .swap_dialog import SwapDialog
from .. import i18n
from ..i18n import live, qt_builtin, settings, tr

HEADERS = live(globals(), "HEADERS", lambda: (tr("条目"), tr("连Co"), tr("排Pl"), tr("装备"), tr("运输"), tr("经验"), tr("件数"), tr("槽位"), "pack#"))
COLUMNS = len(HEADERS)

#: How many battalions a "search everywhere" may show.  2208 of them would
#: defeat the point of searching.
SEARCH_CAP = 300


@dataclass(frozen=True)
class NodeRole:
    """What a tree node *is*, so callers never index a tuple blind.

    An earlier build passed variable-length tuples around and the length checks
    drifted; a named field cannot be misread.
    """

    kind: str            # div | bn | co | pl | entry
    division: str | None = None
    deck_id: int | None = None
    ci: int = -1
    si: int = -1
    ei: int = -1


class SearchableComboBox(QComboBox):
    """An editable combo whose list narrows as you type.

    233 divisions in a plain combo is a wall of text: the only way to reach one
    is to scroll and squint.  Typing filters the model through a
    ``QSortFilterProxyModel``, so the popup *and* the inline completer show only
    what matches — 233 rows become 3 the moment the user types ``pion``.

    Item data still carries whatever the caller put there (the division path,
    the mod's directory name), so nothing downstream changes: ``count()``,
    ``currentData()``, ``currentText()`` and ``findData()`` all keep their usual
    meaning, just over the filtered rows.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QStandardItemModel(0, 1, self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._source)
        self._proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self._proxy.setFilterKeyColumn(0)
        self.setModel(self._proxy)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMaxVisibleItems(20)
        #: last real selection — a filter matching nothing must not erase it
        self._last_data = None
        completer = self.completer()
        if completer is not None:
            # ``MatchContains`` not ``MatchStartsWith``: the useful handle is
            # often in the middle of the name (``AGF_berlin_1989``).
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.lineEdit().textEdited.connect(self._on_typed)
        # Every selection change clears the box and moves the choice into the
        # placeholder — including the one the user makes by clicking a row in
        # the popup, which is the path that would otherwise leave the label in
        # the edit box for the next keystroke to append to.
        self.currentIndexChanged.connect(self._sync_edit)
        # Picking from the inline completion popup — that is the moment the
        # user has *chosen*, and the only one that should move the selection.
        if completer is not None:
            completer.activated.connect(self._pick_text)
        self.lineEdit().returnPressed.connect(self.accept_typed)

    # -- population -------------------------------------------------------

    def set_options(self, options, *, select: int = 0) -> None:
        """Repopulate from ``[(label, data), ...]``.

        **Deliberately silent.**  Swapping the rows walks the combo through
        ``currentIndex = -1`` and through a half-built list; emitting on the
        way makes every listener — the tree reads these filters — run against
        that intermediate state.  The re-fill therefore notifies nobody, and
        the *caller* refreshes afterwards, because only the caller knows
        whether the list it just filled is the one on screen now.

        Signals are blocked for the rebuild so Qt's own housekeeping during
        ``clear()`` cannot escape either.
        """
        self.blockSignals(True)
        self._source.clear()
        for label, data in options:
            item = QStandardItem(label)
            item.setData(data, Qt.UserRole)
            self._source.appendRow(item)
        self._proxy.setFilterFixedString("")
        if self._source.rowCount():
            self.setCurrentIndex(max(0, min(select, self._source.rowCount() - 1)))
            self._last_data = self.currentData()
        self._sync_edit()
        self.blockSignals(False)

    def add_option(self, label: str, data) -> None:
        item = QStandardItem(label)
        item.setData(data, Qt.UserRole)
        self._source.appendRow(item)

    # -- filtering --------------------------------------------------------

    def _sync_edit(self) -> None:
        """Show the current choice as a *placeholder*, leaving the box empty.

        An editable ``QComboBox`` puts the current row's label in its edit box
        with the cursor at the end, so the first keystroke appends:
        ``RFA_TK_Nord  (57 营)`` + ``S``.  Selecting it on focus fixes that only
        for the focus paths that deliver a focus event — clicking the arrow
        first, or tabbing back in, takes a different one.

        Keeping the box empty and putting the choice in the placeholder removes
        the whole class of problem: typing is always a fresh search, whatever
        the focus history, and the current value is still on screen.

        It also **records the committed row**, whatever brought the combo to it
        — the popup list, the arrow keys, the completer.  Only
        :meth:`set_options` and :meth:`_pick_text` used to write
        ``_last_data``, so choosing a row by clicking it in the popup left the
        previous row behind as "the committed choice", and
        :meth:`_restore` put *that* one back on the next focus-out.
        """
        row = self.currentIndex()
        if 0 <= row < self.count():
            self._last_data = self.itemData(row)
        text = self.itemText(row) if 0 <= row < self.count() else ""
        self.lineEdit().setText("")
        self.lineEdit().setPlaceholderText(text or tr("输入以筛选"))

    def focusOutEvent(self, event) -> None:
        """Drop an abandoned search instead of leaving the list narrowed.

        Reached on every focus-out, including when the completion popup is up:
        a click there lands in :meth:`_pick_text` *after* this, and that looks
        its row up by text in the restored (unfiltered) model, so the pick
        still arrives.  The alternative — sitting a narrowed or empty list
        behind a blurred box — is the worse failure.  What it does *not* do is
        move a box nobody was searching in; see :meth:`_restore`.
        """
        super().focusOutEvent(event)
        self._restore()

    def _restore(self) -> None:
        """Snap back to the committed selection and lift the filter.

        Nothing is abandoned when nothing was typed, so an untouched box is
        left exactly as it is: **focus alone must never move a filter.**  That
        is the drift that put the 师 picker back on the country's first
        division — the popup-list path moved the index without moving
        ``_last_data``, and the next focus-out (the modal replace dialog
        taking focus) quietly restored the default row.  The tree did not
        follow at that moment, because signals are blocked here, so the move
        only became visible at the *next* refresh — right after an edit.
        """
        if not self.lineEdit().text() \
                and self._proxy.rowCount() == self._source.rowCount():
            self._sync_edit()
            return
        self.blockSignals(True)
        self._proxy.setFilterFixedString("")
        row = None
        if self._last_data is not None:
            for r in range(self.count()):
                if self.itemData(r) == self._last_data:
                    row = r
                    break
        if row is not None:
            self.setCurrentIndex(row)
        self._sync_edit()
        self.blockSignals(False)

    def _on_typed(self, text: str) -> None:
        """Narrow the list as the user types, without stealing the cursor.

        Two Qt behaviours have to be defused here, and both bit before:

        * **A filtered-out current row makes the combo reset the line edit.**
          ``QComboBox`` watches its own model; when the filter removes the row
          it is on, it clears ``currentIndex`` *and rewrites the edit text*.
          Blocking signals does not stop that — it is internal — so the text is
          put back afterwards.  The keystroke is the truth, not the model.
        * **``currentIndex`` must not be moved.**  Setting it on an editable
          combo rewrites the line edit with the current row's text, so the next
          keystroke appends to that: typing ``SOV`` used to become
          ``SOV_27GMRD  (21 营)OV`` and match nothing.

        The pick is applied by :meth:`_pick_text`, when the user chooses.
        """
        self.blockSignals(True)
        self._proxy.setFilterFixedString(text)
        self.blockSignals(False)
        if self.lineEdit().text() != text:
            self.lineEdit().setText(text)
            self.lineEdit().setCursorPosition(len(text))

    def _pick_text(self, text: str) -> None:
        """Select the row whose label is ``text`` and notify the window.

        The filter is cleared first: ``currentIndex`` is a *proxy* row, and
        lifting the filter reorders those rows, so setting the index and then
        unfiltering would land on whatever slid into that position.
        """
        self.blockSignals(True)
        self._proxy.setFilterFixedString("")
        row = self._row_of_text(text)
        if row is not None:
            self.setCurrentIndex(row)
            self._last_data = self.currentData()
        self._sync_edit()
        self.blockSignals(False)
        if row is not None:
            self.currentIndexChanged.emit(row)

    def accept_typed(self) -> None:
        """Commit the first surviving row — what Enter in the box should do.

        Without this, ``NoInsert`` means Enter on a half-typed name silently
        does nothing at all, which reads as the box being broken.
        """
        if self.lineEdit().text() == "":
            return                          # nothing typed; keep the selection
        if self._proxy.rowCount():
            self._pick_text(self.itemText(0))

    def _row_of_text(self, text: str) -> int | None:
        for row in range(self.count()):
            if self.itemText(row) == text:
                return row
        return None

    def matches(self) -> int:
        return self._proxy.rowCount()

    def current_label(self) -> str:
        """The visible label of the selection.

        ``currentText()`` is the *edit box*, which this combo deliberately keeps
        empty — the choice lives in the placeholder and in the model, so
        anything wanting to show it has to ask here.
        """
        row = self.currentIndex()
        return self.itemText(row) if 0 <= row < self.count() else ""

    def current_path(self):
        """The selected row's data, surviving a filter that matched nothing.

        The two ways ``currentData()`` can be ``None`` mean opposite things:

        * **a valid row whose data is ``None``** — the user picked the
          "全部师 / 全部国家" entry, and ``None`` is the answer.  Returning the
          previous choice here would make that row unselectable.
        * **no valid row** — Qt cleared the index because the filter hid it
          mid-keystroke; the previous choice is what the user still means, and
          without the fallback a stray character would silently widen the tree
          to every battalion in the file.
        """
        row = self.currentIndex()
        if 0 <= row < self.count():
            return self.currentData()
        return self._last_data


class EntryTable(QTableWidget):
    """One row per **entry**, because the entry is the atom of editing.

    ``DeckPackList`` is a *sequence of windows*: entry ``(cg, sg, e)`` owns
    ``count`` consecutive slots, all of which must carry the same pack (W3).
    An earlier build listed raw slots, which made a 3-unit window look like
    three independent things; swapping one of them produced the mixed window
    ``[T72_CMD_SOV, BM27, BM27]`` found on the Berlin file.  Listing entries
    instead means every row *is* one editable thing, and the slot range is
    shown as information rather than as something to click on.
    """

    def __init__(self, parent=None):
        super().__init__(0, COLUMNS, parent)
        self.setHorizontalHeaderLabels(list(HEADERS))
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self._lit: tuple | None = None

    # -- filling ----------------------------------------------------------

    def fill(self, session: Session, deck) -> None:
        """List every entry of a battalion in ``cg -> sg -> entry`` order."""
        pool = session.retiler.pool(deck.deck_object_id)
        rows = []
        for ci, company in enumerate(deck.companies):
            for si, platoon in enumerate(company.platoons):
                # ``windows`` are the (PackIndex, Number) pairs — one per
                # entry — and are exactly what ``Retiler.relay`` walks, so the
                # row index *is* the entry address an edit will use.
                # ``entries`` is slot-level and must not be used here: a
                # 3-unit window would occupy three rows and look editable
                # piecewise, which is the mistake that mixed a window.
                for ei, window in enumerate(platoon.windows):
                    start = window.pack_index
                    count = max(int(window.count or 0), 0)
                    pid = (pool[start]
                           if 0 <= start < len(pool) else None)
                    rows.append((ci, si, ei, pid, start, count))

        self.setRowCount(len(rows))
        for row, (ci, si, ei, pid, start, count) in enumerate(rows):
            key = session.packs.pack_key(pid) if pid is not None else None
            unit = session.display(key.unit) if key else "?"
            trans = (session.display(key.transporter)
                     if key and key.transporter else "—")
            xp = (str(key.experience) if key and key.experience is not None
                  else tr("继承"))
            slots = (f"{start}-{start + count - 1}" if count > 1
                     else str(start))
            cells = (f"#{ei + 1}", f"Co{ci}", f"Pl{si}", unit, trans, xp,
                     str(count), slots, f"#{pid}" if pid is not None else "?")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.UserRole, (ci, si, ei))
                if col == 5 and xp == tr("继承"):
                    item.setForeground(QBrush(QColor(120, 120, 120)))
                if col == 7:
                    item.setToolTip(tr("占 {count} 个槽位，全部必须是同一装备", count=count))
                self.setItem(row, col, item)
        self.resizeColumnsToContents()

    # -- selection --------------------------------------------------------

    def selected_entry(self) -> tuple[int, int, int] | None:
        """The ``(cg, sg, entry)`` of the current row."""
        row = self.currentRow()
        if row < 0:
            return None
        item = self.item(row, 0)
        return None if item is None else item.data(Qt.UserRole)

    def select_entry(self, ci: int, si: int, ei: int) -> bool:
        """Move the cursor to an entry, so a reload keeps the user's place."""
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == (ci, si, ei):
                self.setCurrentCell(row, 0)
                return True
        return False

    def highlight(self, ci: int | None, si: int | None) -> None:
        """Mark the entries of one platoon — or of a whole company when ``si``
        is ``None``, which is what selecting a company node should show."""
        self._lit = None if ci is None else (ci, si)
        brush = QBrush(QColor(200, 240, 200))
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is None:
                continue
            addr = item.data(Qt.UserRole)
            lit = (self._lit is not None and addr is not None
                   and addr[0] == ci and (si is None or addr[1] == si))
            for col in range(COLUMNS):
                cell = self.item(row, col)
                if cell is None:
                    continue
                cell.setBackground(brush if lit else QBrush(QColor(0, 0, 0, 0)))
            font = item.font()
            font.setBold(lit)
            item.setFont(font)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        # The window title is what Windows shows in the taskbar hint when the
        # pointer rests on this button, so it is the tagline rather than the
        # product name — see ``ndfbin_decks.APP_TAGLINE``.
        self.setWindowTitle(APP_TAGLINE)
        icon = paths.icon_path()
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1460, 900)
        self.session: Session | None = None
        self._divisions: list = []
        self._countries: list = []
        self._country_of: dict[str, object] = {}
        #: combo item key -> resolved ModPaths (a hand-opened mod lives only here)
        self._mod_index: dict[str, modlocator.ModPaths] = {}
        #: ``retranslate()`` replays these; see the note on that method
        self._retranslaters: list = []
        self._build()
        self._refresh_mods()

    def _rt(self, fn) -> None:
        """Set a piece of static text now, and remember how to set it again.

        Every label in this window is registered rather than merely assigned,
        so switching language is a matter of replaying the same ``tr()`` calls
        instead of rebuilding the window — which would throw away unsaved work.
        """
        self._retranslaters.append(fn)
        fn()

    def retranslate(self) -> None:
        """Re-run every static text call, then refresh what is derived."""
        for fn in self._retranslaters:
            fn()
        # Text built from the open file — battalion names, the counters, the
        # log line — comes out of a fresh pass rather than a second copy of the
        # rules that produced it.
        if self.session is not None:
            self._fill_tree()
            self._refresh_mods()

    def on_language(self, index: int) -> None:
        code = self.lang_box.itemData(index)
        if not code or code == i18n.language():
            return
        i18n.set_language(code)
        qt_builtin.install(QApplication.instance(), code)
        settings.save_language(code)
        self.retranslate()

    # -- layout -----------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)

        outer.addWidget(self._build_filter_bar())

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self._rt(lambda: self.tree.setHeaderLabels(
            [tr("师Div / 营Bn / 连Co / 排Pl")]))
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.org_label = QLabel()
        self._rt(lambda: self.org_label.setText(tr("组织")))
        left_col.addWidget(self.org_label)
        left_col.addWidget(self.tree, 1)
        split.addWidget(left)

        right = QWidget()
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        self.entries_label = QLabel()
        self._rt(lambda: self.entries_label.setText(
            tr("装备条目（一行 = 一个窗口，整行同时换装）")))
        right_col.addWidget(self.entries_label)
        self.table = EntryTable()
        right_col.addWidget(self.table, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        outer.addWidget(self._build_actions())

        self.log = QLabel("")
        self.log.setWordWrap(True)
        self.log.setMaximumHeight(76)
        outer.addWidget(self.log)

        self.setCentralWidget(root)

        self.mod_box.currentIndexChanged.connect(self._on_mod_changed)
        self.country_box.currentIndexChanged.connect(self._on_country_changed)
        self.div_box.currentIndexChanged.connect(self._fill_tree)
        self.bn_search.textChanged.connect(self._fill_tree)
        self.tree.currentItemChanged.connect(self._on_tree_select)
        self.table.cellDoubleClicked.connect(lambda *_: self.on_swap)

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(QLabel("Mod"))
        self.mod_box = QComboBox()
        self.mod_box.setMinimumWidth(150)
        self._rt(lambda: self.mod_box.setToolTip(
            tr("本机 Saved Games 工作区 + Steam 创意工坊的 mod。\n"
            "找不到时用右边的「打开 mod 文件夹」手动指定。")))
        row.addWidget(self.mod_box, 1)

        self.open_folder_btn = QPushButton()
        self._rt(lambda: self.open_folder_btn.setText(tr("打开 mod 文件夹")))
        self._rt(lambda: self.open_folder_btn.setToolTip(
            tr("手动指定 Mod 部署目录（兜底）。\n"
            "可以选一个 mod 文件夹，也可以选装着多个 mod 的父目录 ——\n"
            "Steam 库目录、Saved Games 工作区、整包解压出来的文件夹都行。")))
        self.open_folder_btn.clicked.connect(self.on_open_folder)
        row.addWidget(self.open_folder_btn)

        self.country_label = QLabel()
        self._rt(lambda: self.country_label.setText(tr("国家 Nat")))
        row.addWidget(self.country_label)
        self.country_box = SearchableComboBox()
        self.country_box.setMinimumWidth(230)
        self._rt(lambda: self.country_box.setToolTip(
            tr("按师的前缀（SOV / RFA / RDA… ）筛选，师名为 RDA 的即东德 ——\n"
            "输入 DDR / USSR 这类别名也能命中。选完国家后右边的师列表只剩它。")))
        row.addWidget(self.country_box)

        self.div_label = QLabel()
        self._rt(lambda: self.div_label.setText(tr("师 Div")))
        row.addWidget(self.div_label)
        self.div_box = SearchableComboBox()
        self.div_box.setMinimumWidth(280)
        self._rt(lambda: self.div_box.setToolTip(
            tr("本 mod 的师；营按 DeckDivision 归属。\n"
            "直接输入字符即可缩小列表，例如 SOV_TK / 12Pz / 27GMRD")))
        row.addWidget(self.div_box, 1)

        self.bn_label = QLabel()
        self._rt(lambda: self.bn_label.setText(tr("营搜索")))
        row.addWidget(self.bn_label)
        self.bn_search = QLineEdit()
        self._rt(lambda: self.bn_search.setPlaceholderText(
            tr("营名，例如 pion / TK / 11Sq")))
        self.bn_search.setMinimumWidth(200)
        row.addWidget(self.bn_search, 1)

        # No label: the items name themselves in their own language, which is
        # the one control that must stay readable whatever is selected.
        self.lang_box = QComboBox()
        self.lang_box.setMinimumWidth(120)
        self._rt(lambda: self.lang_box.setToolTip(tr("界面语言")))
        for code, label in i18n.available():
            self.lang_box.addItem(label, code)
        self.lang_box.setCurrentIndex(
            max(self.lang_box.findData(i18n.language()), 0))
        self.lang_box.currentIndexChanged.connect(self.on_language)
        row.addWidget(self.lang_box)
        return bar

    def _build_actions(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.info = QLabel()
        self._rt(lambda: self.info.setText(tr("选一个 mod 开始")))
        self.info.setWordWrap(True)
        row.addWidget(self.info, 1)
        self._action_buttons: list = []

        # Written out rather than looped over a table of labels: ``tr()`` has to
        # be called *inside* the stored lambda, or a language switch would just
        # re-apply whatever the build-time call returned.  That also keeps every
        # key visible to ``tools/i18n_sync.py``, which reads source, not runtime.
        for slot in (self.on_set_count, self.on_swap, self.on_insert,
                     self.on_remove, self.on_replace, self.on_save):
            btn = QPushButton()
            btn.clicked.connect(slot)
            row.addWidget(btn)
            self._action_buttons.append((btn, slot))
        self._rt(self._label_actions)

        # The title bar carries the tagline, so the window itself is where the
        # product name has to appear — otherwise nothing on screen says what
        # this program is.  Grey and after the buttons, so it reads as a stamp
        # rather than as another control.
        stamp = QLabel(f"{APP_NAME} v{__version__}")
        stamp.setStyleSheet("color: palette(mid);")
        row.addWidget(stamp)
        return bar

    def _label_actions(self) -> None:
        labels = {
            self.on_set_count: (tr("改件数"),
                                tr("修改选中条目的件数（整营自动重排槽位）")),
            self.on_swap: (tr("换装备"), tr("把选中槽位换成别的装备")),
            self.on_insert: (tr("插入条目"), tr("在选中排里加一条装备")),
            self.on_remove: (tr("删除条目"), tr("删掉选中排里的一条装备")),
            self.on_replace: (tr("批量替换"), tr("按 师/营/连/排 层级批量换装备")),
            self.on_save: (tr("保存"), tr("写回 Deck.ndfbin（先备份）")),
        }
        for btn, slot in self._action_buttons:
            text, tip = labels[slot]
            btn.setText(text)
            btn.setToolTip(tip)

    # -- mods -------------------------------------------------------------

    def _refresh_mods(self) -> None:
        """List every mod the editor can reach, keeping the objects around.

        The combo's item data is the mod's directory name — that keeps
        ``findData("Test")`` working — so the resolved :class:`ModPaths` live in
        ``self._mod_index``.  A mod opened from a hand-picked folder exists
        only in that dict, which is why the object, not the name, is what
        ``_on_mod_changed`` opens.
        """
        try:
            mods = modlocator.discover()
        except modlocator.ModNotFound as exc:
            self.info.setText(str(exc))
            return
        self._mod_index = {}
        self.mod_box.blockSignals(True)
        self.mod_box.clear()
        # A placeholder keeps index 0 from being a real mod: without it, the
        # first mod is already "current", so picking it changes no index, emits
        # no signal and silently loads nothing.
        self.mod_box.addItem(tr("— 选择 mod —"), None)
        for mod in mods:
            self._add_mod_item(mod)
        self.mod_box.blockSignals(False)
        self.info.setText(
            tr("发现 {v0} 个 mod（本机 + 创意工坊）—— 选一个开始；找不到就用「打开 mod 文件夹」",
                v0=len(mods),
            ))

    def _add_mod_item(self, mod: modlocator.ModPaths) -> None:
        """Append one mod, greyed out when it has no compiled deck.

        ``deck_bin`` raises for an AGF scratch namespace that has no ``Gen/``
        yet, so probe defensively: such a mod is still listed — "why can't I
        open my mod" is the most common question — but is marked and made
        unselectable, so the reason is visible in the item itself.
        """
        try:
            has = Path(mod.deck_bin).is_file()
        except Exception:                                  # noqa: BLE001
            has = False
        label = f"{mod.label}  · {mod.source_label}"
        if not has:
            label += tr("（无牌组）")
        # Data stays the directory name when possible so a plain
        # ``findData("Test")`` keeps working; a name already taken (two mods
        # called the same thing from two sources) falls back to the id.
        key = mod.name
        if key in self._mod_index:
            key = f"{mod.source}:{mod.name}"
        self._mod_index[key] = mod
        self.mod_box.addItem(label, key if has else None)
        if not has:
            item = self.mod_box.model().item(self.mod_box.count() - 1)
            if item is not None:
                item.setEnabled(False)

    def on_open_folder(self) -> None:
        """Let the user point at a mod directory — the final fallback.

        Automatic discovery can only look where Steam and the Saved Games
        profile say to look.  A mod on a USB stick, an extracted archive, a
        second Windows account's workspace, or a Steam library whose
        ``libraryfolders.vdf`` failed to parse are all reachable through this
        one dialog, and none of them require the editor to guess.
        """
        chosen = QFileDialog.getExistingDirectory(
            self, tr("选择 Mod 部署目录（含 Gen/NDF/GFX/Deck.ndfbin 的那一层）"),
            str(modlocator.deploy_root()))
        if not chosen:
            return
        try:
            found = modlocator.discover_in_folder(Path(chosen))
        except Exception as exc:                           # noqa: BLE001
            QMessageBox.warning(self, tr("读取失败"), str(exc))
            return
        if not found:
            QMessageBox.information(
                self, tr("没找到 mod"),
                tr("{chosen}\n\n里面没有 mod：既不是含 Gen/ 或 Config.ini 的 mod 目录，子目录里也没有这样的 mod。",
                    chosen=chosen,
                ))
            return
        added: list[str] = []
        first_loadable: str | None = None
        self.mod_box.blockSignals(True)
        for mod in found:
            if mod.name in self._mod_index:
                continue
            self._add_mod_item(mod)
            added.append(mod.label)
            if first_loadable is None:
                try:
                    if Path(mod.deck_bin).is_file():
                        first_loadable = mod.name
                except Exception:                          # noqa: BLE001
                    pass
        self.mod_box.blockSignals(False)
        if not added:
            QMessageBox.information(
                self, tr("已经在列表里"), tr("这个目录里的 {v0} 个 mod 都已在下拉列表中，无需重复添加。",
                    v0=len(found),
                ))
            return
        self.info.setText(
            tr("已从 {chosen} 加入 {v1} 个 mod：", chosen=chosen, v1=len(added))
            + "、".join(added[:4]) + ("…" if len(added) > 4 else ""))
        if first_loadable is not None:
            index = self.mod_box.findData(first_loadable)
            if index > 0:
                self.mod_box.setCurrentIndex(index)

    def _on_mod_changed(self, index: int) -> None:
        key = self.mod_box.itemData(index)
        if not key:
            return
        mod = self._mod_index.get(key, key)
        try:
            self.session = open_mod(mod)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, tr("打开失败"), f"{exc}")
            traceback.print_exc()
            return
        self._divisions = self.session.divisions()
        self._countries = self.session.countries()
        self._country_of = {c.code: c for c in self._countries}
        total = len(self.session.battalions())
        # ``set_options`` notifies nobody, so the refresh below is explicit and
        # happens once, on a whole list — the country box used to need its own
        # ``blockSignals`` wrapper to keep the tree from rebuilding mid-swap.
        self.country_box.set_options(
            [(tr("全部国家  ({v0} 国 · {v1} 师 · {total} 营)",
                v0=len(self._countries),
                v1=len(self._divisions),
                total=total,
            ), None)]
            + [(c.label(), c.code) for c in self._countries])
        self._fill_division_box()

        self.log.setText("")
        cat = self.session.get_catalogue()
        self.info.setText(
            tr("{v0} 个师 · {v1} 个营 · {v2}",
                v0=len(self._divisions),
                v1=len(self.session.battalions()),
                v2=cat.stats.summary(),
            ))
        self._fill_tree()
        # Warm the unit universe *after* the tree is up, so the window stays
        # usable while the 27 MB baseline inside ``base.zip`` — and, for a mod
        # that ships one, its ``Unit.ndfbin`` — is read.  0.3-2.4 s once per
        # mod, and it is what keeps the equipment dialog from freezing on its
        # first open.
        try:
            units = self.session.get_units()
        except Exception:                                  # noqa: BLE001
            units = None
        if units is not None:
            self.info.setText(self.info.text()
                              + tr(" · 可定义单位 {v0} 个（{v1}）",
                                  v0=len(units),
                                  v1=units.summary(),
                              ))

    def current_country(self):
        """The selected :class:`~ndfbin_decks.app.session.Country`, or ``None``."""
        code = self.country_box.current_path()
        return self._country_of.get(code) if code else None

    def _on_country_changed(self) -> None:
        """Re-scope the 师 list to one country, then refresh the tree.

        This is the whole point of the picker: 233 divisions is a long list to
        hunt through, while one country's 54 (or 3) is readable.
        """
        self._fill_division_box()
        self._fill_tree()

    def _fill_division_box(self) -> None:
        """Repopulate 师 with the divisions of the selected country.

        Index 0 is always an "all divisions in scope" entry so choosing a
        country is useful on its own — "all Soviet battalions" is a real thing
        to want, and without this the picker would force a division choice.
        The default selection stays on a *division* rather than on that entry,
        because "全部师" of all 233 divisions is a 2208-battalion tree.
        """
        country = self.current_country()
        scope = country.divisions if country is not None else self._divisions
        label = (tr("全部师（{code} · {v1} 师）", code=country.code, v1=len(scope)) if country is not None
                 else tr("全部师（{v0} 师）", v0=len(scope)))
        self.div_box.set_options(
            [(label, None)]
            + [(tr("{label}  ({v1} 营)", label=d.label, v1=len(d)), d.path) for d in scope],
            select=1 if len(scope) > 1 else 0)

    # -- tree -------------------------------------------------------------

    def _matching_battalions(self):
        """The battalions the filter chain currently selects.

        Three states, in narrowing order: one 师; every 师 of the chosen 国家;
        or the whole file.  The country level exists so that "all Soviet
        battalions" is one selection instead of 54.
        """
        s = self.session
        needle = self.bn_search.text().strip().lower()
        path = self.div_box.current_path()
        if path is not None:
            pool = next((d.battalions for d in self._divisions
                         if d.path == path), [])
        else:
            country = self.current_country()
            pool = country.battalions if country is not None else s.battalions()
        if not needle:
            return list(pool)
        hits = [b for b in pool
                if needle in b.export_name.lower()
                or needle in (b.identifier or "").lower()]
        return hits[:SEARCH_CAP]

    def _fill_tree(self) -> None:
        if self.session is None:
            return
        s = self.session
        self.tree.clear()
        path = self.div_box.current_path()
        division = next((d for d in self._divisions if d.path == path), None)
        # The label comes from the division list, not from ``currentText()``:
        # the combo is editable now, so its text can be a half-typed filter
        # while ``currentData()`` still points at a real division.
        country = self.current_country()
        if division is not None:
            label = division.label
        elif country is not None:
            label = tr("{code} 全国 · {name}", code=country.code, name=country.name)
        else:
            label = tr("全部师")

        root = QTreeWidgetItem(
            self.tree, [tr("师Div {label}  ({v1} 营)", label=label, v1=len(self._matching_battalions()))])
        root.setData(0, Qt.UserRole, NodeRole("div", division=path))
        root.setExpanded(True)

        for b in self._matching_battalions():
            node = QTreeWidgetItem(root, [self._bn_label(b)])
            node.setData(0, Qt.UserRole,
                         NodeRole("bn", division=b.division,
                                  deck_id=b.deck_object_id))
            node.setToolTip(0, tr("{export_name}\n师：{v1}\n{v2} 个连",
                export_name=b.export_name,
                v1=b.division or '—',
                v2=len(b.companies),
            ))
            for ci, c in enumerate(b.companies):
                cn = QTreeWidgetItem(node, [self._co_label(b, ci, c)])
                cn.setData(0, Qt.UserRole,
                           NodeRole("co", division=b.division,
                                    deck_id=b.deck_object_id, ci=ci))
                cn.setToolTip(0, self._name_tip(c))
                for si, p in enumerate(c.platoons):
                    pn = QTreeWidgetItem(cn, [self._pl_label(b, ci, si, p)])
                    pn.setData(0, Qt.UserRole,
                               NodeRole("pl", division=b.division,
                                        deck_id=b.deck_object_id,
                                        ci=ci, si=si))
                    pn.setToolTip(0, " ".join(str(w) for w in p.windows))
                    for ei, w in enumerate(p.windows):
                        en = QTreeWidgetItem(pn, [
                            tr("#{ei} {v1}  槽位{v2}",
                                ei=ei,
                                v1=self._entry_label(b, w),
                                v2=list(w.slots()),
                            )])
                        en.setData(0, Qt.UserRole,
                                   NodeRole("entry", division=b.division,
                                            deck_id=b.deck_object_id,
                                            ci=ci, si=si, ei=ei))
                        en.setToolTip(0, tr("占槽位 {v0}，{count} 队", v0=list(w.slots()), count=w.count))
        self.tree.expandToDepth(1)

    # -- labels -----------------------------------------------------------

    def _bn_label(self, b) -> str:
        name = b.export_name
        if name.startswith("Descriptor_Deck_"):
            name = name[len("Descriptor_Deck_"):]
        return tr("营Bn {name}", name=name)

    def _co_label(self, b, ci, c) -> str:
        return tr("连Co {ci} {v1}", ci=ci, v1=self._name_or_content(b, ci, None, c))

    def _pl_label(self, b, ci, si, p) -> str:
        return tr("排Pl {si} {v1}", si=si, v1=self._name_or_content(b, ci, si, p))

    def _name_or_content(self, b, ci, si, node) -> str:
        """Dictionary name when there is one, equipment composition otherwise."""
        name = self.session.platoon_name(node.name_hash)
        if not name.startswith(tr("⟨未知")):
            return name
        return self.session.composition(b, ci, si) or name

    def _name_tip(self, node) -> str:
        name = self.session.platoon_name(node.name_hash)
        return name

    def _entry_label(self, b, w) -> str:
        pool = self.session.retiler.pool(b.deck_object_id)
        if not (0 <= w.pack_index < len(pool)):
            return "?"
        key = self.session.packs.pack_key(pool[w.pack_index])
        return f"{self.session.display(key.unit) if key else '?'} ×{w.count}"

    # -- selection --------------------------------------------------------

    def _current(self) -> NodeRole | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.UserRole)

    def _deck_of(self, role: NodeRole):
        if role is None or role.deck_id is None:
            return None
        return next((b for b in self.session.battalions()
                     if b.deck_object_id == role.deck_id), None)

    def _on_tree_select(self, *_args) -> None:
        s = self.session
        if s is None:
            return
        role = self._current()
        if role is None:
            return
        deck = self._deck_of(role)
        if deck is None:
            self.table.setRowCount(0)
            self.info.setText(tr("选一个营查看槽位"))
            return
        self.table.fill(s, deck)
        if role.kind == "co":
            # A company is not something you can swap — but showing which rows
            # belong to it tells the user how far their edit would reach.
            self.table.highlight(role.ci, None)
            self.info.setText(
                tr("连Co {ci}：{v1} 个排，{v2} 条  —— 要改装备请在右侧表里选一行",
                    ci=role.ci,
                    v1=len(deck.companies[role.ci].platoons),
                    v2=sum(len(p.windows) for p in deck.companies[role.ci].platoons),
                ))
        elif role.kind in ("pl", "entry"):
            self.table.highlight(role.ci, role.si)
            # The table row is the only thing an action acts on, so aiming at
            # a platoon in the tree has to land on a row too — otherwise the
            # toolbar would be dead until the user clicked the right panel.
            self.table.select_entry(role.ci, role.si, max(role.ei, 0))
            platoon = deck.companies[role.ci].platoons[role.si]
            slots = [x for w in platoon.windows for x in w.slots()]
            self.info.setText(
                tr("连Co {ci} 排Pl {si}：{v2} 条，占槽位 {v3}  ——  改件数会重排该营全部下游窗口",
                    ci=role.ci,
                    si=role.si,
                    v2=len(platoon.windows),
                    v3=sorted(slots),
                ))
        else:
            self.table.highlight(None, None)
            self.info.setText(
                tr("{export_name}  槽位 {v1} 个，Σ件数 {window_sum}",
                    export_name=deck.export_name,
                    v1=len(s.retiler.pool(deck.deck_object_id)),
                    window_sum=deck.window_sum,
                ))

    def _need(self, *kinds: str) -> NodeRole | None:
        """The selected node, if its kind is acceptable.

        ``pl`` implies ``entry``: aiming at a platoon or at one of its entries
        is the same intent for every edit here, so the user should not have to
        hit the leaf.
        """
        if self.session is None:
            QMessageBox.information(self, tr("先打开一个 mod"), "")
            return None
        role = self._current()
        if role is None:
            QMessageBox.information(self, tr("选择不对"), tr("请先在左侧选中一个节点"))
            return None
        if role.kind in kinds:
            return role
        if "entry" in kinds and role.kind == "pl":
            return NodeRole("entry", role.division, role.deck_id,
                            role.ci, role.si, 0)
        names = {"div": tr("师"), "bn": tr("营"), "co": tr("连"), "pl": tr("排"),
                 "entry": tr("条目")}
        QMessageBox.information(
            self, tr("选择不对"),
            tr("请先选中一个排或装备条目（当前选中的是{v0}）",
                v0=names.get(role.kind, '节点'),
            ))
        return None

    # -- actions ----------------------------------------------------------

    def on_set_count(self) -> None:
        s = self.session
        role = self._need("entry")
        if role is None:
            return
        deck = self._deck_of(role)
        platoon = deck.companies[role.ci].platoons[role.si]
        if not platoon.windows:
            QMessageBox.information(self, tr("空排"), tr("这个排没有装备条目"))
            return
        ei = min(role.ei, len(platoon.windows) - 1)
        window = platoon.windows[ei]
        text, ok = QInputDialog.getInt(
            self, tr("改成几队"),
            tr("排Pl {si} 第 {v1} 条\n当前 {count} 队，占槽位 {v3}\n\n改成：",
                si=role.si,
                v1=ei + 1,
                count=window.count,
                v3=list(window.slots()),
            ),
            window.count, 1, 99)
        if not ok:
            return
        res = s.set_count(role.deck_id, role.ci, role.si, ei, text)
        self._report(res, tr("排Pl {si} 第 {v1} 条", si=role.si, v1=ei + 1))
        self._reload()

    def on_swap(self) -> None:
        """Replace one **entry's** equipment.

        The row, not the slot, is the address.  A window owns ``count``
        consecutive slots that must all carry the same pack, so the change is
        applied to the whole window by re-laying the deck — which also means a
        swap can never leave a half-converted window behind.
        """
        s = self.session
        role = self._current()
        if role is None:
            QMessageBox.information(self, tr("先选一个营"), "")
            return
        # One selection source: the table row.  Falling back to the tree node
        # made the button act on an entry the user never picked, and opened a
        # modal picker when they expected a refusal.
        addr = self.table.selected_entry()
        if addr is None:
            QMessageBox.information(
                self, tr("先选一条装备"),
                tr("在右侧条目表里选一行（一行 = 一个装备条目，整行一起换）"))
            return
        ci, si, ei = addr
        deck = self._deck_of(role)
        if deck is None:
            return
        try:
            window = deck.companies[ci].platoons[si].windows[ei]
        except (IndexError, AttributeError):
            QMessageBox.information(self, tr("找不到这条"), tr("该条目已不存在"))
            return
        pool = s.retiler.pool(role.deck_id)
        pid = pool[window.pack_index] if 0 <= window.pack_index < len(pool) \
            else None
        cur = s.packs.pack_key(pid) if pid is not None else None
        dlg = SwapDialog(s, cur, self)
        dlg.setWindowTitle(
            tr("换装备 —— 连Co {ci} 排Pl {si} 第 {v2} 条（{count} 件，占槽位 {v4}）",
                ci=ci,
                si=si,
                v2=ei + 1,
                count=window.count,
                v4=list(window.slots()),
            ))
        if dlg.exec() != QDialog.Accepted:
            return
        want = dlg.chosen()
        if want is None:
            return
        res = s.swap_entry(role.deck_id, ci, si, ei, want)
        self._report(res, tr("换装备 cg{ci}/sg{si}/e{ei}", ci=ci, si=si, ei=ei))
        self._reload()

    def on_insert(self) -> None:
        s = self.session
        role = self._need("entry")
        if role is None:
            return
        deck = self._deck_of(role)
        platoon = deck.companies[role.ci].platoons[role.si]
        sample = platoon.entries[0].pack_object_id if platoon.entries else None
        cur = s.packs.pack_key(sample) if sample is not None else None

        dlg = SwapDialog(s, cur, self)
        dlg.setWindowTitle(tr("插入装备条目"))
        if dlg.exec() != QDialog.Accepted:
            return
        want = dlg.chosen()
        if want is None:
            return
        count, ok = QInputDialog.getInt(
            self, tr("插入装备条目"),
            tr("在排Pl {si} 里新增一条装备。\n\n件数：", si=role.si), 1, 1, 99)
        if not ok:
            return
        res = s.insert_window(role.deck_id, role.ci, role.si,
                              count=count, key=want)
        s.note_change(res, tr("插入条目 co{ci}/pl{si}", ci=role.ci, si=role.si))
        self._report(res)
        self._reload()

    def on_remove(self) -> None:
        s = self.session
        role = self._need("entry")
        if role is None:
            return
        deck = self._deck_of(role)
        platoon = deck.companies[role.ci].platoons[role.si]
        if len(platoon.windows) <= 1:
            QMessageBox.information(
                self, tr("不能删"),
                tr("这个排只剩一条装备，删掉就成了空排（官方数据没有这种形态）"))
            return
        res = s.remove_window(role.deck_id, role.ci, role.si,
                              min(role.ei, len(platoon.windows) - 1))
        self._report(res)
        self._reload()

    def on_replace(self) -> None:
        """Batch replace over 师 / 营 / 连 / 排, starting from what is selected."""
        if self.session is None:
            QMessageBox.information(self, tr("先打开一个 mod"), "")
            return
        role = self._current()
        if role is None or role.deck_id is None:
            QMessageBox.information(self, tr("先选一个营"),
                                    tr("批量替换需要一个起始营来定位范围"))
            return
        from ..edit.bulk import Scope
        # Open on the narrowest level the selection supports, so the common
        # case — "replace across this battalion" — is one click, and widening
        # to the division is a deliberate press.
        kind = {"pl": "platoon", "entry": "platoon",
                "co": "company"}.get(role.kind, "battalion")
        scope = Scope(kind=kind, division=role.division, deck_id=role.deck_id,
                      ci=role.ci if role.ci >= 0 else None,
                      si=role.si if role.si >= 0 else None,
                      country=divisions.country_code(role.division))

        dlg = ReplaceDialog(self.session, scope, self.session.display, self)
        if dlg.exec() != QDialog.Accepted:
            return
        picked = dlg.result()
        if picked is None:
            return
        scope, from_packs, to_key = picked
        res = self.session.scoped_replace(scope, from_packs).apply(to_key)
        self.session.note_change(res, tr("批量替换"))
        self._report(res, tr("批量替换（{v0}）", v0=scope.label()))
        self._reload()

    def on_save(self) -> None:
        s = self.session
        if s is None:
            return
        v = s.validate()
        if v.errors:
            QMessageBox.critical(
                self, tr("有硬错误，不能保存"), self._error_report(v))
            return
        target, _ = QFileDialog.getSaveFileName(
            self, tr("保存到"), str(s.path), "Deck.ndfbin (*.ndfbin)")
        if not target:
            return
        target = Path(target)
        if target == s.path:
            backup = s.path.with_suffix(".ndfbin.bak")
            backup.write_bytes(s.path.read_bytes())
        try:
            data = s.save(target)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, tr("保存失败"), str(exc))
            traceback.print_exc()
            return
        QMessageBox.information(self, tr("已保存"), tr("{target}\n{v1:,} 字节", target=target, v1=len(data)))

    #: Findings spelled out in full before the report switches to a summary.
    #: A single damaged deck can produce dozens; listing them all buries the
    #: one line the reader needs.
    _ERRORS_SHOWN = 5

    @staticmethod
    def _deck_name(where: str) -> str:
        """``Descriptor_Deck_X/3/1`` → ``X``."""
        name = where.split("/")[0]
        return name[len("Descriptor_Deck_"):] if name.startswith("Descriptor_Deck_") else name

    def _where_label(self, where: str) -> str:
        """``Descriptor_Deck_X/3/1`` → ``X · 连3/排1``.

        The validator's ``where`` already carries the deck, company and platoon
        an issue belongs to; it was simply never shown.  Rendered with the
        tree's own vocabulary so the reader can go straight to that node.
        """
        parts = where.split("/")
        name = self._deck_name(where)
        if len(parts) >= 3:
            return tr("{v1} · 连{v2}/排{v3}", v1=name, v2=parts[1], v3=parts[2])
        if len(parts) == 2:
            return tr("{v1} · 连{v2}", v1=name, v2=parts[1])
        return name

    def _error_report(self, v) -> str:
        """Message body for a refused save: what, where, and how much.

        ``on_save`` blocks the whole file on any hard error, so the two things
        the reader needs are *which deck* to go and repair and *how many* others
        are waiting behind it.  Neither was in the old dialog, which printed
        bare messages and stopped at five.
        """
        lines = [f"{i.message}\n    — {self._where_label(i.where)}" if i.where
                 else i.message
                 for i in v.errors[:self._ERRORS_SHOWN]]
        if len(v.errors) > self._ERRORS_SHOWN:
            lines.append(tr("…另有 {v1} 条未显示", v1=len(v.errors) - self._ERRORS_SHOWN))

        decks = sorted({self._deck_name(i.where) for i in v.errors if i.where})
        body = "\n".join(lines)
        body += "\n\n" + tr("共 {v1} 条硬错误，涉及 {v2} 个营：",
                            v1=len(v.errors), v2=len(decks))
        body += "\n" + "\n".join(decks[:self._ERRORS_SHOWN])
        if len(decks) > self._ERRORS_SHOWN:
            body += "\n" + tr("…等 {v1} 个", v1=len(decks))
        return body

    # -- helpers ----------------------------------------------------------

    def _node_for(self, deck_id: int, ci: int = -1, si: int = -1):
        """Locate a tree node by organisation coordinates.

        Needed because :meth:`_fill_tree` rebuilds every node: the
        ``QTreeWidgetItem`` a caller held before an edit is a dangling pointer
        afterwards, and using it is a hard crash rather than a stale read.
        """
        root = self.tree.topLevelItem(0)
        if root is None:
            return None
        for i in range(root.childCount()):
            bn = root.child(i)
            role = bn.data(0, Qt.UserRole)
            if role is None or role.deck_id != deck_id:
                continue
            if ci < 0:
                return bn
            if bn.childCount() <= ci:
                return None
            co = bn.child(ci)
            if si < 0:
                return co
            return co.child(si) if co.childCount() > si else None
        return None

    def _reload(self) -> None:
        """Refresh the tree and table from the mutated graph, keeping the place.

        Rebuilding the tree drops the selection, which would leave the user
        staring at the top of the list after every edit; the coordinates are
        therefore remembered and the node re-located afterwards.  Restoring it
        re-fires :meth:`_on_tree_select`, so the table refreshes too.
        """
        if self.session is None:
            return
        role = self._current()
        addr = self.table.selected_entry()
        self._fill_tree()
        if role is not None and role.deck_id is not None:
            node = self._node_for(role.deck_id, role.ci, role.si)
            if node is not None:
                self.tree.setCurrentItem(node)
            else:
                deck = self._deck_of(role)
                if deck is not None:
                    self.table.fill(self.session, deck)
        if addr is not None:
            self.table.select_entry(*addr)
        self._refresh_log()

    def _refresh_log(self) -> None:
        s = self.session
        if s is None:
            return
        tail = s.log[-4:]
        mark = tr("● 已修改，未保存") if s.dirty else tr("○ 未修改")
        self.log.setText(mark + ("\n" + "\n".join(tail) if tail else ""))

    def _report(self, res, what: str = "") -> None:
        head = f"{what}\n\n" if what else ""
        if getattr(res, "ok", False):
            warns = getattr(res, "warnings", []) or []
            msg = head + res.summary()
            if warns:
                msg += tr("\n\n提示：\n") + "\n".join(f"· {w}" for w in warns[:3])
            QMessageBox.information(self, tr("完成"), msg)
        else:
            QMessageBox.warning(self, tr("被拒绝"), head + (res.note or tr("未知原因")))
        self._refresh_log()


def main() -> int:
    app = QApplication(sys.argv)
    # ``setApplicationName`` labels the process for the shell and for
    # ``QSettings``; ``setApplicationDisplayName`` is deliberately **not** used,
    # because Qt appends that to every window title and the title is already
    # the tagline the taskbar is supposed to show.
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = paths.icon_path()
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    # Before any window exists, so every label built afterwards — including the
    # module-level ones, which see the language at import time — comes out right
    # without a second pass.
    code = settings.saved_language()
    i18n.set_language(code)
    qt_builtin.install(app, code)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
