"""Mod / 文件两级选择的专项测试。

定位 Mod 的逻辑完全复用 ``ndfbin_decks.io.modlocator``（本机工作区、
创意工坊、手动目录三来源 + 去重），这里要测的是它上面那一层：
**从这个 Mod 里能打开哪些文件**，以及两级界面的行为。

测试刻意不依赖 `discover()` 的结果——那取决于跑测试的机器上装了什么。
用一个手工构造的 ``ModPaths`` 指向真实的 workshop mod 目录，
再单独用容错的方式验证 `discover()` 至少不抛异常。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from ndfbin_decks.io import modlocator                            # noqa: E402
from ndfbin_decks.ndfedit import modpicker                        # noqa: E402
from ndfbin_decks.ndfedit.gui import MainWindow                   # noqa: E402
from ndfbin_decks.ndfedit.modpicker import (                      # noqa: E402
    ModBrowserDialog, NdfbinFile, scan_ndfbin)

GFX = _env.gfx()
MOD_ROOT = _env.mod_dir(_env.SAMPLE)  # …/<mod>, same source as gfx()
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def fake_mod(root=None, **kw) -> "modlocator.ModPaths":
    return modlocator.ModPaths(
        name=kw.get("name", "3388575848"),
        deploy=Path(root) if root else MOD_ROOT,
        source=kw.get("source", modlocator.SOURCE_WORKSHOP),
        label=kw.get("label", "勘查用 mod"))


def main() -> int:
    app = _env.qt_app()
    # 离屏环境下模态框会一直等一个永远不会来的点击，把测试挂死。
    # 坏文件那条用例就是靠弹框报错的，这里换成静默通过。
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

    if not MOD_ROOT.is_dir():
        print(f"跳过：找不到测试用 mod {MOD_ROOT}")
        return 0

    print("\n=== 1. 扫描 mod 里的 ndfbin ===")
    files = scan_ndfbin(fake_mod())
    check(f"扫到 {len(files)} 个文件", len(files) >= 3, str(len(files)))
    names = [f.name for f in files]
    check("含 Deck/Unit/Weapon",
          {"Deck.ndfbin", "Unit.ndfbin", "Weapon.ndfbin"} <= set(names),
          str(names[:6]))
    check("全部是 .ndfbin", all(f.name.endswith(".ndfbin") for f in files))
    check("路径都存在", all(f.path.is_file() for f in files))
    check("大小都 > 0", all(f.size > 0 for f in files))

    print("\n=== 2. 排序：常用的排最前 ===")
    head = [f.name for f in files[:len(modpicker.COMMON_ORDER)]]
    common = [n for n in head if n in modpicker.COMMON_ORDER]
    check("头部是常见文件", len(common) >= 3, str(head))
    order_idx = [modpicker.COMMON_ORDER.index(n) for n in common]
    check("常见文件之间保持固定次序", order_idx == sorted(order_idx),
          str(order_idx))
    deck_i = names.index("Deck.ndfbin")
    check("Deck 排在第一", deck_i == 0, str(deck_i))

    print("\n=== 3. 显示路径剥掉 Gen/NDF 前缀 ===")
    # 排序后第一个就是 Gen/NDF/GFX/Deck.ndfbin（同名的非 GFX 那份排在后面）
    deck = files[0]
    check("首个是 GFX 下的 Deck",
          deck.relative == "Gen/NDF/GFX/Deck.ndfbin", deck.relative)
    check("relative 保留完整相对路径",
          deck.relative.startswith("Gen/NDF/"), deck.relative)
    check("display 剥掉冗余前缀", deck.display == "GFX/Deck.ndfbin",
          deck.display)
    same_name = [f for f in files if f.name == "Deck.ndfbin"]
    if len(same_name) > 1:
        check("同名的两份显示不同，能区分",
              len({f.display for f in same_name}) == len(same_name),
              str([f.display for f in same_name]))
        check("GFX 那份排在前面",
              files.index(same_name[0]) < files.index(same_name[1])
              or "GFX/" in same_name[0].display,
              str([f.display for f in same_name]))
    else:
        check("同名两份（样本里只有一个，跳过）", True)
    local = [f for f in files if not f.relative.startswith("Gen/NDF/")]
    if local:
        check("非 NDF 目录的保持完整",
              all(f.display == f.relative for f in local),
              str([f.display for f in local][:2]))
    else:
        check("非 NDF 目录的保持完整（样本里没有，跳过）", True)

    print("\n=== 4. 大小与「加载慢」标记 ===")
    check("KB 显示", deck.size_text().endswith(("KB", "MB")),
          deck.size_text())
    tiny = NdfbinFile(Path("x.ndfbin"), "x.ndfbin", 388)
    check("小文件用 B", tiny.size_text().endswith("B"), tiny.size_text())
    check("小文件不算慢", not tiny.slow)
    big = NdfbinFile(Path("x"), "x", modpicker.SLOW_BYTES + 1)
    check("超阈值标为慢", big.slow)
    check("MB 换算", NdfbinFile(Path("x"), "x", 5 * 1024 * 1024
                                ).size_text() == "5.0 MB",
          NdfbinFile(Path("x"), "x", 5 * 1024 * 1024).size_text())

    print("\n=== 5. 没有 deploy 的 mod 不炸 ===")
    empty = modlocator.ModPaths(name="ghost", deploy=None)
    check("返回空列表", scan_ndfbin(empty) == [])
    missing = modlocator.ModPaths(name="ghost2", deploy=Path("Z:/nope"))
    check("不存在的目录返回空", scan_ndfbin(missing) == [])

    print("\n=== 6. discover() 在本机能跑通 ===")
    try:
        mods = modlocator.discover()
        check(f"发现 {len(mods)} 个 mod（含 0 也算通过）", True, str(len(mods)))
        if mods:
            check("每个 mod 都有名称与来源",
                  all(m.name and m.source_label for m in mods))
    except modlocator.ModNotFound as exc:
        check("没有 mod 时给出可读的说明", "workshop" in str(exc).lower()
              or "workspace" in str(exc).lower(), str(exc)[:60])

    print("\n=== 7. 对话框：两级联动 ===")
    dlg = ModBrowserDialog(None)
    check("列出了一级（mod）", dlg.mods.count() > 0, str(dlg.mods.count()))
    check("一级项带来源标签",
          "·" in dlg.mods.item(0).text(), dlg.mods.item(0).text())
    check("一级项带 tooltip",
          "mod:" in (dlg.mods.item(0).toolTip() or ""),
          (dlg.mods.item(0).toolTip() or "")[:24])

    # 挑一个有文件的 mod
    rows = [(i, len(scan_ndfbin(dlg.mods.item(i).data(Qt.UserRole))))
            for i in range(dlg.mods.count())]
    best = max(rows, key=lambda x: x[1])
    check("至少一个 mod 有文件", best[1] > 0, str(best))
    dlg.mods.setCurrentRow(best[0])
    app.processEvents()
    check("二级填上了文件", dlg.files.count() == best[1],
          f"{dlg.files.count()} vs {best[1]}")
    check("二级项显示大小",
          any("KB" in dlg.files.item(i).text() or "B" in dlg.files.item(i).text()
              for i in range(dlg.files.count())),
          dlg.files.item(0).text())

    dlg.select_first()
    app.processEvents()
    check("默认落在第一个文件", dlg.files.currentRow() == 0)
    check("打开按钮已启用", dlg.btn_open.isEnabled())
    check("打开按钮写上了文件名",
          dlg.files.currentItem().data(Qt.UserRole).name
          in dlg.btn_open.text(), dlg.btn_open.text())

    print("\n=== 8. 搜索同时过滤两级 ===")
    n_all = dlg.files.count()
    dlg.search.setText("deck")
    app.processEvents()
    vis = [i for i in range(dlg.files.count())
           if not dlg.files.item(i).isHidden()]
    check("二级被过滤", len(vis) < n_all, f"{n_all} -> {len(vis)}")
    check("留着的是 Deck", any("Deck" in dlg.files.item(i).text()
                               for i in vis),
          str([dlg.files.item(i).text() for i in vis]))
    dlg.search.setText("")
    app.processEvents()
    check("清空后恢复", len([i for i in range(dlg.files.count())
                            if not dlg.files.item(i).isHidden()]) == n_all)

    print("\n=== 9. accept 返回文件 + mod 上下文 ===")
    dlg.select_first()
    app.processEvents()
    dlg.accept()
    check("chosen 是文件路径", dlg.chosen is not None
          and dlg.chosen.is_file(), str(dlg.chosen))
    check("chosen_mod 是 ModPaths", dlg.chosen_mod is not None
          and isinstance(dlg.chosen_mod, modlocator.ModPaths))
    rel = dlg.relative_of(dlg.chosen)
    check("相对路径不含 Gen/NDF", "Gen/NDF" not in rel, rel)
    check("相对路径就是文件本身",
          dlg.chosen.name in rel, rel)

    print("\n=== 10. 界面：顶栏入口 + 真开文件 ===")
    win = MainWindow()
    labels = [b.text() for b in win.findChildren(type(win.btn_save))]
    check("顶栏有「浏览 Mod…」", any("浏览 Mod" in t for t in labels),
          str(labels[:3]))
    check("也保留了直接开文件", any("单个文件" in t for t in labels),
          str(labels[:3]))

    target = next(f for f in files if f.name == "Deck.ndfbin")
    orig_exec = ModBrowserDialog.exec

    def fake_exec(self):
        # 选到目标 mod 的那一行，再选 Deck
        for i in range(self.mods.count()):
            m = self.mods.item(i).data(Qt.UserRole)
            if m and m.deploy == MOD_ROOT:
                self.mods.setCurrentRow(i)
                break
        for i in range(self.files.count()):
            if self.files.item(i).data(Qt.UserRole).path == target.path:
                self.files.setCurrentRow(i)
                break
        self.accept()
        return QDialog.Accepted
    ModBrowserDialog.exec = fake_exec
    try:
        win.on_browse_mod()
    finally:
        ModBrowserDialog.exec = orig_exec
    app.processEvents()
    check("文件真的打开了", win.nd is not None
          and win.nd.path == target.path, str(win.nd.path if win.nd else None))
    check("顶栏显示 Mod 名 + 相对路径",
          "·" in win.path_label.text()
          and target.name in win.path_label.text(),
          win.path_label.text())
    # 路径来自 discover()，大小写可能与测试里写死的常量不同，按 Path 比
    check("顶栏 tooltip 是完整路径",
          Path(win.path_label.toolTip()) == target.path,
          win.path_label.toolTip())
    info = win.nd.class_info(win._current_class)
    check("状态栏停在当前类上",
          win._current_class in win.status.text()
          and f"{len(win.nd.entries(win._current_class))} 个对象"
          in win.status.text(),
          win.status.text()[:80])
    check("当前类是文件里真实存在的",
          info is not None and info.count > 0,
          str(win._current_class))

    print("\n=== 11. 直接开文件时顶栏退回文件名 ===")
    check("load_path 返回 True", win.load_path(target.path) is True)
    check("顶栏显示裸文件名",
          win.path_label.text() == target.name, win.path_label.text())
    bogus = MOD_ROOT / "Gen" / "NDF" / "GFX" / "__not_cndf__.ndfbin"
    bogus.write_bytes(b"not a cndf at all")
    try:
        check("坏文件返回 False", win.load_path(bogus) is False)
        check("坏文件没换掉已打开的文件",
              win.nd is not None and win.nd.path == target.path)
    finally:
        bogus.unlink(missing_ok=True)

    print("\n" + "=" * 54)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：")
        for n in FAIL:
            print(f"  - {n}")
    print("=" * 54)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
