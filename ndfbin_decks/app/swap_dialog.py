"""The equipment dialog: pick an existing pack, or build a combination.

Two questions, one dialog — because the user often does not know in advance
which one they are asking:

* **"换成这个"** — pick a pack object the open ``Deck.ndfbin`` already contains.
* **"造一个这个"** — build a ``(Unit, Transporter, Experience)`` combination the
  file does not have yet, which means **appending a** ``TDeckPackDescriptor``
  when the deck is saved.

The second one is why this dialog got a lower half.  Before it, the only way to
reach a pack was through :class:`~ndfbin_decks.app.catalog.PackCatalogue`, which
by construction only ever offers packs the file *already* references — so a unit
the mod defines in its ``Unit.ndfbin`` but has never fielded was unreachable,
and typing its name was refused with "本 mod 里没有".  That refusal was correct
for the picker and wrong for the job: the field was being asked to do something
the engine could already do.

What did **not** change is the boundary.  Both halves still draw from one file:

* the dropdowns list values drawn from this file's own packs;
* a hand-typed ``$/GFX/Unit/...`` path that this file has never referenced is
  allowed, but only after an explicit confirmation — it can only be legitimate
  if the mod's own ``Unit.ndfbin`` defines it, and a wrong guess produces an
  empty unit **silently**, with no error at load time;
* nothing is written to disk here.  :meth:`~ndfbin_decks.app.session.Session.save`
  is still the only thing that touches the file.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QVBoxLayout, QWidget,
)

from ..core.deckmodel import unit_leaf
from ..edit.equip import PackKey
from .catalog import PackCatalogue, parse_unit_path
from ..i18n import live, tr

#: The 经验 selector.  ``None`` means "inherit the division's level", which is
#: the default: 311 of Test's 2690 AG packs write no ``ExperienceLevel`` and are
#: load-bearing, so an absent level is a legitimate choice rather than a gap.
EXPERIENCE_CHOICES = live(globals(), "EXPERIENCE_CHOICES", lambda: ((tr("继承（不写 Xp）"), None), (tr("经验 1"), 1),
                      (tr("经验 2"), 2), (tr("经验 3"), 3)))

NO_TRANSPORT = live(globals(), "NO_TRANSPORT", lambda: tr("— 无 —"))

#: Separator between a unit name and its provenance tag in the dropdowns
#: (``FOB_BEL  · 本mod``).  Two spaces and a middle dot, which no unit leaf can
#: contain, so stripping the tag back off is unambiguous.
TAG_SEP = "  · "


def _plain(text: str) -> str:
    """``"Su_27K_SOV  · 原版"`` -> ``"Su_27K_SOV"``."""
    return (text or "").split(TAG_SEP, 1)[0].strip()


def _sort_key(path: str) -> tuple[str, str]:
    return (unit_leaf(path).lower(), path)

#: What :meth:`SwapDialog.evaluate` concluded about the current combination.
STATE_REUSE = "reuse"      # an AG-proven pack with this content already exists
STATE_CREATE = "create"    # new combination, safe to append
STATE_CONFIRM = "confirm"  # new *and* built on a path this file never referenced
STATE_INVALID = "invalid"  # nothing usable typed yet


class SwapDialog(QDialog):
    """Pick the pack an entry should point at — existing or brand new."""

    def __init__(self, session, current: PackKey | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("换装备 · 新建装备组合"))
        self.resize(760, 700)
        self._session = session
        self._catalogue: PackCatalogue = session.get_catalogue()
        # Every unit this mod can field — from the mod's own Unit.ndfbin, the
        # ModGen baseline, and whatever this file already references.  Lazy
        # inside the session, and cached after the first dialog on a mod.
        self._universe = session.get_units()
        # Cached because ``evaluate`` runs on every keystroke and both are
        # derived from a 2690-row list.
        self._ag_units = set(self._catalogue.units())
        self._ag_transporters = set(self._catalogue.transporters())
        self._current = current
        self._choices: list = []
        self._result: PackKey | None = None
        #: ``True`` once the user edits the lower half — then that half, not the
        #: list selection, is what a press of OK means.
        self._manual = False
        #: guards the list -> fields copy from looking like a user edit
        self._syncing = False

        root = QVBoxLayout(self)

        # Created before the builder, because building the two dropdowns ends in
        # an ``_evaluate`` that writes the status line.
        self.hint = QLabel("")
        self.hint.setWordWrap(True)

        root.addWidget(self._build_intro())
        root.addWidget(self._build_picker(), stretch=1)
        root.addWidget(self._build_separator())
        root.addWidget(self._build_builder())
        root.addWidget(self.hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_list()
        self._preselect()
        self._evaluate()

    # -- construction ------------------------------------------------------

    def _build_intro(self) -> QWidget:
        label = QLabel(
            tr("已有装备包 {v0} 个（下面第 1 区）｜可定义单位 {v1} 个（下面第 2 区的下拉）\n{v2}\n牌组来源：{source_path}",
                v0=len(self._catalogue),
                v1=len(self._universe),
                v2=self._universe.summary(),
                source_path=self._catalogue.source_path,
            ))
        label.setWordWrap(True)
        return label

    def _build_picker(self) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)

        head = QLabel(tr("1 &nbsp;从本 mod 已有的装备包里选"))
        head.setTextFormat(Qt.RichText)
        col.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("搜索：")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("装备名 / 运输机 / pack 编号 / 经验3"))
        self.search.textChanged.connect(self._refresh_list)
        row.addWidget(self.search, stretch=1)
        self.group_box = QComboBox()
        self.group_box.addItem(tr("全部分组"))
        self.group_box.addItems(self._catalogue.groups())
        self.group_box.currentIndexChanged.connect(self._refresh_list)
        row.addWidget(self.group_box)
        col.addLayout(row)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_pick)
        col.addWidget(self.list, stretch=1)
        return box

    def _build_separator(self) -> QWidget:
        label = QLabel(tr("或者 ↓ &nbsp;在下面造一个本 mod 还没有的组合"
                       "（单位 + 载具 + 经验）"))
        label.setTextFormat(Qt.RichText)
        label.setStyleSheet("color: palette(mid); padding-top: 4px;")
        return label

    def _build_builder(self) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)

        self.unit_box = self._nameable_combo()
        grid.addWidget(QLabel(tr("单位（可手输）")), 0, 0)
        grid.addWidget(self.unit_box, 1, 0)

        self.trans_box = self._nameable_combo(none_label=NO_TRANSPORT)
        grid.addWidget(QLabel(tr("载具（可留空）")), 0, 1)
        grid.addWidget(self.trans_box, 1, 1)

        self.xp_box = QComboBox()
        for text, value in EXPERIENCE_CHOICES:
            self.xp_box.addItem(text, value)
        grid.addWidget(QLabel(tr("经验")), 0, 2)
        grid.addWidget(self.xp_box, 1, 2)

        self.only_proven = QCheckBox(tr("只列出将军模式已引用过的（默认列出全部可定义单位）"))
        self.only_proven.setToolTip(
            tr("关（默认）：列出本 mod 能引用的全部单位 —— 原版数据 + 本 mod 的\n"
            "Unit.ndfbin + 本文件已引用过的。开着：只列出本文件的将军模式牌组\n"
            "已经用过的，最保守。"))
        self.only_proven.stateChanged.connect(self._fill_unit_combos)
        grid.addWidget(self.only_proven, 2, 0, 1, 3)

        self.unit_box.currentTextChanged.connect(self._on_field_edited)
        self.trans_box.currentTextChanged.connect(self._on_field_edited)
        self.xp_box.currentIndexChanged.connect(self._on_field_edited)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.PlainText)
        self.preview.setStyleSheet(
            "font-family: monospace; color: palette(mid);"
            "border: 1px solid palette(mid); padding: 4px;")
        grid.addWidget(self.preview, 3, 0, 1, 3)

        self._fill_unit_combos()
        return box

    def _nameable_combo(self, none_label: str | None = None) -> QComboBox:
        """An editable combo with a contains-matching popup.

        Still editable even now that the list comes from the definition files:
        a mod can name a unit in its own ``Unit.ndfbin`` that this editor has no
        way to see, and typing its name must stay possible.
        """
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.lineEdit().setPlaceholderText(tr("输入名字，或从下拉里选"))
        if none_label is not None:
            combo.addItem(none_label, "")
        completer = combo.completer()
        if completer is not None:
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
        return combo

    def unit_items(self) -> list[tuple[str, str]]:
        """``(path, tag)`` for every unit this mod may field, name-sorted.

        The tag says where the unit comes from and is blank for the one class
        that needs no explaining — a unit this file's own AG decks already
        field.  Everything else is spelled out, because "why is this one
        different" is the question the list has to answer.
        """
        proven = self._ag_units
        if self.only_proven.isChecked():
            return [(p, "") for p in sorted(proven, key=_sort_key)]
        out: dict[str, str] = {}
        for path in self._universe.all:
            out[path] = "" if path in proven else self._universe.origin(path)
        for path in proven:                 # a file with no resolvable mod
            out.setdefault(path, "")
        return sorted(out.items(), key=lambda pt: _sort_key(pt[0]))

    def _fill_unit_combos(self) -> None:
        """Repopulate both dropdowns, keeping whatever the user has typed."""
        items = self.unit_items()
        keep_unit = self.unit_box.currentText()
        keep_trans = self.trans_box.currentText()
        self._syncing = True
        try:
            self._reload_combo(self.unit_box, items)
            self._reload_combo(self.trans_box, items)
            self.unit_box.setCurrentText(keep_unit)
            self.trans_box.setCurrentText(keep_trans)
        finally:
            self._syncing = False
        self._evaluate()

    @staticmethod
    def _reload_combo(combo: QComboBox, items: list[tuple[str, str]]) -> None:
        has_none = combo.itemData(0) == "" and combo.itemText(0) == NO_TRANSPORT
        combo.blockSignals(True)
        combo.clear()
        if has_none:
            combo.addItem(NO_TRANSPORT, "")
        for path, tag in items:
            label = unit_leaf(path)
            combo.addItem(f"{label}  · {tag}" if tag else label, path)
        combo.blockSignals(False)

    # -- the picker half ---------------------------------------------------

    def _refresh_list(self) -> None:
        group = self.group_box.currentText()
        pool = (self._catalogue.in_group(group)
                if group and group != tr("全部分组") else self._catalogue.items)
        text = self.search.text().strip()
        self._choices = (self._catalogue.search(text, limit=500) if text
                         else pool[:500])
        self.list.clear()
        for c in self._choices:
            item = QListWidgetItem(c.label())
            item.setToolTip(
                tr("pack#{pack_object_id}\n{unit}\n将军模式中 {uses} 个条目在用",
                    pack_object_id=c.pack_object_id,
                    unit=c.unit,
                    uses=c.uses,
                ))
            self.list.addItem(item)

    def _preselect(self) -> None:
        """Show what the entry carries now, in both halves."""
        if self._current is None:
            return
        hit = self._catalogue.owns_key(self._current)
        if hit is None:
            # The current pack may be one the AG gate excludes — show it at the
            # top anyway, so the user can see what they are replacing.
            self._fill_fields(self._current)
            item = QListWidgetItem("◆ " + self._current.label())
            self.list.insertItem(0, item)
            return
        for row, c in enumerate(self._choices):
            if c.pack_object_id == hit.pack_object_id:
                self.list.setCurrentRow(row)
                return
        item = QListWidgetItem("◆ " + hit.label())
        self.list.insertItem(0, item)
        self._choices.insert(0, hit)
        self.list.setCurrentRow(0)

    def _on_pick(self, row: int) -> None:
        if not (0 <= row < len(self._choices)):
            return
        choice = self._choices[row]
        # Picking a row means "this one, as it is" — so the lower half follows
        # it and stops being an independent answer.
        self._manual = False
        self._fill_fields(choice.key)
        self._evaluate()

    def _fill_fields(self, key: PackKey) -> None:
        self._syncing = True
        try:
            self._select_path(self.unit_box, key.unit)
            self._select_path(self.trans_box, key.transporter)
            index = self.xp_box.findData(key.experience)
            self.xp_box.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._syncing = False

    @staticmethod
    def _select_path(combo: QComboBox, path: str | None) -> None:
        """Point a nameable combo at ``path``, tag and all.

        Setting the index rather than the text keeps the provenance tag
        visible, so the lower half says where the combination's unit comes
        from without the user having to open the dropdown.
        """
        if not path:
            combo.setCurrentText(NO_TRANSPORT if combo.count()
                                 and combo.itemText(0) == NO_TRANSPORT
                                 else "")
            return
        for row in range(combo.count()):
            if combo.itemData(row) == path:
                combo.setCurrentIndex(row)
                return
        combo.setCurrentText(unit_leaf(path))

    # -- the builder half --------------------------------------------------

    def _on_field_edited(self, *_args) -> None:
        if self._syncing:
            return
        self._manual = True
        # Blocked, or clearing the row would re-enter ``_on_pick`` and undo the
        # edit that has just been made.
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self._evaluate()

    def _combo_path(self, combo: QComboBox) -> str | None:
        """The ``$/…`` path a nameable combo denotes, or ``None``.

        Picking from the list is preferred over parsing the text, because the
        visible label carries a provenance tag (``FOB_BEL  · 本mod``) that is
        not part of the name.  Parsing stays as the fallback so a hand-typed
        name that is in no list at all is still reachable.
        """
        text = _plain(combo.currentText())
        if not text or text == NO_TRANSPORT:
            return None
        data = combo.currentData()
        if isinstance(data, str) and data.startswith("$/") \
                and text == unit_leaf(data):
            return data
        return parse_unit_path(text)

    def _typed_unit(self) -> str | None:
        return self._combo_path(self.unit_box)

    def _typed_transporter(self) -> str | None:
        return self._combo_path(self.trans_box)

    def evaluate(self) -> tuple[PackKey | None, str, str]:
        """``(key, state, message)`` for whatever the two halves now say.

        Split out from :meth:`_evaluate` so a headless test can ask the same
        question the dialog asks itself.
        """
        if not self._manual:
            row = self.list.currentRow()
            if 0 <= row < len(self._choices):
                choice = self._choices[row]
                return (choice.key, STATE_REUSE,
                        tr("✓ 将换成 pack#{pack_object_id} —— {v1}",
                            pack_object_id=choice.pack_object_id,
                            v1=choice.label(),
                        ))
            return (None, STATE_INVALID, tr("从上面的列表里选一条，或者填写下面的组合"))

        unit = self._typed_unit()
        if unit is None:
            return (None, STATE_INVALID, tr("先填一个单位名"))
        transporter = self._typed_transporter()
        if transporter is not None and transporter == unit:
            return (None, STATE_INVALID, tr("载具不能和单位是同一个"))

        key = PackKey(unit=unit, transporter=transporter,
                      experience=self.xp_box.currentData(), number=1)

        ag_hit = self._catalogue.find_exact(key)
        if ag_hit is not None:
            return (key, STATE_REUSE,
                    tr("✓ 本文件里已有这个组合 —— 将复用 pack#{pack_object_id}（不新建对象）",
                        pack_object_id=ag_hit.pack_object_id,
                    ))

        # What ``ensure_pack`` will actually find, which searches the *whole*
        # pack pool rather than only the AG-proven part of it.
        anywhere = self._session.packs.index.find(key)
        if anywhere is not None:
            return (key, STATE_CONFIRM,
                    tr("⚠ 这个组合本文件里只有 pack#{anywhere}，而且只被 PvP/行动模式引用过 —— 将军模式从未用过它。\n   仍要使用请确认。",
                        anywhere=anywhere,
                    ))

        # The gate is now "is this path in the load order at all" — answered
        # from the mod's ``Unit.ndfbin``, ModGen's baseline, and this file's own
        # references.  It is deliberately *not* the AG pack gate any more: that
        # one answers "has an AG deck fielded it", which refused 2898-2100
        # vanilla units such as ``Su_27K_SOV`` for no good reason.
        unknown: list[str] = []
        if not self._universe.knows(unit):
            unknown.append(unit_leaf(unit))
        if transporter is not None and not self._universe.knows(transporter):
            unknown.append(unit_leaf(transporter))
        if unknown:
            return (key, STATE_CONFIRM,
                    tr("⚠ 这些名字在任何定义文件里都找不到：{v0}\n   已查过：本 mod 的 Unit.ndfbin、原版数据（base.zip）、以及本文件引用过的全部单位。\n   如果它是本 mod 后来才定义、而这个编辑器读不到的，可以继续；否则游戏里会是空装备，而且不会报错。\n   确定要用吗？",
                        v0=' / '.join(unknown),
                    ))

        # Known, but never fielded by this file's AG decks — worth saying, not
        # worth blocking.  It is the common case for the units this change
        # exists to unlock.
        if unit not in self._ag_units:
            origin = self._universe.origin(unit)
            why = {
                tr("原版"): tr("原版单位"),
                tr("本mod"): tr("本 mod 定义的单位"),
                tr("本文件"): tr("本文件里有 pack 用它，但都不是将军模式牌组"),
            }.get(origin, tr("定义文件里查不到来源"))
            return (key, STATE_CREATE,
                    tr("＋ 新组合 —— {v0} 是{why}；\n   本文件的将军模式数据还没引用过它，保存时会新建一个 pack 对象。",
                        v0=unit_leaf(unit),
                        why=why,
                    ))
        return (key, STATE_CREATE,
                tr("＋ 新组合 —— 保存时会新建一个 pack 对象（复用现有的 {v0} 与载具）",
                    v0=unit_leaf(unit),
                ))

    def _evaluate(self) -> None:
        key, state, message = self.evaluate()
        self.hint.setText(message)
        if key is None:
            self.preview.setText("")
            return
        lines = ["TDeckPackDescriptor"]
        if key.unit:
            lines.append(f"  Unit        = {key.unit}")
        if key.transporter:
            lines.append(f"  Transport   = {key.transporter}")
        lines.append("  Xp          = "
                     + (tr("（不写 · 继承师的经验）") if key.experience is None
                        else str(key.experience)))
        self.preview.setText("\n".join(lines))

    # -- result ------------------------------------------------------------

    def accept(self) -> None:
        """Confirm the confirmation-worthy cases *before* closing.

        An unreferenced path is the one way this dialog can produce a pack the
        game cannot render, and it does so silently.  Asking here — rather than
        refusing outright — keeps the "build equipment the mod defines but has
        not fielded" workflow reachable, which is the whole point of the lower
        half.
        """
        key, state, message = self.evaluate()
        if key is None:
            QMessageBox.information(self, tr("还不能确定"), message)
            return
        if state == STATE_CONFIRM:
            answer = QMessageBox.warning(
                self, tr("需要确认"), message + tr("\n\n继续吗？"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._result = key
        super().accept()

    def chosen(self) -> PackKey | None:
        """The pack key to apply, or ``None`` when cancelled."""
        return self._result
