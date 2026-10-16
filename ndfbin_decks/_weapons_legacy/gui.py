"""武器数值编辑器界面 —— MVP。

**为谁做的.** 会开Excel、不知道什么是 ndfbin 的mod 玩家。所以界面上
不存在「object_id」「type_id」「section」这些词，只有：弹药名、数值框、保存按钮。

**三栏布局**::

    [ 文件路径 / 打开 ]搜索框
    ┌──────────────┬─────────────────────────────────┐
    │ 弹药列表│ 选中弹药的数值面板               │
    │ 1548 行      │  物理伤害 [5.0      ] 最大射程 [    ]│
    │ 可搜索       │  ...│
    └──────────────┴─────────────────────────────────┘
    [ 已改动N项  ] [ 保存到文件 ]

**为什么改动要显式提交.** 编辑器改的是内存里的对象图，点「保存」才写盘。
中间任何一个时刻关掉窗口，游戏里的武器数值都不变—— 对二进制文件做编辑，
这条边界必须硬。

**共用弹药必须在界面上说清楚.** 实测 1548 种弹药里有 945 种被多把武器引用，
最极端的一种被 62 把武器共用。玩家在这里改一个数字，游戏里可能有一整排武器
同时变了。共用面板顶部会列出受影响的武器数与名字，不允许玩家在不知情的情况下
保存。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QVBoxLayout, QWidget,
)

from ..core import writer
from .model import FIELD_KINDS, FIELD_LABELS, FIELD_SPECS, AmmoEntry, WeaponFile

APP_TITLE = "Warno 武器数值编辑器"

#: 浮点框显示的小数位。NDF 里存的是 float32，6 位足够往返且不误导玩家。
DECIMALS = 4

#: 单个数值允许的范围。NDF 是 float32，超出这个范围游戏里会出怪值。
MIN_FLOAT = -1e9
MAX_FLOAT = 1e9

DIRTY_COLOR = QColor(255, 214, 102)


class AmmoList(QListWidget):
    """左侧弹药列表。改过的行标黄并置顶。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self._entries: list[AmmoEntry] = []
        self._current: AmmoEntry | None = None
        self._rebuilding = False

    def load(self, entries: list[AmmoEntry]):
        self._entries = entries
        self._rebuild()

    def _rebuild(self):
        self.clear()
        for entry in self._entries:
            label = entry.display or f"ammo#{entry.object_id}"
            if len(entry.weapons) > 1:
                label += f"   ({len(entry.weapons)} 把武器共用)"
            item = QListWidgetItem(label)
            if entry.dirty:
                item.setForeground(DIRTY_COLOR)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            if len(entry.weapons) == 1:
                item.setToolTip(entry.weapons[0])
            elif entry.weapons:
                item.setToolTip("、".join(entry.weapons[:12])
                                + ("…" if len(entry.weapons) > 12 else ""))
            self.addItem(item)

    def refresh_current(self):
        """仅刷新当前高亮行的字体状态，避免重建整个列表丢失选中项。"""
        idx = self.currentRow()
        if 0 <= idx < len(self._entries) and not self._rebuilding:
            self._rebuilding = True
            try:
                self._rebuild()
                self.setCurrentRow(idx)
            finally:
                self._rebuilding = False

    def entry_at(self, row: int) -> AmmoEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    @property
    def count(self) -> int:
        return len(self._entries)


