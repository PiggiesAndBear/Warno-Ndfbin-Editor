"""通用 .ndfbin 浏览器 —— 四栏，数据驱动，兼容任意文件。

**与第一版的差别.** 第一版把 Weapon.ndfbin 的弹药写死成三栏+9个固定字段。
用户指出 UI 应当兼容所有 ndfbin，所以现在是：

.. code-block:: text

    ┌──────────────────────────────────────────────────────────┐
    │ 打开任意 .ndfbin          [文件路径]        [搜索框]     │
    ├────────────┬──────────────┬──────────────────────────────┤
    │ 类列表│ 对象列表   │ 字段编辑面板│
    │ (Schema  │  (该类的所有  │  (按实际存在的字段           │
    │  自动发现) │   实例)      │   动态生成)│
    └────────────┴──────────────┴──────────────────────────────┘
        [ 状态 ]      [撤销] [重做] [ 放弃改动 ]  [ 保存到文件 ]

**字段从哪来.** 每个 .ndfbin 自带 PROP 表，且每条属性记录都带所属 ``class``。
所以「这个类有哪些数值字段」不需要任何硬编码白名单——读PROP 表即可，
且结果永远和文件一致。换文件、换mod、换游戏版本都不需要改代码。

**只暴露标量.** int / float / bool 进编辑器；数组、对象引用、loc_hash
虽然技术上能改，但语义不可理解，改了只会弄坏 mod，一律不显示。

**只显示真实存在的字段.** 用户实测发现 TAmmunitionDescriptor 里有
8 对射程字段（对地 / 对直升机 / 对飞机 / 对投射物），但覆盖率差异极大::

    MaximumRangeGRU1548 中1396 个有 (90.2%)
    MinimumRangeGRU              1143 (73.8%)
    MaximumRangeHelicopterGRU     445 (28.7%)

所以面板按**当前这个对象实际拥有的字段**生成，并给低覆盖率字段加提示。
不存在的字段不占位置——玩家不会以为「射程 0」是真值。
"""

from __future__ import annotations

import sys
import traceback
from collections import namedtuple
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QKeySequence,
                           QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_TITLE, paths
from ..core import writer
from . import ag_view, entity, modpicker, refs
from .ammo import AMMO_CLASS, AmmoTitles
from .ag_page import AgPage
from .ammo_page import AmmoEditorDialog
from .schema import ClassInfo, NdfDocument, ObjEntry, humanize

DECIMALS = 4
MIN_FLOAT = -1e9
MAX_FLOAT = 1e9
DIRTY_COLOR = QColor(255, 214, 102)
MISSING_TEXT = "无此字段"

#: GRU = Gaph Unit，游戏内部的长度单位。实测弹药的射程 6000 GRU ≈ 6 km，
#: 所以按 1000 GRU = 1 km 显示。**只做显示，绝不改存储值**——
#: 游戏读的就是 GRU，换算只是给玩家一个可感的量级。
GRU_PER_KM = 1000.0


def is_gru_field(name: str) -> bool:
    return name.endswith("GRU")


def gru_text(value) -> str:
    """``6000`` -> ``≈ 6.00 km``。非数值返回空串。"""
    try:
        return f"≈ {float(value) / GRU_PER_KM:.2f} km"
    except (TypeError, ValueError):
        return ""

CJK_FONTS = ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei",
             "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC")


def _pick_cjk_font(app):
    from PySide6.QtGui import QFontDatabase
    families = set(QFontDatabase.families())
    for name in CJK_FONTS:
        if name in families:
            app.setFont(QFont(name, 9))
            return name
    return None


def _paint(box: QWidget, dirty: bool):
    """标黄改过的控件，让玩家一眼看到自己动了什么。"""
    if isinstance(box, QCheckBox):
        # 整块背景色会盖掉勾选框本身，只改文字颜色
        box.setStyleSheet("color:#ffd66a;" if dirty else "")
        return
    if dirty:
        box.setStyleSheet("background:#4a3f1e; border:1px solid #b89b45;"
                          "border-radius:3px; padding:2px;")
    else:
        box.setStyleSheet("")


def _mark_missing(box: QWidget, missing: bool):
    """把数值框切到「该对象没有此字段」状态。

    ``QSpinBox`` 没有 ``setPlaceholderText``，所以用 specialValueText：
    设一个专用最小值并让spinbox 停在该值，同时锁定下界——玩家既看到提示，
    也拖不动它。这比灰掉一个 0 让人以为「射程真的是 0」要诚实得多。
    """
    # QCheckBox 没有最小值概念，缺字段时置灰 + 取消勾选即可
    if isinstance(box, QCheckBox):
        box.setEnabled(not missing)
        if missing:
            block = box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(block)
        return

    if missing:
        # 只禁用。不用 specialValueText 写「无此字段」——那会盖住标签、
        # 在长列表里造成噪音，而且 QSpinBox 与 QDoubleSpinBox 行为不一致。
        # 真正的说明放在字段的 tooltip 和面板顶部的汇总句里。
        box.setEnabled(False)
        box.setToolTip(MISSING_TEXT)
    else:
        box.setEnabled(True)
        box.setToolTip("")
        if isinstance(box, QSpinBox):
            box.setMinimum(-1_000_000_000)
        else:
            box.setMinimum(MIN_FLOAT)


#: 引用长按钮末尾的记号，表示「进入这个对象」而不是「切换到这个目标」。
#: 两个按钮并排时，纯文字的长按钮看起来像标签，加个记号才立得住。
ENTER_MARK = "  ›"

#: 克隆后「谁改指向它」的候选列表硬上限。
#: 实测弹药类有 9001 条，QTableWidget 4 列 × 9001 行 66ms——远没到上限。
#: 留这个数只是防病态文件把界面拖死；真被截断时计数行会把它显示出来。
MAX_CANDIDATE_ROWS = 50000

#: 多选改指向窗口里的一条候选。
#: ``node`` 是那个 obj_ref 的**节点本身**，改指向直接写它，不必回头再扫一遍。
_Cand = namedtuple(
    "_Cand", "node ref_oid field target_oid ref_title target_title to_source")


def describe_target(nd, rf, btn: QPushButton, index=None) -> None:
    """更新引用字段的**长按钮**：写出指向谁，并决定能不能「进去」。

    三种情形必须禁用长按钮，否则点下去会开出一个空窗口：

    - ``pathref`` —— 目标是 ``$/GFX/Terrains/…`` 这种资源路径，不是一个
      对象，没有字段可以编辑。
    - 空引用 —— 还没指向任何东西。
    - 跨文件 —— ``ModulesDescriptors`` 里的模块可能住在 ``Unit.ndfbin``，
      本编辑器一次只打开一个文件。

    **禁用而不是隐藏**：藏起来的话行的右半截会空掉，玩家看不出这里本来
    有一个「可以进去」的动作。灰着 + tooltip 才说得清为什么进不去。
    """
    index = index if index is not None else nd.ref
    text = refs.describe(index, rf)
    if rf.is_null:
        btn.setText(text)
        btn.setEnabled(False)
        btn.setToolTip(f"{rf.name}：{text}\n\n还没指向任何东西。\n"
                       f"先点右边的「替换」选一个目标。")
        return
    if rf.kind != "objref":
        btn.setText(text)
        btn.setEnabled(False)
        btn.setToolTip(
            f"{rf.name}：{text}\n\n"
            f"这是一个资源路径，不是一个对象——它自己没有字段可以改。\n"
            f"要换路径请点右边的「替换」。")
        return
    target = nd.entry_of(rf.key)
    if target is None:
        btn.setText(text)
        btn.setEnabled(False)
        btn.setToolTip(
            f"{rf.name}：{text}\n\n"
            f"这个目标不在当前打开的文件里（多半住在别的 .ndfbin）。\n"
            f"本编辑器一次只打开一个文件，所以进不去。")
        return
    btn.setText(text + ENTER_MARK)
    btn.setEnabled(True)
    btn.setToolTip(
        f"{rf.name}：{text}\n\n"
        f"点一下进去改这个对象自己的字段"
        f"（{target.class_name}，{len(target.fields)} 个可改字段）。\n"
        f"要换目标请点右边的「替换」。")


class ClassList(QListWidget):
    """左栏：类列表，带对象数与可改字段数。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._infos: list[ClassInfo] = []
        self._rebuilding = False

    def load(self, infos: list[ClassInfo]):
        self._infos = infos
        self.clear()
        for info in infos:
            if info.field_count == 0:
                label = f"{info.name}  ×{info.count}"
                item = QListWidgetItem(label)
                item.setForeground(QColor(130, 130, 130))
                item.setToolTip("这个类没有可编辑的数值字段")
                item.setData(Qt.UserRole, None)
            else:
                label = f"{info.name}  ×{info.count}   ({info.field_count} 字段)"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, info.name)
                item.setToolTip(
                    "\n".join(f"{humanize(n)}  {k}  ({c * 100:.0f}%)"
                              for n, k, c in info.fields[:24]))
            self.addItem(item)

    def refresh_counts(self):
        """只更新「×N」里的数字，不动选择。

        克隆对象（或撤销克隆）会让某个类的对象数变一变，而类列表在这里
        显示它。整表 ``load()`` 会丢掉当前选中行，所以走这条轻路。
        """
        for i, info in enumerate(self._infos):
            item = self.item(i)
            if item is None:
                continue
            if info.field_count == 0:
                item.setText(f"{info.name}  ×{info.count}")
            else:
                item.setText(
                    f"{info.name}  ×{info.count}   ({info.field_count} 字段)")

    def info_at(self, row: int):
        if 0 <= row < len(self._infos):
            return self._infos[row]
        return None

    @property
    def count(self) -> int:
        return len(self._infos)


class ObjectList(QListWidget):
    """中栏：某个类的所有实例。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[ObjEntry] = []
        self._rebuilding = False

    def load(self, entries: list[ObjEntry]):
        self._entries = entries
        self._rebuild()

    def _rebuild(self):
        self.clear()
        for e in self._entries:
            title = e.title or f"#{e.object_id}"
            label = f"{title}   #{e.object_id}"
            if e.ref_count > 1:
                label +=f"   ({e.ref_count} 装备共用)"
            item = QListWidgetItem(label)
            item.setToolTip(e.subtitle or f"object_id={e.object_id}")
            if e.dirty:
                item.setForeground(DIRTY_COLOR)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.addItem(item)

    def refresh_current(self):
        if self._rebuilding:
            return
        idx = self.currentRow()
        if 0 <= idx < len(self._entries):
            self._rebuilding = True
            try:
                self._rebuild()
                self.setCurrentRow(idx)
            finally:
                self._rebuilding = False

    def entry_at(self, row: int):
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    @property
    def count(self) -> int:
        return len(self._entries)


