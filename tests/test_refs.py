"""引用字段编辑 + 实体聚合视图的专项测试。

用户需求：
  1. Unit.ndfbin 的 TEntityDescriptor 专用视图（血量/速度/装甲面板）；
  2. 指向其它类别对象的字段也应该能被编辑。

第 2 条的技术核心是 ``obj_ref`` 的 ``class_id``：编码里 ``object_id`` 和
``class_id`` 是两个 u32，``cndf_codec.decode`` 会校验
``object_class_ids[object_id] == class_id``。只改一半 = 整个文件解不开。
本测试第 4 节专门证明这一点。
"""

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtWidgets import QApplication, QDialog               # noqa: E402

from ndfbin_decks.core import cndf_codec, writer                  # noqa: E402
from ndfbin_decks.ndfedit import entity, refs, schema             # noqa: E402
from ndfbin_decks.ndfedit.gui import (                            # noqa: E402
    MainWindow, RefListDialog, RefPickerDialog)
from ndfbin_decks.ndfedit.schema import NdfDocument               # noqa: E402

SRC = _env.gfx()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def roundtrip(nd):
    """编码-解码往返，返回新的 decoded。"""
    repl = writer.sections_of(nd.doc)
    repl["OBJE"] = cndf_codec.encode_objects(nd.decoded["objects"])
    out = writer.write_bytes(nd.doc, repl)
    _doc, dec = cndf_codec.decode(out, source="<rebuilt>")
    return dec


def prop_of(dec, obj_id, name):
    obj = next(o for o in dec["objects"] if o["id"] == obj_id)
    return next(p for p in obj["properties"] if p["property_name"] == name)


