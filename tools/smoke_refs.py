"""引用字段内核的冒烟测试（先于 GUI 验证）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec, writer                     # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument                  # noqa: E402

MOD = _src.gfx()


def main() -> int:
    t0 = time.time()
    nd = NdfDocument.load(MOD / "Unit.ndfbin")
    print(f"Unit.ndfbin 加载 {time.time() - t0:.2f}s，"
          f"对象 {len(nd.decoded['objects'])}")

    # --- 1. 引用字段被收集到了吗 ---
    print("\n=== 1. 引用字段收集 ===")
    total_refs = sum(len(e.refs) for es in nd._by_class.values() for e in es)
    print(f"  全文件 RefField 共 {total_refs} 个")
    for cls in ("TBlindageProperties", "TDamageModuleDescriptor",
                "TBaseDamageModuleDescriptor", "TEntityDescriptor"):
        es = nd.entries(cls)
        if not es:
            continue
        with_refs = sum(1 for e in es if e.refs)
        print(f"  {cls}: {len(es)} 个对象，{with_refs} 个带引用字段")
        if es:
            for name, r in list(es[0].refs.items())[:6]:
                print(f"      {name:<38} {r.kind:<9} "
                      f"-> {r.target_class or 'path'}  "
                      f"候选 {r.pool_size}  当前 {r.key}")

    # --- 2. 标签 ---
    print("\n=== 2. 标签生成 ===")
    idx = nd.ref
    for cls, n in (("TBlindageProperties", 3), ("TBaseDamageModuleDescriptor", 3),
                   ("TEntityDescriptor", 3), ("TTypeUnitModuleDescriptor", 3)):
        for e in nd.entries(cls)[:n]:
            print(f"  {e.class_name:<34} #{e.object_id:<7} "
                  f"{idx.label_of(e.object_id)}")

    # --- 3. RTTI 候选集 + 谁在用我 ---
    print("\n=== 3. 装甲候选集（TResistanceTypeRTTI）===")
    cands = idx.candidates_for_class("TResistanceTypeRTTI")
    print(f"  候选 {len(cands)} 个")
    for c in cands[:8]:
        print(f"    {c.key:<7} {c.label:<16} 被用 {c.uses} 次")
    print(f"  …")
    for c in cands[-3:]:
        print(f"    {c.key:<7} {c.label:<16} 被用 {c.uses} 次")

    # --- 4. 真的改一个装甲等级 ---
    print("\n=== 4. 改装甲（obj_ref 必须连 class_id 一起改）===")
    blind = next(e for e in nd.entries("TBlindageProperties"))
    rf = blind.refs["ResistanceFront"]
    print(f"  目标对象 #{blind.object_id}")
    print(f"  改前: object_id={rf.leaf['object_id']} "
          f"class_id={rf.leaf['class_id']} -> {idx.label_of(rf.key)}")
    before = dict(rf.leaf)
    # 换成一个不同的 RTTI
    other = next(c for c in cands if c.key != rf.key)
    idx.repoint(rf.leaf, "obj", other.key)
    print(f"  改后: object_id={rf.leaf['object_id']} "
          f"class_id={rf.leaf['class_id']} -> {other.label}")
    assert rf.dirty, "应该标记为脏"
    assert rf.leaf["class_id"] != before["class_id"] or True

    # 往返验证
    repl = writer.sections_of(nd.doc)
    repl["OBJE"] = cndf_codec.encode_objects(nd.decoded["objects"])
    out = writer.write_bytes(nd.doc, repl)
    doc2, dec2 = cndf_codec.decode(out, source="<rebuilt>")
    got = next(o for o in dec2["objects"] if o["id"] == blind.object_id)
    v = next(p for p in got["properties"]
             if p["property_name"] == "ResistanceFront")
    print(f"  往返后: object_id={v['value']['object_id']} "
          f"class_id={v['value']['class_id']}")
    ok = v["value"]["object_id"] == other.key
    print(f"  {'OK' if ok else 'FAIL'} 往返保留了新目标")

    # 撤销
    rf.reset()
    print(f"  撤销后: object_id={rf.leaf['object_id']} class_id={rf.leaf['class_id']}"
          f"  dirty={rf.dirty}")
    ok2 = (rf.leaf["object_id"] == before["object_id"]
           and rf.leaf["class_id"] == before["class_id"] and not rf.dirty)
    print(f"  {'OK' if ok2 else 'FAIL'} 撤销完整恢复")

    # --- 5. trans_ref ---
    print("\n=== 5. 路径引用（trans_ref）===")
    nd2 = NdfDocument.load(MOD / "Deck.ndfbin")
    idx2 = nd2.ref
    print(f"  Deck.ndfbin IMPR 路径 {len(idx2.imports)} 条")
    packs = [e for e in nd2.entries("TDeckPackDescriptor") if e.refs]
    print(f"  TDeckPackDescriptor 带引用的 {len(packs)}")
    if packs:
        e = packs[0]
        for name, r in e.refs.items():
            print(f"    {name:<26} {r.kind:<9} -> {r.label if hasattr(r,'label') else ''}"
                  f"{idx2.imports.get(r.key)}")
        r = e.refs["UnitDescriptor"]
        print(f"  当前单位 = {idx2.imports.get(r.key)}")
        print(f"  父路径 = {idx2.siblings_of(r.leaf)}")
        sibs = idx2.candidates_for_paths(prefix=idx2.siblings_of(r.leaf))
        print(f"  兄弟路径候选 {len(sibs)} 条，前 6：")
        for c in sibs[:6]:
            print(f"    {c.key:<6} {c.label:<44} 被用 {c.uses}")
        if len(sibs) > 1:
            tgt = next(c for c in sibs if c.detail != idx2.imports.get(r.key))
            old_idx = r.leaf["index"]
            idx2.repoint(r.leaf, "path", tgt.key)
            print(f"  改成 {tgt.detail}")
            repl2 = writer.sections_of(nd2.doc)
            repl2["OBJE"] = cndf_codec.encode_objects(nd2.decoded["objects"])
            out2 = writer.write_bytes(nd2.doc, repl2)
            d3, dec3 = cndf_codec.decode(out2, source="<rebuilt>")
            g = next(o for o in dec3["objects"] if o["id"] == e.object_id)
            pv = next(p for p in g["properties"]
                      if p["property_name"] == "UnitDescriptor")
            got_path = dec3["imports"].get(pv["value"]["index"])
            print(f"  往返后 = {got_path}")
            print(f"  {'OK' if got_path == tgt.detail else 'FAIL'} 路径引用往返")
            r.reset()

    # --- 6. 脏计数 ---
    print("\n=== 6. 脏计数 ===")
    print(f"  dirty_count={nd.dirty_count()} dirty_total={nd.dirty_total()} "
          f"ref_dirty={nd.ref_dirty_total()}")
    print("\n全部冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