class RefPickerDialog(QDialog):
    """选择一个引用字段要指向什么。

    **三种规模共用同一个界面.** 实测候选集差异极大：

    ==========================================  ========
    字段                                        候选数
    ==========================================  ========
    ``TBlindageProperties.ResistanceFront``          62
    ``TTurretIdleBehaviourDescriptor``                5
    ``TAmmunitionDescriptor.HitRollRuleDescriptor`` 222
    ``TAmmunitionDescriptor.Ammunition``           1548
    ``TLandMovementModuleDescriptor.Terrain…``     6728
    ==========================================  ========

    所以刻意**不做**「小候选用下拉、大候选用搜索」的分支——两套界面等于让
    玩家学两次，而且分支条件对玩家不可见（他不会知道为什么这次是下拉）。
    一个带搜索的列表在 5 项和 6728 项下都够用。

    **排序即提示.** 列表按「被引用次数」降序——用得最多的通常就是最常见
    的那一档。这比按字母排序有用得多：玩家想找「常见的穿甲弹伤害类型」，
    它会出现在最上面，不需要认得它的名字。
    """

    #: 列表最多铺这么多行。Unit.ndfbin 有 6728 条路径，全塞进控件会卡，
    #: 而且没人会滚到第 3000 行——他要的是搜索。
    MAX_SHOWN = 600

    def __init__(self, parent, index, rf, owner_class: str = "",
                 *, class_choices=None):
        super().__init__(parent)
        self._index = index
        self._rf = rf
        self._choice: tuple[str, int | None] = ("null", None)
        self._all: list[refs.RefTarget] = []
        self._prefix = ""
        #: 允许跨类时（``ModulesDescriptors`` 这种混着 77 种模块的列表）
        #: 先让玩家挑一个类，再在那个类里选对象。空/单项时不显示这一行。
        self._class_choices = [c for c in (class_choices or ()) if c]
        self._class = rf.target_class or (
            self._class_choices[0] if self._class_choices else "")
        self.setWindowTitle(f"选择目标 —— {rf.name}")
        self.resize(800, 600)

        root = QVBoxLayout(self)

        head = QLabel()
        head.setWordWrap(True)
        owner = f"属于 <b>{owner_class}</b>，" if owner_class else ""
        if rf.kind == "objref":
            head.setText(
                f"<b>{rf.name}</b> {owner}指向<b>本文件里</b>的一个 "
                f"<b>{rf.target_class}</b> 对象。这个类共有 "
                f"{rf.pool_size} 个对象可以选。"
                f"<br><span style='color:#8ab4d8;'>列表按「被引用次数」排序，"
                f"用得最多的排在前面。</span>")
        else:
            head.setText(
                f"<b>{rf.name}</b> {owner}指向一个资源路径（$/GFX/…），"
                f"可能在本文件之外。本文件记录了 {rf.pool_size} 条路径。"
                f"<br><span style='color:#8ab4d8;'>同一个目录下的兄弟资源"
                f"排在最前面——同类东西通常放在一起。</span>")
        root.addWidget(head)

        if len(self._class_choices) > 1:
            cr = QHBoxLayout()
            cr.addWidget(QLabel("先选目标类："))
            self.class_box = QComboBox()
            self.class_box.addItems(self._class_choices)
            if self._class in self._class_choices:
                self.class_box.setCurrentText(self._class)
            self.class_box.currentTextChanged.connect(self._on_class)
            cr.addWidget(self.class_box, 1)
            root.addLayout(cr)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "搜索：输入名字的一部分，例如 T72 / AP / TrackVehicle / Descriptor_Unit")
        self.search.textChanged.connect(lambda _: self._refill())
        root.addWidget(self.search)

        self.listw = QListWidget()
        self.listw.setAlternatingRowColors(True)
        self.listw.itemDoubleClicked.connect(lambda _it: self.accept())
        self.listw.currentItemChanged.connect(self._on_current_changed)
        root.addWidget(self.listw, 1)

        self.used_by = QLabel("")
        self.used_by.setWordWrap(True)
        self.used_by.setStyleSheet("color:#c9a227;")
        root.addWidget(self.used_by)

        self.count = QLabel("")
        self.count.setStyleSheet("color:#999;")
        root.addWidget(self.count)

        row = QHBoxLayout()
        b_none = QPushButton("（不指向任何东西）")
        b_none.clicked.connect(self._pick_null)
        row.addWidget(b_none)
        row.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("用这个")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        root.addLayout(row)

        self._load()

    def _on_class(self, text: str):
        """换了目标类就重新拉候选——两个类的对象编号空间完全不同。"""
        self._class = text
        self._load()

    def _load(self):
        rf = self._rf
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if rf.kind == "objref":
                self._all = self._index.candidates_for_class(self._class)
            else:
                self._all = self._index.candidates_for_paths()
                self._prefix = self._index.siblings_of(rf.leaf)
                if self._prefix:
                    self._all.sort(key=lambda t: (
                        not (t.detail or "").startswith(self._prefix + "/"),
                        -t.uses, t.label.lower()))
        finally:
            QApplication.restoreOverrideCursor()
        self._refill()

    def _refill(self):
        needle = self.search.text().strip().lower()
        items = self._all
        if needle:
            items = [t for t in items
                     if needle in (t.label or "").lower()
                     or needle in (t.detail or "").lower()
                     or needle in (t.class_name or "").lower()]
        cur = self._rf.key
        self.listw.clear()
        for t in items[:self.MAX_SHOWN]:
            is_cur = (cur is not None and t.key == cur
                      and ((t.kind == "obj" and self._rf.kind == "objref")
                           or (t.kind == "path" and self._rf.kind == "pathref")))
            text = f"{'● ' if is_cur else '   '}{t.label}"
            if t.uses:
                text += f"    ·  {t.uses} 处引用"
            it = QListWidgetItem(text)
            # 存字符串而不是元组：Qt 的 UserRole 跨语言边界传容器类型时
            # 有时会把 tuple 变成 list，取值处就得写防御代码。
            it.setData(Qt.UserRole, f"{t.kind}|{t.key}")
            tip = t.detail or t.label
            if t.class_name:
                tip = f"{t.class_name}   {t.label}"
            it.setToolTip(tip)
            if is_cur:
                it.setForeground(QBrush(DIRTY_COLOR))
            self.listw.addItem(it)
        shown = min(len(items), self.MAX_SHOWN)
        msg = f"显示 {shown} / {len(items)} 条"
        if len(items) > self.MAX_SHOWN:
            msg += f"　（只铺了前 {self.MAX_SHOWN} 条，用搜索缩小范围）"
        if not items:
            msg = "没有匹配的候选。换个关键词，或者看看是不是所有同类对象都缺这个字段。"
        self.count.setText(msg)
        if self.listw.count():
            row = next((i for i in range(self.listw.count())
                        if self.listw.item(i).text().startswith("●")), 0)
            self.listw.setCurrentRow(row)
            self._on_current_changed(self.listw.currentItem(), None)

    def _on_current_changed(self, cur, _prev):
        """选中一项时，在下面说明「谁在用它」。

        ``TResistanceTypeRTTI #547`` 这种标签本身没有信息量——它的直接引用者
        只是 ``TBlindageProperties``，也不是玩家认得的东西。顺着
        ``装甲 ← 损伤模块 ← 实体`` 爬上去，就成了
        「2S19_MstaS_SOV、AMX_10_HOT_FR 等 450 个实体」——这才是能拿来做
        判断的依据。
        """
        if cur is None:
            self.used_by.setText("")
            return
        raw = cur.data(Qt.UserRole) or ""
        kind, _, key = raw.partition("|")
        if not key:
            self.used_by.setText("")
            return
        if kind == "path":
            self.used_by.setText(
                f"路径：{self._index.imports.get(int(key), '')}")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            names, total = self._index.entity_users(int(key))
        finally:
            QApplication.restoreOverrideCursor()
        if not total:
            self.used_by.setText(
                "这个对象没有被任何实体间接用到（可能被别的类型引用，"
                "或者是个孤立的备用对象）。")
            return
        shown = names[:5]
        head = "、".join(shown)
        if total > len(shown):
            self.used_by.setText(f"⚠ 改这里会同时影响到 {head} 等 {total} 个实体")
        elif total > 1:
            self.used_by.setText(f"⚠ 改这里会同时影响到 {head} 这 {total} 个实体")
        else:
            self.used_by.setText(f"改这里只会影响到 {head}")

    def _pick_null(self):
        self._choice = ("null", None)
        super().accept()

    def accept(self):
        it = self.listw.currentItem()
        if it is not None:
            kind, _, key = it.data(Qt.UserRole).partition("|")
            self._choice = (kind, int(key))
        super().accept()

    def chosen(self) -> tuple[str, int | None]:
        return self._choice


class RefListDialog(QDialog):
    """编辑「一串引用」—— 逐项换目标、增删项、跳到目标对象。

    实测这类字段的形态（``tools/probe_listrefs.py``）：

    ==================================  ======  ========  ==========
    字段                                中位数  最大长度  目标类数
    ==================================  ======  ========  ==========
    ``MountedWeaponDescriptorList``          1        4  1
    ``SmartGroupList``                       4       12  1
    ``ModulesDescriptors``（Unit）          46       53  **77**
    ``DeckPackList``                        77      268  1
    ==================================  ======  ========  ==========

    三个结论决定了这个界面的样子：

    1. **不能直接在字段面板里铺开** —— ``DeckPackList`` 有 268 项的，
       铺开就没法用了。所以走这个独立对话框。
    2. **候选集必须按「这一项」的目标类算** —— ``ModulesDescriptors``
       里混着 77 种模块，把 flags 模块换成弹药编码上合法但语义全乱。
    3. **增删是安全的**（``tools/probe_listadd.py`` 实测了完整往返）：
       列表元素是*引用*，增删只改宿主对象自己的 OBJE 字节，对象编号与
       CHNK 计数都不动。但 ``DeckPackList`` 这类列表的长度受游戏侧约束
       （一个槽位一个 pack）——所以允许改，同时把这件事说清楚。

    **非模态.** 每项的「查看」要打开对象编辑窗口，而模态对话框会把主窗口
    一起锁住。生命周期交给 ``MainWindow._tool_windows``：换文件时一起关、
    撤销后一起刷新。
    """

    #: 列表被改动了（换目标 / 增删 / 调序）。
    dirtyChanged = Signal()
    #: 想看某一项指向的对象。
    openObjectRequested = Signal(int)

    def __init__(self, nd, parent=None):
        super().__init__(parent)
        self.nd = nd
        self._entry = None
        self._field_name = ""
        self.setWindowTitle("一串引用")
        self.resize(880, 660)

        root = QVBoxLayout(self)
        self.head = QLabel("")
        self.head.setWordWrap(True)
        root.addWidget(self.head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索：过滤出含某个名字的项")
        self.search.textChanged.connect(lambda _: self._refill())
        root.addWidget(self.search)

        self.listw = QListWidget()
        self.listw.setAlternatingRowColors(True)
        root.addWidget(self.listw, 1)

        self.count = QLabel("")
        self.count.setStyleSheet("color:#999;")
        root.addWidget(self.count)

        row = QHBoxLayout()
        self.btn_add = QPushButton("+ 添加一项")
        self.btn_add.setToolTip(
            "在末尾补一项。候选集按这个列表本来的目标类算。\n"
            "注意：有些列表的长度由游戏侧决定（牌组里一个槽位一个 pack），"
            "加多了游戏里会对不上。")
        self.btn_add.clicked.connect(self._add)
        row.addWidget(self.btn_add)
        row.addStretch(1)
        b_close = QPushButton("完成")
        b_close.setDefault(True)
        b_close.clicked.connect(self.close)
        row.addWidget(b_close)
        root.addLayout(row)

    # ---- 外部接口 ----

    @property
    def field(self):
        if self._entry is None:
            return None
        return self._entry.ref_lists.get(self._field_name)

    def show_field(self, entry, field_name: str) -> None:
        """切到某个对象的某个「一串引用」字段。

        同一个窗口反复复用——玩家在对象列表里换一个对象、点开同一个字段，
        不该又弹一个新窗口出来。
        """
        self._entry = entry
        self._field_name = field_name
        self.search.clear()
        f = self.field
        self.head.setText(self._head_text(f) if f is not None else "")
        self.setWindowTitle(
            f"{field_name} —— {f.length} 项" if f is not None else field_name)
        self._refill()

    @staticmethod
    def _head_text(f) -> str:
        tcs = f.target_classes()
        if len(tcs) == 1:
            what = f"每一项都是一个 <b>{tcs[0]}</b>"
        elif tcs:
            what = (f"这个列表里混着 <b>{len(tcs)} 种</b>不同的目标"
                    f"（{'、'.join(tcs[:3])} …）")
        else:
            what = "目前没有任何项"
        return (f"<b>{f.name}</b> 有 {f.length} 项，{what}。"
                f"<br>每一项可以 <b>换目标</b>、<b>查看它指的对象</b>、"
                f"<b>删除</b>，也能上移下移；底部可以 <b>添加一项</b>。")

    def refresh(self) -> None:
        """撤销 / 重做之后由主窗口调用——列表可能整条变了。"""
        self._refill()

    # ---- 列表 ----

    def _refill(self):
        pos = self.listw.verticalScrollBar().value()
        self.listw.clear()
        f = self.field
        if f is None:
            self.count.setText("")
            self.btn_add.setEnabled(False)
            return
        needle = self.search.text().strip().lower()
        shown = 0
        changed = set(f.dirty_indices())
        for i, rf in enumerate(f.items):
            full = refs.describe(self.nd.ref, rf)
            label = refs.friendly(full)
            if needle and needle not in label.lower():
                continue
            it = QListWidgetItem("")
            it.setData(Qt.UserRole, i)
            self.listw.addItem(it)
            self.listw.setItemWidget(
                it, self._make_row(i, rf, label, full, i in changed))
            shown += 1
        bits = [f"{f.name}：{f.length} 项"]
        if shown != f.length:
            bits.append(f"筛出 {shown} 项")
        if changed:
            bits.append(f"{len(changed)} 项与打开时不同")
        self.count.setText("，".join(bits))
        self.btn_add.setEnabled(True)
        self.listw.verticalScrollBar().setValue(pos)

    def _make_row(self, i: int, rf, label: str, full: str,
                  changed: bool) -> QWidget:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(6, 3, 6, 3)
        hl.setSpacing(6)
        num = QLabel(f"{i}")
        num.setFixedWidth(34)
        num.setStyleSheet("color:#ffd66a;" if changed else "color:#888;")
        name = QLabel(label)
        if changed:
            name.setStyleSheet("color:#ffd66a;")
        # tooltip 给全名（含 Descriptor_ 前缀）——列表里剥掉了，
        # 但要和 NDF 源码对得上时还得看全名。
        name.setToolTip(refs.describe_full(self.nd.ref, rf)
                        + (f"\n显示名：{full}" if full and full != label else ""))
        hl.addWidget(num)
        hl.addWidget(name, 1)
        for text, width, tip, slot in (
            ("↑", 30, "往上挪一位——第 0 项常常是主武器，顺序有含义",
             lambda _=False, n=i: self._move(n, -1)),
            ("↓", 30, "往下挪一位",
             lambda _=False, n=i: self._move(n, 1)),
            ("查看", 52, "打开它指向的对象，看 / 改那个对象自己的字段",
             lambda _=False, n=i: self._view(n)),
            ("换…", 52, "换一个目标",
             lambda _=False, n=i: self._swap(n)),
            ("删", 40, "删掉这一项（可以撤销）",
             lambda _=False, n=i: self._remove(n)),
        ):
            b = QPushButton(text)
            b.setFixedWidth(width)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            hl.addWidget(b)
        return w

    def _notify(self) -> None:
        self._refill()
        self.dirtyChanged.emit()

    # ---- 动作 ----

    def _view(self, i: int):
        f = self.field
        if f is None or not (0 <= i < len(f.items)):
            return
        key = f.items[i].key
        if key is None:
            QMessageBox.information(self, "看不了", "这一项没有指向任何对象。")
            return
        self.openObjectRequested.emit(key)

    def _swap(self, i: int):
        f = self.field
        if f is None or not (0 <= i < len(f.items)):
            return
        rf = f.items[i]
        dlg = RefPickerDialog(self, self.nd.ref, rf,
                              owner_class=f"{self._entry.class_name}."
                                          f"{f.name}[{i}]")
        if dlg.exec() != QDialog.Accepted:
            return
        kind, key = dlg.chosen()
        try:
            self.nd.ref.repoint(rf.leaf, kind, key)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, "换不了", str(exc))
            return
        self._notify()

    def _remove(self, i: int):
        f = self.field
        if f is None or not (0 <= i < f.length):
            return
        if QMessageBox.question(
                self, "删掉这一项",
                f"删掉第 {i} 项？\n\n"
                f"这一步只改变「{f.name}」这个列表本身，不动被指向的对象。\n"
                f"注意：有些列表的长度由游戏侧决定（牌组里一个槽位一个 "
                f"pack），删多了游戏里会对不上。\n\n"
                f"删错了随时可以 Ctrl+Z 退回来。") != QMessageBox.Yes:
            return
        f.remove(i)
        self._notify()

    def _move(self, i: int, delta: int):
        f = self.field
        if f is None:
            return
        if f.move(i, delta) != i:
            self._notify()

    def _add(self):
        f = self.field
        if f is None:
            return
        tcs = f.target_classes()
        if not tcs:
            QMessageBox.information(
                self, "加不了",
                "这个列表目前是空的，看不出该往里面放什么类的东西。\n\n"
                "空列表在文件里几乎不存在（这种情况通常是统计出来的字段表"
                "与当前对象不匹配），换一个有内容的同类对象再试。")
            return
        # 候选集按列表本来的目标类算。混类的（ModulesDescriptors 有 77 种）
        # 让玩家先在下拉里挑一个类——给整个混类列表一个统一候选集，
        # 等于允许把一个 flags 模块换成弹药。
        probe = refs.RefField(
            name=f.name, kind="objref",
            leaf={"type_id": refs.OBJECT_REFERENCE, "type": "obj_ref",
                  "reference_prefix": False,
                  "object_id": refs.NULL_REF, "class_id": refs.NULL_REF},
            original_kind="null", original_key=-1,
            target_class=tcs[0], coverage=1.0,
            pool_size=len(self.nd.ref.pool_of_class(tcs[0])))
        dlg = RefPickerDialog(self, self.nd.ref, probe,
                              owner_class=f"{self._entry.class_name}.{f.name}",
                              class_choices=tcs)
        if dlg.exec() != QDialog.Accepted:
            return
        kind, key = dlg.chosen()
        if key is None:
            return
        try:
            f.add(kind, key)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, "加不了", str(exc))
            return
        self._notify()
        self.listw.scrollToBottom()


