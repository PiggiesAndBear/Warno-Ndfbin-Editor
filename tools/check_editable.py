"""检查 ``_editable`` 收紧 "id" 匹配后，到底多了哪些字段。

改判据一定要看它放行了什么——收紧是为了放过 ResistanceSides，
但要确认没有顺手放过 DescriptorId 这类真标识符。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                              # noqa: E402

from ndfbin_decks.core import cndf_codec                          # noqa: E402
from ndfbin_decks.ndfedit.schema import _editable                 # noqa: E402

MOD = _src.gfx()
OLD_DENY = ("descriptorid", "guid", "hash", "token", "texture",
            "name", "id", "path", "reference", "mesh")


def old_editable(n: str) -> bool:
    if n in ("Name",):
        return False
    low = n.lower()
    return not any(b in low for b in OLD_DENY)


def main() -> int:
    for f in ("Weapon.ndfbin", "Unit.ndfbin", "Deck.ndfbin", "Division.ndfbin"):
        p = MOD / f
        if not p.exists():
            continue
        _doc, info = cndf_codec.decode(p.read_bytes(), source=str(p))
        names = sorted({x["name"] for x in info["properties"]})
        newly = [n for n in names if _editable(n) and not old_editable(n)]
        lost = [n for n in names if old_editable(n) and not _editable(n)]
        print(f"\n### {f}  属性 {len(names)} 种")
        print(f"  新放行 {len(newly)} 个: {newly}")
        if lost:
            print(f"  ⚠ 新挡掉 {len(lost)} 个: {lost}")
        # 反向检查：还不该放行的标识符有没有漏进来
        bad = [n for n in names if _editable(n)
               and n.lower().endswith("id") and n not in ("Valid",)]
        print(f"  仍暴露的 *id 结尾字段: {bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
