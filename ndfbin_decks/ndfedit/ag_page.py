# -*- coding: utf-8 -*-
"""将军模式牌组属性 —— 第二个专用编辑页。

**P0 阶段：只看不改。** 这一页现在能列出全部营、按名称维度筛、把战略属性
并排看；编辑与批量改是 P1 的事。界面上把这句写明了，不让人以为改不动是坏了。

**读的是投影，不是 pawn 本身。** 战略属性挂在 pawn 引用的模块对象上
（``ModulesDescriptors``），pawn 自己几乎是空的。所以这一页的值全部来自
:mod:`ndfedit.ag_view` 那张投影表 —— 也正因为如此，「按字段筛」这种通用能力
才能原样用在它上面。

**要打开 ``Pawn.ndfbin``。** 战略属性全在这一个文件里，``Deck.ndfbin`` 只装
编制（连/排/槽位），不含任何战略属性。打开错文件时给一句人话，而不是空表。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import ag_edit, ag_view
from .ag_view import ATTRS, PawnView
from .query import (Condition, MATCH_EQUAL, MATCH_GE, MATCH_LABELS,
                    MATCH_LE)
from .schema import NdfDocument

#: 排序不用 Qt 的内置实现。
#:
#: 先试的是「自定义 ``QTableWidgetItem`` 子类，持一个数值键、重写 ``__lt__``」
#: —— **段错误**（exit 139，稳定复现）。``QTableWidget.sortItems`` 在 C++ 侧
#: 遍历时回调 Python 比较函数，item 是 Python 子类时 PySide6 6.12 会崩；
#: 而 `QTableWidget` 默认按**显示文本**排，「12」会排在「5」前面，数值列一眼
#: 就看得出不对，所以不能放着不管。
#:
#: 改成：表头点击 → Python 里排好 → 重填。没有 C++ 回调，行为可预测；
#: 代价是每次点击重填一遍（2265 行实测 0.2 s）。

#: 名称维度的固定列：(表头, 宽度)。
NAME_COLS = (
    ("营名", 200),
    ("上级编制", 150),
    ("国家前缀", 84),
    ("阵营字段", 84),
)

#: 属性列宽度。
ATTR_WIDTH = 96

#: 移动方式的显示名。值是 ``StrategicMovementDescriptor_xxx`` 这样的描述符名，
#: 直接摆给玩家看太长；实测只有 6 种，够稳定，可以写死一张小表。
MOVEMENT_LABELS = {
    "StrategicMovementDescriptor_mecanized": "机械化",
    "StrategicMovementDescriptor_howitzer": "牵引炮",
    "StrategicMovementDescriptor_aerial": "空中",
    "StrategicMovementDescriptor_helico": "直升机",
    "StrategicMovementDescriptor_montagne": "山地",
    "StrategicMovementDescriptor_helico_inf": "直升机步兵",
}

#: 阵营字段与名字里的国家前缀不一致时的提示色。
MISMATCH_COLOR = QColor(230, 170, 60)

#: 批量改的值框用「未填」哨兵。
#:
#: 不这么做的话，值框一建出来就是 0 —— 而旁边就是「应用到当前这批」。
#: 手滑点一下，这批营的行动点全变 0。所以初始值是最小值并显示「（未填）」，
#: 按钮在这之前是禁用的。
SENTINEL = -1e12
UNFILLED_TEXT = "（未填）"

#: 内部键 → 中文名。``Condition.describe()`` 拿到的是键（``air_deny``），
#: 直接显示会是一行看不懂的标识符。
_ATTR_LABELS = {a.key: a.label for a in ATTRS}


def _attr_label(name: str) -> str:
    return _ATTR_LABELS.get(name, name)


def movement_label(value) -> str:
    s = str(value or "")
    if not s:
        return ""
    return MOVEMENT_LABELS.get(s, s.replace("StrategicMovementDescriptor_", ""))


def _fmt(value, kind: str) -> str:
    """一个属性值在表格里的样子。缺失一律 ``—``，不写 0。"""
    if value is None:
        return "—"
    if kind == "bool":
        return "是" if value else "否"
    if kind == "float":
        try:
            return f"{float(value):g}"
        except (TypeError, ValueError):
            return str(value)
    if kind == "int":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return str(value)
    return str(value)


class _CondRow(QWidget):
    """一行属性条件。

    **直接复用** :class:`ndfedit.query.Condition` 的匹配逻辑：它把 float 按
    显示精度比较（否则界面上看到 ``12``、输入 12 会一条都命不中），
    并且「对象没有这个字段 = 不匹配」—— 这正是「只改有支援半径的营」
    能成立的原因。

    只做「等于 / 不小于 / 不大于」三种：实测关心的都是这类阈值判断，
    区间与多选留给弹药页，这里加上只会让条件区更难读。
    """

    def __init__(self, changed, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.field = QComboBox()
        self.field.setMinimumWidth(150)
        for a in ATTRS:
            if a.kind in ("float", "int", "bool"):
                self.field.addItem(a.label, a.key)
        self.op = QComboBox()
        for key in (MATCH_EQUAL, MATCH_GE, MATCH_LE):
            self.op.addItem(MATCH_LABELS[key], key)
        self.value = QLineEdit()
        self.value.setPlaceholderText("值")
        self.value.setMaximumWidth(110)
        self.btn_del = QPushButton("×")
        self.btn_del.setFixedWidth(26)
        self.btn_del.setToolTip("删掉这条条件")
        self.btn_del.clicked.connect(lambda: changed(self, remove=True))
        self.field.currentIndexChanged.connect(lambda _i: changed(self))
        self.op.currentIndexChanged.connect(lambda _i: changed(self))
        self.value.textChanged.connect(lambda _t: changed(self))
        lay.addWidget(self.field)
        lay.addWidget(self.op)
        lay.addWidget(self.value)
        lay.addWidget(self.btn_del)
        lay.addStretch(1)

    def condition(self) -> "Condition | None":
        """填完整了才返回条件；没填完的不参与筛选。

        不返回「等于 0」—— 条件行一建出来值是空的，当成 0 会立刻命中
        0 条，看起来像功能坏了。
        """
        key = self.field.currentData()
        attr = next((a for a in ATTRS if a.key == key), None)
        text = self.value.text().strip()
        if attr is None or not text:
            return None
        try:
            val = float(text)
        except ValueError:
            return None
        return Condition(field=key, op=self.op.currentData(), value=val,
                         kind=attr.kind)


class AgPage(QDialog):
    """将军模式牌组属性页。"""

    #: 数据被改过，主界面要刷新「已改动 N 个对象」。
    dirtyApplied = Signal()

    def __init__(self, nd: NdfDocument, parent=None):
        super().__init__(parent)
        self.nd = nd
        self.pawns: list[PawnView] = []
        self._shown: list[PawnView] = []
        #: 属性条件行（AND）。名称维度定「候选池」，它们定「这一批」。
        self._conds: list[_CondRow] = []
        #: 当前排序列与方向（None = 保持文件里的原顺序）。
        self._sort_col: int | None = None
        self._sort_desc = False
        self.setWindowTitle("编辑牌组属性 —— 将军模式营")
        self.resize(1260, 800)
        self._build_ui()
        self.reload()

    # ---- 界面 ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        head = QLabel(
            "将军模式营的战略属性：部队类型、移动方式、行动点、炮兵支援范围。"
            "<br><span style='color:#8ab4d8;'>"
            "上面按「国家前缀 / 上级编制 / 营名」筛，下面按属性排序看；"
            "底部的「应用到当前这批」会改掉筛选出的所有营。"
            "</span>")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        flt = QHBoxLayout()
        # 默认只看「部队」：``AGF_<hash>`` 那 58 个是机场与补给站，不是营，
        # 它们的国家前缀是空的 —— 混在营列表里只会让人问「这些是什么」。
        # 它们确实也带战略属性，所以留一个入口，不删掉。
        self.f_kind = QComboBox()
        self.f_kind.addItem("部队（营）", ag_view.KIND_UNIT)
        self.f_kind.addItem("设施（机场/补给站）", ag_view.KIND_FACILITY)
        self.f_kind.addItem("全部类型", None)
        self.f_kind.setMinimumWidth(160)
        self.f_kind.currentIndexChanged.connect(self._apply_filter)
        self.f_country = QComboBox()
        self.f_country.setMinimumWidth(130)
        self.f_country.currentIndexChanged.connect(self._apply_filter)
        self.f_parent = QComboBox()
        self.f_parent.setMinimumWidth(150)
        self.f_parent.currentIndexChanged.connect(self._apply_filter)
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜营名或完整标识（如 595Reco）")
        self.f_search.setClearButtonEnabled(True)
        self.f_search.textChanged.connect(self._apply_filter)
        btn_reset = QPushButton("清空筛选")
        btn_reset.clicked.connect(self._reset_filter)

        flt.addWidget(QLabel("类型"))
        flt.addWidget(self.f_kind)
        flt.addWidget(QLabel("国家"))
        flt.addWidget(self.f_country)
        flt.addWidget(QLabel("上级编制"))
        flt.addWidget(self.f_parent)
        flt.addWidget(QLabel("搜索"))
        flt.addWidget(self.f_search, 1)
        flt.addWidget(btn_reset)
        root.addLayout(flt)

        # ---- 属性条件（AND）----
        # 与上面的名称维度是两条独立的路：名称维度决定「候选池」，
        # 条件决定「这一批」，最后合并到同一个命中集合。
        self.cond_box = QVBoxLayout()
        self.cond_box.setSpacing(4)
        root.addLayout(self.cond_box)
        crow = QHBoxLayout()
        btn_add_cond = QPushButton("+ 添加条件")
        btn_add_cond.clicked.connect(lambda: self.add_condition())
        btn_clear_cond = QPushButton("清空条件")
        btn_clear_cond.clicked.connect(self.clear_conditions)
        self.cond_hint = QLabel("")
        self.cond_hint.setStyleSheet("color:#8ab4d8;")
        self.cond_hint.setWordWrap(True)
        crow.addWidget(btn_add_cond)
        crow.addWidget(btn_clear_cond)
        crow.addWidget(self.cond_hint, 1)
        root.addLayout(crow)

        self.table = QTableWidget(0, len(NAME_COLS) + len(ATTRS))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(
            self._on_header_clicked)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        headers = [h for h, _ in NAME_COLS] + [a.label for a in ATTRS]
        self.table.setHorizontalHeaderLabels(headers)
        for i, (_h, w) in enumerate(NAME_COLS):
            self.table.setColumnWidth(i, w)
        for j in range(len(ATTRS)):
            self.table.setColumnWidth(len(NAME_COLS) + j, ATTR_WIDTH)
        root.addWidget(self.table, 1)

        # ---- 批量改 ----
        # 作用范围就是**当前筛选出的这一批**，所以按钮上写「当前这批」，
        # 不写「全部」—— 玩家改了筛选就该预期范围跟着变。
        brow = QHBoxLayout()
        self.bulk_attr = QComboBox()
        self.bulk_attr.setMinimumWidth(170)
        for a in ATTRS:
            if a.kind in ("float", "int", "bool"):
                self.bulk_attr.addItem(a.label, a.key)
        self.bulk_attr.setToolTip(
            "只列数值属性。「移动方式」「部队类型」是换引用，"
            "走的是另一条路（下一阶段）。")
        self.bulk_value = QDoubleSpinBox()
        self.bulk_value.setDecimals(4)
        self.bulk_value.setRange(SENTINEL, 1e9)
        self.bulk_value.setSpecialValueText(UNFILLED_TEXT)
        self.bulk_value.setValue(SENTINEL)
        self.bulk_value.setMinimumWidth(130)
        self.bulk_value.setKeyboardTracking(False)
        self.bulk_value.valueChanged.connect(self._on_bulk_value)
        self.btn_apply = QPushButton("应用到当前这批")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setToolTip(
            "对下面列出的这批营生效。\n"
            "共用同一模块的营会一起变；只命中一部分时自动克隆模块，"
            "没命中的营不受影响。")
        self.btn_apply.clicked.connect(self.on_apply)
        brow.addWidget(QLabel("把这批营的"))
        brow.addWidget(self.bulk_attr)
        brow.addWidget(QLabel("改成"))
        brow.addWidget(self.bulk_value)
        brow.addWidget(self.btn_apply)
        brow.addStretch(1)
        root.addLayout(brow)

        self.apply_note = QLabel("")
        self.apply_note.setStyleSheet("color:#8ab4d8;")
        self.apply_note.setWordWrap(True)
        root.addWidget(self.apply_note)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        foot = QHBoxLayout()
        self.note = QLabel("")
        self.note.setStyleSheet("color:#999;")
        foot.addWidget(self.note, 1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)
        root.addLayout(foot)

    # ---- 数据 ----

    def reload(self) -> None:
        if ag_view.PAWN_CLASS not in self.nd.class_names:
            self.pawns = []
            self._shown = []
            self.table.setRowCount(0)
            self.note.setText(
                f"{self.nd.path.name} 里没有 {ag_view.PAWN_CLASS} —— "
                "将军模式的战略属性在 Pawn.ndfbin 里，换那个文件再打开。")
            return
        self.pawns = ag_view.build_pawns(self.nd)
        self._fill_filters()
        self._apply_filter()

    def _fill_filters(self) -> None:
        for box, key, label in (
            (self.f_country, "country", "全部国家"),
            (self.f_parent, "parent", "全部上级"),
        ):
            was = box.currentData()
            box.blockSignals(True)
            box.clear()
            box.addItem(label, None)
            for value, n in ag_view.choices(self.pawns, key):
                box.addItem(f"{value}（{n}）", value)
            if was is not None:
                i = box.findData(was)
                if i >= 0:
                    box.setCurrentIndex(i)
            box.blockSignals(False)

    def _reset_filter(self) -> None:
        self.f_kind.setCurrentIndex(0)
        self.f_country.setCurrentIndex(0)
        self.f_parent.setCurrentIndex(0)
        self.f_search.clear()

    def _matches(self, p: PawnView, kind, country, parent, text) -> bool:
        if kind and p.kind != kind:
            return False
        if country and p.name_country != country:
            return False
        if parent and p.best_parent() != parent:
            return False
        if text:
            hay = f"{p.identifier} {p.export_name} {p.unit}".lower()
            if text not in hay:
                return False
        return True

    def _apply_filter(self) -> None:
        kind = self.f_kind.currentData()
        country = self.f_country.currentData()
        parent = self.f_parent.currentData()
        text = self.f_search.text().strip().lower()
        conds = [c for c in (r.condition() for r in self._conds) if c]
        self._shown = [
            p for p in self.pawns
            if self._matches(p, kind, country, parent, text)
            and all(c.match(p.values.get(c.field)) for c in conds)]
        if conds:
            # Condition.describe 不知道我们的内部键，要把 air_deny 这类
            # 翻译成人话；否则条件栏会显示一行看不懂的标识符。
            self.cond_hint.setText(
                "条件：" + "　且　".join(
                    c.describe(humanize=_attr_label) for c in conds))
        else:
            self.cond_hint.setText("")
        self._fill(self._shown)

    # ---- 属性条件 ----

    def add_condition(self) -> None:
        row = _CondRow(self._on_cond_changed)
        self._conds.append(row)
        self.cond_box.addWidget(row)
        self._on_cond_changed(row)

    def clear_conditions(self) -> None:
        for row in list(self._conds):
            self.cond_box.removeWidget(row)
            row.deleteLater()
        self._conds.clear()
        self._apply_filter()

    def _on_cond_changed(self, row: "_CondRow", remove: bool = False) -> None:
        if remove and row in self._conds:
            self._conds.remove(row)
            self.cond_box.removeWidget(row)
            row.deleteLater()
        self._apply_filter()

    def _fill(self, rows: list[PawnView]) -> None:
        rows = self._sorted(rows)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            self._put(r, 0, p.unit or "—")
            self._put(r, 1, " / ".join(p.parents) or "—")
            self._put(r, 2, p.name_country or "—")
            cell = QTableWidgetItem(p.country or "—")
            if p.country and p.name_country and p.country != p.name_country:
                # 名字说一件事、阵营字段说另一件事。这不是显示 bug ——
                # 柏林 mod 里成片存在（复制模板没改），标出来让人自己判断。
                cell.setForeground(MISMATCH_COLOR)
                cell.setToolTip(
                    f"名字里的国家是 {p.name_country}，但阵营字段是 {p.country}。\n"
                    "游戏读的是阵营字段；这一批多半是复制模板后没改。")
            self.table.setItem(r, 3, cell)
            for j, attr in enumerate(ATTRS):
                raw = p.values.get(attr.key)
                if attr.key == "movement":
                    text = movement_label(raw)
                else:
                    text = _fmt(raw, attr.kind)
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(r, len(NAME_COLS) + j, item)
        self.table.setUpdatesEnabled(True)
        self._update_note()

    # ---- 排序 ----

    def _on_header_clicked(self, col: int) -> None:
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, False
        self._fill(self._shown)

    def _sorted(self, rows: list[PawnView]) -> list[PawnView]:
        """按当前排序列排一遍。缺失值的行**不论升降序都排在最后**。"""
        col = self._sort_col
        if col is None:
            return rows
        if col < len(NAME_COLS):
            key_of = (
                lambda p: p.unit or "",
                lambda p: p.best_parent() or "",
                lambda p: p.name_country or "",
                lambda p: p.country or "",
            )[col]
            have = [p for p in rows if key_of(p)]
            miss = [p for p in rows if not key_of(p)]
            have.sort(key=key_of, reverse=self._sort_desc)
            return have + miss
        attr = ATTRS[col - len(NAME_COLS)]
        have = [p for p in rows if p.values.get(attr.key) is not None]
        miss = [p for p in rows if p.values.get(attr.key) is None]
        have.sort(key=lambda p: p.values[attr.key], reverse=self._sort_desc)
        return have + miss

    def _put(self, r: int, c: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        self.table.setItem(r, c, item)

    def _update_note(self) -> None:
        total = len(self.pawns)
        shown = len(self._shown)
        if not total:
            return
        mismatch = sum(1 for p in self._shown
                       if p.country and p.name_country
                       and p.country != p.name_country)
        bits = [f"{self.nd.path.name} · 共 {total} 个营，当前显示 {shown} 个"]
        if mismatch:
            bits.append(f"其中 {mismatch} 个的阵营字段与国家前缀不一致（橙色）")
        self.note.setText("　·　".join(bits))

    # ---- 写入 ----

    def _on_bulk_value(self, value: float) -> None:
        """没填值时不让点「应用」——那一下会把整批改成 0。"""
        self.btn_apply.setEnabled(value > SENTINEL)

    def on_apply(self) -> None:
        """把当前筛选出的这批营的某个属性改成同一个值。"""
        key = self.bulk_attr.currentData()
        attr = next((a for a in ATTRS if a.key == key), None)
        if attr is None or not self._shown:
            self.apply_note.setText("当前没有营可改 —— 先放宽筛选。")
            return
        value = self.bulk_value.value()
        if value <= SENTINEL:
            self.apply_note.setText("先在上面填一个值。")
            return

        # 确认框里要说清「共用」这件事：这是唯一会误伤别人的地方。
        groups: dict[int, int] = {}
        for p in self._shown:
            mid = p.modules.get(attr.module)
            if mid is not None:
                groups[mid] = groups.get(mid, 0) + 1
        shared = [n for n in groups.values() if n > 1]
        extra = ""
        if shared:
            extra = (f"\n\n其中 {len(shared)} 组共用同一个模块"
                     f"（最多一组 {max(shared)} 个营）。"
                     "整组命中会一起改；只命中一部分时自动克隆，"
                     "没命中的营不受影响。")

        ok = QMessageBox.question(
            self, "确认批量修改",
            f"把这 {len(self._shown)} 个营的「{attr.label}」"
            f"改成 {value:g}？{extra}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return

        try:
            res = ag_edit.apply_attr(self.nd, self._shown, attr, value)
        except ag_edit.AgEditError as exc:
            QMessageBox.warning(self, "改不了", str(exc))
            return

        self.apply_note.setText(res.summary())
        if res.ok:
            self.dirtyApplied.emit()
            self.reload()
