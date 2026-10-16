# -*- coding: utf-8 -*-
"""Recon the top-down organisation of a WARNO mod, straight from the binaries.

    python tools/probe_org.py levels      # 每个编制层级有多少节点
    python tools/probe_org.py tree        # 完整编制树（--front 只看一个战区）
    python tools/probe_org.py join        # deck → 编制节点 的命中率与阵营覆盖
    python tools/probe_org.py tags        # DivisionTags 分类 + 国家前缀可靠性

Why this exists
---------------
Three compiled files carry the answer, and none of them is the one you would
guess.  A ``TDeckDescriptor`` (the battalion you edit) has exactly one upward
link — ``DeckDivision``, a **string path**, not an object reference — so every
question above the battalion means opening another file:

* ``GFX/Deck.ndfbin``      → the battalions (``TDeckDescriptor``)
* ``GFX/Division.ndfbin``  → coalition / country (``TDeckDivisionDescriptor``)
* ``UI/BattleOrder.ndfbin`` → the whole command tree (``TBattleOrderSubordination``)

Two facts make this tractable, and both were measured rather than assumed:

* **The level of a node is a field, not a depth.**  ``BackgroundTexture`` →
  a constant whose ``Value`` is ``Texture_STRATEGIC_{front,corps,division,
  brigade,regiment}``.  All 643 nodes carry it.  Walking ``Superior`` and
  counting hops gives a different answer, because 25 corps hang off another
  corps and 5 divisions have no superior at all.
* **Coalition is not in the tree.**  The seven ``front`` nodes are named
  ``UK_NORTHAG`` / ``SOV_WGF`` / …, with no NATO/PACT token anywhere.  It comes
  from ``DivisionTags`` on the division descriptor instead, and matches the
  front's side 159 times out of 159 where both are available.

Run it against the checkout that has PySide6-free dependencies only — this
touches :mod:`ndfbin_decks.core.cndf_codec` and :mod:`ndfbin_decks.io`, nothing
else.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ndfbin_decks.core import cndf_codec                      # noqa: E402
from ndfbin_decks.io import modlocator, tradic                # noqa: E402

#: The seven command levels, and what they mean to a human.
LEVELS = {
    "front": "方面军 / 集团军群",
    "corps": "军 / 同级单位",
    "division": "师",
    "brigade": "旅",
    "regiment": "团",
}

#: ``front`` node → the side it belongs to.  Read off the country prefixes of
#: its children and cross-checked against every division's ``DivisionTags``;
#: see ``tags``.
FRONT_COALITION = {
    "UK_NORTHAG": "NATO", "US_CENTAG": "NATO", "FR_SOUTHAG": "NATO",
    "SOV_WGF": "PACT", "SOV_NGF": "PACT", "SOV_CGV": "PACT", "TCH_CSLA": "PACT",
}

_SUFFIX = re.compile(r"_(Subordination|Command)$")
_DIV_SUFFIX = re.compile(r"_(solo|coop|multi|challenge)$")
_PREFIX = "Descriptor_Deck_Division_"


def decode(path: Path) -> dict:
    _doc, info = cndf_codec.decode(Path(path).read_bytes(), str(path))
    return info


def bag(info: dict, obj: dict) -> dict:
    return {p["property_name"]: p["value"] for p in obj["properties"]}


def leaf(info: dict, obj: dict) -> str:
    return info["exports"].get(obj["id"], "").rsplit("/", 1)[-1]


def pick_mod(name: str | None):
    """The mod to recon, defaulting to the one that ships all three files."""
    if name:
        return modlocator.resolve(name)
    best = None
    for mod in modlocator.discover():
        try:
            if not mod.deck_bin.is_file():
                continue
            score = int(mod.division_bin.is_file()) + int(mod.battleorder_bin.is_file())
        except modlocator.ModNotFound:
            continue
        if best is None or score > best[0]:
            best = (score, mod)
    if best is None:
        raise SystemExit("no mod with a Deck.ndfbin was found")
    return best[1]


def read_names(mod) -> dict[bytes, str]:
    """Every ``loc_hash`` the mod's own dictionaries can resolve."""
    out: dict[bytes, str] = {}
    root = mod.localisation_gen_dir()
    if root is None:
        return out
    for path in sorted(root.rglob("*.dic")):
        if tradic.language_of(path) != "SC":
            continue
        try:
            for key, text in tradic.read(path).items():
                out.setdefault(key, text)
        except Exception:
            continue
    return out