class ObjectEditorDialog(QDialog):
    """编辑「所指涉的对象」的窗口 —— 一个带面包屑的钻取栈。

    **为什么不每层开一个新窗口。** 钻三层就是三个叠着的窗口，关的时候
    得记住顺序，而且看不出「我现在在哪一层、是从哪儿来的」。做成
    单窗口加面包屑：每次进去压一节、点面包屑回退，位置永远只有一处。

    复用同一个 :class:`FieldPanel`，所以这里的每一行、每一个引用字段的
    行为和主界面完全一致——**里面的长按钮再点就继续压栈**，不用另写一套。
    面板自己不决定去哪：它只发 ``openObjectRequested``，由窗口决定压栈，
    由主窗口决定开窗。

    非模态。玩家可以一边看主界面一边钻，而且两边改的是同一份原位 dict，
    未保存状态与撤销天然共用。
    """

    dirtyChanged = Signal()
    #: 玩家要求「在主界面定位」当前这个对象。
    locateRequested = Signal(int)
    cloneRequested = Signal(object)
    #: 面板要求打开「一串引用」编辑器——原样转发给主窗口（它管窗口生命周期）。
    openRefListRequested = Signal(object, str)

    def __init__(self, nd, parent=None):
        super().__init__(parent)
        self.nd = nd
        #: [(ObjEntry, 进入时用的字段名)]，最后一项是当前层。
        self._stack: list[tuple[ObjEntry, str]] = []
        self.setWindowTitle("编辑所指涉的对象")
        self.resize(560, 680)

        root = QVBoxLayout(self)

        self.header = QLabel("")
        f = self.header.font()
        f.setPointSize(f.pointSize() + 1)
        f.setBold(True)
        self.header.setFont(f)
        self.header.setWordWrap(True)
        root.addWidget(self.header)

        # 面包屑放在滚动区里：链路长了不至于把窗口撑宽。
        self.crumbs = QScrollArea()
        self.crumbs.setWidgetResizable(True)
        self.crumbs.setMaximumHeight(40)
        self.crumbs.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.crumb_lay = QHBoxLayout(holder)
        self.crumb_lay.setContentsMargins(0, 2, 0, 2)
        self.crumb_lay.setSpacing(2)
        self.crumbs.setWidget(holder)
        root.addWidget(self.crumbs)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        self.panel = FieldPanel()
        self.panel.set_document(nd)
        self.panel.dirtyChanged.connect(self.dirtyChanged.emit)
        self.panel.openObjectRequested.connect(self._on_open_requested)
        self.panel.cloneRequested.connect(self.cloneRequested.emit)
        self.panel.openRefListRequested.connect(self.openRefListRequested.emit)
        root.addWidget(self.panel, 1)

        foot = QHBoxLayout()
        self.note = QLabel("改动记在主窗口的未保存状态里，共用同一套撤销。")
        self.note.setStyleSheet("color:#999;")
        self.note.setWordWrap(True)
        self.btn_locate = QPushButton("在主界面定位")
        self.btn_locate.setToolTip("把主界面切到当前这个对象——想同时看两边时用。")
        self.btn_locate.clicked.connect(self._on_locate)
        btn_back = QPushButton("返回上一层")
        btn_back.clicked.connect(self.pop)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        foot.addWidget(self.note, 1)
        foot.addWidget(self.btn_locate)
        foot.addWidget(btn_back)
        foot.addWidget(btn_close)
        root.addLayout(foot)
        self._btn_back = btn_back

    # ---- 钻取栈 ----

    def push(self, entry, via: str = "") -> None:
        """进入一层。传同一个对象不会重复压栈（避免来回点撑出一串）。"""
        if entry is None:
            return
        if self._stack and self._stack[-1][0].object_id == entry.object_id:
            self._paint()
            return
        self._stack.append((entry, via))
        self._paint()

    def pop(self) -> None:
        if len(self._stack) <= 1:
            return
        self._stack.pop()
        self._paint()

    def pop_to(self, index: int) -> None:
        if 0 <= index < len(self._stack) - 1:
            del self._stack[index + 1:]
            self._paint()

    def _on_open_requested(self, object_id: int) -> None:
        self.push(self.nd.entry_of(object_id))

    def _on_locate(self) -> None:
        if self._stack:
            self.locateRequested.emit(self._stack[-1][0].object_id)

    # ---- 重画 ----

    def refresh(self) -> None:
        """数据在别处被改了（撤销、批量改指向）之后重画这一层。"""
        if self._stack:
            self._paint()

    def _paint(self) -> None:
        if not self._stack:
            return
        entry, _via = self._stack[-1]
        info = self.nd.class_info(entry.class_name)
        # 类变了必须重建表单——字段行是按类生成的。
        if getattr(self, "_built_class", None) != entry.class_name:
            self.panel.build_for(info)
            self._built_class = entry.class_name
        self.panel.show_entry(entry, info)

        self.header.setText(
            f"{entry.class_name}  #{entry.object_id}"
            + (f"  ·  {entry.title}" if entry.title else ""))
        self._fill_crumbs()
        self._btn_back.setEnabled(len(self._stack) > 1)
        self.note.setText(
            f"第 {len(self._stack)} 层 · 改动记在主窗口的未保存状态里，"
            f"共用同一套撤销。")

    def _fill_crumbs(self) -> None:
        while self.crumb_lay.count():
            item = self.crumb_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        last = len(self._stack) - 1
        for i, (entry, via) in enumerate(self._stack):
            if i:
                sep = QLabel("›")
                sep.setStyleSheet("color:#777;")
                self.crumb_lay.addWidget(sep)
            btn = QPushButton(entry.title or f"#{entry.object_id}")
            btn.setFlat(True)
            tip = f"{entry.class_name} #{entry.object_id}"
            if via:
                tip += f"\n从「{humanize(via)}」进来"
            btn.setToolTip(tip)
            if i == last:
                btn.setEnabled(False)          # 当前层，没什么可点的
                btn.setStyleSheet("color:#ffd66a;font-weight:bold;")
            else:
                btn.clicked.connect(lambda _, k=i: self.pop_to(k))
            self.crumb_lay.addWidget(btn)
        self.crumb_lay.addStretch(1)


class RefBulkDialog(QDialog):
    """克隆之后：勾选哪些引用字段改指向新对象。

    实测一个弹药类有 **9001** 个候选引用实例（``TMountedWeaponDescriptor
    .Ammunition`` 8995 处 + ``TFireModuleDescriptor.AmmunitionForBurn``
    6 处）。量过了：扫描 34ms、全部标签 19ms、``QTableWidget``
    4 列 × 9001 行 66ms、排序 27ms——**撑得住，所以全部列出来**，
    靠搜索和排序去找，而不是先替玩家收敛掉。

    排序把「现在指向原件的」放最前：实测每个弹药只被 0~7 个字段引用，
    真正想改的那几处会立刻浮到顶上，而「指向别处的」也仍在列表里——
    想把一个全新的武器指过来同样做得到。

    勾选**默认**打在这些「指向原件的」上面：克隆的常见用途就是「拿原件
    改一改再换上去」。不想要就全选/反选，或直接跳过。
    """

    dirtyApplied = Signal()

    def __init__(self, nd, clone, source, guid_fields, parent=None):
        super().__init__(parent)
        self.nd = nd
        self.clone = clone
        self.source = source
        self.guid_fields = list(guid_fields)
        self.setWindowTitle("让哪些字段改指向新建的对象？")
        self.resize(940, 620)

        root = QVBoxLayout(self)
        head = QLabel("")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜引用者名字、字段名或当前目标")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = QPushButton("全不选")
        btn_none.clicked.connect(lambda: self._set_all(False))
        btn_inv = QPushButton("反选")
        btn_inv.clicked.connect(self._invert)
        bar.addWidget(self.search, 1)
        bar.addWidget(btn_all)
        bar.addWidget(btn_none)
        bar.addWidget(btn_inv)
        root.addLayout(bar)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["改指向", "引用者 · 字段", "现在指向"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        root.addWidget(self.table, 1)

        self.count = QLabel("")
        self.count.setStyleSheet("color:#8ab4d8;")
        root.addWidget(self.count)

        foot = QHBoxLayout()
        note = QLabel("确认后合并成一步改动，Ctrl+Z 一次全部退回。")
        note.setStyleSheet("color:#999;")
        foot.addWidget(note, 1)
        btn_skip = QPushButton("跳过，之后再改")
        btn_skip.clicked.connect(self.reject)
        self.btn_apply = QPushButton("确认改指向")
        self.btn_apply.clicked.connect(self.on_apply)
        foot.addWidget(btn_skip)
        foot.addWidget(self.btn_apply)
        root.addLayout(foot)

        self._build_candidates()
        head.setText(
            f"新建 <b>{clone.class_name} #{clone.object_id}</b>，"
            f"克隆自 <b>#{source.object_id}"
            + (f" {source.title}" if source.title else "")
            + "</b>。<br>下面列出全部指向这个类的引用字段；"
            "勾选后把它们改指向新对象。")
        if self.guid_fields:
            head.setText(head.text()
                         + f"<br><span style='color:#8ab4d8;'>新对象已重新生成 GUID："
                           f"{'、'.join(self.guid_fields)}</span>")
        self._refill()

    # ---- 候选集 ----

    def _build_candidates(self):
        """把全部候选引用实例扫出来。"""
        raw = self.nd.referrers_for_class(self.clone.class_name)
        titles: dict[int, str] = {}
        tgt_labels: dict[int, str] = {}
        index = self.nd.ref
        out = []
        for ref_oid, field, target_oid, node in raw:
            if ref_oid not in titles:
                e = self.nd.entry_of(ref_oid)
                titles[ref_oid] = (e.title or f"#{ref_oid}") if e else f"#{ref_oid}"
            if target_oid not in tgt_labels:
                tgt_labels[target_oid] = (
                    f"#{target_oid}" if target_oid in (None, 0xFFFFFFFF)
                    else index.label_of(target_oid))
            out.append(_Cand(node, ref_oid, field, target_oid,
                             titles[ref_oid], tgt_labels[target_oid],
                             target_oid == self.source.object_id))
        # 指向原件的排前面——那几处几乎总是玩家想改的。
        out.sort(key=lambda c: (not c.to_source, c.ref_title.lower(),
                                c.field))
        self._all = out

    def _refill(self):
        """按搜索词重建表格。

        重建而不是隐藏行：9001 行重建 66ms，而逐行 setRowHidden 要调用
        9001 次，更慢也更难保证计数对得上。
        """
        needle = self.search.text().strip().lower()
        keep = []
        for cand in self._all:
            if needle and not (needle in cand.ref_title.lower()
                               or needle in cand.field.lower()
                               or needle in cand.target_title.lower()):
                continue
            keep.append(cand)
            if len(keep) >= MAX_CANDIDATE_ROWS:
                break
        self._shown = keep
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(keep))
        for i, cand in enumerate(keep):
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            box.setCheckState(Qt.Checked if cand.to_source else Qt.Unchecked)
            box.setData(Qt.UserRole, i)
            self.table.setItem(i, 0, box)

            # 带上 object_id：**引用者的显示名会重名**。实测搜「BMP」命中
            # 119 个不同对象，标题全是 FireEffect_ATGM_9M113_Konkurs_BMP2
            # 之类——没有 id 的话 9001 行列表里根本分不出哪行是哪行。
            name = QTableWidgetItem(
                f"{cand.ref_title}  ·  {cand.field}   #{cand.ref_oid}")
            if cand.to_source:
                name.setForeground(QBrush(DIRTY_COLOR))
            ref_entry = self.nd.entry_of(cand.ref_oid)
            name.setToolTip(
                f"{ref_entry.class_name if ref_entry else '?'} "
                f"#{cand.ref_oid} 的 {cand.field}")
            self.table.setItem(i, 1, name)

            tgt = QTableWidgetItem(cand.target_title)
            tgt.setToolTip(f"object #{cand.target_oid}"
                           if cand.target_oid not in (None, 0xFFFFFFFF)
                           else "空引用")
            self.table.setItem(i, 2, tgt)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._update_count()

    def _update_count(self):
        n = len(self._checked())
        total = len(self._all)
        to_src = sum(1 for c in self._all if c.to_source)
        self.count.setText(
            f"列表 {len(self._shown)} / {total} 条　·　"
            f"其中现在指向原件的 {to_src} 条　·　已勾选 {n} 条")
        self.btn_apply.setEnabled(n > 0)
        self.btn_apply.setText(f"确认改指向（{n}）" if n else "确认改指向")

    def _checked(self) -> list:
        out = []
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item is not None and item.checkState() == Qt.Checked:
                out.append(self._shown[i])
        return out

    def _set_all(self, on: bool):
        state = Qt.Checked if on else Qt.Unchecked
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item is not None:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_count()

    def _invert(self):
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked
                                   if item.checkState() == Qt.Checked
                                   else Qt.Checked)
        self._update_count()

    # ---- 应用 ----

    def on_apply(self):
        picked = self._checked()
        if not picked:
            return
        done = 0
        failed: list[str] = []
        for cand in picked:
            try:
                self.nd.ref.repoint(cand.node, "obj", self.clone.object_id)
                done += 1
            except (KeyError, ValueError) as exc:
                failed.append(f"{cand.ref_title} 的 {cand.field}：{exc}")
        if not done:
            QMessageBox.warning(
                self, "一处也没改成", "\n".join(failed[:5]) or "没有可改的。")
            return
        self.dirtyApplied.emit()
        msg = (f"{done} 处改为指向 #{self.clone.object_id}"
               f"（{self.clone.class_name}）。\n\n"
               f"已计入未保存状态，回主界面点「保存到文件」才写盘。\n"
               f"按一次 Ctrl+Z 可以全部退回。")
        if failed:
            msg += f"\n\n另有 {len(failed)} 处没改成：\n" + "\n".join(failed[:3])
        QMessageBox.information(self, "改好了", msg)
        self.accept()


