"""筛选与批量改写的测试。

核心场景来自用户的原话：**「把对地射程 1925 的 Shell 提升至 2100」**。
实测该条件命中 152 条弹药（Shell 150 + AA Missile 2），
所以每条断言都用真实数据，不用构造的假对象。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from ndfbin_decks.core import cndf_codec, writer                    # noqa: E402
from ndfbin_decks.ndfedit import query as Q                         # noqa: E402
from ndfbin_decks.ndfedit.ammo import AMMO_CLASS, projectile_label  # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument                 # noqa: E402

SRC = _env.gfx()
RANGE = "MaximumRangeGRU"
DAMAGE = "PhysicalDamages"
TYPE = "ProjectileType"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def roundtrip(nd):
    repl = writer.sections_of(nd.doc)
    repl["OBJE"] = cndf_codec.encode_objects(nd.decoded["objects"])
    out = writer.write_bytes(nd.doc, repl)
    _d, dec = cndf_codec.decode(out, source="<rebuilt>")
    return dec


def main() -> int:
    print("\n=== 1. 条件匹配按值类型自适应 ===")
    # float：玩家看到 0.1320，照着输入 0.132，必须命中 float32 的
    #       0.13199999928474426
    c = Q.Condition(DAMAGE, Q.MATCH_EQUAL, value=0.132, kind="float")
    check("float 按显示精度匹配",
          c.match(0.13199999928474426), "0.132 vs 0.13199999928474426")
    check("float 精度外不误判", not c.match(0.1505))
    ci = Q.Condition("Number", Q.MATCH_EQUAL, value=1, kind="int")
    check("int 精确匹配", ci.match(1) and not ci.match(2))
    cb = Q.Condition("Flag", Q.MATCH_EQUAL, value=True, kind="bool")
    check("bool 真值匹配", cb.match(True) and not cb.match(False))
    cg = Q.Condition(RANGE, Q.MATCH_GE, value=2000, kind="float")
    check("不小于", cg.match(2100) and not cg.match(1925))
    cl = Q.Condition(RANGE, Q.MATCH_LE, value=2000, kind="float")
    check("不大于", cl.match(1925) and not cl.match(2100))
    cr = Q.Condition(RANGE, Q.MATCH_RANGE, low=1800, high=2000, kind="float")
    check("区间含端点", cr.match(1800) and cr.match(2000))
    check("区间排除外点", not cr.match(1750) and not cr.match(2100))
    cm = Q.Condition(TYPE, Q.MATCH_IN, values=(1, 5), kind="int")
    check("枚举多选", cm.match(1) and cm.match(5) and not cm.match(2))
    check("缺失值不匹配", not c.match(None))

    print("\n=== 2. 未填完的条件不参与筛选 ===")
    empty = Q.Condition(RANGE, Q.MATCH_RANGE, kind="float")
    check("空区间不可用", not empty.is_usable())
    no_val = Q.Condition(RANGE, Q.MATCH_EQUAL, kind="float")
    check("空值不可用", not no_val.is_usable())
    check("空多选不可用", not Q.Condition(TYPE, Q.MATCH_IN, values=(),
                                          kind="int").is_usable())
    q = Q.Query([empty, no_val])
    check("Query 跳过不可用条件", q.usable() == [])

    print("\n=== 3. 真实文件：字段画像 ===")
    nd = NdfDocument.load(SRC / "Weapon.ndfbin")
    ammo = nd.entries(AMMO_CLASS)
    check(f"弹药 {len(ammo)} 条", len(ammo) > 1500, str(len(ammo)))
    rng_counts = Q.value_counts(ammo, RANGE)
    # 取值种类与数据相关，只断言量级——mod 目录里加过一个射程不同的弹药
    # 就会多一种取值，那不是缺陷。
    check(f"射程有 {len(rng_counts)} 种取值", len(rng_counts) > 50,
          str(len(rng_counts)))
    check("1925 是最常见的射程", rng_counts[1925.0] == 152,
          str(rng_counts[1925.0]))
    check("射程不算枚举（种类过多）", not Q.is_enum_like(ammo, RANGE),
          f"{len(rng_counts)} 种")
    check("弹种算枚举", Q.is_enum_like(ammo, TYPE),
          f"{len(Q.value_counts(ammo, TYPE))} 种")

    print("\n=== 4. 用户原场景：射程 1925 的 Shell ===")
    q = Q.Query([
        Q.Condition(TYPE, Q.MATCH_IN, values=(1,), kind="int"),        # Shell
        Q.Condition(RANGE, Q.MATCH_EQUAL, value=1925, kind="float"),
    ])
    hits = q.select(ammo)
    check(f"命中 {len(hits)} 条", len(hits) == 150, str(len(hits)))
    kinds = {projectile_label(e.fields[TYPE].value) for e in hits}
    check("命中项弹种全是 Shell", kinds == {"Shell"}, str(kinds))
    check("描述句可读", "等" in q.describe() or "1925" in q.describe(),
          q.describe())

    print("\n=== 5. 不限定弹种会误伤（这就是要加条件的原因）===")
    only_range = Q.Query([Q.Condition(RANGE, Q.MATCH_EQUAL, value=1925,
                                      kind="float")])
    all_hits = only_range.select(ammo)
    check(f"只按射程命中 {len(all_hits)} 条", len(all_hits) == 152,
          str(len(all_hits)))
    mixed = {projectile_label(e.fields[TYPE].value) for e in all_hits}
    check("其中混着别的弹种", "AA Missile" in mixed, str(mixed))
    check("比加弹种条件多 2 条", len(all_hits) - len(hits) == 2)

    print("\n=== 6. 预览不改数据 ===")
    res = Q.preview(ammo, q, RANGE, 2100, "float")
    check("matched 对", res.matched == 150, str(res.matched))
    check("changed 对", res.changed == 150, str(res.changed))
    check("新值已记录", res.new_value == 2100.0, repr(res.new_value))
    check("改前分布正确", res.before[1925.0] == 150, str(dict(res.before)))
    still = {e.fields[RANGE].value for e in hits}
    check("预览后磁盘数据未动", still == {1925.0}, str(still))
    check("摘要可读", "150" in res.summary(), res.summary())
    check("改前分布句可读", "1925" in res.before_text(),
          res.before_text())

    print("\n=== 7. 应用并落盘 ===")
    applied = Q.apply_value(ammo, q, RANGE, 2100, "float")
    check("应用数量一致", applied.changed == 150, str(applied.changed))
    after = {e.fields[RANGE].value for e in hits}
    check("150 条都变成 2100", after == {2100.0}, str(after))
    check("dirty 计数反映改动", nd.dirty_count() == 150,
          str(nd.dirty_count()))
    untouched = [e for e in ammo
                 if e.fields.get(RANGE) and e.fields[RANGE].value == 1925.0]
    check("其余 1925 的两条 AA Missile 未被动", len(untouched) == 2,
          str(len(untouched)))

    dec = roundtrip(nd)
    def range_of(obj_id):
        o = next(o for o in dec["objects"] if o["id"] == obj_id)
        p = next(p for p in o["properties"] if p["property_name"] == RANGE)
        return p["value"]["value"]
    check("往返后 2100 保留", range_of(hits[0].object_id) == 2100.0,
          str(range_of(hits[0].object_id)))
    check("往返后未命中项不受影响",
          range_of(untouched[0].object_id) == 1925.0,
          str(range_of(untouched[0].object_id)))

    print("\n=== 8. 撤销完整恢复 ===")
    for e in hits:
        e.fields[RANGE].reset()
    check("撤销后回到 1925",
          {e.fields[RANGE].value for e in hits} == {1925.0})
    check("撤销后不脏", nd.dirty_count() == 0, str(nd.dirty_count()))

    print("\n=== 9. 幂等：再应用一次不该重复计数 ===")
    again = Q.preview(ammo, q, RANGE, 1925, "float")
    check("目标值等于原值时 changed 为 0", again.changed == 0,
          f"changed={again.changed} unchanged={again.unchanged}")
    check("unchanged 正确", again.unchanged == 150, str(again.unchanged))
    check("摘要说明无需改动", "无需改动" in again.summary(),
          again.summary())

    print("\n=== 10. 无命中的情形 ===")
    none = Q.Query([Q.Condition(RANGE, Q.MATCH_EQUAL, value=99999,
                                kind="float")])
    r0 = Q.preview(ammo, none, RANGE, 1000, "float")
    check("matched 为 0", r0.matched == 0)
    check("摘要提示无命中", "没有对象" in r0.summary(), r0.summary())

    print("\n=== 11. 缺字段的对象被排除 ===")
    # 一部分弹药没有 MaximumRangeGRU。数量按数据现算——写死会让
    # 「用户在 mod 目录加过一个弹药」变成一次误报。
    missing = [e for e in ammo if RANGE not in e.fields]
    check(f"{len(missing)} 条缺射程字段", len(missing) > 0,
          str(len(missing)))
    q_all = Q.Query([Q.Condition(RANGE, Q.MATCH_GE, value=0, kind="float")])
    got = q_all.select(ammo)
    check("按射程筛时缺字段的不入选", len(got) == len(ammo) - len(missing),
          f"{len(got)} vs {len(ammo) - len(missing)}")

    print("\n=== 12. 类型转换（界面会传字符串）===")
    check("字符串转 float", Q.coerce_value("2100", "float") == 2100.0)
    check("字符串转 int", Q.coerce_value("2100", "int") == 2100
          and isinstance(Q.coerce_value("2100", "int"), int))
    check("小数转 int 四舍五入", Q.coerce_value(9.7, "int") == 10)
    check("转 bool", Q.coerce_value(1, "bool") is True)
    check("相等比较跨类型", Q.values_equal(2100, "2100.0", "float"))

    print("\n" + "=" * 54)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：")
        for n in FAIL:
            print(f"  - {n}")
    print("=" * 54)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
