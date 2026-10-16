"""勘查五：list 型字段里内嵌的引用，长度分布如何。

probe_refs 已经知道「哪些字段是引用」，但只看顶层。这一轮回答：
**list 里的引用能不能展开成一行一项来编辑？**

能展开的前提是列表足够短。``ModulesDescriptors`` 实测 18 项还行，
但 ``DeckPackList`` 在一个 deck 上到底多长？如果中位数是 200，
就不能在面板里铺开，只能开一个独立的列表编辑器。

同时确认每项的**目标类是否唯一**——如果一个 list 里混着好几种目标类
（``ModulesDescriptors`` 就是这样），那么「换目标」的候选集是逐项不同的。
"""

import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                          # noqa: E402
from ndfbin_decks.core.cndf_layout import OBJECT_REFERENCE        # noqa: E402

MOD = _src.gfx()

FILES = ["Weapon.ndfbin", "Unit.ndfbin", "Deck.ndfbin", "Division.ndfbin",
         "Depiction.ndfbin"]


def ref_items(value):
    """一个 list 值里，哪些 item 直接是 obj_ref。返回 ``(下标, node)``。"""
    if not isinstance(value, dict) or value.get("type_id") != 0x11:
        return []
    out = []
    for i, it in enumerate(value.get("items", ())):
        if isinstance(it, dict) and it.get("type_id") == OBJECT_REFERENCE:
            out.append((i, it))
    return out


def has_nested_ref(value, depth=0):
    """list 里是否还有更深一层的引用（如 list<list<ref>>）。"""
    if not isinstance(value, dict) or depth > 3:
        return False
    for it in value.get("items", ()):
        if isinstance(it, dict) and it.get("type_id") == OBJECT_REFERENCE:
            continue
        if isinstance(it, dict) and it.get("items"):
            if has_nested_ref(it, depth + 1):
                return True
    return False


def main() -> int:
    for fname in FILES:
        path = MOD / fname
        if not path.exists():
            continue
        _doc, info = cndf_codec.decode(path.read_bytes(), source=str(path))
        classes = info["classes"]

        # (class, field) -> [列表长度...]
        lengths: dict[tuple, list[int]] = defaultdict(list)
        # (class, field) -> 目标类分布
        targets: dict[tuple, Counter] = defaultdict(Counter)
        nested = defaultdict(int)
        all_lists = defaultdict(int)

        for o in info["objects"]:
            for p in o["properties"]:
                v = p["value"]
                if not isinstance(v, dict) or v.get("type_id") != 0x11:
                    continue
                key = (o["class"], p["property_name"])
                all_lists[key] += 1
                items = ref_items(v)
                if not items:
                    if has_nested_ref(v):
                        nested[key] += 1
                    continue
                lengths[key].append(len(items))
                for _i, node in items:
                    cid = node.get("class_id")
                    if cid is not None and cid < len(classes):
                        targets[key][classes[cid]] += 1

        print(f"\n{'=' * 84}\n### {fname}\n{'=' * 84}")
        print(f"{'字段':<50} {'对象':>6} {'长度 min/中/max':>18} "
              f"{'目标类数':>8} {'类':>4}")
        rows = sorted(lengths.items(), key=lambda kv: -len(kv[1]))
        for (cls, fld), ls in rows[:22]:
            med = statistics.median(ls)
            tgts = targets[(cls, fld)]
            print(f"{(cls + '.' + fld)[:50]:<50} {len(ls):>6} "
                  f"{min(ls):>6}/{med:>7.0f}/{max(ls):<4} "
                  f"{len(tgts):>8} {len(ls):>4}")
            if len(tgts) <= 3:
                for t, n in tgts.most_common(3):
                    print(f"{'':<50}   -> {t} × {n}")
        if nested:
            print("\n  含更深层嵌套引用的 list 字段（第一版不处理）:")
            for (cls, fld), n in sorted(nested.items(),
                                        key=lambda kv: -kv[1])[:6]:
                print(f"    {cls}.{fld}  {n} 个对象")

        # 长度分桶：判断「展开编辑」的可行比例
        print("\n  长度分桶（所有含引用的 list 字段合计）:")
        buckets = Counter()
        total = 0
        for ls in lengths.values():
            for n in ls:
                total += 1
                if n <= 8:
                    buckets["1-8"] += 1
                elif n <= 32:
                    buckets["9-32"] += 1
                elif n <= 128:
                    buckets["33-128"] += 1
                else:
                    buckets[">128"] += 1
        for k in ("1-8", "9-32", "33-128", ">128"):
            n = buckets[k]
            pct = n * 100 / total if total else 0
            print(f"    {k:<8} {n:>8}  ({pct:5.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
