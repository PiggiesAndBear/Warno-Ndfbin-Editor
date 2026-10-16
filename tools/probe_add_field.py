"""核实：能否给缺少某属性的对象「补上」这个属性？

**这不只是 UI 问题。** encode_objects 是逐条写property_id + 值，
理论上可以往 properties 列表里追加。但如果 NDF 的属性是编译期固定布局，
补进去的文件游戏可能直接读不出来。

要回答四件事：
  Q1 PROP 表里这些低覆盖率属性是否都注册过（能不能引用）
  Q2 同一个 property_id 是否被多个 class 共用（补进去是否合法）
  Q3 编码-解码往返后补进去的属性是否稳定保留
  Q4 补进去后对象的字节布局变化如何
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.ndfedit import NdfDocument
from ndfbin_decks.core import cndf_codec

WEAPON = _src.gfx() / "Weapon.ndfbin"

AMMO = "TAmmunitionDescriptor"


def main() -> int:
    doc, decoded = cndf_codec.decode(WEAPON.read_bytes(), source=WEAPON)
    props = decoded["properties"]
    classes = decoded["classes"]
    objects = decoded["objects"]
    print(f"PROP 表 {len(props)} 条 / 类 {len(classes)} 个 / 对象 {len(objects)}")

    # ---- Q1/Q2：属性 -> 哪些 class 用它 ----
    info = next(c for c in classes if c == AMMO)
    class_id = classes.index(AMMO)
    print(f"\n=== Q1/Q2 {AMMO} (class_id={class_id}) 的属性归属 ===")

    # PROP 表里带 class 字段
    mine = [(p["id"], p["name"], p.get("class"), p.get("class_id"))
            for p in props if p.get("class") == AMMO]
    print(f"PROP 表声明属于该类的属性: {len(mine)}")

    # 实际出现在对象里的
    in_use = {}
    for o in objects:
        if o["class"] != AMMO:
            continue
        for p in o["properties"]:
            in_use.setdefault(p["property_name"], set()).add(p["property_id"])
    print(f"实际出现在对象里的属性: {len(in_use)}")

    # 属性是否被多个 class 共用
    shared = {}
    for p in props:
        shared.setdefault(p["name"], set()).add(p.get("class"))
    multi = {n: cs for n, cs in shared.items() if len(cs) > 1}
    print(f"\n被多个类共用的属性名: {len(multi)} / {len(shared)}")
    for n, cs in list(multi.items())[:6]:
        print(f"    {n:34s} {sorted(x for x in cs if x)[:3]}")

    # ---- 找一个低覆盖字段做实验 ----
    nd = NdfDocument.load(WEAPON)
    ci = nd.class_info(AMMO)
    target = None
    for name, kind, cov in ci.fields:
        if 0.2 < cov < 0.8:
            target = (name, kind, cov)
            break
    print(f"\n=== 实验目标：{target} ===")
    if not target:
        print("没有合适的目标字段")
        return 1
    name, kind, cov = target

    # 该属性在 PROP 表里的 id 和 type
    pid = next(p["id"] for p in props if p["name"] == name)
    print(f"PROP 表中 {name} 的 property_id = {pid}")

    # 找一个「没有」这个字段的对象
    entries = nd.entries(AMMO)
    missing = [e for e in entries if name not in e.fields]
    present = [e for e in entries if name in e.fields]
    print(f"有此字段 {len(present)} / 缺此字段 {len(missing)}")

    victim = missing[0]
    donor = present[0]
    print(f"\n供体对象 #{donor.object_id}: {name} = {donor.fields[name].value}")
    print(f"受体对象 #{victim.object_id}: 该字段不存在，原有 {len(victim.fields)} 个字段")

    # 看供体的原始 value 结构（要照抄 type_id 等元数据）
    donor_obj = next(o for o in objects if o["id"] == donor.object_id)
    donor_prop = next(p for p in donor_obj["properties"]
                      if p["property_name"] == name)
    sample = dict(donor_prop["value"])
    print(f"\n供体 value 结构: type_id={sample['type_id']:#06x} "
          f"keys={sorted(sample.keys())}")

    # ---- Q3：给受体补上属性，编码回去 ----
    new_value = dict(sample)
    new_value["value"] = (int(sample["value"]) + 7 if kind == "int"
                          else float(sample["value"]) + 7.0)

    victim_obj = next(o for o in objects if o["id"] == victim.object_id)
    before_count = len(victim_obj["properties"])
    victim_obj["properties"].append({
        "property_id": pid, "property_name": name, "value": new_value})
    after_count = len(victim_obj["properties"])
    print(f"\n已给 #{victim.object_id} 追加 {name}："
          f"{before_count} -> {after_count} 个属性")

    # 编码 -> 解码往返
    obje = cndf_codec.encode_objects(objects)
    rebuilt = cndf_codec.rebuild_sections(doc, {"OBJE": obje})
    doc2, decoded2 = cndf_codec.decode(rebuilt, source="<rebuilt>")

    v2 = next(o for o in decoded2["objects"] if o["id"] == victim.object_id)
    got = [p for p in v2["properties"] if p["property_name"] == name]
    print(f"\n=== Q3 往返结果 ===")
    print(f"受体属性数 {len(v2['properties'])}，"
          f"找到 {name}: {'是' if got else '否'}")
    if got:
        print(f"  值 = {got[0]['value'].get('value')}")
        print(f"  类型 = {got[0]['value'].get('type_id'):#06x}")

    # 其它对象是否受影响
    other_diffs = 0
    for o2 in decoded2["objects"]:
        if o2["id"] == victim.object_id:
            continue
        o1 = next(x for x in objects if x["id"] == o2["id"])
        if len(o1["properties"]) != len(o2["properties"]):
            other_diffs += 1
    print(f"其它对象属性数变化: {other_diffs} 个（应为 0）")
    print(f"对象总数 {len(decoded2['objects'])}（原 {len(objects)}）")

    # ---- Q4：体积与结构 ----
    print(f"\n=== Q4 体积 ===")
    print(f"原始 {len(WEAPON.read_bytes()):,} 字节")
    print(f"重建 {len(rebuilt):,} 字节")

    # 无改动时字节是否一致（基线校验）
    doc3, decoded3 = cndf_codec.decode(WEAPON.read_bytes(), source=WEAPON)
    plain = cndf_codec.encode_objects(decoded3["objects"])
    same = cndf_codec.rebuild_sections(doc3, {"OBJE": plain}) == WEAPON.read_bytes()
    print(f"无改动往返字节一致: {same}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())