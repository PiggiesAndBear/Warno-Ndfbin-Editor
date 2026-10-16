"""通用 ndfbin 字段地图 —— 由脚本生成，数据来自真实文件。

这个文件回答一个具体问题：**每个 ndfbin 里有哪些数值字段可以改。**
因为字段清单是从文件自带的 PROP 表自动发现的，所以换 mod、换游戏版本
都不用重新生成——重跑一次脚本即可。

生成方式::

    python tools/gen_field_map.py            # 扫 mod 目录
    python tools/gen_field_map.py <目录>     # 扫指定目录
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _src                                                 # noqa: E402
from ndfbin_decks.ndfedit import NdfDocument, humanize     # noqa: E402

#: Scanned in order; the sample mod first so a mod's own overrides win, then
#: the game's copy for everything it does not touch.  Both discovered — the
#: literal ``D:\Steam\...`` that used to be here is why this file could only
#: run on one machine.
DEFAULT_ROOTS = [p for p in (_src.gfx(), _src.vanilla_gfx()) if p]


def scan(root: Path):
    out = []
    files = sorted(root.glob("*.ndfbin"))
    for f in files:
        if f.name.endswith(".bak"):
            continue
        try:
            nd = NdfDocument.load(f)
        except Exception as exc:
            out.append((f.name, None, str(exc)))
            continue
        out.append((f.name, nd, None))
        print(f"  {f.name:36s} {len(nd.decoded['objects']):6d} 对象  "
              f"{len(nd.classes):4d} 类", file=sys.stderr)
    return out


def main() -> int:
    roots = [Path(a) for a in sys.argv[1:]] or [
        r for r in DEFAULT_ROOTS if r.exists()]
    if not roots:
        print("没有可扫描的目录")
        return 2

    print(f"扫描 {len(roots)} 个目录\n")
    all_data = []
    for root in roots:
        print(f"[{root}]", file=sys.stderr)
        all_data.append((root, scan(root)))

    lines = ["# 通用 ndfbin 字段地图", "",
             "> 本文件由 `tools/gen_field_map.py` 生成，请勿手工编辑。",
             "> 字段清单来自每个文件自带的 PROP 表，因此永远与文件一致。", ""]

    for root, data in all_data:
        lines += [f"## {root}", ""]
        for name, nd, err in data:
            if nd is None:
                lines.append(f"### {name}\n\n解析失败：{err}\n")
                continue
            lines.append(f"### {name} — {len(nd.decoded['objects'])} 对象 / "
                         f"{len(nd.classes)} 类")
            lines.append("")
            editable = [c for c in nd.classes if c.field_count > 0]
            lines.append(f"{len(editable)} / {len(nd.classes)} 个类含可改数值字段。")
            lines.append("")
            for c in editable:
                lines.append(f"#### `{c.name}` ×{c.count} — "
                             f"{c.field_count} 个字段")
                lines.append("")
                lines.append("| 字段 | 类型 | 覆盖率 |")
                lines.append("|---|---|---|")
                for fn, kind, cov in c.fields:
                    lines.append(f"| {humanize(fn)} `{fn}` | {kind} | "
                                 f"{cov * 100:.1f}% |")
                lines.append("")

    out = Path(__file__).resolve().parent.parent / "doc" / "通用字段地图.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已生成 {out}（{len(lines)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())