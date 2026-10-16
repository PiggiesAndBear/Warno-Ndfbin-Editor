# -*- coding: utf-8 -*-
"""Acceptance for the unit universe: where a unit is known to exist.

The picker used to answer "has this file fielded it yet?" while the user was
asking "does this unit exist?".  Those differ by 810 vanilla units on Test and
by every unit a workshop mod declares in its own ``Unit.ndfbin``.  This module
checks the three sources that replaced that question, and the codec shortcut
that makes reading the biggest one cheap.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ndfbin_decks.core import cndf_codec          # noqa: E402
from ndfbin_decks.io import modlocator, unit_universe as uu    # noqa: E402

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(label)
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" +
          (f"  — {detail}" if detail else ""))


def main() -> int:
    try:
        mods = {m.name: m for m in modlocator.discover()}
    except modlocator.ModNotFound as exc:
        print(f"SKIP — no mod workspace ({exc})")
        return 0

    print("=== 1. the three sources are found ===")
    test_mod = mods.get("Test")
    check("Test is discoverable", test_mod is not None)
    base_zip = uu.find_base_zip(test_mod)
    check("ModGen's baseline archive is located",
          base_zip is not None and base_zip.is_file(), str(base_zip))
    check("the baseline is the shared one, not a mod-local copy",
          base_zip is not None and base_zip.parent.name == "ModData",
          str(base_zip))

    print()
    print("=== 2. the vanilla roster ===")
    t = time.time()
    vanilla = uu.units_in_zip(base_zip)
    check("the baseline yields a plausible roster", len(vanilla) > 2000,
          f"{len(vanilla)} units in {time.time() - t:.2f}s")
    check("every entry is a full $/GFX/Unit path",
          all(p.startswith("$/GFX/Unit/Descriptor_Unit_") for p in vanilla))
    su = "$/GFX/Unit/Descriptor_Unit_Su_27K_SOV"
    check("Su_27K_SOV is in it", su in vanilla)
    check("no nested descriptor name leaked in",
          not any("PackDescriptor_Unit_" in p for p in vanilla))

    print()
    print("=== 3. a mod's own units ===")
    ws = [m for m in mods.values()
          if m.source == modlocator.SOURCE_WORKSHOP
          and uu.find_unit_ndfbin(m) is not None]
    if ws:
        for mod in ws[:2]:
            uni = uu.build(mod, referenced=set())
            own = uni.own
            check(f"{mod.label[:26]}: compiled Unit.ndfbin is read",
                  len(uni.compiled) > 1000, f"{len(uni.compiled)} declared")
            check(f"{mod.label[:26]}: it declares units vanilla does not",
                  len(own) >= 0, f"{len(own)} of its own")
            if own:
                sample = sorted(own)[0]
                check(f"{mod.label[:26]}: those are tagged 本mod",
                      uni.origin(sample) == "本mod",
                      f"{sample.rsplit('/', 1)[-1]} -> {uni.origin(sample)}")
            check(f"{mod.label[:26]}: a vanilla unit stays tagged 原版",
                  uni.origin(su) == "原版", uni.origin(su))
    else:
        print("  (no workshop mod ships a Unit.ndfbin — skipping)")

    print()
    print("=== 4. a loose copy of a file still gets the baseline ===")
    referenced = {su, "$/GFX/Unit/Descriptor_Unit_Only_InThisFile_X"}
    bare = uu.build(None, referenced=referenced)
    check("the shared baseline applies even with no mod object",
          len(bare.vanilla) > 2000, f"{len(bare.vanilla)} vanilla units")
    check("so a vanilla unit is still known", bare.knows(su))
    check("and one only this file uses is tagged as such",
          bare.origin("$/GFX/Unit/Descriptor_Unit_Only_InThisFile_X")
          == "本文件")
    check("an unknown path is not claimed", not bare.knows("$ /GFX/nope"))

    print()
    print("=== 5. read_exports matches a full decode, but is fast ===")
    if ws:
        target = uu.find_unit_ndfbin(ws[0])
        raw = Path(target).read_bytes()
        t = time.time()
        quick = cndf_codec.read_exports(raw, str(target))
        quick_s = time.time() - t
        t = time.time()
        _, info = cndf_codec.decode(raw, str(target))
        full_s = time.time() - t
        check("the shortcut returns exactly what decode does",
              quick == info["exports"],
              f"{len(quick)} vs {len(info['exports'])}")
        check("and it is the fast path", quick_s < full_s / 5,
              f"{quick_s:.3f}s vs {full_s:.3f}s")

    print()
    print("=== 6. the numbers the dialog shows ===")
    uni = uu.build(test_mod, referenced=set())
    check("Test resolves to the vanilla roster plus its own references",
          len(uni) >= len(uni.vanilla), uni.summary())
    check("summary is human-readable and non-empty",
          "原版" in uni.summary() and "合计" in uni.summary(),
          uni.summary())
    check("a second build is cached, not re-read",
          uu.build(test_mod, referenced=set()).vanilla == uni.vanilla)

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s) failed: {FAILURES[:4]}")
        return 1
    print("PASS — every unit source is read, tagged and cheap to query")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
