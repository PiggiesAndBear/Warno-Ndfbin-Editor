"""先选 Mod，再选它里面的 ``.ndfbin``。

**为什么多一级.** NdfbinDecksEditor 的流程是「选 mod → 直接开它那个固定文件
（``Deck.ndfbin``）」，因为那个编辑器只编牌组。通用编辑器要编辑 mod 里的
**任意** ndfbin（``Unit`` / ``Weapon`` / ``Depiction`` …），所以选完 mod 还得
列一下它有什么。

**定位逻辑一个字没重写.** 全部走 :mod:`ndfbin_decks.io.modlocator` ——
它已经把三件麻烦事解决过了：

1. **两个部署根**。``Saved Games/EugenSystems/WARNO/mod/<Name>/`` 是本机
   ModGen 工作区；``<steamapps>/workshop/content/1611600/<id>/`` 是创意工坊。
   订阅的 mod **不会**被复制进本机工作区，只认前者会让「我订阅的 mod 呢」
   成为每个人的第一个问题。
2. **Steam 库不在 C 盘**。游戏常装在别的盘，靠 ``libraryfolders.vdf`` 找，
   注册表只是线索之一而不是必需。
3. **同一个 mod 从两处发现**。按解析后的真实路径去重。

这里只做一件 modlocator 不做的事：**在选中的 mod 里找出可编辑的文件**。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from ..io import modlocator

NDFBIN_SUFFIX = ".ndfbin"

#: 超过这个大小就提示「加载慢」。实测 ``Depiction.ndfbin`` 有 77923 个对象。
SLOW_BYTES = 4 * 1024 * 1024

#: 这几个文件覆盖了 mod 改造的绝大部分需求，排在列表最前面。
#: 排序不是按大小也不是纯字母——玩家找 ``Unit.ndfbin`` 的次数会远多于
#: ``Depiction.ndfbin``。
COMMON_ORDER = ("Deck.ndfbin", "Unit.ndfbin", "Weapon.ndfbin",
                "Division.ndfbin", "Skirmish.ndfbin", "Depiction.ndfbin")


@dataclass(frozen=True)
class NdfbinFile:
    """一个 mod 里可以打开的文件。"""

    path: Path
    #: 相对 mod 根的路径，正斜杠——列表里显示这个，跨盘符也对得上。
    relative: str
    size: int

    @property
    def name(self) -> str:
        return self.relative.rsplit("/", 1)[-1]

    @property
    def display(self) -> str:
        """列表里显示的路径。

        ``Gen/NDF/`` 是 ModGen 编译产物的固定前缀，几乎每个文件都带着它
        ——列一整屏全是这四个词，纯属噪音。剥掉之后 ``GFX/Deck.ndfbin``
        和 ``Localisation/Test.ndfbin`` 一眼就分得清。

        不在 ``Gen/NDF/`` 下的（``Gen/TextureProxy/…`` 这种）保持完整，
        因为对它们来说那段路径是有信息量的。

        **剥完仍然可能重名**：``Gen/NDF/Deck.ndfbin`` 显示成 ``Deck.ndfbin``，
        而 ``Gen/NDF/GFX/Deck.ndfbin`` 显示成 ``GFX/Deck.ndfbin``——
        两者依然分得开，而且 :func:`_sort_key` 让 ``GFX/`` 那份排前面。
        """
        for prefix in ("Gen/NDF/", "gen/ndf/", "Gen\\NDF\\"):
            if self.relative.startswith(prefix):
                return self.relative[len(prefix):]
        return self.relative

    @property
    def directory(self) -> str:
        head = self.display.rsplit("/", 1)[0] if "/" in self.display else ""
        return head

    @property
    def slow(self) -> bool:
        return self.size > SLOW_BYTES

    def size_text(self) -> str:
        n = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return (f"{n:.0f} {unit}" if unit == "B"
                        else f"{n:.1f} {unit}")
            n /= 1024
        return f"{n:.1f} GB"


#: 编译产物在 ``Gen/NDF`` 下的位置。``Gen/NDF/GFX`` 是游戏真正读的那一份。
GFX_PREFIX = "Gen/NDF/GFX/"


def _sort_key(rel: str):
    """列表排序：先 ``GFX/``、再常见文件名、最后字母序。

    **``GFX/`` 优先是有原因的.** 实测某个 mod 里同时存在
    ``Gen/NDF/Deck.ndfbin`` 和 ``Gen/NDF/GFX/Deck.ndfbin``——同名两份。
    游戏加载的是 ``GFX/`` 那份，所以它必须排在前面；否则用户按名字找
    ``Deck.ndfbin``，点开的是另一份，改完发现游戏里没变化。
    """
    base = rel.rsplit("/", 1)[-1]
    in_gfx = 0 if rel.startswith(GFX_PREFIX) else 1
    if base in COMMON_ORDER:
        return (in_gfx, 0, COMMON_ORDER.index(base), rel)
    return (in_gfx, 1, 0, rel)


def _walk_ndfbin(base: Path, *, max_depth: int, max_entries: int) -> list[Path]:
    """在 ``base`` 下找 ``.ndfbin``，带深度与条目上限。

    **必须设上限.** ``rglob`` 整棵树在有的 mod 上会卡死——实测一个地图 mod
    的目录里塞着几 GB 的资源，而它一个 ndfbin 都没有。宁可在极端目录结构上
    少列几个文件，也不能让对话框打开时卡住。
    """
    out: list[Path] = []
    seen = 0
    queue: list[tuple[Path, int]] = [(base, 0)]
    while queue:
        d, depth = queue.pop()
        try:
            with os.scandir(d) as entries:
                for e in entries:
                    seen += 1
                    if seen > max_entries:
                        return out
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                queue.append((Path(e.path), depth + 1))
                        elif e.name.lower().endswith(NDFBIN_SUFFIX):
                            out.append(Path(e.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


#: ``str(mod 根) -> 文件列表``。对话框会在「先列 mod、再选 mod」之间反复问
#: 同一批目录，不缓存的话每切一次就要重扫一遍。
_SCAN_CACHE: dict[str, list[NdfbinFile]] = {}


def scan_ndfbin(mod: "modlocator.ModPaths", *, refresh: bool = False
                ) -> list[NdfbinFile]:
    """列出这个 mod 里所有 ``.ndfbin``。

    三级退让，每一级都比上一级贵，所以正常情况下只走第一级：

    1. ``Gen/NDF`` —— ModGen 编译产物的固定位置，实测所有标准 mod 都在这；
    2. ``Gen`` —— 深度 4，覆盖 ``Gen/TextureProxy/…`` 这类非标准位置；
    3. 整个 mod 根 —— 条目上限 3 万，兜住用户手工拼的目录，
       同时在几 GB 的资源包上及时收手。
    """
    root = mod.deploy
    if root is None or not root.is_dir():
        return []
    key = str(root)
    if not refresh and key in _SCAN_CACHE:
        return _SCAN_CACHE[key]

    found: dict[Path, None] = {}
    for base, depth, budget in (
            (root / "Gen" / "NDF", 6, 20000),
            (root / "Gen", 4, 20000),
            (root, 4, 30000)):
        if not base.is_dir():
            continue
        for p in _walk_ndfbin(base, max_depth=depth, max_entries=budget):
            found[p] = None
        if found:
            break

    out: list[NdfbinFile] = []
    for p in found:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = Path(p.name)
        out.append(NdfbinFile(path=p, relative=str(rel).replace("\\", "/"),
                              size=size))
    out.sort(key=lambda f: _sort_key(f.relative))
    _SCAN_CACHE[key] = out
    return out


class ModBrowserDialog(QDialog):
    """两级选择：左边 Mod，右边它里面的 ndfbin。

    单选一级做不到这件事——NdfbinDecksEditor 的 mod 下拉框背后是「这个 mod
    的牌组在哪」这一个答案，而这里的问题是「这个 mod 里有什么可以改」。
    """

    def __init__(self, parent=None, *, start_dir: Path | None = None):
        super().__init__(parent)
        #: 用户选定的文件；取消则保持 ``None``。
        self.chosen: Path | None = None
        #: 选中的文件属于哪个 Mod——主界面拿它拼「Mod 名 · 相对路径」。
        self.chosen_mod: "modlocator.ModPaths | None" = None
        self._extra_mods: list[modlocator.ModPaths] = []
        self._files: list[NdfbinFile] = []
        self.setWindowTitle("浏览 Mod")
        self.resize(960, 640)

        root = QVBoxLayout(self)

        head = QLabel(
            "先选一个 Mod，再从右边挑它里面的 <code>.ndfbin</code>。"
            "<br><span style='color:#8ab4d8;'>列表来自两处：本机 ModGen 工作区"
            "（Saved Games）和 Steam 创意工坊。"
            "两者都没有的话，用下面的「打开 Mod 文件夹」指过去。</span>")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("过滤 Mod 名 / 文件名")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._filter())
        root.addWidget(self.search)

        split = QSplitter(Qt.Horizontal)
        self.mods = QListWidget()
        self.mods.currentRowChanged.connect(self._on_mod)
        split.addWidget(self._wrap("Mod", self.mods))

        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.SingleSelection)
        self.files.currentRowChanged.connect(self._on_file)
        self.files.itemDoubleClicked.connect(lambda _it: self.accept())
        split.addWidget(self._wrap("可编辑的 .ndfbin", self.files))
        split.setSizes([330, 610])
        root.addWidget(split, 1)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color:#8ab4d8;")
        root.addWidget(self.detail)

        row = QHBoxLayout()
        self.btn_folder = QPushButton("打开 Mod 文件夹…")
        self.btn_folder.setToolTip(
            "自动发现只能看 Steam 和本机工作区。U 盘上的 mod、解压出来的包、"
            "另一个 Windows 账户的工作区都从这里进。")
        self.btn_folder.clicked.connect(self._on_open_folder)
        row.addWidget(self.btn_folder)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#999;")
        row.addWidget(self.status, 1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_open = QPushButton("打开这个文件")
        self.btn_open.setDefault(True)
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self.accept)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_open)
        root.addLayout(row)

        self._load_mods(start_dir)

    @staticmethod
    def _wrap(title: str, widget: QWidget) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        tag = QLabel(title)
        tag.setStyleSheet("color:#8ab4d8; font-weight:bold;")
        lay.addWidget(tag)
        lay.addWidget(widget, 1)
        return box

    # ---- 数据 ----

    def _load_mods(self, start_dir: Path | None):
        self._extra_mods = []
        errors: list[str] = []
        try:
            mods = modlocator.discover()
        except modlocator.ModNotFound as exc:
            mods = []
            errors.append(str(exc))
        except Exception as exc:                       # noqa: BLE001
            mods = []
            errors.append(f"自动发现失败：{exc}")
        if start_dir is not None and Path(start_dir).is_dir():
            try:
                mods = list(mods) + [
                    m for m in modlocator.discover_in_folder(Path(start_dir))
                    if all(m.deploy != x.deploy for x in mods)]
            except Exception:                          # noqa: BLE001
                pass

        self.mods.blockSignals(True)
        self.mods.clear()
        for m in mods:
            self._add_mod(m)
        self.mods.blockSignals(False)

        if not mods:
            self.status.setText(
                errors[0] if errors
                else "没有发现任何 Mod。用下面的「打开 Mod 文件夹」指过去。")
        else:
            self.status.setText(
                f"发现 {len(mods)} 个 Mod（本机 + 创意工坊）")
        if self.mods.count():
            self.mods.setCurrentRow(0)

    def _add_mod(self, mod: "modlocator.ModPaths"):
        files = scan_ndfbin(mod)
        label = f"{mod.label}   ·   {mod.source_label}"
        if not files:
            # 灰掉但**仍可选中**：tooltip 里解释了为什么没有文件，
            # 用户点一下就能看到，比一个点不动的灰条目有用。
            label += "（无 ndfbin）"
        it = QListWidgetItem(label)
        tip = [mod.readme(), "", f"{len(files)} 个 .ndfbin"]
        if not files:
            tip.append("（这个 mod 里没有编译好的 ndfbin——"
                       "可能还没在 ModGen 里导出过，或者它的数据用别的方式打包）")
        it.setToolTip("\n".join(tip))
        it.setData(Qt.UserRole, mod)
        self.mods.addItem(it)
        if not files:
            it.setForeground(Qt.gray)

    def _on_mod(self, row: int):
        it = self.mods.item(row)
        self.files.clear()
        self.detail.setText("")
        if it is None:
            return
        mod = it.data(Qt.UserRole)
        files = scan_ndfbin(mod)
        self._files = files
        for f in files:
            label = f"{f.display}    {f.size_text()}"
            if f.slow:
                label += "   （较大，加载要几秒）"
            fi = QListWidgetItem(label)
            fi.setToolTip(f"{f.relative}\n{f.path}")
            fi.setData(Qt.UserRole, f)
            self.files.addItem(fi)
        if not files:
            self.detail.setText(
                "这个 Mod 里没找到 .ndfbin。"
                "它可能只有 NDF 源码（还没编译），或者用了非标准的目录结构。")
        else:
            self.detail.setText(
                f"{mod.label} —— {len(files)} 个文件。"
                f"双击一个就能打开。")
            # 自动落在第一个文件上：省一次点击，而且「打开这个文件」按钮
            # 一开始就是可用的，玩家不会以为这个界面只能看不能动。
            self.files.setCurrentRow(0)
        self._on_file(self.files.currentRow())

    def _filter(self):
        needle = self.search.text().strip().lower()
        # 过滤 Mod 列表，同时也过滤右侧文件列表
        for i in range(self.mods.count()):
            it = self.mods.item(i)
            it.setHidden(bool(needle) and needle not in it.text().lower())
        for i in range(self.files.count()):
            it = self.files.item(i)
            it.setHidden(bool(needle)
                         and needle not in it.text().lower())
        if self.mods.currentItem() is not None \
                and self.mods.currentItem().isHidden():
            row = next((i for i in range(self.mods.count())
                        if not self.mods.item(i).isHidden()), None)
            if row is not None:
                self.mods.setCurrentRow(row)

    def _on_file(self, row: int):
        it = self.files.item(row)
        self.btn_open.setEnabled(it is not None)
        if it is None:
            return
        f = it.data(Qt.UserRole)
        self.btn_open.setText(f"打开 {f.name}")

    def _on_open_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择 Mod 目录（含 Gen/NDF 的那一层）",
            str(self._default_browse_dir()))
        if not chosen:
            return
        try:
            found = modlocator.discover_in_folder(Path(chosen))
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        if not found:
            # 也允许直接指到一个裸目录：只要有 ndfbin 就收
            loose = sorted(Path(chosen).glob(f"*{NDFBIN_SUFFIX}"))
            if loose:
                self._add_loose_dir(Path(chosen), loose)
                return
            QMessageBox.information(
                self, "没找到 Mod",
                f"{chosen}\n\n里面既不是含 Gen/ 或 Config.ini 的 Mod 目录，"
                f"子目录里也没有这样的 Mod，也没直接看到 .ndfbin 文件。")
            return
        known = {self.mods.item(i).data(Qt.UserRole).deploy
                 for i in range(self.mods.count())}
        added = 0
        for m in found:
            if m.deploy in known:
                continue
            self._add_mod(m)
            added += 1
        self.status.setText(
            f"从 {chosen} 加了 {added} 个 Mod" if added
            else f"{chosen} 里的 Mod 已经在列表里了")
        if added:
            self.mods.setCurrentRow(self.mods.count() - 1)

    def _add_loose_dir(self, path: Path, loose: list[Path]):
        """一个裸目录，里面直接躺着 .ndfbin（不是标准 mod 结构）。"""
        mod = modlocator.ModPaths(name=path.name, deploy=path,
                                  source=modlocator.SOURCE_MANUAL,
                                  label=f"{path.name}（直接放的 ndfbin）")
        self._add_mod(mod)
        self.status.setText(
            f"把 {path} 当成一个目录加了 {len(loose)} 个文件")
        self.mods.setCurrentRow(self.mods.count() - 1)

    def _default_browse_dir(self) -> Path:
        try:
            return modlocator.deploy_root()
        except Exception:                              # noqa: BLE001
            return Path.home()

    # ---- 结果 ----

    def accept(self):
        it = self.files.currentItem()
        if it is None:
            return
        self.chosen = it.data(Qt.UserRole).path
        mod_item = self.mods.currentItem()
        self.chosen_mod = mod_item.data(Qt.UserRole) if mod_item else None
        super().accept()

    def relative_of(self, path: Path) -> str:
        """选中文件在 Mod 里的位置（给主界面顶栏显示）。"""
        mod = self.chosen_mod
        if mod is not None and mod.deploy is not None:
            try:
                rel = str(path.relative_to(mod.deploy)).replace("\\", "/")
                for prefix in ("Gen/NDF/", "gen/ndf/"):
                    if rel.startswith(prefix):
                        return rel[len(prefix):]
                return rel
            except ValueError:
                pass
        return path.name

    def select_first(self) -> None:
        """默认落在第一个文件上——省一次点击。"""
        if self.files.count():
            self.files.setCurrentRow(0)


def pick(dialog_parent=None, *, start_dir: Path | None = None) -> Path | None:
    """跑一次浏览，返回用户选的文件或 ``None``。"""
    dlg = ModBrowserDialog(dialog_parent, start_dir=start_dir)
    if dlg.exec() != QDialog.Accepted:
        return None
    return dlg.chosen


__all__ = ["NdfbinFile", "ModBrowserDialog", "scan_ndfbin", "pick",
           "SLOW_BYTES", "COMMON_ORDER"]
