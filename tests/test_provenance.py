"""Validate three-way provenance classification across the sample mods.

Expected, from recon:

* vanilla AG decks = 2208
* Berlin: 186 new (58 authored + 128 promoted), 0 removed
* Northern Thunder: 257 new (129 authored + 128 promoted), 0 removed
* Test: 0 new, 0 removed — it only edits in place

Run with::

    python -m tests.test_provenance
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import (NORTHERN_THUNDER, mods as discover_mods,  # noqa: E402
                  opt_mod_dir)
from ndfbin_decks.core import cndf_codec              # noqa: E402
from ndfbin_decks.io import agfilter, modlocator, provenance   # noqa: E402

# Optional on purpose: it adds a third dataset, it does not define the
# test.  Requiring somebody else to be subscribed to a specific workshop mod
# would mean these checks never run outside this machine.
WORKSHOP_NORTHERN = opt_mod_dir(NORTHERN_THUNDER)

EXPECTED = {
    # name        (ag_total, new_total, authored, promoted, removed)
    #            ag_total == vanilla_ag + authored, because AG decks are
    #            exactly the vanilla AG ones plus mod-authored ones.  The
    #            128 promoted decks are PvP and fall outside the AG scope.
    "Test":                      (2208, 128,   0, 128, 0),
    "WarnoAGF_berlin_1989":      (2266, 186,  58, 128, 0),
    "Northern Thunder":          (2337, 257, 129, 128, 0),
}


def deck_names(mod_path: Path):
    p = mod_path / "Gen" / "NDF" / "GFX" / "Deck.ndfbin"
    if not p.is_file():
        return None
    _doc, dec = cndf_codec.decode(p.read_bytes(), str(p))
    out = []
    for o in dec["objects"]:
        if o["class"] == "TDeckDescriptor" and o["is_top_object"]:
            out.append(dec["exports"].get(o["id"], "").rsplit("/", 1)[-1])
    return out


def main() -> int:
    try:
        base = provenance.find_base_zip()
    except FileNotFoundError as exc:
        print(f"FATAL: {exc}")
        return 2
    print(f"vanilla baseline: {base}")
    vanilla = provenance.load_vanilla(base)
    print(f"  StrategicDecks.ndf (AG)   : {len(vanilla.strategic)}")
    print(f"  Decks.ndf (skirmish/PvP)  : {len(vanilla.skirmish)}")

    found = discover_mods()
    if not found and WORKSHOP_NORTHERN is None:
        print("SKIP — no WARNO mod workspace on this machine")
        return 0
    targets: list[tuple[str, Path]] = []
    for m in found.values():
        if m.deploy and (m.deploy / "Gen").is_dir():
            targets.append((m.name, m.deploy))
    if WORKSHOP_NORTHERN is not None:
        targets.append(("Northern Thunder", WORKSHOP_NORTHERN))

    failures = 0
    print()
    print(f"{'mod':<24} {'total':>6} {'AG':>6} {'PvP':>5} "
          f"{'vanilla':>8} {'promot':>7} {'new':>5} {'removed':>8}")
    print("-" * 84)

    for name, path in targets:
        names = deck_names(path)
        if names is None:
            print(f"{name:<24} (no Deck.ndfbin)")
            continue
        prov = provenance.classify(names, vanilla)
        c = prov.counts()

        # AG vs PvP split
        _doc, dec = cndf_codec.decode(
            (path / "Gen" / "NDF" / "GFX" / "Deck.ndfbin").read_bytes(), str(path))
        ag = pvp = 0
        for o in dec["objects"]:
            if o["class"] != "TDeckDescriptor" or not o["is_top_object"]:
                continue
            props = {p["property_name"]: p["value"] for p in o["properties"]}
            if agfilter.is_ag_pawn_deck(props):
                ag += 1
            else:
                pvp += 1

        print(f"{name:<24} {len(names):>6} {ag:>6} {pvp:>5} "
              f"{c[provenance.Origin.VANILLA]:>8} "
              f"{c[provenance.Origin.PROMOTED]:>7} "
              f"{c[provenance.Origin.MOD_NEW]:>5} "
              f"{len(prov.removed):>8}")

        key = "Northern Thunder" if "Northern" in name else name
        exp = EXPECTED.get(key)
        if exp is None:
            print(f"    (no expectation recorded for '{key}')")
            continue
        exp_ag, exp_new, exp_authored, exp_promoted, exp_removed = exp
        got = (ag,
               c[provenance.Origin.PROMOTED] + c[provenance.Origin.MOD_NEW],
               c[provenance.Origin.MOD_NEW],
               c[provenance.Origin.PROMOTED],
               len(prov.removed))
        want = (exp_ag, exp_new, exp_authored, exp_promoted, exp_removed)
        if got == want:
            print(f"    OK   AG={ag} new={got[1]} (authored {got[2]} + "
                  f"promoted {got[3]}) removed={got[4]}")
        else:
            failures += 1
            print(f"    FAIL got  {got}")
            print(f"         want {want}")

    print()
    if failures:
        print(f"FAIL — {failures} mod(s) did not match the expected split")
        return 1
    print("PASS — provenance classification matches the recon figures.")
    print()
    print("Note: 'Test' adds nothing; it edits vanilla decks in place.  A")
    print("      prefix-based scan would report zero changes for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
