"""离屏渲染通用编辑器的界面截图。

    python tools/screenshot_ndfedit.py
输出：
    doc/ui截图-通用.png        弹药视图
    doc/ui截图-炮塔.png        通用类视图
    doc/ui截图-实体视图.png     TEntityDescriptor 聚合面板（血量/装甲/速度）
    doc/ui截图-引用选择.png     给「装甲」这种引用字段挑目标
    doc/ui截图-列表引用.png     给「一串引用」逐项换目标（Deck 的载具槽位）
    doc/ui截图-Mod浏览器.png    先选 Mod、再选它里面的 ndfbin
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from PySide6.QtWidgets import QApplication

from ndfbin_decks.ndfedit import entity, refs
from ndfbin_decks.ndfedit.ammo import AMMO_CLASS, AmmoTitles
from ndfbin_decks.ndfedit.gui import (ChangePreviewDialog, HashPickerDialog,
                                      MainWindow, RefBulkDialog,
                                      RefListDialog, RefPickerDialog,
                                      StrListDialog, StrPickerDialog,
                                      _pick_cjk_font)

SRC = _src.gfx()
MOD = SRC
OUT = Path(__file__).resolve().parent.parent / "doc" / "ui截图-通用.png"


def ensure_font(app):
    if _pick_cjk_font(app):
        return
    from PySide6.QtGui import QFont, QFontDatabase
    for f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if Path(f).exists():
            fid = QFontDatabase.addApplicationFont(f)
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                app.setFont(QFont(fams[0], 9))
                print("从文件加载中文字体：", fams[0])
                return


def shoot(app, win, filename, note):
    app.processEvents()
    out = OUT.with_name(filename)
    win.grab().save(str(out))
    print(f"{note} -> {out}")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    ensure_font(app)

    work = SRC / "Weapon_shot.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", work)

    win = MainWindow()
    win.resize(1400, 760)
    win.load_path(work)
    app.processEvents()

    # 选弹药类
    idx = next(i for i, c in enumerate(win.nd.classes)
               if c.name == "TAmmunitionDescriptor")
    win.cls.setCurrentRow(idx)
    app.processEvents()

    # 选中一条被大量装备共用的
    best, best_row = None, 0
    for i, e in enumerate(win.objs._entries):
        if best is None or e.ref_count > best.ref_count:
            best, best_row = e, i
    win.objs.setCurrentRow(best_row)
    app.processEvents()

    # 制造一处改动。注意不要给 entry.title 赋值——应用已经通过
    # ensure_titles() 填好了弹药标题（弹种 · GUID），脚本覆盖会让截图失真。
    entry = win.objs.entry_at(best_row)
    if entry and "PhysicalDamages" in entry.fields:
        entry.fields["PhysicalDamages"].leaf["value"] = 88.0
        win.panel.show_entry(entry, win.nd.class_info("TAmmunitionDescriptor"))
        win._on_entry_dirty()
    app.processEvents()
    shoot(app, win, "ui截图-通用.png",
          f"弹药视图（{entry.title if entry else '-'}，"
          f"{entry.ref_count if entry else 0} 装备共用）")

    # 第二张：通用类视图（证明不局限于弹药）
    win2 = MainWindow()
    win2.resize(1400, 760)
    win2.load_path(work)
    app.processEvents()
    idx2 = next(i for i, c in enumerate(win2.nd.classes)
                if c.name == "TTurretTwoAxisDescriptor")
    win2.cls.setCurrentRow(idx2)
    app.processEvents()
    win2.objs.setCurrentRow(0)
    app.processEvents()
    shoot(app, win2, "ui截图-炮塔.png", "炮塔类（通用视图）")

    # 第三、四张：实体聚合视图 + 引用选择（Unit.ndfbin）
    unit_work = SRC / "Unit_shot.ndfbin"
    shutil.copy2(SRC / "Unit.ndfbin", unit_work)
    try:
        win3 = MainWindow()
        win3.resize(1400, 820)
        win3.load_path(unit_work)
        app.processEvents()
        idx3 = next(i for i, c in enumerate(win3.nd.classes)
                    if c.name == "TEntityDescriptor")
        win3.cls.setCurrentRow(idx3)
        app.processEvents()
        # 挑一个真坦克：有 export 名、有血量、有装甲、有速度
        nd3 = win3.nd
        shared = win3.panel._shared_lookup
        row3, view3 = 0, None
        for i, en in enumerate(win3.objs._entries):
            if not nd3.ref.label_of(en.object_id).startswith("Descriptor_Unit_"):
                continue
            v = entity.build_entity_view(nd3, en, shared_lookup=shared)
            names = {r.name for r in v.rows}
            if ({"MaxPhysicalDamages", "BlindageProperties"} <= names
                    and any("Speed" in n for n in names)):
                row3, view3 = i, v
                break
        win3.objs.setCurrentRow(row3)
        app.processEvents()
        # 打开「显示全部模块字段」，让截图里能看到分组是分层的
        win3.panel.entity_all.setChecked(True)
        app.processEvents()
        shoot(app, win3, "ui截图-实体视图.png",
              f"实体聚合视图（{view3.title if view3 else '-'}，"
              f"{view3.summary() if view3 else ''}）")

        # 引用选择对话框：装甲等级
        blind = next(i for i, c in enumerate(win3.nd.classes)
                     if c.name == "TBlindageProperties")
        win3.cls.setCurrentRow(blind)
        app.processEvents()
        win3.objs.setCurrentRow(0)
        app.processEvents()
        rf = win3.objs.entry_at(0).refs.get("ResistanceFront")
        if rf is not None:
            dlg = RefPickerDialog(win3, win3.nd.ref, rf,
                                  owner_class="TBlindageProperties")
            dlg.resize(860, 620)
            dlg.show()
            app.processEvents()
            dlg.grab().save(str(OUT.with_name("ui截图-引用选择.png")))
            print("引用选择对话框 ->", OUT.with_name("ui截图-引用选择.png"))
            dlg.close()
    finally:
        unit_work.unlink(missing_ok=True)
        Path(str(unit_work) + ".bak").unlink(missing_ok=True)

    work.unlink(missing_ok=True)
    Path(str(work) + ".bak").unlink(missing_ok=True)

    # 第五张：列表型引用（Deck 的载具槽位）
    deck_work = SRC / "Deck_shot.ndfbin"
    shutil.copy2(SRC / "Deck.ndfbin", deck_work)
    try:
        win4 = MainWindow()
        win4.resize(1400, 820)
        win4.load_path(deck_work)
        app.processEvents()
        di = next(i for i, c in enumerate(win4.nd.classes)
                  if c.name == "TDeckDescriptor")
        win4.cls.setCurrentRow(di)
        app.processEvents()
        drow = next(i for i, en in enumerate(win4.objs._entries)
                    if en.ref_lists.get("DeckPackList")
                    and en.ref_lists["DeckPackList"].length >= 20)
        win4.objs.setCurrentRow(drow)
        app.processEvents()
        lf = win4.objs.entry_at(drow).ref_lists["DeckPackList"]
        # 制造一处改动，让截图里能看到高亮
        if len(lf.items) > 3:
            alt = next(c for c in win4.nd.ref.candidates_for_class(
                "TDeckPackDescriptor") if c.key != lf.items[3].key)
            win4.nd.ref.repoint(lf.items[3].leaf, "obj", alt.key)
            win4.panel.show_entry(win4.objs.entry_at(drow),
                                  win4.nd.class_info("TDeckDescriptor"))
            win4._on_entry_dirty()
        app.processEvents()
        shoot(app, win4, "ui截图-面板-列表引用.png",
              f"字段面板（{lf.length} 项的一串引用）")

        ldlg = RefListDialog(win4.nd, win4)
        ldlg.show_field(win4.objs.entry_at(drow), "DeckPackList")
        ldlg.resize(920, 660)
        ldlg.show()
        app.processEvents()
        ldlg.grab().save(str(OUT.with_name("ui截图-列表引用.png")))
        print("列表引用对话框 ->", OUT.with_name("ui截图-列表引用.png"))
        ldlg.close()
    finally:
        deck_work.unlink(missing_ok=True)
        Path(str(deck_work) + ".bak").unlink(missing_ok=True)

    # 第六张：Mod 浏览器（先选 Mod，再选文件）
    from PySide6.QtCore import Qt
    from ndfbin_decks.ndfedit.modpicker import ModBrowserDialog, scan_ndfbin
    mb = ModBrowserDialog(None)
    if mb.mods.count():
        counts = [(i, len(scan_ndfbin(mb.mods.item(i).data(Qt.UserRole))))
                  for i in range(mb.mods.count())]
        best = max(counts, key=lambda x: x[1])[0]
        mb.mods.setCurrentRow(best)
        app.processEvents()
        mb.resize(1020, 680)
        mb.show()
        app.processEvents()
        mb.grab().save(str(OUT.with_name("ui截图-Mod浏览器.png")))
        print("Mod 浏览器 ->", OUT.with_name("ui截图-Mod浏览器.png"))
        mb.close()

    # 第七、八张：弹药专用页 + 改动预览
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QMessageBox as _MB
    _MB.question = staticmethod(lambda *a, **k: _MB.Yes)
    _MB.information = staticmethod(lambda *a, **k: _MB.Ok)

    ammo_work = SRC / "Weapon_ammopage.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", ammo_work)
    try:
        win5 = MainWindow()
        win5.resize(1520, 900)
        win5.load_path(ammo_work)
        app.processEvents()
        ad = win5.open_ammo_editor()
        app.processEvents()
        ad.resize(1260, 840)

        # 条件：弹种 = Shell 且 对地射程 = 1925 —— 用户的原始场景
        r0 = ad.rows[0]
        r0.set_field("ProjectileType")
        app.processEvents()
        lst = r0.value._multi
        for i in range(lst.count()):
            lst.item(i).setCheckState(
                _Qt.CheckState.Checked if lst.item(i).text() == "Shell"
                else _Qt.CheckState.Unchecked)
        r1 = ad.add_row()
        r1.set_field("MaximumRangeGRU")
        app.processEvents()
        r1.value._single.setValue(1925)
        j = ad.bulk_field.findData("MaximumRangeGRU")
        ad.bulk_field.setCurrentIndex(j)
        ad.bulk_value.setValue(2100)
        app.processEvents()
        ad.show()
        app.processEvents()
        ad.grab().save(str(OUT.with_name("ui截图-弹药编辑页.png")))
        print("弹药专用页 ->", OUT.with_name("ui截图-弹药编辑页.png"))

        # 改动预览：先真的改一批，再打开保存前的对照表
        ad.on_apply()
        app.processEvents()
        prev = ChangePreviewDialog(win5.nd, win5)
        prev.resize(1060, 740)
        prev.show()
        app.processEvents()
        prev.grab().save(str(OUT.with_name("ui截图-改动预览.png")))
        print("改动预览 ->", OUT.with_name("ui截图-改动预览.png"))
        prev.close()
        ad.close()
    finally:
        ammo_work.unlink(missing_ok=True)
        Path(str(ammo_work) + ".bak").unlink(missing_ok=True)

    # ---- 引用行两按钮 / 对象钻取 / 克隆候选 ----
    # 这一段不落盘，所以复制到临时目录去开——不往游戏 mod 目录里写东西。
    clone_tmp = Path(tempfile.mkdtemp(prefix="ndfshot_"))
    clone_work = clone_tmp / "Weapon.ndfbin"
    shutil.copy2(MOD / "Weapon.ndfbin", clone_work)
    try:
        win6 = MainWindow()
        win6.resize(1400, 860)
        # show() 必须在 load_path **之前**：字段面板的滚动区尺寸是在那一刻
        # 定的，晚于它再 show 的话滚动条范围算不出来，滚不动。
        win6.show()
        app.processEvents()
        win6.load_path(clone_work)
        app.processEvents()
        AmmoTitles(win6.nd).apply()
        idx = next(i for i, c in enumerate(win6.nd.classes)
                   if c.name == AMMO_CLASS)
        win6.cls.setCurrentRow(idx)
        app.processEvents()
        win6.objs.setCurrentRow(0)
        app.processEvents()
        # 引用行排在数值字段后面，弹药有 60 个数值字段——不滚到底就截不到。
        bar = win6.panel.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        app.processEvents()
        shoot(app, win6, "ui截图-引用行两按钮.png",
              "字段面板里的引用行：长按钮进入目标，右边「替换」改指向")
        win6.hide()
        app.processEvents()

        # 对象钻取窗口：先开在弹药上，再往里钻一层 —— 这样能看到两层面包屑
        entry = win6.objs.entry_at(0)
        win6.on_open_object(entry.object_id)
        app.processEvents()
        dlg = win6._tool_windows.get("object")
        if dlg is not None:
            inner = dlg._stack[-1][0]
            drill = [n for n, r in inner.refs.items()
                     if r.kind == "objref" and not r.is_null
                     and win6.nd.entry_of(r.key) is not None]
            if drill:
                dlg.panel._on_open_ref(drill[0])
                app.processEvents()
            dlg.resize(560, 700)
            dlg.show()
            app.processEvents()
            dlg.grab().save(str(OUT.with_name("ui截图-对象钻取.png")))
            print("对象钻取 ->", OUT.with_name("ui截图-对象钻取.png"))
            dlg.close()

        # 克隆候选：真的克隆一个弹药，再打开多选窗口
        win6._close_tool_windows()
        win6.locate_object(entry.object_id)
        app.processEvents()
        win6.history.break_merge()
        clone = win6.nd.clone_object(win6.nd.entry_of(entry.object_id))
        win6.nd.ensure_titles(AMMO_CLASS)
        win6._on_entry_dirty()
        app.processEvents()
        bulk = RefBulkDialog(win6.nd, clone, win6.nd.entry_of(entry.object_id),
                             win6.nd.last_clone_guid_fields, win6)
        bulk.resize(980, 660)
        bulk.show()
        app.processEvents()
        bulk.search.setText("BMP")
        app.processEvents()
        bulk.grab().save(str(OUT.with_name("ui截图-克隆候选.png")))
        print("克隆候选 ->", OUT.with_name("ui截图-克隆候选.png"))
        bulk.close()

        # ---- 文本 / 哈希字段：字段面板、选字符串、编标签、照抄哈希 ----
        win6._close_tool_windows()
        win6.locate_object(entry.object_id)
        win6.show()
        app.processEvents()
        e_now = win6.nd.entry_of(entry.object_id)
        if e_now.strs and e_now.str_lists and e_now.hashes:
            # 这三类字段排在数值字段后面，不滚到底截不到。
            bar = win6.panel.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            app.processEvents()
            shoot(app, win6, "ui截图-文本字段.png",
                  "字段面板：字符串、一串字符串、哈希三类字段的行")

            sp = StrPickerDialog(win6, "ImpactHappening",
                                 e_now.strs["ImpactHappening"].index,
                                 win6.nd.strings_pool)
            sp.search.setText("Roquette")
            app.processEvents()
            sp.resize(840, 640)
            sp.show()
            app.processEvents()
            sp.grab().save(str(OUT.with_name("ui截图-字符串选择.png")))
            print("字符串选择 ->", OUT.with_name("ui截图-字符串选择.png"))
            sp.close()

            ld = StrListDialog(win6, e_now.str_lists["TraitsToken"],
                               win6.nd.strings_pool,
                               owner=f"#{entry.object_id}")
            ld.resize(720, 560)
            ld.show()
            app.processEvents()
            ld.grab().save(str(OUT.with_name("ui截图-标签编辑.png")))
            print("标签编辑 ->", OUT.with_name("ui截图-标签编辑.png"))
            ld.close()

            hp = HashPickerDialog(
                win6, "TypeCategoryName",
                e_now.hashes["TypeCategoryName"].hex,
                win6.nd.hash_choices(AMMO_CLASS, "TypeCategoryName"),
                owner=f"#{entry.object_id}")
            hp.resize(840, 600)
            hp.show()
            app.processEvents()
            hp.grab().save(str(OUT.with_name("ui截图-哈希照抄.png")))
            print("哈希照抄 ->", OUT.with_name("ui截图-哈希照抄.png"))
            hp.close()

        # ---- 一串引用：增删 + 查看 ----
        owner = lname = None
        for cand in win6.nd.entries("TTurretTwoAxisDescriptor"):
            for n, l in cand.ref_lists.items():
                if l.length >= 2:
                    owner, lname = cand, n
                    break
            if owner is not None:
                break
        if owner is not None:
            rl = RefListDialog(win6.nd, win6)
            rl.show_field(owner, lname)
            rl.search.setText("FireEffect")
            app.processEvents()
            rl.resize(920, 620)
            rl.show()
            app.processEvents()
            rl.grab().save(str(OUT.with_name("ui截图-列表增删.png")))
            print("列表增删 ->", OUT.with_name("ui截图-列表增删.png"))
            rl.close()
    finally:
        shutil.rmtree(clone_tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())