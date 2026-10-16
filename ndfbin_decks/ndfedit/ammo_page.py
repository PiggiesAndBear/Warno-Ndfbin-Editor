"""弹药专用编辑器：按字段值筛选，批量设为固定值。

**为什么不复用通用三栏.** 通用三栏要求玩家先选「类」——而
``TAmmunitionDescriptor`` 这个名字对玩家没有意义。这个页面的入口是
「编辑弹药」，进去直接看到 1548 条弹药和它们的弹种 / 伤害 / 射程，
不出现类名和 object_id。

**匹配方式随字段类型自适应**（用户明确要求）。实测依据：

- 射程只有 74 种取值 → 适合「等于」「在区间内」
- 伤害是 float32，实测值 ``0.13199999928474426`` → 「等于」必须按显示精度比较
- 弹种只有 6 种取值 → 应该给人选，而不是让人输 ``1``

**筛选是 AND 组合，条件可任意增删。**

入口：主窗口顶栏的「专用编辑器」下拉框 → 编辑弹药。
后续同类页面（单位、牌组）走同一个下拉框。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog,
    QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import query as Q
from .ammo import AMMO_CLASS, projectile_label
from .query import (MATCH_EQUAL, MATCH_GE, MATCH_IN, MATCH_LE, MATCH_RANGE,
                    Condition, Query)
from .schema import NdfDocument, ObjEntry, humanize

DIRTY_COLOR = QColor(255, 214, 102)
MISSING_COLOR = QColor(150, 150, 150)

#: 数值框的「未填」哨兵。
#: ``QSpinBox`` 没有空状态——不处理的话，条件行一建出来值就是 0，
#: 于是「等于 0」这个条件立刻生效，命中 0 条，看起来像功能坏了。
#: 所以把下界设成一个正常不会用到的值，并配 ``specialValueText``。
#:
#: float 用 -1e12；int 不能用它——``QSpinBox`` 的区间是 C ``int``
#: （±2147483647），``setRange(-1000000000000, …)`` 直接 OverflowError。
SENTINEL = -1e12
SENTINEL_INT = -2_000_000_000
UNFILLED_TEXT = "（未填）"

#: 结果表里固定展示的列（弹药编辑最常看的四个）。筛选用到的字段会自动补进来。
RESULT_COLUMNS = ("ProjectileType", "PhysicalDamages", "MaximumRangeGRU",
                  "TimeBetweenTwoShots")

#: 字段下拉的优先顺序。
#: **纯按覆盖率排会把 ``FXPow`` 排到第一**——它 100% 覆盖，但对玩家没意义。
#: 这里把玩家真正会来筛的字段提上去，其余仍按覆盖率排（不丢字段）。
COMMON_FIELDS = (
    "ProjectileType", "PhysicalDamages", "SuppressDamages",
    "MaximumRangeGRU", "MinimumRangeGRU",
    "MaximumRangeHelicopterGRU", "MaximumRangeAirplaneGRU",
    "MaximumRangeProjectileGRU",
    "TimeBetweenTwoShots", "TimeBetweenTwoSalvos",
    "ShotsCountPerSalvo", "SimultaneousShotsCount", "SupplyCost",
    "ProjectileSpeedGRU", "AimingTime",
)


def _field_sort_key(name: str, coverage: float):
    if name in COMMON_FIELDS:
        return (0, COMMON_FIELDS.index(name), 0)
    return (1, 0, -coverage)

#: 条件行里，数值字段可选的匹配方式。
NUMERIC_OPS = (
    (MATCH_EQUAL, "等于"),
    (MATCH_GE, "不小于"),
    (MATCH_LE, "不大于"),
    (MATCH_RANGE, "在区间内"),
)
ENUM_OPS = ((MATCH_IN, "是其中之一"),)


def value_label(field: str, value) -> str:
    """把原始值换成人读的标签——弹种是本页唯一需要翻译的枚举。"""
    if field == "ProjectileType":
        return projectile_label(value)
    if isinstance(value, float):
        # float32 原文长得像 0.13199999928474426，照原样显示没人看得懂
        return f"{round(value, Q.FLOAT_DIGITS):g}"
    return str(value)


def field_label(name: str) -> str:
    return humanize(name)


#: 结果表里追加的派生列（不是文件里的字段）。
DPS_COLUMN = "DPS（推算）"

#: 条件区高度的下界与上界（像素）。
#: 下界保证一行条件旁边能露出「+ 添加条件」那一排；上界避免条件把结果表挤没。
COND_MIN_H, COND_MAX_H = 96, 280


def dps_text(entry) -> str:
    """推算每秒伤害：``单发伤害 × 每轮弹量 × 同时弹丸数 ÷ 射击间隔``。

    **这是只读推算，不是文件里的字段。** 玩家改伤害或射速时，
    没有一个「结果指标」跟着动，就没法判断自己改得对不对——
    这是唯一能立刻给出反馈的东西。

    缺任何一个必要的乘数就不显示（返回 ``—``），不做假设性默认值：
    猜一个数字出来比留空更容易误导。
    """
    def val(name):
        f = entry.fields.get(name)
        return None if f is None else f.value

    dmg = val("PhysicalDamages")
    gap = val("TimeBetweenTwoShots")
    if dmg is None or not gap:
        return "—"
    count = 1.0
    for x in (val("ShotsCountPerSalvo"), val("SimultaneousShotsCount")):
        if x is not None:
            count *= float(x)
    try:
        return f"{float(dmg) * count / float(gap):.1f}"
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"


class ValueEditor(QWidget):
    """条件行的「值」部分，按匹配方式切换形态。

    三种形态：

    ==============  ============================  ================
    匹配方式        控件                          适用
    ==============  ============================  ================
    等于/不小于/…   一个数字框                    射程、伤害
    在区间内        两个数字框                    伤害这种连续量
    是其中之一      带勾选的列表                  弹种这种低基数枚举
    ==============  ============================  ================
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(4)
        self._kind = "float"
        self._op = MATCH_EQUAL
        self._field = ""
        self._enum_values: list = []
        #: 切换形态时用它把玩家已经填的值带过去，免得一切换就白填
        self._remembered = {"value": None, "low": None, "high": None,
                            "values": ()}
        self._building = False
        self._rebuild()

    # ---- 配置 ----

    def configure(self, kind: str, op: str, enum_values=()):
        self._kind = kind
        self._enum_values = list(enum_values)
        if op != self._op:
            self._capture()
        self._op = op
        self._rebuild()

    def _make_spin(self, value):
        if self._kind == "float":
            box = QDoubleSpinBox()
            box.setDecimals(Q.FLOAT_DIGITS)
            box.setRange(SENTINEL, 1e12)
            box.setSingleStep(0.5)
        else:
            box = QSpinBox()
            box.setRange(SENTINEL_INT, 2_000_000_000)
            box.setSingleStep(1)
        # 停在下界上就是「还没填」，条件不参与筛选
        box.setSpecialValueText(UNFILLED_TEXT)
        box.setValue(value if value is not None else box.minimum())
        box.setKeyboardTracking(False)
        box.valueChanged.connect(lambda _v: self.changed.emit())
        return box

    def _rebuild(self):
        self._building = True
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w:
                # 先 setParent(None) 再 deleteLater()。只 deleteLater 的话，
                # 控件在事件循环回来之前仍然是 ValueEditor 的可见子控件，
                # 会按旧几何画在别的控件上面——切换匹配方式时看起来像叠影。
                w.setParent(None)
                w.deleteLater()
        self._single = None
        self._low = self._high = None
        self._multi = None

        if self._op == MATCH_IN:
            self._build_multi()
        elif self._op == MATCH_RANGE:
            self._low = self._make_spin(self._remembered["low"])
            sep = QLabel("~")
            self._high = self._make_spin(self._remembered["high"])
            self._lay.addWidget(self._low)
            self._lay.addWidget(sep)
            self._lay.addWidget(self._high)
        else:
            self._single = self._make_spin(self._remembered["value"])
            self._lay.addWidget(self._single)
        self._building = False
        self.changed.emit()

    def _build_multi(self):
        lst = QListWidget()
        # 够显示 4~5 行即可。弹种最多 6 项，再高就会把下面的条件行挤出可视区。
        lst.setMaximumHeight(76)
        remembered = set(self._remembered["values"] or ())
        for v in self._enum_values:
            it = QListWidgetItem(value_label(self._field_name, v))
            it.setData(Qt.UserRole, v)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if v in remembered else Qt.Unchecked)
            lst.addItem(it)
        lst.itemChanged.connect(lambda _i: self.changed.emit())
        self._multi = lst
        self._lay.addWidget(lst, 1)

    @property
    def _field_name(self) -> str:
        return self._field

    def set_field_name(self, name: str):
        self._field = name

    # ---- 取值 ----

    def _capture(self):
        self._remembered["value"] = self._spin_value(self._single)
        if self._low is not None and self._high is not None:
            self._remembered["low"] = self._spin_value(self._low)
            self._remembered["high"] = self._spin_value(self._high)
        if self._multi is not None:
            self._remembered["values"] = tuple(
                self._multi.item(i).data(Qt.UserRole)
                for i in range(self._multi.count())
                if self._multi.item(i).checkState() == Qt.Checked)

    @staticmethod
    def _spin_value(box):
        """停在下界 = 玩家没填 = ``None``。

        判断用 ``minimum()`` 而不是写死的哨兵常量——float 和 int 的下界
        不同（``QSpinBox`` 装不下 -1e12），让控件自己说是最稳的。
        """
        if box is None:
            return None
        v = box.value()
        return None if v == box.minimum() else v

    def parts(self) -> dict:
        self._capture()
        if self._op == MATCH_IN:
            return {"values": self._remembered["values"]}
        if self._op == MATCH_RANGE:
            return {"low": self._remembered["low"],
                    "high": self._remembered["high"]}
        return {"value": self._remembered["value"]}

    def seed(self, cond: Condition):
        """按已有条件回填。"""
        self._remembered = {"value": cond.value, "low": cond.low,
                            "high": cond.high, "values": cond.values}
        self._rebuild()