class Tree:
    """``BattleOrder.ndfbin`` as a walkable tree with levels attached."""

    def __init__(self, mod):
        self.mod = mod
        self.nodes: dict[int, dict] = {}
        self.kids: dict[int, list[int]] = defaultdict(list)
        if not mod.battleorder_bin.is_file():
            return
        info = decode(mod.battleorder_bin)
        by_id = {o["id"]: o for o in info["objects"]}
        names = read_names(mod)
        for obj in info["objects"]:
            if obj["class"] != "TBattleOrderSubordination":
                continue
            props = bag(info, obj)
            sup = props.get("Superior")
            token = props.get("NameToken", {})
            key = bytes.fromhex(token["value_hex"]) if token.get("value_hex") else None
            self.nodes[obj["id"]] = {
                "name": leaf(info, obj),
                "level": self._level(by_id, info, props),
                "sup": sup.get("object_id") if sup else None,
                "display": names.get(key) if key else None,
            }
        for i, node in self.nodes.items():
            if node["sup"] is not None:
                self.kids[node["sup"]].append(i)

    @staticmethod
    def _level(by_id, info, props) -> str | None:
        bg = props.get("BackgroundTexture")
        if not bg:
            return None
        target = by_id.get(bg.get("object_id"))
        if target is None:
            return None
        value = bag(info, target).get("Value", {}).get("value", "") or ""
        return re.sub(r"^Texture_STRATEGIC_", "", value) or None

    def __bool__(self) -> bool:
        return bool(self.nodes)

    @property
    def roots(self) -> list[int]:
        return sorted((i for i, n in self.nodes.items() if n["sup"] is None),
                      key=lambda i: self.nodes[i]["name"])

    def bare(self, i: int) -> str:
        return _SUFFIX.sub("", self.nodes[i]["name"])

    def chain(self, i: int) -> list[dict]:
        """``[{level, name}, …]`` from the top down."""
        out, seen = [], set()
        while i is not None and i not in seen:
            seen.add(i)
            node = self.nodes.get(i)
            if node is None:
                break
            out.append(node)
            i = node["sup"]
        return list(reversed(out))

    def front_of(self, i: int) -> str | None:
        """The side this node ultimately answers to, or ``None``.

        A node marks the top of a command chain in *two* ways, and only one of
        them is the ``front`` icon: ``TCH_CSLA`` (the Czechoslovak People's
        Army) is a root whose icon says ``brigade``.  Reading the icon alone
        would drop every Czechoslovak deck out of the tree, so a root whose
        name is a known command also counts.  That is the same trap as §2.2 —
        the field describes how the unit is *drawn*, not what it *is*.
        """
        for node in self.chain(i):
            name = _SUFFIX.sub("", node["name"])
            if node["level"] == "front" or node["sup"] is None:
                if name in FRONT_COALITION:
                    return FRONT_COALITION[name]
        return None

    def by_bare(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i, node in self.nodes.items():
            out.setdefault(self.bare(i), i)
        return out


def load_aliases() -> dict[str, str]:
    """``{deck-side division name: tree node}`` — the reconciled spellings.

    Two naming systems meet here and neither is derivable from the other: the
    deck says ``US_1Arm`` where the tree says ``US_1AD``.  Every entry was
    checked against the division descriptor's ``EmblemTexture`` or the tree's
    own parent prefixes, and the judgement ships with it.  Fuzzy matching was
    rejected on purpose — it silently pairs ``RFA_FlaKdo2`` with
    ``RFA_FlaKdo1``, and a wrong corps is invisible in the UI.
    """
    path = ROOT / "ndfbin_decks" / "data" / "division_aliases.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v["node"] for k, v in data.get("aliases", {}).items()
            if isinstance(v, dict) and v.get("node")}


