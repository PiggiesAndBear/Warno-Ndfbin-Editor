"""勘查三：引用目标能不能生成人类可读的标签。

决定 UI 能不能做下拉框的最后一环。一个 obj_ref 字段即使候选集只有 62 个，
如果那 62 个对象全是「#1234」这种不可读的 id，用户照样选不了。

本脚本对高价值目标类逐一评估五种标签来源的命中率：
  1. export 名（$/GFX/... 的末段）
  2. 文本字段（strg_ref / wide_string / trans_ref 路径）
  3. Name 型字段的 loc_hash（能显示成 hex 前 8 位）
  4. 一个指向别处的引用（可以递归借标签）
  5. 兜底（只能显示 object_id）
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                          # noqa: E402
from ndfbin_decks.core.cndf_layout import (                       # noqa: E402
    OBJECT_REFERENCE, TRANS_REFERENCE, _VALUE_NAMES)

MOD = _src.gfx()

# 每个文件里值得做成下拉的「目标类」
INTEREST = {
    "Weapon.ndfbin": [
        "TResistanceTypeRTTI", "TDamageTypeRTTI", "TDiceHitRollRuleDescriptor",
        "TTurretIdleBehaviourDescriptor", "TBlindageProperties",
        "TAmmunitionDescriptor", "TMountedWeaponDescriptor",
        "TTurretTwoAxisDescriptor", "TStairsDamageTypeEvolutionOverRangeDescriptor",
        "TEntityDescriptor",
    ],
    "Unit.ndfbin": [
        "TResistanceTypeRTTI", "TBlindageProperties", "TDamageLevelsPackDescriptor",
        "TDamageLevelDescriptor", "TBaseDamageModuleDescriptor",
        "TDamageModuleDescriptor", "TTypeUnitModuleDescriptor",
    ],
    "Deck.ndfbin": [
        "TDeckPackDescriptor", "TDeckSmartGroupDescriptor",
    ],
}

NAME_FIELDS = ("name", "id", "descriptorid", "descriptor", "label",
               "classname", "identifier", "tag", "debug")


def classify_field(v):
    """给一个值判个「可读性类别」。"""
    t = v.get("type_id")
    if t == OBJECT_REFERENCE:
        return "ref"
    if t == TRANS_REFERENCE:
        return "path"
    if t in (7, 0x1C):                       # strg_ref / file_strg_ref
        return "text"
    if t == 8:                               # wide_string
        return "text"
    if t == 0x1D:                            # loc_hash
        return "hash"
    if t in (0x1A, 0x25, 0x0C):              # guid / hash16 / color128
        return "hex"
    if t in (0x11, 0x12):
        return "list"
    return "scalar"


def main() -> int:
    for fname, wanted in INTEREST.items():
        path = MOD / fname
        doc, info = cndf_codec.decode(path.read_bytes(), source=str(path))
        objs = info["objects"]
        exports = info["exports"]
        classes = info["classes"]
        by_class = defaultdict(list)
        for o in objs:
            by_class[o["class"]].append(o)

        print(f"\n{'=' * 78}\n### {fname}\n{'=' * 78}")

        for cls in wanted:
            pool = by_class.get(cls, [])
            if not pool:
                continue
            named = sum(1 for o in pool if o["id"] in exports)
            # 统计每个对象能从哪一步拿到标签
            verdict = Counter()
            samples = []
            field_presence = Counter()
            for o in pool:
                for p in o["properties"]:
                    field_presence[p["property_name"]] += 1
                if o["id"] in exports:
                    verdict["1 export名"] += 1
                    got = exports[o["id"]]
                else:
                    cats = {p["property_name"]: classify_field(p["value"])
                            for p in o["properties"]}
                    text = [n for n, c in cats.items() if c == "text"]
                    path_ = [n for n, c in cats.items() if c == "path"]
                    hash_ = [n for n, c in cats.items()
                             if c == "hash" and any(k in n.lower()
                                                    for k in NAME_FIELDS)]
                    ref = [n for n, c in cats.items() if c == "ref"]
                    if text:
                        verdict["2 文本字段"] += 1
                        got = f"{text[0]}={o['properties'][0]!r}"
                    elif hash_:
                        verdict["3 Name哈希(hex)"] += 1
                        hv = next(p["value"]["value_hex"]
                                  for p in o["properties"]
                                  if p["property_name"] == hash_[0])
                        got = f"{hash_[0]}={hv[:8]}"
                    elif ref:
                        verdict["4 可递归借标签"] += 1
                        r = next(p for p in o["properties"]
                                 if p["property_name"] == ref[0])
                        got = (f"{ref[0]} -> "
                               f"{classes[r['value']['class_id']]}")
                    else:
                        visible_path = [n for n, c in cats.items()
                                        if c == "path"]
                        verdict["5 只能显示 id"] += 1
                        got = f"#{o['id']}"
                if len(samples) < 4:
                    samples.append((o["id"], got))

            print(f"\n  {cls}  ({len(pool)} 个对象，{named} 有 export 名)")
            print(f"    标签来源分布: {dict(verdict)}")
            print(f"    字段: {', '.join(sorted(field_presence)[:12])}")
            print("    样本:")
            for oid, got in samples:
                print(f"      #{oid:<7} {got}")

            # 如果有 Name 型哈希，展示前 4 个 hex —— 这是下拉框里会显示的东西
            for o in pool[:4]:
                for p in o["properties"]:
                    v = p["value"]
                    if v.get("type_id") == 0x1D:
                        print(f"      loc_hash {p['property_name']}"
                              f" = {v['value_hex']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
