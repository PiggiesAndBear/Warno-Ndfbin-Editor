# -*- coding: utf-8 -*-
"""T4.3 acceptance: adding / removing a window inside a smart group.

Checks, in order of importance:

1. the deck still satisfies W1-W4 after the edit (read back from bytes);
2. the file re-encodes and re-decodes without error;
3. the guards fire — removing the last window is refused, bad arguments are
   rejected;
4. no-op invariance — a relay that changes nothing must rebuild byte-identical.
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import TEST, deck                            # noqa: E402
from ndfbin_decks.core import cndf_codec
from ndfbin_decks.core.deckmodel import DeckFile
from ndfbin_decks.edit.equip import DeckEditor, PackKey
from ndfbin_decks.edit.quantity import Retiler
from ndfbin_decks.edit.structure import StructureEditor

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(label)
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))


SRC = deck(TEST)


def fresh() -> tuple[DeckFile, StructureEditor, Path]:
    tmp = Path(tempfile.mkdtemp()) / "Deck.ndfbin"
    shutil.copy(SRC, tmp)
    d = DeckFile(tmp)
    return d, StructureEditor(d, DeckEditor(d)), tmp


def audit(d: DeckFile, did: int) -> tuple[bool, bool, bool, bool, int, int]:
    r = Retiler(d)
    pool = r.pool(did)
    groups = r._windows(did)
    pos, seen = 0, set()
    w2 = w3 = w4 = True
    for cgroup in groups:
        for pr in cgroup:
            for item in pr:
                s = item["key"]["value"]
                c = item["value"]["value"]
                if pos != s:
                    w4 = False
                seg = set(range(pos, pos + c))
                if seen & seg:
                    w2 = False
                seen |= seg
                ids = pool[s:s + c]
                if ids and len(set(ids)) != 1:
                    w3 = False
                pos += c
    return (pos == len(pool)), w2, w3, w4, len(pool), pos


def roundtrip(d: DeckFile, tmp: Path, name: str) -> DeckFile:
    tmp.write_bytes(cndf_codec.rebuild_objects(d.doc, d.info))
    return DeckFile(tmp)


def main() -> int:
    print("=== 1. insert a window (reuse an existing pack) ===")
    d, se, tmp = fresh()
    b = next(x for x in d.battalions()
             if any(len(p.windows) >= 2 for c in x.companies for p in c.platoons))
    did = b.deck_object_id
    loc = next((c.index, p.index) for c in b.companies for p in c.platoons
               if len(p.windows) >= 2)
    ci, si = loc
    before_windows = len(b.companies[ci].platoons[si].windows)
    before_pool = len(Retiler(d).pool(did))
    sample_pack = b.companies[ci].platoons[si].entries[0].pack_object_id

    res = se.insert_window(did, ci, si, count=2, reuse_pack=sample_pack)
    check("insert accepted", res.ok, res.note)
    print(f"        {res.summary()}")
    check("pack was reused, not created", res.pack_reused)
    check("window count grew", len(Retiler(d)._windows(did)[ci][si]) == before_windows + 1)

    d2 = roundtrip(d, tmp, "after insert")
    b2 = d2.find(b.export_name)
    w1, w2, w3, w4, npl, pos = audit(d2, b2.deck_object_id)
    check("W1 holds", w1, f"pool={npl} sum={pos}")
    check("W2 holds", w2)
    check("W3 holds", w3)
    check("W4 holds", w4)
    check("pool grew by count", npl == before_pool + 2, f"{before_pool} -> {npl}")
    p2 = b2.companies[ci].platoons[si]
    check("new window reads back with the reused pack",
          any(e.pack_object_id == sample_pack for e in p2.entries))

    print()
    print("=== 2. insert creating a brand-new pack (same pipeline as swap_slot) ===")
    d, se, tmp = fresh()
    b = d.battalions()[0]
    did = b.deck_object_id
    n_packs_before = len(d._packs)
    key = PackKey(unit="$/GFX/Unit/Descriptor_Unit_Mi_24_P_POL", experience=1, number=1)
    res = se.insert_window(did, 0, 0, count=1, key=key)
    check("insert with new key accepted", res.ok, res.note)
    print(f"        {res.summary()}")
    check("a pack object was created", not res.pack_reused)
    # A brand-new pack brings a new $/GFX/Unit path with it, so the IMPR trie
    # has to grow first — that is DeckEditor.save()'s job, not rebuild_objects'.
    se.packs.save(tmp)
    d2 = DeckFile(tmp)
    b2 = d2.find(b.export_name)
    check("deck still resolves", b2 is not None)
    check("file has one more pack", len(d2._packs) == n_packs_before + 1,
          f"{n_packs_before} -> {len(d2._packs)}")
    w1, w2, w3, w4, npl, pos = audit(d2, b2.deck_object_id)
    check("W1-W4 hold", w1 and w2 and w3 and w4, f"pool={npl} sum={pos}")
    check("the new unit is reachable from the pool",
          any(e.unit == key.unit for e in b2.companies[0].platoons[0].entries))

    print()
    print("=== 3. removing a window shrinks the pool ===")
    d, se, tmp = fresh()
    b = next(x for x in d.battalions()
             if any(len(p.windows) >= 2 for c in x.companies for p in c.platoons))
    did = b.deck_object_id
    ci, si = next((c.index, p.index) for c in b.companies for p in c.platoons
              if len(p.windows) >= 2)
    pl = b.companies[ci].platoons[si]
    before_windows = len(pl.windows)
    dropped_count = pl.windows[1].count
    before_pool = len(Retiler(d).pool(did))
    res = se.remove_window(did, ci, si, 1)
    check("remove accepted", res.ok, res.note)
    print(f"        {res.summary()}")
    check("pool shrank by the dropped count",
          res.retile.pool_after == before_pool - dropped_count,
          f"{before_pool} -> {res.retile.pool_after} (dropped {dropped_count})")
    d2 = roundtrip(d, tmp, "after remove")
    b2 = d2.find(b.export_name)
    w1, w2, w3, w4, npl, pos = audit(d2, b2.deck_object_id)
    check("W1-W4 hold", w1 and w2 and w3 and w4, f"pool={npl} sum={pos}")
    check("group lost exactly one window",
          len(b2.companies[ci].platoons[si].windows) == before_windows - 1,
          f"{before_windows} -> {len(b2.companies[ci].platoons[si].windows)}")

    print()
    print("=== 4. guards ===")
    d, se, tmp = fresh()
    b = next(x for x in d.battalions()
             if any(len(p.windows) == 1 for c in x.companies for p in c.platoons))
    did = b.deck_object_id
    ci, si = next((c.index, p.index) for c in b.companies for p in c.platoons
                  if len(p.windows) == 1)
    res = se.remove_window(did, ci, si, 0)
    check("removing the only window is refused", not res.ok, res.note)
    print(f"        note: {res.note}")

    res = se.insert_window(did, ci, si, count=0, reuse_pack=sample_pack)
    check("count=0 refused", not res.ok, res.note)
    res = se.insert_window(did, ci, si, count=1)
    check("neither key nor reuse_pack refused", not res.ok, res.note)
    res = se.insert_window(did, ci, si, count=1,
                           key=key, reuse_pack=sample_pack)
    check("both key and reuse_pack refused", not res.ok, res.note)
    res = se.remove_window(did, ci, si, 9)
    check("out-of-range window refused", not res.ok, res.note)
    res = se.insert_window(did, 99, 99, count=1, reuse_pack=sample_pack)
    check("out-of-range smart group refused", not res.ok, res.note)

    print()
    print("=== 5. no-op relay is byte-identical ===")
    d, se, tmp = fresh()
    before = tmp.read_bytes()
    n = 0
    all_ok = True
    for b in d.battalions():
        tile = Retiler(d).relay(b.deck_object_id)
        all_ok = all_ok and tile.ok
        if any(x.moved for x in tile.layouts):
            FAILURES.append("relay moved a window it should not have")
        n += 1
    after = cndf_codec.rebuild_objects(d.doc, d.info)
    check(f"all {n} relays accepted", all_ok)
    check("no window moved", not any(x.startswith("relay moved")
                                    for x in FAILURES))
    check("bytes unchanged", after == before,
          "" if after == before else f"{len(after)} vs {len(before)}")

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("PASS — all structure-edit checks green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())