def deck_divisions(mod) -> Counter:
    """``{division leaf: how many AG decks reference it}``."""
    info = decode(mod.deck_bin)
    out: Counter = Counter()
    for obj in info["objects"]:
        if obj["class"] != "TDeckDescriptor":
            continue
        props = bag(info, obj)
        if "DeckName" in props:            # PvP / skirmish, out of scope
            continue
        out[re.sub(r"^.*" + _PREFIX, "",
                   props.get("DeckDivision", {}).get("value") or "")] += 1
    return out


def descriptors(mod) -> dict[str, dict]:
    """``{division leaf: {tags, country, emblem, color}}`` from Division.ndfbin."""
    out: dict[str, dict] = {}
    if not mod.division_bin.is_file():
        return out
    info = decode(mod.division_bin)
    for obj in info["objects"]:
        if obj["class"] != "TDeckDivisionDescriptor":
            continue
        props = bag(info, obj)
        country = props.get("CountryId")
        out[re.sub(r"^.*" + _PREFIX, "", leaf(info, obj))] = {
            "tags": tuple(i.get("value")
                          for i in bag(info, obj).get("DivisionTags", {}).get("items", [])),
            "country": country.get("value") if isinstance(country, dict) else None,
            "emblem": (props.get("EmblemTexture") or {}).get("value"),
            "color": (props.get("StrategicLabelColor") or {}).get("value"),
        }
    return out


# ---------------------------------------------------------------- subcommands

def cmd_levels(mod, args) -> int:
    tree = Tree(mod)
    if not tree:
        print(f"{mod.name}: no BattleOrder.ndfbin — 编制树不可用")
        return 1
    print(f"{mod.name}: {len(tree.nodes)} 个节点，{len(tree.roots)} 个根")
    print()
    count = Counter(n["level"] for n in tree.nodes.values())
    for level in [*LEVELS, None]:
        if level in count:
            print(f"  {str(level):10s} {LEVELS.get(level, '<无标注>'):20s} {count[level]:4d}")
    print()
    print("  层级 ≠ 深度，实测父层级分布：")
    for level in LEVELS:
        parents = Counter(
            tree.nodes[n["sup"]]["level"] if n["sup"] is not None else "<根>"
            for n in tree.nodes.values() if n["level"] == level)
        print(f"    {level:10s} 的父: {dict(parents)}")
    print()
    for r in tree.roots:
        node = tree.nodes[r]
        print(f"  ● {node['name']:44s} [{LEVELS.get(node['level'], node['level'])}] "
              f"{node['display'] or ''}")
    return 0


def cmd_tree(mod, args) -> int:
    tree = Tree(mod)
    if not tree:
        print(f"{mod.name}: no BattleOrder.ndfbin — 编制树不可用")
        return 1

    def walk(i, depth=0):
        node = tree.nodes[i]
        pad = "    " * depth
        print(f"{pad}{node['name']:44s} [{node['level']}] {node['display'] or ''}")
        for kid in sorted(tree.kids.get(i, []), key=lambda k: tree.nodes[k]["name"]):
            walk(kid, depth + 1)

    show = tree.roots
    if args.front:
        want = {f.lower() for f in args.front}
        show = [r for r in tree.roots if tree.bare(r).lower() in want]
        if not show:
            print(f"没有匹配的 front；可选: {[tree.bare(r) for r in tree.roots]}")
            return 1
    for r in show:
        walk(r)
    return 0


