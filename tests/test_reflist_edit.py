"""「一串引用」的增删与查看。

早先这个界面**只允许换目标、不允许增删**，理由是「改 ``length`` 要连带改
``CHNK`` 计数、重新编号后续对象」。实测那条理由**不成立**：列表元素是
*引用*，增删只改变宿主对象自己的 OBJE 字节，对象编号与 CHNK 计数都不动；
编码器只校验 ``len(items) == length``（``tools/probe_listadd.py`` 跑通了
删一项再加两项的完整往返）。

本文件覆盖放开之后的行为，重点是三件容易做错的事：

1. **新项的 ``class_id`` 必须对** —— 手填一个错的会直接触发
   ``Unresolved CNDF object/class reference``，而写盘时不会报错
2. **``length`` 必须跟着项数走** —— 不一致编码器直接抛
3. **撤销要能退回长度** —— 增删没法逐项回退，状态只能是整条列表
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from ndfbin_decks.ndfedit.schema import NdfDocument       # noqa: E402

SRC = _env.gfx()
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond, extra="") -> bool:
    (PASS if cond else FAIL).append(label)
    mark = "OK  " if cond else "FAIL"
    print(f"  {mark} {label}" + (f"   [{extra}]" if extra != "" else ""))
    return bool(cond)


def sep(t: str) -> None:
    print(f"\n--- {t} ---")


def find_owner(nd, min_len: int = 2):
    """找一个项数足够、且**目标类唯一、每项都非空**的对象引用列表。

    **不写死 object_id**：用户的 mod 目录随时在改，写死的编号迟早会变成
    「失败但其实没问题」。挑单类是因为「加一项」要先知道往里面放什么类——
    混类的列表（``ModulesDescriptors`` 那种）走的是另一条路（先选类）。
    """
    for entries in nd._by_class.values():
        for e in entries:
            for name, lf in e.ref_lists.items():
                if (lf.length >= min_len and lf.uniform_class()
                        and all(r.key is not None for r in lf.items)):
                    return e, name, lf
    return None, "", None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="test_rle_"))
    work = tmp / "Weapon.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", work)
    try:
        _run(work)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for name in FAIL:
        print(f"  - {name}")
    return 1 if FAIL else 0


def _run(work: Path) -> None:
    nd = NdfDocument.load(work)
    e, fname, lf = find_owner(nd)
    if lf is None:
        check("找得到一个可测的「一串引用」", False)
        return
    print(f"样本 #{e.object_id} {e.class_name} .{fname} 长度 {lf.length}")
    tgt = lf.items[0].key

    # ==================================================================
    sep("1. 初始状态")
    # ==================================================================
    n0 = lf.length
    check("长度与项数一致", lf.length == len(lf.leaf["items"]))
    check("初始不脏", not lf.dirty)
    check("uniform_class 认得目标类", bool(lf.uniform_class()),
          lf.uniform_class())
    check("每一项的 target_class 与 uniform_class 一致",
          all(r.target_class == lf.uniform_class() for r in lf.items))

    # ==================================================================
    sep("2. 加一项")
    # ==================================================================
    rf = lf.add("obj", tgt)
    check("长度 +1", lf.length == n0 + 1, f"{n0} -> {lf.length}")
    check("★ length 跟着改了（编码器会校验）",
          lf.leaf["length"] == len(lf.leaf["items"]))
    check("现在脏了", lf.dirty)
    check("★ dirty_indices 指到新加的那一项",
          lf.dirty_indices() == [n0], str(lf.dirty_indices()))
    check("新项的目标就是选的那个", rf.key == tgt)
    check("★ 新项带上了 type_id（少了它就不被认作引用）",
          rf.leaf.get("type_id") == refs_type_id(), str(rf.leaf.get("type_id")))
    check("★ 新项的 class_id 是对的",
          rf.leaf.get("class_id") == nd.ref._class_id_of(lf.uniform_class()),
          f"{rf.leaf.get('class_id')} vs "
          f"{nd.ref._class_id_of(lf.uniform_class())}")
    check("新项出现在 items 里（界面读的是它）",
          len(lf.items) == lf.length)

    # ==================================================================
    sep("3. 删一项")
    # ==================================================================
    lf.remove(n0)                      # 删掉刚加的那个
    check("长度回到原值", lf.length == n0, str(lf.length))
    check("★ length 同步", lf.leaf["length"] == len(lf.leaf["items"]))
    check("删干净后不脏", not lf.dirty, str(lf.dirty_indices()))
    lf.remove(0)
    check("删首项后长度 -1", lf.length == n0 - 1, str(lf.length))
    check("删首项后仍然脏", lf.dirty)
    check("★ 越界删除被忽略（不炸）",
          (lf.remove(999), lf.length == n0 - 1)[1])
    lf.reset()
    check("reset 把长度也复原", lf.length == n0 and not lf.dirty)

    # ==================================================================
    sep("4. 移动")
    # ==================================================================
    first2 = [r.key for r in lf.items[:2]]
    j = lf.move(0, 1)
    check("下移返回新位置", j == 1, str(j))
    check("★ 两项互换了", [r.key for r in lf.items[:2]] == first2[::-1],
          str([r.key for r in lf.items[:2]]))
    check("移动也算改动", lf.dirty)
    lf.reset()
    check("reset 复原顺序", [r.key for r in lf.items[:2]] == first2)
    check("★ 越界移动返回原位置", lf.move(0, -1) == 0)

    # ==================================================================
    sep("5. 撤销栈：增删各自成步、能退回长度")
    # ==================================================================
    h = nd.history
    h.commit()
    lf.add("obj", tgt)
    h.commit()
    check("加一项记了一步", h.depth()[0] == 1, str(h.depth()))
    check("状态栏计数认得改了几项", h.stats().lists >= 1,
          str(h.stats().lists))
    # 连着再加一项会被**合并**进上一步——键集相同（都是同一个列表）且在
    # 合并窗口内。这和数值字段连点方向键是同一套规则：玩家想一次退掉
    # 刚才那几下。要走独立的一步就先 break_merge。
    h.break_merge()
    lf.add("obj", tgt)
    h.commit()
    check("★ break_merge 之后是独立的一步", h.depth()[0] == 2, str(h.depth()))
    check("长度 = 原值 + 2", lf.length == n0 + 2, str(lf.length))
    h.undo()
    check("★ 退一步：长度 -1", lf.length == n0 + 1, str(lf.length))
    h.undo()
    check("★ 退两步：长度回到原值", lf.length == n0, str(lf.length))
    check("★ 退到底不脏", not lf.dirty)
    check("★ 退到底后节点与打开时逐个相同",
          lf.nodes() == lf.original)
    h.redo()
    h.redo()
    check("★ 重做两步：长度又回到 +2", lf.length == n0 + 2, str(lf.length))
    while h.can_undo:
        h.undo()
    check("再退到底：长度复原", lf.length == n0, str(lf.length))

    # 删除也能撤销
    h.commit()
    removed_key = lf.items[0].key
    lf.remove(0)
    h.commit()
    check("删一项记了一步", lf.length == n0 - 1, str(lf.length))
    h.undo()
    check("★ 撤销删除把项放回来了（含位置）",
          lf.length == n0 and lf.items[0].key == removed_key,
          f"{lf.length} / {lf.items[0].key if lf.items else None}")
    while h.can_undo:
        h.undo()

    # ==================================================================
    sep("5a. 连续增删会被合并（与数值字段连点同一套规则）")
    # ==================================================================
    while h.can_undo:
        h.undo()
    h.commit()
    lf.add("obj", tgt)
    h.commit()
    lf.add("obj", tgt)
    h.commit()
    check("★ 窗口内连加两项合并成一步", h.depth()[0] == 1, str(h.depth()))
    check("长度确实 +2", lf.length == n0 + 2, str(lf.length))
    h.undo()
    check("★ 一次就把两项都退掉", lf.length == n0, str(lf.length))
    while h.can_undo:
        h.undo()

    # ==================================================================
    sep("6. 落盘读回")
    # ==================================================================
    h.commit()
    lf.add("obj", tgt)
    h.commit()
    expected = lf.length
    res = nd.save()
    check("写盘成功", res.size_after > 0,
          f"{res.size_before} -> {res.size_after}")
    re_nd = NdfDocument.load(work)
    re_e = re_nd.entry_of(e.object_id)
    re_lf = re_e.ref_lists[fname]
    check("★ 读回长度一致", re_lf.length == expected,
          f"{re_lf.length} vs {expected}")
    check("★ 读回后 length 与项数也一致",
          re_lf.leaf["length"] == len(re_lf.leaf["items"]))
    check("★ 读回后不脏（写进去的和读出来的一样）", not re_lf.dirty,
          str(re_lf.dirty_indices()))

    # ==================================================================
    sep("7. 界面：对话框、增删按钮、查看")
    # ==================================================================
    from PySide6.QtWidgets import QApplication, QPushButton
    from ndfbin_decks.ndfedit.gui import MainWindow
    app = _env.qt_app()
    tmp2 = Path(tempfile.mkdtemp(prefix="test_rle_ui_"))
    work2 = tmp2 / "Weapon.ndfbin"
    shutil.copy2(work, work2)
    try:
        win = MainWindow()
        win.resize(1400, 860)
        win.show()
        app.processEvents()
        win.load_path(work2)
        app.processEvents()
        win.locate_object(e.object_id)
        app.processEvents()
        p = win.panel
        check("字段面板上有这个列表的按钮", fname in p._list_buttons,
              str(sorted(p._list_buttons)))
        check("按钮文字写出了项数", str(p._list_buttons[fname].text()).startswith(
            str(re_lf.length)) if p._list_buttons.get(fname) else False,
            p._list_buttons[fname].text() if fname in p._list_buttons else "")
        # 走真实信号链路打开
        p._on_open_list(fname)
        app.processEvents()
        dlg = win._tool_windows.get("reflist")
        check("列表编辑器作为工具窗口建起来了", dlg is not None)
        if dlg is not None:
            check("窗口指向正确的字段", dlg._field_name == fname)
            check("行数与项数一致", dlg.listw.count() == dlg.field.length,
                  f"{dlg.listw.count()} vs {dlg.field.length}")
            row0 = dlg.listw.itemWidget(dlg.listw.item(0))
            texts = [b.text() for b in row0.findChildren(QPushButton)]
            check("★ 每行有查看 / 换 / 删", "查看" in texts and "换…" in texts
                  and "删" in texts, str(texts))
            check("★ 每行有上移下移", "↑" in texts and "↓" in texts, str(texts))
            check("有添加按钮", dlg.btn_add.text().startswith("+"),
                  dlg.btn_add.text())
            # 增一项
            n_before = dlg.field.length
            dlg.field.add("obj", dlg.field.items[0].key)
            dlg._notify()
            app.processEvents()
            check("★ 加完行数跟着长", dlg.listw.count() == n_before + 1,
                  f"{dlg.listw.count()} vs {n_before + 1}")
            check("计数文字更新了", str(n_before + 1) in dlg.count.text(),
                  dlg.count.text())
            # 删一项
            dlg.field.remove(0)
            dlg._notify()
            app.processEvents()
            check("★ 删完行数跟着缩", dlg.listw.count() == n_before,
                  str(dlg.listw.count()))
            # 查看：应当打开对象钻取窗口
            got = []
            dlg.openObjectRequested.connect(lambda oid: got.append(oid))
            dlg._view(0)
            app.processEvents()
            check("★ 「查看」发出了目标 object_id",
                  got and got[0] == dlg.field.items[0].key, str(got))
            check("★ 对象钻取窗口真的开了", "object" in win._tool_windows,
                  str(sorted(win._tool_windows)))
            # 撤销之后列表要能刷新
            while win.history.can_undo:
                win.on_undo()
            app.processEvents()
            check("★ 撤销后列表窗口跟着刷新",
                  dlg.listw.count() == dlg.field.length,
                  f"{dlg.listw.count()} vs {dlg.field.length}")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)


def refs_type_id() -> int:
    from ndfbin_decks.ndfedit import refs
    return refs.OBJECT_REFERENCE


if __name__ == "__main__":
    raise SystemExit(main())
