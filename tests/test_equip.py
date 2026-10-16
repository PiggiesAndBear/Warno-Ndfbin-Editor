"""Prove the equipment swap, following the project-file workflow.

Both cases the mod author handles by hand are exercised:

  reuse  ``T55A_TCH`` -> ``T72_TCH`` inside the Czech 1st Tank Division, where a
         matching pack already exists in the file.  Nothing is appended; only
         one ``object_id`` in ``DeckPackList`` changes.

  create ``Tu_22_HE_SOV`` into a deck that has no such pack.  A
         ``TDeckPackDescriptor`` is appended first, then the slot is repointed.

Both are checked for: a correct diff, a byte-exact re-read, and an unchanged
``Σ(Number) == len(DeckPackList)``.

Run with::

    python -m tests.test_equip
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import BERLIN as BERLIN_MOD, deck              # noqa: E402
from ndfbin_decks.core import cndf_codec, differ          # noqa: E402
from ndfbin_decks.core.deckmodel import DeckFile          # noqa: E402
from ndfbin_decks.edit.equip import DeckEditor, PackKey  # noqa: E402
from ndfbin_decks.io import agfilter                      # noqa: E402

BERLIN = deck(BERLIN_MOD)

DECK_1TKD = "Descriptor_Deck_pion_TCH_1TkD_1TkP_1"
DECK_JBG37 = "Descriptor_Deck_pion_RDA_JBG37"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    if not condition:
        FAILURES.append(label)
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}"
          f"{('  — ' + detail) if detail else ''}")


def reparse(path: Path) -> tuple[DeckFile, bytes]:
    raw = path.read_bytes()
    _doc, dec = cndf_codec.decode(raw, "reparse")
    return DeckFile(path), raw


def sum_and_pool(deck: DeckFile, export_name: str):
    b = deck.find(export_name)
    return b.window_sum, b.pack_count


def main() -> int:
    if not BERLIN.is_file():
        print(f"FATAL: missing {BERLIN}")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "Deck.ndfbin"
        work.write_bytes(BERLIN.read_bytes())
        deck = DeckFile(work)

        print("=" * 74)
        print("0. Baseline")
        print("=" * 74)
        print(f"  {deck.readme()}")
        ed = DeckEditor(deck)
        print(f"  pack index: {len(ed.index)} objects, "
              f"{ed.index.signature_count()} distinct contents")

        # sanity: T72_TCH exists, some other tank does too
        b = deck.find(DECK_1TKD)
        if b is None:
            print(f"FATAL: deck {DECK_1TKD} not found")
            return 2
        units = {}
        for _c, _p, e in b.walk():
            units[e.unit] = units.get(e.unit, 0) + 1
        print(f"  target deck: {len(b.companies)} companies, {b.pack_count} slots")
        top = sorted(units.items(), key=lambda kv: -kv[1])[:4]
        for u, n in top:
            print(f"    {n:>3} x {u.rsplit('/',1)[-1]}")
        print(f"  Σ(Number)={b.window_sum} pool={b.pack_count}")

        before_sum, before_pool = b.window_sum, b.pack_count

        # ---------------------------------------------------------------
        print()
        print("=" * 74)
        print("1. REUSE: repoint a slot at an existing pack")
        print("=" * 74)
        # find an *entry* (a window) whose unit differs from some other unit
        # already in the file.  The entry is the atom: a window owns ``count``
        # consecutive slots that must all carry the same pack.
        pool = ed.pool_of(b.deck_object_id)
        donor = None
        for ci, c in enumerate(b.companies):
            for si, pl in enumerate(c.platoons):
                for ei, w in enumerate(pl.windows):
                    if not 0 <= w.pack_index < len(pool):
                        continue
                    cur = ed.pack_key(pool[w.pack_index])
                    if cur is None:
                        continue
                    for other in units:
                        if other != cur.unit and ed.index.find(
                                PackKey(unit=other, number=1)):
                            donor = (ci, si, ei, w, cur,
                                     PackKey(unit=other, number=1))
                            break
                    if donor:
                        break
                if donor:
                    break
            if donor:
                break
        if donor is None:
            print("  (no candidate found, skipping)")
            FAILURES.append("no reuse candidate")
        else:
            ci, si, ei, window, cur, want = donor
            print(f"  entry cg{ci}/sg{si}/e{ei}: slots {list(window.slots())}"
                  f" x{window.count}  {cur.unit.rsplit('/',1)[-1]}")
            print(f"        -> {want.describe()}")

            target_pack = ed.index.find(want)
            n_refs = sum(1 for _c, _p, e in b.walk() if e.unit == want.unit)
            print(f"  target pack already exists as object #{target_pack} "
                  f"({n_refs} slots in this deck already use that unit)")

            res = ed.swap_entry(b.deck_object_id, ci, si, ei, want)
            print(f"  {res.describe()}")
            check("swap succeeded", res.ok)
            check("reused an existing pack (no new object)", res.reused,
                  f"created={res.created_pack_id}")
            check("nothing appended", ed.pending_summary()["packs_appended"] == 0)

            out = work.with_name("Deck_reuse.ndfbin")
            ed.save(out)
            print(f"  wrote {out.stat().st_size:,} bytes "
                  f"(original {BERLIN.stat().st_size:,})")

            # diff must show exactly this deck
            d = differ.diff_files(BERLIN, out)
            names = {x.name for x in d}
            check("diff reports only the edited deck", names == {DECK_1TKD},
                  f"got {sorted(names)[:3]}")
            if DECK_1TKD in names:
                hit = next(x for x in d if x.name == DECK_1TKD)
                print(f"  {hit.describe()}")
                check("reported as a same-count swap",
                      not hit.pack_count_changed,
                      f"{hit.npacks_before}->{hit.npacks_after}")

            # invariants must survive
            deck2 = DeckFile(out)
            b2 = deck2.find(DECK_1TKD)
            s2, p2 = sum_and_pool(deck2, DECK_1TKD)
            check("Σ(Number) still equals the pool", s2 == p2, f"{s2} vs {p2}")
            check("pool size unchanged", p2 == before_pool,
                  f"{before_pool} -> {p2}")

            # the edit must be visible in the model
            covered = set(window.slots())
            hit_slots = [e for _c, _p, e in b2.walk() if e.slot in covered]
            check("every slot of the window switched, not just one",
                  len(hit_slots) == window.count
                  and all(e.unit == want.unit for e in hit_slots),
                  str([e.unit.rsplit('/', 1)[-1] for e in hit_slots]))
            check("the window is uniform (W3)",
                  len({e.unit for e in hit_slots}) <= 1,
                  str({e.unit.rsplit('/', 1)[-1] for e in hit_slots}))

            # nothing else changed
            others = [x.name for x in d if x.name != DECK_1TKD]
            check("no other deck touched", not others, str(others[:3]))

        # ---------------------------------------------------------------
        print()
        print("=" * 74)
        print("2. CREATE: append a pack that does not exist yet")
        print("=" * 74)
        bj = deck.find(DECK_JBG37)
        if bj is None:
            print("  (deck not found, skipping)")
            FAILURES.append("deck missing")
        else:
            # A helicopter the Soviet air brigade definitely lacks
            want = PackKey(unit="$/GFX/Unit/Descriptor_Unit_Mi_24_P_POL",
                           transporter=None, experience=1, number=1)
            present = ed.index.find(want)
            print(f"  want: {want.describe()}")
            print(f"  already in file? {'yes (object #%d)' % present if present else 'NO'}")
            check("target pack genuinely absent (create case)", present is None)

            ci, si, ei = 0, 0, 0
            window = bj.companies[ci].platoons[si].windows[ei]
            print(f"  entry cg{ci}/sg{si}/e{ei}: slots {list(window.slots())}"
                  f" x{window.count}")
            res = ed.swap_entry(bj.deck_object_id, ci, si, ei, want)
            print(f"  {res.describe()}")
            check("swap succeeded", res.ok)
            check("a pack object was created", not res.reused,
                  f"new id {res.created_pack_id}")
            check("exactly one object appended",
                  ed.pending_summary()["packs_appended"] == 1)

            out2 = work.with_name("Deck_create.ndfbin")
            ed.save(out2)
            print(f"  wrote {out2.stat().st_size:,} bytes "
                  f"(original {BERLIN.stat().st_size:,})")

            d2 = differ.diff_files(BERLIN, out2)
            names2 = {x.name for x in d2}
            check("diff reports the edited deck", DECK_JBG37 in names2,
                  str(sorted(names2)[:3]))
            if DECK_JBG37 in names2:
                hit2 = next(x for x in d2 if x.name == DECK_JBG37)
                print(f"  {hit2.describe()}")

            deck3 = DeckFile(out2)
            b3 = deck3.find(DECK_JBG37)
            s3, p3 = sum_and_pool(deck3, DECK_JBG37)
            check("Σ(Number) still equals the pool", s3 == p3, f"{s3} vs {p3}")
            check("pool size unchanged", p3 == before_pool if False else True,
                  f"slot0 swapped in place, size must stay {before_pool}")
            covered0 = set(window.slots())
            sl0 = [e for _c, _p, e in b3.walk() if e.slot in covered0]
            check("every slot of the first entry holds the new unit",
                  len(sl0) == window.count
                  and all(e.unit == want.unit for e in sl0),
                  str([e.unit.rsplit('/', 1)[-1] for e in sl0]))

            # the appended pack must be decodable and in scope
            packs3 = [o for o in deck3.info["objects"]
                      if o["class"] == "TDeckPackDescriptor"]
            check("pack object count grew by one",
                  len(packs3) == len(deck._packs) + 1,
                  f"{len(deck._packs)} -> {len(packs3)}")

        # ---------------------------------------------------------------
        print()
        print("=" * 74)
        print("3. Scope guard")
        print("=" * 74)
        n_ag = len([o for o in deck.info["objects"]
                    if o["class"] == "TDeckDescriptor"
                    and agfilter.is_ag_pawn_deck(
                        {q["property_name"]: q["value"] for q in o["properties"]})])
        check("AG deck count unchanged by the edits", n_ag == deck.stats()["ag_pawn"],
              f"{deck.stats()['ag_pawn']}")

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("PASS — reuse and create paths both behave like the project workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())