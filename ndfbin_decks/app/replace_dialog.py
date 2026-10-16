"""Replace one equipment across a chosen level of the organisation.

Why this is a dialog of its own rather than another button: **replace is not
pick**.  Picking touches one entry; replacing rewrites every entry in the scope
that uses the same equipment.  Before pressing the button the modder has to see
the blast radius — how many battalions, companies, platoons, entries and
individual units move — so the dialog shows it live and recomputes it on every
change of scope or target.

The scope level is four mutually exclusive buttons, widest last, because
widening the scope is the whole point of the feature and must be impossible to
do by accident.  The buttons carry the same 师/营/连/排 vocabulary as the tree,
so the dialog and the tree can never disagree about what a level means.

The left column is **multi-select**: "make every T-72 variant in this division
a T-72B" is one request, and a modder who wants that should not have to run the
dialog three times.  Checkboxes rather than Ctrl-click, because a list that
only looks single-selection gives no hint that more than one can be taken, and
an accidental click elsewhere silently throws the selection away.  Choosing the
target among the sources means "fold the others into it" — the target is dropped
from the set and the exclusion is stated, never applied as a silent no-op.

The picker on the right is the same :class:`~.swap_dialog.SwapDialog` used for a
single swap — one equipment list, one AG-provenance gate, no second place where
"which packs are legal" could drift.

**Filtering is a view, not a selection.**  The search box hides rows; it never
unticks one.  A wide scope lists four hundred kinds of equipment and the modder
hunts through them by name, but the moment a filter also dropped ticks, typing a
letter would quietly shrink a half-built request — and the blast-radius figure
would follow it down without anything on screen saying why.  So a hidden row
keeps its tick, the count keeps counting it, and 全选 is the one control that
*is* bounded by the filter, because "take everything I can see" is exactly what
someone who just narrowed the list means.  清空 ignores the filter and clears
everything, so there is always one way back to a clean slate.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

# ``LEVEL_LABELS`` comes from the same module as ``LEVELS``: a second copy was
# how the 国家 level ended up with a button and no label.
from ..edit.bulk import LEVELS, LEVEL_LABELS, Scope
from .swap_dialog import SwapDialog
from ..i18n import tr


class ReplaceDialog(QDialog):
    """Choose *what* to replace, *with what*, and *how far* it reaches."""

    def __init__(self, session, scope: Scope, display, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("批量替换装备"))
        self.resize(1040, 680)
        self.session = session
        self.display = display
        self._entry_scope = scope
        self._level = scope.kind if scope.kind in LEVELS else "battalion"
        #: pack ids ticked in the left column — a *set*, see the module note
        self._checked: set[int] = set()
        self._to_key = None
        self._to_label = ""
        #: one row per *kind of equipment*, see :meth:`_reload_from`
        self._rows: list[dict] = []
        #: true while :meth:`_reload_from` fills the list, so the checkbox
        #: callback cannot fire a survey against a half-built list
        self._loading = False

        root = QVBoxLayout(self)

        intro = QLabel(
            tr("把所选<b>范围</b>内用到左栏装备的每一条，一次性换成右栏装备。<br>"
            "<b>件数与槽位顺序不变</b>——游戏仍按原来的数量派兵，只是换了装备；"
            "因此该操作不会产生非法布局。"))
        intro.setWordWrap(True)
        root.addWidget(intro)

        root.addWidget(self._build_scope_box())

        columns = QHBoxLayout()
        columns.addLayout(self._build_from_column(), 1)
        columns.addLayout(self._build_middle(), 0)
        columns.addLayout(self._build_to_column(), 1)
        root.addLayout(columns, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_btn = buttons.button(QDialogButtonBox.Ok)
        self.ok_btn.setText(tr("替换"))
        self.ok_btn.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._apply_level(self._level, initial=True)

    # -- widgets -----------------------------------------------------------

    def _build_scope_box(self) -> QWidget:
        box = QGroupBox(tr("范围"))
        row = QHBoxLayout(box)
        row.addWidget(QLabel(tr("替换范围：")))
        self._level_btns: dict[str, QPushButton] = {}
        for kind in LEVELS:
            btn = QPushButton(LEVEL_LABELS[kind])
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setToolTip(tr("影响范围：{v0} 之内的全部条目", v0=LEVEL_LABELS[kind]))
            if kind == "country":
                # The country is read off the division label, so a division
                # without one has no country to widen to.  Disabled rather
                # than hidden: the level exists, it just cannot apply *here*.
                country = self._entry_scope.country
                if country:
                    btn.setToolTip(
                        tr("影响范围：{v0} —— {country} 的全部师，共 {v2} 个",
                            v0=LEVEL_LABELS[kind],
                            country=country,
                            v2=len(self._level_scope_divisions(country)),
                        ))
                else:
                    btn.setEnabled(False)
                    btn.setToolTip(
                        tr("起始营所在师没有可识别的国家前缀（例如未标师的营），"
                        "无法按国家替换"))
            btn.clicked.connect(lambda _c=False, k=kind: self._apply_level(k))
            row.addWidget(btn)
            self._level_btns[kind] = btn
        row.addStretch(1)
        self.scope_label = QLabel("")
        row.addWidget(self.scope_label)
        return box

    def _build_from_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(QLabel(tr("<b>范围内用到的装备（可多选）</b>")))
        self.from_search = QLineEdit()
        self.from_search.setPlaceholderText(
            tr("输入字符缩小待选范围，例如 T72 / Igla / 经验2"))
        self.from_search.setClearButtonEnabled(True)
        self.from_search.setToolTip(
            tr("只影响这个列表看得见什么，不影响已勾选的装备。\n"
               "匹配整行文字：装备名、运输载具、经验等级都算。"))
        self.from_search.textChanged.connect(self._apply_filter)
        col.addWidget(self.from_search)
        self.from_list = QListWidget()
        self.from_list.setAlternatingRowColors(True)
        # Selection still tracks one row — the target picker uses it to open on
        # something sensible — but it is the *checkbox* that decides what a
        # replace will touch.
        self.from_list.currentItemChanged.connect(
            lambda *_: self._refresh_summary())
        self.from_list.itemChanged.connect(self._on_check)
        col.addWidget(self.from_list, 1)

        picks = QHBoxLayout()
        self.all_btn = QPushButton(tr("全选"))
        self.none_btn = QPushButton(tr("清空"))
        # 全选 follows the filter, 清空 never does — stated on the buttons
        # themselves, because the two behaving differently is the part that
        # would otherwise have to be guessed.
        self.all_btn.setToolTip(tr("勾选当前列表里看得见的全部装备（搜索有筛选时只勾可见的）"))
        self.none_btn.setToolTip(tr("取消全部勾选，包括被搜索隐藏的"))
        self.all_btn.clicked.connect(lambda: self._set_all(True))
        self.none_btn.clicked.connect(lambda: self._set_all(False))
        picks.addWidget(self.all_btn)
        picks.addWidget(self.none_btn)
        picks.addStretch(1)
        col.addLayout(picks)

        self.from_count = QLabel("")
        col.addWidget(self.from_count)
        return col

    def _set_all(self, on: bool) -> None:
        # ``_loading`` mutes ``_on_check``, so the ticking has to record itself
        # here — otherwise 全选 would paint every row and still report "已勾选
        # 0 种", a button that visibly does nothing.
        self._loading = True
        if not on:
            # 清空 means "all of it", so it deliberately reaches past the
            # filter.  Leaving hidden rows ticked would keep a number on screen
            # that the user has no way to see, let alone clear.
            self._checked.clear()
        for row in range(self.from_list.count()):
            item = self.from_list.item(row)
            if on and item.isHidden():
                continue
            item.setCheckState(Qt.Checked if on else Qt.Unchecked)
            if on:
                self._checked.update(item.data(Qt.UserRole))
        self._loading = False
        self._refresh_summary()

    def _apply_filter(self, _text: str = "") -> None:
        """Hide the rows the search box does not match.

        Nothing is ticked or unticked here — see the module docstring.  The
        current row *is* moved, because the target picker opens on it and
        opening on a row the user cannot see would pick the wrong equipment.
        """
        needle = self.from_search.text().strip().lower()
        first_visible = -1
        for row in range(self.from_list.count()):
            item = self.from_list.item(row)
            hit = not needle or needle in item.text().lower()
            item.setHidden(not hit)
            if hit and first_visible < 0:
                first_visible = row
        current = self.from_list.currentRow()
        if first_visible >= 0 and (
                current < 0 or self.from_list.item(current).isHidden()):
            self.from_list.setCurrentRow(first_visible)
        self._refresh_summary()

    def _visible_rows(self) -> int:
        return sum(1 for row in range(self.from_list.count())
                   if not self.from_list.item(row).isHidden())

    def _on_check(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        ids = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._checked.update(ids)
        else:
            self._checked.difference_update(ids)
        self._refresh_summary()

    def _build_middle(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addStretch(1)
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignCenter)
        col.addWidget(arrow)
        col.addStretch(1)
        return col

    def _build_to_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(QLabel(tr("<b>换成</b>")))
        self.to_btn = QPushButton(tr("选择装备…"))
        self.to_btn.clicked.connect(self._on_pick_target)
        col.addWidget(self.to_btn)
        self.to_label = QLabel(tr("<i>尚未选择</i>"))
        self.to_label.setWordWrap(True)
        col.addWidget(self.to_label)
        col.addStretch(1)
        return col

    # -- scope -------------------------------------------------------------

    def _level_scope_divisions(self, country: str) -> list:
        """The divisions a country-wide replace would cover."""
        return [d for d in self.session.divisions() if d.country == country]

    def _scope_for(self, kind: str) -> Scope:
        """The entry scope widened (or narrowed) to ``kind``.

        The coordinates come from wherever the user was in the tree, so opening
        on a platoon and widening to 师 still targets *that* division rather
        than the first one in the list.
        """
        e = self._entry_scope
        return Scope(kind=kind, division=e.division, deck_id=e.deck_id,
                     ci=e.ci, si=e.si, country=e.country)

    def _apply_level(self, kind: str, *, initial: bool = False) -> None:
        self._level = kind
        btn = self._level_btns.get(kind)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self.scope_label.setText(tr("当前：{v0}", v0=LEVEL_LABELS[kind]))
        self._reload_from()

    def _reload_from(self) -> None:
        scope = self._scope_for(self._level)
        executor = self.session.scoped_replace(scope, -1)
        # Grouped by *content*, not by pack object.  A file routinely carries
        # two pack objects with the same signature — one hand-written, one
        # emitted by the game — and listing them separately shows the modder two
        # rows of "T-72B" with no way to tell which is which, or why ticking one
        # left half the tanks behind.  They are the same equipment; the row
        # stands for all of them and ticking it takes all of them.
        groups: list[dict] = []
        by_key: dict = {}
        for pack_id, entries, units in executor.usage():
            key = self.session.packs.pack_key(pack_id)
            slot = by_key.get(key) if key is not None else None
            if slot is None:
                slot = {"key": key, "ids": [], "entries": 0, "units": 0}
                if key is not None:
                    by_key[key] = slot
                groups.append(slot)
            slot["ids"].append(pack_id)
            slot["entries"] += entries
            slot["units"] += units
        self._rows = groups

        self._loading = True
        self.from_list.clear()
        for row in self._rows:
            key, ids = row["key"], row["ids"]
            name = self.display(key.unit) if key else f"pack#{ids[0]}"
            bits = [name]
            if key and key.transporter:
                bits.append(f"[{self.display(key.transporter)}]")
            if key and key.experience is not None:
                bits.append(tr("经验{experience}", experience=key.experience))
            bits.append(tr("— {v0} 条 / {v1} 件", v0=row['entries'], v1=row['units']))
            item = QListWidgetItem(" ".join(bits))
            item.setData(Qt.UserRole, tuple(ids))
            shown = "、".join(f"pack#{p}" for p in ids[:4])
            if len(ids) > 4:
                shown += tr(" 等 {v0} 个", v0=len(ids))
            item.setToolTip(tr("{shown}\n范围内 {v1} 条装备，共 {v2} 件",
                shown=shown,
                v1=row['entries'],
                v2=row['units'],
            ))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            # Carried across a change of level: widening the scope from 营 to
            # 师 keeps ticking whatever is still there, so the user widens a
            # request instead of rebuilding it.
            item.setCheckState(
                Qt.Checked if any(p in self._checked for p in ids)
                else Qt.Unchecked)
            self.from_list.addItem(item)
        self._loading = False
        # Anything the narrower scope had ticked but this one does not field
        # stays in ``_checked``; dropping it here would silently shrink the
        # request, and re-surveying would not notice.
        if self._rows:
            self.from_list.setCurrentRow(0)
        # The list is brand new, so the filter has to be laid over it again.
        # The search text itself is deliberately kept: widening the scope from
        # 营 to 师 should not also throw away the name being hunted for.
        self._apply_filter()

    # -- target ------------------------------------------------------------

    def _on_pick_target(self) -> None:
        row = self.from_list.currentRow()
        current = None
        if 0 <= row < len(self._rows):
            current = self._rows[row]["key"]
        dlg = SwapDialog(self.session, current, self)
        dlg.setWindowTitle(tr("选择替换用的装备"))
        if dlg.exec() != QDialog.Accepted:
            return
        key = dlg.chosen()
        if key is None:
            return
        self._to_key = key
        self._to_label = (f"{self.display(key.unit)}"
                          + (f" [{self.display(key.transporter)}]"
                             if key.transporter else "")
                          + (tr(" 经验{experience}", experience=key.experience)
                             if key.experience is not None else tr(" 继承经验")))
        self.to_label.setText(self._to_label)
        self._refresh_summary()

    # -- summary -----------------------------------------------------------

    def _selected(self) -> list[int]:
        """Ticked pack ids, in the order the list shows them.

        One row can stand for several pack objects, so this is not simply
        ``sorted(self._checked)``: it is every id the ticked rows cover.
        """
        return [pid for row in self._rows for pid in row["ids"]
                if pid in self._checked]

    def _ticked_rows(self) -> int:
        return sum(1 for row in self._rows
                   if any(p in self._checked for p in row["ids"]))

    def _refresh_summary(self) -> None:
        if self._rows:
            total = len(self._rows)
            shown = self._visible_rows()
            ticked = self._ticked_rows()
            if shown < total:
                # Both numbers, because "已勾选 3 种" next to a list showing two
                # ticked rows reads as a bug unless the hidden ones are owned up
                # to.  The tick is deliberately not limited to what is visible.
                self.from_count.setText(
                    tr("筛选出 {v0} / {v1} 种，已勾选 {v2} 种",
                       v0=shown, v1=total, v2=ticked))
            else:
                self.from_count.setText(
                    tr("{v0} 种装备，已勾选 {v1} 种", v0=total, v1=ticked))
        else:
            self.from_count.setText(tr("范围内没有装备"))
        picked = self._selected()
        if not picked or self._to_key is None:
            self.summary.setText(
                tr("勾选左侧装备（可多选）与右侧目标后显示影响范围。"))
            self.ok_btn.setEnabled(False)
            return
        # Matched by content, not by id — see ScopedReplace.apply.  A file can
        # hold two pack objects with the same signature, and the one the target
        # resolves to need not be the one this row stands for.
        sources = [p for p in picked
                   if self.session.packs.pack_key(p) != self._to_key]
        if not sources:
            self.summary.setText(
                tr("勾选的装备就是目标本身，无需替换。")
                if len(picked) == 1 else
                tr("勾选的装备里只有目标本身，其余没有可替换的来源。"))
            self.ok_btn.setEnabled(False)
            return
        executor = self.session.scoped_replace(
            self._scope_for(self._level), sources)
        survey = executor.survey(sources)
        excluded = len(sources) != len(picked)
        self.summary.setText(
            tr("<b>影响范围</b>：{v0}<br>件数与槽位顺序保持不变；共改写 {v1} 个营。",
                v0=survey.summary(),
                v1=len(survey.decks),
            )
            + (tr("<br><i>目标本身也在勾选之列，已从来源中剔除。</i>")
               if excluded else ""))
        self.ok_btn.setEnabled(bool(survey.entries))

    # -- result ------------------------------------------------------------

    def result(self) -> tuple[Scope, list[int], object] | None:
        """``(scope, [from_pack_id, ...], to_key)`` when everything was chosen."""
        picked = self._selected()
        if not picked or self._to_key is None:
            return None
        return self._scope_for(self._level), picked, self._to_key
