# -*- coding: utf-8 -*-
"""AG 属性页的撤销接线验收测试。

分两层，因为**它们会分别坏掉，而且症状完全不同**：

* **数据层** —— ``History`` 能不能把「克隆 + 重指向 + 改值」这一步退回原样。
  这一步压在同一次 ``commit()`` 里，于是 ``_apply`` 必须按语义决定写入顺序：
  撤销时先把引用指回去、最后才砍掉克隆体。顺序反了会被
  ``set_appended_count`` 的引用检查拦下（那句拒绝是对的，它挡住的是
  「文件里留下指向不存在对象的引用」）。
* **界面层** —— 撤销之后表格里的数字有没有跟着变。不跟着变的话，玩家会
  以为撤销没生效，然后再改一次。这一层靠 ``AgPage.refresh()``，而主窗口的
  ``_refresh_after_history()`` 是**按这个名字**去找各专用页的。

    python -m tests.test_ag_undo
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from ndfbin_decks.ndfedit import ag_view                 # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument      # noqa: E402

PAWN = _env.mod_dir(_env.BERLIN) / "Gen" / "NDF" / "GFX" / "Pawn.ndfbin"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if detail:
        print(f"        {detail}")


def air_distribution(nd: NdfDocument) -> Counter:
    """全文件里防空半径的取值分布——撤销前后必须逐值对得上。"""
    pawns = ag_view.build_pawns(nd)
    return Counter(p.values.get("air_deny") for p in pawns
                   if "air_deny" in p.values)


def fresh_copy(tmp: Path, name: str = "Pawn.ndfbin") -> Path:
    dst = tmp / name
    shutil.copy2(PAWN, dst)
    return dst


# ---- 一、数据层 ----


def test_data_layer() -> None:
    from ndfbin_decks.ndfedit import ag_edit
    from ndfbin_decks.ndfedit import ag_view as av

    print("\n=== 1. 数据层：History 能不能退回一步「克隆 + 重指向 + 改值」 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agundo1_"))
    try:
        nd = NdfDocument.load(fresh_copy(tmp))
        h = nd.history
        attr = next(a for a in av.ATTRS if a.key == "air_deny")

        before = air_distribution(nd)
        n_before = len(nd.decoded["objects"])
        check("打开时没有可撤销的改动", not h.can_undo,
              f"depth={h.depth()}")

        # 只命中 3 个 → 必然克隆（这 126 个营共用同一个模块）
        targets = [p for p in av.build_pawns(nd)
                   if p.values.get("air_deny") == 12.0][:3]
        res = ag_edit.apply_attr(nd, targets, attr, 15.0)
        n_after = len(nd.decoded["objects"])
        check("只命中一部分时逐营克隆", res.cloned == 3 and res.in_place == 0,
              f"cloned={res.cloned} in_place={res.in_place}")
        check("克隆体算进对象表", n_after == n_before + 3,
              f"{n_before} → {n_after}")

        h.commit()
        check("改动记成一步", h.depth() == (1, 0), f"depth={h.depth()}")

        # ★ 这一步曾经抛 ValueError：delta 用 set 并集攒出来，顺序不定，
        #   可能先砍克隆体、再改指向。
        try:
            step = h.undo()
            ok = step is not None
            err = ""
        except Exception as exc:                        # noqa: BLE001
            ok, err = False, repr(exc)
        check("撤销一步「克隆 + 重指向」不抛异常", ok, err)

        check("撤销后对象数回到原样",
              len(nd.decoded["objects"]) == n_before,
              f"{len(nd.decoded['objects'])} vs {n_before}")
        check("撤销后克隆体登记清零", nd.appended_count() == 0,
              str(nd.appended_count()))
        check("撤销后取值分布逐值等于改前", air_distribution(nd) == before,
              f"{sorted(air_distribution(nd).items())}")

        # 撤销之后文件应该回到「一字未改」的状态
        check("撤销后文档不再算脏", nd.dirty_count() == 0,
              str(nd.dirty_count()))

        h.redo()
        check("重做后对象数回来", len(nd.decoded["objects"]) == n_after,
              str(len(nd.decoded["objects"])))
        after = air_distribution(nd)
        check("重做后取值分布恢复", after.get(15.0) == 3 and after.get(12.0) == 123,
              f"{sorted(after.items())}")

        h.undo()
        check("二次撤销仍然干净", air_distribution(nd) == before)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_inplace_undo() -> None:
    """整组命中时走 in-place（0 克隆），撤销只需把模块的值写回去。"""
    from ndfbin_decks.ndfedit import ag_edit
    from ndfbin_decks.ndfedit import ag_view as av

    print("\n=== 2. 数据层：整组命中（in-place）的撤销 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agundo2_"))
    try:
        nd = NdfDocument.load(fresh_copy(tmp))
        h = nd.history
        attr = next(a for a in av.ATTRS if a.key == "air_deny")
        before = air_distribution(nd)
        n_before = len(nd.decoded["objects"])

        every = [p for p in av.build_pawns(nd) if p.values.get("air_deny") == 12.0]
        res = ag_edit.apply_attr(nd, every, attr, 20.0)
        # in_place 数的是**模块**，不是营：126 个营共用 1 个模块，
        # 整组命中时原地改的就是那一个。
        check("整组命中直接改模块，不克隆",
              res.cloned == 0 and res.in_place == 1,
              f"cloned={res.cloned} in_place={res.in_place} 目标 {len(every)}")
        check("126 个营都算改了", res.changed == len(every),
              f"changed={res.changed}")
        h.commit()
        check("对象数不变", len(nd.decoded["objects"]) == n_before)

        h.undo()
        check("in-place 撤销后分布复原", air_distribution(nd) == before,
              f"{sorted(air_distribution(nd).items())}")
        check("in-place 撤销后不脏", nd.dirty_count() == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_multi_step() -> None:
    """两次改动 → 逐级退，不能一步退掉两步。"""
    from ndfbin_decks.ndfedit import ag_edit
    from ndfbin_decks.ndfedit import ag_view as av

    print("\n=== 3. 数据层：两步改动逐级撤销 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agundo3_"))
    try:
        nd = NdfDocument.load(fresh_copy(tmp))
        h = nd.history
        ap = next(a for a in av.ATTRS if a.key == "action_points")
        air = next(a for a in av.ATTRS if a.key == "air_deny")
        v0 = air_distribution(nd)

        pawns = av.build_pawns(nd)
        one = [p for p in pawns if p.values.get("air_deny") == 12.0][:1]
        ag_edit.apply_attr(nd, one, air, 15.0)
        h.commit()

        two = [p for p in av.build_pawns(nd) if p.values.get("air_deny") == 12.0][:1]
        ag_edit.apply_attr(nd, two, ap, 9.0)
        h.commit()
        check("两步各自成一步", h.depth() == (2, 0), f"depth={h.depth()}")

        steps = h.steps()
        check("步骤说明能读", all(steps), f"{steps}")

        h.undo()
        check("退一步：防空半径那步还在",
              air_distribution(nd).get(15.0) == 1,
              f"{sorted(air_distribution(nd).items())}")
        check("退一步后仍未回到原样", air_distribution(nd) != v0)

        h.undo()
        check("退两步回到原样", air_distribution(nd) == v0,
              f"{sorted(air_distribution(nd).items())}")
        check("全退完不脏", nd.dirty_count() == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undo_after_save_clears() -> None:
    """保存是提交点：历史随之清空，退不回保存前。"""
    from ndfbin_decks.ndfedit import ag_edit
    from ndfbin_decks.ndfedit import ag_view as av

    print("\n=== 4. 数据层：撤销的边界是「打开时」，不跨保存 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agundo4_"))
    try:
        dst = fresh_copy(tmp)
        nd = NdfDocument.load(dst)
        attr = next(a for a in av.ATTRS if a.key == "air_deny")
        one = [p for p in av.build_pawns(nd) if p.values.get("air_deny") == 12.0][:1]
        ag_edit.apply_attr(nd, one, attr, 15.0)
        nd.history.commit()
        nd.save()

        nd2 = NdfDocument.load(dst)
        check("保存后重新加载，历史是空的", not nd2.history.can_undo,
              f"depth={nd2.history.depth()}")
        check("保存后的值确实落盘了",
              air_distribution(nd2).get(15.0) == 1,
              f"{sorted(air_distribution(nd2).items())}")
        check("原模块未被波及（仍有 125 个营在用）",
              air_distribution(nd2).get(12.0) == 125,
              f"{sorted(air_distribution(nd2).items())}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 二、界面层 ----


def test_ui_routing() -> None:
    """撤销按钮 / 快捷键 → 主窗口 → 回画这张表。"""
    print("\n=== 5. 界面层：撤销的接线与重画 ===")
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ndfbin_decks.ndfedit.gui import MainWindow
    from ndfbin_decks.ndfedit.query import MATCH_GE

    tmp = Path(tempfile.mkdtemp(prefix="agundo5_"))
    try:
        dst = fresh_copy(tmp)
        app = QApplication.instance() or QApplication([])
        win = MainWindow()
        check("主窗口能加载 Pawn.ndfbin", win.load_path(dst))
        dlg = win.open_ag_editor()
        check("AG 页打开", dlg is not None)
        if dlg is None:
            return

        ap_col = 4 + [a.key for a in ag_view.ATTRS].index("air_deny")

        def shown_values() -> Counter:
            return Counter(dlg.table.item(r, ap_col).text()
                           for r in range(dlg.table.rowCount()))

        # 快捷键：这一页是独立顶层窗口，主窗口的 Ctrl+Z 在这里按不到，
        # 所以 AgPage 必须自己挂一份。
        seqs = {sc.key().toString() for sc in dlg.findChildren(QShortcut)}
        want_undo = QKeySequence(QKeySequence.Undo).toString()
        want_redo = QKeySequence(QKeySequence.Redo).toString()
        check("AgPage 自己挂了撤销键", want_undo in seqs,
              f"想要 {want_undo!r}，实有 {sorted(seqs)}")
        check("AgPage 自己挂了重做键（含 Ctrl+Shift+Z）",
              want_redo in seqs and "Ctrl+Shift+Z" in seqs,
              f"实有 {sorted(seqs)}")

        check("初始撤销按钮是灰的", not dlg.btn_undo.isEnabled())

        # 筛「防空半径 >= 10」→ 123 个营，改成 15
        dlg.add_condition()
        row = dlg._conds[0]
        row.field.setCurrentIndex(row.field.findData("air_deny"))
        row.op.setCurrentIndex(row.op.findData(MATCH_GE))
        row.value.setText("10")
        app.processEvents()
        check("条件筛出 123 个营", dlg.table.rowCount() == 123,
              str(dlg.table.rowCount()))

        dlg.bulk_attr.setCurrentIndex(dlg.bulk_attr.findData("air_deny"))
        dlg.bulk_value.setValue(15.0)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        dlg.on_apply()
        app.processEvents()

        check("批量改后历史有一步", win.history.can_undo,
              f"depth={win.history.depth()}")
        check("批量改后撤销按钮亮起", dlg.btn_undo.isEnabled())
        check("表格显示新值", shown_values().get("15") == 123,
              f"{sorted(shown_values().items())}")

        # ★ 核心：从这一页发撤销请求，表格必须跟着变
        dlg.undoRequested.emit()
        app.processEvents()
        check("撤销后表格回到旧值（不是停在 15）",
              "15" not in shown_values() and shown_values().get("12") == 123,
              f"{sorted(shown_values().items())}")
        check("撤销后模型也回到旧值",
              all(p.values.get("air_deny") != 15.0 for p in dlg._rows),
              f"{sorted(Counter(p.values.get('air_deny') for p in dlg._rows).items())}")
        check("撤销后重做按钮亮起", dlg.btn_redo.isEnabled())
        check("撤销后撤销按钮变灰", not dlg.btn_undo.isEnabled())
        # 「命中 123 个、克隆 123 个」是上一次点应用的账，撤销后它已经
        # 不对应当前状态了，还留着会让人以为没退成功。
        check("撤销后清掉了过时的改动账", dlg.apply_note.text() == "",
              repr(dlg.apply_note.text()))

        dlg.redoRequested.emit()
        app.processEvents()
        check("重做后表格恢复新值", shown_values().get("15") == 123,
              f"{sorted(shown_values().items())}")

        # 主窗口的「刷新全部页面」是独立入口，单独测一次：
        # 它按 refresh 这个名字找页面，找不到就静默跳过。
        win.on_undo()
        app.processEvents()
        check("主窗口自己按撤销，页面也会被重画",
              "15" not in shown_values(),
              f"{sorted(shown_values().items())}")

        # 筛选条件不该被撤销冲掉
        check("撤销之后筛选条件还留着", row.condition() is not None,
              f"field={row.field.currentData()} op={row.op.currentData()}")
        dlg.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if not PAWN.is_file():
        print(f"SKIP — 找不到 {PAWN}")
        return 0
    print(f"样本：{PAWN}")

    test_data_layer()
    test_inplace_undo()
    test_multi_step()
    test_undo_after_save_clears()
    test_ui_routing()

    print()
    if FAIL:
        print(f"FAIL — {len(FAIL)} / {len(PASS) + len(FAIL)} 项未通过：")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
