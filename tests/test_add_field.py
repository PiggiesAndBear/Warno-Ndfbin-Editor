"""补加字段（add_field）的专项测试。

用户需求：覆盖率不足 100% 的字段，应该允许添加进没有该字段的对象。
这条需求背后的技术前提是「NDF 属性能否后加」——已用探针实测可行，
这里把它固化成回归测试。
"""

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from PySide6.QtWidgets import QApplication                # noqa: E402

from ndfbin_decks.ndfedit.gui import MainWindow             # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument         # noqa: E402
from ndfbin_decks.core import cndf_codec, writer            # noqa: E402

SRC = _env.gfx()
AMMO = "TAmmunitionDescriptor"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    app = QApplication.instance() or QApplication([])

    print("\n=== 1. 找一个「部分覆盖」的字段作为样本 ===")
    nd = NdfDocument.load(SRC / "Weapon.ndfbin")
    info = nd.class_info(AMMO)
    partial = [(n, k, c) for n, k, c in info.fields if 0.05 < c < 0.95]
    check(f"弹药类有 {len(partial)} 个部分覆盖字段", len(partial) > 5)
    for n, k, c in partial[:5]:
        print(f"       {n:32s} {k:6s} {c * 100:5.1f}%")

    fname, fkind, fcov = partial[0]
    entries = nd.entries(AMMO)
    withf = [e for e in entries if fname in e.fields]
    without = [e for e in entries if fname not in e.fields]
    print(f"\n       样本字段 {fname}: 有 {len(withf)} / 缺 {len(without)}")
    check(f"{fname} 同时存在有/无两类对象",
          len(withf) > 0 and len(without) > 0)

    print("\n=== 2. 模板取值（同类中位数）===")
    tpl = nd.field_template(AMMO, fname)
    check("拿到模板", tpl is not None)
    if tpl:
        check("模板带 type_id", "type_id" in tpl.leaf, str(tpl.leaf.get("type_id")))
        donor = withf[0]
        print(f"       模板值 {tpl.leaf['value']}，"
              f"供体值 {donor.fields[fname].value}")

    print("\n=== 3. add_field / remove_field ===")
    victim = without[0]
    before_props = len(nd._object_by_id(victim.object_id)["properties"])
    check("目标对象确实缺该字段", fname not in victim.fields)
    fld = nd.add_field(victim, fname)
    check("add_field 返回 Field", fld is not None)
    check("marked as added", fld.added is True)
    check("进入 entry.fields", fname in victim.fields)
    after_props = len(nd._object_by_id(victim.object_id)["properties"])
    check(f"对象属性数 {before_props} -> {after_props}",
          after_props == before_props + 1)
    check("计入 added_total", nd.added_total() >= 1, str(nd.added_total()))
    check("计入 dirty_count（未保存状态）", nd.dirty_count() >= 1)

    print("\n=== 4. 类型转换（低代码用户会乱填）===")
    victim2 = without[1]
    if fkind == "float":
        f2 = nd.add_field(victim2, fname, value=42)      # 整数填进 float 字段
        check(f"整数 42 填入 float 字段 -> {f2.value}",
              isinstance(f2.value, float) and f2.value == 42.0,
              f"{f2.value!r} ({type(f2.value).__name__})")
    else:
        f2 = nd.add_field(victim2, fname, value=9.7)     # 小数填进 int 字段
        check(f"小数 9.7 填入 {fkind} 字段 -> {f2.value}",
              isinstance(f2.value, int), f"{f2.value!r}")

    print("\n=== 5. 编码往返后新属性稳定保留 ===")
    fld.leaf["value"] = fld.leaf["value"]      # 保持模板值
    if not isinstance(fld.leaf["value"], bool):
        fld.leaf["value"] = fld.leaf["value"]
    repl = writer.sections_of(nd.doc)
    repl["OBJE"] = cndf_codec.encode_objects(nd.decoded["objects"])
    out = writer.write_bytes(nd.doc, repl)
    doc2, dec2 = cndf_codec.decode(out, source="<rebuilt>")

    v2 = next(o for o in dec2["objects"] if o["id"] == victim.object_id)
    got = [p for p in v2["properties"] if p["property_name"] == fname]
    check(f"往返后 {fname} 仍在", len(got) == 1)
    if got:
        check("类型未变",
              got[0]["value"]["type_id"] == tpl.leaf["type_id"],
              f"{got[0]['value']['type_id']:#06x}")
        check("值一致",
              got[0]["value"]["value"] == fld.leaf["value"],
              f"{got[0]['value']['value']} vs {fld.leaf['value']}")
    check("其它对象未受影响",
          len(dec2["objects"]) == len(nd.decoded["objects"]),
          f"{len(dec2['objects'])} vs {len(nd.decoded['objects'])}")

    print("\n=== 6. remove_field 与 reset ===")
    check("remove_field 返回 True", nd.remove_field(victim, fname) is True)
    check("字段已从 entry 摘除", fname not in victim.fields)
    check("对象属性数回落",
          len(nd._object_by_id(victim.object_id)["properties"]) == before_props)
    check("remove 不存在的 added 字段返回 False",
          nd.remove_field(victim, fname) is False)

    # 清掉第 4 节留下的补加字段，否则后面的「归零」断言不成立
    nd.remove_field(victim2, fname)

    # reset 必须把 added 字段整个摘掉，而不只是恢复原值
    victim3 = without[2]
    nd.add_field(victim3, fname)
    victim3.reset()
    check("reset 摘掉了补加的字段", fname not in victim3.fields)
    check("reset 后 added_total 归零", nd.added_total() == 0,
          str(nd.added_total()))
    check("reset 后 dirty_count 归零", nd.dirty_count() == 0)

    print("\n=== 7. UI：缺字段时露出「+ 添加」===")
    work = SRC / "Weapon_addtest.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", work)
    try:
        win = MainWindow()
        win.load_path(work)
        check("面板已注入 document", win.panel._document is not None)

        idx = next(i for i, c in enumerate(win.nd.classes)
                   if c.name == AMMO)
        win.cls.setCurrentRow(idx)
        app.processEvents()
        check("构建了添加按钮", len(win.panel._add_buttons) > 0,
              str(len(win.panel._add_buttons)))

        # 找一个缺 fname 的对象
        row = next((i for i, e in enumerate(win.objs._entries)
                    if fname not in e.fields), None)
        check("找到缺该字段的对象", row is not None)
        if row is not None:
            win.objs.setCurrentRow(row)
            app.processEvents()
            btn = win.panel._add_buttons[fname]
            check("缺字段时按钮可见", not btn.isHidden())
            check("按钮文案为「+ 添加」", btn.text() == "+ 添加", btn.text())
            box = win.panel._editors[fname]
            check("输入框被禁用", not box.isEnabled())

            # 点添加
            btn.click()
            app.processEvents()
            entry = win.objs.entry_at(row)
            check("点击后字段已加入", fname in entry.fields)
            check("输入框已启用", win.panel._editors[fname].isEnabled())
            check("按钮变为「− 移除」",
                  win.panel._add_buttons[fname].text() == "− 移除",
                  win.panel._add_buttons[fname].text())
            check("保存按钮已启用", win.btn_save.isEnabled())
            check("状态栏提到补加",
                  "补加" in win.status.text(), win.status.text())

            # 有字段时按钮应隐藏
            row2 = next(i for i, e in enumerate(win.objs._entries)
                        if fname in e.fields)
            win.objs.setCurrentRow(row2)
            app.processEvents()
            check("原生有该字段时按钮隐藏",
                  win.panel._add_buttons[fname].isHidden())

            # 移除回去
            win.objs.setCurrentRow(row)
            app.processEvents()
            win.panel._add_buttons[fname].click()
            app.processEvents()
            check("移除后字段消失", fname not in win.objs.entry_at(row).fields)
            check("移除后保存按钮禁用", not win.btn_save.isEnabled())
    finally:
        work.unlink(missing_ok=True)
        Path(str(work) + ".bak").unlink(missing_ok=True)

    print("\n=== 8. 带补加字段真实保存到磁盘 ===")
    work2 = SRC / "Weapon_addsave.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", work2)
    try:
        nd2 = NdfDocument.load(work2)
        target = next(e for e in nd2.entries(AMMO)
                      if "MaximumRangeHelicopterGRU" not in e.fields)
        nd2.add_field(target, "MaximumRangeHelicopterGRU", value=5000.0)
        res = nd2.save()
        check("保存成功", res.size_after != res.size_before,
              f"{res.size_before}->{res.size_after}")
        nd3 = NdfDocument.load(work2)
        t2 = next(e for e in nd3.entries(AMMO)
                  if e.object_id == target.object_id)
        got = t2.fields.get("MaximumRangeHelicopterGRU")
        check("重读后新字段存在且值为 5000",
              got is not None and got.value == 5000.0,
              f"{got.value if got else None}")
    finally:
        work2.unlink(missing_ok=True)
        Path(str(work2) + ".bak").unlink(missing_ok=True)

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