class ConditionRow(QWidget):
    """一行筛选条件：字段 + 匹配方式 + 值 + 删除。"""

    removed = Signal(object)
    changed = Signal()

    def __init__(self, nd: NdfDocument, entries, parent=None):
        super().__init__(parent)
        self._nd = nd
        self._entries = entries
        self.field = ""
        self.kind = "float"

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)

        self.field_box = QComboBox()
        self.field_box.setEditable(True)
        self.field_box.setInsertPolicy(QComboBox.NoInsert)
        self.field_box.setMinimumWidth(190)
        self.field_box.lineEdit().setPlaceholderText("字段名")
        completer = self.field_box.completer()
        if completer is not None:
            completer.setFilterMode(Qt.MatchContains)
            completer.setCompletionMode(QCompleter.PopupCompletion)

        self.op_box = QComboBox()
        self.op_box.setMinimumWidth(92)

        self.value = ValueEditor()

        btn = QPushButton("×")
        btn.setFixedWidth(28)
        btn.setToolTip("删掉这个条件")
        btn.clicked.connect(lambda: self.removed.emit(self))

        lay.addWidget(self.field_box, 0)
        lay.addWidget(self.op_box, 0)
        lay.addWidget(self.value, 1)
        lay.addWidget(btn, 0)

        self._fill_fields()
        self.field_box.currentIndexChanged.connect(self._on_field)
        self.op_box.currentIndexChanged.connect(self._on_op)
        self.value.changed.connect(self.changed.emit)

    def _fill_fields(self):
        """字段下拉：只列可编辑的数值字段。

        排序见 :func:`_field_sort_key`——常用字段在前，其余按覆盖率。
        下拉可输入过滤（``MatchContains``），所以 75 个字段也不难找。
        """
        info = self._nd.class_info(AMMO_CLASS)
        self._field_meta = {}
        if info:
            for name, kind, cov in info.fields:
                if kind in ("int", "float", "bool"):
                    self._field_meta[name] = (kind, cov)
        ordered = sorted(
            self._field_meta,
            key=lambda n: _field_sort_key(n, self._field_meta[n][1]))
        self.field_box.blockSignals(True)
        self.field_box.clear()
        self.field_box.addItem("", "")
        for name in ordered:
            self.field_box.addItem(field_label(name), name)
        self.field_box.blockSignals(False)

    def set_field(self, name: str):
        i = self.field_box.findData(name)
        if i >= 0:
            self.field_box.setCurrentIndex(i)
        else:
            self.field_box.setEditText(name)
        self._on_field()

    def _on_field(self):
        name = self.field_box.currentData()
        if not name:
            name = self.field_box.currentText().strip()
            name = name if name in self._field_meta else ""
        self.field = name
        self.kind = self._field_meta.get(name, ("float", 0))[0]
        self.value.set_field_name(name)
        self._fill_ops()
        self.changed.emit()

    def _fill_ops(self):
        """匹配方式按字段类型给——低基数字段走多选，其余走数值比较。"""
        enum_values = ()
        if self.kind == "bool":
            ops = ((MATCH_EQUAL, "等于"),)
        elif Q.is_enum_like(self._entries, self.field):
            counts = Q.value_counts(self._entries, self.field)
            enum_values = [v for v, _n in counts.most_common()]
            ops = ENUM_OPS
        else:
            ops = NUMERIC_OPS
        self._enum_values = enum_values
        self.op_box.blockSignals(True)
        self.op_box.clear()
        for op, label in ops:
            self.op_box.addItem(label, op)
        self.op_box.blockSignals(False)
        self._current_ops = [op for op, _ in ops]
        self._apply_op()

    def _on_op(self):
        self._apply_op()
        self.changed.emit()

    def _apply_op(self):
        op = self.op_box.currentData() or MATCH_EQUAL
        self.value.configure(self.kind, op, self._enum_values)

    def condition(self) -> Condition:
        op = self.op_box.currentData() or MATCH_EQUAL
        parts = self.value.parts()
        return Condition(field=self.field, op=op, kind=self.kind, **parts)

    def seed(self, cond: Condition):
        self.set_field(cond.field)
        i = self.op_box.findData(cond.op)
        if i >= 0:
            self.op_box.setCurrentIndex(i)
        self._apply_op()
        self.value.seed(cond)


