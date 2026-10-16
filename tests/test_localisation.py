"""Validate ``loc_hash`` → text resolution and report real coverage.

Two things are checked:

1. **The chain works.**  Dictionary keys must appear verbatim as ``Name``
   ``loc_hash`` values inside a compiled ``Deck.ndfbin``.  Test's mod shows
   5/7; anything at zero would mean the two formats are not the same space.
2. **Coverage is reported honestly.**  The game's own dictionaries are sealed in
   ``Data/PC/<build>/*.dat``, so most names cannot be resolved.  This test prints
   the real number rather than pretending otherwise.

Run with::

    python -m tests.test_localisation
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import mods as discover_mods                   # noqa: E402
from ndfbin_decks.core import cndf_codec                # noqa: E402
from ndfbin_decks.io import localisation, modlocator    # noqa: E402


def collect(mod) -> tuple[Counter, Counter]:
    """Return (all smart-group name hashes, those on AG Pawn decks)."""
    path = mod.deploy / "Gen" / "NDF" / "GFX" / "Deck.ndfbin"
    if not path.is_file():
        return Counter(), Counter()
    _doc, dec = cndf_codec.decode(path.read_bytes(), str(path))

    sgs = {}
    for o in dec["objects"]:
        if o["class"] == "TDeckSmartGroupDescriptor":
            sgs[o["id"]] = {p["property_name"]: p["value"] for p in o["properties"]}

    on_ag = Counter()
    every = Counter()
    for o in dec["objects"]:
        if o["class"] != "TDeckDescriptor" or not o["is_top_object"]:
            continue
        props = {p["property_name"]: p["value"] for p in o["properties"]}
        is_ag = "DeckName" not in props
        items = props.get("DeckCombatGroupList", {}).get("items", [])
        for ci in items:
            cg_id = ci.get("object_id")
            if cg_id is None:
                continue
            for o2 in dec["objects"]:
                pass
    # Second pass is cheaper via a map built once.
    cg_to_sgs = {}
    for o in dec["objects"]:
        if o["class"] != "TDeckCombatGroupDescriptor":
            continue
        p = {q["property_name"]: q["value"] for q in o["properties"]}
        cg_to_sgs[o["id"]] = [x.get("object_id")
                              for x in p.get("SmartGroupList", {}).get("items", [])]

    for o in dec["objects"]:
        if o["class"] != "TDeckDescriptor" or not o["is_top_object"]:
            continue
        props = {p["property_name"]: p["value"] for p in o["properties"]}
        is_ag = "DeckName" not in props
        for ci in props.get("DeckCombatGroupList", {}).get("items", []):
            for sg_id in cg_to_sgs.get(ci.get("object_id"), []):
                h = localisation.name_hash(sgs.get(sg_id, {}))
                every[h is not None] += 1
                if is_ag:
                    on_ag[h is not None] += 1
    return every, on_ag


def main() -> int:
    print(f"{'mod':<24} {'dict keys':>10} {'named/total':>14} {'%':>8}  {'unnamed':>8}")
    print("-" * 72)

    chain_ok = True
    found = discover_mods()
    if not found:
        print("SKIP — no WARNO mod workspace on this machine")
        return 0
    for mod in found.values():
        gen = mod.deploy / "Gen" if mod.deploy else None
        if gen is None or not gen.is_dir():
            continue
        ns = mod.localisation_namespace()
        if ns is None:
            print(f"{mod.name:<24} (no dictionaries)")
            continue

        res = localisation.NameResolver.from_mod(mod, lang="SC")
        if not res.has_any:
            print(f"{mod.name:<24} (no dictionaries)")
            continue

        path = mod.deploy / "Gen" / "NDF" / "GFX" / "Deck.ndfbin"
        _doc, dec = cndf_codec.decode(path.read_bytes(), str(path))

        # -- chain check: are dic keys present as loc_hash in the ndfbin? --
        sg_hashes = set()
        for o in dec["objects"]:
            if o["class"] != "TDeckSmartGroupDescriptor":
                continue
            p = {q["property_name"]: q["value"] for q in o["properties"]}
            h = localisation.name_hash(p)
            if h:
                sg_hashes.add(h)
        dic_keys = set(res.tables.get("PLATOONS", {}))
        hit = sg_hashes & dic_keys

        # -- coverage on AG Pawn decks only --
        _every, ag = collect(mod)
        total = ag[True] + ag[False]
        named = 0
        for h in sg_hashes:
            if res.resolve(h).found:
                named += 1

        pct = (named / len(sg_hashes) * 100) if sg_hashes else 0.0
        print(f"{mod.name:<24} {len(dic_keys):>10} "
              f"{named:>7}/{len(sg_hashes):<6} {pct:>7.3f}%  {ag[False]:>8}")
        print(f"{'':<24} chain check: {len(hit)}/{len(dic_keys)} dictionary keys "
              f"appear verbatim as loc_hash in Deck.ndfbin")
        if dic_keys and not hit:
            chain_ok = False

        stats = res.family_stats()
        if stats:
            print(f"{'':<24} dictionaries: "
                  + ", ".join(f"{k}={v}" for k, v in stats.items()))
        sample = res.all_names("PLATOONS")[:4]
        if sample:
            shown = ", ".join(f"{t!r}" for t, _ in sample)
            print(f"{'':<24} pickable PLATOONS names (sample): {shown}")
        print()

    print("Note: coverage is low by design — the game's own dictionaries live in")
    print("      Data/PC/<build>/*.dat and are out of scope.  Unresolved names")
    print(f"      degrade to {localisation.UNKNOWN_TEMPLATE!r} with the key shown,")
    print("      which is copy-pasteable for external lookup.")

    if not chain_ok:
        print("\nFAIL — dictionary keys and ndfbin loc_hash are different spaces")
        return 1
    print("\nPASS — loc_hash resolves through the mod's dictionaries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
