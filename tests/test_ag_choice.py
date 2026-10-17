# -*- coding: utf-8 -*-
"""AG 属性页的两条新写入路径：换引用、补属性。

与 :mod:`tests.test_ag_edit` 分开是因为**它们不是同一种改动**：

============  ==========================  ============================
路径          动的对象                    典型场景
============  ==========================  ============================
改数值        模块上的一个标量            把 126 个 AA 营的 12 改成 15
**换引用**    模块上的 strg_ref / 载体上  把某营的移动方式改成空中
              的成对纹理名
**补属性**    克隆描述器 + 追加一个字段   给工兵营加炮兵支援半径
============  ==========================  ============================

全部在临时副本上做，不碰 mod 里的原文件。

    python -m tests.test_ag_choice
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

from ndfbin_decks.ndfedit import ag_edit, ag_view        # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument      # noqa: E402

PAWN = _env.mod_dir(_env.BERLIN) / "Gen" / "NDF" / "GFX" / "Pawn.ndfbin"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if detail:
        print(f"        {detail}")


def attr(key: str) -> ag_view.Attr:
    return next(a for a in ag_view.ATTRS if a.key == key)


def fresh(tmp: Path) -> Path:
    dst = tmp / "Pawn.ndfbin"
    shutil.copy2(PAWN, dst)
    return dst


def reload(dst: Path) -> list[ag_view.PawnView]:
    return ag_view.build_pawns(NdfDocument.load(dst))


def by_ident(pawns, ident: str):
    return next(p for p in pawns if p.identifier == ident)


# ---- 一、取值域 ----


def test_choice_values() -> None:
    print("\n=== 1. 可选值全部来自文件里已有的取值 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch1_"))
    try:
        nd = NdfDocument.load(fresh(tmp))
        mv = ag_view.choice_values(nd, attr("movement"))
        ic = ag_view.choice_values(nd, attr("icon"))
        check("移动方式 6 种", len(mv) == 6, f"{mv}")
        check("移动方式都带前缀",
              all(s.startswith("StrategicMovementDescriptor_") for s in mv))
        check("部队类型 17 种", len(ic) == 17, f"{ic}")
        check("图标类型里没有 RTS_H_ 残留",
              not any(k.startswith("RTS_H_") for k in ic), f"{ic}")

        # 每一个可选值都必须真的能在 STRG 表里查到 —— 否则写不进去
        pool = nd.strings_pool
        miss = [s for s in mv if pool.index_of(s) is None]
        check("移动方式的取值都能在字符串表里查到", not miss, f"缺 {miss}")
        pairs = [("Texture_STRATEGIC_RTS_H_" + k, "Texture_STRATEGIC_" + k)
                 for k in ic]
        miss = [t for pair in pairs for t in pair if pool.index_of(t) is None]
        check("图标的两套纹理名都能查到", not miss, f"缺 {miss[:4]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 二、换引用 ----


def test_set_movement() -> None:
    print("\n=== 2. 换移动方式：模块被 1618 个营共用，必须只改一个 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch2_"))
    try:
        dst = fresh(tmp)
        nd = NdfDocument.load(dst)
        users = ag_edit.module_users(nd)
        t = next(p for p in ag_view.build_pawns(nd)
                 if p.values.get("movement")
                 == "StrategicMovementDescriptor_mecanized")
        mod_id = t.modules[ag_view.ATTRS[1].module]
        shared = users.get(mod_id)
        n_before = len(nd.decoded["objects"])

        res = ag_edit.set_choice(nd, t, attr("movement"),
                                 "StrategicMovementDescriptor_montagne",
                                 users=users)
        check("共享模块 → 克隆", res.cloned and shared > 1,
              f"shared={shared} cloned={res.cloned}")
        check("对象数 +1", len(nd.decoded["objects"]) == n_before + 1,
              f"{n_before} → {len(nd.decoded['objects'])}")

        nd.save()
        p2 = reload(dst)
        t2 = by_ident(p2, t.identifier)
        check("重载后这个营是山地",
              t2.values.get("movement")
              == "StrategicMovementDescriptor_montagne",
              str(t2.values.get("movement")))
        still = [p for p in p2 if p.modules.get(ag_view.ATTRS[1].module) == mod_id]
        check("原模块上剩下的营一个都没变",
              all(p.values.get("movement")
                  == "StrategicMovementDescriptor_mecanized" for p in still),
              f"剩 {len(still)} 个营")
        check("机械化总数只少了 1",
              sum(1 for p in p2 if p.values.get("movement")
                  == "StrategicMovementDescriptor_mecanized") == 1617,
              str(sum(1 for p in p2 if p.values.get("movement")
                      == "StrategicMovementDescriptor_mecanized")))

        # 换成一个文件里没有的名字必须被拒绝
        try:
            ag_edit.set_choice(nd, t2, attr("movement"),
                               "StrategicMovementDescriptor_no_such_thing")
            check("不存在的移动方式被拒绝", False)
        except ag_edit.AgEditError as exc:
            check("不存在的移动方式被拒绝", True, str(exc)[:60])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_set_icon() -> None:
    print("\n=== 3. 换部队类型：载体每营独占，不克隆；成对两个纹理要一起改 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch3_"))
    try:
        dst = fresh(tmp)
        nd = NdfDocument.load(dst)
        t = next(p for p in ag_view.build_pawns(nd)
                 if p.values.get("icon") == "howitzer")
        carrier = t.carriers.get("icon")
        check("PawnView 带出了图标载体 id", carrier is not None, str(carrier))
        n_before = len(nd.decoded["objects"])

        res = ag_edit.set_choice(nd, t, attr("icon"), "AA")
        check("不克隆", not res.cloned)
        check("对象数不变", len(nd.decoded["objects"]) == n_before)

        sl = nd.entry_of(carrier).str_lists["Values"]
        check("两个纹理名都换成了 AA",
              sl.texts() == ["Texture_STRATEGIC_RTS_H_AA",
                             "Texture_STRATEGIC_AA"],
              f"{sl.texts()}")

        nd.save()
        p2 = reload(dst)
        check("重载后这个营是 AA",
              by_ident(p2, t.identifier).values.get("icon") == "AA",
              str(by_ident(p2, t.identifier).values.get("icon")))
        check("howitzer 只少了一个（424）",
              sum(1 for p in p2 if p.values.get("icon") == "howitzer") == 424,
              str(sum(1 for p in p2 if p.values.get("icon") == "howitzer")))
        check("AA 变成 132",
              sum(1 for p in p2 if p.values.get("icon") == "AA") == 132,
              str(sum(1 for p in p2 if p.values.get("icon") == "AA")))

        try:
            ag_edit.set_choice(nd, t, attr("icon"), "no_such_kind")
            check("不存在的图标类型被拒绝", False)
        except ag_edit.AgEditError as exc:
            check("不存在的图标类型被拒绝", True, str(exc)[:60])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_choice_inplace() -> None:
    print("\n=== 4. 批量换移动方式：整组命中直接改模块，0 克隆 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch4_"))
    try:
        dst = fresh(tmp)
        nd = NdfDocument.load(dst)
        every = [p for p in ag_view.build_pawns(nd)
                 if p.values.get("movement")
                 == "StrategicMovementDescriptor_montagne"]
        n_before = len(nd.decoded["objects"])
        res = ag_edit.apply_choice(nd, every, attr("movement"),
                                   "StrategicMovementDescriptor_helico")
        check("整组命中不克隆", res.cloned == 0 and res.in_place >= 1,
              f"cloned={res.cloned} in_place={res.in_place} 目标 {len(every)}")
        check("对象数不变", len(nd.decoded["objects"]) == n_before)
        check("全部算改", res.changed == len(every), str(res.changed))

        nd.save()
        p2 = reload(dst)
        check("重载后山地营为 0",
              sum(1 for p in p2 if p.values.get("movement")
                  == "StrategicMovementDescriptor_montagne") == 0)
        check("直升国营多了 4 个",
              sum(1 for p in p2 if p.values.get("movement")
                  == "StrategicMovementDescriptor_helico") == 63,
              str(sum(1 for p in p2 if p.values.get("movement")
                      == "StrategicMovementDescriptor_helico")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 三、补属性 ----


def test_grant() -> None:
    print("\n=== 5. 补属性：给工兵/步兵营加上它本来没有的支援半径 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch5_"))
    try:
        dst = fresh(tmp)
        nd = NdfDocument.load(dst)
        sup = attr("support_radius")
        users = ag_edit.module_users(nd)
        t = next(p for p in ag_view.build_pawns(nd)
                 if p.values.get("icon") == "Infantry"
                 and "support_radius" not in p.values)
        mod_id = t.modules[sup.module]
        n_before = len(nd.decoded["objects"])
        check("靶子确实没有支援半径", "support_radius" not in t.values)
        check("它的描述器被多个营共用", users.get(mod_id, 0) > 1,
              f"#{mod_id} 被 {users.get(mod_id)} 个营共用")

        res = ag_edit.grant_attr(nd, [t], sup, 20.0)
        check("补上了", res.changed == 1 and res.cloned == 1,
              f"changed={res.changed} cloned={res.cloned}")
        check("对象数 +1（克隆了描述器）",
              len(nd.decoded["objects"]) == n_before + 1)

        nd.save()
        p2 = reload(dst)
        t2 = by_ident(p2, t.identifier)
        check("重载后这个营有支援半径 20",
              t2.values.get("support_radius") == 20.0,
              str(t2.values.get("support_radius")))
        has = sum(1 for p in p2 if "support_radius" in p.values)
        check("有支援半径的营从 582 变 583", has == 583, str(has))
        still = [p for p in p2 if p.modules.get(sup.module) == mod_id]
        check("原描述器上剩下的营一个都没多出这个字段",
              all("support_radius" not in p.values for p in still),
              f"剩 {len(still)} 个营")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_grant_existing() -> None:
    print("\n=== 6. 补属性作用在已有该字段的营上 → 退化成改值 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch6_"))
    try:
        dst = fresh(tmp)
        nd = NdfDocument.load(dst)
        sup = attr("support_radius")
        users = ag_edit.module_users(nd)
        t = next(p for p in ag_view.build_pawns(nd)
                 if p.values.get("support_radius") is not None
                 and p.values.get("support_radius") > 0)
        mod_id = t.modules[sup.module]
        res = ag_edit.grant_attr(nd, [t], sup, 25.0)
        # 注意：这里**会**克隆——不是因为它没有字段，而是因为那个描述器
        # 被很多营共用，「改值」本身也要 clone-on-write。所以能断言的只有
        # 「没有走追加字段那条路」，而那一点在数据上表现为：
        # 该营的描述器上这个字段本来就在，克隆体继承它，值被改掉。
        check("改值成功", res.changed == 1, str(res.changed))
        if users.get(mod_id, 1) > 1:
            check("共享描述器 → 改值也克隆（不是在原地改）", res.cloned == 1,
                  f"#{mod_id} 被 {users.get(mod_id)} 个营共用")
        nd.save()
        p2 = reload(dst)
        check("重载后是 25",
              by_ident(p2, t.identifier).values.get("support_radius") == 25.0,
              str(by_ident(p2, t.identifier).values.get("support_radius")))
        still = [p for p in p2 if p.modules.get(sup.module) == mod_id]
        check("原描述器上的其它营没有被改成 25",
              all(p.values.get("support_radius") != 25.0 for p in still),
              f"剩 {len(still)} 个营")
        check("有支援半径的营总数不变（582）",
              sum(1 for p in p2 if "support_radius" in p.values) == 582,
              str(sum(1 for p in p2 if "support_radius" in p.values)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_grant_other_field() -> None:
    print("\n=== 7. 给防空营补支援半径：两个字段共存，防空数量不变 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch7_"))
    try:
        dst = fresh(tmp)
        nd = NdfDocument.load(dst)
        sup = attr("support_radius")
        aa = next(p for p in ag_view.build_pawns(nd)
                  if p.values.get("icon") == "AA"
                  and "support_radius" not in p.values)
        res = ag_edit.grant_attr(nd, [aa], sup, 8.0)
        check("补上了", res.changed == 1, str(res.changed))
        nd.save()
        p2 = reload(dst)
        a2 = by_ident(p2, aa.identifier)
        check("支援半径 8 与防空半径 12 共存",
              a2.values.get("support_radius") == 8.0
              and a2.values.get("air_deny") == 12.0,
              f"支援={a2.values.get('support_radius')} "
              f"防空={a2.values.get('air_deny')}")
        check("防空营总数仍是 130",
              sum(1 for p in p2 if "air_deny" in p.values) == 130,
              str(sum(1 for p in p2 if "air_deny" in p.values)))

        # 不支持补加的字段要被拒绝
        try:
            ag_edit.grant_attr(nd, [a2], attr("action_points"), 9.0)
            check("不可补加的字段被拒绝", False)
        except ag_edit.AgEditError as exc:
            check("不可补加的字段被拒绝", True, str(exc)[:50])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 四、撤销 ----


def test_undo() -> None:
    print("\n=== 8. 两条新路径的撤销 ===")
    tmp = Path(tempfile.mkdtemp(prefix="agch8_"))
    try:
        for name in ("a", "b", "c"):     # 三种场景各用一份独立副本
            (tmp / name).mkdir()
        # 换移动方式 → 撤销
        dst = fresh(tmp / "a")
        nd = NdfDocument.load(dst)
        h = nd.history
        users = ag_edit.module_users(nd)
        t = next(p for p in ag_view.build_pawns(nd)
                 if p.values.get("movement")
                 == "StrategicMovementDescriptor_mecanized")
        n0 = len(nd.decoded["objects"])
        ag_edit.set_choice(nd, t, attr("movement"),
                           "StrategicMovementDescriptor_aerial", users=users)
        h.commit()
        h.undo()
        check("换移动方式撤销后对象数回原",
              len(nd.decoded["objects"]) == n0 and nd.appended_count() == 0,
              f"{len(nd.decoded['objects'])} appended={nd.appended_count()}")
        nd.save()
        check("落盘后仍是机械化",
              by_ident(reload(dst), t.identifier).values.get("movement")
              == "StrategicMovementDescriptor_mecanized")

        # 换图标 → 撤销
        d2 = fresh(tmp / "b")
        nd = NdfDocument.load(d2)
        h = nd.history
        t = next(p for p in ag_view.build_pawns(nd)
                 if p.values.get("icon") == "howitzer")
        ag_edit.set_choice(nd, t, attr("icon"), "AA")
        h.commit()
        h.undo()
        nd.save()
        check("换图标撤销后回到 howitzer",
              by_ident(reload(d2), t.identifier).values.get("icon")
              == "howitzer")
        check("AA 数量回原（131）",
              sum(1 for p in reload(d2) if p.values.get("icon") == "AA") == 131,
              str(sum(1 for p in reload(d2) if p.values.get("icon") == "AA")))

        # 补属性 → 撤销（克隆 + 加字段压在同一步，撤销顺序必须反过来）
        d3 = fresh(tmp / "c")
        nd = NdfDocument.load(d3)
        h = nd.history
        sup = attr("support_radius")
        users = ag_edit.module_users(nd)
        t = next(p for p in ag_view.build_pawns(nd)
                 if p.values.get("icon") == "Infantry"
                 and "support_radius" not in p.values)
        n0 = len(nd.decoded["objects"])
        ag_edit.grant_attr(nd, [t], sup, 20.0)
        h.commit()
        check("补属性记成一步", h.depth() == (1, 0), f"{h.depth()}")
        try:
            h.undo()
            ok = True
            err = ""
        except Exception as exc:                        # noqa: BLE001
            ok, err = False, repr(exc)
        check("撤销「克隆 + 加字段」不抛异常", ok, err)
        check("撤销后对象数回原、字段消失、不再脏",
              len(nd.decoded["objects"]) == n0
              and nd.appended_count() == 0 and nd.dirty_count() == 0,
              f"{len(nd.decoded['objects'])} appended={nd.appended_count()} "
              f"dirty={nd.dirty_count()}")
        nd.save()
        check("落盘后该营没有支援半径",
              "support_radius" not in by_ident(reload(d3), t.identifier).values)
        check("有支援半径的营回 582",
              sum(1 for p in reload(d3) if "support_radius" in p.values) == 582,
              str(sum(1 for p in reload(d3) if "support_radius" in p.values)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 五、界面 ----


def test_ui() -> None:
    print("\n=== 9. 界面：控件按属性切换、补按钮只在该出现时出现 ===")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ndfbin_decks.ndfedit.ag_page import AgPage

    tmp = Path(tempfile.mkdtemp(prefix="agch9_"))
    try:
        dst = fresh(tmp)
        app = _env.qt_app()
        nd = NdfDocument.load(dst)
        page = AgPage(nd)
        # 这一节只测这一页自己的行为，所以不拉整个 MainWindow（那要扫 mod）。
        # 但撤销/提交是由主窗口接的线：`dirtyApplied` → commit、
        # `undoRequested` → undo → refresh。这两条在这里手工接上，
        # 语义与 gui.py 里一致；真实接线由 test_ag_undo.py 覆盖。
        page.dirtyApplied.connect(
            lambda: (nd.history.commit(), page._update_undo_buttons()))
        page.undoRequested.connect(
            lambda: (nd.history.undo(), page.refresh()))
        page.show()
        app.processEvents()

        labels = [page.bulk_attr.itemText(i)
                  for i in range(page.bulk_attr.count())]
        check("批量改里有移动方式", "移动方式" in labels, str(labels[:4]))
        check("批量改里有部队类型", "部队类型" in labels)
        check("批量改里没有国家（它与编制耦合）", "国家" not in labels, str(labels))

        # 数值属性：数字框可见、下拉隐藏、补按钮隐藏
        page.bulk_attr.setCurrentIndex(page.bulk_attr.findData("action_points"))
        app.processEvents()
        check("数值属性用数字框",
              page.bulk_value.isVisible() and not page.bulk_choice.isVisible())
        check("行动点不支持补加", not page.btn_grant.isVisible())
        check("没填值时应用按钮是灰的", not page.btn_apply.isEnabled())

        # 枚举属性：下拉出现，首项「未选」，按钮仍灰
        page.bulk_attr.setCurrentIndex(page.bulk_attr.findData("movement"))
        app.processEvents()
        check("移动方式用下拉",
              page.bulk_choice.isVisible() and not page.bulk_value.isVisible())
        check("下拉有 6 项可选（另加一个未选）",
              page.bulk_choice.count() == 7, str(page.bulk_choice.count()))
        check("默认停在「未选」", page.bulk_choice.currentData() is None,
              str(page.bulk_choice.currentText()))
        check("没选值时应用按钮是灰的", not page.btn_apply.isEnabled())
        page.bulk_choice.setCurrentIndex(1)
        app.processEvents()
        check("选了值之后按钮亮起", page.btn_apply.isEnabled())

        # 支援半径：补按钮出现
        page.bulk_attr.setCurrentIndex(
            page.bulk_attr.findData("support_radius"))
        page.bulk_value.setValue(18.0)
        app.processEvents()
        check("支援半径有补按钮", page.btn_grant.isVisible())
        check("填了值之后补按钮可用", page.btn_grant.isEnabled())

        # 真的补一个
        page.f_search.setText("Security1")
        app.processEvents()
        check("筛到一个营", page.table.rowCount() == 1,
              str(page.table.rowCount()))
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        page.on_grant()
        app.processEvents()
        check("补加有结果说明", "克隆" in page.apply_note.text(),
              page.apply_note.text()[:70])
        check("补加后能撤销", page.btn_undo.isEnabled())

        # 撤销后表格跟着变
        page.undoRequested.emit()
        app.processEvents()
        check("撤销后支援半径不见了",
              all("support_radius" not in p.values for p in page._rows
                  if p.values.get("icon") == "Infantry"
                  and p.identifier.endswith("Security1")),
              str([p.values.get("support_radius") for p in page._rows
                   if p.identifier.endswith("Security1")]))
        page.close()
        page.deleteLater()
        app.processEvents()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if not PAWN.is_file():
        print(f"SKIP — 找不到 {PAWN}")
        return 0
    print(f"样本：{PAWN}")

    test_choice_values()
    test_set_movement()
    test_set_icon()
    test_apply_choice_inplace()
    test_grant()
    test_grant_existing()
    test_grant_other_field()
    test_undo()
    test_ui()

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
