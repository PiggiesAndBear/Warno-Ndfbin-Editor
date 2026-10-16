# -*- coding: utf-8 -*-
"""Acceptance for the division-label / country rule.

One rule, three consumers — the 师 picker, the 国家 picker and a country-wide
batch replace — so it is checked directly rather than only through the window.
The interesting part is that the file does **not** store a country: it is read
off the export name, and the name spells East Germany the French way.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ndfbin_decks.io import modlocator, divisions as D    # noqa: E402

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(label)
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" +
          (f"  — {detail}" if detail else ""))


def main() -> int:
    print("=== 1. what a label is made of ===")
    check("the export prefix and the campaign suffix both come off",
          D.division_label(
              "$/GFX/Division/Descriptor_Deck_Division_RFA_TK_Nord_solo")
          == "RFA_TK_Nord",
          D.division_label(
              "$/GFX/Division/Descriptor_Deck_Division_RFA_TK_Nord_solo"))
    check("each campaign suffix is handled",
          all(D.division_label(f"$/GFX/Division/Descriptor_Deck_Division_A_b{s}")
              == "A_b" for s in ("_solo", "_coop", "_multi")),
          str([D.division_label(
              f"$/GFX/Division/Descriptor_Deck_Division_A_b{s}")
              for s in ("_solo", "_coop", "_multi")]))
    check("no path becomes a visible placeholder, not an empty string",
          D.division_label(None) and D.division_label(""),
          repr(D.division_label("")))

    print()
    print("=== 2. the country is the label's first token ===")
    check("a full path works",
          D.country_code(
              "$/GFX/Division/Descriptor_Deck_Division_SOV_27GMRD_solo")
          == "SOV")
    check("an already-stripped label works too",
          D.country_code("SOV_27GMRD") == "SOV")
    check("there is no country without an underscore",
          D.country_code("NATO") == "" and D.country_code("") == "")
    check("case is normalised", D.country_code("sov_1") == "SOV")

    print()
    print("=== 3. DDR has to find RDA ===")
    check("the division exports say RDA",
          D.country_code(
              "$/GFX/Division/Descriptor_Deck_Division_RDA_11MSD_solo") == "RDA")
    check("the picker spells both",
          "RDA" in D.country_label("RDA", 18, 229)
          and "DDR" in D.country_label("RDA", 18, 229),
          D.country_label("RDA", 18, 229))
    check("and shows the name and both counts",
          D.country_label("SOV", 54, 515) == "SOV / USSR  苏联  (54 师 · 515 营)",
          D.country_label("SOV", 54, 515))
    check("an invented code degrades to itself",
          D.country_label("XYZ", 1) == "XYZ  XYZ  (1 师)",
          D.country_label("XYZ", 1))

    print()
    print("=== 4. against a real mod ===")
    try:
        mods = {m.name: m for m in modlocator.discover()}
    except modlocator.ModNotFound:
        print("SKIP — no mod workspace")
        return 0
    from ndfbin_decks.app.session import open_mod
    if "Test" not in mods:
        print("SKIP — Test mod not installed")
        return 0
    session = open_mod("Test")
    divs = session.divisions()
    codes = {D.country_code(d.path) for d in divs}
    check("every division yields a country", "" not in codes,
          f"{sum(1 for d in divs if not D.country_code(d.path))} without one")
    check("the countries partition the divisions",
          sum(len(c.divisions) for c in session.countries()) == len(divs),
          f"{sum(len(c.divisions) for c in session.countries())} vs {len(divs)}")
    check("the battalions are partitioned too",
          sum(len(c.battalions) for c in session.countries())
          == len(session.battalions()),
          f"{sum(len(c.battalions) for c in session.countries())} "
          f"vs {len(session.battalions())}")
    biggest = session.countries()[0]
    check("the biggest contingent leads", biggest.code == "SOV",
          biggest.code)
    check("RDA and RFA are distinct, not one 'Germany'",
          {"RDA", "RFA"} <= codes, str(sorted(codes)))

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s) failed: {FAILURES[:4]}")
        return 1
    print("PASS — the country rule holds on the real roster")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
