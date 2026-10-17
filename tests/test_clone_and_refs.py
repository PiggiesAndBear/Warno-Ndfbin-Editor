"""OBJREF 两按钮 + 对象钻取窗口 + 克隆对象 + 多选改指向的测试。

两个环境陷阱（都踩过）：
- 离屏下模态框永久阻塞。``QMessageBox.*`` 与 ``RefBulkDialog.exec`` 必须换掉。
- ``ObjectEditorDialog`` 是非模态的，用 ``show()`` 不是 ``exec()``。

克隆这条链路上最关键的一条断言是**落盘读回后 ``is_top_object`` 是否仍为
True**：TOPO 忘了补时，写盘本身不会报错、读回也不报错，只有这个字段会
从 True 变成 False。实测弹药 1548/1548 都在 TOPO 里，所以它是硬指标。
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtCore import Qt                                      # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog,              # noqa: E402
                               QMessageBox)

from ndfbin_decks.core import cndf_codec                           # noqa: E402
from ndfbin_decks.ndfedit.ammo import AMMO_CLASS, AmmoTitles       # noqa: E402
from ndfbin_decks.ndfedit.gui import (ChangePreviewDialog,         # noqa: E402
                                      MainWindow, RefBulkDialog,
                                      RefPickerDialog)
from ndfbin_decks.ndfedit.schema import NdfDocument                # noqa: E402

SRC = _env.gfx()
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def objgraph(nd) -> bytes:
    return cndf_codec.encode_objects(nd.decoded["objects"])


def main() -> int:
    app = _env.qt_app()
    for name in ("critical", "information", "warning", "question"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    ChangePreviewDialog.exec = lambda self: QDialog.Accepted

    if not (SRC / "Weapon.ndfbin").is_file():
        print(f"跳过：找不到 {SRC}")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="ndfclone_"))
    try:
        return run(app, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(app, tmp) -> int:
    work = tmp / "Weapon.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", work)
    win = MainWindow()
    win.resize(1400, 820)
    win.load_path(work)
    app.processEvents()
    nd = win.nd
    AmmoTitles(nd).apply()

    i = next(i for i, c in enumerate(nd.classes) if c.name == AMMO_CLASS)
    win.cls.setCurrentRow(i)
    app.processEvents()
    win.objs.setCurrentRow(0)
    app.processEvents()
    entry = win.objs.entry_at(0)

    # ================================================================
    print("\n=== 1. 引用行是两个按钮 ===")
    name = next(iter(entry.refs))
    long_btn = win.panel._ref_open[name]
    rep_btn = win.panel._ref_buttons[name]
    check("长按钮存在", long_btn is not None)
    check("替换按钮文字是「替换」", rep_btn.text() == "替换", rep_btn.text())
    check("替换按钮是固定窄宽", rep_btn.width() == 58
          or rep_btn.maximumWidth() == 58, str(rep_btn.width()))
    check("长按钮文字以 › 结尾（表示进入）",
          long_btn.text().endswith("\u203a"), repr(long_btn.text()))
    check("长按钮文字含目标名",
          long_btn.text().replace("\u203a", "").strip()
          == __import__("ndfbin_decks.ndfedit.refs", fromlist=["x"]).describe(
              nd.ref, entry.refs[name]),
          repr(long_btn.text()))
    check("长按钮可点", long_btn.isEnabled())
    check("替换按钮 tooltip 说明是换目标",
          "换" in rep_btn.toolTip(), rep_btn.toolTip()[:40])
    check("长按钮 tooltip 说明是进去改字段",
          "进去改" in long_btn.toolTip(), long_btn.toolTip()[:60])

    print("\n--- 1b. 长按钮不发数据改动，只发信号 ---")
    got: list[int] = []
    win.panel.openObjectRequested.connect(got.append)
    before = objgraph(nd)
    long_btn.click()
    app.processEvents()
    check("长按钮发出了 object_id", got == [entry.refs[name].key], str(got))
    check("长按钮没有改数据", objgraph(nd) == before)
    check("长按钮没有让文档变脏", nd.dirty_count() == 0, str(nd.dirty_count()))
    win.panel.openObjectRequested.disconnect(got.append)
    win._close_tool_windows()

    print("\n--- 1c. 三种情形必须禁用长按钮 ---")
    # ① 路径引用：目标是 $/GFX/… 资源路径
    path_ref = None
    for cls_name in nd.class_names:
        for e in nd.entries(cls_name):
            for n2, r2 in e.refs.items():
                if r2.kind == "pathref" and not r2.is_null:
                    path_ref = (e, n2)
                    break
            if path_ref:
                break
        if path_ref:
            break
    if path_ref is None:
        check("样本里有路径引用（跳过）", True)
    else:
        e2, n2 = path_ref
        win.locate_object(e2.object_id)
        app.processEvents()
        b2 = win.panel._ref_open.get(n2)
        check("① 路径引用的长按钮被禁用", b2 is not None and not b2.isEnabled())
        check("① 替换按钮仍然可用",
              win.panel._ref_buttons[n2].isEnabled())
        check("① tooltip 说明是资源路径",
              "资源路径" in b2.toolTip(), b2.toolTip()[:60])

    # ② 空引用：直接把节点置空
    # 注意：上面切过类，``build_for`` 会把表单里的按钮**整批重建**，
    # 所以之前抓的那个控件已经是僵尸了，必须重新取。
    win.locate_object(entry.object_id)
    app.processEvents()
    long_btn = win.panel._ref_open[name]
    rep_btn = win.panel._ref_buttons[name]
    rf = entry.refs[name]
    saved = {k: rf.leaf.get(k) for k in ("object_id", "class_id")}
    rf.leaf["object_id"] = 0xFFFFFFFF
    rf.leaf["class_id"] = 0xFFFFFFFF
    win.panel.show_entry(entry, nd.class_info(AMMO_CLASS))
    app.processEvents()
    check("② 空引用的长按钮被禁用", not long_btn.isEnabled(),
          long_btn.text())
    check("② tooltip 说明还没指向任何东西",
          "还没指向" in long_btn.toolTip(), long_btn.toolTip()[:60])
    check("② 替换按钮仍可用（可以给它挑一个）", rep_btn.isEnabled())

    # ③ 跨文件：目标不在本文件里
    rf.leaf["object_id"] = 999999
    rf.leaf["class_id"] = saved["class_id"]
    win.panel.show_entry(entry, nd.class_info(AMMO_CLASS))
    app.processEvents()
    check("③ 跨文件目标的长按钮被禁用", not long_btn.isEnabled())
    check("③ tooltip 说明目标不在这个文件里",
          "不在当前打开的文件里" in long_btn.toolTip(),
          long_btn.toolTip()[:70])
    rf.leaf["object_id"] = saved["object_id"]
    rf.leaf["class_id"] = saved["class_id"]
    rf.reset()
    win.panel.show_entry(entry, nd.class_info(AMMO_CLASS))
    app.processEvents()
    check("恢复后长按钮又可点", long_btn.isEnabled(), long_btn.text())
    check("恢复后 tooltip 回到「进去改字段」",
          "进去改" in long_btn.toolTip(), long_btn.toolTip()[:50])

    # ================================================================
    print("\n=== 2. 对象钻取窗口 ===")
    win._close_tool_windows()
    win.panel._on_open_ref(name)
    app.processEvents()
    dlg = win._tool_windows.get("object")
    check("点长按钮开出了对象窗口", dlg is not None)
    check("窗口里是新开的", dlg.isVisible() or dlg.isHidden() is False)
    check("栈深 1", len(dlg._stack) == 1, str(len(dlg._stack)))
    check("标题含类名与 object_id",
          dlg._stack[-1][0].class_name in dlg.header.text()
          and f"#{dlg._stack[-1][0].object_id}" in dlg.header.text(),
          dlg.header.text())
    check("只有一层时「返回上一层」禁用", not dlg._btn_back.isEnabled())

    inner = dlg._stack[-1][0]
    drillable = [n for n, r in inner.refs.items()
                 if r.kind == "objref" and not r.is_null
                 and nd.entry_of(r.key) is not None]
    if not drillable:
        check("内层有可继续钻的引用（跳过）", True)
    else:
        n3 = drillable[0]
        check("内层长按钮可点",
              dlg.panel._ref_open[n3].isEnabled())
        dlg.panel._on_open_ref(n3)
        app.processEvents()
        check("★ 内层长按钮是压栈，不是再开一个窗口",
              len(dlg._stack) == 2 and len(win._tool_windows) == 1,
              f"栈={len(dlg._stack)} 窗口={len(win._tool_windows)}")
        check("栈深 2 时「返回上一层」启用", dlg._btn_back.isEnabled())
        check("窗口标题跟着变",
              dlg._stack[-1][0].class_name in dlg.header.text(),
              dlg.header.text())
        crumbs = [dlg.crumb_lay.itemAt(k).widget()
                  for k in range(dlg.crumb_lay.count())
                  if dlg.crumb_lay.itemAt(k).widget() is not None]
        check("面包屑有两节 + 分隔符", len(crumbs) >= 3, str(len(crumbs)))
        check("当前那一节的按钮是禁用的（没什么可点）",
              not crumbs[-1].isEnabled(), crumbs[-1].text())
        dlg.pop()
        app.processEvents()
        check("pop 回退一层", len(dlg._stack) == 1)
        dlg.panel._on_open_ref(n3)
        app.processEvents()
        dlg.pop_to(0)
        app.processEvents()
        check("pop_to(0) 直接回到第一层", len(dlg._stack) == 1)

    print("\n--- 2b. 窗口里的改动回流到主窗口 ---")
    # 直接开在弹药本身上——它有几十个 float 字段，而上面那层引用目标
    # 可能一个数值字段都没有（TDamageTypeRTTI 只有 2 个字段且都是引用）。
    win._close_tool_windows()
    win.on_open_object(entry.object_id)
    app.processEvents()
    dlg = win._tool_windows["object"]
    check("直接开在弹药上", dlg._stack[-1][0].object_id == entry.object_id)
    before_dirty = nd.dirty_count()
    fname = next(n for n, f in dlg._stack[-1][0].fields.items()
                 if f.kind == "float" and not f.added)
    box = dlg.panel._editors[fname]
    box.setValue(float(box.value()) + 3.0)
    app.processEvents()
    check("窗口里改值让文档变脏", nd.dirty_count() >= before_dirty)
    check("主窗口状态栏跟着更新", "已改动" in win.status.text(),
          win.status.text()[:50])
    check("撤销栈记到了这一步", win.history.depth()[0] >= 1,
          str(win.history.depth()))

    print("\n--- 2c. 撤销后窗口要重画 ---")
    val_at_clone = None
    win.on_undo()
    app.processEvents()
    check("撤销后窗口里的格子回到原值",
          abs(float(dlg.panel._editors[fname].value())
              - float(dlg._stack[-1][0].fields[fname].original)) < 1e-6,
          str(dlg.panel._editors[fname].value()))

    print("\n--- 2d. 在主界面定位 ---")
    target_id = dlg._stack[-1][0].object_id
    dlg._on_locate()
    app.processEvents()
    cur = win.objs.entry_at(win.objs.currentRow())
    check("主界面切到了那个对象",
          cur is not None and cur.object_id == target_id,
          f"{cur.object_id if cur else None} vs {target_id}")
    dlg.close()
    win._close_tool_windows()

    # ================================================================
    print("\n=== 3. 克隆对象：内核 ===")
    win.locate_object(entry.object_id)
    app.processEvents()
    n0 = len(nd.decoded["objects"])
    topo0 = len(nd._topo)
    ammo0 = len(nd.entries(AMMO_CLASS))
    base = objgraph(nd)
    src = nd.entry_of(entry.object_id)

    def guid_of(obj):
        return next((p["value"]["value_hex"] for p in obj["properties"]
                     if p["property_name"] == "DescriptorId"), None)

    src_guid = guid_of(nd._object_by_id(src.object_id))

    win.history.break_merge()
    clone = nd.clone_object(src)
    nd.ensure_titles(AMMO_CLASS)
    win._on_entry_dirty()          # 走真实信号链路：提交历史 + 刷界面
    app.processEvents()

    check("对象数 +1", len(nd.decoded["objects"]) == n0 + 1,
          str(len(nd.decoded["objects"])))
    check("新对象 id 是原来的末尾", clone.object_id == n0, str(clone.object_id))
    check("类相同", clone.class_name == src.class_name)
    check("字段集与原件一致", set(clone.fields) == set(src.fields))
    check("引用集与原件一致", set(clone.refs) == set(src.refs))
    check("★ TOPO 也跟着涨了（现成追加器不做这件事）",
          len(nd._topo) == topo0 + 1, f"{topo0} -> {len(nd._topo)}")
    check("★ 新对象是顶层对象",
          nd._object_by_id(clone.object_id)["is_top_object"])
    cobj = nd._object_by_id(clone.object_id)
    check("★ GUID 换成了新的", guid_of(cobj) != src_guid,
          f"{src_guid[:8]}… -> {guid_of(cobj)[:8]}…")
    check("类计数 +1",
          next(c for c in nd.classes if c.name == AMMO_CLASS).count == ammo0 + 1,
          str(next(c for c in nd.classes if c.name == AMMO_CLASS).count))
    check("克隆记进了历史", win.history.depth()[0] >= 1,
          str(win.history.depth()))
    check("★ 克隆体没有任何脏字段，但保存按钮必须可用（它本身也是未保存改动）",
          win.btn_save.isEnabled(), f"dirty_count={nd.dirty_count()}")
    check("状态栏如实说新增了几个对象",
          "新增 1 个对象" in win.status.text(), win.status.text()[:70])

    print("\n--- 3b. 编码往返：TOPO 补没补只有重新解码才看得出来 ---")
    # **不写工作文件**。写盘会让 ``doc.raw_data`` 变成过期快照，后面每一次
    # ``save()`` 都会被过期保护挡住（那是它正常工作，但会打断这条用例）。
    # 所以往返写到旁边一个文件上。
    from ndfbin_decks.core import writer
    out = writer.write_bytes(nd.doc, nd.replacements())
    trip = tmp / "roundtrip.ndfbin"
    trip.write_bytes(out)
    re = NdfDocument.load(trip)
    objs = re.decoded["objects"]
    check("读回对象数一致", len(objs) == n0 + 1, str(len(objs)))
    check("★ 读回后新对象 is_top_object 仍为 True",
          objs[clone.object_id]["is_top_object"])
    check("读回后 TOPO 长度一致",
          len(re._topo) == len(nd._topo), str(len(re._topo)))
    check("读回后类相同", objs[clone.object_id]["class"] == src.class_name)
    check("读回后 GUID 保持",
          guid_of(objs[clone.object_id]) == guid_of(cobj))
    check("读回后属性数一致",
          len(objs[clone.object_id]["properties"]) == len(cobj["properties"]))
    check("原文档的写盘快照没被弄脏（dry_run 走完整条写盘链路）",
          nd.dry_run().size_after > 0)

    print("\n--- 3c. 撤销 / 重做克隆 ---")
    win.on_undo()
    app.processEvents()
    check("撤销克隆后对象数回到原样",
          len(nd.decoded["objects"]) == n0,
          str(len(nd.decoded["objects"])))
    check("撤销克隆后 TOPO 也回去", len(nd._topo) == topo0, str(len(nd._topo)))
    check("撤销克隆后 appended=0", nd.appended_count() == 0)
    check("撤销克隆后类计数回去",
          next(c for c in nd.classes if c.name == AMMO_CLASS).count == ammo0)
    check("★ 撤销克隆后对象图与打开时逐字节相同", objgraph(nd) == base)
    win.on_redo()
    app.processEvents()
    check("重做克隆后对象数又 +1",
          len(nd.decoded["objects"]) == n0 + 1,
          str(len(nd.decoded["objects"])))
    check("重做能进到新对象去（条目在活数据里）",
          nd.entry_of(clone.object_id) is not None)

    print("\n--- 3d. 改克隆体自己的字段也能撤销 ---")
    c2 = nd.entry_of(clone.object_id)
    fname2 = next(n for n, f in c2.fields.items()
                  if f.kind == "float" and not f.added)
    orig2 = c2.fields[fname2].value
    c2.fields[fname2].leaf["value"] = float(orig2) + 11.0
    win._on_entry_dirty()
    check("改克隆体记进了历史", win.history.depth()[0] >= 2,
          str(win.history.depth()))
    win.on_undo()
    app.processEvents()
    check("★ 撤销后克隆体的值回到原样",
          nd.entry_of(clone.object_id).fields[fname2].value == orig2,
          str(nd.entry_of(clone.object_id).fields[fname2].value))

    print("\n--- 3e. 守卫：还有引用指着它时不许撤销克隆 ---")
    node = nd.referrers_for_class(AMMO_CLASS)[0][3]
    keep = {k: node.get(k) for k in ("object_id", "class_id")}
    nd.ref.repoint(node, "obj", clone.object_id)
    try:
        nd.set_appended_count(0)
        check("★ 有引用时拒绝截断", False, "竟然通过了")
    except ValueError as exc:
        check("★ 有引用时拒绝截断", True)
        check("拒绝了并且说清了原因",
              "引用" in str(exc) and "撤销克隆" in str(exc), str(exc)[:70])
    check("拒绝后对象数没变", len(nd.decoded["objects"]) == n0 + 1)
    # 这个 repoint 是刻意绕过历史做的，得手动还原，否则「全退后与打开时
    # 相同」这条就无从成立。
    node["object_id"] = keep["object_id"]
    node["class_id"] = keep["class_id"]
    while win.history.can_undo:
        win.on_undo()
    app.processEvents()
    check("全退之后对象图与打开时逐字节相同", objgraph(nd) == base)
    check("全退之后写盘是真正的 no-op",
          nd.save().size_before == nd.save().size_after,
          str(nd.save().size_after))

    # ================================================================
    print("\n=== 4. 多选改指向窗口 ===")
    base = objgraph(nd)
    n0 = len(nd.decoded["objects"])
    win.history.break_merge()
    clone = nd.clone_object(entry)
    nd.ensure_titles(AMMO_CLASS)
    win._on_entry_dirty()
    app.processEvents()
    check("★ 前面撤销掉的克隆体没有跟着复活（对象数只 +1）",
          len(nd.decoded["objects"]) == n0 + 1,
          f"{n0} -> {len(nd.decoded['objects'])}")

    cands = nd.referrers_for_class(AMMO_CLASS)
    check("候选实例数与实测一致（9001）", len(cands) == 9001, str(len(cands)))
    check("候选里带节点（改指向不必再扫一遍）",
          all(len(c) == 4 and isinstance(c[3], dict) for c in cands[:50]))
    check("候选按类判定，覆盖两个字段",
          {c[1] for c in cands} == {"Ammunition", "AmmunitionForBurn"},
          str({c[1] for c in cands}))

    dlg2 = RefBulkDialog(nd, clone, entry, nd.last_clone_guid_fields, win)
    app.processEvents()
    check("★ 全部候选都列出来了（不收敛）",
          dlg2.table.rowCount() == 9001, str(dlg2.table.rowCount()))
    check("表格有 3 列",
          dlg2.table.columnCount() == 3, str(dlg2.table.columnCount()))
    to_src = [c for c in dlg2._all if c.to_source]
    check("★ 默认勾选的就是「现在指向原件」的那些",
          len(dlg2._checked()) == len(to_src) and len(to_src) >= 1,
          f"勾了 {len(dlg2._checked())}，指向原件 {len(to_src)}")
    check("指向原件的排在列表最前",
          all(dlg2._all[k].to_source for k in range(len(to_src))),
          str([dlg2._all[k].to_source for k in range(min(4, len(to_src)))]))
    check("计数行把两个数都说了",
          str(len(to_src)) in dlg2.count.text()
          and "9001" in dlg2.count.text(), dlg2.count.text())
    check("按钮文字带勾选数",
          "确认改指向" in dlg2.btn_apply.text(), dlg2.btn_apply.text())

    print("\n--- 4b. 搜索 / 全选 / 反选 ---")
    dlg2.search.setText("AmmunitionForBurn")
    app.processEvents()
    check("搜索把列表收窄了", dlg2.table.rowCount() == 6,
          str(dlg2.table.rowCount()))
    check("搜索后计数行显示收窄后的条数",
          "列表 6 / 9001" in dlg2.count.text(), dlg2.count.text())
    dlg2.search.setText("")
    app.processEvents()
    dlg2._set_all(True)
    app.processEvents()
    check("全选 → 勾了 9001 条", len(dlg2._checked()) == 9001,
          str(len(dlg2._checked())))
    dlg2._set_all(False)
    app.processEvents()
    check("全不选 → 0 条", len(dlg2._checked()) == 0)
    check("一条没勾时应用按钮禁用", not dlg2.btn_apply.isEnabled())
    dlg2._invert()
    app.processEvents()
    check("反选 → 9001 条（原来 0）", len(dlg2._checked()) == 9001,
          str(len(dlg2._checked())))

    print("\n--- 4c. 只勾指向原件的那几处，应用 ---")
    dlg2._set_all(False)
    for k in range(dlg2.table.rowCount()):
        if dlg2._shown[k].to_source:
            dlg2.table.item(k, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    n_pick = len(dlg2._checked())
    dlg2.dirtyApplied.connect(win._on_entry_dirty)
    dlg2.on_apply()
    app.processEvents()
    check("改指向生效", sum(1 for c in nd.referrers_for_class(AMMO_CLASS)
                            if c[2] == clone.object_id) == n_pick,
          str(n_pick))
    check("★ 合并成一步（不是 N 步）",
          win.history.depth()[0] == 2, str(win.history.depth()))
    check("状态栏说改了引用目标",
          "改了引用目标" in win.status.text(), win.status.text()[:60])
    check("这次改动没有动对象数", len(nd.decoded["objects"]) == n0 + 1)

    print("\n--- 4d. 一次 Ctrl+Z 把 n 处一起退回 ---")
    win.on_undo()
    app.processEvents()
    check("★ 一步撤销全部退回",
          sum(1 for c in nd.referrers_for_class(AMMO_CLASS)
              if c[2] == clone.object_id) == 0)
    win.on_undo()
    app.processEvents()
    check("再退一步退回克隆", len(nd.decoded["objects"]) == n0,
          str(len(nd.decoded["objects"])))
    check("★ 全退后对象图与打开时逐字节相同", objgraph(nd) == base)

    print("\n--- 4e. 没有指向原件的对象时怎么办 ---")
    orphan = None
    for e in nd.entries(AMMO_CLASS):
        if len(nd.referrers_for_class(AMMO_CLASS)) and not any(
                c[2] == e.object_id
                for c in nd.referrers_for_class(AMMO_CLASS)):
            orphan = e
            break
    if orphan is None:
        check("能找到一个无人引用的弹药（跳过）", True)
    else:
        win.history.break_merge()
        c3 = nd.clone_object(orphan)
        win._on_entry_dirty()
        d3 = RefBulkDialog(nd, c3, orphan, [], win)
        app.processEvents()
        check("列表仍然全量列出", d3.table.rowCount() == 9001,
              str(d3.table.rowCount()))
        check("没有指向原件的，所以一条也没默认勾",
              len(d3._checked()) == 0, str(len(d3._checked())))
        check("计数行说明这个情况",
              "0 条" in d3.count.text(), d3.count.text())
        d3.reject()

    # ================================================================
    print("\n" + "=" * 54)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：")
        for n in FAIL:
            print(f"  - {n}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