def cmd_join(mod, args) -> int:
    tree = Tree(mod)
    decks = deck_divisions(mod)
    desc = descriptors(mod)
    total = sum(decks.values())
    print(f"{mod.name}: AG deck {total} 个，division 名 {len(decks)} 种")
    print()
    if desc:
        hit = [d for d in decks if d in desc]
        side = Counter()
        for d in hit:
            tags = desc[d]["tags"]
            side["NATO" if "NATO" in tags else ("PACT" if "PACT" in tags else "?")] += decks[d]
        print(f"  Division.ndfbin 命中 {len(hit)}/{len(decks)} 种 "
              f"({sum(decks[d] for d in hit)/total:.1%} 的 deck)")
        print(f"  阵营覆盖: {dict(side)}")
    else:
        print("  无 Division.ndfbin —— 阵营只能退回名字前缀")
    print()
    if not tree:
        print("  无 BattleOrder.ndfbin —— 战区/军/师三级不可用")
        return 0
    by_bare = tree.by_bare()
    alias = load_aliases()
    ok = front = under = selfc = agf = 0
    miss: Counter = Counter()
    for name, n in decks.items():
        bare = _DIV_SUFFIX.sub("", name)
        node = bare if bare in by_bare else alias.get(bare)
        if node is None or node not in by_bare:
            miss[name] += n
            continue
        ok += n
        chain = tree.chain(by_bare[node])
        levels = [x["level"] for x in chain]
        if levels and levels[-1] == "corps":
            selfc += n                      # the unit *is* a corps
        elif "corps" in levels:
            under += n                      # hangs off one
        elif tree.front_of(by_bare[node]):
            front += n                      # front-direct, no corps
        else:
            agf += n                        # standalone, no superior at all
    print(f"  别名表 {len(alias)} 条（ndfbin_decks/data/division_aliases.json）")
    print()
    print(f"  口径 A 定位到树上的节点      {ok}/{total} ({ok/total:.1%})")
    reach = under + selfc
    print(f"  口径 B 能追到某个军          {reach}/{total} ({reach/total:.1%})")
    print(f"          其中 军子树下 {under} · 本身是军 {selfc}")
    print(f"        （独立单位无上级 {agf} · 战区直属 {front} —— 不是缺失，"
          f"是独立编队本就不隶属任何军）")
    print(f"  无归属 {sum(miss.values())} 个 deck / {len(miss)} 种名字")
    if miss:
        print(f"    样例: {sorted(miss)[:12]}")
    return 0


def cmd_tags(mod, args) -> int:
    desc = descriptors(mod)
    if not desc:
        print(f"{mod.name}: no Division.ndfbin")
        return 1
    decks = deck_divisions(mod)
    print(f"{mod.name}: Division.ndfbin {len(desc)} 条描述符")
    buckets = Counter(m["tags"][0] if m["tags"] else "<empty>" for m in desc.values())
    print(f"  按首标签: {dict(buckets)}")
    print()
    for key in sorted(buckets):
        names = [n for n, m in desc.items() if (m["tags"][:1] or ("",)) == (key,)]
        used = [n for n in names if n in decks]
        print(f"  {key:10s} {len(names):4d} 条  被 AG deck 引用 {len(used):4d} 条 "
              f"/ {sum(decks[n] for n in used)} 个 deck")
        if key != "STRAT" and not used:
            print(f"             后缀样例: {sorted(names)[:4]}")
    print()
    print("  名字首段 vs EmblemTexture（只看被引用的）")
    known = {"SOV", "POL", "TCH", "RDA", "RFA", "FR", "UK", "NL", "BEL",
             "CAN", "ESP", "US", "DDR", "NATO"}
    suspect = []
    for name in sorted(decks):
        meta = desc.get(name)
        if meta is None:
            continue
        head = name.split("_")[0]
        m = re.match(r"Texture_Division_Emblem_([A-Za-z]{2,4})", meta["emblem"] or "")
        if not m:
            continue
        code = m.group(1).upper()
        if code not in known:
            continue
        normal = {"FRA": "FR", "DDR": "RDA"}.get(code, code)
        if normal != head:
            suspect.append((name, head, normal, meta["emblem"]))
    print(f"  ★ 不符 {len(suspect)} 条:")
    for name, head, normal, emblem in suspect:
        print(f"     {name:36s} 首段={head:6s} 实际={normal:6s} {emblem}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["levels", "tree", "join", "tags"])
    ap.add_argument("--mod", help="mod 名字（默认挑文件最全的那个）")
    ap.add_argument("--front", action="append",
                    help="tree 子命令：只展开这个战区（可重复）")
    args = ap.parse_args(argv)

    mod = pick_mod(args.mod)
    print(f"mod: {mod.name}  ({mod.deploy})")
    print()
    return {"levels": cmd_levels, "tree": cmd_tree,
            "join": cmd_join, "tags": cmd_tags}[args.command](mod, args)


if __name__ == "__main__":
    raise SystemExit(main())