def main() -> int:
    app = _env.qt_app()

    print("\n=== 1. 引用字段被收进对象条目 ===")
    nd = NdfDocument.load(SRC / "Unit.ndfbin")
    total = sum(len(e.refs) for es in nd._by_class.values() for e in es)
    check(f"全文件 RefField {total} 个", total > 5000, str(total))
    info = nd.class_info("TBlindageProperties")
    kinds = {k for _, k, _ in info.fields}
    check("TBlindageProperties 亮出引用字段",
          kinds >= {"objref"}, str(sorted(kinds)))
    e0 = nd.entries("TBlindageProperties")[0]
    check("四个面都有", set(e0.refs) >= {"ResistanceFront", "ResistanceSides",
                                        "ResistanceRear", "ResistanceTop"})
    r0 = e0.refs["ResistanceFront"]
    check("目标类被识别", r0.target_class == "TResistanceTypeRTTI",
          r0.target_class)
    check("候选数被算出", r0.pool_size == 62, str(r0.pool_size))

    print("\n=== 2. 标签逐级回退 ===")
    idx = nd.ref
    # TEntityDescriptor 有 export 名
    ent = nd.entries("TEntityDescriptor")[0]
    lab = idx.label_of(ent.object_id)
    check("有 export 名的用短名", lab.startswith("Descriptor_"),
          lab)
    # TResistanceTypeRTTI 只有 Family + Index
    rtti = nd.entries("TResistanceTypeRTTI")[0]
    lab2 = idx.label_of(rtti.object_id)
    check("RTTI 退化成 F<family>-<index>", lab2.startswith("F"),
          lab2)
    # TBlindageProperties 借引用者的标签
    lab3 = idx.label_of(e0.object_id)
    check("无名字能借到标签", not lab3.startswith("#"), lab3)

    print("\n=== 3. describe 区分 objref / pathref ===")
    # 这是踩过的坑：pathref 的 key 是 IMPR 节点号，当 object_id 用会
    # 显示成一个毫不相干的对象（TerrainSpeedFactors 曾显示成 #73）。
    deck = NdfDocument.load(SRC / "Deck.ndfbin")
    didx = deck.ref
    pack = next(e for e in deck.entries("TDeckPackDescriptor") if e.refs)
    up = pack.refs["UnitDescriptor"]
    got = refs.describe(didx, up)
    check("pathref 显示为路径末段", got.startswith("Descriptor_Unit_"),
          got)
    check("pathref 不需要 label_of",
          got == refs.short_name(didx.imports[up.key]))

    # trans_ref 的字段也出现在 Unit.ndfbin
    nd2 = nd
    found = None
    for cls in ("TLandMovementModuleDescriptor",):
        for e in nd2.entries(cls):
            if "TerrainSpeedFactors" in e.refs:
                found = (e, e.refs["TerrainSpeedFactors"])
                break
        if found:
            break
    check("找到 trans_ref 样本", found is not None)
    if found:
        e, rf = found
        txt = refs.describe(nd2.ref, rf)
        check("地形速度因子显示为路径而不是 #对象",
              "/" not in txt and not txt.startswith("#"), txt)
        check("路径是 Terrain 类资源",
              "Terrain" in nd2.ref.imports[rf.key],
              nd2.ref.imports[rf.key])

    print("\n=== 4. 改写引用：class_id 必须一起改 ===")
    victim = next(e for e in nd.entries("TBlindageProperties"))
    rf = victim.refs["ResistanceFront"]
    old = (rf.leaf["object_id"], rf.leaf["class_id"])
    cands = idx.candidates_for_class("TResistanceTypeRTTI")
    other = next(c for c in cands if c.key != rf.key)
    idx.repoint(rf.leaf, "obj", other.key)
    want_cid = idx.classes.index("TResistanceTypeRTTI")
    check("object_id 已换", rf.leaf["object_id"] == other.key)
    check("class_id 被重新写成目标类", rf.leaf["class_id"] == want_cid,
          f"{rf.leaf['class_id']} vs {want_cid}")
    check("字段标记为脏", rf.dirty)

    dec = roundtrip(nd)
    p = prop_of(dec, victim.object_id, "ResistanceFront")
    check("往返后指向新目标", p["value"]["object_id"] == other.key)
    check("往返后 class_id 自洽", p["value"]["class_id"] == want_cid)

    # 故意写一个「跨类但不改 class_id」的引用，证明校验真的存在
    cross = next(e for e in nd.entries("TBlindageProperties")
                 if e.object_id != victim.object_id)
    rf.leaf["object_id"] = cross.object_id          # 目标是别的类
    rf.leaf["class_id"] = want_cid                  # 沿用 RTTI 的 class_id
    try:
        roundtrip(nd)
        broken_rejected = False
    except ValueError:
        broken_rejected = True
    check("class_id 与目标类不符时 decode 拒绝", broken_rejected)
    # 写对 class_id 就合法了
    rf.leaf["class_id"] = idx.classes.index("TBlindageProperties")
    try:
        roundtrip(nd)
        ok_after = True
    except ValueError:
        ok_after = False
    check("class_id 写对后又能编解码", ok_after)
    rf.reset()
    check("撤销回到原值",
          (rf.leaf["object_id"], rf.leaf["class_id"]) == old,
          f"{rf.leaf['object_id']},{rf.leaf['class_id']}")
    check("撤销后不脏", not rf.dirty)

    print("\n=== 5. 置空与撤销空引用 ===")
    rf.leaf["object_id"] = refs.NULL_REF
    rf.leaf["class_id"] = refs.NULL_REF
    check("识别为空引用", rf.is_null)
    dec_null = roundtrip(nd)
    pn = prop_of(dec_null, victim.object_id, "ResistanceFront")
    check("空引用能编解码", pn["value"]["object_id"] == refs.NULL_REF)
    check("空引用也算改动", rf.dirty)
    rf.reset()
    check("撤销后恢复原目标", rf.leaf["object_id"] == old[0])

    print("\n=== 6. 候选集与「谁在用我」 ===")
    cands = idx.candidates_for_class("TResistanceTypeRTTI")
    check("候选数等于池子", len(cands) == 62, str(len(cands)))
    check("按被引用次数降序",
          all(cands[i].uses >= cands[i + 1].uses for i in range(6)))
    check("有使用计数", cands[0].uses > 0, str(cands[0].uses))
    top = cands[0]
    owners = idx.reverse.get(top.key, [])
    check("反向索引能查到引用者", len(owners) > 0, str(len(owners)))
    check("引用者字段名正确",
          all(f.startswith("Resistance") for _, f in owners),
          str(sorted({f for _, f in owners})))

    print("\n=== 7. 实体聚合视图 ===")
    check("TEntityDescriptor 被认成实体",
          entity.looks_like_entity(nd, "TEntityDescriptor"))
    check("非实体类不误判",
          not entity.looks_like_entity(nd, "TBlindageProperties"))

    shared = entity._build_shared_lookup(nd)
    tank = None
    for e in nd.entries("TEntityDescriptor"):
        if not idx.label_of(e.object_id).startswith("Descriptor_Unit_"):
            continue
        v = entity.build_entity_view(nd, e, shared_lookup=shared)
        names = {r.name for r in v.rows}
        if ({"MaxPhysicalDamages", "BlindageProperties"} <= names
                and any("Speed" in n for n in names)):
            tank = (e, v)
            break
    check("找到一个真正的实体（有血量+装甲+速度）", tank is not None)
    e, view = tank
    print(f"       样本 {view.title}  {view.summary()}")

    groups = view.groups()
    check("分成多个语义组", len(groups) >= 4, str(list(groups)))
    check("血量组有字段", len(groups.get("生存 / 血量", [])) >= 2)
    check("装甲组有字段", len(groups.get("装甲", [])) >= 1)
    check("机动组有字段", len(groups.get("机动 / 速度", [])) >= 2)
    hp = next(r for r in view.rows if r.name == "MaxPhysicalDamages")
    check("血量行指向模块", hp.module_class == "TBaseDamageModuleDescriptor",
          hp.module_class)
    armor = next(r for r in view.rows if r.name == "BlindageProperties")
    check("装甲是个引用不是数字", armor.kind == "ref")
    check("装甲的模块标了共享", armor.shared or armor.shared_by == 1,
          str(armor.shared_by))
    check("共享警告里有实体数", "%d 个实体" % armor.shared_by
          in (armor.warning or "") or not armor.shared)

    print("\n=== 8. 改血量直接落到模块原位 ===")
    f = hp.entry.fields["MaxPhysicalDamages"]
    before = f.value
    f.leaf["value"] = before + 7
    check("dirty_count 计入", nd.dirty_count() >= 1)
    dec2 = roundtrip(nd)
    p2 = prop_of(dec2, hp.module_id, "MaxPhysicalDamages")
    check("往返后血量是新值", p2["value"]["value"] == before + 7,
          f"{p2['value']['value']}")
    f.reset()
    check("撤销后回到原值", f.value == before)

    print("\n=== 9. 外部模块（trans_ref）被标出来 ===")
    ext = [m for m in view.modules if m.kind == "path"]
    check("实体的外部模块被收集", len(ext) >= 1, str(len(ext)))
    check("外部模块带完整路径",
          all(m.path.startswith("$/") for m in ext),
          str([m.path for m in ext][:2]))

    print("\n=== 10. UI：引用按钮 + 实体面板 ===")
    work = SRC / "Unit_reftest.ndfbin"
    shutil.copy2(SRC / "Unit.ndfbin", work)
    try:
        win = MainWindow()
        win.load_path(work)
        ci = next(i for i, c in enumerate(win.nd.classes)
                  if c.name == "TBlindageProperties")
        win.cls.setCurrentRow(ci)
        app.processEvents()
        check("建出了引用按钮", len(win.panel._ref_buttons) >= 4,
              str(len(win.panel._ref_buttons)))
        win.objs.setCurrentRow(0)
        app.processEvents()
        btn = win.panel._ref_buttons["ResistanceFront"]
        check("按钮显示当前目标", btn.text() and btn.text() != "…",
              btn.text())
        check("按钮已启用", btn.isEnabled())

        # 走完整链路：拦截对话框，选一个别的目标
        target = next(c for c in idx.candidates_for_class("TResistanceTypeRTTI")
                      if c.key != win.objs.entry_at(0).refs[
                          "ResistanceFront"].key)
        orig = RefPickerDialog.exec

        def fake_exec(self):
            self._choice = ("obj", target.key)
            return QDialog.Accepted
        RefPickerDialog.exec = fake_exec
        try:
            win.panel._on_pick_ref("ResistanceFront")
        finally:
            RefPickerDialog.exec = orig
        app.processEvents()
        entry = win.objs.entry_at(0)
        check("点击后引用目标已改",
              entry.refs["ResistanceFront"].key == target.key,
              str(entry.refs["ResistanceFront"].key))
        check("保存按钮已启用", win.btn_save.isEnabled())
        check("状态栏提到引用", "引用" in win.status.text(),
              win.status.text())

        # 实体面板
        ei = next(i for i, c in enumerate(win.nd.classes)
                  if c.name == "TEntityDescriptor")
        win.cls.setCurrentRow(ei)
        app.processEvents()
        row = next(i for i, en in enumerate(win.objs._entries)
                   if entity.build_entity_view(
                       win.nd, en,
                       shared_lookup=win.panel._shared_lookup or {}).key_rows())
        win.objs.setCurrentRow(row)
        app.processEvents()
        check("实体面板可见", win.panel.entity_box.isVisible()
              or not win.panel.entity_box.isHidden())
        check("实体面板建了控件",
              len(win.panel._entity_editors) >= 1,
              str(len(win.panel._entity_editors)))
        check("实体面板有汇总句", "模块" in win.panel.entity_note.text(),
              win.panel.entity_note.text()[:60])
        # 说明与共享警告合成了一个富文本标签，所以查文本而不是查控件可见性
        check("共享警告写进了说明里",
              "共用" in win.panel.entity_note.text()
              and "#ffd66a" in win.panel.entity_note.text(),
              win.panel.entity_note.text()[-60:])
    finally:
        work.unlink(missing_ok=True)
        Path(str(work) + ".bak").unlink(missing_ok=True)

    print("\n=== 11. 对话框本身 ===")
    dlg = RefPickerDialog(None, idx, idx and nd.entries(
        "TBlindageProperties")[0].refs["ResistanceFront"],
        owner_class="TBlindageProperties")
    check("对话框列出候选", dlg.listw.count() > 0, str(dlg.listw.count()))
    check("当前目标被标出",
          any(dlg.listw.item(i).text().startswith("●")
              for i in range(dlg.listw.count())))
    n_all = dlg.listw.count()
    dlg.search.setText("F2-3")
    dlg._refill()
    check("搜索能收窄", dlg.listw.count() < n_all,
          f"{n_all} -> {dlg.listw.count()}")
    if dlg.listw.count():
        dlg.listw.setCurrentRow(0)
        dlg.accept()
        kind, key = dlg.chosen()
        check("chosen 返回可用的目标", kind == "obj" and isinstance(key, int),
              f"{kind},{key}")
    dlg2 = RefPickerDialog(None, idx, nd.entries(
        "TBlindageProperties")[0].refs["ResistanceFront"])
    dlg2._pick_null()
    check("可以选「不指向任何东西」", dlg2.chosen() == ("null", None),
          str(dlg2.chosen()))

    print("\n=== 12. 带引用改动真实保存到磁盘 ===")
    work2 = SRC / "Unit_refsave.ndfbin"
    shutil.copy2(SRC / "Unit.ndfbin", work2)
    try:
        nd4 = NdfDocument.load(work2)
        idx4 = nd4.ref
        blind = next(e for e in nd4.entries("TBlindageProperties"))
        rf4 = blind.refs["ResistanceFront"]
        old_key = rf4.key
        tgt = next(c for c in idx4.candidates_for_class("TResistanceTypeRTTI")
                   if c.key != old_key)
        idx4.repoint(rf4.leaf, "obj", tgt.key)

        hp_entry = next(e for e in nd4.entries("TBaseDamageModuleDescriptor")
                        if "MaxPhysicalDamages" in e.fields)
        f4 = hp_entry.fields["MaxPhysicalDamages"]
        old_hp = f4.value
        f4.leaf["value"] = old_hp + 3

        res = nd4.save()
        check("保存成功", res.size_after != res.size_before,
              f"{res.size_before}->{res.size_after}")

        nd5 = NdfDocument.load(work2)
        v5 = next(e for e in nd5.entries("TBlindageProperties")
                  if e.object_id == blind.object_id)
        check("重读后装甲指向了新目标",
              v5.refs["ResistanceFront"].key == tgt.key,
              str(v5.refs["ResistanceFront"].key))
        check("重读后 class_id 自洽",
              v5.refs["ResistanceFront"].leaf["class_id"]
              == nd5.ref.classes.index("TResistanceTypeRTTI"))
        check("重读后标签正确",
              refs.describe(nd5.ref, v5.refs["ResistanceFront"]) == tgt.label,
              refs.describe(nd5.ref, v5.refs["ResistanceFront"]))
        h5 = next(e for e in nd5.entries("TBaseDamageModuleDescriptor")
                  if e.object_id == hp_entry.object_id)
        check("重读后血量是新值",
              h5.fields["MaxPhysicalDamages"].value == old_hp + 3,
              str(h5.fields["MaxPhysicalDamages"].value))
    finally:
        work2.unlink(missing_ok=True)
        Path(str(work2) + ".bak").unlink(missing_ok=True)

    print("\n=== 13. 列表型引用（一串引用）逐项编辑 ===")
    lnd = NdfDocument.load(SRC / "Deck.ndfbin")
    lnd_idx = lnd.ref
    total_lists = sum(len(e.ref_lists) for es in lnd._by_class.values()
                      for e in es)
    check(f"Deck 里收集到 {total_lists} 个列表引用", total_lists > 1000,
          str(total_lists))
    info = lnd.class_info("TDeckDescriptor")
    kinds = {k for _, k, _ in info.fields}
    check("字段表里出现 objlist", "objlist" in kinds, str(sorted(kinds)))

    deck_entry = next(e for e in lnd.entries("TDeckDescriptor")
                      if e.ref_lists.get("DeckPackList")
                      and e.ref_lists["DeckPackList"].length >= 10)
    lf = deck_entry.ref_lists["DeckPackList"]
    print(f"       样本 deck #{deck_entry.object_id}  DeckPackList {lf.length} 项")
    check("列表项都是 RefField", all(hasattr(x, "leaf") for x in lf.items))
    check("目标类是 TDeckPackDescriptor",
          lf.uniform_class() == "TDeckPackDescriptor", lf.uniform_class())
    labels = [lnd_idx.label_of(r.key) for r in lf.items[:3]]
    check("pack 标签借到了单位名（不是 ExperienceLevel=…）",
          all(x.startswith("Descriptor_Unit_") for x in labels), str(labels))
    check("摘要句可读且剥掉了冗长前缀",
          "_CAN" in lf.summary(lnd_idx)
          and "Descriptor_Unit_" not in lf.summary(lnd_idx),
          lf.summary(lnd_idx)[:60])

    # 改第 3 项
    r3 = lf.items[3]
    old3 = (r3.leaf["object_id"], r3.leaf["class_id"])
    other = next(c for c in
                 lnd_idx.candidates_for_class("TDeckPackDescriptor")
                 if c.key != r3.key)
    lnd_idx.repoint(r3.leaf, "obj", other.key)
    check("第 3 项已改", r3.leaf["object_id"] == other.key)
    check("class_id 同步", r3.leaf["class_id"]
          == lnd_idx.classes.index("TDeckPackDescriptor"))
    check("命中下标被记录", lf.dirty_indices() == [3], str(lf.dirty_indices()))
    check("列表整体标记为脏", lf.dirty)
    check("全局计数", lnd.ref_list_dirty_total() == 1,
          str(lnd.ref_list_dirty_total()))

    dec13 = roundtrip(lnd)
    o13 = next(o for o in dec13["objects"] if o["id"] == deck_entry.object_id)
    p13 = next(p for p in o13["properties"]
               if p["property_name"] == "DeckPackList")
    check("往返后第 3 项是新目标",
          p13["value"]["items"][3]["object_id"] == other.key)
    check("往返后长度一项不差",
          p13["value"]["length"] == lf.length,
          f'{p13["value"]["length"]} vs {lf.length}')
    check("往返后其余项未动",
          p13["value"]["items"][0]["object_id"] == lf.items[0].key)
    lf.reset()
    # reset 走的是「整条替换」，items 里是重建出来的对象——旧的引用已经
    # 和 leaf 脱节了。列表的状态只能是整条：增删会移动索引，逐项退不回去。
    check("撤销后第 3 项复原", lf.items[3].leaf["object_id"] == old3[0])
    check("撤销后不再脏", not lf.dirty)

    # 混类型的列表必须被拒
    check("空列表不算",
          not schema._all_items_are_refs({"type_id": 0x11, "items": []}))
    check("混合列表不算", not schema._all_items_are_refs(
        {"type_id": 0x11, "items": [{"type_id": 0xBBBBBBBB}, {"type_id": 2}]}))
    wnd = NdfDocument.load(SRC / "Weapon.ndfbin")
    bad = [n for es in wnd._by_class.values() for e in es
           for n in e.ref_lists
           if n in ("WeaponShootDataPropertyName", "AmmoBoxIndex")]
    check("字符串型列表没被误收", not bad, str(bad[:3]))

    # ModulesDescriptors：同一个字段混着 77 种目标类
    me = next(e for e in wnd.entries("TEntityDescriptor") if e.ref_lists)
    mlf = me.ref_lists["ModulesDescriptors"]
    check("ModulesDescriptors 被收进来了", mlf.length > 0, str(mlf.length))
    check("它确实混着多个目标类", len(mlf.target_classes()) > 3,
          str(len(mlf.target_classes())))
    check("uniform_class 对混合列表返回空",
          mlf.uniform_class() == "", mlf.uniform_class())

    print("\n=== 14. 列表编辑器 UI ===")
    work3 = SRC / "Deck_listtest.ndfbin"
    shutil.copy2(SRC / "Deck.ndfbin", work3)
    try:
        win3 = MainWindow()
        win3.load_path(work3)
        ci = next(i for i, c in enumerate(win3.nd.classes)
                  if c.name == "TDeckDescriptor")
        win3.cls.setCurrentRow(ci)
        app.processEvents()
        check("建出了列表按钮", "DeckPackList" in win3.panel._list_buttons,
              str(list(win3.panel._list_buttons)))
        # 换到那条有 10+ 项的 deck
        row3 = next(i for i, en in enumerate(win3.objs._entries)
                    if en.ref_lists.get("DeckPackList")
                    and en.ref_lists["DeckPackList"].length >= 10)
        win3.objs.setCurrentRow(row3)
        app.processEvents()
        btn3 = win3.panel._list_buttons["DeckPackList"]
        n_items3 = win3.objs.entry_at(row3).ref_lists["DeckPackList"].length
        check("按钮显示项数与内容", btn3.text().startswith(f"{n_items3} 项"),
              btn3.text()[:70])

        lf3 = win3.objs.entry_at(row3).ref_lists["DeckPackList"]
        # 列表对话框现在是非模态工具窗口（它里面的「查看」要能打开对象
        # 编辑窗口），接口也从「传 index + field」变成「传文档 + 指定字段」。
        dlg3 = RefListDialog(win3.nd, win3)
        dlg3.show_field(win3.objs.entry_at(row3), "DeckPackList")
        check("对话框列出全部项", dlg3.listw.count() == lf3.length,
              f"{dlg3.listw.count()} vs {lf3.length}")
        check("计数句带上了项数", f"{lf3.length} 项" in dlg3.count.text(),
              dlg3.count.text())
        w0 = dlg3.listw.itemWidget(dlg3.listw.item(0))
        from PySide6.QtWidgets import QLabel as _QL
        texts = [x.text() for x in w0.findChildren(_QL)]
        check("每行有下标与标签", texts and texts[0] == "0", str(texts))

        # 搜索
        dlg3.search.setText("M109")
        dlg3._refill()
        n_search = dlg3.listw.count()
        check("搜索能过滤", n_search < lf3.length, f"{n_search}")
        dlg3.search.setText("")
        dlg3._refill()

        # 走真实链路改一项：列表窗口现在是非模态工具窗口（不走 exec），
        # 所以先从 _tool_windows 里取它，再拦住「选目标」那个模态框。
        target3 = next(c for c in
                       win3.nd.ref.candidates_for_class("TDeckPackDescriptor")
                       if c.key != lf3.items[1].key)
        win3.panel._on_open_list("DeckPackList")
        app.processEvents()
        dlg_live = win3._tool_windows.get("reflist")
        check("列表编辑器作为工具窗口开出来了", dlg_live is not None,
              str(sorted(win3._tool_windows)))
        if dlg_live is not None:
            fired: list = []
            dlg_live.dirtyChanged.connect(lambda: fired.append(1))
            orig_pick = RefPickerDialog.exec

            def fake_pick(self):
                self._choice = ("obj", target3.key)
                return QDialog.Accepted
            RefPickerDialog.exec = fake_pick
            try:
                dlg_live._swap(1)
            finally:
                RefPickerDialog.exec = orig_pick
            app.processEvents()
            check("对话框内改项生效", lf3.items[1].key == target3.key)
            check("★ 对话框发出了 dirtyChanged 信号", bool(fired), str(fired))
            check("对话框计数句提到改动",
                  "与打开时不同" in dlg_live.count.text(),
                  dlg_live.count.text())
            check("主面板按钮反映改动",
                  "已改" in win3.panel._list_buttons["DeckPackList"].text(),
                  win3.panel._list_buttons["DeckPackList"].text()[-24:])
            check("保存按钮已启用", win3.btn_save.isEnabled())
            check("状态栏提到列表引用",
                  "列表里的引用" in win3.status.text(), win3.status.text())
    finally:
        work3.unlink(missing_ok=True)
        Path(str(work3) + ".bak").unlink(missing_ok=True)

    print("\n=== 15. 换目标类会被拦住 ===")
    # 实测踩过：把 ResistanceFront 指向一个 TTagsModuleDescriptor。
    # class_id 写对了，所以编码完全合法、decode 也通过——但语义崩坏。
    # 编码层的校验抓不到，必须在内核拦。
    v15 = next(e for e in nd.entries("TBlindageProperties") if e.refs)
    rf15 = v15.refs["ResistanceFront"]
    same = [c for c in idx.candidates_for_class(rf15.target_class)
            if c.key != rf15.key]
    idx.repoint(rf15.leaf, "obj", same[0].key)
    check("同类换目标仍然可以", rf15.key == same[0].key, str(rf15.key))
    rf15.reset()

    foreign_id = next(cid for cid, name in enumerate(idx.classes)
                      if name == "TTagsModuleDescriptor")
    foreign = next(o for o in nd.decoded["objects"]
                   if o["class_id"] == foreign_id)
    try:
        idx.repoint(rf15.leaf, "obj", foreign["id"])
        check("异类换目标被拦", False, "没有被拦住")
    except ValueError as exc:
        check("异类换目标被拦", "不能改成" in str(exc), str(exc)[:44])
    check("被拒后字段原封不动",
          rf15.key == rf15.original_key, str(rf15.key))
    check("被拒后不算脏", not rf15.dirty)

    idx.repoint(rf15.leaf, "obj", foreign["id"], allow_class_change=True)
    check("显式允许后可以跨类", rf15.key == foreign["id"])
    rf15.reset()

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
