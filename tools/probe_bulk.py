"""勘查六：批量编辑的真实战场有多大。

用户给的例子：**「把对地射程 1925 的 Shell 提升至 2100」**。

这句话里藏着三个待决策点，这个脚本先把事实测出来：

  1. **筛选条件命中多少条**——如果 1925 的 Shell 只有 1 条，
     「批量」就没有意义，说明用户的真实意图是别的筛法。
  2. **值的分布有多碎**——如果每个弹种的 MaximumRangeGRU 各不相同
     （1548 条弹药有 1500 种取值），那「按精确值筛」几乎等于「按对象筛」。
  3. **哪些字段值得做筛选**——射程？伤害？还是都要。

同时测「弹种」（ProjectileType）到底是不是一个可用的分组维度。
"""

import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                          # noqa: E402
from ndfbin_decks.ndfedit.ammo import AMMO_CLASS, projectile_label  # noqa: E402

MOD = _src.gfx()

#: 用户例子里的三个字段
RANGE_FIELD = "MaximumRangeGRU"
DAMAGE_FIELD = "PhysicalDamages"
TYPE_FIELD = "ProjectileType"


def leaf(props, name):
    for p in props:
        if p["property_name"] == name:
            v = p["value"]
            return v.get("value") if "value" in v and "items" not in v else None
    return None


def main() -> int:
    _doc, info = cndf_codec.decode((MOD / "Weapon.ndfbin").read_bytes(),
                                   source="Weapon.ndfbin")
    ammo = [o for o in info["objects"] if o["class"] == AMMO_CLASS]
    print(f"{AMMO_CLASS}: {len(ammo)} 条")

    # --- 弹种 ---
    kinds = Counter()
    no_kind = 0
    for o in ammo:
        k = leaf(o["properties"], TYPE_FIELD)
        if k is None:
            no_kind += 1
        else:
            kinds[projectile_label(k)] += 1
    print(f"\n--- 弹种（{TYPE_FIELD}）---  无此字段 {no_kind} 条")
    for k, n in kinds.most_common():
        print(f"  {k:<24} {n:>5}")

    # --- 射程值分布 ---
    def value_profile(field, label):
        vals = defaultdict(list)          # 值 -> 对象
        missing = 0
        for o in ammo:
            v = leaf(o["properties"], field)
            if v is None:
                missing += 1
            else:
                vals[v].append(o)
        uniq = len(vals)
        dup = {v: len(objs) for v, objs in vals.items() if len(objs) > 1}
        print(f"\n--- {label}（{field}）---")
        print(f"  有值 {len(ammo) - missing} 条，缺字段 {missing} 条")
        print(f"  不同取值 {uniq} 种"
              f"（平均每个值 {sum(len(v) for v in vals.values()) / max(uniq, 1):.2f} 条）")
        print(f"  被 ≥2 条共用的取值 {len(dup)} 种，"
              f"覆盖 {sum(dup.values())} 条"
              f"（占 {sum(dup.values()) * 100 / max(len(ammo) - missing, 1):.1f}%）")
        top = sorted(vals.items(), key=lambda kv: -len(kv[1]))[:12]
        print("  最常见的取值:")
        for v, objs in top:
            ks = Counter(projectile_label(leaf(o["properties"], TYPE_FIELD))
                         for o in objs)
            tag = "、".join(f"{k}×{n}" for k, n in ks.most_common(3))
            print(f"    {v!r:<12} {len(objs):>4} 条   [{tag}]")
        return vals

    rng = value_profile(RANGE_FIELD, "对地射程")
    dmg = value_profile(DAMAGE_FIELD, "物理伤害")

    # --- 直接验证用户的例子 ---
    print(f"\n{'=' * 70}\n用户例子的实测：{RANGE_FIELD} == 1925 的 Shell\n{'=' * 70}")
    hit_any = [o for o, v in ((o, leaf(o["properties"], RANGE_FIELD))
                              for o in ammo) if v == 1925]
    hit_shell = [o for o in hit_any
                 if projectile_label(leaf(o["properties"], TYPE_FIELD)) == "Shell"]
    print(f"  射程 == 1925 的弹药：{len(hit_any)} 条")
    print(f"  其中弹种是 Shell 的：{len(hit_shell)} 条")
    for o in hit_any[:8]:
        k = projectile_label(leaf(o["properties"], TYPE_FIELD))
        d = leaf(o["properties"], DAMAGE_FIELD)
        print(f"    #{o['id']:<6} {k:<14} 伤害 {d}  射程 1925")
    near = [v for v in rng if isinstance(v, (int, float))
            and 1800 <= v <= 2050]
    print(f"\n  射程落在 1800~2050 区间的取值："
          f"{sorted(near)[:20]}{' …' if len(near) > 20 else ''}")

    # --- 组合筛选的规模 ---
    print(f"\n--- 组合筛选规模（弹种 + 射程精确匹配）---")
    combo = Counter()
    for o in ammo:
        k = projectile_label(leaf(o["properties"], TYPE_FIELD))
        v = leaf(o["properties"], RANGE_FIELD)
        combo[(k, v)] += 1
    multi = {kv: n for kv, n in combo.items() if n >= 2}
    print(f"  「弹种+射程」组合共 {len(combo)} 种，"
          f"其中 ≥2 条的 {len(multi)} 种")
    for (k, v), n in sorted(multi.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {k:<16} {v!r:<10} {n:>4} 条")

    # --- 只看弹种（不加值条件）的规模 ---
    print(f"\n--- 只按弹种筛的规模 ---")
    for k, n in kinds.most_common():
        if n >= 20:
            print(f"    {k:<24} {n:>5} 条")

    # --- 射程的统计量（决定「按范围筛」是否有用）---
    nums = [v for v in rng if isinstance(v, (int, float))]
    if nums:
        print(f"\n--- 射程统计 ---")
        print(f"  min {min(nums)}  中位 {statistics.median(nums)}  "
              f"max {max(nums)}  均值 {statistics.mean(nums):.0f}")
        buckets = Counter()
        for v in nums:
            buckets[(v // 500) * 500] += 1
        print("  按 500 分桶:")
        for b in sorted(buckets):
            print(f"    {b:>6}~{b + 499:<6} {buckets[b]:>5} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
