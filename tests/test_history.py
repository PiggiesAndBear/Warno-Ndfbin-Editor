"""撤销 / 重做的测试。

设计上最容易出错的地方不是「撤销把值写回」，而是**写回之后文档是否
真的回到了那个时刻**——包括引用节点、补加字段这些「结构」上的东西。
所以除了逐项测试，最后压了一段随机操作序列，退到底再进到头，
用 ``encode_objects`` 的字节序列做全等比对：不是比几个字段，是比整个
对象图。差一个字节就说明有东西没退干净。

两个环境陷阱（都踩过）：
- 离屏环境下模态框会永久阻塞。``QMessageBox`` 的四个方法和保存前的
  ``ChangePreviewDialog.exec`` 必须换成 no-op，否则测试挂死。
- 各小节需要的是「一份没被动过的文档 + 一份空历史」，
  :func:`reseat` 用 ``reset_all`` 做到只要 96ms，而重新 ``load`` 要 3.2s。
"""

import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtCore import Qt                                      # noqa: E402
from PySide6.QtGui import QShortcut                                # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog,              # noqa: E402
                               QMessageBox)

from ndfbin_decks.core import cndf_codec                           # noqa: E402
from ndfbin_decks.ndfedit import query as Q                        # noqa: E402
from ndfbin_decks.ndfedit.ammo import AMMO_CLASS, AmmoTitles       # noqa: E402
from ndfbin_decks.ndfedit.gui import (ChangePreviewDialog,         # noqa: E402
                                      MainWindow)
from ndfbin_decks.ndfedit.history import (                         # noqa: E402
    MERGE_WINDOW, MISSING, History, Stats)
from ndfbin_decks.ndfedit.schema import NdfDocument                # noqa: E402

SRC = _env.gfx()
RANGE = "MaximumRangeGRU"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def objgraph(nd) -> bytes:
    """整个对象图的规范字节表示——用来做「退回去没有」的全等比对。"""
    return cndf_codec.encode_objects(nd.decoded["objects"])


def reseat(nd):
    """把文档退回原样并换一份全新的历史。

    各小节要的只是「没被动过的文档 + 空历史」，重新解析文件并不必要。
    实测 96ms vs 3206ms，十几节下来差一分多钟。
    """
    nd.reset_all()
    nd._history = None                     # 丢掉旧历史，下次访问重建
    return nd, nd.history


def ammo_field(entry, kind="float"):
    return next(f for f in entry.fields.values()
                if f.kind == kind and not f.added)


def set_multi(row, wanted: set):
    """勾选枚举多选里的若干项。"""
    lst = row.value._multi
    for i in range(lst.count()):
        it = lst.item(i)
        it.setCheckState(Qt.CheckState.Checked if it.text() in wanted
                         else Qt.CheckState.Unchecked)


