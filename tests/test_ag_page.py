# -*- coding: utf-8 -*-
"""AG 牌组属性页（P0 只读）的验收测试。

标准直接来自 `doc/AG牌组属性编辑-实现计划.md` §6 的 P0 段：
**分布数字必须与移交文档逐位一致**。对不上就说明投影层读错了模块 ——
而这类错误不会抛异常，只会让界面安静地显示错的数。

    python -m tests.test_ag_page
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtWidgets import QApplication               # noqa: E402

from ndfbin_decks.ndfedit import ag_view                 # noqa: E402
from ndfbin_decks.ndfedit.ag_page import (               # noqa: E402
    AgPage, movement_label)
from ndfbin_decks.ndfedit.schema import NdfDocument      # noqa: E402

PAWN = _env.mod_dir(_env.BERLIN) / "Gen" / "NDF" / "GFX" / "Pawn.ndfbin"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def main() -> int:
    app = _env.qt_app()
    if not PAWN.is_file():
        _env.skip(f"这个 mod 没有 Pawn.ndfbin（{PAWN}）")

    nd = NdfDocument.load(PAWN)
    pawns = ag_view.build_pawns(nd)

    # ---- 1. 投影层 ----
    print("=== 1. 投影层读出来的分布（对照移交文档 §4） ===")
    check("营数 2268", len(pawns) == 2268, str(len(pawns)))
    check("有 DeckIdentifier 的 2265",
          sum(1 for p in pawns if p.identifier) == 2265,
          str(sum(1 for p in pawns if p.identifier)))

    ap = Counter(p.values.get("action_points")
                 for p in pawns if "action_points" in p.values)
    check("行动点 12/5/4 = 1676/473/116",
          ap == {12.0: 1676, 5.0: 473, 4.0: 116},
          str(ap.most_common(3)))

    mv = Counter(p.values.get("movement")
                 for p in pawns if "movement" in p.values)
    check("移动方式 6 种，机械化 1618",
          len(mv) == 6 and mv.get(
              "StrategicMovementDescriptor_mecanized") == 1618,
          f"{len(mv)} 种，机械化 {mv.get('StrategicMovementDescriptor_mecanized')}")
    check("移动方式覆盖 2265", sum(mv.values()) == 2265, str(sum(mv.values())))

    ic = Counter(p.values.get("icon") for p in pawns if "icon" in p.values)
    check("部队类型 17 种（RTS_H_ 变体已归一）", len(ic) == 17, str(len(ic)))
    check("howitzer 425 / apc 364", ic.get("howitzer") == 425
          and ic.get("apc") == 364,
          f"howitzer={ic.get('howitzer')} apc={ic.get('apc')}")

    sr = Counter(p.values.get("support_radius")
                 for p in pawns if "support_radius" in p.values)
    check("支援半径只 582 个营有（缺是常态）", sum(sr.values()) == 582,
          str(sum(sr.values())))
    check("支援半径 10.0 → 411，-1.0 → 111",
          sr.get(10.0) == 411 and sr.get(-1.0) == 111)

    # ---- 2. 名称维度 ----
    print()
    print("=== 2. 名字切分与国家维度 ===")
    check("pion 型 2207（其余 61 是 AGF 设施 + 无 identifier 的）",
          sum(1 for p in pawns if p.is_unit) == 2207,
          str(sum(1 for p in pawns if p.is_unit)))
    agf = [p for p in pawns if p.kind == "AGF"]
    check("AGF 不把哈希当国家", all(not p.name_country for p in agf),
          f"{len(agf)} 个 AGF")

    # 段数不固定，但切法必须稳定
    lens = Counter(len(p.identifier.split("_")) for p in pawns if p.identifier)
    check("段数分布 2/3/4/5/6 = 58/244/811/1141/11",
          lens == {2: 58, 3: 244, 4: 811, 5: 1141, 6: 11},
          str(dict(sorted(lens.items()))))

    kind, country, unit, parents = ag_view.parse_identifier(
        "pion_SOV_8GA_34ArtDiv_4_1")
    check("6 段切分：末段是营、中间是上级链",
          (kind, country, unit, parents)
          == ("pion", "SOV", "1", ("8GA", "34ArtDiv", "4")),
          str((kind, country, unit, parents)))
    check("3 段切分：没有上级",
          ag_view.parse_identifier("pion_BEL_11Sq")[3] == ())

    countries = ag_view.choices(pawns, "country")
    check("国家前缀 12 种", len(countries) == 12, str(len(countries)))
    check("SOV 最多", countries[0][0] == "SOV", str(countries[:3]))

    # ---- 3. 页面 ----
    print()
    print("=== 3. 专用页 ===")
    page = AgPage(nd)
    units = sum(1 for p in pawns if p.is_unit)
    check("默认只显示部队（营），不混入机场/补给站",
          page.table.rowCount() == units,
          f"{page.table.rowCount()} vs {units}")
    page.f_kind.setCurrentIndex(2)             # 第 3 项 = 全部类型
    check("切到「全部类型」后行数 = 全部 pawn",
          page.table.rowCount() == len(pawns), str(page.table.rowCount()))
    page.f_kind.setCurrentIndex(0)             # 回到部队
    check("列数 = 4 名称列 + 投影属性数",
          page.table.columnCount() == 4 + len(ag_view.ATTRS),
          str(page.table.columnCount()))
    check("国家下拉 = 全部 + 12 国", page.f_country.count() == 13,
          str(page.f_country.count()))

    page.f_country.setCurrentIndex(1)          # 第一项是「全部」，1 是 SOV
    sov = page.f_country.currentData()
    check(f"筛 {sov} 后行数与该国计数一致",
          page.table.rowCount() == dict(countries)[sov],
          f"{page.table.rowCount()} vs {dict(countries)[sov]}")

    page.f_country.setCurrentIndex(0)
    page.f_search.setText("595Reco")
    check("搜 595Reco 命中 1 行", page.table.rowCount() == 1,
          str(page.table.rowCount()))
    if page.table.rowCount() == 1:
        check("命中的确实是 595Reco", page.table.item(0, 0).text() == "595Reco",
              page.table.item(0, 0).text())

    page.f_search.clear()
    check("清空搜索后恢复部队全量", page.table.rowCount() == units,
          str(page.table.rowCount()))

    # ---- 3b. 师 / 团两级筛选 ----
    # 早先「上级编制」是一个下拉，按「最靠近营的那一级」取值，等于把师、团、
    # 军混在一起 —— 实测 514 项，长到没法用。现在拆成上级链的第 0 段（师）
    # 与第 1 段（团），并按「国家 → 师 → 团」逐级收窄。
    print()
    print("=== 3b. 上级编制拆成师、团两级 ===")
    check("师下拉第一项是「全部师」，data 为 None",
          page.f_div.itemData(0) is None)
    check("第二项是「国家直属」，data 为空串",
          page.f_div.itemData(1) == "", str(page.f_div.itemData(1)))
    n_div_all = page.f_div.count()
    check("师下拉比原来的一锅端（514 项）短得多", n_div_all < 514,
          f"{n_div_all} 项")

    # 国家 → 师：师下拉的选项只从当前国家池里算
    page.f_country.setCurrentIndex(page.f_country.findData("SOV"))
    check("选国家后师下拉变短", page.f_div.count() < n_div_all,
          f"{n_div_all} → {page.f_div.count()}")
    check("师下拉里的师都属于这个国家",
          all(any(p.division == page.f_div.itemData(i)
                  and p.name_country == "SOV" for p in pawns)
              for i in range(2, page.f_div.count())),
          f"{page.f_div.count()} 项")

    # 师 → 团：团下拉收窄到这个师
    page.f_div.setCurrentIndex(page.f_div.findData("8GA"))
    check("8GA 命中 32 个营", page.table.rowCount() == 32,
          str(page.table.rowCount()))
    check("8GA 的团下拉只有几项（不是全局 377 项）",
          page.f_reg.count() <= 10, f"{page.f_reg.count()} 项")
    check("团下拉里的团都属于 8GA",
          all(any(p.division == "8GA"
                  and p.regiment == page.f_reg.itemData(i) for p in pawns)
              for i in range(1, page.f_reg.count())
              if page.f_reg.itemData(i) != ""),
          f"{page.f_reg.count()} 项")

    page.f_reg.setCurrentIndex(page.f_reg.findData("34ArtDiv"))
    check("8GA / 34ArtDiv 命中 8 行", page.table.rowCount() == 8,
          str(page.table.rowCount()))
    check("命中的行上级列确实以 8GA / 34ArtDiv 开头",
          page.table.rowCount() > 0
          and all(page.table.item(r, 1).text().startswith("8GA / 34ArtDiv")
                  for r in range(page.table.rowCount())),
          page.table.item(0, 1).text() if page.table.rowCount() else "—")

    # 国家直属：连师都没有。与「师直属营」（有师、没团）必须分开。
    page._reset_filter()
    page.f_div.setCurrentIndex(page.f_div.findData(""))
    n_free = page.table.rowCount()
    check("国家直属 = 244 个营", n_free == 244, str(n_free))
    check("它们的上级列全是「—」",
          n_free > 0 and all(page.table.item(r, 1).text() == "—"
                             for r in range(n_free)))

    page._reset_filter()
    page.f_reg.setCurrentIndex(page.f_reg.findData(""))
    n_org = page.table.rowCount()
    check("师直属营 = 811（有师无团，且不含国家直属）", n_org == 811,
          str(n_org))
    check("师直属营的上级列都不为空",
          n_org > 0 and all(page.table.item(r, 1).text() != "—"
                            for r in range(n_org)))
    page._reset_filter()
    check("清空筛选后回到部队全量", page.table.rowCount() == units,
          str(page.table.rowCount()))

    # 排序：行动点列必须是数值序，不是字符串序。
    # 用真实的表头点击信号，而不是直接调私有方法 —— 走的是玩家会走的那条路。
    ap_col = 4 + [a.key for a in ag_view.ATTRS].index("action_points")
    page.table.horizontalHeader().sectionClicked.emit(ap_col)
    first = page.table.item(0, ap_col)
    check("行动点升序第一行是 4（数值序，不是字符串序）",
          first is not None and first.text() == "4",
          first.text() if first else "None")
    # 缺失值的行不论升降序都排在最后：支援半径只有 582 个营有。
    sr_col = 4 + [a.key for a in ag_view.ATTRS].index("support_radius")
    page.table.horizontalHeader().sectionClicked.emit(sr_col)
    last_row = page.table.rowCount() - 1
    check("缺支援半径的行排在最后",
          page.table.item(last_row, sr_col) is not None
          and page.table.item(last_row, sr_col).text() == "—",
          page.table.item(last_row, sr_col).text()
          if page.table.item(last_row, sr_col) else "None")

    check("打开页面不改数据", nd.dirty_count() == 0, str(nd.dirty_count()))

    # ---- 4. 单营核对 ----
    print()
    print("=== 4. 拿一个具体的营核对 ===")
    target = next((p for p in pawns if p.identifier == "pion_SOV_8GA_34ArtDiv_4_1"),
                  None)
    if target is None:
        check("找得到 pion_SOV_8GA_34ArtDiv_4_1", False)
    else:
        check("6 段营的上级链正确", target.parents == ("8GA", "34ArtDiv", "4"),
              str(target.parents))
        check("它的最细上级是 4", target.best_parent() == "4",
              target.best_parent())
        check("有行动点", "action_points" in target.values,
              str(target.values.get("action_points")))
        check("有移动方式", "movement" in target.values,
              str(target.values.get("movement")))

    # ---- 5. 缺字段不显示 0 ----
    print()
    print("=== 5. 缺字段的处理 ===")
    no_support = [p for p in pawns if "support_radius" not in p.values]
    check("有营没有支援半径字段", len(no_support) > 0, str(len(no_support)))
    from ndfbin_decks.ndfedit.ag_page import _fmt
    check("缺字段显示「—」而不是 0", _fmt(None, "float") == "—",
          _fmt(None, "float"))

    print()
    # 显式关掉页面再退出：窗口对象最后会被 Python 回收，而回收发生在
    # 解释器退出阶段，那时 Qt 的静态数据可能已经拆了。先 close 掉能让
    # 析构走在还有 QApplication 的时候（配合 _env.qt_app() 的模块级持有）。
    page.close()
    page.deleteLater()
    app.processEvents()

    if FAIL:
        print(f"FAIL — {len(FAIL)} / {len(PASS) + len(FAIL)} 项未通过：")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