class ValuePanel(QWidget):
    """右侧数值编辑面板。每个字段一个自旋框，直接写回原位dict。"""

    #: 任何影响「有没有改动」的操作都要发这个信号。
    #: 底栏的保存按钮状态由此驱动——不这么做就会出现
    #: 「数字改了但保存按钮还是灰的」，玩家根本存不了。
    dirtyChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entry: AmmoEntry | None = None
        self._editors: dict[str, QWidget] = {}
        self._loading = False

        root = QVBoxLayout(self)

        self.header = QLabel("未选择弹药")
        f = self.header.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        self.header.setFont(f)
        self.header.setWordWrap(True)
        root.addWidget(self.header)

        # 共用弹药警告——见模块 docstring
        self.shared = QLabel()
        self.shared.setWordWrap(True)
        self.shared.setStyleSheet(
            "background:#3d3320; border:1px solid #6b5a2a; border-radius:4px;"
            "padding:6px; color:#ffd66a;")
        self.shared.setVisible(False)
        root.addWidget(self.shared)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight)
        root.addLayout(self.form)

        root.addStretch(1)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        # 全部还原
        self.btn_revert = QPushButton("撤销这一条的全部改动")
        self.btn_revert.clicked.connect(self._revert_current)
        self.btn_revert.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.btn_revert)
        row.addStretch(1)
        root.addLayout(row)

    def _build_editors(self):
        for name, label, kind in FIELD_SPECS:
            if kind == "int":
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
            self.form.addRow(label, box)
            self._editors[name] = box

    def show_entry(self, entry: AmmoEntry | None):
        self._loading = True
        self._entry = entry

        if entry is None:
            self.header.setText("未选择弹药")
            self.shared.setVisible(False)
            self.btn_revert.setEnabled(False)
            for box in self._editors.values():
                _mark_missing(box, True)
            self._loading = False
            return

        self.header.setText(entry.display or f"ammo#{entry.object_id}")
        self._show_shared_warning(entry)
        self.btn_revert.setEnabled(entry.dirty)

        for name, box in self._editors.items():
            slot = entry.slots.get(name)
            if slot is None:
                _mark_missing(box, True)
                continue
            _mark_missing(box, False)
            value = slot.leaf["value"]
            if isinstance(box, QSpinBox):
                box.setValue(int(value))
            else:
                box.setValue(float(value))
            _paint(box, slot.dirty)

        self._loading = False

    def _show_shared_warning(self, entry: AmmoEntry):
        n = len(entry.weapons)
        if n == 0:
            self.shared.setText("⚠ 这个弹药没有被任何武器引用（可能是遗留数据）")
            self.shared.setVisible(True)
        elif n > 1:
            names = "、".join(entry.weapons[:8])
            more = f" 等 {n} 把" if n > 8 else ""
            self.shared.setText(
                f"⚠ 改动会同时影响 {n} 把武器：{names}{more}\n"
                f"   它们共用同一种弹药。")
            self.shared.setVisible(True)
        else:
            self.shared.setVisible(False)

    def _on_change(self, name: str):
        if self._loading or self._entry is None:
            return
        slot = self._entry.slots.get(name)
        if slot is None:
            return
        box = self._editors[name]
        new = box.value() if isinstance(box, QSpinBox) else box.value()
        if isinstance(box, QSpinBox):
            new = int(new)
        else:
            new = float(new)
        if new == slot.leaf["value"]:
            return
        slot.leaf["value"] = new
        _paint(box, slot.dirty)
        self.btn_revert.setEnabled(self._entry.dirty)
        self.dirtyChanged.emit()

    def _revert_current(self):
        if self._entry is None:
            return
        self._entry.reset()
        self.show_entry(self._entry)
        self.dirtyChanged.emit()


