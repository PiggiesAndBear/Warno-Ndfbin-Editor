"""Read the *plaintext* NDF that the game ships alongside the compiled files.

``Mods/ModData/base.zip`` holds the pre-compile sources of ``Deck.ndfbin``:
``StrategicDecks.ndf`` (the decks) and ``StrategicCombatGroups.ndf`` (their
companies and platoons).  That is the only place where a pack or a platoon
still has a **name** — the compiled ``ndfbin`` keeps just object ids, so
"which unit is slot 7 supposed to be" can only be answered here.

What it is for: auditing a deck's ``(PackIndex, Number)`` pairs against the
packs those indices actually land on.  A ``PackIndex`` is a bare integer, so a
deck can be structurally perfect — windows tiling the pool exactly, no
overlap — and still field the wrong vehicle, because the integer points one
block too far.  ``audit`` prints each platoon next to the packs its windows
resolve to; that is what such a mistake looks like from the outside.

Usage::

    probe_deck_source.py list                        # every deck export name
    probe_deck_source.py deck   SOV_1GTA_595Reco     # pack list + platoons
    probe_deck_source.py audit  SOV_1GTA_595Reco     # platoon vs packs
    probe_deck_source.py diff   SOV_1GTA_595Reco SOV_20GMRD_68Reco
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ndfbin_decks.io import modlocator          # noqa: E402

#: Where the plaintext sources live, relative to the game install.
SOURCES = {
    "decks": "GameData/Generated/Gameplay/Decks/StrategicDecks.ndf",
    "groups": "GameData/Generated/Gameplay/Decks/StrategicCombatGroups.ndf",
}

#: ``export X is Y`` — and plain ``X is Y``: the combat-group file writes its
#: definitions without the ``export`` keyword (they are still top level).
_EXPORT = re.compile(r"^(?:export\s+)?(\S+)\s+is\s+(\w+)\s*$", re.M)

#: Smart groups are written **inline** inside their combat group, so they have
#: no header line of their own and have to be cut out by hand.
_SMART_GROUP = re.compile(r"TDeckSmartGroupDescriptor\s*\(")


# -- tiny NDF reading helpers ---------------------------------------------

def _balance(text: str, open_at: int) -> str:
    """The body of the bracket group starting at *open_at* (``(`` or ``[``).

    Only the same bracket kind is counted: NDF nests ``( ... )`` inside
    ``[ ... ]`` but never mixes the two into a crossing pair, so tracking one
    kind is both simpler and immune to the other kind appearing in a string.
    """
    pairs = {"(": ")", "[": "]"}
    opener = text[open_at]
    closer = pairs.get(opener)
    if closer is None:
        return ""
    depth = 0
    for j in range(open_at, len(text)):
        c = text[j]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return text[open_at + 1:j]
    return ""


def _blocks(text: str) -> dict[str, str]:
    """``{export name: body}`` for every top-level definition in *text*.

    The sources are machine-written and consistently indented, so a depth
    counter over parentheses is enough; no full NDF grammar is needed.
    """
    out: dict[str, str] = {}
    for m in _EXPORT.finditer(text):
        i = text.find("(", m.end())
        if i >= 0:
            out[m.group(1)] = _balance(text, i)
    return out


def _body_of(body: str, field_name: str) -> str:
    """The ``[ ... ]`` of one list field, or ``""``."""
    m = re.search(rf"\b{field_name}\s*=\s*\[", body)
    return _balance(body, m.end() - 1) if m else ""


def _refs(body: str, field_name: str) -> list[str]:
    """``~/Descriptor_X`` entries of one list field, in order."""
    return re.findall(r"~/(\S+?)\s*,", _body_of(body, field_name))


def _scalar(body: str, field_name: str) -> str | None:
    m = re.search(rf"^\s*{field_name}\s*=\s*([^\n]+)$", body, re.M)
    return m.group(1).strip().rstrip(",") if m else None


def _pairs(body: str) -> list[tuple[int, int]]:
    return [(int(k), int(v)) for k, v in re.findall(
        r"\(\s*(\d+)\s*,\s*(\d+)\s*\)", _body_of(body, "PackIndexUnitNumberList"))]


# -- the parsed model ------------------------------------------------------

@dataclass
class PlatoonSource:
    """One ``TDeckSmartGroupDescriptor`` as it appears in the source."""

    company: str
    #: obfuscated display-name key, e.g. ``"AEHXPSCGSH"``; the compiled file
    #: stores its hash instead, which is what the editor's UI shows when no
    #: dictionary resolves it
    name: str
    is_hq: bool
    pairs: list[tuple[int, int]] = field(default_factory=list)

    def slots(self) -> list[int]:
        out = []
        for i, n in self.pairs:
            out.extend(range(i, i + n))
        return out

    def label(self) -> str:
        hq = " (HQ)" if self.is_hq else ""
        return f"{self.name}{hq}  " + " ".join(f"({i},{n})" for i, n in self.pairs)


class Source:
    """The plaintext deck sources, parsed once."""

    def __init__(self, root: Path | None = None):
        self.root = root or game_root()
        z = self.root / "Mods" / "ModData" / "base.zip"
        with zipfile.ZipFile(z) as zf:
            self.raw = {k: zf.read(v).decode("utf-8", "replace")
                        for k, v in SOURCES.items()}
        self.blocks = {k: _blocks(t) for k, t in self.raw.items()}

    # -- lookups -----------------------------------------------------------
    def deck(self, needle: str) -> str:
        return self._pick("decks", needle,
                          ["Descriptor_Deck_pion_", "Descriptor_Deck_"])

    def company(self, needle: str) -> str:
        return self._pick("groups", needle, ["Descriptor_CombatGroup_"])

    def _pick(self, kind: str, needle: str, prefixes: list[str]) -> str:
        """Resolve a short name (``SOV_1GTA_595Reco``) to a full export name.

        The prefix is not guessable from the deck's identifier — the export is
        ``Descriptor_Deck_pion_SOV_...`` while ``DeckIdentifier`` says just
        ``pion_SOV_...`` — so try the known spellings and fall back to a
        substring search, refusing anything ambiguous.
        """
        if needle.startswith("Descriptor_"):
            if needle in self.blocks[kind]:
                return needle
        else:
            for p in prefixes:
                if p + needle in self.blocks[kind]:
                    return p + needle
        hits = [k for k in self.blocks[kind] if needle in k]
        if len(hits) == 1:
            return hits[0]
        raise SystemExit(f"{kind}: {needle!r} 命中 {len(hits)} 条；"
                         f"写全一点，候选：{hits[:6]}")

    # -- structure ---------------------------------------------------------
    def pack_list(self, deck: str) -> list[str]:
        """The deck's slot pool, index-ordered, as pack export names."""
        return _refs(self.blocks["decks"][self.deck(deck)], "DeckPackList")

    def companies(self, deck: str) -> list[str]:
        return _refs(self.blocks["decks"][self.deck(deck)], "DeckCombatGroupList")

    def smart_groups(self, company: str) -> list[PlatoonSource]:
        body = self.blocks["groups"].get(company, "")
        out = []
        for m in _SMART_GROUP.finditer(body):
            inner = _balance(body, m.end() - 1)
            out.append(PlatoonSource(
                company=company,
                name=(_scalar(inner, "Name") or '""').strip('"'),
                is_hq=bool(re.search(r"^\s*IsHQ\s*=", inner, re.M)),
                pairs=_pairs(inner),
            ))
        return out

    def platoons(self, deck: str) -> list[PlatoonSource]:
        """Every platoon of a deck, in company order."""
        return [p for cg in self.companies(deck) for p in self.smart_groups(cg)]


def game_root() -> Path:
    """The install whose ``Mods/`` tree holds the NDF sources.

    ``Saved Games`` normally sits on a different drive from the install, so
    this delegates to the same discovery the rest of the tooling uses rather
    than walking up from the deployment folder.
    """
    root = modlocator.warno_root()
    if not (root / "Mods" / "ModData" / "base.zip").is_file():
        raise SystemExit(f"{root} 下没有 Mods/ModData/base.zip")
    return root


def _pack_name(export: str) -> str:
    """``Descriptor_StrategicPack_UAZ_469_Reco_SOV_Scout_SOV_1`` → its unit part.

    The name is ``<unit>_<pawn>_<n>``; both halves describe the slot, which is
    what makes a wrong ``PackIndex`` visible at all.
    """
    return export.removeprefix("Descriptor_StrategicPack_")


def _pack_kind(pack_export: str) -> str:
    """Drop the trailing ``_1`` / ``_2`` variant digit from a pack name."""
    return re.sub(r"_\d+$", "", _pack_name(pack_export))


# -- commands --------------------------------------------------------------

def _all_decks(src: Source) -> list[str]:
    return sorted(k for k in src.blocks["decks"] if k.startswith("Descriptor_Deck_"))


def cmd_census(src: Source, only: str | None) -> None:
    """Every pack a given platoon *name* ever resolves to, across all decks.

    Platoon names are obfuscated keys (``AEHXPSCGSH``) but they are shared: the
    same key is reused by every deck that fields that kind of platoon.  So if
    one deck points such a platoon at a different vehicle, grouping by name is
    what makes it stand out — a single deck cannot tell right from wrong.
    """
    seen: dict[str, dict[str, list[str]]] = {}
    for deck in _all_decks(src):
        pool = src.pack_list(deck)
        for p in src.platoons(deck):
            kinds = {_pack_kind(pool[k]) for k in p.slots() if 0 <= k < len(pool)}
            for kind in kinds:
                seen.setdefault(p.name, {}).setdefault(kind, []).append(
                    deck.removeprefix("Descriptor_Deck_"))
    rows = [(n, k) for n, k in seen.items() if not only or only in n]
    odd = [(n, k) for n, k in rows if len(k) > 1]
    print(f"{len(seen)} 个排名；其中 {len(odd)} 个在同一名字下出现多种装备 ← 映射可疑")
    print()
    for name, kinds in sorted(odd, key=lambda t: -len(t[1])):
        total = sum(len(v) for v in kinds.values())
        print(f"### {name}   出现 {total} 次，{len(kinds)} 种装备")
        for kind, decks in sorted(kinds.items(), key=lambda t: -len(t[1])):
            sample = ", ".join(decks[:4])
            more = f" …(+{len(decks) - 4})" if len(decks) > 4 else ""
            print(f"    {len(decks):4d}x  {kind:52s} {sample}{more}")
        print()


def cmd_anomaly(src: Source, deck: str) -> None:
    """Which of a deck's platoons disagree with the same name elsewhere."""
    deckname = src.deck(deck)
    pool = src.pack_list(deckname)
    census: dict[str, dict[str, int]] = {}
    for other in _all_decks(src):
        if other == deckname:
            continue
        opool = src.pack_list(other)
        for p in src.platoons(other):
            kinds = {_pack_kind(opool[k]) for k in p.slots() if 0 <= k < len(opool)}
            for kind in kinds:
                census.setdefault(p.name, {})[kind] = \
                    census.setdefault(p.name, {}).get(kind, 0) + 1
    print(f"### {deckname}")
    for p in src.platoons(deckname):
        kinds = {_pack_kind(pool[k]) for k in p.slots() if 0 <= k < len(pool)}
        mine = sorted(kinds)
        rest = census.get(p.name, {})
        flag = "  ← 与其他编制不一致" if rest and set(mine) - set(rest) else ""
        peer = ", ".join(f"{k}×{v}" for k, v in
                         sorted(rest.items(), key=lambda t: -t[1])[:3]) or "（无同名排）"
        print(f"  {p.name:12s}{' (HQ)' if p.is_hq else '     '} "
              f"{' '.join(f'({i},{n})' for i, n in p.pairs):20s} "
              f"本营={mine}  别处多数={peer}{flag}")

def cmd_list(src: Source) -> None:
    decks = sorted(k for k in src.blocks["decks"] if k.startswith("Descriptor_Deck_"))
    print(f"明文源里 {len(decks)} 个 deck")
    for d in decks:
        print("   ", d.removeprefix("Descriptor_Deck_"))


def cmd_deck(src: Source, needle: str) -> None:
    deck = src.deck(needle)
    body = src.blocks["decks"][deck]
    pool = src.pack_list(needle)
    print(f"### {deck}")
    print("    DeckIdentifier =", _scalar(body, "DeckIdentifier"))
    print("    DeckDivision   =", _scalar(body, "DeckDivision"))
    print(f"    DeckPackList   = {len(pool)} 槽")
    hq = "  (HQ)" if re.search(r"^\s*IsHQ\s*=", body, re.M) else ""
    print(f"    连 = {len(src.companies(needle))}{hq}")
    print()
    for p in src.platoons(needle):
        print(f"  {p.label()}")
        for k in p.slots():
            if 0 <= k < len(pool):
                print(f"        {k:4d}  {_pack_name(pool[k])}")
    print()


def cmd_audit(src: Source, needle: str) -> None:
    """Per platoon: the packs its windows actually land on, grouped."""
    pool = src.pack_list(needle)
    print(f"### audit {src.deck(needle)}   ({len(pool)} 槽)")
    for p in src.platoons(needle):
        got = [_pack_name(pool[k]) for k in p.slots() if 0 <= k < len(pool)]
        if not got:
            print(f"  {p.label()}   ← 空")
            continue
        summary = ", ".join(f"{u}×{got.count(u)}" for u in dict.fromkeys(got))
        print(f"  {p.name:12s}{' (HQ)' if p.is_hq else '     '} "
              f"{' '.join(f'({i},{n})' for i, n in p.pairs):22s} {summary}")
    print()
    print("槽位视图（槽号 → pack 名）：")
    for i, name in enumerate(pool):
        print(f"   {i:4d}  {_pack_name(name)}")


def cmd_diff(src: Source, a: str, b: str) -> None:
    pa = [_pack_name(x) for x in src.pack_list(a)]
    pb = [_pack_name(x) for x in src.pack_list(b)]
    print(f"### 槽位对齐：{a}  vs  {b}")
    print(f"{'槽':>4}     {'A':46s}  B")
    for i in range(max(len(pa), len(pb))):
        x = pa[i] if i < len(pa) else ""
        y = pb[i] if i < len(pb) else ""
        print(f"{i:4d}  {' ' if x == y else '≠'}{x:46s}  {y}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="游戏安装根目录（默认自动探测）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    d = sub.add_parser("deck");     d.add_argument("deck")
    a = sub.add_parser("audit");    a.add_argument("deck")
    f = sub.add_parser("diff");     f.add_argument("a"); f.add_argument("b")
    c = sub.add_parser("census");   c.add_argument("--name", help="只看含此串的排名")
    n = sub.add_parser("anomaly");  n.add_argument("deck")
    args = ap.parse_args()

    src = Source(Path(args.root) if args.root else None)
    if args.cmd == "list":
        cmd_list(src)
    elif args.cmd == "deck":
        cmd_deck(src, args.deck)
    elif args.cmd == "audit":
        cmd_audit(src, args.deck)
    elif args.cmd == "diff":
        cmd_diff(src, args.a, args.b)
    elif args.cmd == "census":
        cmd_census(src, args.name)
    elif args.cmd == "anomaly":
        cmd_anomaly(src, args.deck)


if __name__ == "__main__":
    main()
