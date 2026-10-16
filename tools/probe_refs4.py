"""勘查四：把决定 UI 可读性的几处细节钉死。

  A. TResistanceTypeRTTI / TDamageTypeRTTI 的 Family / Index 到底是什么类型？
     如果 Family 是 loc_hash 或字符串，装甲就能显示成人看得懂的东西；
     如果只是 int，装甲下拉框只能退化成「候选 #id + 谁在用我」。
  B. TMountedWeaponDescriptor.HandheldEquipmentKey /
     TTurretTwoAxisDescriptor.Tag 的真实取值 —— 这两个可能是现成的可读标签。
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                          # noqa: E402

MOD = _src.gfx()


def brief(v):
    t = v.get("type_id")
    if t == 17 or t == 18:
        return f"list[{v.get('length')}]"
    if t == 3149642683:
        return f"obj_ref -> #{v['object_id']} class {v['class_id']}"
    if t == 2863311530:
        return f"trans_ref[{v.get('index')}] {v.get('value')!r}"
    if t in (7, 28):
        return f"strg_ref[{v.get('index')}] {v.get('value')!r}"
    if t == 8:
        return f"wide {v.get('value')!r}"
    if t == 0x1D:
        return f"loc_hash {v.get('value_hex')}"
    if t == 0x1A:
        return f"guid {v.get('value_hex')}"
    if "value" in v:
        return f"{type(v['value']).__name__} {v['value']!r}"
    return str(v)


def dump_class(path, cls, limit=6, label=""):
    doc, info = cndf_codec.decode(path.read_bytes(), source=str(path))
    pool = [o for o in info["objects"] if o["class"] == cls]
    exports = info["exports"]
    print(f"\n--- {label or cls}  ({len(pool)} 个) ---")
    for o in pool[:limit]:
        exp = exports.get(o["id"], "")
        print(f"  #{o['id']}  {exp}")
        for p in o["properties"]:
            print(f"      {p['property_name']:<38} {brief(p['value'])}")
    # 全池的 Family/Index 分布
    fams = Counter()
    idxs = Counter()
    for o in pool:
        for p in o["properties"]:
            if p["property_name"] == "Family":
                fams[brief(p["value"])] += 1
            if p["property_name"] == "Index":
                idxs[p["value"].get("value")] += 1
    if fams:
        print(f"  Family 取值分布: {dict(fams)}")
        print(f"  Index 取值: {sorted(idxs)[:20]}  …共 {len(idxs)} 个")
    return info


def who_uses(info, target_class):
    """谁引用了某个类的对象 —— 做下拉时这是最有用的上下文。"""
    classes = info["classes"]
    users = defaultdict(int)
    n = 0
    for o in info["objects"]:
        for p in o["properties"]:
            v = p["value"]
            if (v.get("type_id") == 3149642683
                    and classes[v.get("class_id")] == target_class):
                users[f"{o['class']}.{p['property_name']}"] += 1
                n += 1
    return n, users


def main() -> int:
    unit = MOD / "Unit.ndfbin"
    print("=" * 78)
    print("A. RTTI 类到底长什么样")
    print("=" * 78)
    info = dump_class(unit, "TResistanceTypeRTTI", 5, "TResistanceTypeRTTI")
    n, users = who_uses(info, "TResistanceTypeRTTI")
    print(f"  被引用 {n} 次，来源字段: {dict(users)}")

    dump_class(MOD / "Weapon.ndfbin", "TDamageTypeRTTI", 4, "TDamageTypeRTTI")

    print("\n" + "=" * 78)
    print("B. 可能的现成可读标签")
    print("=" * 78)
    dump_class(MOD / "Weapon.ndfbin", "TMountedWeaponDescriptor", 3)
    dump_class(MOD / "Weapon.ndfbin", "TTurretTwoAxisDescriptor", 2)

    print("\n" + "=" * 78)
    print("C. TBlindageProperties 全貌（装甲的核心）")
    print("=" * 78)
    ui = dump_class(unit, "TBlindageProperties", 3, "TBlindageProperties")
    n, users = who_uses(ui, "TBlindageProperties")
    print(f"  被引用 {n} 次，来源: {dict(users)}")

    print("\n" + "=" * 78)
    print("D. 一个实体的 Module 列表长什么样（聚合视图的依据）")
    print("=" * 78)
    ent = [o for o in ui["objects"] if o["class"] == "TEntityDescriptor"]
    named = [o for o in ent if o["id"] in ui["exports"]]
    print(f"  TEntityDescriptor {len(ent)} 个，{len(named)} 有 export 名")
    if named:
        o = named[0]
        print(f"\n  样本 #{o['id']} {ui['exports'][o['id']]}")
        for p in o["properties"]:
            v = p["value"]
            if p["property_name"] == "ModulesDescriptors":
                print(f"      ModulesDescriptors: {v['length']} 项")
                for it in v["items"]:
                    t = it.get("type_id")
                    if t == 3149642683:
                        tgt = ui["objects"][it["object_id"]]
                        print(f"        obj_ref  #{it['object_id']:<7} "
                              f"{tgt['class']}")
                    elif t == 2863311530:
                        print(f"        trans_ref {it.get('value')}")
                    else:
                        print(f"        {brief(it)}")
            else:
                print(f"      {p['property_name']:<38} {brief(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
