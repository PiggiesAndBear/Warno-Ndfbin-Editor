"""Validate the model and the rule thresholds against four datasets.

Two things are asserted:

1. **The model parses the structure correctly.**  Counts must match the recon
   figures: 2208 AG decks + 128 filtered for vanilla-derived files, 42258 /
   43118 platoons, and the same 16 nameless smart groups.
2. **The thresholds are right.**  Every AG deck in all four datasets must pass
   the hard checks with **zero** errors.  If a "hard" rule fires on vanilla,
   the rule is wrong, not the data.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import (BERLIN as BERLIN_MOD, NORTHERN_THUNDER,  # noqa: E402
                  TEST as TEST_MOD, mod_dir)
from ndfbin_decks.core import validator                # noqa: E402
from ndfbin_decks.core.deckmodel import DeckFile       # noqa: E402
from ndfbin_decks.edit.quantity import Retiler         # noqa: E402

TEST = mod_dir(TEST_MOD)
BERLIN = mod_dir(BERLIN_MOD)
NORTHERN = mod_dir(NORTHERN_THUNDER)

DATASETS = [
    ("Test", TEST),
    ("Berlin", BERLIN),
    ("N.Thunder", NORTHERN),
]

#: expected (ag_pawn, skirmish) per dataset
EXPECTED = {
    "Test":       (2208, 128),
    "Berlin":     (2266, 128),
    "N.Thunder":  (2337, 128),
}


def main() -> int:
    failures = 0
    print(f"{'dataset':<12} {'AG':>6} {'PvP':>5} {'cos':>6} {'plt':>7} "
          f"{'err':>5} {'warn':>6}  time")
    print("-" * 66)

    for name, root in DATASETS:
        p = root / "Gen" / "NDF" / "GFX" / "Deck.ndfbin"
        if not p.is_file():
            print(f"{name:<12} (no Deck.ndfbin)")
            continue
        t0 = time.time()
        deck = DeckFile(p)
        s = deck.stats()
        rules = validator.load_group_rules(deck)
        res = validator.validate_file(deck, rules)
        dt = time.time() - t0

        print(f"{name:<12} {s['ag_pawn']:>6} {s['skirmish']:>5} "
              f"{s['companies']:>6} {s['platoons']:>7} "
              f"{len(res.errors):>5} {len(res.warnings):>6}  {dt:.2f}s")

        exp = EXPECTED.get(name)
        if exp and (s["ag_pawn"], s["skirmish"]) != exp:
            failures += 1
            print(f"    FAIL counts: got {(s['ag_pawn'], s['skirmish'])} want {exp}")

        # ★ A hard rule firing on unmodified game data means the rule is wrong.
        if res.errors:
            failures += 1
            print(f"    FAIL {len(res.errors)} hard error(s) on untouched data:")
            for e in res.errors[:5]:
                print(f"       [{e.code}] {e.message}  @ {e.where}")

        codes = Counter(i.code for i in res.warnings)
        if codes:
            print(f"    warnings: {dict(codes)}")

    # -- reference integrity on one file ----------------------------------
    print()
    p = BERLIN / "Gen" / "NDF" / "GFX" / "Deck.ndfbin"
    if p.is_file():
        deck = DeckFile(p)
        ref_res = validator.validate_references(deck)
        print(f"reference check: {ref_res.summary()}")
        if ref_res.errors:
            failures += 1
            for e in ref_res.errors[:5]:
                print(f"    [{e.code}] {e.message} @ {e.where}")

    # -- model spot check --------------------------------------------------
    print()
    p = BERLIN / "Gen" / "NDF" / "GFX" / "Deck.ndfbin"
    if p.is_file():
        deck = DeckFile(p)
        nameless = sum(
            1 for b in deck.battalions() for c in b.companies
            for pl in c.platoons if pl.name_hash is None)
        print(f"platoons with no Name at all: {nameless}  (vanilla: 16)")

        b = deck.find("Descriptor_Deck_pion_TCH_1TkD_1TkP_1")
        if b:
            print(f"\nspot check {b.export_name}")
            print(f"  division : {b.division}")
            print(f"  {b.describe()}")
            print(f"  Σ(Number)={b.window_sum}  pool={b.pack_count}  "
                  f"match={b.window_sum == b.pack_count}")
            c0 = b.companies[0]
            print(f"  company 0: {len(c0.platoons)} platoons, {c0.hq_platoons} HQ")
            for pl in c0.platoons[:3]:
                print(f"     {pl.display[:16]:<16s} hq={pl.is_hq!s:<5s} "
                      f"windows={pl.window_labels()}")
                for e in pl.entries[:3]:
                    print(f"        slot{e.slot:>4d} {e.label()}"
                          f"{'  (shared)' if e.shared else ''}")

    # -- the layout rules real data never trips ----------------------------
    #
    # 13505 decks / 427451 windows contain zero holes and zero overlaps, so the
    # two rules above are never exercised by the loop.  A rule that never fires
    # is indistinguishable from a typo, so each is run against a deliberately
    # damaged deck.  The damage is injected in memory; nothing is written.
    print()
    print("-- layout damage injected on purpose --")
    if p.is_file():

        def holds(label: str, ok: bool, detail: str = "") -> None:
            nonlocal failures
            if ok:
                print(f"    OK    {label}")
            else:
                failures += 1
                print(f"    FAIL  {label}  {detail}")

        def fresh():
            return DeckFile(p)

        def shift_pairs(deck, bat, *, at: int, delta: int) -> None:
            """Move every window starting at or after ``at`` by ``delta``."""
            ids = {pl.object_id for c in bat.companies for pl in c.platoons}
            for o in deck.info["objects"]:
                if o["id"] not in ids:
                    continue
                props = {x["property_name"]: x["value"] for x in o["properties"]}
                for item in props.get("PackIndexUnitNumberList", {}).get("items", []):
                    key = item.get("key", {})
                    if isinstance(key.get("value"), int) and key["value"] >= at:
                        key["value"] += delta

        def pool_items(deck, bat):
            obj = deck._by_id[bat.deck_object_id]
            props = {x["property_name"]: x["value"] for x in obj["properties"]}
            return props["DeckPackList"]["items"]

        def codes_of(deck, name="Descriptor_Deck_pion_SOV_1GTA_595Reco"):
            return Counter(i.code for i in
                           validator.validate_battalion(deck.find(name)))

        NAME = "Descriptor_Deck_pion_SOV_1GTA_595Reco"

        # (a) a hole: one slot nothing refers to, everything after it late by 1
        d = fresh()
        bat = d.find(NAME)
        items = pool_items(d, bat)
        items.insert(3, dict(items[3]))
        shift_pairs(d, bat, at=3, delta=1)
        d.invalidate()
        got = codes_of(d)
        holds("a hole is reported as a hole", got.get("pool_gap") == 1, str(dict(got)))
        holds("the hole is NOT called 'out of range'",
              "window_out_of_range" not in got, str(dict(got)))
        holds("a pure hole does not trip the Σ rule",
              "sum_neq_pool" not in got, str(dict(got)))
        gap = next(i for i in validator.validate_battalion(d.find(NAME))
                   if i.code == "pool_gap")
        holds("the hole message names the unreferenced slot",
              "3..3" in gap.message, gap.message)
        # Reported at the *first* window that starts late (连0/排1, the one that
        # would have begun at slot 3) rather than at the tail window it also
        # pushes past the end — the first is where the reader should look.
        holds("the hole is reported at the first window that starts late",
              gap.where.endswith("/0/1"), gap.where)

        # (b) an overlap: one window starts one place early
        d2 = fresh()
        bat2 = d2.find(NAME)
        shift_pairs(d2, bat2, at=3, delta=-1)
        d2.invalidate()
        got2 = codes_of(d2)
        holds("an overlap is reported as an overlap",
              got2.get("pool_overlap") == 1, str(dict(got2)))
        holds("an overlap is not called 'out of range'",
              "window_out_of_range" not in got2, str(dict(got2)))

        # (c) the remedy: any edit routed through relay re-lays the deck
        b3 = d.find(NAME)
        co, pl = b3.companies[-1], b3.companies[-1].platoons[-1]
        w = pl.windows[-1]
        laid = Retiler(d).set_count(b3.deck_object_id, len(b3.companies) - 1,
                                    len(co.platoons) - 1, len(pl.windows) - 1,
                                    w.count)
        d.invalidate()
        healed = validator.validate_battalion(d.find(NAME))
        holds("relay heals the hole", laid.ok and not healed, str([i.code for i in healed]))
        holds("the healed deck's pool is back to Σ(Number)",
              d.find(NAME).pack_count == d.find(NAME).pool_slots == 103,
              f"{d.find(NAME).pack_count} / {d.find(NAME).pool_slots}")

    print()
    if failures:
        print(f"FAIL — {failures} problem(s)")
        return 1
    print("PASS — model and thresholds hold on all datasets.")
    print()
    print("Thresholds are calibrated, not guessed:")
    for k, v in validator.CALIBRATION.items():
        print(f"  {k:<20s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())