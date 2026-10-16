import io

p = "ndfbin_decks/ndfedit/gui.py"
s = io.open(p, encoding="utf-8", newline="").read()

# ============ 1) 导入 QSizePolicy 等 ============
old_imp = "from PySide6.QtWidgets import (\n    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,\n    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,\n    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,\n    QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout,\n    QWidget,\n)"
new_imp = "from PySide6.QtWidgets import (\n    QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout,\n    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,\n    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox,\n    QSplitter, QVBoxLayout, QWidget,\n)"
assert old_imp in s, "imports"
s = s.replace(old_imp, new_imp, 1)

# ============ 2) FieldPanel：加「添加」按钮行 ============
old_init = '''        self._editors: dict[str, QWidget] = {}
        self._labels: dict[str, QWidget] = {}
        self._info: ClassInfo | None = None
        self._loading = False'''
new_init = '''        self._editors: dict[str, QWidget] = {}
        self._labels: dict[str, QWidget] = {}
        self._add_buttons: dict[str, QPushButton] = {}
        self._info: ClassInfo | None = None
        self._document = None          # 需要它来调 add_field
        self._loading = False'''
assert old_init in s, "panel init"
s = s.replace(old_init, new_init, 1)

# ============ 3) build_for：每个字段行加上「+ 添加」按钮 ============
old_build = '''        for name, kind, coverage in info.fields:
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
                tip += f"\\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
            if coverage < 0.5:
                tip += "\\n多数对象没有这个字段，改它只影响少数条目"
            label.setToolTip(tip)
            self.form.addRow(label, box)
            self._editors[name] = box
            self._labels[name] = label'''
new_build = '''        for name, kind, coverage in info.fields:
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
                tip += f"\\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"
            if coverage < 0.5:
                tip += "\\n多数对象没有这个字段，改它只影响少数条目"
            label.setToolTip(tip)

            # 缺字段时的「+ 添加」按钮。放在输入框右侧，正常状态隐藏，
            # 只有当前对象缺这个字段时才出现——这样默认界面依然干净。
            add = QPushButton("+ 添加")
            add.setFixedWidth(66)
            add.setToolTip(
                f"这个对象原本没有 {humanize(name)}。\\n"
                f"点一下就会补上，取同类对象的常见值作为初始值。\\n"
                f"随时可以再点「移除」撤销。")
            add.clicked.connect(lambda _, n=name: self._on_add(n))
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(4)
            hl.addWidget(box, 1)
            hl.addWidget(add, 0)
            add.setVisible(False)

            self.form.addRow(label, holder)
            self._editors[name] = box
            self._labels[name] = label
            self._add_buttons[name] = add'''
assert old_build in s, "build_for"
s = s.replace(old_build, new_build, 1)

# build_for 里清空 _add_buttons
old_clear = '''        self._editors.clear()
        self._labels.clear()'''
new_clear = '''        self._editors.clear()
        self._labels.clear()
        self._add_buttons.clear()'''
assert old_clear in s, "clear"
s = s.replace(old_clear, new_clear, 1)

io.open(p, "w", encoding="utf-8", newline="").write(s)
print("part1 ok")