def _paint(box: QWidget, dirty: bool):
    """改过的框标黄，让玩家一眼看到自己动了什么。"""
    if dirty:
        box.setStyleSheet("background:#4a3f1e; border:1px solid #b89b45;"
                          "border-radius:3px; padding:2px;")
    else:
        box.setStyleSheet("")


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1080, 640)
        self.wf: WeaponFile | None = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addLayout(self._build_topbar())
        layout.addWidget(self._build_body(), 1)
        layout.addLayout(self._build_bottombar())

    # ---- 顶栏 ----

    def _build_topbar(self):
        row = QHBoxLayout()
        self.path_label = QLabel("尚未打开文件")
        self.path_label.setStyleSheet("color:#bbb;")
        btn_open = QPushButton("打开 Weapon.ndfbin…")
        btn_open.clicked.connect(self.on_open)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索弹药或武器名，例如2K11")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.on_search)
        row.addWidget(btn_open)
        row.addWidget(self.path_label, 1)
        row.addWidget(QLabel("搜索"))
        row.addWidget(self.search, 1)
        return row

    # ---- 主体 ----

    def _build_body(self):
        split = QSplitter(Qt.Horizontal)
        self.lst = AmmoList()
        self.lst.currentRowChanged.connect(self.on_select)
        split.addWidget(self.lst)
        self.panel = ValuePanel()
        self.panel._build_editors()
        self.panel.dirtyChanged.connect(self._on_entry_dirty)
        split.addWidget(self.panel)
        split.setSizes([420, 660])
        return split

    def _on_entry_dirty(self):
        """面板里改动了一个值：刷新底栏，并把该行在列表里标黄。"""
        self.lst.refresh_current()
        self._update_dirty()

    # ---- 底栏 ----

    def _build_bottombar(self):
        row = QHBoxLayout()
        self.status = QLabel("")
        self.btn_save = QPushButton("保存到文件")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.on_save)
        btn_reload = QPushButton("放弃全部改动")
        btn_reload.clicked.connect(self.on_reload)
        self.btn_reload = btn_reload
        self.btn_reload.setEnabled(False)
        row.addWidget(self.status, 1)
        row.addWidget(btn_reload)
        row.addWidget(self.btn_save)
        return row

    # ---- 动作 ----

    def on_open(self):
        start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Weapon.ndfbin", start,
            "Warno 数据文件 (*.ndfbin);;所有文件 (*)")
        if path:
            self.load_path(Path(path))

    def load_path(self, path: Path):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            wf = WeaponFile.load(path)
        except Exception as exc:                       # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self, "打不开",
                f"无法解析这个文件：\n\n{exc}\n\n"
                "它可能不是 WARNO 的 Weapon.ndfbin。")
            traceback.print_exc()
            return
        QApplication.restoreOverrideCursor()

        self.wf = wf
        self.path_label.setText(str(path))
        self.lst.load(wf.entries())
        self.panel.show_entry(None)
        n_named = sum(1 for a in wf.ammo if a.weapons)
        self.status.setText(
            f"{len(wf.ammo)} 种弹药，其中 {n_named} 种已关联到武器"
            f" · {wf.decoded['object_count']} 个对象")
        self._update_dirty()
        if self.lst.count:
            self.lst.setCurrentRow(0)

    def on_search(self, text: str):
        if self.wf is None:
            return
        row = self.lst.currentRow()
        self.lst.load(self.wf.search(text))
        if self.lst.count:
            self.lst.setCurrentRow(min(max(row, 0), self.lst.count - 1))
        self.status.setText(f"筛选出 {self.lst.count} 条"
                            if text else self._default_status())

    def _default_status(self) -> str:
        if self.wf is None:
            return ""
        n_named = sum(1 for a in self.wf.ammo if a.weapons)
        return (f"{len(self.wf.ammo)} 种弹药，其中 {n_named} 种已关联到武器"
                f" · {self.wf.decoded['object_count']} 个对象")

    def on_select(self, row: int):
        entry = self.lst.entry_at(row)
        self.panel.show_entry(entry)

    def _update_dirty(self):
        n = self.wf.dirty_count if self.wf else 0
        self.btn_save.setEnabled(n > 0)
        self.btn_reload.setEnabled(n > 0)
        if n:
            self.status.setText(f"● 已改动 {n} 种弹药，尚未保存")
        else:
            self.status.setText(self._default_status())

    def on_reload(self):
        if self.wf is None:
            return
        if QMessageBox.question(
                self, "放弃改动",
                f"确定放弃全部 {self.wf.dirty_count} 处改动？"
                "\n\n文件从未被改动过，放弃后需要重新打开才能再编辑。") \
                != QMessageBox.Yes:
            return
        self.wf.reset_all()
        self.load_path(self.wf.path)

    def on_save(self):
        if self.wf is None or not self.wf.dirty_count:
            return
        n = self.wf.dirty_count
        # 汇总共用弹药的连带影响，让玩家在写盘前知情
        shared = [a for a in self.wf.ammo if a.dirty and len(a.weapons) > 1]
        extra = ""
        if shared:
            worst = max(shared, key=lambda a: len(a.weapons))
            extra = (f"\n\n注意：其中 {len(shared)} 种弹药被多把武器共用，"
                     f"改动会同时影响它们。\n"
                     f"影响最大的一种：{worst.display} —— 共 {len(worst.weapons)} 把武器。")

        if QMessageBox.question(
                self, "确认保存",
                f"将把 {n} 处改动写入：\n{self.wf.path}\n\n"
                f"首次保存会在同目录生成 .bak 原始备份。{extra}\n\n继续？") \
                != QMessageBox.Yes:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.wf.save()
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

        # 写盘后doc 的raw_data 仍是旧快照，必须重新解码才能继续编辑
        try:
            self.load_path(self.wf.path)
        except Exception:                              # noqa: BLE001
            pass
        QMessageBox.information(
            self, "已保存",
            f"写入完成。\n\n"
            f"体积：{result.size_before:,} → {result.size_after:,} 字节"
            + (f"\n原始备份：{result.backup.name}" if result.backup else "")
            + "\n\n进入游戏即可看到效果。")


#: 优先使用的中文字体，按可用性降级。玩家看到豆腐块是最糟的失败形态，
#: 所以字体必须在入口就定死，不能指望 Qt 在任何机器上都能猜对。
CJK_FONTS = ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei",
             "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC")


def _pick_cjk_font(app) -> str | None:
    from PySide6.QtGui import QFont, QFontDatabase
    families = set(QFontDatabase.families())
    for name in CJK_FONTS:
        if name in families:
            font = QFont(name, 9)
            app.setFont(font)
            return name
    return None


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setStyle("Fusion")
    _pick_cjk_font(app)

    win = MainWindow()
    win.show()

    # 支持双击 .pyw / 命令行传参直接打开
    if len(argv) > 1:
        candidate = Path(argv[1])
        if candidate.exists():
            QTimer.singleShot(0, lambda: win.load_path(candidate))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


MISSING_TEXT = "无此字段"


def _mark_missing(box: QWidget, missing: bool):
    """把数值框切到「该弹药没有此字段」状态。

    ``QSpinBox`` 没有 ``setPlaceholderText``，所以用 specialValueText：
    设一个专用最小值并让spinbox 停在该值，同时锁定下界——玩家既看到提示，
    也拖不动它。这比灰掉一个 0 让人以为「射程真的是 0」要诚实得多。
    """
    if missing:
        box.setEnabled(False)
        try:
            box.setMinimum(-1_000_000_001)
            box.setSpecialValueText(MISSING_TEXT)
            box.setValue(-1_000_000_001)
            box.setMinimum(-1_000_000_001)
        except (AttributeError, TypeError):
            pass
    else:
        box.setEnabled(True)
        if isinstance(box, QSpinBox):
            box.setMinimum(-1_000_000_000)
            box.setSpecialValueText("")
        else:
            box.setMinimum(MIN_FLOAT)
            box.setSpecialValueText("")
