"""实体聚合视图的冒烟测试。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.ndfedit.schema import NdfDocument                  # noqa: E402
from ndfbin_decks.ndfedit import entity, refs                        # noqa: E402

MOD = _src.gfx()


def main() -> int:
    nd = NdfDocument.load(MOD / "Unit.ndfbin")
    print(f"对象 {len(nd.decoded['objects'])}")

    print("\n=== 1. 哪些类配得上实体视图 ===")
    ent_classes = [c.name for c in nd.classes
                   if entity.looks_like_entity(nd, c.name)]
    print(f"  {len(ent_classes)} 个类声明了 ModulesDescriptors")
    for c in ent_classes:
        info = nd.class_info(c)
        print(f"    {c:<34} {info.count} 个对象")
    assert "TEntityDescriptor" in ent_classes, "TEntityDescriptor 应该入选"
    assert not entity.looks_like_entity(nd, "TBlindageProperties")

    print("\n=== 2. 造一个漂亮点的题目：找一个有血量的坦克 ===")
    t0 = time.time()
    shared = entity._build_shared_lookup(nd)
    print(f"  共享查找表 {len(shared)} 项，耗时 {time.time() - t0:.2f}s")

    # 找一个真正的主战坦克：有 export 名、有血量、有装甲
    idx = nd.ref
    best = None
    for e in nd.entries("TEntityDescriptor"):
        label = idx.label_of(e.object_id)
        if not label.startswith("Descriptor_Unit_"):
            continue
        view = entity.build_entity_view(nd, e, shared_lookup=shared)
        hp = [r for r in view.rows if r.name == "MaxPhysicalDamages"]
        armor = [r for r in view.rows if r.name == "BlindageProperties"]
        speed = [r for r in view.rows if "Speed" in r.name]
        if hp and armor and speed:
            score = len(hp) + len(armor) + len(speed)
            if best is None or score > best[0]:
                best = (score, e, view)
    if best is None:
        print("  没找到像坦克的实体，退回任意实体")
        for e in nd.entries("TEntityDescriptor"):
            view = entity.build_entity_view(nd, e, shared_lookup=shared)
            if view.key_rows():
                best = (0, e, view)
                break
    _, e, view = best
    print(f"  {view.title}  (#{e.object_id})")
    print(f"  {view.summary()}")

    print("\n=== 3. 聚合行按分组排列 ===")
    for title, rows in view.groups().items():
        print(f"\n  【{title}】{len(rows)} 个字段")
        for r in rows[:8]:
            idx = nd.ref
            if r.kind == "ref":
                rf = r.entry.refs[r.name]
                val = refs.describe(idx, rf)
                extra = f"  候选 {rf.pool_size}"
            else:
                f = r.entry.fields[r.name]
                val = f.value
                extra = ""
            flag = "  ⚠共用" if r.shared else ""
            print(f"      {r.name:<38} {str(val)[:26]:<26}"
                  f" [{r.module_class}]{extra}{flag}")
        if len(rows) > 8:
            print(f"      … 还有 {len(rows) - 8} 个")

    print("\n=== 4. 共享警告是否准确 ===")
    sh = view.shared_modules()
    print(f"  {len(view.modules)} 个模块，{len(sh)} 个共用")
    for m in view.modules[:6]:
        tag = f"被 {m.shared_by} 个实体共用" if m.shared_by > 1 else "独享"
        ident = f"#{m.module_id}" if m.kind == "obj" else "外部"
        print(f"    {ident:<9} {m.module_class[:34]:<34} "
              f"{m.title[:24]:<24} {tag}")

    print("\n=== 5. 改血量真的落到原位了吗 ===")
    hprow = next(r for r in view.rows if r.name == "MaxPhysicalDamages")
    f = hprow.entry.fields["MaxPhysicalDamages"]
    print(f"  模块 #{hprow.module_id} 的血量原本是 {f.value}")
    before = f.value
    f.leaf["value"] = before * 2
    # 换一个共用同一模块的实体，看它是不是也变了
    same = [x for x in nd.entries(hprow.module_class)]
    print(f"  改成 {f.leaf['value']}")
    print(f"  dirty_count={nd.dirty_count()}  dirty_total={nd.dirty_total()}")
    f.reset()
    print(f"  撤销后 {f.value}   dirty_count={nd.dirty_count()}")

    print("\n=== 6. 外部模块（trans_ref）被认出来了吗 ===")
    ext = [m for m in view.modules if m.kind == "path"]
    print(f"  {view.title} 有 {len(ext)} 个外部模块")
    for m in ext:
        print(f"    {m.path}")

    print("\n=== 7. 非实体类不误判 ===")
    e2 = nd.entries("TBlindageProperties")[0]
    v2 = entity.build_entity_view(nd, e2, shared_lookup=shared)
    print(f"  TBlindageProperties -> ok={v2.ok}  note={v2.note}")
    assert not v2.ok

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
