"""弹药专用页的测试。

用户原话：「制作武器编辑专用页面，尝试实现按字段值筛选对象批量编辑功能
（例如：把对地射程 1925 的 Shell 提升至 2100）」，
入口形态：「首页增加一个专用编辑器下拉框，点击"编辑弹药"按钮进入」。

所以这里既测入口，也测从条件设置到批量落盘的完整链路。
模态框在离屏环境下会永久阻塞，开头统一换成 no-op。
"""

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtCore import Qt                                     # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox           # noqa: E402

from ndfbin_decks.ndfedit import query as Q                       # noqa: E402
from ndfbin_decks.ndfedit.ammo import AMMO_CLASS                  # noqa: E402
from ndfbin_decks.ndfedit.ammo_page import (                      # noqa: E402
    COMMON_FIELDS, SENTINEL, AmmoEditorDialog)
from ndfbin_decks.ndfedit.gui import MainWindow                   # noqa: E402

SRC = _env.gfx()
RANGE = "MaximumRangeGRU"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def set_multi(row, wanted: set):
    """勾选枚举多选里的若干项。"""
    lst = row.value._multi
    for i in range(lst.count()):
        st = (Qt.CheckState.Checked if lst.item(i).text() in wanted
              else Qt.CheckState.Unchecked)
        lst.item(i).setCheckState(st)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    print("\n=== 1. 顶栏入口 ===")
    win = MainWindow()
    win.resize(1500, 860)
    check("有专用编辑器下拉框", hasattr(win, "tool_box"))
    # 断言「有这一项」，不是「只有这一项」—— 专用编辑页会越来越多，
    # 精确相等会让每新增一个页面都误报一次。
    check("下拉框里有「编辑弹药」",
          "编辑弹药" in [win.tool_box.itemText(i)
                         for i in range(win.tool_box.count())],
          str([win.tool_box.itemText(i)
               for i in range(win.tool_box.count())]))
    check("按钮文字跟随下拉框", win.btn_tool.text() == "编辑弹药",
          win.btn_tool.text())

    print("\n=== 2. 未打开文件时给提示 ===")
    check("未打开文件时点它不炸", win.on_open_tool() is None)
    check("仍停留在未打开状态", win.nd is None)

    print("\n=== 3. 打开非弹药文件时的拒绝 ===")
    div = SRC / "Division.ndfbin"
    if div.exists():
        win.load_path(div)
        app.processEvents()
        got = win.open_ammo_editor()
        check("Division.ndfbin 打不开弹药页", got is None)

    print("\n=== 4. 打开弹药页 ===")
    win.load_path(SRC / "Weapon.ndfbin")
    app.processEvents()
    dlg = win.open_ammo_editor()
    app.processEvents()
    check("弹窗建立", isinstance(dlg, AmmoEditorDialog))
    check(f"读到 {len(dlg.entries)} 条弹药",
          len(dlg.entries) == len(win.nd.entries(AMMO_CLASS)),
          str(len(dlg.entries)))
    check("弹窗非模态", not dlg.isModal())
    check("默认有一行条件", len(dlg.rows) == 1, str(len(dlg.rows)))

    print("\n=== 5. 初始状态不误筛 ===")
    check("无条件时提示命中全部",
          "（无条件" in dlg.hit_label.text(), dlg.hit_label.text())
    check("表格铺满全部条目", dlg.table.rowCount() == len(dlg.entries),
          str(dlg.table.rowCount()))
    check("未填的数值框显示提示文字",
          dlg.rows[0].value._single.specialValueText() == "（未填）")
    check("未填的值停在控件下界",
          dlg.rows[0].value._single.value()
          == dlg.rows[0].value._single.minimum())
    check("未填的条件不可用",
          not dlg.current_query().usable(),
          str(dlg.current_query().usable()))
    check("应用按钮初始禁用或全量", dlg.btn_apply.isEnabled())

    print("\n=== 6. 字段排序把玩家认得的排前面 ===")
    head = [dlg.rows[0].field_box.itemText(i) for i in range(1, 8)]
    check("前几个是常用字段",
          sum(1 for t in head
              if t.replace(" ", "") in
              {c.replace("_", "") for c in COMMON_FIELDS}) >= 5,
          str(head))
    check("Projectile Type 排第一", head[0] == "Projectile Type", head[0])

    print("\n=== 7. 匹配方式随字段类型自适应 ===")
    r0 = dlg.rows[0]
    r0.set_field("ProjectileType")
    app.processEvents()
    ops = [r0.op_box.itemText(i) for i in range(r0.op_box.count())]
    check("弹种只给「是其中之一」", ops == ["是其中之一"], str(ops))
    check("弹种值是勾选列表而不是数字框",
          r0.value._multi is not None and r0.value._single is None)
    check("多选项用可读标签",
          [r0.value._multi.item(i).text()
           for i in range(r0.value._multi.count())]
          == ["Shell", "AA Missile", "Rocket", "Missile", "Bomb"],
          str([r0.value._multi.item(i).text()
               for i in range(r0.value._multi.count())]))

    r1 = dlg.add_row()
    app.processEvents()
    r1.set_field("MaximumRangeGRU")
    app.processEvents()
    ops1 = [r1.op_box.itemText(i) for i in range(r1.op_box.count())]
    check("射程给数值比较（含区间）",
          "等于" in ops1 and "在区间内" in ops1, str(ops1))
    check("射程用数字框", r1.value._single is not None)

    r2 = dlg.add_row()
    app.processEvents()
    bool_field = next((n for n, (k, _c) in r0._field_meta.items()
                       if k == "bool"), None)
    check("弹药类里确实有 bool 字段", bool_field is not None, str(bool_field))
    if bool_field:
        r2.set_field(bool_field)
        app.processEvents()
        ops2 = [r2.op_box.itemText(i) for i in range(r2.op_box.count())]
        check(f"bool 字段（{bool_field}）只给等值", ops2 == ["等于"], str(ops2))
        check("bool 字段用数字框或勾选框（不用多选列表）",
              r2.value._multi is None)
    dlg.remove_row(r2)
    app.processEvents()

    print("\n=== 8. 用户原场景：弹种 Shell + 射程 1925 ===")
    set_multi(r0, {"Shell"})
    app.processEvents()
    # 数量按数据现算。写死数字的话，用户在 mod 目录加过一个弹药之后
    # 这个测试就会「失败但其实没问题」——那种误报比没有测试更浪费。
    n_shell = sum(1 for x in dlg.entries
                  if getattr(x.fields.get("ProjectileType"), "value", None) == 1)
    check(f"只按弹种命中 {n_shell}", dlg.table.rowCount() == n_shell,
          str(dlg.table.rowCount()))
    r1.value._single.setValue(1925)
    app.processEvents()
    n_both = sum(1 for x in dlg.entries
                 if getattr(x.fields.get("ProjectileType"), "value", None) == 1
                 and getattr(x.fields.get(RANGE), "value", None) == 1925)
    check(f"加上射程后命中 {n_both}", dlg.table.rowCount() == n_both,
          str(dlg.table.rowCount()))
    check("命中句写出了两个条件",
          "Shell" in dlg.hit_label.text()
          and "1925" in dlg.hit_label.text(), dlg.hit_label.text())
    check("应用按钮带上数量",
          str(n_both) in dlg.btn_apply.text(), dlg.btn_apply.text())

    print("\n=== 9. 结果表 ===")
    cols = [dlg.table.horizontalHeaderItem(i).text()
            for i in range(dlg.table.columnCount())]
    check("名称列在最前", cols[0] == "名称", str(cols))
    check("含筛选用到的字段", "Maximum Range GRU" in cols, str(cols))
    first = [dlg.table.item(0, i).text()
             for i in range(dlg.table.columnCount())]
    check("首行值合理", first[1] == "Shell" and first[3] == "1925",
          str(first))
    check("名称不是裸 id", not first[0].startswith("#"), first[0])

    print("\n=== 10. 中文区间条件 ===")
    r1.op_box.setCurrentIndex(r1.op_box.findData(Q.MATCH_RANGE))
    app.processEvents()
    check("切到区间后出现两个框",
          r1.value._low is not None and r1.value._high is not None)
    r1.value._low.setValue(1800)
    r1.value._high.setValue(2000)
    app.processEvents()
    # 1800~2000 之间只有 1925
    check("区间筛选命中同一批 150 条", dlg.table.rowCount() == 150,
          str(dlg.table.rowCount()))
    r1.value._high.setValue(1900)
    app.processEvents()
    check("收窄区间后命中 0", dlg.table.rowCount() == 0,
          str(dlg.table.rowCount()))
    r1.value._high.setValue(2000)
    app.processEvents()

    print("\n=== 11. 完整批量改链路 ===")
    before = {e.object_id: e.fields["MaximumRangeGRU"].value
              for e in dlg.entries
              if "MaximumRangeGRU" in e.fields}
    i = dlg.bulk_field.findData("MaximumRangeGRU")
    check("批量字段里有射程", i >= 0, str(i))
    dlg.bulk_field.setCurrentIndex(i)
    dlg.bulk_value.setValue(2100)
    app.processEvents()
    check("批量值已填", dlg.bulk_value.value() == 2100)

    dirty_before = win.nd.dirty_count()
    dlg.on_apply()
    app.processEvents()
    # 原本就有 78 条是 2100，改完的 150 条也变成 2100，所以总数是 228
    n2100 = sum(1 for e in dlg.entries
                if e.fields.get("MaximumRangeGRU")
                and e.fields["MaximumRangeGRU"].value == 2100.0)
    check("2100 增至 228 条（78 原有 + 150 改动）", n2100 == 228, str(n2100))
    n1925 = sum(1 for e in dlg.entries
                if e.fields.get("MaximumRangeGRU")
                and e.fields["MaximumRangeGRU"].value == 1925.0)
    check("1925 只剩那 2 条 AA Missile", n1925 == 2, str(n1925))
    check("dirty 计入 150", win.nd.dirty_count() == 150,
          str(win.nd.dirty_count()))
    check("主界面保存按钮已启用", win.btn_save.isEnabled())
    untouched = [oid for oid, v in before.items() if v == 1925.0]
    check("原本 1925 的共 152 条", len(untouched) == 152, str(len(untouched)))
    left = [e for e in dlg.entries
            if e.object_id in untouched
            and e.fields["MaximumRangeGRU"].value == 1925.0]
    check("其余 2 条 AA Missile 未被动", len(left) == 2, str(len(left)))

    print("\n=== 12. 撤销 ===")
    win.nd.reset_all()
    dlg.close()                  # 关窗时主界面会收到通知并刷新
    app.processEvents()
    check("撤销后不脏", win.nd.dirty_count() == 0, str(win.nd.dirty_count()))
    back = [e for e in dlg.entries
            if e.fields.get("MaximumRangeGRU")
            and e.fields["MaximumRangeGRU"].value == 1925.0]
    check("1925 的又回到 152 条", len(back) == 152, str(len(back)))
    check("主界面保存按钮回到禁用", not win.btn_save.isEnabled())

    print("\n=== 13. 换文件时关掉专用页 ===")
    win.load_path(SRC / "Weapon.ndfbin")
    app.processEvents()
    check("换文件后专用页被清理", not win._tool_windows,
          str(list(win._tool_windows)))
    dlg2 = win.open_ammo_editor()
    app.processEvents()
    check("能重新打开",
          dlg2 is not None and len(dlg2.entries) == len(win.nd.entries(AMMO_CLASS)))

    print("\n=== 13.5 两行条件都要看得见（高度自适应） ===")
    win.load_path(SRC / "Weapon.ndfbin")
    app.processEvents()
    dlg3 = win.open_ammo_editor()
    app.processEvents()
    dlg3.resize(1120, 760)
    r0 = dlg3.rows[0]
    r0.set_field("ProjectileType")
    app.processEvents()
    set_multi(r0, {"Shell"})
    app.processEvents()
    r1 = dlg3.add_row()
    app.processEvents()
    r1.set_field(RANGE)
    app.processEvents()
    r1.value._single.setValue(1925)
    dlg3.show()
    app.processEvents()
    vp = dlg3._cond_scroll.viewport().height()
    # 原先只设了 setMaximumHeight，滚动区被布局压到 70px，
    # 第二行整条落在可视区外——截图才发现，功能测试全绿。
    check("滚动区容得下两行条件", vp >= 100, f"viewport={vp}")
    inside = [r.geometry().y() + r.geometry().height() <= vp + 2
              for r in dlg3.rows]
    check("★ 两行条件都完整在可视区内", all(inside), str(inside))
    h_two = dlg3._cond_scroll.height()
    r2 = dlg3.add_row()
    app.processEvents()
    r2.set_field("PhysicalDamages")
    app.processEvents()
    check("加第三行时条件区跟着长高", dlg3._cond_scroll.height() > h_two,
          f"{h_two} -> {dlg3._cond_scroll.height()}")
    dlg3.remove_row(r2)
    app.processEvents()
    check("删掉后缩回去", dlg3._cond_scroll.height() == h_two,
          str(dlg3._cond_scroll.height()))
    dlg3.close()

    print("\n=== 14. 真实落盘 ===")
    work = SRC / "Weapon_bulk.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", work)
    try:
        w2 = MainWindow()
        w2.load_path(work)
        app.processEvents()
        d2 = w2.open_ammo_editor()
        app.processEvents()
        a = d2.rows[0]
        a.set_field("ProjectileType")
        app.processEvents()
        set_multi(a, {"Shell"})
        b = d2.add_row()
        b.set_field("MaximumRangeGRU")
        app.processEvents()
        b.value._single.setValue(1925)
        app.processEvents()
        j = d2.bulk_field.findData("MaximumRangeGRU")
        d2.bulk_field.setCurrentIndex(j)
        d2.bulk_value.setValue(2100)
        d2.on_apply()
        app.processEvents()
        check("落盘前 dirty 150", w2.nd.dirty_count() == 150,
              str(w2.nd.dirty_count()))
        res = w2.nd.save()
        check("保存成功", res.size_after != res.size_before,
              f"{res.size_before}->{res.size_after}")

        from ndfbin_decks.ndfedit.schema import NdfDocument
        nd3 = NdfDocument.load(work)
        rng = Q.value_counts(nd3.entries(AMMO_CLASS), "MaximumRangeGRU")
        check("重读后 1925 只剩 2 条（那两条 AA Missile）",
              rng.get(1925.0, 0) == 2, str(rng.get(1925.0, 0)))
        check("重读后 2100 增至 228 条（78 原有 + 150）",
              rng.get(2100.0, 0) == 228, str(rng.get(2100.0, 0)))
    finally:
        work.unlink(missing_ok=True)
        Path(str(work) + ".bak").unlink(missing_ok=True)

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
