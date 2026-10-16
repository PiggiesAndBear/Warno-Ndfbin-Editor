"""勘查二：候选集规模 + TEntityDescriptor 字段清单。

probe_refs 回答了「引用指向谁」，这一轮回答两个决定 UI 形态的问题：

  A. 每个引用字段的**候选集有多大**？
     候选集 = 被引用类在同一个文件里的对象总数。
     3 个候选 -> 下拉框；300 个 -> 搜索框；180000 个 -> 只读。
     这是「能不能编辑」的真正门槛，不是字段名。

  B. TEntityDescriptor 及其派生类有哪些字段？血量/速度/装甲叫什么？
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                          # noqa: E402
from ndfbin_decks.core.cndf_layout import (                       # noqa: E402
    OBJECT_REFERENCE, TRANS_REFERENCE, _VALUE_NAMES)

MOD = _src.gfx()


def walk_children(node):
    if not isinstance(node, dict):
        return
    for v in node.values():
        if isinstance(v, (dict, list)):
            yield from walk_all(v)


def walk_all(node):
    if isinstance(node, list):
        for x in node:
            yield from walk_all(x)
        return
    if not isinstance(node, dict):
        return
    yield node
    yield from walk_children(node)


def type_counts(node, acc):
    if isinstance(node, list):
        for x in node:
            type_counts(x, acc)
        return
    if not isinstance(node, dict):
        return
    if "type_id" in node:
        acc[node["type_id"]] += 1
    for v in node.values():
        if isinstance(v, (dict, list)):
            type_counts(v, acc)


def main() -> int:
    for fname in ("Weapon.ndfbin", "Unit.ndfbin", "Deck.ndfbin"):
        path = MOD / fname
        doc, info = cndf_codec.decode(path.read_bytes(), source=str(path))
        objs = info["objects"]
        exports = info["exports"]

        class_count = Counter(o["class"] for o in objs)
        named_count = Counter(
            o["class"] for o in objs if o["id"] in exports)

        # (owner_class, field) -> 引用的目标类集合
        ref_targets = defaultdict(Counter)
        for o in objs:
            cls = o["class"]
            for p in o["properties"]:
                v = p["value"]
                if v.get("type_id") == OBJECT_REFERENCE:
                    ref_targets[(cls, p["property_name"])][
                        v.get("class_id")] += 1
                for sub in walk_children(v):
                    if sub.get("type_id") == OBJECT_REFERENCE:
                        ref_targets[(cls, p["property_name"] + "[*]")][
                            sub.get("class_id")] += 1

        classes = info["classes"]

        print(f"\n{'=' * 78}\n### {fname}  对象 {len(objs)}  "
              f"类 {len(classes)}  IMPR {len(info['imports'])}\n{'=' * 78}")

        print("\n--- 引用字段的候选集规模 ---")
        print(f"{'字段':<52} {'目标类':<28} {'候选':>7} {'命名':>7}")
        rows = []
        for (cls, fld), targets in ref_targets.items():
            for cid, n in targets.items():
                tname = classes[cid] if cid < len(classes) else f"#{cid}"
                pool = class_count.get(tname, 0)
                named = named_count.get(tname, 0)
                rows.append((pool, cls, fld, tname, n, named))
        rows.sort()
        for pool, cls, fld, tname, n, named in rows:
            if pool > 3000:
                continue          # 巨大的略过，下面单独统计
            print(f"{(cls + '.' + fld)[:52]:<52} {tname[:28]:<28} "
                  f"{pool:>7} {named:>7}")

        big = [r for r in rows if r[0] > 3000]
        if big:
            print(f"\n  （另有 {len(big)} 个字段候选集 > 3000，不适合下拉）")
            for pool, cls, fld, tname, n, named in big[:8]:
                print(f"    {(cls + '.' + fld)[:56]:<56} {tname[:24]:<24} "
                      f"{pool:>8} 命名{named}")

        # ---------- TEntityDescriptor 系 ----------
        if fname == "Unit.ndfbin":
            print(f"\n{'=' * 78}\n--- TEntityDescriptor 系字段 ---\n{'=' * 78}")
            ent_classes = [c for c in classes
                           if "Entity" in c or "Unit" in c and "Descriptor" in c]
            families = sorted(
                (c for c in class_count
                 if "Entity" in c or c in ("TUnitDescriptor",)),
                key=lambda c: -class_count[c])

            # 找血量/速度/装甲相关字段：扫所有类，按名字筛
            KEYWORDS = ("damage", "health", "hitpoint", "armor", "blindage",
                        "speed", "resist", "life", "hp", "suppress",
                        "maxspeed", "accel", "rotdelay", "optics", "vision")
            hits = defaultdict(lambda: defaultdict(list))
            for o in objs:
                for p in o["properties"]:
                    low = p["property_name"].lower()
                    if any(k in low for k in KEYWORDS):
                        v = p["value"]
                        hits[o["class"]][p["property_name"]].append(v)

            print("\n含 血量/装甲/速度 关键词的字段（按类）:")
            for cls in sorted(hits, key=lambda c: -class_count[c])[:30]:
                fields = hits[cls]
                print(f"\n  {cls}  ({class_count[cls]} 个对象)")
                for fn in sorted(fields):
                    vs = fields[fn]
                    sample = vs[0]
                    tid = sample.get("type_id")
                    tn = _VALUE_NAMES.get(tid, hex(tid))
                    val = sample.get("value")
                    if tid in (0x11, 0x12):
                        val = f"[{sample.get('length')} 项]"
                    if tid == OBJECT_REFERENCE:
                        val = f"-> {classes[sample['class_id']]}"
                    print(f"    {fn:<42} {tn:<10} "
                          f"覆盖 {len(vs)}/{class_count[cls]}  {val!r}")

            print("\n\n--- TEntityDescriptor 全部字段 ---")
            ent_fields = defaultdict(int)
            ent_n = 0
            for o in objs:
                if o["class"] == "TEntityDescriptor":
                    ent_n += 1
                    for p in o["properties"]:
                        ent_fields[p["property_name"]] += 1
            print(f"  TEntityDescriptor 对象 {ent_n} 个，字段 {len(ent_fields)} 种")
            for fn, n in sorted(ent_fields.items(), key=lambda x: -x[1]):
                print(f"    {fn:<52} {n:>6}/{ent_n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
