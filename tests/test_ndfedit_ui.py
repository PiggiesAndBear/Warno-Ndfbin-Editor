"""通用 ndfbin 编辑器的冒烟测试。

**重点不是 Weapon.** 用户明确要求 UI 兼容所有 ndfbin，所以这里对
Weapon / Unit / Deck / Division 四个差异极大的文件都跑一遍，
证明 Schema 驱动的内核真的与文件无关。
"""

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtWidgets import QApplication                    # noqa: E402

from ndfbin_decks.ndfedit.gui import MainWindow                 # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument             # noqa: E402
from ndfbin_decks.core import writer                            # noqa: E402

SRC = _env.gfx()

#: 挑差异最大的四个文件：对象数从 18到 77923，类从 5 到 102
FILES = ["Weapon.ndfbin", "Unit.ndfbin", "Deck.ndfbin", "Division.ndfbin"]

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    app = _env.qt_app()

    print("\n=== 1. Schema 对多个文件都能自动发现字段 ===")
    docs = {}
    for f in FILES:
        p = SRC / f
        if not p.exists():
            check(f"{f}存在", False)
            continue
        nd = NdfDocument.load(p)
        docs[f] = nd
        editable = [c for c in nd.classes if c.field_count > 0]
        check(f"{f}: 类 {len(nd.classes)}，其中 {len(editable)} 个含可改数值",
              len(nd.classes) > 0)
        if editable:
            top = max(editable, key=lambda c: c.field_count)
            print(f"       最大类 {top.name} ×{top.count}，{top.field_count} 字段")
    check("四个文件全部解析成功", len(docs) == len(FILES), f"{len(docs)}/{len(FILES)}")

    print("\n=== 2. 弹药字段与射程覆盖率（数据驱动，不是白名单）===")
    wnd = docs.get("Weapon.ndfbin")
    if wnd:
        from ndfbin_decks.ndfedit.ammo import load_with_ammo_titles
        wnd = load_with_ammo_titles(SRC / "Weapon.ndfbin")
        info = wnd.class_info("TAmmunitionDescriptor")
        check("弹药类被发现", info is not None)
        if info:
            names = {n for n, _, _ in info.fields}
            # 8 对射程里，只有真实存在的才会出现（覆盖率 > 0）
            ranges = {n: c for n, k, c in info.fields if "Range" in n}
            print(f"       射程类字段 {len(ranges)} 个：")
            for n, c in sorted(ranges.items(), key=lambda x: -x[1]):
                print(f"         {n:34s} {c * 100:5.1f}%")
            check("射程字段数量 > 2（不止最初统计的 2 个）", len(ranges) > 2,
                  f"{len(ranges)} 个")
            check("PhysicalDamages 在字段列表里", "PhysicalDamages" in names)
            # loc_hash 字段原先被整体挡在编辑器外；现在它们以「哈希字段」
            # 的形式开放了（值不可逆，只能照抄同类对象已用过的值）——
            # 所以 Name 现在**应该**在字段表里，而 DescriptorId（guid）
            # 仍然不能动。
            check("loc_hash 字段 Name 已开放为哈希字段", "Name" in names)
            check("DescriptorId 未被暴露", "DescriptorId" not in names)

    print("\n=== 3. 弹药标题不是装备名 ===")
    if wnd:
        es = wnd.entries("TAmmunitionDescriptor")
        check(f"弹药 {len(es)} 条",
              len(es) == wnd.class_info("TAmmunitionDescriptor").count,
              str(len(es)))
        with_sub = [e for e in es if e.subtitle]
        check("每条弹药都有副标题（装备归属）", len(with_sub) == len(es))
        # 标题里不应直接出现装备名
        bad = [e for e in es[:200]
               if e.title and e.subtitle
               and any(w in e.title for w in e.subtitle.replace("装备：", "")
                       .split("、")[:2])]
        check("标题未直接复用装备名", not bad, f"{len(bad)} 例可疑")
        for e in es[:4]:
            print(f"       {e.title:32s} | {e.subtitle[:52]}")
        multi = [e for e in es if e.ref_count > 1]
        check(f"共用弹药 {len(multi)} 条被识别", len(multi) == 945)

    print("\n=== 4. GUI 打开多个文件 ===")
    win = MainWindow()
    check("窗口构建", win is not None)
    for f in ["Weapon.ndfbin", "Unit.ndfbin", "Division.ndfbin"]:
        if not (SRC / f).exists():
            continue
        win.load_path(SRC / f)
        check(f"{f}: 类列表 {win.cls.count} 项", win.cls.count > 0)
        # 选中第一个有字段的类
        idx = next((i for i, c in enumerate(win.nd.classes)
                    if c.field_count > 0), None)
        if idx is not None:
            win.cls.setCurrentRow(idx)
            app.processEvents()
            check(f"{f}: 对象列表 {win.objs.count} 项", win.objs.count > 0)
            # 字段编辑器 + 引用选择按钮，两者合起来才是「能改的东西」。
            # 有些类（如 TBlindageProperties）全是引用字段，一个数值框都没有。
            n_edit = (len(win.panel._editors)
                      + len(win.panel._ref_buttons))
            check(f"{f}: 字段编辑器 {n_edit} 个", n_edit > 0)

    print("\n=== 5. 编辑并保存 Unit.ndfbin（证明非 Weapon 也成立）===")
    work = SRC / "Division_uitest.ndfbin"
    shutil.copy2(SRC / "Division.ndfbin", work)
    try:
        win.load_path(work)
        info = next(c for c in win.nd.classes
                    if any(k not in ("objref", "pathref")
                           for _, k, _ in c.fields))
        win.cls.setCurrentRow(win.nd.classes.index(info))
        app.processEvents()
        check("Division 选中类有对象", win.objs.count > 0)
        win.objs.setCurrentRow(0)
        app.processEvents()

        name = next(iter(win.panel._editors))
        box = win.panel._editors[name]
        entry = win.objs.entry_at(0)
        if name in entry.fields and isinstance(box, type(box)):
            old = entry.fields[name].value
            if hasattr(box, "setChecked"):
                box.setChecked(not box.isChecked())
                new = not bool(old)
            elif hasattr(box, "setValue"):
                if isinstance(box.value(), int):
                    box.setValue(int(old) + 3)
                    new = int(old) + 3
                else:
                    box.setValue(float(old) + 1.5)
                    new = float(old) + 1.5
            app.processEvents()
            check(f"Division 编辑生效 {name}: {old} → {new}",
                  entry.fields[name].value == new,
                  f"实际 {entry.fields[name].value}")
            check("保存按钮已启用", win.btn_save.isEnabled())

            size_before = work.stat().st_size
            prev = win.nd.dry_run()
            check("dry_run 不碰磁盘", work.stat().st_size == size_before)
            res = win.nd.save()
            check(f"Division 保存成功 {res.size_before}→{res.size_after}",
                  res.size_after != res.size_before or res.byte_identical)
            check("生成 .bak", res.backup is not None and res.backup.exists())
            nd2 = NdfDocument.load(work)
            check("重读对象数不变",
                  len(nd2.decoded["objects"]) ==
                  len(NdfDocument.load(SRC / "Division.ndfbin").decoded["objects"]))
        else:
            check("Division 找到可编辑字段", False)
    finally:
        work.unlink(missing_ok=True)
        Path(str(work) + ".bak").unlink(missing_ok=True)


    print("\n=== 6. bool 字段可编辑（上一版漏掉的分支）===")
    # TTurretTwoAxisDescriptor 有 ShowDispersion(bool) 等 bool 字段，
    # 而 QCheckBox 没有 setMinimum——这个分支曾经崩过。
    wwork = SRC / "Weapon_uitest.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", wwork)
    try:
        win.load_path(wwork)
        ti = next(i for i, c in enumerate(win.nd.classes)
                  if c.name == "TAmmunitionDescriptor")
        win.cls.setCurrentRow(ti)
        app.processEvents()
        info = win.nd.class_info("TAmmunitionDescriptor")
        bools = [n for n, k, _ in info.fields if k == "bool"]
        check(f"弹药类有 {len(bools)} 个 bool 字段", len(bools) > 0)

        # 找一条真的带 bool 字段的对象
        row = None
        for i, e in enumerate(win.objs._entries):
            if any(n in e.fields for n in bools):
                row = i
                break
        check("找到带 bool 字段的对象", row is not None)
        if row is not None:
            win.objs.setCurrentRow(row)
            app.processEvents()
            entry = win.objs.entry_at(row)
            bname = next(n for n in bools if n in entry.fields)
            box = win.panel._editors[bname]
            check(f"{bname} 渲染为 QCheckBox",
                  type(box).__name__ == "QCheckBox", type(box).__name__)
            old = bool(entry.fields[bname].value)
            box.setChecked(not old)
            app.processEvents()
            check(f"bool 切换生效 {old} -> {not old}",
                  bool(entry.fields[bname].value) == (not old),
                  f"实际 {entry.fields[bname].value}")
            check("保存按钮已启用", win.btn_save.isEnabled())
            # 缺字段的框不应崩溃
            missing = [n for n in win.panel._editors
                       if n not in entry.fields]
            if missing:
                mb = win.panel._editors[missing[0]]
                check("缺字段的 bool 框已禁用", not mb.isEnabled())
            win.panel._revert_current()
            app.processEvents()
            check("撤销恢复 bool 原值",
                  bool(entry.fields[bname].value) == old)
    finally:
        wwork.unlink(missing_ok=True)
        Path(str(wwork) + ".bak").unlink(missing_ok=True)

    print("\n=== 7. 弹药标题不会被退化成裸 ID ===")
    if wnd:
        from ndfbin_decks.ndfedit.gui import MainWindow as _MW
        win.load_path(SRC / "Weapon.ndfbin")
        idx = next(i for i, c in enumerate(win.nd.classes)
                   if c.name == "TAmmunitionDescriptor")
        win.cls.setCurrentRow(idx)
        app.processEvents()
        titles = [e.title for e in win.objs._entries[:50]]
        naked = [t for t in titles if t.startswith("#")]
        check("列表标题不是裸 object_id", not naked,
              f"{len(naked)}/{len(titles)} 个退化了")
        check("标题含弹药类型标识",
              any("Missile" in t or "Shell" in t or "Rocket" in t
                  or "Bomb" in t for t in titles), str(titles[:3]))

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：")
        for n in FAIL:
            print(f"  - {n}")
    print("=" * 52)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())