def main() -> int:
    app = _env.qt_app()
    for name in ("critical", "information", "warning", "question"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    # 保存前会弹逐字段预览框；离屏下 exec() 会永久阻塞。
    ChangePreviewDialog.exec = lambda self: QDialog.Accepted

    if not (SRC / "Weapon.ndfbin").is_file():
        print(f"跳过：找不到 {SRC}")
        return 0

    nd = NdfDocument.load(SRC / "Weapon.ndfbin")
    AmmoTitles(nd).apply()

    # ================================================================
    print("\n=== 1. 初始状态 ===")
    h = nd.history
    check("刚打开时没有可撤销步骤", not h.can_undo and not h.can_redo)
    check("刚打开时快照为空", h.snapshot() == {}, str(len(h.snapshot())))
    check("刚打开时统计全零", h.stats() == Stats(), str(h.stats()))
    check("历史与文档一对一（同一个对象）", nd.history is h)

    # ================================================================
    print("\n=== 2. 单个字段：改 → 撤销 → 重做 ===")
    nd, h = reseat(nd)
    base = objgraph(nd)

    e0 = nd.entries(AMMO_CLASS)[0]
    f0 = ammo_field(e0)
    orig = f0.value
    f0.leaf["value"] = orig + 1.0
    step = h.commit()
    check("提交拿到步骤", step is not None)
    check("步骤说明含对象名与字段名",
          "AA Missile" in step.label and "Fx Power" in step.label, step.label)
    check("栈深 1", h.depth() == (1, 0), str(h.depth()))
    check("快照记录了新值",
          h.snapshot()[e0.object_id][("field", f0.name, 0)] == orig + 1.0)

    h.undo()
    check("撤销回到原值", f0.value == orig, f"{f0.value} vs {orig}")
    check("撤销后统计归零", h.stats() == Stats(), str(h.stats()))
    check("撤销后对象图与初始全等", objgraph(nd) == base)
    check("撤销后栈深 (0,1)", h.depth() == (0, 1), str(h.depth()))

    h.redo()
    check("重做回到新值", f0.value == orig + 1.0, str(f0.value))
    check("重做后可撤销", h.depth() == (1, 0), str(h.depth()))

    # ================================================================
    print("\n=== 3. 连续微调合并成一步 ===")
    nd, h = reseat(nd)
    e1 = nd.entries(AMMO_CLASS)[0]
    f1 = ammo_field(e1)
    start = f1.value
    for delta in (1.0, 2.0, 3.0):
        f1.leaf["value"] = start + delta
        h.commit()
    check("连点三次只留一步", h.depth() == (1, 0), str(h.depth()))
    check("合并后快照是最后那个值",
          h.snapshot()[e1.object_id][("field", f1.name, 0)] == start + 3.0)
    before, after = h._undo[-1].delta[e1.object_id][("field", f1.name, 0)]
    check("合并后改前是 MISSING（=原值）", before is MISSING, str(before))
    check("合并后改后是最后那个值", after == start + 3.0, str(after))
    h.undo()
    check("一步撤销回到起点", f1.value == start, str(f1.value))

    print("\n--- 3b. 超出时间窗口不合并 ---")
    nd, h = reseat(nd)
    e2 = nd.entries(AMMO_CLASS)[0]
    f2 = ammo_field(e2)
    s2 = f2.value
    f2.leaf["value"] = s2 + 1.0
    h.commit()
    h._undo[-1].at -= MERGE_WINDOW + 1.0        # 假装已经过了一段时间
    f2.leaf["value"] = s2 + 2.0
    h.commit()
    check("超出窗口不合并", h.depth() == (2, 0), str(h.depth()))

    print("\n--- 3c. 改出去又改回来不留空步骤 ---")
    nd, h = reseat(nd)
    e3 = nd.entries(AMMO_CLASS)[0]
    f3 = ammo_field(e3)
    s3 = f3.value
    f3.leaf["value"] = s3 + 1.0
    h.commit()
    f3.leaf["value"] = s3
    h.commit()
    check("净效果为零 → 步骤被撤销掉", h.depth() == (0, 0), str(h.depth()))
    check("值回到原值", f3.value == s3)

    print("\n--- 3d. break_merge 强制开新的一步 ---")
    nd, h = reseat(nd)
    e3b = nd.entries(AMMO_CLASS)[0]
    f3b = ammo_field(e3b)
    s3b = f3b.value
    f3b.leaf["value"] = s3b + 1.0
    h.commit()
    h.break_merge()
    f3b.leaf["value"] = s3b
    h.commit()
    # 同样的序列在 3c 里净效果为零、不留步骤；break_merge 之后它必须留成
    # 独立的一步，否则「撤销这一条」这种明确动作会变得无法回退。
    check("★ 明确动作即使在窗口内也单独成步", h.depth() == (2, 0),
          str(h.depth()))
    h.undo()
    check("★ 于是这一步可以退回来", f3b.value == s3b + 1.0, str(f3b.value))
    h.undo()
    check("再退一步才回到起点", f3b.value == s3b, str(f3b.value))

    print("\n--- 3e. 不同字段不合并 ---")
    nd, h = reseat(nd)
    e4 = nd.entries(AMMO_CLASS)[0]
    fs = [f for f in e4.fields.values()
          if f.kind == "float" and not f.added][:2]
    fs[0].leaf["value"] = float(fs[0].value) + 1.0
    h.commit()
    fs[1].leaf["value"] = float(fs[1].value) + 1.0
    h.commit()
    check("改了另一个字段 → 两步", h.depth() == (2, 0), str(h.depth()))

    # ================================================================
    print("\n=== 4. 批量改 150 条：一步撤销 ===")
    nd, h = reseat(nd)
    base = objgraph(nd)
    ammo = nd.entries(AMMO_CLASS)
    q4 = Q.Query([
        Q.Condition("ProjectileType", Q.MATCH_IN, values=(1,), kind="int"),
        Q.Condition(RANGE, Q.MATCH_EQUAL, value=1925, kind="float")])
    res = Q.apply_value(ammo, q4, RANGE, 2100, "float")
    h.commit()
    check("命中 150 条", res.changed == 150, str(res.changed))
    check("批量改是一步", h.depth() == (1, 0), str(h.depth()))
    check("说明里含对象数", "150 个对象" in h.undo_label(), h.undo_label())
    check("统计：150 个对象 / 150 个字段",
          h.stats().objects == 150 and h.stats().fields == 150,
          str(h.stats()))

    h.undo()
    left = [e for e in ammo
            if e.fields.get(RANGE) and e.fields[RANGE].value == 1925.0]
    check("撤销后 1925 又是 152 条", len(left) == 152, str(len(left)))
    check("撤销后统计归零", h.stats() == Stats(), str(h.stats()))
    check("撤销后对象图与初始全等", objgraph(nd) == base)

    h.redo()
    at2100 = [e for e in ammo
              if e.fields.get(RANGE) and e.fields[RANGE].value == 2100.0]
    check("重做后 2100 的条数恢复", len(at2100) == 228, str(len(at2100)))

    print("\n--- 4b. 两次批量改是两步 ---")
    nd, h = reseat(nd)
    ammo = nd.entries(AMMO_CLASS)
    Q.apply_value(ammo, Q.Query([Q.Condition("ProjectileType", Q.MATCH_IN,
                                            values=(1,), kind="int")]),
                  "PhysicalDamages", 99.0, "float")
    h.commit()
    n_first = h.stats().fields
    Q.apply_value(ammo, Q.Query([Q.Condition("ProjectileType", Q.MATCH_IN,
                                            values=(2,), kind="int")]),
                  "PhysicalDamages", 88.0, "float")
    h.commit()
    check("两次批量改 → 两步", h.depth() == (2, 0), str(h.depth()))
    h.undo()
    check("退一步只退第二次",
          h.stats().fields == n_first, f"{h.stats().fields} vs {n_first}")
    h.undo()
    check("退两步全清", h.stats() == Stats(), str(h.stats()))

    # ================================================================
    print("\n=== 5. 引用：撤销要连 class_id 一起恢复 ===")
    nd, h = reseat(nd)
    holder = next((e for e in nd.entries("TBlindageProperties") if e.refs), None)
    if holder is None:
        check("样本里有引用字段（跳过）", True)
    else:
        rname, rf = next(iter(holder.refs.items()))
        before = (rf.leaf.get("object_id"), rf.leaf.get("class_id"))
        other = next(c for c in nd.ref.candidates_for_class(rf.target_class)
                     if c.key != rf.key)
        nd.ref.repoint(rf.leaf, "obj", other.key)
        h.commit()
        check("同类换目标后脏", rf.dirty and h.stats().refs == 1,
              str(h.stats()))
        check("同类换目标 class_id 不变（本来就该如此）",
              rf.leaf.get("class_id") == before[1],
              f"{rf.leaf.get('class_id')} vs {before[1]}")
        h.undo()
        check("撤销恢复 object_id", rf.leaf.get("object_id") == before[0],
              str(rf.leaf.get("object_id")))
        check("撤销恢复 class_id", rf.leaf.get("class_id") == before[1],
              str(rf.leaf.get("class_id")))
        check("撤销后引用不脏", not rf.dirty)
        h.redo()
        check("重做回到新目标", rf.key == other.key, str(rf.key))

    print("\n--- 5b. 跨类换目标后撤销，class_id 必须一起回来 ---")
    nd, h = reseat(nd)
    holder = next((e for e in nd.entries("TBlindageProperties") if e.refs), None)
    alien = nd.ref.pool_of_class("TTagsModuleDescriptor")
    if holder is None or not alien:
        check("样本里有可跨类指向的对象（跳过）", True)
    else:
        rname, rf = next(iter(holder.refs.items()))
        old_oid, old_cid = rf.leaf.get("object_id"), rf.leaf.get("class_id")
        nd.ref.repoint(rf.leaf, "obj", alien[0], allow_class_change=True)
        h.commit()
        check("跨类换目标后 class_id 变了",
              rf.leaf.get("class_id") != old_cid,
              f"{rf.leaf.get('class_id')} vs {old_cid}")
        h.undo()
        check("★ 撤销把 object_id 退回去了",
              rf.leaf.get("object_id") == old_oid,
              str(rf.leaf.get("object_id")))
        check("★ 撤销把 class_id 也退回去了",
              rf.leaf.get("class_id") == old_cid,
              str(rf.leaf.get("class_id")))
        check("★ 退回去之后引用自洽（没留下半个引用）", not rf.dirty)
        h.redo()
        check("重做跨类目标", rf.leaf.get("object_id") == alien[0])
        h.undo()
        check("再撤销又自洽", not rf.dirty)

    # ================================================================
    print("\n=== 6. 一串引用：撤销第 i 项 ===")
    deck = NdfDocument.load(SRC / "Deck.ndfbin")
    hd = deck.history
    holder = next((e for e in deck.entries("TDeckDescriptor")
                   if e.ref_lists.get("DeckPackList")
                   and e.ref_lists["DeckPackList"].length >= 6), None)
    if holder is None:
        check("样本里有列表引用（跳过）", True)
    else:
        lf = holder.ref_lists["DeckPackList"]
        idx, n_before = 2, lf.length
        item = lf.items[idx]
        before = (item.leaf.get("object_id"), item.leaf.get("class_id"))
        other = next(c for c in deck.ref.candidates_for_class(item.target_class)
                     if c.key != item.key)
        deck.ref.repoint(item.leaf, "obj", other.key)
        hd.commit()
        check("列表项改后只这一项脏", lf.dirty_indices() == [idx],
              str(lf.dirty_indices()))
        hd.undo()
        # 撤销之后要**重新取**那一项。列表的状态是「整条」而不是「逐项」——
        # 增删会移动索引，逐项状态退不回去；所以 set_nodes 之后 items 里
        # 是新建的 RefField 对象，旧的那个已经和 leaf 脱节了。
        after = lf.items[idx]
        check("撤销恢复该项",
              after.leaf.get("object_id") == before[0]
              and after.leaf.get("class_id") == before[1],
              str((after.leaf.get("object_id"), after.leaf.get("class_id"))))
        check("撤销后整个列表不脏", not lf.dirty, str(lf.dirty_indices()))
        check("撤销后统计归零", hd.stats() == Stats(), str(hd.stats()))
        check("列表长度一项未动", lf.length == n_before, str(lf.length))
        # 列表的状态是整条的，所以步骤说明里不再有「第几项」——一次撤销
        # 退掉的是整条列表，说具体下标反而是误导。
        check("列表说明里带字段名", "Deck Pack List" in hd.redo_label(),
              hd.redo_label())

    # ================================================================
    print("\n=== 7. 补加字段：撤销要整个摘掉 ===")
    nd, h = reseat(nd)
    base = objgraph(nd)
    e5 = nd.entries(AMMO_CLASS)[0]
    missing = next((n for n, k, c in nd.class_info(AMMO_CLASS).fields
                    if k == "float" and n not in e5.fields), None)
    if missing is None:
        check("样本里有可补加的字段（跳过）", True)
    else:
        nd.add_field(e5, missing)
        h.commit()
        check("补加后统计 added=1", h.stats().added == 1, str(h.stats()))
        check("补加后对象被算作已改动", h.stats().objects == 1)
        check("补加的字段不计入 fields", h.stats().fields == 0)
        h.undo()
        check("撤销后字段被整个摘掉", missing not in e5.fields)
        check("撤销后对象图与初始全等", objgraph(nd) == base)
        h.redo()
        check("重做后又补上了",
              missing in e5.fields and e5.fields[missing].added)
        h.undo()
        check("再撤销又摘掉", missing not in e5.fields)

    print("\n--- 7b. 补加后立刻改值：合并成一步，撤销直接删掉 ---")
    nd, h = reseat(nd)
    e6 = nd.entries(AMMO_CLASS)[0]
    m6 = next((n for n, k, c in nd.class_info(AMMO_CLASS).fields
               if k == "float" and n not in e6.fields), None)
    if m6 is not None:
        nd.add_field(e6, m6)
        h.commit()
        default6 = e6.fields[m6].value
        e6.fields[m6].leaf["value"] = float(default6) + 7.0
        h.commit()
        # 两步的状态键完全相同（都是那一个「补加的字段」），又在合并窗口内，
        # 于是压成一步。这符合直觉：「我刚加了这个字段并填了个值」，
        # Ctrl+Z 一下就该整个消失，而不是先退回默认值。
        check("补加 + 立刻改值 → 合并成一步", h.depth() == (1, 0),
              str(h.depth()))
        check("★ 合并后改前 = 不存在（撤销等于删掉它）",
              h._undo[-1].delta[e6.object_id][("field", m6, 0)][0] is MISSING)
        h.undo()
        check("一步撤销就把字段整个删掉", m6 not in e6.fields)
        h.redo()
        check("重做后字段回来了", m6 in e6.fields)
        check("值带的是改后的那个",
              e6.fields[m6].value == float(default6) + 7.0,
              str(e6.fields[m6].value))
        h.undo()

    print("\n--- 7c. 隔开一段时间再改值：两步，逐步退 ---")
    nd, h = reseat(nd)
    e7 = nd.entries(AMMO_CLASS)[0]
    m7 = next((n for n, k, c in nd.class_info(AMMO_CLASS).fields
               if k == "float" and n not in e7.fields), None)
    if m7 is not None:
        nd.add_field(e7, m7)
        h.commit()
        h._undo[-1].at -= MERGE_WINDOW + 1.0        # 假装过了一会儿
        default7 = e7.fields[m7].value
        e7.fields[m7].leaf["value"] = float(default7) + 7.0
        h.commit()
        check("超出窗口 → 两步", h.depth() == (2, 0), str(h.depth()))
        check("第二处改动改前是默认值",
              h._undo[-1].delta[e7.object_id][("field", m7, 0)][0] == default7)
        h.undo()
        check("退一步只退值，字段还在",
              m7 in e7.fields and not e7.fields[m7].dirty,
              str(e7.fields[m7].dirty if m7 in e7.fields else None))
        check("退回到补加时的默认值", e7.fields[m7].value == default7)
        h.undo()
        check("退第二步才摘掉字段", m7 not in e7.fields)

    print("\n--- 7d. 摘掉补加字段也能撤销回来 ---")
    nd, h = reseat(nd)
    e8 = nd.entries(AMMO_CLASS)[0]
    m8 = next((n for n, k, c in nd.class_info(AMMO_CLASS).fields
               if k == "float" and n not in e8.fields), None)
    if m8 is not None:
        nd.add_field(e8, m8, 3.5)
        h.commit()
        h._undo[-1].at -= MERGE_WINDOW + 1.0
        nd.remove_field(e8, m8)
        h.commit()
        check("补加再摘掉 → 两步", h.depth() == (2, 0), str(h.depth()))
        h.undo()
        check("撤销摘掉 → 字段回来了", m8 in e8.fields)
        check("值也回来了", e8.fields[m8].value == 3.5,
              str(e8.fields[m8].value))
        check("且仍然是「补加的」", e8.fields[m8].added)
        h.undo()
        check("再撤销 → 又没了", m8 not in e8.fields)

    print("\n--- 7e. 补加又立刻摘掉：净效果为零 ---")
    nd, h = reseat(nd)
    e8b = nd.entries(AMMO_CLASS)[0]
    m8b = next((n for n, k, c in nd.class_info(AMMO_CLASS).fields
                if k == "float" and n not in e8b.fields), None)
    if m8b is not None:
        nd.add_field(e8b, m8b)
        h.commit()
        nd.remove_field(e8b, m8b)
        h.commit()
        check("加完又删 → 不留步骤", h.depth() == (0, 0), str(h.depth()))

    # ================================================================
    print("\n=== 8. 新改动会作废重做分支 ===")
    nd, h = reseat(nd)
    e9 = nd.entries(AMMO_CLASS)[0]
    f9 = ammo_field(e9)
    s9 = f9.value
    f9.leaf["value"] = s9 + 1.0
    h.commit()
    h.undo()
    check("撤销后有重做", h.can_redo and h.redo_label() != "")
    f9.leaf["value"] = s9 + 9.0
    h.commit()
    check("新改动清空了重做", not h.can_redo)
    check("栈里只有新那一步", h.depth() == (1, 0), str(h.depth()))

    print("\n--- 8b. 重做的顺序 ---")
    nd, h = reseat(nd)
    e10 = nd.entries(AMMO_CLASS)[0]
    a = ammo_field(e10)
    b = [f for f in e10.fields.values()
         if f.kind == "float" and not f.added and f is not a][0]
    sa, sb = float(a.value), float(b.value)
    a.leaf["value"] = sa + 1.0
    h.commit()
    b.leaf["value"] = sb + 2.0
    h.commit()
    h.undo()
    h.undo()
    check("两次撤销都回到原值", a.value == sa and b.value == sb,
          f"{a.value},{b.value}")
    h.redo()
    check("第一次重做只恢复第一处", a.value == sa + 1.0 and b.value == sb,
          f"{a.value},{b.value}")
    h.redo()
    check("第二次重做恢复第二处", b.value == sb + 2.0, str(b.value))

    # ================================================================
    print("\n=== 9. 上限：超出 limit 丢最旧的 ===")
    nd, _ = reseat(nd)
    h = History(nd, limit=3)
    targets = [e for e in nd.entries(AMMO_CLASS)
               if e.fields.get(RANGE)][:5]
    for i, e in enumerate(targets):
        e.fields[RANGE].leaf["value"] = 1000.0 + i
        h.commit()
    check("只留 3 步", h.depth() == (3, 0), str(h.depth()))
    while h.can_undo:
        h.undo()
    check("退到上限就没了", not h.can_undo)
    still = [e for e in targets
             if e.fields[RANGE].value != e.fields[RANGE].original]
    check("最旧的两步退不掉（符合预期）", len(still) == 2, str(len(still)))

    # ================================================================
    print("\n=== 10. 统计与 NdfDocument 的老方法一致 ===")
    nd, h = reseat(nd)
    ammo = nd.entries(AMMO_CLASS)
    Q.apply_value(ammo, Q.Query([Q.Condition(RANGE, Q.MATCH_EQUAL,
                                            value=1925, kind="float"),
                                 Q.Condition("ProjectileType", Q.MATCH_IN,
                                             values=(1,), kind="int")]),
                  RANGE, 2100, "float")
    holder = next((e for e in nd.entries("TBlindageProperties") if e.refs), None)
    if holder is not None:
        rf = next(iter(holder.refs.values()))
        nd.ref.repoint(rf.leaf, "obj",
                       next(c for c in nd.ref.candidates_for_class(
                           rf.target_class) if c.key != rf.key).key)
    e11 = nd.entries(AMMO_CLASS)[0]
    m11 = next((n for n, k, c in nd.class_info(AMMO_CLASS).fields
                if k == "float" and n not in e11.fields), None)
    if m11:
        nd.add_field(e11, m11)
    h.commit()
    st = h.stats()
    check("objects 一致", st.objects == nd.dirty_count(),
          f"{st.objects} vs {nd.dirty_count()}")
    check("fields 一致", st.fields == nd.dirty_total(),
          f"{st.fields} vs {nd.dirty_total()}")
    check("added 一致", st.added == nd.added_total(),
          f"{st.added} vs {nd.added_total()}")
    check("refs 一致", st.refs == nd.ref_dirty_total(),
          f"{st.refs} vs {nd.ref_dirty_total()}")
    check("lists 一致", st.lists == nd.ref_list_dirty_total(),
          f"{st.lists} vs {nd.ref_list_dirty_total()}")
    check("invalidate 之后重算仍然一致",
          (h.invalidate(), h.stats() == st)[1])

    # ================================================================
    print("\n=== 11. 随机序列：退到底再进到头，对象图全等 ===")
    nd, h = reseat(nd)
    rng = random.Random(20261015)
    base = objgraph(nd)
    ammo = nd.entries(AMMO_CLASS)
    holders = [e for e in nd.entries("TBlindageProperties") if e.refs]
    addable = [n for n, k, c in nd.class_info(AMMO_CLASS).fields if k == "float"]

    ops = 0
    for _ in range(40):
        kind = rng.choice(["field", "field", "field", "ref", "add", "bulk"])
        if kind == "field":
            f = rng.choice(ammo[:200]).fields.get(RANGE)
            if f is None:
                continue
            f.leaf["value"] = float(rng.choice([1500, 1750, 1925, 2100, 2600]))
        elif kind == "ref" and holders:
            rf = rng.choice(list(rng.choice(holders).refs.values()))
            cands = nd.ref.candidates_for_class(rf.target_class)
            if len(cands) < 2:
                continue
            nd.ref.repoint(rf.leaf, "obj", rng.choice(cands).key)
        elif kind == "add":
            e = rng.choice(ammo[:200])
            name = rng.choice(addable)
            if name in e.fields:
                if e.fields[name].added:
                    nd.remove_field(e, name)
                else:
                    continue
            else:
                nd.add_field(e, name, rng.choice([1.0, 2.5, 7.0]))
        else:
            q = Q.Query([Q.Condition("ProjectileType", Q.MATCH_IN,
                                     values=(rng.choice([0, 1, 2, 3, 4, 5]),),
                                     kind="int")])
            if not Q.apply_value(ammo, q, RANGE,
                                 float(rng.choice([1200, 2400])),
                                 "float").changed:
                continue
        h.commit()
        ops += 1

    final = objgraph(nd)
    n_undo, _n_redo = h.depth()
    check(f"{ops} 次操作被合并成 {n_undo} 步", n_undo > 0, str(h.depth()))

    undone = 0
    while h.can_undo:
        h.undo()
        undone += 1
    check("撤销步数与记录一致", undone == n_undo, f"{undone} vs {n_undo}")
    check("退到底后统计归零", h.stats() == Stats(), str(h.stats()))
    check("★ 退到底后对象图与打开时逐字节相同", objgraph(nd) == base)

    redone = 0
    while h.can_redo:
        h.redo()
        redone += 1
    check("重做步数一致", redone == n_undo, f"{redone} vs {n_undo}")
    check("★ 重做到头后对象图与改完时逐字节相同", objgraph(nd) == final)

    print("\n--- 11b. 只退一半再继续改，仍然自洽 ---")
    for _ in range(n_undo // 2):
        h.undo()
    mid = objgraph(nd)
    e12 = rng.choice(ammo[:200])
    if RANGE in e12.fields:
        e12.fields[RANGE].leaf["value"] = 1234.0
        h.commit()
        check("中途改动后有可撤销", h.can_undo)
        check("中途改动清空了重做分支", not h.can_redo)
        h.undo()
        check("撤销后回到中途那一点", objgraph(nd) == mid)
    while h.can_undo:
        h.undo()
    check("★ 继续退到底仍是打开时的样子", objgraph(nd) == base)

    # ================================================================
    print("\n=== 12. 界面：按钮、状态栏、快捷键 ===")
    win = MainWindow()
    win.resize(1400, 820)
    win.load_path(SRC / "Weapon.ndfbin")
    app.processEvents()
    check("刚打开时撤销按钮禁用", not win.btn_undo.isEnabled())
    check("刚打开时重做按钮禁用", not win.btn_redo.isEnabled())
    check("撤销按钮说明了为什么不能点",
          "没有可撤销" in win.btn_undo.toolTip(), win.btn_undo.toolTip())

    i = next(i for i, c in enumerate(win.nd.classes) if c.name == AMMO_CLASS)
    win.cls.setCurrentRow(i)
    app.processEvents()
    win.objs.setCurrentRow(0)
    app.processEvents()
    entry = win.objs.entry_at(0)
    fname = next(n for n, f in entry.fields.items()
                 if f.kind == "float" and not f.added)
    box = win.panel._editors[fname]
    old = float(box.value())
    box.setValue(old + 2.0)
    app.processEvents()
    check("改一处后撤销按钮亮了", win.btn_undo.isEnabled())
    check("撤销按钮的 tooltip 说要退什么",
          "撤销" in win.btn_undo.toolTip()
          and "Fx Power" in win.btn_undo.toolTip(), win.btn_undo.toolTip())
    check("撤销按钮 tooltip 带快捷键",
          "Ctrl+Z" in win.btn_undo.toolTip(), win.btn_undo.toolTip())
    check("状态栏说已改动", "已改动" in win.status.text(), win.status.text())

    win.on_undo()
    app.processEvents()
    check("撤销后格子里的数字回原样",
          abs(float(win.panel._editors[fname].value()) - old) < 1e-6,
          str(win.panel._editors[fname].value()))
    check("撤销后保存按钮禁用", not win.btn_save.isEnabled())
    check("撤销后状态栏不再是已改动", "已改动" not in win.status.text(),
          win.status.text())
    check("撤销后状态栏说明撤了什么", "已撤销" in win.status.text(),
          win.status.text())
    check("撤销后重做按钮亮了", win.btn_redo.isEnabled())

    win.on_redo()
    app.processEvents()
    check("重做后值回来了",
          abs(float(win.panel._editors[fname].value()) - (old + 2.0)) < 1e-6,
          str(win.panel._editors[fname].value()))
    check("重做后保存按钮亮了", win.btn_save.isEnabled())
    check("重做后状态栏说明重做了什么", "已重做" in win.status.text(),
          win.status.text())

    print("\n--- 12b. 快捷键 ---")
    keys = [s.key().toString() for s in win.findChildren(QShortcut)]
    check("注册了 Ctrl+Z", "Ctrl+Z" in keys, str(keys))
    check("注册了 Ctrl+Y", "Ctrl+Y" in keys, str(keys))
    check("额外注册了 Ctrl+Shift+Z", "Ctrl+Shift+Z" in keys, str(keys))

    print("\n--- 12c. 面板「撤销这一条」现在会刷新界面（原来的 bug） ---")
    check("前提：现在是脏的", win.nd.dirty_count() >= 1,
          str(win.nd.dirty_count()))
    win.panel._revert_current()
    app.processEvents()
    check("面板撤销后 dirty 归零", win.nd.dirty_count() == 0,
          str(win.nd.dirty_count()))
    check("★ 状态栏不再谎报已改动", "已改动" not in win.status.text(),
          win.status.text())
    check("★ 保存按钮已禁用", not win.btn_save.isEnabled())
    check("★ 放弃改动按钮已禁用", not win.btn_reload.isEnabled())
    # 「撤销这一条」自己是历史里独立的一步——退它要用 Ctrl+Z，不是 Ctrl+Y。
    check("★ 这一步也进了历史", win.btn_undo.isEnabled())

    print("\n--- 12d. 「撤销这一条」可以再用 Ctrl+Z 退回来 ---")
    win.on_undo()
    app.processEvents()
    check("★ 撤销「撤销这一条」把改动找回来了", win.nd.dirty_count() == 1,
          str(win.nd.dirty_count()))
    check("格子里的数字也回来了",
          abs(float(win.panel._editors[fname].value()) - (old + 2.0)) < 1e-6,
          str(win.panel._editors[fname].value()))
    win.on_redo()
    app.processEvents()
    check("重做把「撤销这一条」又执行了一遍", win.nd.dirty_count() == 0,
          str(win.nd.dirty_count()))

    print("\n--- 12e. 弹药页批量改在界面上也是一步 ---")
    win2 = MainWindow()
    win2.resize(1400, 820)
    win2.load_path(SRC / "Weapon.ndfbin")
    app.processEvents()
    dlg = win2.open_ammo_editor()
    app.processEvents()
    row0 = dlg.rows[0]
    row0.set_field("ProjectileType")
    app.processEvents()
    set_multi(row0, {"Shell"})
    app.processEvents()
    row1 = dlg.add_row()
    app.processEvents()
    row1.set_field(RANGE)
    app.processEvents()
    row1.value._single.setValue(1925)
    app.processEvents()
    dlg.bulk_field.setCurrentIndex(dlg.bulk_field.findData(RANGE))
    dlg.bulk_value.setValue(2100)
    app.processEvents()
    before_used = sum(1 for e in dlg.entries
                      if e.fields.get(RANGE) and e.fields[RANGE].value == 2100.0)
    dlg.on_apply()
    app.processEvents()
    check("批量改后主界面撤销按钮亮了", win2.btn_undo.isEnabled())
    check("批量改记成一步", win2.history.depth() == (1, 0),
          str(win2.history.depth()))
    check("说明里是 150 个对象", "150 个对象" in win2.history.undo_label(),
          win2.history.undo_label())
    win2.on_undo()
    app.processEvents()
    back = sum(1 for e in dlg.entries
               if e.fields.get(RANGE) and e.fields[RANGE].value == 2100.0)
    check("★ 撤销批量后回到改前", back == before_used,
          f"{back} vs {before_used}")
    check("弹药页表格跟着刷新了", dlg.table.rowCount() > 0,
          str(dlg.table.rowCount()))
    check("弹药页的命中句还有内容", dlg.hit_label.text() != "")
    win2._close_tool_windows()

    print("\n--- 12f. 保存后历史清空 ---")
    tmp = Path(tempfile.mkdtemp(prefix="ndfhist_"))
    try:
        work = tmp / "Weapon.ndfbin"
        shutil.copy2(SRC / "Weapon.ndfbin", work)
        win3 = MainWindow()
        win3.load_path(work)
        app.processEvents()
        i3 = next(i for i, c in enumerate(win3.nd.classes)
                  if c.name == AMMO_CLASS)
        win3.cls.setCurrentRow(i3)
        app.processEvents()
        win3.objs.setCurrentRow(0)
        app.processEvents()
        e13 = win3.objs.entry_at(0)
        n13 = next(n for n, f in e13.fields.items()
                   if f.kind == "float" and not f.added)
        win3.panel._editors[n13].setValue(
            float(win3.panel._editors[n13].value()) + 1.0)
        app.processEvents()
        check("保存前可撤销", win3.btn_undo.isEnabled())
        win3.on_save()
        app.processEvents()
        check("保存后清空历史", not win3.btn_undo.isEnabled()
              and not win3.btn_redo.isEnabled())
        check("保存后不再是脏状态", win3.nd.dirty_count() == 0,
              str(win3.nd.dirty_count()))
        check("保存后拿到的是一份新历史", win3.nd.history.depth() == (0, 0),
              str(win3.nd.history.depth()))
        check("保存后按钮 tooltip 回到初始文案",
              "没有可撤销" in win3.btn_undo.toolTip(),
              win3.btn_undo.toolTip())
        check("磁盘上真的写了", work.stat().st_size > 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ================================================================
    print("\n" + "=" * 54)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：")
        for name in FAIL:
            print(f"  - {name}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
