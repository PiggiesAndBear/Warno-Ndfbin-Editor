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

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import ag_view
from .ag_view import ATTRS, PawnView
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


class AgPage(QDialog):
    """将军模式牌组属性页。"""

    def __init__(self, nd: NdfDocument, parent=None):
        super().__init__(parent)
        self.nd = nd
        self.pawns: list[PawnView] = []
        self._shown: list[PawnView] = []
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
            "上面按「国家前缀 / 上级编制 / 营名」筛；下面按属性排序看。"
            "本页目前<b>只读</b>——编辑与批量改在下一阶段。</span>")
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
        self._shown = [p for p in self.pawns
                       if self._matches(p, kind, country, parent, text)]
        self._fill(self._shown)

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
        bits.append("只读预览")
        self.note.setText("　·　".join(bits))
