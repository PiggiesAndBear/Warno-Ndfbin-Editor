"""弹药对象能不能新增？—— 编码层 + 语义层实测。

    python tools/probe_add_ammo.py

背景：`NdfDocument.add_field` 只给**已存在的对象**补属性，对象总数不变
（见 `probe_add_field.py`）。这里问的是更进一步的事：往 OBJE 里**多塞一个
对象**行不行。

项目里已经有先例——Deck 编辑器追加 pack 时走
`cndf_codec.append_graph_objects`（`edit/equip.py`），它会一起扩
CLAS/PROP/CHNK 并跑一次完整 decode 校验。但那是 Deck 域，且**不动 TOPO**。
弹药是不是同一回事，必须实测。

要回答的六件事，任何一件为「否」都会改变结论：

1. 追加编码能不能过（往返 + 完整 decode 校验）
2. 新对象要不要进 TOPO（顶层对象表）
3. 谁引用弹药——新增之后靠什么被游戏用到
4. 有没有「标识符型」属性会让克隆体与原体撞车
5. 现有保存链路能不能带上 CLAS/PROP 的增长
6. obj_ref 校验会不会因为新对象而失效
"""

from __future__ import annotations

import copy
import struct
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec, cndf_layout as ndf      # noqa: E402
from ndfbin_decks.ndfedit.ammo import AMMO_CLASS                   # noqa: E402
from ndfbin_decks.ndfedit.refs import RefIndex, is_reference       # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument, name_is_identifier  # noqa: E402

SRC = _src.gfx()
FILE = "Weapon.ndfbin"


def sep(title: str) -> None:
    print(f"\n=== {title} ===")


def object_size(obj: dict) -> int:
    """这个对象在 OBJE 里占多少字节（用编码器实测，不推算）。"""
    probe = [dict(obj, id=0)]
    return len(cndf_codec.encode_objects(probe))


