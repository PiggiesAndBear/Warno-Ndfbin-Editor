"""勘查：弹药的 ``Arme`` 字段——它指向什么、怎么赋值、含义是什么。

**用户问题.** 通过 WeaponManager 定位弹药型号，探索弹药对象的 ``Arme``
赋值规律和含义。

**结论摘要（实测）.**

``Arme`` 是**伤害类型**，不是「某把武器」。它指向 ``TDamageTypeRTTI``，
而该对象的 ``(Family, Index)`` 两个整数定位到一张**伤害矩阵的一行**：

.. code-block:: text

    TGameplayDamageResistanceContainer            (BaseWeapon.ndfbin)
      ├ DamageFamilyCounts     24 项 map：家族下标 -> 该家族下的行数
      ├ ResistanceFamilyCounts 13 项 map：抵抗家族下标 -> 该家族下的列数
      └ Values                152 行 × 113 列 float32
                              行 = 伤害类型（按 DamageFamily 分段）
                              列 = 目标类型（按 ResistanceFamily 分段）

    行号 = offsets[Family] + (Index - 1)         ← Index 是 1-based
    Family 属性缺席 = Family 0（``DamageFamily_ap``）

    TDamageFamilyList.Values      24 个 ``DamageFamily_*`` 字符串
    TResistanceFamilyList.Values  13 个 ``ResistanceFamily_*`` 字符串

**赋值规律（实测）.**

- 一个弹药恰好一个 ``Arme``（1548/1548，覆盖率 100%，无空引用）
- 1548 个弹药只用到 91 种行，说明**大量弹药共享同一伤害类型**
- 与武器型号**无关**：同型号的多发弹药各有各的 ``Arme``
- 与弹种 ``ProjectileType`` 强相关但不是一一对应

数据来源：mod 目录的 ``Weapon.ndfbin`` + ``BaseWeapon.ndfbin``。
前者实测被测试残留污染（比 ``.bak`` 多一个克隆弹药），统计一律排除。
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.ndfedit.ammo import (AMMO_CLASS, AmmoTitles,  # noqa: E402
                                       projectile_label)
from ndfbin_decks.ndfedit.schema import NdfDocument, resolve_text  # noqa: E402

G = _src.gfx()
DT_CLASS = "TDamageTypeRTTI"

#: 系数摘要里点名要看的目标列（抵抗家族）。步兵 / 重装甲 / 顶甲 / 飞机
HIGHLIGHT = ("ResistanceFamily_blindage", "ResistanceFamily_infanterie",
             "ResistanceFamily_toit", "ResistanceFamily_avion")


def sep(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def items_of(obj, name):
    for p in obj.get("properties", []):
        if p["property_name"] == name:
            return p["value"].get("items", [])
    return []


def scalar(obj, name):
    for p in obj.get("properties", []):
        if p["property_name"] == name:
            return p["value"].get("value")
    return None


class Matrix:
    """``BaseWeapon.ndfbin`` 里的伤害矩阵与两张家族名表。"""

    def __init__(self, path: Path):
        bw = NdfDocument.load(path)
        dec = bw.decoded
        by_id = {o["id"]: o for o in dec["objects"]}

        self.dmg_families = [it["value"] for it in
                             items_of(by_id[1], "Values")] if 1 in by_id else []
        self.res_families = [it["value"] for it in
                             items_of(by_id[0], "Values")] if 0 in by_id else []
        # 找到容器（不假定 id）
        cont = next(o for o in dec["objects"]
                    if o["class"] == "TGameplayDamageResistanceContainer")
        self.dmg_counts = {it["key"]["value"]: it["value"]["value"]
                           for it in items_of(cont, "DamageFamilyCounts")}
        self.res_counts = {it["key"]["value"]: it["value"]["value"]
                           for it in items_of(cont, "ResistanceFamilyCounts")}
        self.rows = [[x["value"] for x in it["items"]]
                     for it in items_of(cont, "Values")]
        # 每种伤害类型的起始行
        self.offsets: dict[int, int] = {}
        off = 0
        for f in sorted(self.dmg_counts):
            self.offsets[f] = off
            off += self.dmg_counts[f]
        self.row_total = off
        # 每列属于哪个抵抗家族
        self.col_family: list[int] = []
        for r in sorted(self.res_counts):
            self.col_family += [r] * self.res_counts[r]

    def name_of_family(self, f) -> str:
        if f is None:
            f = 0                       # 缺席 = 0
        if 0 <= f < len(self.dmg_families):
            return self.dmg_families[f]
        return f"Family#{f}"

    def row_of(self, family, index) -> int | None:
        """行号 = offsets[Family] + Index - 1（Index 是 1-based）。"""
        if family is None:
            family = 0
        if family not in self.offsets or index is None:
            return None
        r = self.offsets[family] + int(index) - 1
        return r if 0 <= r < len(self.rows) else None

    def row_vec(self, row: int) -> list[float]:
        return self.rows[row]

    def summary(self, row: int) -> dict[str, tuple]:
        """某一行在每个抵抗家族上的系数切片（只取有代表的几族）。"""
        out: dict[str, tuple] = {}
        vec = self.rows[row]
        k = 0
        for r in sorted(self.res_counts):
            seg = tuple(vec[k:k + self.res_counts[r]])
            out[self.res_families[r] if r < len(self.res_families) else str(r)] = seg
            k += self.res_counts[r]
        return out


def main() -> int:
    # ---------------- 数据准备 ----------------
    mat = Matrix(G / "BaseWeapon.ndfbin")
    sep("0. 伤害矩阵（BaseWeapon.ndfbin 里的 TGameplayDamageResistanceContainer）")
    print(f"伤害家族 {len(mat.dmg_families)} 个，"
          f"共 {mat.row_total} 种伤害类型（= Values 行数 {len(mat.rows)}）")
    print(f"抵抗家族 {len(mat.res_families)} 个，"
          f"共 {sum(mat.res_counts.values())} 种目标类型"
          f"（= 每行宽度 {len(mat.rows[0])}）")

    nd = NdfDocument.load(G / "Weapon.ndfbin")
    AmmoTitles(nd).apply()
    objs = nd.decoded["objects"]
    base_ids = {o["id"] for o in
                NdfDocument.load(G / "Weapon.ndfbin.bak").decoded["objects"]}
    extra_ids = {o["id"] for o in objs} - base_ids
    if extra_ids:
        print(f"!! 排除测试残留对象 {sorted(extra_ids)}"
              f"（当前文件比 .bak 多 {len(extra_ids)} 个）")
    live = [o for o in objs if o["id"] not in extra_ids]
    live_ids = {o["id"] for o in live}
    ammo_objs = [o for o in live if o["class"] == AMMO_CLASS]
    dt_objs = [o for o in live if o["class"] == DT_CLASS]

    # ---------------- 靶子 ----------------
    sep("1. Arme 的靶子：Weapon.ndfbin 里的 TDamageTypeRTTI")
    dt_family: dict[int, tuple] = {}
    fam_objs: dict = defaultdict(list)
    for o in dt_objs:
        f = scalar(o, "Family")
        i = scalar(o, "Index")
        dt_family[o["id"]] = (f, i)
        fam_objs[f].append((i, o["id"]))
    print(f"对象数 {len(dt_objs)}（不是矩阵的全部 {mat.row_total} 行——"
          f"NDF 只序列化被引用到的对象）")
    print(f"\n{'Family':>14} {'名称':<30} {'对象数':>5} {'家族行数':>7}  校验")
    bad = 0
    for f in sorted(fam_objs, key=lambda x: (x is None, x)):
        idxs = sorted(i for i, _ in fam_objs[f])
        cnt = mat.dmg_counts.get(0 if f is None else f)
        ok = all(1 <= i <= cnt for i in idxs) if cnt else False
        bad += not ok
        print(f"{str(f):>14} {mat.name_of_family(f):<30} "
              f"{len(idxs):>5} {cnt:>7}  "
              f"{'Index 都在 1..%d 内 ✓' % cnt if ok else '★越界'}")
    print(f"\n→ {len(dt_objs)} 个对象全部落在各自家族的行区间内"
          if not bad else f"\n→ {bad} 个家族越界")
    print("→ 这里就是「Family 缺席 = Family 0」的铁证：那 30 个对象的 Index "
          "落在 1..40，\n   而 DC[0]（DamageFamily_ap）的行数正好是 40")

    # ---------------- 弹药侧 ----------------
    sep("2. 弹药侧：Arme 实际指向哪一行、系数长什么样")
    ammo_row: dict[int, int] = {}
    ammo_key: dict[int, tuple] = {}
    for o in ammo_objs:
        f = i = None
        tgt = None
        for p in o["properties"]:
            if p["property_name"] == "Arme":
                tgt = p["value"].get("object_id")
        if tgt in (None, 0xFFFFFFFF):
            continue
        f, i = dt_family.get(tgt, (None, None))
        r = mat.row_of(f, i)
        if r is not None:
            ammo_row[o["id"]] = r
            ammo_key[o["id"]] = (f, i)
    used_rows = Counter(ammo_row.values())
    print(f"{len(ammo_row)}/{len(ammo_objs)} 个弹药有可解析的 Arme；"
          f"用到 {len(used_rows)} 种行（文件里 {len(dt_objs)} 种，"
          f"矩阵共 {mat.row_total} 行）")
    print(f"\n{'行':>4} {'(Family,Index)':>15} {'家族名':<30} {'弹药数':>5}  "
          f"打重甲 打步兵 打顶甲 打飞机")
    for row, n in used_rows.most_common(16):
        sample_oid = next(o for o, v in ammo_row.items() if v == row)
        key = ammo_key[sample_oid]
        s = mat.summary(row)
        def pick(nm):
            v = s.get(nm, ())
            return f"{v[0]:.2f}" if v else "?"
        print(f"{row:>4} {str(key):>15} {mat.name_of_family(key[0]):<30} {n:>5}  "
              f"{pick(HIGHLIGHT[0]):>6} {pick(HIGHLIGHT[1]):>6} "
              f"{pick(HIGHLIGHT[2]):>6} {pick(HIGHLIGHT[3]):>6}")

    print("\n--- 2a. 完整系数：挑三行对比（看出「含义」）---")
    picks = []
    for want in (9, 12, 1, 7, 21):          # he / howz / ap_missile / fmballe / roquette_ap
        r = mat.offsets.get(want)
        if r is not None:
            picks.append((want, r))
    for fam, row in picks:
        obj = next((o for o in dt_objs if (scalar(o, "Family") or 0) == fam), None)
        idx = scalar(obj, "Index") if obj else 1
        s = mat.summary(row)
        print(f"\n  ◆ {mat.name_of_family(fam)}  (Family={fam}, Index={idx} → row {row})")
        for nm in HIGHLIGHT:
            v = s.get(nm, ())
            if v:
                if len(v) > 6:
                    print(f"      {nm:<30} {tuple(round(x, 2) for x in v[:6])} … "
                          f"（{len(v)} 级）")
                else:
                    print(f"      {nm:<30} {tuple(round(x, 2) for x in v)}")

    # ---------------- 与型号的关系 ----------------
    sep("3. 通过 WeaponManager 定位型号：Arme 属于弹药，不属于武器")
    used_by = AmmoTitles(nd).used_by
    by_equip: dict[str, list[int]] = defaultdict(list)
    for aid, names in used_by.items():
        if aid not in live_ids:
            continue
        for nm in names:
            by_equip[nm].append(aid)
    multi = sorted(((n, ids) for n, ids in by_equip.items() if len(ids) > 1),
                   key=lambda kv: -len(kv[1]))
    print(f"装备型号名 {len(by_equip)} 个，引用 >1 种弹药的 {len(multi)} 个")
    same = sum(1 for _, ids in multi
               if len({ammo_row.get(i) for i in ids if i in ammo_row}) <= 1)
    print(f"同型号内 Arme 完全一致的 {same} 个 / 有分歧的 {len(multi) - same} 个"
          f"  → 多数型号有多种弹，各用各的伤害类型")
    obj_by_id = {o["id"]: o for o in live}
    for name, ids in multi[:5]:
        rows = {(i, ammo_row.get(i)) for i in ids if i in ammo_row}
        print(f"\n  ◆ {name} —— {len(ids)} 种弹药 → {len(rows)} 种 Arme")
        for i, r in sorted(rows):
            key = ammo_key[i]
            print(f"      #{i:<5} {projectile_label(scalar(obj_by_id[i], 'ProjectileType')):<11}"
                  f" → {mat.name_of_family(key[0])} ({key[0]}, {key[1]})")

    # ---------------- 使用/闲置 ----------------
    sep("4. 哪些行被用了、哪些空着（决定「能不能自定义伤害类型」）")
    unused_file = [(f, i) for f, lst in fam_objs.items() for i, _ in lst
                   if not any(ammo_row.get(oid) == mat.row_of(f, i)
                              for oid in ammo_row)]
    print(f"文件里有 {len(dt_objs)} 种伤害类型；弹药用了 {len(used_rows)} 种；"
          f"文件里没被任何弹药用的 {len(dt_objs) - len(used_rows)} 种")
    print("矩阵剩余行（文件里没有对应 TDamageTypeRTTI 对象的）：")
    missing = []
    for f in sorted(mat.dmg_counts):
        rng = range(mat.offsets[f], mat.offsets[f] + mat.dmg_counts[f])
        have = {mat.row_of(*dt_family[o["id"]]) for o in dt_objs
                if (scalar(o, "Family") or 0) == f}
        gap = [r for r in rng if r not in have]
        if gap:
            missing.append((mat.name_of_family(f), len(gap), f"{gap[0]}..{gap[-1]}"))
    for nm, n, rng in missing:
        print(f"    {nm:<34} 缺 {n:>3} 行  行 {rng}")

    # ---------------- 谁还引用 ----------------
    sep("5. 还有谁引用 TDamageTypeRTTI")
    dt_ids = set(dt_family)
    users = Counter()
    for o in live:
        for p in o["properties"]:
            for node in _walk_refs(p["value"]):
                if node.get("object_id") in dt_ids:
                    users[(o["class"], p["property_name"])] += 1
    for (cls, field), n in users.most_common(8):
        print(f"  {cls:<40} .{field:<30} {n:>5}")

    # ---------------- 表现层字段 ----------------
    sep("6. 表现层字段：与 Arme 只是「成套配置」，改 Arme 不会带动它们")
    print("（这一节是「把 HEAT 弹改造成高爆弹」教程的依据）\n")
    strings = nd.decoded["strings"]
    VIEW = ("ImpactHappening", "TraitsToken", "PiercingWeapon",
            "MinMaxCategory", "WeaponCursorType", "TypeCategoryName")
    per_fam: dict = defaultdict(lambda: defaultdict(Counter))
    for o in ammo_objs:
        k = ammo_key.get(o["id"])
        if k is None:
            continue
        fam = mat.name_of_family(k[0])
        if fam.startswith("DamageFamily_"):
            fam = fam[len("DamageFamily_"):]
        for fname in VIEW:
            v = None
            for p in o["properties"]:
                if p["property_name"] == fname:
                    v = p["value"]
                    break
            if v is None:
                per_fam[fam][fname]["(缺)"] += 1
                continue
            t = v.get("type")
            if t == "strg_ref":
                val = resolve_text(v, strings)
            elif t == "list":
                val = tuple(resolve_text(x, strings)
                            for x in v.get("items", []))
            elif t == "loc_hash":
                val = v.get("value_hex")
            else:
                val = v.get("value")
            per_fam[fam][fname][val] += 1

    for fam in ("he", "howz", "missile_he", "fmballe",
                "ap_missile", "ap_missile_tandem", "ap"):
        if fam not in per_fam:
            continue
        print(f"  ◆ {fam}")
        for fname in VIEW:
            c = per_fam[fam][fname]
            bits = "  ".join(f"{k}×{v}" for k, v in c.most_common(3))
            print(f"      {fname:<20} {bits}")
        print()

    # 步兵反坦克武器的 HE 弹（改造参照）
    sep("6a. 可直接照抄的参照：步兵反坦克武器的高爆弹")

    def _hex(o, name):
        for p in o["properties"]:
            if p["property_name"] == name:
                return p["value"].get("value_hex")
        return None

    for o in ammo_objs:
        k = ammo_key.get(o["id"])
        if k is None or mat.name_of_family(k[0]) != "DamageFamily_he":
            continue
        ih = scalar(o, "ImpactHappening")     # scalar 已把 strg_ref 解包成字符串
        if ih != "RoquetteRecoilessGun":
            continue
        toks = None
        for p in o["properties"]:
            if p["property_name"] == "TraitsToken":
                toks = [resolve_text(x, strings) for x in p["value"].get("items", [])]
        print(f"  #{o['id']:<6} tokens={toks}  "
              f"PiercingWeapon={scalar(o, 'PiercingWeapon')}  "
              f"TypeCategoryName={_hex(o, 'TypeCategoryName')}  "
              f"WeaponCursorType={scalar(o, 'WeaponCursorType')}  "
              f"MinMaxCategory={scalar(o, 'MinMaxCategory')}")

    print("\n" + "=" * 72)
    return 0


def _walk_refs(value):
    stack = [value]
    while stack:
        v = stack.pop()
        if not isinstance(v, dict):
            continue
        if v.get("type") == "obj_ref" and "object_id" in v:
            yield v
        for k in ("items", "value"):
            sub = v.get(k)
            if isinstance(sub, list):
                stack.extend(sub)
            elif isinstance(sub, dict):
                stack.append(sub)


if __name__ == "__main__":
    raise SystemExit(main())