class AmmoEditorDialog(QDialog):
    """弹药专用编辑器。"""

    dirtyApplied = Signal()

    def __init__(self, nd: NdfDocument, parent=None):
        super().__init__(parent)
        self.nd = nd
        self.entries: list[ObjEntry] = []
        self.rows: list[ConditionRow] = []
        self._hits: list[ObjEntry] = []
        self.setWindowTitle("编辑弹药 —— 按字段值筛选并批量修改")
        self.resize(1120, 760)

        root = QVBoxLayout(self)

        head = QLabel(
            "先设条件，下面就会列出命中的弹药；再选一个字段和新值，"
            "一次改完它们。"
            "<br><span style='color:#8ab4d8;'>条件可以加多条，它们之间是「并且」。"
            "例如「弹种 = Shell」并且「对地射程 = 1925」。</span>")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        self.cond_box = QVBoxLayout(holder)
        self.cond_box.setContentsMargins(2, 2, 2, 2)
        self.cond_box.setSpacing(2)
        scroll.setWidget(holder)
        #: 高度按内容自适应（见 :meth:`_fit_cond_height`），不是固定值。
        self._cond_scroll = scroll
        root.addWidget(scroll)

        row = QHBoxLayout()
        btn_add = QPushButton("+ 添加条件")
        btn_add.clicked.connect(lambda: self.add_row())
        btn_clear = QPushButton("清空条件")
        btn_clear.clicked.connect(self.clear_rows)
        self.hit_label = QLabel("")
        self.hit_label.setStyleSheet("color:#8ab4d8;")
        row.addWidget(btn_add)
        row.addWidget(btn_clear)
        row.addWidget(self.hit_label, 1)
        root.addLayout(row)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        self.table = QTableWidget(0, 1)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        root.addWidget(self.table, 1)

        self.bulk_hint = QLabel(
            "把上面命中的弹药，统一改成下面这个值：")
        self.bulk_hint.setStyleSheet("color:#8ab4d8;")
        root.addWidget(self.bulk_hint)

        brow = QHBoxLayout()
        self.bulk_field = QComboBox()
        self.bulk_field.setMinimumWidth(220)
        self.bulk_field.currentIndexChanged.connect(self._on_bulk_field)
        self.bulk_value = QDoubleSpinBox()
        self.bulk_value.setDecimals(Q.FLOAT_DIGITS)
        self.bulk_value.setRange(SENTINEL, 1e12)
        self.bulk_value.setSpecialValueText(UNFILLED_TEXT)
        self.bulk_value.setKeyboardTracking(False)
        self.bulk_value.setMinimumWidth(140)
        self.bulk_check = QCheckBox()
        self.btn_apply = QPushButton("预览并应用")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.on_apply)
        brow.addWidget(self.bulk_field, 0)
        brow.addWidget(QLabel("设为"))
        brow.addWidget(self.bulk_value, 0)
        brow.addWidget(self.bulk_check, 0)
        brow.addStretch(1)
        brow.addWidget(self.btn_apply)
        root.addLayout(brow)

        foot = QHBoxLayout()
        self.note = QLabel("")
        self.note.setStyleSheet("color:#999;")
        foot.addWidget(self.note, 1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)
        root.addLayout(foot)

        self.reload()

    # ---- 数据 ----

    def reload(self):
        if AMMO_CLASS not in self.nd.class_names:
            self.note.setText(f"这个文件里没有 {AMMO_CLASS}，没有弹药可编辑。")
            self.table.setColumnCount(0)
            self.entries = []
            return
        self.entries = self.nd.entries(AMMO_CLASS)
        self.nd.ensure_titles(AMMO_CLASS)
        self._fill_bulk_fields()
        if not self.rows:
            self.add_row()
        self.note.setText(
            f"{self.nd.path.name} · {len(self.entries)} 条弹药 · "
            f"改动会进入主界面的未保存状态，由那里统一保存。")

    def _fill_bulk_fields(self):
        info = self.nd.class_info(AMMO_CLASS)
        self._bulk_meta = {}
        if info:
            for name, kind, cov in info.fields:
                if kind in ("int", "float", "bool"):
                    self._bulk_meta[name] = (kind, cov)
        ordered = sorted(
            self._bulk_meta,
            key=lambda n: _field_sort_key(n, self._bulk_meta[n][1]))
        was = self.bulk_field.currentData()
        self.bulk_field.blockSignals(True)
        self.bulk_field.clear()
        for name in ordered:
            self.bulk_field.addItem(field_label(name), name)
        if was:
            i = self.bulk_field.findData(was)
            if i >= 0:
                self.bulk_field.setCurrentIndex(i)
        self.bulk_field.blockSignals(False)
        self._on_bulk_field()

    def _on_bulk_field(self):
        """布尔字段用勾选框，数值字段用数字框——同一个位置换控件。"""
        name = self.bulk_field.currentData()
        kind = self._bulk_meta.get(name, ("float", 0))[0]
        is_bool = kind == "bool"
        self.bulk_value.setVisible(not is_bool)
        self.bulk_check.setVisible(is_bool)
        self.bulk_value.setDecimals(
            Q.FLOAT_DIGITS if kind == "float" else 0)
        if kind == "int":
            self.bulk_value.setSingleStep(1)

    # ---- 条件 ----

    def add_row(self, cond: Condition | None = None) -> ConditionRow:
        row = ConditionRow(self.nd, self.entries)
        row.removed.connect(self.remove_row)
        row.changed.connect(self.refresh)
        self.cond_box.addWidget(row)
        self.rows.append(row)
        if cond is not None:
            row.seed(cond)
        self.refresh()
        return row

    def remove_row(self, row):
        if row in self.rows:
            self.rows.remove(row)
            self.cond_box.removeWidget(row)
            row.deleteLater()
        self.refresh()

    def clear_rows(self):
        for row in list(self.rows):
            self.remove_row(row)
        self.add_row()

    def current_query(self) -> Query:
        return Query([r.condition() for r in self.rows])

    # ---- 刷新 ----

    def refresh(self):
        q = self.current_query()
        self._hits = q.select(self.entries)
        self.hit_label.setText(
            f"命中 {len(self._hits)} / {len(self.entries)} 条　·　"
            + q.describe(field_label, value_label))
        self._fill_table()
        self.btn_apply.setEnabled(bool(self._hits))
        self.btn_apply.setText(
            f"预览并应用（{len(self._hits)} 条）" if self._hits
            else "预览并应用")
        self._fit_cond_height()

    def _fit_cond_height(self):
        """把条件区高度调到刚好装下这些行。

        **不能只设 ``setMaximumHeight``**：那只是封顶，控件实际高度仍由
        布局按 ``sizeHint`` 分配。实测两行条件时它只拿到 70px，
        而两行本身有 101px——第二行整条落在可视区外，玩家会以为
        「+ 添加条件」没生效，其实只是被裁掉了。

        高度随行数增长到 :data:`COND_MAX_H` 为止，再多就出现滚动条。
        """
        inner = sum(r.sizeHint().height() for r in self.rows)
        if len(self.rows) > 1:
            inner += self.cond_box.spacing() * (len(self.rows) - 1)
        m = self.cond_box.contentsMargins()
        self._cond_scroll.setFixedHeight(
            max(COND_MIN_H, min(COND_MAX_H, inner + m.top() + m.bottom() + 4)))

    def _columns(self) -> list[str]:
        cols = ["__title__"]
        for c in RESULT_COLUMNS:
            if c in self._bulk_meta:
                cols.append(c)
        for r in self.rows:                      # 筛选用到的字段也显示出来
            if r.field and r.field not in cols:
                cols.append(r.field)
        cols.append(DPS_COLUMN)                  # 派生列放最后
        return cols

    def _fill_table(self):
        cols = self._columns()
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(
            ["名称"] + [field_label(c) for c in cols[1:-1]] + [DPS_COLUMN])
        self.table.setRowCount(len(self._hits))
        for i, e in enumerate(self._hits):
            title = e.title or f"#{e.object_id}"
            it = QTableWidgetItem(title)
            if e.dirty:
                it.setForeground(QBrush(DIRTY_COLOR))
            it.setData(Qt.UserRole, e.object_id)
            self.table.setItem(i, 0, it)
            for j, name in enumerate(cols[1:-1], start=1):
                f = e.fields.get(name)
                text = (value_label(name, f.value) if f is not None
                        else "—")
                cell = QTableWidgetItem(text)
                if f is None:
                    cell.setForeground(QBrush(MISSING_COLOR))
                elif f.dirty:
                    cell.setForeground(QBrush(DIRTY_COLOR))
                self.table.setItem(i, j, cell)
            dps = QTableWidgetItem(dps_text(e))
            dps.setToolTip(
                "单发伤害 × 每轮弹量 × 同时弹丸数 ÷ 射击间隔\n"
                "由已有字段推算，不是文件里的值——用来判断改动后的效果。")
            self.table.setItem(i, len(cols) - 1, dps)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for j in range(1, len(cols)):
            header.setSectionResizeMode(j, QHeaderView.ResizeToContents)

    # ---- 应用 ----

    def on_apply(self):
        name = self.bulk_field.currentData()
        if not name:
            return
        kind = self._bulk_meta.get(name, ("float", 0))[0]
        if kind == "bool":
            value = self.bulk_check.isChecked()
        else:
            value = self.bulk_value.value()
            if value == self.bulk_value.minimum():
                QMessageBox.information(
                    self, "还没填值",
                    f"先填一个要把「{field_label(name)}」改成的值。")
                return
        q = self.current_query()
        res = Q.preview(self.entries, q, name, value, kind)
        if not res.matched:
            QMessageBox.information(
                self, "没有命中",
                "当前条件没有命中任何弹药，改不了。\n\n"
                "看看条件是不是太严了——比如同时限定了弹种和射程。")
            return
        if not res.changed:
            QMessageBox.information(
                self, "不用改", res.summary())
            return

        lines = [
            f"将把 {res.changed} 条弹药的「{field_label(name)}」"
            f"统一改成 {value_label(name, res.new_value)}。",
            "",
            f"筛选条件：{q.describe(field_label, value_label)}",
            f"改动前的取值：{res.before_text(labeler=value_label)}",
        ]
        if res.unchanged:
            lines.append(
                f"（另有 {res.unchanged} 条命中项本来就是这个值，不计入改动）")
        lines += [
            "",
            "这一步只改内存，还需在主界面点「保存到文件」才会写盘。",
            "",
            "继续？",
        ]
        if QMessageBox.question(
                self, "确认批量修改", "\n".join(lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes) != QMessageBox.Yes:
            return

        done = Q.apply_value(self.entries, q, name, value, kind)
        self.refresh()
        self.dirtyApplied.emit()
        QMessageBox.information(
            self, "改好了",
            done.summary() + "\n\n改动已计入未保存状态，"
            "回到主界面点「保存到文件」才会写盘。")
