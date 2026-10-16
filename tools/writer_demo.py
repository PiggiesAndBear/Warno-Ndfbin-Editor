"""writer 使用示例：改一个 NDF 二进制里的字段并安全写回。

运行：
    python tools/writer_demo.py [目标文件]

默认对 Weapon.ndfbin 的副本操作（绝不碰原文件），演示四件事：
    1. 打开 + 解码
    2. 按「类名 + 属性名」定位字段（不依赖硬编码 ID）
    3. 改值 -> 重新编码 OBJE -> 原子写回 + 自动备份
    4. 重新解码校验，确认改动真的落盘
"""

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec, writer

# 玩家最常改的几个字段，全在 TAmmunitionDescriptor 上
TARGETS = {
    "PhysicalDamages": 99.0,        # 物理伤害
    "SuppressDamages": 5.0,         # 压制伤害
    "MaximumRangeGRU": 30000.0,     # 最大射程
    "MinimumRangeGRU": 0.0,         # 最小射程
    "TimeBetweenTwoShots": 1.5,     # 两发间隔（秒）
    "ShotsCountPerSalvo": 3,        # 每轮弹量
}

AMMO_CLASS = "TAmmunitionDescriptor"


def iter_values(value):
    """递归展开一个属性值，产出所有叶子节点 dict。"""
    if isinstance(value, dict):
        yield value
        for item in value.get("items", []):
            yield from iter_values(item)
        for key in ("key", "value"):
            if key in value:
                yield from iter_values(value[key])


def find_ammo_slots(objects):
    """找出所有 TAmmunitionDescriptor 对象里的目标字段。"""
    hits = []
    for obj in objects:
        if obj["class"] != AMMO_CLASS:
            continue
        found = {}
        for prop in obj["properties"]:
            if prop["property_name"] not in TARGETS:
                continue
            for leaf in iter_values(prop["value"]):
                # float 是 type_id 5，int 是 3
                if leaf.get("type_id") in (3, 5, 4, 2) and isinstance(leaf.get("value"), (int, float)):
                    found[prop["property_name"]] = leaf
                    break
        if found:
            hits.append((obj["id"], found))
    return hits


def edit_file(path: Path) -> int:
    raw = path.read_bytes()
    doc, decoded = cndf_codec.decode(raw, source=path)

    hits = find_ammo_slots(decoded["objects"])
    print(f"[1] 解码完成：对象 {decoded['object_count']} 个，{AMMO_CLASS} 命中 {len(hits)} 个")
    if not hits:
        print("    没有找到可改的弹药字段，未做任何写入。")
        return 1

    # 只改第一个命中的对象，够演示了
    object_id, slots = hits[0]
    changed = []
    for name, leaf in slots.items():
        old = leaf["value"]
        new = TARGETS[name]
        if old == new:
            continue
        leaf["value"] = new
        changed.append((name, old, new))

    if not changed:
        print("[2] 目标字段值已相同，未写入。")
        return 0

    print(f"[2] 准备修改 object_id={object_id}：")
    for name, old, new in changed:
        print(f"      {name}: {old} -> {new}")

    # 关键一步：把改过的对象图重新编码成 OBJE 字节，交给 writer
    replacements = writer.sections_of(doc)
    replacements["OBJE"] = cndf_codec.encode_objects(decoded["objects"])

    # 先 dry_run 预演：不碰文件，只验证重建结果能不能再次解析
    preview = writer.write_file(path, doc, replacements, dry_run=True)
    print(f"[3] dry_run 通过：{preview.size_before} -> {preview.size_after} 字节")

    result = writer.write_file(path, doc, replacements)
    print(f"[4] 写回完成：{result!r}")
    if result.backup:
        print(f"    原始备份：{result.backup}")

    # 落盘校验
    doc2, decoded2 = cndf_codec.decode(path.read_bytes(), source=path)
    hits2 = dict(find_ammo_slots(decoded2["objects"]))
    ok = True
    for name, old, new in changed:
        got = hits2.get(object_id, {}).get(name, {}).get("value")
        flag = "OK" if got == new else "FAIL"
        ok &= got == new
        print(f"    {flag} {name}: 重新读取 = {got}")
    print(f"    对象总数 {decoded2['object_count']}（原 {decoded['object_count']}）")
    return 0 if ok else 1


def main() -> int:
    default = _src.gfx() / "Weapon.ndfbin"
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    if not src.exists():
        print(f"找不到 {src}")
        return 1

    work = src.with_name(src.stem + "_writerDemo" + src.suffix)
    shutil.copy2(src, work)
    print(f"演示副本：{work}\n")
    return edit_file(work)


if __name__ == "__main__":
    raise SystemExit(main())