def main() -> int:
    path = SRC / FILE
    if not path.is_file():
        print(f"跳过：找不到 {path}")
        return 0

    raw = path.read_bytes()
    doc, dec = cndf_codec.decode(raw, source=str(path))
    sec = ndf.section_map(doc.sections)
    objs = dec["objects"]
    classes = dec["classes"]
    print(f"文件：{FILE}  {len(raw):,} 字节")
    print(f"分区：{[(s.name, s.size) for s in doc.sections]}")
    print(f"对象 {len(objs):,} · 类 {len(classes)} · 属性 {len(dec['properties'])}")

    # ================================================================
    sep("1. 弹药对象长什么样")
    ammo = [o for o in objs if o["class"] == AMMO_CLASS]
    sizes = sorted(len(o["properties"]) for o in ammo)
    print(f"{AMMO_CLASS}: {len(ammo)} 个对象")
    print(f"  属性数：最少 {sizes[0]} / 中位 {sizes[len(sizes)//2]} / 最多 {sizes[-1]}")
    print(f"  单对象编码体积：{object_size(ammo[0])} 字节（含 class_id 与结束标记）")

    ident = [(p["property_name"], p["value"].get("type_id"))
             for p in ammo[0]["properties"]
             if name_is_identifier(p["property_name"])]
    print(f"  标识符型属性（编辑器刻意不开放的）：{ident}")
    hashed = [(p["property_name"], hex(p["value"]["type_id"]))
              for p in ammo[0]["properties"]
              if p["value"].get("value_hex") is not None]
    print(f"  哈希型属性（不可逆）：{hashed}")

    # 同类的所有对象是不是属性集一致？不一致的话克隆谁就有讲究
    sigs = Counter(tuple(sorted(p["property_name"]
                               for p in o["properties"])) for o in ammo)
    print(f"  属性集签名种类：{len(sigs)} 种"
          f"（最常见一种覆盖 {sigs.most_common(1)[0][1]} 个）")

    # ================================================================
    sep("2. TOPO：顶层对象表")
    topo = ndf.parse_u32_list(doc.full_data, sec.get("TOPO"))
    print(f"TOPO = {len(topo)} 个顶层对象索引（对象总数的 "
          f"{len(topo)/len(objs)*100:.1f}%）")
    ammo_top = [o for o in ammo if o["is_top_object"]]
    print(f"  ★ 弹药里在 TOPO 中的：{len(ammo_top)} / {len(ammo)}")
    print(f"  TOPO 的类分布 top6："
          f"{Counter(o['class'] for o in objs if o['is_top_object']).most_common(6)}")

    # ================================================================
    sep("3. 谁引用弹药")
    index = RefIndex(dec)
    rev = index.reverse
    sample = ammo[len(ammo) // 2]
    users = rev.get(sample["id"], [])
    print(f"样本弹药 #{sample['id']}（{index.label_of(sample['id'])[:40]}）")
    print(f"  被 {len(users)} 个字段引用，引用者分布：")
    print(f"    {Counter(objs[u]['class'] for u, _f in users).most_common(8)}")
    print(f"    字段名：{Counter(f for _u, f in users).most_common(8)}")

    print("\n  全部弹药（1548 个）的引用者汇总：")
    ref_cls: Counter = Counter()
    ref_field: Counter = Counter()
    no_user = 0
    for o in ammo:
        u = rev.get(o["id"], [])
        if not u:
            no_user += 1
            continue
        for uid, fname in u:
            ref_cls[objs[uid]["class"]] += 1
            ref_field[fname] += 1
    print(f"    引用者类：{ref_cls.most_common(6)}")
    print(f"    字段名：{ref_field.most_common(6)}")
    print(f"    ★ 没有任何字段引用的弹药：{no_user} 个")

    multi = Counter(len(rev.get(o["id"], [])) for o in ammo)
    print(f"    每个弹药被几个字段引用：{sorted(multi.items())[:8]}")

    sep("3b. 引用字段是单个引用还是一串引用")
    fields = Counter()
    for uid, fname in ((u, f) for o in ammo for u, f in rev.get(o["id"], [])):
        obj = objs[uid]
        prop = next(p for p in obj["properties"] if p["property_name"] == fname)
        fields[(obj["class"], fname, is_reference(prop["value"]))] += 1
    for (cls, fname, kind), n in fields.most_common(6):
        print(f"    {cls}.{fname}  引用类型={kind}  {n} 处")

    # ================================================================
    sep("4. 追加试验：克隆一个弹药对象")
    src = ammo[0]
    clone = copy.deepcopy(src)
    clone["id"] = len(objs)
    clone["is_top_object"] = False          # TOPO 不动，先如实声明
    print(f"克隆 #{src['id']} → 新 id #{clone['id']}，类 {clone['class']}，"
          f"属性 {len(clone['properties'])} 个")

    try:
        out = cndf_codec.append_graph_objects(doc, dec, [clone])
    except Exception as exc:                                   # noqa: BLE001
        print(f"  FAIL 追加失败：{type(exc).__name__}: {exc}")
        return 1
    print(f"  OK 追加成功（内部已跑过一次完整 decode 校验）")

    doc2, dec2 = cndf_codec.decode(out, source="<appended>")
    objs2 = dec2["objects"]
    got = objs2[clone["id"]]
    print(f"  文件 {len(raw):,} → {len(out):,} 字节（+{len(out)-len(raw):,}）")
    for name in ("OBJE", "CHNK", "CLAS", "PROP", "TOPO"):
        a = sec[name].size
        b = ndf.section_map(doc2.sections)[name].size
        same_bytes = (doc.full_data[sec[name].offset:sec[name].offset + a]
                      == doc2.full_data[ndf.section_map(doc2.sections)[name].offset:
                                         ndf.section_map(doc2.sections)[name].offset + b])
        flag = "字节未变" if same_bytes else "内容已变"
        print(f"    {name:<5} {a:>9,} → {b:>9,} 字节  {flag}")
    print(f"  ★ 注意：文件总长 {len(raw):,} → {len(out):,}。"
          f"zstd 重新压缩后长度与「加了几个对象」不成正比，"
          f"所以体积不能当校验依据。")
    print(f"  对象数 {len(objs):,} → {len(objs2):,}")
    print(f"  类表 {len(dec['classes'])} → {len(dec2['classes'])}"
          f"　属性表 {len(dec['properties'])} → {len(dec2['properties'])}")
    print(f"  新对象：id={got['id']} class={got['class']} "
          f"class_id={got['class_id']} 属性 {len(got['properties'])} 个"
          f"　is_top_object={got['is_top_object']}")
    same = ([(p["property_name"], p["value"].get("type_id"))
             for p in got["properties"]]
            == [(p["property_name"], p["value"].get("type_id"))
                for p in src["properties"]])
    print(f"  属性集与原件一致：{same}")

    print("  --- 字节稳定性：再解一次再编一次 ---")
    again = cndf_codec.rebuild_objects(doc2, dec2)
    print(f"    rebuild_objects 后与首次产物一致：{again == out}")

    # ================================================================
    sep("5. 把它当成文件让 NdfDocument 读（编辑器那一层）")
    tmp = Path(tempfile.mkdtemp(prefix="probe_add_ammo_"))
    try:
        dst = tmp / FILE
        dst.write_bytes(out)
        nd = NdfDocument.load(dst)
        n_ammo = len(nd.entries(AMMO_CLASS))
        print(f"  NdfDocument 读得进：{len(nd.decoded['objects']):,} 个对象，"
              f"{AMMO_CLASS} {n_ammo} 个（原 {len(ammo)}）")
        e = nd.entries(AMMO_CLASS)[n_ammo - 1] if n_ammo else None
        print(f"  最后一个弹药条目：id={e.object_id if e else None} "
              f"字段 {len(e.fields) if e else 0} 个 "
              f"引用 {len(e.refs) if e else 0} 个")

        sep("6. 保存链路覆盖度")
        from ndfbin_decks.core import writer
        repl = nd.replacements()
        print(f"  NdfDocument.replacements() 的键：{sorted(repl)}")
        print("    键虽然齐，但**只有 OBJE 被重新编码**，其余是原样透传。")
        changed = writer.changed_sections(nd.doc, repl)
        print(f"    相对 doc 实际变化的 section：{sorted(changed)}"
              f"（空 = 这份文件与磁盘一致）")
        print(f"  writer.sections_of() 的键：{sorted(writer.sections_of(nd.doc))}")
        print("  → 追加对象时只要在 replacements 里同时覆盖 "
              "OBJE/CHNK/CLAS/PROP/TOPO，就能走 writer 拿到原子写 + .bak + 过期保护")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ================================================================
    sep("7. 把新对象也加进 TOPO 会怎样")
    n_top_before = len(topo)
    new_topo = struct.pack(f"<{len(topo)+1}I", *topo, clone["id"])
    try:
        out2 = cndf_codec.rebuild_sections(doc2, {"TOPO": new_topo})
        doc3, dec3 = cndf_codec.decode(out2, source="<topo>")
        got3 = dec3["objects"][clone["id"]]
        print(f"  OK TOPO {n_top_before} → "
              f"{len(ndf.parse_u32_list(doc3.full_data, ndf.section_map(doc3.sections)['TOPO']))}"
              f"，新对象 is_top_object={got3['is_top_object']}")
        print(f"     文件再 +{len(out2)-len(out):,} 字节")
    except Exception as exc:                                   # noqa: BLE001
        print(f"  FAIL 扩 TOPO 失败：{type(exc).__name__}: {exc}")

    # ================================================================
    sep("8. 引用校验：旧对象指向新对象行不行")
    holder = next((o for o in objs
                   if any(is_reference(p["value"]) == "objref"
                          for p in o["properties"])), None)
    if holder is None:
        print("  样本里没有 obj_ref 字段（跳过）")
    else:
        field = next(p for p in holder["properties"]
                     if is_reference(p["value"]) == "objref")
        probe = copy.deepcopy(holder)
        probe["id"] = len(objs2)
        node = next(p for p in probe["properties"]
                    if p["property_name"] == field["property_name"])["value"]
        node["object_id"] = clone["id"]
        node["class_id"] = got["class_id"]
        try:
            out3 = cndf_codec.append_graph_objects(doc2, dec2, [probe])
            cndf_codec.decode(out3, source="<ref>")
            print(f"  OK 旧对象 #{holder['id']} 的 "
                  f"{field['property_name']} 可以指向新对象 #{clone['id']}")
        except Exception as exc:                               # noqa: BLE001
            print(f"  FAIL {type(exc).__name__}: {exc}")

        # 反例：class_id 故意写错，验证校验确实在
        bad = copy.deepcopy(probe)
        bad["id"] = len(objs2)
        bnode = next(p for p in bad["properties"]
                     if p["property_name"] == field["property_name"])["value"]
        bnode["object_id"] = clone["id"]
        bnode["class_id"] = (got["class_id"] + 1) % len(dec2["classes"])
        try:
            cndf_codec.decode(
                cndf_codec.append_graph_objects(doc2, dec2, [bad]),
                source="<bad>")
            print("  FAIL 写错 class_id 竟然也通过了 —— 校验不在")
        except Exception as exc:                               # noqa: BLE001
            print(f"  OK 写错 class_id 被拦下：{type(exc).__name__}: {exc}")

    sep("9. 身份字段：GUID 与 loc_hash 的唯一性")
    def hexval(obj, name):
        for p in obj["properties"]:
            if p["property_name"] == name:
                return p["value"].get("value_hex")
        return None

    for field_name in ("DescriptorId", "Name", "Caliber",
                       "TypeCategoryName", "WeaponDescriptionToken"):
        vals = [hexval(o, field_name) for o in ammo]
        vals = [v for v in vals if v is not None]
        uniq = len(set(vals))
        verdict = ("唯一 → 是身份标识" if uniq == len(vals)
                   else f"有 {len(vals)-uniq} 个重复 → 不是唯一标识")
        print(f"  {field_name:<24} 出现在 {len(vals):>4} / {len(ammo)} 个弹药上，"
              f"去重后 {uniq:>4} 个　{verdict}")

    print("\n  DescriptorId(guid) 在别的类上也出现吗：")
    holders = Counter(o["class"] for o in objs
                      if any(p["property_name"] == "DescriptorId"
                             for p in o["properties"]))
    print(f"    {holders.most_common(8)}")

    print("\n  全文件 guid 型属性的稀缺程度（出现次数 / 取值去重）：")
    guid_fields: Counter = Counter()
    for o in objs[:4000]:
        for p in o["properties"]:
            if p["value"].get("type_id") == 0x1A:
                guid_fields[(o["class"], p["property_name"])] += 1
    print(f"    前 4000 个对象里 guid 型字段：{guid_fields.most_common(8)}")

    sep("10. 新弹药会带哪些属性：克隆谁有讲究")
    sig = Counter(tuple(sorted(p["property_name"] for p in o["properties"]))
                  for o in ammo)
    print(f"  223 种属性集签名 → 克隆不同的弹药会得到不同的属性集")
    print(f"  最完整的那个弹药带 {max(len(o['properties']) for o in ammo)} 个属性，"
          f"最少的带 {min(len(o['properties']) for o in ammo)} 个")
    common = Counter()
    for o in ammo:
        for p in o["properties"]:
            common[p["property_name"]] += 1
    always = [n for n, c in common.items() if c == len(ammo)]
    print(f"  出现在全部 1548 个弹药上的属性：{len(always)} / {len(common)}")

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
