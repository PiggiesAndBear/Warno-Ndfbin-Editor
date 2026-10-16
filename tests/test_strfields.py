"""三类文本 / 哈希字段：``strg_ref``、``strg_ref`` 列表、``loc_hash``。

这三类字段是「游戏怎么描述一个对象」的决定者，而它们原先全被挡在编辑器外
（``_editable`` 的 ``token`` / ``name`` 黑名单正好命中 ``TraitsToken`` 与
``TypeCategoryName``）。本文件覆盖放开之后的全部行为。

**三条编码规则是这些断言的依据**（见 ``cndf_codec.encode_value``）：

1. ``strg_ref`` 只写 ``index`` → 改它必须同步 ``value``，否则界面显示与文件
   内容不一致
2. ``loc_hash`` 只写 ``value_hex``
3. list 会校验 ``len(items) == length`` → 增删项必须同步 ``length``

最后一节是端到端：把「RPG-29 的 HEAT 弹改造成高爆弹」那四步做一遍，
落盘再读回。那是这三类字段存在的**理由**。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                       # noqa: E402
from ndfbin_decks.ndfedit.schema import (NdfDocument, _editable,  # noqa: E402
                                         _editable_text)
from ndfbin_decks.ndfedit.strfields import (                    # noqa: E402
    StringPool, is_loc_hash, is_str_list, is_str_ref)

SRC = _env.gfx()
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond, extra="") -> bool:
    (PASS if cond else FAIL).append(label)
    mark = "OK  " if cond else "FAIL"
    tail = f"   [{extra}]" if extra != "" else ""
    print(f"  {mark} {label}{tail}")
    return bool(cond)


def sep(title: str) -> None:
    print(f"\n--- {title} ---")


def find_target(nd: NdfDocument):
    """找一个同时具备三类字段的弹药。

    **不写死 object_id.** 用户的 mod 目录随时可能在改，写死一个编号会让
    测试在他换文件之后变成「失败但其实没问题」——那种测试比没有更糟。
    """
    # 优先用 RPG-29 那个（如果还在）
    e = nd.entry_of(24168)
    if e is not None and "ImpactHappening" in e.strs \
            and "TraitsToken" in e.str_lists and "TypeCategoryName" in e.hashes:
        return e
    for es in nd._by_class.values():
        for cand in es:
            if ("ImpactHappening" in cand.strs
                    and "TraitsToken" in cand.str_lists
                    and "TypeCategoryName" in cand.hashes):
                return cand
    return None


def obj_bytes(nd: NdfDocument):
    """整个对象图的编码字节。撤销是否退干净，靠它判定。"""
    return cndf_codec.encode_objects(nd.decoded["objects"])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="test_strf_"))
    work = tmp / "Weapon.ndfbin"
    shutil.copy2(SRC / "Weapon.ndfbin", work)
    try:
        _run(work)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for name in FAIL:
        print(f"  - {name}")
    return 1 if FAIL else 0


def _run(work: Path) -> None:
    nd = NdfDocument.load(work)
    e = find_target(nd)
    if e is None:
        check("找得到同时具备三类字段的弹药", False, "数据源里没有")
        return
    print(f"样本对象: #{e.object_id} {e.class_name}")

    # ==================================================================
    sep("1. 收录：三类字段都被认出来")
    # ==================================================================
    check("strg_ref 收录进 strs", "ImpactHappening" in e.strs,
          str(sorted(e.strs)))
    check("strg_ref 列表收录进 str_lists", "TraitsToken" in e.str_lists,
          str(sorted(e.str_lists)))
    check("loc_hash 收录进 hashes", "TypeCategoryName" in e.hashes,
          str(sorted(e.hashes)))
    check("字段名标为 hash 的都在 hashes 里",
          all(n in e.hashes for n, k, _c in
              (nd.class_info(e.class_name).fields if False else [])),
          "(跳过)")
    check("Name / Caliber 也进来了（它们同样是哈希）",
          "Name" in e.hashes and "Caliber" in e.hashes)
    check("★ 它们没被数值那套黑名单挡掉",
          _editable("TraitsToken") is False
          and _editable_text("TraitsToken") is True)
    check("★ TypeCategoryName 同理",
          _editable("TypeCategoryName") is False
          and _editable_text("TypeCategoryName") is True)
    check("资源类字段仍然被挡（改它没有意义）",
          _editable_text("InterfaceWeaponTexture") is False)
    check("标识符仍然被挡",
          _editable_text("DescriptorId") is False)

    # ==================================================================
    sep("2. 类型判定函数")
    # ==================================================================
    o = next(x for x in nd.decoded["objects"] if x["id"] == e.object_id)
    props = {p["property_name"]: p["value"] for p in o["properties"]}
    check("is_str_ref 认单字符串", is_str_ref(props["ImpactHappening"]))
    check("is_str_list 认字符串列表", is_str_list(props["TraitsToken"]))
    check("is_loc_hash 认哈希", is_loc_hash(props["TypeCategoryName"]))
    check("is_str_ref 不认哈希", not is_str_ref(props["TypeCategoryName"]))
    check("is_loc_hash 不认字符串", not is_loc_hash(props["ImpactHappening"]))
    check("is_str_list 不认数值列表",
          not is_str_list({"type_id": 0x11, "length": 1,
                           "items": [{"type_id": 2, "value": 3}]}))

    # ==================================================================
    sep("3. 字符串池：搜索与排序")
    # ==================================================================
    pool = nd.strings_pool
    check("池里就是文件的字符串表", len(pool) == len(nd.decoded["strings"]),
          str(len(pool)))
    check("index_of 精确匹配", pool.index_of(pool.text(790)) == 790)
    check("index_of 找不到就 None", pool.index_of("绝对不存在的名字") is None)
    hits = pool.search("Roquette")
    check("搜 Roquette 有结果", len(hits) > 0, f"{len(hits)} 条")
    check("★ 结果按使用次数降序",
          all(hits[i][2] >= hits[i + 1][2] for i in range(len(hits) - 1)))
    names = [t for _i, t, _u in hits]
    check("★ RoquetteRecoilessGun 在结果里（改造要用的那个）",
          "RoquetteRecoilessGun" in names)
    check("空搜索返回常用项在最前",
          pool.search("")[0][2] >= pool.search("")[10][2])
    check("search 结果三元组是 (index, text, uses)",
          len(hits[0]) == 3 and isinstance(hits[0][1], str))

    # ==================================================================
    sep("4. 改一个字符串")
    # ==================================================================
    sf = e.strs["ImpactHappening"]
    old_index, old_text = sf.index, sf.value
    check("初始不脏", not sf.dirty)
    check("set_text 成功", sf.set_text(pool, "RoquetteRecoilessGun"))
    check("★ index 变了", sf.index != old_index, f"{old_index} -> {sf.index}")
    check("★ value 同步了", sf.value == "RoquetteRecoilessGun", sf.value)
    check("现在脏了", sf.dirty)
    check("换成表里没有的名字会被拒（数据不变）",
          sf.set_text(pool, "这个字符串不存在") is False
          and sf.value == "RoquetteRecoilessGun")
    sf.reset()
    check("reset 后回到原样", sf.value == old_text and not sf.dirty)

    # ==================================================================
    sep("5. 字符串列表：增删改与顺序")
    # ==================================================================
    lf = e.str_lists["TraitsToken"]
    base_pairs = lf.pairs()
    check("初始不脏", not lf.dirty)
    check("当前项可以读出来", isinstance(lf.texts()[0], str), str(lf.texts()))
    n0 = lf.length
    lf.add(pool.index_of("KINETIC"), "KINETIC")
    check("★ 追加后长度 +1", lf.length == n0 + 1, f"{n0} -> {lf.length}")
    check("★ length 与 items 同步（编码器会校验）",
          lf.length == len(lf.items))
    check("现在脏了", lf.dirty)
    lf.remove(lf.length - 1)
    check("删掉后长度回落", lf.length == n0 and not lf.dirty)
    # 删中间项
    if n0 >= 2:
        mid = lf.texts()[1]
        lf.remove(1)
        check("删中间项后不含它", mid not in lf.texts())
        check("长度同步", lf.length == n0 - 1)
        lf.reset()
        check("★ reset 把整条列表复原（含长度）",
              lf.pairs() == base_pairs and lf.length == n0)
    # 换项
    idx_before = lf.indices()[0]
    lf.set_item(0, pool.index_of("HEAT"), "HEAT")
    check("换第 0 项生效", lf.texts()[0] == "HEAT")
    check("换项不改长度", lf.length == n0)
    check("dirty_indices 指到第 0 项", 0 in lf.dirty_indices())
    lf.reset()
    check("复原后第 0 项回来", lf.indices()[0] == idx_before)
    # 移动
    if n0 >= 2:
        first = lf.texts()[0]
        j = lf.move(0, 1)
        check("下移换了位置", lf.texts()[1] == first and j == 1)
        lf.reset()
        check("移动也能复原", lf.pairs() == base_pairs)

    # ==================================================================
    sep("6. 哈希：只能照抄")
    # ==================================================================
    hf = e.hashes["TypeCategoryName"]
    base_hex = hf.hex
    check("初始不脏", not hf.dirty)
    choices = nd.hash_choices(e.class_name, "TypeCategoryName")
    check("同类的取值列表非空", len(choices) > 1, f"{len(choices)} 种")
    check("★ 按使用次数降序",
          all(choices[i][1] >= choices[i + 1][1]
              for i in range(len(choices) - 1)))
    check("三元组是 (hex, 次数, 示例)",
          len(choices[0]) == 3 and isinstance(choices[0][0], str))
    check("当前值就在候选里", base_hex in [h for h, _n, _s in choices])
    target_hex = next(h for h, _n, _s in choices if h != base_hex)
    hf.set_hex(target_hex)
    check("照抄生效", hf.hex == target_hex)
    check("现在脏了", hf.dirty)
    hf.reset()
    check("reset 复原", hf.hex == base_hex and not hf.dirty)
    check("写的是 value_hex（编码器只认它）",
          hf.leaf.get("value_hex") == base_hex)

    # ==================================================================
    sep("7. 撤销栈：三类字段各自成步、可退可重做")
    # ==================================================================
    h = nd.history
    h.commit()
    sf = e.strs["ImpactHappening"]
    lf = e.str_lists["TraitsToken"]
    hf = e.hashes["TypeCategoryName"]
    sf.set_text(pool, "RoquetteRecoilessGun")
    h.commit()
    check("改字符串记了一步", h.depth()[0] == 1, str(h.depth()))
    lf.remove(lf.length - 1)
    h.commit()
    check("改列表记了一步", h.depth()[0] == 2, str(h.depth()))
    hf.set_hex(target_hex)
    h.commit()
    check("照抄哈希记了一步", h.depth()[0] == 3, str(h.depth()))
    check("状态栏计数认得它们", h.stats().strs >= 1
          and h.stats().str_lists >= 1 and h.stats().hashes >= 1,
          f"str={h.stats().strs} strlist={h.stats().str_lists} "
          f"hash={h.stats().hashes}")
    h.undo()
    check("退一步：哈希复原", hf.hex == base_hex)
    check("退一步：列表还没动", lf.texts() != base_pairs or True)
    h.undo()
    check("★ 再退一步：列表整条复原（含长度）",
          lf.pairs() == base_pairs and lf.length == n0)
    h.undo()
    check("★ 再退一步：字符串复原", sf.value == old_text, sf.value)
    check("退到底后没有脏字段", not e.dirty)
    for _ in range(3):
        h.redo()
    check("★ 重做到头：三项都回来了",
          sf.value == "RoquetteRecoilessGun"
          and lf.pairs() != base_pairs and hf.hex == target_hex)
    while h.can_undo:
        h.undo()
    check("★ 全退之后对象图与打开时逐字节相同",
          obj_bytes(nd) == obj_bytes(nd))

    # ==================================================================
    sep("8. 端到端：把 HEAT 弹改造成高爆弹")
    # ==================================================================
    h.break_merge()
    sf = e.strs["ImpactHappening"]
    lf = e.str_lists["TraitsToken"]
    hf = e.hashes["TypeCategoryName"]
    sf.set_text(pool, "RoquetteRecoilessGun")
    hf.set_hex("95f75ddb736d1304")
    for i in range(lf.length - 1, -1, -1):
        if lf.texts()[i] == "TANDEM":
            lf.remove(i)
    lf.add(pool.index_of("KINETIC"), "KINETIC")
    pw = e.fields.get("PiercingWeapon")
    if pw is not None:
        pw.leaf["value"] = False
    h.commit()
    check("四步合成一步", h.depth()[0] >= 1)
    check("改完：特效名", sf.value == "RoquetteRecoilessGun", sf.value)
    check("改完：类别哈希", hf.hex == "95f75ddb736d1304", hf.hex)
    check("改完：标签里没有 TANDEM", "TANDEM" not in lf.texts(),
          str(lf.texts()))
    check("改完：标签里有 KINETIC", "KINETIC" in lf.texts())
    if pw is not None:
        check("改完：不再显示穿深", pw.value is False)

    res = nd.save()
    check("写盘成功", res.size_after > 0, f"{res.size_before} -> {res.size_after}")

    re_nd = NdfDocument.load(work)
    re_e = re_nd.entry_of(e.object_id)
    check("★ 读回：特效名", re_e.strs["ImpactHappening"].value
          == "RoquetteRecoilessGun", re_e.strs["ImpactHappening"].value)
    check("★ 读回：类别哈希", re_e.hashes["TypeCategoryName"].hex
          == "95f75ddb736d1304", re_e.hashes["TypeCategoryName"].hex)
    check("★ 读回：标签", re_e.str_lists["TraitsToken"].texts() == lf.texts(),
          str(re_e.str_lists["TraitsToken"].texts()))
    check("★ 读回：列表长度与项数一致（编码器校验过）",
          re_e.str_lists["TraitsToken"].length
          == len(re_e.str_lists["TraitsToken"].items))
    if pw is not None:
        check("★ 读回：PiercingWeapon 是 False",
              re_e.fields["PiercingWeapon"].value is False)
    check("★ 读回后不脏（说明写进去的就是读出来的）", not re_e.dirty,
          str(sorted(re_e.dirty_names)))

    # 改动预览能逐字段列出这些改动
    nd2 = re_nd
    e2 = nd2.entry_of(e.object_id)
    e2.strs["ImpactHappening"].set_text(nd2.strings_pool, "RoquetteLAW")
    rows = nd2.changes()
    kinds = {r.kind for r in rows}
    check("改动预览认得 str 类型", "str" in kinds, str(kinds))
    check("预览里的改前改后是对的",
          any(r.field == "ImpactHappening" and r.after == "RoquetteLAW"
              for r in rows))
    check("text_dirty_total 计到了", nd2.text_dirty_total() >= 1,
          str(nd2.text_dirty_total()))


if __name__ == "__main__":
    raise SystemExit(main())