class StrPickerDialog(QDialog):
    """挑一个字符串——数据源是文件自己的 STRG 表。

    **不新增字符串.** 新增要重建 STRG 分区，而 ``NdfDocument.replacements()``
    对它是原样透传的。所以这里只能换成表里**已经存在**的名字。对
    ``ImpactHappening``（爆炸特效名）和 ``TraitsToken``（UI 标签）来说
    这正好够用——游戏认得的名字本来就在表里，不认得的名字写进去也没用。

    **按使用次数排序，不是字母序.** 表里有 5734 项，而弹药用到的特效名只有
    几十个。玩家要找 ``RoquetteRecoilessGun`` 时，他其实想知道的是
    「同类的弹药在用什么」——那些名字恰好都是高频的。
    """

    MAX_SHOWN = 600

    def __init__(self, parent, title: str, current, pool, *, prefer=(),
                 note: str = ""):
        super().__init__(parent)
        self._pool = pool
        self._current = current
        self._prefer = tuple(prefer)
        self._choice: tuple[int, str] | None = None
        self.setWindowTitle(f"选择字符串 —— {title}")
        self.resize(780, 620)

        root = QVBoxLayout(self)
        head = QLabel(note or (
            "换成一个<b>本文件里已经存在</b>的字符串。"
            "只能换成已有的名字，不能新建——新名字需要重建字符串表。"
            "<br><span style='color:#8ab4d8;'>列表按「被引用次数」排序，"
            "同类的对象在用什么名字，基本就在最前面。</span>"))
        head.setWordWrap(True)
        root.addWidget(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "搜索：输入名字的一部分，例如 Roquette / HE / Canon / Fusil")
        self.search.textChanged.connect(lambda _: self._refill())
        root.addWidget(self.search)

        self.listw = QListWidget()
        self.listw.setAlternatingRowColors(True)
        self.listw.itemDoubleClicked.connect(lambda _it: self.accept())
        root.addWidget(self.listw, 1)

        self.count = QLabel("")
        self.count.setStyleSheet("color:#999;")
        root.addWidget(self.count)

        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("用这个")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        root.addLayout(row)

        self._refill()

    def _refill(self):
        hits = self._pool.search(self.search.text(), limit=100000,
                                 prefer=self._prefer)
        self.listw.clear()
        for i, text, uses in hits[:self.MAX_SHOWN]:
            label = f"{'● ' if i == self._current else '   '}{text}"
            if uses:
                label += f"    ·  {uses} 处引用"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, f"{i}|{text}")
            if i == self._current:
                it.setForeground(QBrush(DIRTY_COLOR))
            self.listw.addItem(it)
        shown = min(len(hits), self.MAX_SHOWN)
        if not hits:
            msg = "表里没有匹配的字符串。只能换成已有的名字，不能新建。"
        else:
            msg = f"显示 {shown} / {len(hits)} 条"
            if len(hits) > self.MAX_SHOWN:
                msg += f"　（只铺了前 {self.MAX_SHOWN} 条，用搜索缩小范围）"
        self.count.setText(msg)
        if self.listw.count():
            self.listw.setCurrentRow(0)

    def accept(self):
        it = self.listw.currentItem()
        if it is not None:
            i, _, text = (it.data(Qt.UserRole) or "").partition("|")
            if i:
                self._choice = (int(i), text)
        super().accept()

    def chosen(self) -> tuple[int, str] | None:
        return self._choice


