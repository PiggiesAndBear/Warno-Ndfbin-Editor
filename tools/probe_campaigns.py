# -*- coding: utf-8 -*-
"""List the Army General campaigns on this machine and what each one pulls in.

    python tools/probe_campaigns.py            # 本机全部战役
    python tools/probe_campaigns.py --refs     # 连每个战役引用的资源一起列出

Why this exists
---------------
"哪个战役里双方登场哪些部队" is a question the binaries answer only partly, and
the split matters:

* a campaign definition (``Scenarios/CampagneStrat_*_Definition.dat``) is a
  resource manifest — it names a ``MapConfiguration``, a ``GDScript``, a
  ``Loader``, an ``Info`` and a ``Trophy``, and **no unit list at all**;
* the units themselves live in ``GFX/Deck.ndfbin`` (battalions),
  ``GFX/Division.ndfbin`` (coalition tag) and ``UI/BattleOrder.ndfbin``
  (the chain of command) — and that set is **global**, shared by every
  campaign, not sliced per scenario.

So: the campaign files tell you which scenarios exist; the deck binaries tell you
what both sides can field; nothing readable joins the two.

The campaign ``.dat`` files are ``edat`` containers.  Their table of contents is
plain text and this reads it, but the payload may be compressed — which is why a
campaign name can be absent from the big ``Data/PC/<build>/*.dat`` packs even
though the campaign plainly exists.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ndfbin_decks.io import modlocator                     # noqa: E402

#: The five resource kinds a campaign manifest points at.
RESOURCE_KINDS = ("MapConfiguration", "GDScript", "Loader", "Info", "Trophy")

_CAMPAIGN = re.compile(r"^CampagneStrat_(.+?)_Definition\.dat$")
_REF = re.compile(rb"Scenarios/(%s)/([A-Za-z0-9_./-]+)"
                  % b"|".join(k.encode() for k in RESOURCE_KINDS))


def campaign_dir() -> Path | None:
    """The newest unpacked build that carries scenarios."""
    try:
        root = modlocator.warno_root()
    except Exception:
        return None
    candidates = sorted((root / "Data" / "PC").glob("*/Scenarios"),
                        key=lambda p: p.parent.name, reverse=True)
    for d in candidates:
        if d.is_dir() and any(d.glob("CampagneStrat_*_Definition.dat")):
            return d
    return candidates[0] if candidates else None


def campaigns(where: Path) -> list[tuple[str, Path]]:
    out = []
    for p in sorted(where.glob("CampagneStrat_*_Definition.dat")):
        m = _CAMPAIGN.match(p.name)
        if m:
            out.append((m.group(1), p))
    return out


def references(path: Path) -> dict[str, list[str]]:
    """``{kind: [names]}`` read out of the container's plaintext table."""
    data = path.read_bytes()
    found: dict[str, list[str]] = {}
    for m in _REF.finditer(data):
        kind = m.group(1).decode()
        name = m.group(2).decode().rstrip(".")
        found.setdefault(kind, []).append(name)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refs", action="store_true",
                    help="列出每个战役引用的资源")
    args = ap.parse_args(argv)

    where = campaign_dir()
    if where is None:
        print("找不到 Data/PC/*/Scenarios —— 游戏装在哪？")
        return 1
    print(f"战役目录: {where}")
    items = campaigns(where)
    print(f"AG 战役（CampagneStrat）: {len(items)} 个")
    print()
    for name, path in items:
        print(f"  {name:22s} {path.stat().st_size:>8,} B")
        if args.refs:
            refs = references(path)
            for kind in RESOURCE_KINDS:
                names = sorted(set(refs.get(kind, [])))
                if names:
                    print(f"      {kind:18s} {names}")
    print()
    print("说明：这些文件只列资源，不含部队名单；双方编制见")
    print("      GFX/Deck.ndfbin + GFX/Division.ndfbin + UI/BattleOrder.ndfbin")
    print("      （用 tools/probe_org.py join 统计）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
