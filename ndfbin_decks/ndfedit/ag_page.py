# -*- coding: utf-8 -*-
"""将军模式牌组属性 —— 第二个专用编辑页。

**名称维度定候选池，属性条件定这一批。** 上面按国家 / 师 / 团 / 营名四级筛，
下面可以叠任意多条属性条件（AND）；「应用到当前这批」把某个属性一次改成
同一个值。两者最后合并成一个筛选结果，所以「筛」和「改」永远指的是同一批营 ——
这也是批量改最容易出错的地方（筛的是 A、改的是 B）。

**「师 / 团」是筛选维度，不是真实层级。** 名称里的段数只表示往上写了几级
（``pion_BEL_11Sq`` 一级都没有，``pion_SOV_8GA_34ArtDiv_4_1`` 写了三级），
同一个段位在不同国家可能是军也可能是师。这里把第 0 段叫「师」、第 1 段叫
「团」，取的是**上下级关系里那两级的位置**，为的是让下拉框从 514 项降到
几十项；要看真实层级，看结果表里那列「上级编制」。

**改的是投影，落盘的是模块。** 战略属性挂在 pawn 引用的模块对象上
（``ModulesDescriptors``），pawn 自己几乎是空的。所以这一页的值全部来自
:mod:`ndfedit.ag_view` 那张投影表，写入走 :mod:`ndfedit.ag_edit` 的
clone-on-write（只命中一部分时先克隆模块，否则会误伤共用同一个模块的营）。

**撤销走主窗口那一套。** 本页不自己记历史——:class:`history.History` 用脏状态
差分，改完调 ``dirtyApplied``，主窗口统一 ``commit()``。撤销 / 重做由本页的
按钮与快捷键转发给主窗口，撤销后主窗口会回调 :meth:`AgPage.refresh` 重画；
**不重画的话格子里的数字还是撤销前那个，玩家会以为撤销没生效然后再改一次。**

**要打开 ``Pawn.ndfbin``。** 战略属性全在这一个文件里，``Deck.ndfbin`` 只装
编制（连/排/槽位），不含任何战略属性。打开错文件时给一句人话，而不是空表。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
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
    #: 请求撤销 / 重做。本页不自己操作历史——历史归主窗口，
    #: 因为撤销之后要重画的远不止这一页（类计数、对象列表、状态栏）。
    undoRequested = Signal()
    redoRequested = Signal()

    def __init__(self, nd: NdfDocument, parent=None):
        super().__init__(parent)
        self.nd = nd
        self.pawns: list[PawnView] = []
        self._shown: list[PawnView] = []
        #: 表格里**实际显示的顺序**（已排序）。撤销后要按 identifier 找回
        #: 选中行，而那需要一份「行号 → 营」的映射，``_shown`` 是未排序的。
        self._rows: list[PawnView] = []
        #: 枚举属性的可选值缓存（``attr.key -> [值]``）。换文件要清空 ——
        #: 不同文件的 STRG 表不同，复用会写出根本不存在于那个文件里的名字。
        self._choice_cache: dict[str, list[str]] = {}
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
            "上面按「国家 / 师 / 团 / 营名」逐级筛，下面按属性排序看；"
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
        self.f_kind.currentIndexChanged.connect(self._on_pool_changed)
        self.f_country = QComboBox()
        self.f_country.setMinimumWidth(130)
        self.f_country.currentIndexChanged.connect(self._on_pool_changed)
        # 上级编制分两级：师（上级链第一段）与团（第二段）。
        # 合在一起的那个下拉实测 514 项，长到没法用。
        self.f_div = QComboBox()
        self.f_div.setMinimumWidth(150)
        self.f_div.setToolTip(
            "上级编制的第一段 —— 筛选意义上的「师」。\n"
            "这是筛选维度，不等于部队真实层级：名称里的段数只表示往上\n"
            "写了几级，同一个段位在不同国家可能是军，也可能是师。\n"
            "要看真实层级，看结果表里那列「上级」。")
        self.f_div.currentIndexChanged.connect(self._on_div_changed)
        self.f_reg = QComboBox()
        self.f_reg.setMinimumWidth(130)
        self.f_reg.setToolTip(
            "上级编制的第二段 —— 筛选意义上的「团」。\n"
            "选了师之后只列该师下辖的团（平均 5 个）。\n"
            "「师直属营」= 有师但没有团这一级。")
        self.f_reg.currentIndexChanged.connect(self._apply_filter)
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
        flt.addWidget(QLabel("师"))
        flt.addWidget(self.f_div)
        flt.addWidget(QLabel("团"))
        flt.addWidget(self.f_reg)
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
            if a.key == "country":
                # 国家留给筛选，不进批量改：它与编制、名字强耦合，
                # 改了只会制造更多「名字说一件事、字段说另一件事」。
                continue
            self.bulk_attr.addItem(a.label, a.key)
        self.bulk_attr.setToolTip(
            "数值属性直接填新值；「移动方式」「部队类型」是换引用，\n"
            "只能换成文件里**已经有**的取值（下拉选）。")
        self.bulk_value = QDoubleSpinBox()
        self.bulk_value.setDecimals(4)
        self.bulk_value.setRange(SENTINEL, 1e9)
        self.bulk_value.setSpecialValueText(UNFILLED_TEXT)
        self.bulk_value.setValue(SENTINEL)
        self.bulk_value.setMinimumWidth(130)
        self.bulk_value.setKeyboardTracking(False)
        self.bulk_value.valueChanged.connect(self._on_bulk_value)
        self.bulk_choice = QComboBox()
        self.bulk_choice.setMinimumWidth(200)
        self.bulk_choice.setVisible(False)
        self.bulk_choice.currentIndexChanged.connect(
            lambda _i: self._update_apply_buttons())
        self.btn_apply = QPushButton("应用到当前这批")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setToolTip(
            "对下面列出的这批营生效。\n"
            "共用同一模块的营会一起变；只命中一部分时自动克隆模块，"
            "没命中的营不受影响。")
        self.btn_apply.clicked.connect(self.on_apply)
        # 「补属性」：给本来没有这个字段的营加上它。只在支持补加的属性
        # 上出现（实测只有支援半径与防空半径）。
        self.btn_grant = QPushButton("给缺这个属性的营补上")
        self.btn_grant.setVisible(False)
        self.btn_grant.setToolTip(
            "这些营的战术描述器上根本没有这个字段。\n"
            "补加时会先克隆它自己的描述器、再追加字段 ——\n"
            "共用同一个描述器的其它营不受影响。")
        self.btn_grant.clicked.connect(self.on_grant)
        self.bulk_attr.currentIndexChanged.connect(
            self._on_bulk_attr_changed)
        brow.addWidget(QLabel("把这批营的"))
        brow.addWidget(self.bulk_attr)
        brow.addWidget(QLabel("改成"))
        brow.addWidget(self.bulk_value)
        brow.addWidget(self.bulk_choice)
        brow.addWidget(self.btn_apply)
        brow.addWidget(self.btn_grant)
        brow.addStretch(1)
        root.addLayout(brow)
        self._on_bulk_attr_changed()

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
        # 撤销 / 重做。本页是独立顶层窗口，主窗口挂在 QShortcut 上的
        # Ctrl+Z 作用域是主窗口，在这里按是**没有反应**的——所以这一页
        # 必须自己有入口，否则「改错了」在这个窗口里无从挽回。
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(lambda: self.undoRequested.emit())
        self.btn_redo = QPushButton("重做")
        self.btn_redo.clicked.connect(lambda: self.redoRequested.emit())
        foot.addWidget(self.btn_undo)
        foot.addWidget(self.btn_redo)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)
        root.addLayout(foot)
        self._build_shortcuts()
        self._update_undo_buttons()

    def _build_shortcuts(self) -> None:
        """Ctrl+Z / Ctrl+Y（外加 Ctrl+Shift+Z），转发给主窗口。

        搜索框有焦点时不拦：那里的 Ctrl+Z 是文本框自己的撤销，玩家在改
        搜索词时不该把文件里的改动退掉。判断放在主窗口的 ``_step_history``
        里（它已经按「文本框非空才让」处理），这里只管把按键送过去。
        """
        for seq, sig in ((QKeySequence(QKeySequence.Undo), self.undoRequested),
                         (QKeySequence(QKeySequence.Redo), self.redoRequested),
                         (QKeySequence("Ctrl+Shift+Z"), self.redoRequested)):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(sig.emit)

    def _update_undo_buttons(self) -> None:
        """按钮状态取自文档自己的历史——换文件后它也跟着变。"""
        h = getattr(self.nd, "history", None)
        can_undo = bool(h is not None and h.can_undo)
        can_redo = bool(h is not None and h.can_redo)
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)
        self.btn_undo.setToolTip(
            f"撤销：{h.undo_label()}" if can_undo else "没有可撤销的改动")
        self.btn_redo.setToolTip(
            f"重做：{h.redo_label()}" if can_redo else "没有可重做的改动")

    # ---- 数据 ----

    def refresh(self) -> None:
        """数据在别处被改了（撤销 / 重做）之后重画。

        **名字必须是 ``refresh``。** 主窗口的 ``_refresh_after_history``
        按这个名字找各专用页，找不到就跳过——而跳过意味着格子里的数字
        停在撤销前那一刻，玩家会以为撤销没生效，然后再改一次。

        重建 ``pawns`` 而不是只改格子：PawnView 里的值是投影出来的快照，
        撤销之后它们已经是旧的了（克隆体的模块可能整个没了）。
        """
        keep = self._selected_identifiers()
        self.reload()
        self._restore_selection(keep)
        # 「命中 N 个、克隆 M 个」记的是**上一次点应用**的账，撤销之后它
        # 已经不对应当前文件状态了。留着会让人以为改动还没退掉。
        # 撤销/重做实际做了什么，主窗口的状态栏会说（「已撤销：<步骤>」）。
        self.apply_note.clear()

    def _selected_identifiers(self) -> set[str]:
        """当前选中的营。撤销前后要保住选中，否则人得重新找一遍。"""
        rows = self.table.selectionModel()
        if rows is None:
            return set()
        got = set()
        for index in rows.selectedRows():
            r = index.row()
            if 0 <= r < len(self._rows):
                got.add(self._rows[r].identifier)
        return got

    def _restore_selection(self, idents: set[str]) -> None:
        if not idents:
            return
        for r, p in enumerate(self._rows):
            if p.identifier in idents:
                self.table.selectRow(r)
                return

    def reload(self) -> None:
        if ag_view.PAWN_CLASS not in self.nd.class_names:
            self.pawns = []
            self._shown = []
            self._rows = []
            self.table.setRowCount(0)
            self.note.setText(
                f"{self.nd.path.name} 里没有 {ag_view.PAWN_CLASS} —— "
                "将军模式的战略属性在 Pawn.ndfbin 里，换那个文件再打开。")
            self._update_undo_buttons()
            return
        self.pawns = ag_view.build_pawns(self.nd)
        self._fill_filters()
        self._apply_filter()
        self._update_undo_buttons()

    def _name_pool(self) -> list[PawnView]:
        """名称维度的候选池：只应用「类型」与「国家」。

        师、团两个下拉的选项都从**这个池**里算，于是三级是逐级收窄的：
        选了中国就只列该国的师，选了师就只列该师下辖的团。

        搜索框与属性条件**不参与**：否则每敲一个字就要重建两个下拉，
        而且「按名字找团」时越搜选项越少，反而找不到。
        """
        kind = self.f_kind.currentData()
        country = self.f_country.currentData()
        return [p for p in self.pawns
                if (not kind or p.kind == kind)
                and (not country or p.name_country == country)]

    def _fill_filters(self) -> None:
        """重建三个下拉。全程 blockSignals —— 重建不该顺手触发一次筛选。"""
        self._fill_country()
        self._fill_div()
        self._fill_reg()

    def _fill_country(self) -> None:
        was = self.f_country.currentData()
        self.f_country.blockSignals(True)
        self.f_country.clear()
        self.f_country.addItem("全部国家", None)
        for value, n in ag_view.choices(self.pawns, "country"):
            self.f_country.addItem(f"{value}（{n}）", value)
        self._restore(self.f_country, was)
        self.f_country.blockSignals(False)

    def _fill_div(self) -> None:
        """师下拉：全部 / 国家直属 / 当前国家池里的各个师。

        「国家直属」的 data 是**空字符串**，与「全部」（``None``）区分开 ——
        前者要筛出「一个上级都没有」的营，后者是不过滤。
        """
        was = self.f_div.currentData()
        pool = self._name_pool()
        free = sum(1 for p in pool if not p.parents)
        self.f_div.blockSignals(True)
        self.f_div.clear()
        self.f_div.addItem("全部师", None)
        self.f_div.addItem(f"国家直属（{free}）", "")
        for value, n in ag_view.choices(pool, "division"):
            self.f_div.addItem(f"{value}（{n}）", value)
        self._restore(self.f_div, was)
        self.f_div.blockSignals(False)

    def _fill_reg(self) -> None:
        """团下拉：跟着当前选中的师收窄。

        未选师时列出全部（377 项，仍然长，但那是「我还没想好哪个师」的
        正常代价）；选了师之后平均 5 项、最多 13 项。
        """
        was = self.f_reg.currentData()
        pool = self._name_pool()
        div = self.f_div.currentData()
        if div is not None:
            pool = [p for p in pool if p.division == div]
        no_reg = sum(1 for p in pool if p.parents and not p.regiment)
        self.f_reg.blockSignals(True)
        self.f_reg.clear()
        self.f_reg.addItem("全部团" if div is None else "该师全部营", None)
        if no_reg:
            # 有师没团 —— 与「国家直属」不是一回事，所以标签单列。
            self.f_reg.addItem(f"师直属营（{no_reg}）", "")
        for value, n in ag_view.choices(pool, "regiment"):
            self.f_reg.addItem(f"{value}（{n}）", value)
        self._restore(self.f_reg, was)
        self.f_reg.blockSignals(False)

    @staticmethod
    def _restore(box: QComboBox, was) -> None:
        """把重建前的选择尽量还原。找不到就停在「全部」。"""
        if was is None:
            return
        i = box.findData(was)
        if i >= 0:
            box.setCurrentIndex(i)

    def _on_pool_changed(self) -> None:
        """类型或国家变了：师、团两级的选项都从「类型 + 国家」的池里算，
        所以两级都得重建，不能只重画表格。"""
        self._fill_div()
        self._fill_reg()
        self._apply_filter()

    def _on_div_changed(self) -> None:
        """师变了：团下拉收窄到这个师。"""
        self._fill_reg()
        self._apply_filter()

    def _reset_filter(self) -> None:
        self.f_kind.setCurrentIndex(0)
        self.f_country.setCurrentIndex(0)
        self.f_div.setCurrentIndex(0)
        self.f_reg.setCurrentIndex(0)
        self.f_search.clear()

    def _matches(self, p: PawnView, kind, country, div, reg, text) -> bool:
        if kind and p.kind != kind:
            return False
        if country and p.name_country != country:
            return False
        # 空字符串 = 明确要「没有这一级」，所以判 None 而不是判真假。
        if div is not None and p.division != div:
            return False
        if reg is not None:
            if reg == "":
                # 「师直属营」：有师、但没有团这一级。**国家直属不算在内**
                # —— 它连师都没有，在师那一栏有自己的一项。两者混在一起
                # 会得到一个「1055 行、上级列全是 —」的怪结果。
                if not (p.division and not p.regiment):
                    return False
            elif p.regiment != reg:
                return False
        if text:
            hay = f"{p.identifier} {p.export_name} {p.unit}".lower()
            if text not in hay:
                return False
        return True

    def _apply_filter(self) -> None:
        kind = self.f_kind.currentData()
        country = self.f_country.currentData()
        div = self.f_div.currentData()
        reg = self.f_reg.currentData()
        text = self.f_search.text().strip().lower()
        conds = [c for c in (r.condition() for r in self._conds) if c]
        self._shown = [
            p for p in self.pawns
            if self._matches(p, kind, country, div, reg, text)
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
        # 「应用」「补上」都只作用于当前这批 —— 筛完之后按钮状态要跟着变，
        # 否则筛到 0 个营时按钮还亮着，点下去只是一句「没有营可改」。
        self._update_apply_buttons()

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
        self._rows = rows
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
        self._update_apply_buttons()

    # ---- 批量改：控件切换 ----

    def _bulk_attr(self):
        key = self.bulk_attr.currentData()
        return next((a for a in ATTRS if a.key == key), None)

    def _on_bulk_attr_changed(self) -> None:
        """数值属性给数字框，换引用属性给下拉框。

        这两类不能共用同一个控件：换引用只能选文件里已有的取值，
        填一个自由的数字毫无意义。
        """
        attr = self._bulk_attr()
        choice = bool(attr is not None and attr.is_choice)
        self.bulk_value.setVisible(not choice)
        self.bulk_choice.setVisible(choice)
        if choice:
            self._fill_choices(attr)
        self.btn_grant.setVisible(bool(attr is not None and attr.grantable))
        self._update_apply_buttons()

    def _fill_choices(self, attr) -> None:
        """枚举属性的可选值 —— 全部来自文件里**已经存在**的取值。

        移动方式 6 种、部队类型 17 种。这些字符串必须已经在 STRG 表里，
        否则写不进去（``strg_ref`` 只编码下标，不能凭空造名字）。
        """
        got = self._choice_cache.get(attr.key)
        if got is None:
            try:
                got = ag_view.choice_values(self.nd, attr)
            except Exception:                            # noqa: BLE001
                got = []
            self._choice_cache[attr.key] = got
        self.bulk_choice.blockSignals(True)
        self.bulk_choice.clear()
        # 首项是「未选」，data 为 None。不给它的话下拉会默认选中第一项
        # （比如「空中」），切过来按钮立刻可点 —— 手一滑整批就全变空中。
        # 与数值框的「（未填）」哨兵是同一件事。
        self.bulk_choice.addItem("（未选）", None)
        for value in got:
            label = (movement_label(value) if attr.key == "movement"
                     else value)
            self.bulk_choice.addItem(label, value)
        self.bulk_choice.blockSignals(False)

    def _update_apply_buttons(self) -> None:
        """「应用」「补上」两个按钮的可用状态。

        两个都要看「值填没填」——不填就点，等于把整批改成 0 或空字符串。
        """
        attr = self._bulk_attr()
        if attr is None:
            self.btn_apply.setEnabled(False)
            self.btn_grant.setEnabled(False)
            return
        if attr.is_choice:
            ready = self.bulk_choice.currentData() is not None
        else:
            ready = self.bulk_value.value() > SENTINEL
        has_rows = bool(self._shown)
        self.btn_apply.setEnabled(ready and has_rows)
        self.btn_grant.setEnabled(attr.grantable and ready and has_rows)

    def on_apply(self) -> None:
        """把当前筛选出的这批营的某个属性改成同一个值。

        数值属性走 :func:`ag_edit.apply_attr`，枚举属性（移动方式 / 部队
        类型）走 :func:`ag_edit.apply_choice` —— 后者是「换引用」，
        只能换成文件里已有的取值。
        """
        attr = self._bulk_attr()
        if attr is None or not self._shown:
            self.apply_note.setText("当前没有营可改 —— 先放宽筛选。")
            return

        if attr.is_choice:
            text = self.bulk_choice.currentData()
            if not text:
                self.apply_note.setText("先在右边选一个目标值。")
                return
            shown_value = (movement_label(text) if attr.key == "movement"
                           else text)
            value = text
            verb = "换成"
        else:
            value = self.bulk_value.value()
            if value <= SENTINEL:
                self.apply_note.setText("先在上面填一个值。")
                return
            shown_value = f"{value:g}"
            verb = "改成"

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
            f"{verb} {shown_value}？{extra}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return

        try:
            if attr.is_choice:
                res = ag_edit.apply_choice(self.nd, self._shown, attr, value)
            else:
                res = ag_edit.apply_attr(self.nd, self._shown, attr, value)
        except ag_edit.AgEditError as exc:
            QMessageBox.warning(self, "改不了", str(exc))
            return

        self.apply_note.setText(res.summary())
        if res.ok:
            self.dirtyApplied.emit()
            self.reload()

    def on_grant(self) -> None:
        """给本来**没有**这个属性的营加上它。

        与 :meth:`on_apply` 是两条路：那个改已有的值，这个要克隆描述器
        再追加字段。工兵营获得炮兵支援半径只能走这条 ——
        它的战术描述器上压根没有那个字段。
        """
        attr = self._bulk_attr()
        if attr is None or not self._shown or not attr.grantable:
            return
        value = self.bulk_value.value()
        if value <= SENTINEL:
            self.apply_note.setText("先在左边填一个值。")
            return

        missing = ag_edit.missing_field_pawns(self._shown, attr)
        if not missing:
            self.apply_note.setText(
                f"这批营本来就有「{attr.label}」，没有需要补的。"
                "要改值请用「应用到当前这批」。")
            return

        groups: dict[int, int] = {}
        for p in missing:
            mid = p.modules.get(attr.module)
            if mid is not None:
                groups[mid] = groups.get(mid, 0) + 1
        most = max(groups.values()) if groups else 1
        ok = QMessageBox.question(
            self, "确认补加属性",
            f"给 {len(missing)} 个营补上「{attr.label}」= {value:g}？\n\n"
            f"这些营的战术描述器上本来没有这个字段。每个营会先克隆它自己"
            f"一份描述器再追加字段 —— 最热的一个描述器被 {most} 个营共用，"
            f"直接加会让它们全体多出这个属性。\n\n"
            f"这批营里的另外 {len(self._shown) - len(missing)} 个已经有这个"
            f"字段，这次不动它们。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return

        try:
            res = ag_edit.grant_attr(self.nd, missing, attr, value)
        except ag_edit.AgEditError as exc:
            QMessageBox.warning(self, "补不上", str(exc))
            return

        self.apply_note.setText(res.summary())
        if res.ok:
            self.dirtyApplied.emit()
            self.reload()