class StrListDialog(QDialog):
    """编辑一个字符串列表——``TraitsToken`` 这类「UI 标签」字段。

    **这里可以增删项.** 对象引用列表不行：长度受游戏侧槽位约束，改长度
    还要重新编号后续对象。而字符串列表的长度在文件里**只是一个计数**，
    编码时会校验它等于项数，改对了就行。而「去掉 TANDEM」本来就是
    mod 作者最想做的操作，做成只能换不能删等于没做。

    顺序也有意义——标签在 UI 上是按顺序排的，所以给了上移/下移。
    """

    def __init__(self, parent, field, pool, owner: str = ""):
        super().__init__(parent)
        self._f = field
        self._pool = pool
        self.setWindowTitle(f"编辑一串字符串 —— {field.name}")
        self.resize(660, 540)

        root = QVBoxLayout(self)
        head = QLabel()
        head.setWordWrap(True)
        owner_bit = f"（属于 <b>{owner}</b>）" if owner else ""
        head.setText(
            f"<b>{field.name}</b>{owner_bit} 是一串字符串，在游戏里就是 UI 上"
            f"那排标签。<br>与「一串引用」不同，这里<b>可以增删项</b>——"
            f"列表长度在文件里只是一个计数。"
            f"<br><span style='color:#8ab4d8;'>改完关掉这个窗口即生效，"
            f"用 Ctrl+Z 可以整轮退回来。</span>")
        root.addWidget(head)

        self.listw = QListWidget()
        self.listw.setAlternatingRowColors(True)
        self.listw.itemDoubleClicked.connect(lambda _it: self._edit())
        root.addWidget(self.listw, 1)

        row = QHBoxLayout()
        for text, slot, tip in (
            ("添加", self._add, "从已有字符串里挑一个，追加到末尾"),
            ("修改", self._edit, "把选中的那一项换成别的字符串"),
            ("删除", self._remove, "去掉选中的那一项"),
            ("上移", lambda: self._move(-1), "往前挪一位（标签顺序会变）"),
            ("下移", lambda: self._move(1), "往后挪一位"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        b_done = QPushButton("完成")
        b_done.setDefault(True)
        b_done.clicked.connect(self.accept)
        row.addWidget(b_done)
        root.addLayout(row)

        self._refill()

    def _refill(self):
        keep = self.listw.currentRow()
        self.listw.clear()
        for i, text in enumerate(self._f.texts()):
            it = QListWidgetItem(f"{i + 1}.  {text}")
            self.listw.addItem(it)
        if self.listw.count():
            self.listw.setCurrentRow(min(max(keep, 0), self.listw.count() - 1))

    def _add(self):
        dlg = StrPickerDialog(self, self._f.name, None, self._pool)
        if dlg.exec() != QDialog.Accepted:
            return
        got = dlg.chosen()
        if got:
            self._f.add(got[0], got[1])
            self._refill()
            self.listw.setCurrentRow(self.listw.count() - 1)

    def _edit(self):
        i = self.listw.currentRow()
        if i < 0:
            return
        dlg = StrPickerDialog(self, self._f.name, self._f.indices()[i],
                              self._pool)
        if dlg.exec() != QDialog.Accepted:
            return
        got = dlg.chosen()
        if got:
            self._f.set_item(i, got[0], got[1])
            self._refill()
            self.listw.setCurrentRow(i)

    def _remove(self):
        i = self.listw.currentRow()
        if i < 0:
            return
        self._f.remove(i)
        self._refill()

    def _move(self, delta: int):
        i = self.listw.currentRow()
        if i < 0:
            return
        j = self._f.move(i, delta)
        self._refill()
        self.listw.setCurrentRow(j)


class HashPickerDialog(QDialog):
    """照抄一个哈希值。

    ``loc_hash`` **不可逆**——没有「输入一个新值」这种操作，因为哈希对应
    的文本在游戏词典里，而且词典是加密的。能做的只有「跟那个对象一样」。

    所以这个界面列的是**同类对象实际用过的值**，每行配上「多少个同类在
    用」和「一个例子」。只给一串十六进制让人挑是没有意义的；
    「有 124 个同类对象在用，例如 2S19 MstaS」才是能拿来做判断的依据。
    """

    def __init__(self, parent, field_name: str, current: str, choices,
                 owner: str = ""):
        super().__init__(parent)
        self._current = (current or "").lower()
        self._choice: str | None = None
        self.setWindowTitle(f"照抄一个值 —— {field_name}")
        self.resize(760, 560)

        root = QVBoxLayout(self)
        head = QLabel()
        head.setWordWrap(True)
        owner_bit = f"（属于 <b>{owner}</b>）" if owner else ""
        head.setText(
            f"<b>{field_name}</b>{owner_bit} 是一个<b>不可逆的哈希</b>，"
            f"对应的文本存在游戏词典里。所以不能直接输入名字——"
            f"只能从同类对象已经用过的值里挑一个照抄。"
            f"<br><span style='color:#8ab4d8;'>列表按「多少个同类对象在用」"
            f"排序。挑一个用途相近的对象，它的值就是你要的。</span>")
        root.addWidget(head)

        self.listw = QListWidget()
        self.listw.setAlternatingRowColors(True)
        self.listw.itemDoubleClicked.connect(lambda _it: self.accept())
        root.addWidget(self.listw, 1)

        self.count = QLabel("")
        self.count.setStyleSheet("color:#999;")
        root.addWidget(self.count)

        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("用这个")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        root.addLayout(row)

        for h, n, sample in choices:
            label = f"{'● ' if h == self._current else '   '}{h}    ·  " \
                    f"{n} 个同类对象在用    ·  例如 {sample}"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, h)
            if h == self._current:
                it.setForeground(QBrush(DIRTY_COLOR))
            self.listw.addItem(it)
        self.count.setText(f"同类对象一共用过 {len(choices)} 个不同的值")
        if self.listw.count():
            cur = next((i for i in range(self.listw.count())
                        if self.listw.item(i).data(Qt.UserRole) == self._current),
                       0)
            self.listw.setCurrentRow(cur)

    def accept(self):
        it = self.listw.currentItem()
        if it is not None:
            self._choice = it.data(Qt.UserRole)
        super().accept()

    def chosen(self) -> str | None:
        return self._choice


class ChangePreviewDialog(QDialog):
    """保存前的逐字段对照。

    **为什么不能只给数量.** 「已改动 150 个对象」说服不了一个正要按保存的
    玩家——他需要看到 ``Maximum Range GRU: 1925 → 2100`` 才能确认改的是
    想改的那批。批量操作尤其如此：筛选条件里错一个数字，改掉的可能
    完全是另外 150 条，而数量看起来一模一样。
    """

    #: 铺这么多行就够。批量改可能上千处，但玩家只要确认「对不对劲」，
    #: 全铺出来只会把界面卡住。
    MAX_ROWS = 3000

    def __init__(self, nd, parent=None):
        super().__init__(parent)
        self.nd = nd
        self.setWindowTitle("确认保存 —— 逐字段对照")
        self.resize(1020, 700)

        root = QVBoxLayout(self)

        rows = nd.changes(limit=self.MAX_ROWS + 1)
        truncated = len(rows) > self.MAX_ROWS
        rows = rows[:self.MAX_ROWS]

        n_obj = nd.dirty_count()
        n_added = nd.added_total()
        n_ref = nd.ref_dirty_total()
        n_list = nd.ref_list_dirty_total()
        n_text = nd.text_dirty_total()
        total_text = (f"超过 {self.MAX_ROWS} 处改动" if truncated
                      else f"{len(rows)} 处改动")
        extra = []
        if n_added:
            extra.append(f"{n_added} 处是补加的新属性")
        if n_ref:
            extra.append(f"{n_ref} 处改了指向")
        if n_list:
            extra.append(f"{n_list} 处改了列表里的引用")
        if n_text:
            extra.append(f"{n_text} 处改了名字 / 标签 / 哈希")
        head = QLabel(
            f"将写入 <b>{nd.path}</b><br>"
            f"{n_obj} 个对象，{total_text}"
            + ("（" + "，".join(extra) + "）" if extra else "")
            + "<br><span style='color:#ffd66a;'>首次保存会在同目录生成"
              " .bak 原始备份，此后不再覆盖。</span>")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["对象", "字段", "改前", "改后"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        for i, r in enumerate(rows):
            cells = [r.title, humanize(r.field), r.before, r.after]
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if r.kind in ("ref", "list", "str", "strlist", "hash"):
                    it.setForeground(QBrush(QColor(200, 170, 90)))
                table.setItem(i, j, it)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for j in (1, 2, 3):
            header.setSectionResizeMode(j, QHeaderView.ResizeToContents)
        root.addWidget(table, 1)

        if truncated:
            note = QLabel(
                f"改动太多，只列出前 {self.MAX_ROWS} 处。"
                f"文件里实际会写入全部改动。")
            note.setStyleSheet("color:#ffd66a;")
            root.addWidget(note)

        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        row.addWidget(b_cancel)
        b_ok = QPushButton("写入文件")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        row.addWidget(b_ok)
        root.addLayout(row)


class FieldPanel(QWidget):
    """右栏：字段编辑面板。编辑器按所选类实际拥有的字段动态生成。"""

    dirtyChanged = Signal()
    #: 玩家点了某个引用字段的**长按钮**，想进入所指涉的对象（带 object_id）。
    #: 面板自己不开窗口——由外面决定是新开一个对象编辑窗口，还是压面包屑。
    openObjectRequested = Signal(int)
    #: 玩家要求克隆当前对象。参数是 ObjEntry。
    cloneRequested = Signal(object)
    #: 玩家要打开「一串引用」的编辑器（参数是 `(ObjEntry, 字段名)`）。
    #: 面板自己不开窗口——那个对话框是非模态的，生命周期归主窗口统一管。
    #: 带上 entry 是因为同一个面板会被主窗口和对象钻取窗口共用。
    openRefListRequested = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._document = None      # NdfDocument，添加/移除字段靠它改对象图
        self._entry: ObjEntry | None = None
        self._editors: dict[str, QWidget] = {}
        self._labels: dict[str, QWidget] = {}
        self._add_buttons: dict[str, QPushButton] = {}
        #: ``*GRU`` 字段旁的换算标签，键是字段名。
        self._km_labels: dict[str, QLabel] = {}
        #: 引用字段的「选目标」按钮。和 _editors 并列，因为它的值不是数字。
        self._ref_buttons: dict[str, QPushButton] = {}
        #: 引用字段的「进入目标」长按钮。
        self._ref_open: dict[str, QPushButton] = {}
        self._ref_labels: dict[str, QWidget] = {}
        #: 「一串引用」字段的按钮。
        self._list_buttons: dict[str, QPushButton] = {}
        #: 字符串引用字段（``ImpactHappening`` 这类）的按钮。
        self._str_buttons: dict[str, QPushButton] = {}
        #: 字符串列表字段（``TraitsToken``）的按钮。
        self._str_list_buttons: dict[str, QPushButton] = {}
        #: 哈希字段（``TypeCategoryName``）的按钮——点开是「照抄」。
        self._hash_buttons: dict[str, QPushButton] = {}
        #: 实体聚合区的控件。单独一份，避免和本对象字段同名冲突。
        self._entity_editors: dict[str, QWidget] = {}
        self._entity_refs: dict[str, QPushButton] = {}
        self._entity_rows: list = []
        self._shared_lookup = None
        self._info: ClassInfo | None = None
        self._loading = False

        root = QVBoxLayout(self)
        self.header = QLabel("未选择对象")
        f = self.header.font()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        self.header.setFont(f)
        self.header.setWordWrap(True)
        root.addWidget(self.header)

        self.sub = QLabel("")
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet("color:#999;")
        root.addWidget(self.sub)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#8ab4d8;")
        root.addWidget(self.summary)

        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(
            "background:#3d3320; border:1px solid #6b5a2a; border-radius:4px;"
            "padding:6px; color:#ffd66a;")
        self.warn.setVisible(False)
        root.addWidget(self.warn)

        # 字段区放进滚动区：Unit.ndfbin 的某些类有几十个字段。
        # 滚动区里装两段：上面是实体聚合（血量/装甲/速度），下面是本对象字段。
        # 两段共用一个滚动条——拆成两个滚动区会让玩家在两个小窗口里各滚一半。
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        shell = QWidget()
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(2, 2, 2, 2)
        shell_lay.setSpacing(10)

        self.entity_box = QGroupBox("实体概览 —— 血量 / 装甲 / 速度")
        eb = QVBoxLayout(self.entity_box)
        eb.setSpacing(6)
        self.entity_note = QLabel("")
        self.entity_note.setWordWrap(True)
        self.entity_note.setStyleSheet("color:#8ab4d8;")
        self.entity_note.setTextFormat(Qt.RichText)
        eb.addWidget(self.entity_note)
        self.entity_holder = QWidget()
        self.entity_form = QFormLayout(self.entity_holder)
        self.entity_form.setLabelAlignment(Qt.AlignRight)
        eb.addWidget(self.entity_holder)
        self._show_all_entity = False
        self.entity_all = QCheckBox("显示全部模块字段")
        self.entity_all.setVisible(False)
        self.entity_all.toggled.connect(self._on_toggle_entity_all)
        eb.addWidget(self.entity_all)
        self.entity_box.setVisible(False)
        shell_lay.addWidget(self.entity_box)

        self.fields_title = QLabel("本对象的字段")
        self.fields_title.setStyleSheet("color:#8ab4d8; font-weight:bold;")
        self.fields_title.setVisible(False)
        shell_lay.addWidget(self.fields_title)

        holder = QWidget()
        self.form = QFormLayout(holder)
        self.form.setLabelAlignment(Qt.AlignRight)
        shell_lay.addWidget(holder)
        shell_lay.addStretch(1)

        self.scroll.setWidget(shell)
        root.addWidget(self.scroll, 1)

        self.empty = QLabel("这个类没有可编辑的数值字段。\n\n"
                            "编辑器只暴露 int / float / bool —— 数组、对象引用、"
                            "本地化哈希虽然技术上能改，但改了会让mod 损坏。")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color:#888;")
        root.addWidget(self.empty)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)
        self.btn_revert = QPushButton("撤销这一条的全部改动")
        self.btn_revert.clicked.connect(self._revert_current)
        self.btn_revert.setEnabled(False)
        self.btn_clone = QPushButton("克隆这个对象…")
        self.btn_clone.setEnabled(False)
        self.btn_clone.setToolTip(
            "复制这个对象、追加到文件末尾，然后问你要让哪些字段改指向它。\n"
            "新对象会拿到一个新的 GUID；名字与口径是哈希、改不了，所以会沿用原件的。")
        self.btn_clone.clicked.connect(self._on_clone)
        row = QHBoxLayout()
        row.addWidget(self.btn_revert)
        row.addWidget(self.btn_clone)
        row.addStretch(1)
        root.addLayout(row)

    def _on_clone(self):
        if self._entry is None:
            return
        self.cloneRequested.emit(self._entry)

    def build_for(self, info: ClassInfo | None):
        """按类的字段清单重建编辑器。"""
        self._info = info
        while self.form.count():
            item = self.form.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._editors.clear()
        self._labels.clear()
        self._add_buttons.clear()
        self._km_labels.clear()
        self._ref_buttons.clear()
        self._ref_open.clear()
        self._ref_labels.clear()
        self._list_buttons.clear()
        self._str_buttons.clear()
        self._str_list_buttons.clear()
        self._hash_buttons.clear()
        self._clear_entity_rows()
        self.entity_box.setVisible(False)
        self.fields_title.setVisible(False)

        if info is None or info.field_count == 0:
            # info is None = 还没选类；info 有但没字段 = 这个类确实没得改
            self.scroll.setVisible(False)
            self.empty.setVisible(info is not None)
            return

        self.scroll.setVisible(True)
        self.empty.setVisible(False)

        for name, kind, coverage in info.fields:
            if kind in ("objref", "pathref"):
                self._build_ref_row(name, kind, coverage)
                continue
            if kind in ("objlist", "pathlist"):
                self._build_list_row(name, coverage)
                continue
            if kind == "str":
                self._build_str_row(name, coverage)
                continue
            if kind == "strlist":
                self._build_str_list_row(name, coverage)
                continue
            if kind == "hash":
                self._build_hash_row(name, coverage)
                continue
            if kind == "bool":
                box = QCheckBox()
                box.toggled.connect(lambda _, n=name: self._on_change(n))
            elif kind == "int":
                box = QSpinBox()
                box.setRange(-1_000_000_000, 1_000_000_000)
                box.valueChanged.connect(lambda _, n=name: self._on_change(n))
            else:
                box = QDoubleSpinBox()
                box.setDecimals(DECIMALS)
                box.setRange(MIN_FLOAT, MAX_FLOAT)
                box.setSingleStep(0.5)
                box.setKeyboardTracking(False)
                box.valueChanged.connect(lambda _, n=name: self._on_change(n))

            label = QLabel(humanize(name))
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            # 低覆盖率字段加个提示：说明大多数同类对象没有它
            tip = f"{name}"
            if coverage < 0.999:
                tip += f"\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
            if coverage < 0.5:
                tip += "\n多数对象没有这个字段，改它只影响少数条目"
            label.setToolTip(tip)

            # 缺字段时的「+ 添加」按钮。放在输入框右侧，正常状态隐藏，
            # 只有当前对象缺这个字段时才出现——默认界面保持干净。
            add = QPushButton("+ 添加")
            add.setFixedWidth(68)
            add.setToolTip(
                f"这个对象原本没有 {humanize(name)}。\n"
                f"点一下补上，取同类对象的常见值作为初始值。\n"
                f"随时可以再点「移除」撤销。")
            add.clicked.connect(lambda _, n=name: self._on_add(n))
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(4)
            hl.addWidget(box, 1)
            # 以 GRU 结尾的字段旁挂一个实时换算标签。游戏里 6000 这个数字
            # 说不出量级，玩家一眼看不出是「6 公里」还是「6 米」。
            if is_gru_field(name):
                km = QLabel("")
                km.setStyleSheet("color:#7f9bb5;")
                km.setMinimumWidth(78)
                hl.addWidget(km, 0)
                self._km_labels[name] = km
            hl.addWidget(add, 0)
            add.setVisible(False)

            self.form.addRow(label, holder)
            self._editors[name] = box
            self._labels[name] = label
            self._add_buttons[name] = add

    # ---- 引用字段 ----

    def _build_ref_row(self, name: str, kind: str, coverage: float):
        """引用字段的行：**一长一短**两个按钮。

        长按钮写「现在指向谁」，点它 *进入* 那个对象去改它自己的字段；
        右边的「替换」才是改指向。两种意图以前挤在同一个按钮上，
        玩家点下去之前没法知道会换掉目标还是打开目标——而这两件事的
        后果完全不同（一个改对象图，一个只是看）。

        没有做成内嵌下拉框，因为候选集规模差异太大——装甲等级只有 62 个，
        单位路径有 2474 条，弹道规则有 222 个。统一成「按钮 + 选择框」
        可以让玩家只有一个心智模型，而搜索框在三种规模下都够用。
        """
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)

        btn = QPushButton("替换")
        btn.setFixedWidth(58)
        btn.setToolTip(f"{name} —— 点一下换一个目标。")
        btn.clicked.connect(lambda _, n=name: self._on_pick_ref(n))
        #: 长按钮：进入目标对象。文字和原来那个按钮逐字相同，
        #: 变的只是点击行为——玩家看到的界面几乎不变。
        open_btn = QPushButton("…")
        open_btn.clicked.connect(lambda _, n=name: self._on_open_ref(n))

        hl.addWidget(open_btn, 1)
        hl.addWidget(btn, 0)

        label = QLabel(humanize(name))
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = f"{name}\n这个字段不是数字，它指向另一个东西。"
        tip += ("\n指向本文件里的一个对象。" if kind == "objref"
                else "\n指向一个资源路径（$/GFX/…），可能在本文件外。")
        if coverage < 0.999:
            tip += f"\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
        label.setToolTip(tip)
        self.form.addRow(label, holder)
        self._ref_buttons[name] = btn
        self._ref_open[name] = open_btn
        self._ref_labels[name] = label

    def _build_list_row(self, name: str, coverage: float):
        """「一串引用」字段的行：一个按钮，点开逐项改。

        不在面板里铺开，因为 ``DeckPackList`` 实测有 268 项的
        （``tools/probe_listrefs.py``）——铺开就没法用了。
        """
        btn = QPushButton("…")
        btn.setToolTip(f"{name} —— 点开逐项换目标")
        btn.clicked.connect(lambda _, n=name: self._on_open_list(n))
        label = QLabel(humanize(name))
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = (f"{name}\n这是一个「一串引用」字段——里面每一项都指向一个对象。\n"
               f"点开可以逐项换目标，但不能增删项。")
        if coverage < 0.999:
            tip += f"\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
        label.setToolTip(tip)
        self.form.addRow(label, btn)
        self._list_buttons[name] = btn
        self._ref_labels[name] = label

    def _build_str_row(self, name: str, coverage: float):
        """字符串引用字段的行：一个按钮，显示当前字符串，点开换一个。

        **不做成输入框.** 值不是自由文本，而是 STRG 表里的一个下标；
        手打一个表里没有的名字，写进去游戏也认不出（还得多重建一次字符串表）。
        所以是「按钮 + 从表里已有的名字中选」。
        """
        btn = QPushButton("…")
        btn.setToolTip(f"{name} —— 点一下换一个字符串")
        btn.clicked.connect(lambda _, n=name: self._on_pick_str(n))
        label = QLabel(humanize(name))
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = (f"{name}\n这是字符串字段——值不是数字，而是一个名字。\n"
               f"只能换成文件里已经存在的名字，不能新建。")
        if coverage < 0.999:
            tip += f"\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
        label.setToolTip(tip)
        self.form.addRow(label, btn)
        self._str_buttons[name] = btn
        self._ref_labels[name] = label

    def _build_str_list_row(self, name: str, coverage: float):
        """字符串列表字段的行：一个按钮，点开逐项改、可增删。"""
        btn = QPushButton("…")
        btn.setToolTip(f"{name} —— 点开编辑这一串字符串")
        btn.clicked.connect(lambda _, n=name: self._on_open_str_list(n))
        label = QLabel(humanize(name))
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = (f"{name}\n这是一串字符串（游戏里的标签之类）。\n"
               f"点开可以逐项换、增、删，还能调顺序。")
        if coverage < 0.999:
            tip += f"\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
        label.setToolTip(tip)
        self.form.addRow(label, btn)
        self._str_list_buttons[name] = btn
        self._ref_labels[name] = label

    def _build_hash_row(self, name: str, coverage: float):
        """哈希字段的行：一个按钮，点开是「照抄一个值」。

        **不可逆，所以没有输入框.** 界面上显示十六进制——不是为了让人读懂，
        而是为了让「这个值和别的不一样」这件事可见。
        """
        btn = QPushButton("…")
        btn.setToolTip(f"{name} —— 点一下，从同类对象用过的值里照抄一个")
        btn.clicked.connect(lambda _, n=name: self._on_pick_hash(n))
        label = QLabel(humanize(name))
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = (f"{name}\n这是本地化哈希——值不可逆，对应的文本在游戏词典里。\n"
               f"不能直接输入名字，只能照抄同类对象用过的值。")
        if coverage < 0.999:
            tip += f"\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
        label.setToolTip(tip)
        self.form.addRow(label, btn)
        self._hash_buttons[name] = btn
        self._ref_labels[name] = label

    def show_entry(self, entry: ObjEntry | None, info: ClassInfo | None):
        self._loading = True
        self._entry = entry

        if entry is None:
            self.header.setText("未选择对象")
            self.sub.setVisible(False)
            self.summary.setText("")
            self.warn.setVisible(False)
            self.btn_revert.setEnabled(False)
            self.btn_clone.setEnabled(False)
            for box in self._editors.values():
                _mark_missing(box, True)
            for add in self._add_buttons.values():
                add.setVisible(False)
            for btn in self._ref_buttons.values():
                btn.setText("替换")
                btn.setEnabled(False)
            for btn in self._ref_open.values():
                btn.setText("…")
                btn.setEnabled(False)
            for btn in self._list_buttons.values():
                btn.setText("…")
                btn.setEnabled(False)
            for btn in self._str_buttons.values():
                btn.setText("…")
                btn.setEnabled(False)
            for btn in self._str_list_buttons.values():
                btn.setText("…")
                btn.setEnabled(False)
            for btn in self._hash_buttons.values():
                btn.setText("…")
                btn.setEnabled(False)
            self._clear_entity_rows()
            self.entity_box.setVisible(False)
            self._loading = False
            return

        title = entry.title or f"#{entry.object_id}"
        self.header.setText(title)
        self.sub.setText(f"class {entry.class_name}   ·   "
                         f"object_id {entry.object_id}")
        self.sub.setVisible(True)

        if entry.ref_count > 1:
            self.warn.setText(
                f"⚠ 这个对象被 {entry.ref_count} 种装备共用，改动会同时影响它们。\n"
                f"   {entry.subtitle}")
            self.warn.setVisible(True)
        elif entry.ref_count == 0 and entry.subtitle:
            self.warn.setText(f"⚠ {entry.subtitle}")
            self.warn.setVisible(entry.subtitle.startswith("没有"))
        else:
            self.warn.setVisible(False)

        self.btn_revert.setEnabled(entry.dirty)
        # 克隆对**任何**对象都成立（不限于弹药）：复制它、追加到文件末尾，
        # 然后问哪些引用字段改指向新对象。跨文件不能克隆——目标对象不在
        # 本文件里，没有东西可复制。
        self.btn_clone.setEnabled(self._document is not None)

        have = len(entry.fields)
        total = len(self._editors)
        added = len(entry.added_fields())
        parts = []
        if have < total:
            parts.append(f"该对象原本有 {have} / {total} 个可改字段")
        else:
            parts.append(f"该对象有 {have} 个可改字段")
        if added:
            parts.append(f"其中 {added} 个是你补加的")
        rest = total - have - added
        if rest > 0:
            parts.append(f"还有 {rest} 个在这一类里不是每条都有，已置灰"
                         f"（点右侧「+ 添加」可以补上）")
        if entry.refs:
            parts.append(f"{len(entry.refs)} 个字段指向别的对象，"
                         f"点按钮换目标")
        if entry.ref_lists:
            n_items = sum(l.length for l in entry.ref_lists.values())
            parts.append(f"{len(entry.ref_lists)} 个字段是一串引用"
                         f"（共 {n_items} 项），点开逐项改")
        n_text = len(entry.strs) + len(entry.str_lists) + len(entry.hashes)
        if n_text:
            parts.append(f"{n_text} 个字段是名字 / 标签 / 哈希，点按钮改")
        self.summary.setText("，".join(parts) + "。")

        # 引用字段：长按钮写「现在指向谁」并进入它，「替换」改指向
        if self._document is not None:
            index = self._document.ref
            for name, btn in self._ref_buttons.items():
                rf = entry.refs.get(name)
                target = self._ref_open.get(name)
                if rf is None:
                    btn.setEnabled(False)
                    if target is not None:
                        target.setText("（这个对象没有它）")
                        target.setEnabled(False)
                        _paint(target, False)
                    _paint(btn, False)
                    continue
                btn.setEnabled(True)
                extra = f"  候选 {rf.pool_size} 个"
                if rf.kind == "objref":
                    extra = f"\n目标类 {rf.target_class}，{extra.strip()}"
                btn.setToolTip(f"{name}\n" + refs.describe_full(index, rf) + extra
                               + "\n\n点一下换一个别的目标。")
                if target is None:
                    continue
                describe_target(self._document, rf, target, index)
                _paint(target, rf.dirty)

            for name, btn in self._list_buttons.items():
                lf = entry.ref_lists.get(name)
                if lf is None:
                    btn.setText("（这个对象没有它）")
                    btn.setEnabled(False)
                    _paint(btn, False)
                    continue
                btn.setEnabled(True)
                n_dirty = len(lf.dirty_indices())
                text = f"{lf.length} 项 · {lf.summary(index, 2)}"
                if n_dirty:
                    text += f"　（{n_dirty} 项已改）"
                btn.setText(text)
                tcs = lf.target_classes()
                btn.setToolTip(
                    f"{name}：{lf.length} 项，"
                    + (f"每项是 {tcs[0]}" if len(tcs) == 1
                       else f"混着 {len(tcs)} 种目标")
                    + "\n\n点开逐项换目标。")
                _paint(btn, bool(n_dirty))

            # 字符串字段：按钮上写当前的名字。末尾那个 › 表示「点开换成别的」。
            for name, btn in self._str_buttons.items():
                sf = entry.strs.get(name)
                if sf is None:
                    btn.setText("（这个对象没有它）")
                    btn.setEnabled(False)
                    _paint(btn, False)
                    continue
                btn.setEnabled(True)
                btn.setText(f"{sf.value}  ›" if sf.value else "（空）  ›")
                btn.setToolTip(
                    f"{name}\n现在是「{sf.value}」"
                    f"\n\n点一下，换成文件里已有的别的字符串。")
                _paint(btn, sf.dirty)

            # 字符串列表：按钮上摊开前几项——标签的实际值就是这里的最重要信息
            for name, btn in self._str_list_buttons.items():
                lf = entry.str_lists.get(name)
                if lf is None:
                    btn.setText("（这个对象没有它）")
                    btn.setEnabled(False)
                    _paint(btn, False)
                    continue
                btn.setEnabled(True)
                texts = lf.texts()
                shown = "、".join(texts[:4])
                if len(texts) > 4:
                    shown += f" …（共 {len(texts)} 项）"
                btn.setText(shown or "（空）  ›")
                btn.setToolTip(f"{name}：{len(texts)} 项\n{shown}"
                               f"\n\n点开可以逐项换、增、删。")
                _paint(btn, lf.dirty)

            # 哈希字段：显示十六进制。看不懂没关系——它的作用是让
            # 「这个值和别的不一样」这件事可见。
            for name, btn in self._hash_buttons.items():
                hf = entry.hashes.get(name)
                if hf is None:
                    btn.setText("（这个对象没有它）")
                    btn.setEnabled(False)
                    _paint(btn, False)
                    continue
                btn.setEnabled(True)
                btn.setText(f"{hf.hex[:16]}  ›")
                choices = self._document.hash_choices(entry.class_name, name)
                btn.setToolTip(
                    f"{name}\n现在是 {hf.hex}"
                    f"\n同类对象一共用过 {len(choices)} 个不同的值"
                    f"\n\n点一下，从里面挑一个照抄。")
                _paint(btn, hf.dirty)

        # 只给这个对象实际拥有的字段建编辑器，其余禁用。
        # 早期版本让 specialValueText 显示「无此字段」，但它会盖住左侧标签
        # 文字并且在滚动区里造成视觉噪音——改成禁用 + tooltip + 顶部汇总。
        for name, box in self._editors.items():
            fld = entry.fields.get(name)
            fld = entry.fields.get(name)
            add = self._add_buttons.get(name)
            if fld is None:
                _mark_missing(box, True)
                km = self._km_labels.get(name)
                if km is not None:
                    km.setText("")
                if add is not None:
                    add.setVisible(True)
                    add.setText("+ 添加")
                    add.setToolTip(
                        f"这个对象原本没有 {humanize(name)}。\n"
                        f"点一下补上，取同类对象的常见值作为初始值。\n"
                        f"随时可以再点「移除」撤销。")
                continue
            _mark_missing(box, False)
            if add is not None:
                if fld.added:
                    add.setVisible(True)
                    add.setText("− 移除")
                    add.setToolTip(
                        f"{humanize(name)} 是你补加的字段。\n"
                        f"点一下把它整个去掉，恢复成「原本就没有」的样子。")
                else:
                    add.setVisible(False)
            if isinstance(box, QCheckBox):
                box.setChecked(bool(fld.value))
                _paint(box, False)
            elif isinstance(box, QSpinBox):
                box.setValue(int(fld.value))
                _paint(box, fld.dirty)
            else:
                box.setValue(float(fld.value))
                _paint(box, fld.dirty)
            km = self._km_labels.get(name)
            if km is not None:
                km.setText(gru_text(fld.value))
        self._fill_entity(entry)
        self._loading = False

    # ---- 实体聚合区 ----

    def _clear_entity_rows(self):
        while self.entity_form.count():
            item = self.entity_form.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._entity_editors.clear()
        self._entity_refs.clear()
        self._entity_rows.clear()

    def _fill_entity(self, entry: ObjEntry | None):
        """把「实体 = 一堆模块」摊开成血量/装甲/速度面板。

        ``TEntityDescriptor`` 自己只有 4 个属性（一个 guid、一个模块列表、
        一个调试名），**没有任何数值**。所以实体视图不是给这个类做字段表，
        而是顺着 ``ModulesDescriptors`` 把模块的字段捞上来放在一起。
        实测 ``Descriptor_Unit_2K11_KRUG_DDR`` 有 48 个模块。
        """
        nd = self._document
        if (nd is None or entry is None
                or not entity.looks_like_entity(nd, entry.class_name)):
            self.entity_box.setVisible(False)
            self.fields_title.setVisible(False)
            return
        if self._shared_lookup is None:
            self._shared_lookup = entity._build_shared_lookup(nd)
        view = entity.build_entity_view(nd, entry,
                                        shared_lookup=self._shared_lookup)
        if not view.ok:
            self.entity_box.setVisible(False)
            self.fields_title.setVisible(False)
            return

        self.entity_box.setVisible(True)
        self.fields_title.setText(
            "这个类自己的字段 —— 实体类身上只有 guid 和模块清单，"
            "通常不用改这里")
        self.fields_title.setVisible(True)
        # 说明和警告合成一个富文本标签。分成两个 wordWrap 的 QLabel 时，
        # 布局给它们算的高度会打架，警告框直接盖住说明的第二行。
        shared = view.shared_modules()
        warn = ""
        if shared:
            worst = max(shared, key=lambda m: m.shared_by)
            warn = (f"<br><span style='color:#ffd66a;'>⚠ 有 {len(shared)} 个模块"
                    f"是这个实体和别的实体共用的，改它们会同时改到别人。"
                    f"最广的 {worst.module_class} 被 {worst.shared_by} 个实体"
                    f"共用，每个模块标题上都标了数量。</span>")
        self.entity_note.setText(
            f"{view.title} —— {view.summary()}"
            f"<br>血量 / 装甲 / 速度都在上面这些模块里。{warn}")

        self._clear_entity_rows()
        by_mod = view.groups_by_module()
        for gname, mods in by_mod.items():
            if gname == entity.OTHER_GROUP and not self._show_all_entity:
                continue
            total = sum(len(rs) for _, rs in mods)
            hdr = QLabel(f"▸ {gname}（{total}）")
            hdr.setStyleSheet("color:#7f9bb5; font-weight:bold;")
            self.entity_form.addRow(hdr)
            for module, rows in mods:
                self.entity_form.addRow(self._module_header(module, len(rows)))
                for r in rows:
                    self._build_entity_row(r, nd)

        hidden = len(view.groups().get(entity.OTHER_GROUP, ()))
        self.entity_all.setText(
            ("显示其他模块字段" if not self._show_all_entity
             else "收起其他模块字段")
            + f"（其他还有 {hidden} 个）")
        self.entity_all.setVisible(hidden > 0)

    def _module_header(self, module, n_rows: int) -> QWidget:
        """模块小标题：类名 + 标签 + 共享警告。"""
        if module is None:
            text = f"· 未知模块（{n_rows} 个字段）"
            w = QLabel(text)
            w.setStyleSheet("color:#8a8a8a;")
            return w
        bits = [module.module_class]
        if module.title and not module.title.startswith("#"):
            t = module.title
            if len(t) > 24:
                # 有些模块的标签是完整资源路径名（
                # DamageLevelsPackDescriptor_Default_pack_palliers_degats_physiques），
                # 不截断会把共享警告挤出屏幕。
                t = t[:23] + "…"
            bits.append(f"「{t}」")
        bits.append(f"#{module.module_id}")
        w = QLabel(f"· {'  '.join(bits)}")
        tip = f"object #{module.module_id}  {module.module_class}"
        if module.shared_by > 1:
            w.setStyleSheet("color:#c9a227;")
            w.setText(f"· {'  '.join(bits)}   ⚠ {module.shared_by} 个实体共用")
            tip += (f"\n⚠ 这个模块被 {module.shared_by} 个实体共用，"
                    f"改它会影响全部")
        else:
            w.setStyleSheet("color:#8a8a8a;")
            tip += "\n只有这个实体在用"
        w.setToolTip(tip)
        return w

    def _build_entity_row(self, row, nd):
        """聚合区里的一行。控件直接绑在**共享的那个模块条目**上。"""
        label = QLabel(humanize(row.name))
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = (f"{row.name}\n来自模块 {row.module_class}"
               f"   #{row.module_id}  {row.module_title}")
        if row.shared:
            tip += f"\n⚠ {row.warning}"
        label.setToolTip(tip)

        if row.kind == "ref":
            btn = QPushButton("…")
            rf = row.entry.refs.get(row.name)
            if rf is not None:
                btn.setText(refs.describe(nd.ref, rf))
                btn.setToolTip(refs.describe_full(nd.ref, rf)
                               + "\n\n点一下选一个别的目标。")
            btn.clicked.connect(
                lambda _, ro=row: self._on_pick_ref(ro.name, ro.entry))
            self.entity_form.addRow(label, btn)
            self._entity_refs[row.name] = btn
            return

        fld = row.entry.fields.get(row.name)
        if fld is None:
            return
        if fld.kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(fld.value))
            box.toggled.connect(
                lambda _, ro=row: self._on_entity_change(ro))
        elif fld.kind == "int":
            box = QSpinBox()
            box.setRange(-1_000_000_000, 1_000_000_000)
            box.setValue(int(fld.value))
            box.valueChanged.connect(
                lambda _, ro=row: self._on_entity_change(ro))
        else:
            box = QDoubleSpinBox()
            box.setDecimals(DECIMALS)
            box.setRange(MIN_FLOAT, MAX_FLOAT)
            box.setSingleStep(0.5)
            box.setKeyboardTracking(False)
            box.setValue(float(fld.value))
            box.valueChanged.connect(
                lambda _, ro=row: self._on_entity_change(ro))
        _paint(box, fld.dirty)
        self.entity_form.addRow(label, box)
        self._entity_editors[row.name] = box

    def _on_entity_change(self, row):
        """聚合行改数值——直接写模块对象上的原位 dict。"""
        if self._loading or self._document is None:
            return
        fld = row.entry.fields.get(row.name)
        box = self._entity_editors.get(row.name)
        if fld is None or box is None:
            return
        if isinstance(box, QCheckBox):
            new = bool(box.isChecked())
        elif isinstance(box, QSpinBox):
            new = int(box.value())
        else:
            new = float(box.value())
        if new == fld.leaf.get("value"):
            return
        fld.leaf["value"] = new
        _paint(box, fld.dirty)
        self.btn_revert.setEnabled(True)
        self.dirtyChanged.emit()

    def _on_toggle_entity_all(self, checked: bool):
        self._show_all_entity = checked
        if self._entry is not None:
            self._fill_entity(self._entry)

    # ---- 选引用目标 ----

    def _on_pick_ref(self, name: str, owner: ObjEntry | None = None):
        """给一个引用字段换目标。``owner=None`` 表示改当前对象自己的字段。"""
        if self._loading or self._document is None:
            return
        owner = owner or self._entry
        if owner is None:
            return
        rf = owner.refs.get(name)
        if rf is None:
            return
        dlg = RefPickerDialog(self, self._document.ref, rf,
                              owner_class=owner.class_name)
        if dlg.exec() != QDialog.Accepted:
            return
        kind, key = dlg.chosen()
        try:
            self._document.ref.repoint(rf.leaf, kind, key)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, "换不了", str(exc))
            return
        self.show_entry(self._entry, self._info)
        self.dirtyChanged.emit()

    def _on_open_ref(self, name: str):
        """引用字段的长按钮：进入所指涉的那个对象。

        面板**自己不开窗口**——只发一个信号。这样同一个动作在主界面里
        可以新开一个对象编辑窗口，在那个窗口里则是压一节面包屑，
        而面板不需要知道自己在哪一层。
        """
        if self._loading or self._document is None or self._entry is None:
            return
        rf = self._entry.refs.get(name)
        if rf is None or rf.is_null or rf.kind != "objref":
            return
        if self._document.entry_of(rf.key) is None:
            return
        self.openObjectRequested.emit(rf.key)

    def _on_open_list(self, name: str):
        """请求打开「一串引用」编辑器。

        **面板自己不开窗口.** 那个对话框是非模态的（它里面的「查看」还要
        能再打开对象编辑窗口），生命周期、撤销后的刷新都归主窗口统一管，
        和专用编辑器、对象钻取窗口一个待遇。
        """
        if self._loading or self._document is None or self._entry is None:
            return
        if self._entry.ref_lists.get(name) is None:
            return
        self.openRefListRequested.emit(self._entry, name)

    # ---- 文本 / 哈希字段 ----

    def _on_pick_str(self, name: str):
        """换一个字符串（``ImpactHappening`` 这类）。"""
        if self._loading or self._document is None or self._entry is None:
            return
        sf = self._entry.strs.get(name)
        if sf is None:
            return
        dlg = StrPickerDialog(self, name, sf.index,
                              self._document.strings_pool)
        if dlg.exec() != QDialog.Accepted:
            return
        got = dlg.chosen()
        if not got or got[0] == sf.index:
            return
        sf.set_index(*got)
        self.show_entry(self._entry, self._info)
        self.dirtyChanged.emit()

    def _on_open_str_list(self, name: str):
        """打开字符串列表编辑器（``TraitsToken``）。

        列表对话框**直接改数据**（不走 ``accept()`` 回传），所以关掉之后
        要自己比较一次再决定要不要上报——玩家可能打开看一眼就关了。
        """
        if self._loading or self._document is None or self._entry is None:
            return
        lf = self._entry.str_lists.get(name)
        if lf is None:
            return
        before = lf.pairs()
        dlg = StrListDialog(self, lf, self._document.strings_pool,
                            owner=self._entry.title or "")
        dlg.exec()
        if lf.pairs() != before:
            self.show_entry(self._entry, self._info)
            self.dirtyChanged.emit()

    def _on_pick_hash(self, name: str):
        """从同类对象用过的值里照抄一个哈希。"""
        if self._loading or self._document is None or self._entry is None:
            return
        hf = self._entry.hashes.get(name)
        if hf is None:
            return
        choices = self._document.hash_choices(self._entry.class_name, name)
        if not choices:
            return
        dlg = HashPickerDialog(self, name, hf.hex, choices,
                               owner=self._entry.title or "")
        if dlg.exec() != QDialog.Accepted:
            return
        got = dlg.chosen()
        if not got or got == hf.hex:
            return
        hf.set_hex(got)
        self.show_entry(self._entry, self._info)
        self.dirtyChanged.emit()

    def _on_change(self, name: str):
        if self._loading or self._entry is None:
            return
        fld = self._entry.fields.get(name)
        if fld is None:
            return
        box = self._editors[name]
        if isinstance(box, QCheckBox):
            new = bool(box.isChecked())
        elif isinstance(box, QSpinBox):
            new = int(box.value())
        else:
            new = float(box.value())
        if new == fld.leaf["value"]:
            return
        fld.leaf["value"] = new
        _paint(box, fld.dirty)
        km = self._km_labels.get(name)
        if km is not None:
            km.setText(gru_text(new))
        self.btn_revert.setEnabled(self._entry.dirty)
        self.dirtyChanged.emit()

    def _on_add(self, name: str):
        """给当前对象补上 / 摘掉一个它原本没有的字段。

        同一个按钮兼做两件事：原本没有 -> 添加；是自己补加的 -> 移除。
        玩家不需要理解「属性」这个概念，只看到「这个格子空着，点一下填上」。
        """
        if self._loading or self._entry is None or self._document is None:
            return
        fld = self._entry.fields.get(name)
        if fld is not None and fld.added:
            self._document.remove_field(self._entry, name)
            self.show_entry(self._entry, self._info)
            self.dirtyChanged.emit()
            return
        if fld is not None:
            return
        try:
            self._document.add_field(self._entry, name)
        except KeyError as exc:
            QMessageBox.warning(self, "添加不了", str(exc))
            return
        self.show_entry(self._entry, self._info)
        self.dirtyChanged.emit()

    def _revert_current(self):
        if self._entry is None:
            return
        # 这一步必须能被退回来。否则「刚撤销过一次改动、紧接着点撤销这一条」
        # 两下的净效果为零，会被合并规则吃掉，那次点击就永远回不去了——
        # 而它恰恰是最该守住的操作。
        if self._document is not None:
            self._document.history.break_merge()
        self._entry.reset()
        self.show_entry(self._entry, self._info)
        # 必须发信号。少了这一句，数据退回去了但界面还停在「已改动 N 个对象」
        # ——保存按钮亮着、按下去却因为 dirty_count 为 0 而静默什么都不做，
        # 玩家会以为保存成功了。
        self.dirtyChanged.emit()

    def reload_current(self):
        """按当前数据重画一次面板。

        撤销 / 重做改的是文档本身，控件不会自己知道；不重画的话，
        格子里还是撤销前那个数字，玩家会以为撤销没生效然后再改一次。
        """
        self.show_entry(self._entry, self._info)

    def set_document(self, nd):
        """注入 NdfDocument——添加 / 移除字段需要它来真正改对象图。"""
        self._document = nd

    def reset_caches(self):
        """换文件时清掉跨文件的缓存。

        共享查找表是按文件算的，留着会让新文件里同一个 object_id 的模块
        显示成旧文件的共享数——两个文件的对象编号毫无关系。
        """
        self._shared_lookup = None
        self._show_all_entity = False
        self.entity_all.setChecked(False)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 720)
        self.nd: NdfDocument | None = None
        #: 当前文档的撤销历史。与文档一对一——换文件、保存后重新加载，
        #: 拿到的都是新文档，历史自然也是新的一份。
        self.history = None
        self._current_class: str | None = None
        #: 专用编辑器窗口。非模态——玩家可以一边看通用视图一边批量改，
        #: 而且改动直接落在同一份 decoded 上，主界面的 dirty 会同步。
        self._tool_windows: dict[str, QDialog] = {}

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.addLayout(self._build_topbar())
        layout.addWidget(self._build_body(), 1)
        layout.addLayout(self._build_bottombar())
        self._build_shortcuts()

    def _build_shortcuts(self):
        """Ctrl+Z / Ctrl+Y（外加 Ctrl+Shift+Z）。

        用 :class:`QShortcut` 而不是给按钮 ``setShortcut``：按钮一禁用，
        挂在它身上的快捷键也一起失效，玩家按 Ctrl+Z 会毫无反应——
        而「按了没反应」正是撤销最不该有的手感。

        搜索框有焦点时不抢：那里的 Ctrl+Z 是文本框自己的撤销，
        玩家在改搜索词时不该把文件里的改动退掉。
        """
        for seq, slot in ((QKeySequence.Undo, self.on_undo),
                          (QKeySequence.Redo, self.on_redo),
                          (QKeySequence("Ctrl+Shift+Z"), self.on_redo)):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(slot)

    # ---- 顶栏 ----

    def _build_topbar(self):
        row = QHBoxLayout()
        # 「浏览 Mod」排在前面：mod 作者的实际入口是「我的 mod 里那个文件」，
        # 而不是一个不知道在哪的 .ndfbin 路径。
        btn_mod = QPushButton("浏览 Mod…")
        btn_mod.setToolTip(
            "先选一个 Mod，再从它里面挑 .ndfbin。\n"
            "列表来自本机 ModGen 工作区（Saved Games）和 Steam 创意工坊；\n"
            "都不在的话，对话框里可以手动指目录。")
        btn_mod.clicked.connect(self.on_browse_mod)
        btn_open = QPushButton("打开单个文件…")
        btn_open.setToolTip("直接给一个 .ndfbin 路径——不经过 Mod。")
        btn_open.clicked.connect(self.on_open)

        # 专用编辑器入口。通用浏览器解决「能改」，这里解决
        # 「低代码玩家不用先认识类名就能改」。
        self.tool_box = QComboBox()
        self.tool_box.setToolTip("面向特定数据的专用编辑页")
        self.tool_box.addItem("编辑弹药", "ammo")
        self.tool_box.addItem("编辑牌组属性", "ag")
        self.tool_box.currentIndexChanged.connect(self._on_tool_changed)
        self.btn_tool = QPushButton("编辑弹药")
        self.btn_tool.setToolTip(
            "按字段值筛选弹药并批量修改。\n"
            "例如：把所有「对地射程 = 1925」的 Shell 统一改成 2100。")
        self.btn_tool.clicked.connect(self.on_open_tool)

        self.path_label = QLabel("尚未打开文件")
        self.path_label.setStyleSheet("color:#bbb;")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.search = QLineEdit()
        self.search.setPlaceholderText("在当前类里搜索标题、装备名或 object_id")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.on_search)
        row.addWidget(btn_mod)
        row.addWidget(btn_open)
        row.addWidget(QLabel("专用编辑器"))
        row.addWidget(self.tool_box)
        row.addWidget(self.btn_tool)
        row.addWidget(self.path_label, 1)
        row.addWidget(QLabel("搜索"))
        row.addWidget(self.search, 1)
        return row

    def _on_tool_changed(self):
        text = self.tool_box.currentText()
        self.btn_tool.setText(text)
        if self.tool_box.currentData() == "ag":
            self.btn_tool.setToolTip(
                "将军模式营的战略属性：部队类型、移动方式、行动点、"
                "炮兵支援范围。\n需要打开 Pawn.ndfbin。")
        else:
            self.btn_tool.setToolTip(
                "按字段值筛选弹药并批量修改。\n"
                "例如：把所有「对地射程 = 1925」的 Shell 统一改成 2100。")

    # ---- 主体 ----

    def _build_body(self):
        split = QSplitter(Qt.Horizontal)
        self.cls = ClassList()
        self.cls.currentRowChanged.connect(self.on_class)
        split.addWidget(self.cls)

        mid = QSplitter(Qt.Horizontal)
        self.objs = ObjectList()
        self.objs.currentRowChanged.connect(self.on_object)
        mid.addWidget(self.objs)
        self.panel = FieldPanel()
        self.panel.dirtyChanged.connect(self._on_entry_dirty)
        self.panel.openObjectRequested.connect(self.on_open_object)
        self.panel.cloneRequested.connect(self.on_clone_object)
        self.panel.openRefListRequested.connect(self.on_open_ref_list)
        mid.addWidget(self.panel)
        mid.setSizes([380, 620])

        split.addWidget(mid)
        split.setSizes([300, 1000])
        return split

    # ---- 底栏 ----

    def _build_bottombar(self):
        row = QHBoxLayout()
        self.status = QLabel("")
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.setEnabled(False)
        self.btn_undo.setToolTip("没有可撤销的改动")
        self.btn_undo.clicked.connect(self.on_undo)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.setEnabled(False)
        self.btn_redo.setToolTip("没有可重做的改动")
        self.btn_redo.clicked.connect(self.on_redo)
        self.btn_save = QPushButton("保存到文件")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_reload = QPushButton("放弃全部改动")
        self.btn_reload.setEnabled(False)
        self.btn_reload.clicked.connect(self.on_reload)
        row.addWidget(self.status, 1)
        row.addWidget(self.btn_undo)
        row.addWidget(self.btn_redo)
        row.addWidget(self.btn_reload)
        row.addWidget(self.btn_save)
        return row

    # ---- 动作 ----

    def on_open(self):
        start = str(self.nd.path.parent) if self.nd else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 .ndfbin", start,
            "Warno 数据文件 (*.ndfbin);;所有文件 (*)")
        if path:
            self.load_path(Path(path))

    def on_browse_mod(self):
        """先选 Mod，再选它里面的 ndfbin——mod 作者的正常入口。

        定位逻辑全在 :mod:`ndfbin_decks.ndfedit.modpicker` 里，
        而它又完全复用 :mod:`ndfbin_decks.io.modlocator`——本机工作区、
        创意工坊、手动目录三种来源与去重都已经在那边解决过。
        """
        dlg = modpicker.ModBrowserDialog(self)
        dlg.select_first()
        if dlg.exec() != QDialog.Accepted or dlg.chosen is None:
            return
        if not self.load_path(dlg.chosen):
            return
        if dlg.chosen_mod is not None:
            self.path_label.setText(
                f"{dlg.chosen_mod.label}  ·  {dlg.relative_of(dlg.chosen)}")
            self.path_label.setToolTip(str(dlg.chosen))

    # ---- 专用编辑器 ----

    def on_open_tool(self):
        """打开顶栏下拉框里选的那个专用编辑页。"""
        if self.nd is None:
            QMessageBox.information(
                self, "先打开文件",
                "先用「浏览 Mod…」或「打开单个文件…」打开一个 .ndfbin，"
                "再用专用编辑器。")
            return
        kind = self.tool_box.currentData()
        if kind == "ammo":
            self.open_ammo_editor()
        elif kind == "ag":
            self.open_ag_editor()

    def open_ammo_editor(self) -> "AmmoEditorDialog | None":
        """打开弹药专用页。非模态，改动与主界面共用同一份数据。"""
        if AMMO_CLASS not in self.nd.class_names:
            QMessageBox.information(
                self, "这个文件里没有弹药",
                f"{self.nd.path.name} 里没有 {AMMO_CLASS}。\n\n"
                "弹药定义在 Weapon.ndfbin 里——换一个文件再试。")
            return None
        dlg = self._tool_windows.get("ammo")
        if dlg is None:
            dlg = AmmoEditorDialog(self.nd, self)
            dlg.dirtyApplied.connect(self._on_entry_dirty)
            # 关掉专用页时也刷一次：玩家可能在里面撤销或改了值，
            # 主界面底部的「已改动 N 个对象」和保存按钮得跟上。
            dlg.finished.connect(lambda _code: self._on_entry_dirty())
            self._tool_windows["ammo"] = dlg
        else:
            dlg.reload()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def open_ag_editor(self) -> "AgPage | None":
        """打开将军模式牌组属性页。非模态，与主界面共用同一份数据。

        要求打开的是 ``Pawn.ndfbin``：战略属性全在那一个文件里，
        ``Deck.ndfbin`` 只装编制。开错文件时给一句人话，而不是弹一个空表。
        """
        if ag_view.PAWN_CLASS not in self.nd.class_names:
            QMessageBox.information(
                self, "这个文件里没有将军模式营",
                f"{self.nd.path.name} 里没有 {ag_view.PAWN_CLASS}。\n\n"
                "将军模式的战略属性定义在 Pawn.ndfbin 里——换一个文件再试。")
            return None
        dlg = self._tool_windows.get("ag")
        if dlg is None:
            dlg = AgPage(self.nd, self)
            # 改完立刻通知主界面：底部的「已改动 N 个对象」和保存按钮要跟上。
            dlg.dirtyApplied.connect(self._on_entry_dirty)
            # 关掉时也刷一次：玩家可能在别处又改了。
            dlg.finished.connect(lambda _code: self._on_entry_dirty())
            self._tool_windows["ag"] = dlg
        else:
            dlg.reload()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _close_tool_windows(self):
        """换文件时关掉专用页——它们持有旧文件的 decoded。"""
        for dlg in self._tool_windows.values():
            try:
                dlg.close()
            except RuntimeError:                    # 已被 Qt 回收
                pass
        self._tool_windows.clear()

    def load_path(self, path: Path) -> bool:
        """打开一个 ndfbin。返回是否成功——调用方要据此决定标签怎么写。"""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            nd = NdfDocument.load(path)
            # 弹药视图按「这个文件里有没有弹药类」来启用，而不是按文件名。
            # 早先按 ``path.stem == "Weapon"`` 判断，代价是：mod 把文件改名
            # （Weapon_shot、WeaponMerged…）就悄悄退化成通用视图——
            # 一份数据换个文件名行为就不同，这是最难查的一类bug。
            if AMMO_CLASS in nd.class_names:
                AmmoTitles(nd).apply()
        except Exception as exc:                       # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self, "打不开",
                f"无法解析这个文件：\n\n{path}\n\n{exc}\n\n"
                "它可能不是 EUG0/CNDF 格式的 WARNO 数据文件。")
            traceback.print_exc()
            return False
        QApplication.restoreOverrideCursor()

        self.nd = nd
        self.history = nd.history
        self.panel.set_document(nd)
        self.panel.reset_caches()
        self._close_tool_windows()
        self._current_class = None
        self.path_label.setText(path.name)
        self.path_label.setToolTip(str(path))
        self.path_label.setStyleSheet("color:#bbb;")
        self.cls.load(nd.classes)
        self.objs.load([])
        self.panel.build_for(None)
        self.panel.show_entry(None, None)
        self.search.clear()
        self._default_status()
        self._update_dirty()

        # 默认选第一个有可编辑字段的类——直接落到玩家能立刻用上的地方
        first = next((i for i, c in enumerate(nd.classes)
                      if c.field_count > 0), 0)
        if nd.classes:
            self.cls.setCurrentRow(first)
        return True

    def _default_status(self):
        if self.nd is None:
            self.status.setText("")
            return
        editable = [c for c in self.nd.classes if c.field_count > 0]
        n_ref = sum(1 for c in self.nd.classes for n, k, _ in c.fields
                    if k in ("objref", "pathref", "objlist", "pathlist"))
        extra = f"，其中 {n_ref} 个字段指向别的对象" if n_ref else ""
        self.status.setText(
            f"{self.nd.path.name} · {len(self.nd.decoded['objects'])} 个对象 · "
            f"{len(self.nd.classes)} 个类（{len(editable)} 个含可改字段{extra}）")

    def on_class(self, row: int):
        info = self.cls.info_at(row)
        if info is None or self.nd is None:
            self.objs.load([])
            self.panel.build_for(None)
            self.panel.show_entry(None, None)
            return
        self._current_class = info.name
        self.nd.ensure_titles(info.name)
        self.objs.load(self.nd.search(info.name, self.search.text()))
        self.panel.build_for(info)
        self.panel.show_entry(None, info)
        self._update_status_count()

    def _update_status_count(self):
        if self.nd is None or not self._current_class:
            return
        total = len(self.nd.entries(self._current_class))
        shown = self.objs.count
        info = self.nd.class_info(self._current_class)
        fields = info.field_count if info else 0
        self.status.setText(
            f"{self.nd.path.name} · 当前类 {self._current_class} · "
            f"显示 {shown} / {total} 个对象 · 该类有 {fields} 个可改字段")

    def on_search(self, text: str):
        if self.nd is None or not self._current_class:
            return
        row = self.objs.currentRow()
        self.objs.load(self.nd.search(self._current_class, text))
        if self.objs.count:
            self.objs.setCurrentRow(min(max(row, 0), self.objs.count - 1))
        self._update_status_count()

    def on_object(self, row: int):
        entry = self.objs.entry_at(row)
        info = self.nd.class_info(self._current_class) if self.nd else None
        self.panel.show_entry(entry, info)

    def _on_entry_dirty(self):
        self.objs.refresh_current()
        # 改动信号就是天然的提交边界：面板上任何一次编辑、弹药页的每一次
        # 批量应用，都会走到这里，于是历史只需要在这一处记录。
        if self.history is not None:
            self.history.commit()
        self._update_dirty()

    def _update_dirty(self):
        # 计数走 History：它刚在 commit 里扫过一遍，顺带算出了这几个数。
        # 分开调五个方法等于把两万多个对象扫五遍。
        st = self.history.stats() if self.history is not None else None
        if st is None:
            n_obj = self.nd.dirty_count() if self.nd else 0
            total = n_ref = n_list = added = 0
            n_str = n_strlist = n_hash = 0
            appended = self.nd.appended_count() if self.nd else 0
        else:
            n_obj, total = st.objects, st.fields
            added, n_ref, n_list = st.added, st.refs, st.lists
            n_str, n_strlist, n_hash = st.strs, st.str_lists, st.hashes
            appended = st.new_objects
        # 克隆体的「存在」本身就是未保存改动——它没有脏字段，但对象表变了。
        # 不把它算进来的话，克隆完（还没改任何字段）保存按钮是灰的。
        n = n_obj + appended
        self.btn_save.setEnabled(n > 0)
        self.btn_reload.setEnabled(n > 0)
        self._update_history_buttons()
        if n:
            bits = [f"● 已改动 {n_obj} 个对象 / {total} 个字段"]
            if appended:
                bits.append(f"新增 {appended} 个对象")
            if n_ref:
                bits.append(f"{n_ref} 处改了引用目标")
            if n_list:
                bits.append(f"{n_list} 处改了列表里的引用")
            if added:
                bits.append(f"补加 {added} 个字段")
            if n_str:
                bits.append(f"{n_str} 处换了名字")
            if n_strlist:
                bits.append(f"{n_strlist} 处改了一串名字")
            if n_hash:
                bits.append(f"{n_hash} 处照抄了哈希值")
            self.status.setText("，".join(bits) + "，尚未保存")
        else:
            self._default_status()

    def _update_history_buttons(self):
        h = self.history
        for btn, can, kind, label in (
                (self.btn_undo, h is not None and h.can_undo, "撤销",
                 h.undo_label() if h else ""),
                (self.btn_redo, h is not None and h.can_redo, "重做",
                 h.redo_label() if h else "")):
            btn.setEnabled(can)
            key = "Ctrl+Z" if kind == "撤销" else "Ctrl+Y"
            btn.setToolTip(
                f"{kind}「{label}」（{key}）" if can
                else f"没有可{kind}的改动")

    # ---- 撤销 / 重做 ----

    def on_open_object(self, object_id: int):
        """进到引用所指涉的那个对象——新开一个钻取窗口。

        非模态，和专用编辑器一样放在 ``_tool_windows`` 里：换文件时会被
        一起关掉，撤销/重做后也会被一起刷新，不用另写一套生命周期。
        """
        if self.nd is None:
            return
        entry = self.nd.entry_of(object_id)
        if entry is None:
            return
        dlg = self._tool_windows.get("object")
        if dlg is None:
            dlg = ObjectEditorDialog(self.nd, self)
            dlg.dirtyChanged.connect(self._on_entry_dirty)
            dlg.locateRequested.connect(self.locate_object)
            dlg.cloneRequested.connect(self.on_clone_object)
            self._tool_windows["object"] = dlg
        dlg.push(entry)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def on_open_ref_list(self, entry, name: str):
        """打开「一串引用」编辑器——非模态工具窗口。

        **一个窗口反复复用**：玩家在对象列表里换对象、点开同一个字段名，
        不该又弹一个新窗口出来。放进 ``_tool_windows`` 之后，换文件时会被
        一起关掉、撤销/重做后会被一起刷新。
        """
        if self.nd is None or entry is None:
            return
        dlg = self._tool_windows.get("reflist")
        if dlg is None:
            dlg = RefListDialog(self.nd, self)
            dlg.dirtyChanged.connect(self._on_entry_dirty)
            # 「查看」直接复用对象钻取窗口——它会压面包屑、能回主界面定位、
            # 也会被撤销刷新，没有理由再写一遍。
            dlg.openObjectRequested.connect(self.on_open_object)
            self._tool_windows["reflist"] = dlg
        dlg.show_field(entry, name)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def on_clone_object(self, entry):
        """克隆一个对象，然后问「哪些字段改指向它」。

        流程：克隆（复制 + 新 id + 新 GUID + 追加到 OBJE 与 TOPO）→ 独立
        记一步历史 → 弹出多选窗口让玩家勾选谁改指向它。最后那步可以不勾，
        克隆体照样留在文件里，之后在各自的字段面板里用「替换」慢慢指。
        """
        if self.nd is None or entry is None:
            return
        if self.history is not None:
            # 克隆是结构性改动，必须独立成一步。不这样做的话它会和紧接着的
            # 「改指向」落进同一格，按一次 Ctrl+Z 会把克隆对象一起退掉。
            self.history.break_merge()
        try:
            clone = self.nd.clone_object(entry)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, "克隆不了", str(exc))
            return
        self.nd.ensure_titles(clone.class_name)
        if self.history is not None:
            self.history.commit()
        self.cls.refresh_counts()
        self.objs.refresh_current()

        dlg = RefBulkDialog(self.nd, clone, entry,
                            self.nd.last_clone_guid_fields, self)
        dlg.dirtyApplied.connect(self._on_entry_dirty)
        dlg.exec()
        # 克隆体现在有名字了，定位过去让玩家看见它。
        self.locate_object(clone.object_id)
        self._update_dirty()

    def locate_object(self, object_id: int):
        """把主界面的三栏切到某个对象——引用钻取和克隆之后都用它。"""
        if self.nd is None:
            return
        entry = self.nd.entry_of(object_id)
        if entry is None:
            return
        index = next((i for i, c in enumerate(self.nd.classes)
                      if c.name == entry.class_name), None)
        if index is None:
            return
        self.search.clear()
        # 显式调用而不是靠 setCurrentRow 发信号：当前类**已经**是这个类时
        # setCurrentRow 不触发 currentRowChanged，对象列表就还是旧的。
        self.on_class(index)
        # 按 id 扫行号，而不是用 nd.entries() 的下标去套。
        # 列表显示的是 ``nd.search()`` 的结果，那是**按标题排序**的，
        # 和 entries() 的 id 序不是一回事——套下标会选到毫不相干的对象。
        for row in range(self.objs.count):
            e = self.objs.entry_at(row)
            if e is not None and e.object_id == object_id:
                self.objs.setCurrentRow(row)
                break

    def on_undo(self):
        self._step_history(undo=True)

    def on_redo(self):
        self._step_history(undo=False)

    def _step_history(self, *, undo: bool):
        if self.history is None:
            return
        # 搜索框里的 Ctrl+Z 是文本框自己的撤销，别抢——但**只在它真的有东西
        # 可撤时才让**。空白搜索框也拦的话，玩家刚开窗就按 Ctrl+Z 会得到
        # 「毫无反应」，而那是撤销最不该有的手感。
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit) and (focus.text()
                                            or focus.hasSelectedText()):
            return
        try:
            step = self.history.undo() if undo else self.history.redo()
        except ValueError as exc:
            # 唯一会在这里抛的是「撤销克隆时还有引用指着它」——把话说明白，
            # 而不是让异常穿到 Qt 事件循环里变成一个静默的堆栈。
            QMessageBox.warning(self, "退不了", str(exc))
            return
        verb = "已撤销" if undo else "已重做"
        if step is None:
            self.status.setText(f"没有可{'撤销' if undo else '重做'}的改动了。")
            return
        self._refresh_after_history()
        self.status.setText(f"{verb}：{step.label}　—　{self.status.text()}")

    def _refresh_after_history(self):
        """把界面上所有还在显示旧数据的地方重画一遍。

        撤销改的是文档本身，控件不会自己知道——不重画的话，格子里的数字
        还是撤销前那个，玩家会以为撤销没生效，然后再改一次。
        """
        self.objs.refresh_current()
        self.panel.reload_current()
        for dlg in list(self._tool_windows.values()):
            refresh = getattr(dlg, "refresh", None)
            if not callable(refresh):
                continue
            try:
                refresh()
            except RuntimeError:                    # 已被 Qt 回收
                pass
        if self.history is not None:
            self.history.invalidate()
        self._update_dirty()

    def on_reload(self):
        if self.nd is None:
            return
        if QMessageBox.question(
                self, "放弃改动",
                f"确定放弃全部 {self.nd.dirty_count()} 个对象的改动？\n\n"
                "磁盘上的文件从未被写入过，所以「放弃」等于把它重新读一遍。\n"
                "这一步会连撤销记录一起清掉，之后退不回来了。") \
                != QMessageBox.Yes:
            return
        path = self.nd.path
        self.nd.reset_all()
        self.load_path(path)

    def on_save(self):
        if self.nd is None:
            return
        # 追加的对象没有脏字段，`dirty_count()` 看不见它们——只看那个数
        # 会让「克隆完什么都没改」这种情况存不下去。
        if not (self.nd.dirty_count() or self.nd.appended_count()):
            return
        # 用逐字段对照代替原来那句「已改动 N 个对象」——数量说服不了一个
        # 正要按保存的人，尤其批量改之后（条件错一个数就改错了另一批）。
        if ChangePreviewDialog(self.nd, self).exec() != QDialog.Accepted:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.nd.save()
        except writer.StaleDocumentError:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self, "文件已被改动",
                "这个文件在你编辑期间被其它程序改过。\n\n"
                "为避免覆盖掉别人的改动，本次保存已取消。\n"
                "请关闭其它程序后重新打开本文件。")
            return
        except Exception as exc:                       # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "保存失败", f"写入失败：\n\n{exc}")
            traceback.print_exc()
            return
        QApplication.restoreOverrideCursor()

        # 写盘后 doc.raw_data 仍是旧快照，必须重新加载才能继续编辑
        path = self.nd.path
        self.load_path(path)
        QMessageBox.information(
            self, "已保存",
            f"写入完成。\n\n"
            f"体积：{result.size_before:,} → {result.size_after:,} 字节"
            + (f"\n原始备份：{result.backup.name}" if result.backup else "")
            + "\n\n进入游戏即可看到效果。"
            + "\n\n（文件已重新载入，本次会话的撤销记录随之清空。）")


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setStyle("Fusion")
    _pick_cjk_font(app)
    # One icon for every window this process opens (the main one, the tool
    # windows, the message boxes), set at the application level rather than per
    # dialog.  ``icon_path()`` resolves inside the one-file bundle too, so this
    # works the same frozen as it does from a checkout — and it is skipped
    # rather than raising if the payload is ever missing, because a missing
    # icon must not stop the editor from opening a file.
    icon = paths.icon_path()
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    win = MainWindow()
    win.show()

    if len(argv) > 1:
        candidate = Path(argv[1])
        if candidate.exists():
            QTimer.singleShot(0, lambda: win.load_path(candidate))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())