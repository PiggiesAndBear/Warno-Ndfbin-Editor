"""Validate the two-level deck diff against the known ``Test`` mod edits.

Ground truth established during recon:

* ``Test`` changes **39** decks across **7** divisions.
* Of those, 6 also change the pack *count*; the other 33 are same-count swaps
  (``T55A``->``T72``, ``MiG-21MA``->``MiG-29AA`` …) that a deck-property-only
  fingerprint cannot see.
* ``Berlin`` versus ``Test`` must report the 39 as a superset, since Berlin is
  the untouched reference for the vanilla decks.

Run with::

    python -m tests.test_differ
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import BERLIN as BERLIN_MOD, TEST as TEST_MOD, deck, mod_dir  # noqa: E402
from ndfbin_decks.core import differ                     # noqa: E402
from ndfbin_decks.io import agfilter, localisation       # noqa: E402

# Discovered rather than written down: the suite has to run on a machine that
# is not this one, and a literal path would also publish its user name.
BERLIN = mod_dir(BERLIN_MOD)
TEST = mod_dir(TEST_MOD)

BERLIN_DECK = deck(BERLIN_MOD)
TEST_DECK = deck(TEST_MOD)

#: decks the recon identified as edited, by division
EXPECTED_DECKS = {
    "Descriptor_Deck_pion_RDA_JBG37",
    "Descriptor_Deck_pion_SOV_27GMRD_244GMSP_1",
    "Descriptor_Deck_pion_SOV_27GMRD_244GMSP_3",
    "Descriptor_Deck_pion_SOV_27GMRD_28GTk_3",
    "Descriptor_Deck_pion_SOV_27GMRD_68GMSP_1",
    "Descriptor_Deck_pion_SOV_27GMRD_68GMSP_2",
    "Descriptor_Deck_pion_TCH_19MRD_104MSP_1",
    "Descriptor_Deck_pion_TCH_19MRD_104MSP_2",
    "Descriptor_Deck_pion_TCH_19MRD_104MSP_3",
    "Descriptor_Deck_pion_TCH_19MRD_57MSP_1",
    "Descriptor_Deck_pion_TCH_19MRD_57MSP_2",
    "Descriptor_Deck_pion_TCH_19MRD_57MSP_3",
    "Descriptor_Deck_pion_TCH_19MRD_67MSP_1",
    "Descriptor_Deck_pion_TCH_19MRD_67MSP_2",
    "Descriptor_Deck_pion_TCH_19MRD_67MSP_3",
    "Descriptor_Deck_pion_TCH_1TkD_1TkP_1",
    "Descriptor_Deck_pion_TCH_1TkD_1TkP_2",
    "Descriptor_Deck_pion_TCH_1TkD_1TkP_3",
    "Descriptor_Deck_pion_TCH_1TkD_21TkP_1",
    "Descriptor_Deck_pion_TCH_1TkD_21TkP_2",
    "Descriptor_Deck_pion_TCH_1TkD_21TkP_3",
    "Descriptor_Deck_pion_TCH_1TkD_2TkP_1",
    "Descriptor_Deck_pion_TCH_1TkD_2TkP_2",
    "Descriptor_Deck_pion_TCH_1TkD_2TkP_3",
    "Descriptor_Deck_pion_TCH_1TkD_3MSP_1",
    "Descriptor_Deck_pion_TCH_1TkD_3MSP_2",
    "Descriptor_Deck_pion_TCH_1TkD_3MSP_3",
    "Descriptor_Deck_pion_TCH_20MRD_49MSP_1",
    "Descriptor_Deck_pion_TCH_20MRD_49MSP_2",
    "Descriptor_Deck_pion_TCH_20MRD_49MSP_3",
    "Descriptor_Deck_pion_TCH_20MRD_65MSP_1",
    "Descriptor_Deck_pion_TCH_20MRD_65MSP_2",
    "Descriptor_Deck_pion_TCH_20MRD_65MSP_3",
    "Descriptor_Deck_pion_TCH_20MRD_74MSP_1",
    "Descriptor_Deck_pion_TCH_20MRD_74MSP_2",
    "Descriptor_Deck_pion_TCH_20MRD_74MSP_3",
    "Descriptor_Deck_pion_TCH_216TkInde_1",
    "Descriptor_Deck_pion_TCH_4FighterRgt",
    "Descriptor_Deck_pion_TCH_5FighterRgt",
}


def main() -> int:
    if not BERLIN_DECK.is_file() or not TEST_DECK.is_file():
        print("FATAL: need both Berlin and Test Deck.ndfbin to run this test")
        return 2

    before = differ.DeckFileView.load(BERLIN_DECK)
    after = differ.DeckFileView.load(TEST_DECK)
    print(f"loaded  Berlin {len(before.decks)} decks / "
          f"{len(before.packs)} packs")
    print(f"loaded  Test   {len(after.decks)} decks / "
          f"{len(after.packs)} packs")

    changes = differ.diff(before, after)
    print(f"\ndiffs: {len(changes)}")

    by_name = {c.name: c for c in changes}
    found = set(by_name) & EXPECTED_DECKS
    extra = set(by_name) - EXPECTED_DECKS
    missed = EXPECTED_DECKS - set(by_name)

    # only AG decks are in scope
    pvp = [n for n in by_name
           if not agfilter.is_ag_pawn_deck(after.decks[n])]

    count_changed = [c for c in changes if c.pack_count_changed]
    retiled = [c for c in changes if c.group_changes]

    print(f"  expected decks edited      : {len(EXPECTED_DECKS)}")
    print(f"  found                      : {len(found)}")
    print(f"  missing                    : {len(missed)}")
    print(f"  unexpected extras          : {len(extra)}")
    print(f"  pack count also changed    : {len(count_changed)}")
    print(f"  smart-group intervals moved: {len(retiled)}")
    if pvp:
        print(f"  !! out-of-scope PvP decks in results: {pvp}")

    if missed:
        print(f"\n  MISSED: {sorted(missed)[:6]}")
    if extra:
        print(f"\n  EXTRA  : {sorted(extra)[:6]}")

    # Show one of the same-count swaps, the case a naive diff misses.
    print("\n--- sample: same-count equipment swap ---")
    swap = next((c for c in changes
                 if not c.pack_count_changed and c.added_units), None)
    if swap:
        print(f"  {swap.describe()}")
        for u in swap.removed_units[:3]:
            print(f"      - {u.label()}")
        for u in swap.added_units[:3]:
            print(f"      + {u.label()}")
    else:
        print("  (none found)")

    print("\n--- sample: pack insertion + retiling ---")
    ins = next((c for c in changes if c.pack_count_changed
                and c.group_changes), None)
    if ins:
        print(f"  {ins.describe()}")
        for u in ins.added_units[:3]:
            print(f"      + {u.label()}")
        for u in ins.removed_units[:3]:
            print(f"      - {u.label()}")
    else:
        print("  (none found)")

    print(f"\nchanged decks by family: "
          f"{Counter(n.split('_')[2] if len(n.split('_')) > 2 else '?' for n in by_name).most_common()}")

    if missed or extra or pvp:
        print("\nFAIL — deck set does not match the recon ground truth")
        return 1
    if len(found) < 30:
        print(f"\nFAIL — only {len(found)} of 39 decks detected; "
              f"the unit-level expansion is not working")
        return 1
    print(f"\nPASS — {len(found)}/{len(EXPECTED_DECKS)} edited decks detected, "
          f"including the same-count swaps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
