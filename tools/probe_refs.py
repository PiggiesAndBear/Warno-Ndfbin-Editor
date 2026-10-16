"""勘查：ndfbin 里指向其它对象的字段到底长什么样。

两个待回答的问题：
  1. 引用字段（obj_ref 0xBBBBBBBB / trans_ref 0xAAAAAAAA）在各文件里的分布；
  2. 被引用的对象能不能给出一个人类可读的标签（有没有 export 名）。

结论将决定 UI 用「下拉选择」还是「搜索框」还是「只读」。
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                          # noqa: E402
from ndfbin_decks.core.cndf_layout import (                       # noqa: E402
    OBJECT_REFERENCE, TRANS_REFERENCE)

MOD = _src.gfx()

FILES = ["Weapon.ndfbin", "Unit.ndfbin", "Deck.ndfbin"]


def walk(node, depth=0):
    """展开任意嵌套的值节点。"""
    if isinstance(node, list):
        for x in node:
            yield from walk(x, depth)
        return
    if not isinstance(node, dict):
        return
    yield node
    for v in node.values():
        if isinstance(v, (dict, list)):
            yield from walk(v, depth + 1)


def survey(path: Path) -> dict:
    doc, info = cndf_codec.decode(path.read_bytes(), source=str(path))
    objs = info["objects"]
    by_id = {o["id"]: o for o in objs}
    exports = info["exports"]          # {object_id: "$/…"}
    imports = info["imports"]          # {node_id: "$/…"}

    ref_fields = Counter()             # (class, field) -> obj_ref 次数
    target_class = Counter()           # (class, field) -> 目标类
    target_named = Counter()           # (class, field) -> 目标有 export 名吗
    trans_fields = Counter()
    trans_paths = Counter()
    obj_ref_targets = set()

    for o in objs:
        cls = o["class"]
        for p in o["properties"]:
            v = p["value"]
            if v.get("type_id") == OBJECT_REFERENCE:
                key = (cls, p["property_name"])
                ref_fields[key] += 1
                oid = v["object_id"]
                obj_ref_targets.add(oid)
                t = by_id.get(oid)
                if t:
                    target_class[key + (t["class"],)] += 1
                    named = "named" if oid in exports else "anon"
                    target_named[key + (named,)] += 1
            elif v.get("type_id") == TRANS_REFERENCE:
                trans_fields[(cls, p["property_name"])] += 1
                trans_paths[v.get("value")] += 1
            # list 里也可能藏着引用
            for sub in walk(v):
                if sub is v:
                    continue
                if sub.get("type_id") == OBJECT_REFERENCE:
                    ref_fields[(cls, p["property_name"] + "[*]")] += 1
                    t = by_id.get(sub["object_id"])
                    if t:
                        target_class[(cls, p["property_name"] + "[*]",
                                      t["class"])] += 1
                    obj_ref_targets.add(sub["object_id"])
                elif sub.get("type_id") == TRANS_REFERENCE:
                    trans_fields[(cls, p["property_name"] + "[*]")] += 1
                    trans_paths[sub.get("value")] += 1

    return {
        "objects": len(objs), "exports": len(exports), "imports": len(imports),
        "ref_fields": ref_fields, "target_class": target_class,
        "target_named": target_named, "trans_fields": trans_fields,
        "trans_paths": trans_paths, "ref_targets": len(obj_ref_targets),
        "info": info,
    }


def main() -> int:
    for name in FILES:
        path = MOD / name
        if not path.exists():
            print(f"\n跳过 {name}（不存在）")
            continue
        print(f"\n{'=' * 66}\n### {name}\n{'=' * 66}")
        s = survey(path)
        print(f"对象 {s['objects']}  有 export 名的 {s['exports']}  "
              f"IMPR 路径 {s['imports']}")

        def dump(title, counter, limit=14, suffix=()):
            print(f"\n--- {title}（{len(counter)} 种）---")
            for k, n in counter.most_common(limit):
                body = k[:-1] if suffix else k
                extra = ""
                if suffix and k[-1] in suffix:
                    pass
                print(f"  {n:>7}  " + " | ".join(str(x) for x in body) + extra)

        print(f"\n--- obj_ref 字段（{len(s['ref_fields'])} 种，"
              f"共 {sum(s['ref_fields'].values())} 处，"
              f"{s['ref_targets']} 个不同目标）---")
        for (cls, fld), n in s["ref_fields"].most_common(16):
            kinds = Counter()
            for (c, f, tgt), m in s["target_class"].items():
                if c == cls and f == fld:
                    kinds[tgt] += m
            named = Counter()
            for (c, f, t), m in s["target_named"].items():
                if c == cls and f == fld:
                    named[t] += m
            top = ", ".join(f"{t}×{m}" for t, m in kinds.most_common(3))
            print(f"  {n:>7}  {cls}.{fld}")
            print(f"           目标类: {top}")
            print(f"           目标命名: {dict(named)}")

        print(f"\n--- trans_ref 字段（{len(s['trans_fields'])} 种，"
              f"共 {sum(s['trans_fields'].values())} 处，"
              f"{len(s['trans_paths'])} 个不同路径）---")
        for (cls, fld), n in s["trans_fields"].most_common(16):
            print(f"  {n:>7}  {cls}.{fld}")
        print("\n  路径样本：")
        for p, n in s["trans_paths"].most_common(8):
            print(f"    {n:>7}  {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
