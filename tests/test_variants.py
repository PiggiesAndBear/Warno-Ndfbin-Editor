# -*- coding: utf-8 -*-
"""T4.4 acceptance: change ExperienceLevel / TransporterDescriptor.

Both are part of a pack's content signature, so neither is an in-place field
write — each repoints the slot at the pack that carries the wanted variant,
reusing an existing one when the file has it.  Measured on Test: 1082 of 2100
units ship more than one variant, so most transitions are pure reuse.
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import TEST, deck                            # noqa: E402
from ndfbin_decks.core import cndf_codec
from ndfbin_decks.core.deckmodel import DeckFile
from ndfbin_decks.edit.equip import DeckEditor, PackIndex, PackKey
from ndfbin_decks.edit.quantity import Retiler

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(label)
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))


SRC = deck(TEST)


def fresh() -> tuple[DeckFile, DeckEditor, Path]:
    tmp = Path(tempfile.mkdtemp()) / "Deck.ndfbin"
    shutil.copy(SRC, tmp)
    d = DeckFile(tmp)
    return d, DeckEditor(d), tmp


def main() -> int:
    print("=== 1. census: the field really does split packs into variants ===")
    d, ed, _tmp = fresh()
    from collections import defaultdict
    var = defaultdict(set)
    for oid, p in d._packs.items():
        u = (p.get("UnitDescriptor", {}).get("value") or "?").rsplit("/", 1)[-1]
        var[u].add((p.get("ExperienceLevel", {}).get("value"),
                    (p.get("TransporterDescriptor", {}).get("value") or "")))
    multi = {k: v for k, v in var.items() if len(v) > 1}
    check("many units ship several variants", len(multi) > 500,
          f"{len(multi)} of {len(var)} units")
    xp_levels = sorted({x for v in var.values() for x, _ in v if x is not None})
    check("ExperienceLevel stays in 1..3", xp_levels == [1, 2, 3], str(xp_levels))

    print()
    print("=== 2. set_experience onto an existing variant reuses a pack ===")
    d, ed, tmp = fresh()
    b = d.battalions()[0]
    did = b.deck_object_id
    r = Retiler(d)
    # Find an *entry* whose unit also exists at another level.  A variant
    # lives in the pack, so the whole window is repointed, never one slot --
    # that is what W3 demands and what the old slot-level setter broke.
    idx = PackIndex.build(d)
    target = None
    for b in d.battalions():
        pool = r.pool(b.deck_object_id)
        for ci, c in enumerate(b.companies):
            for si, pl in enumerate(c.platoons):
                for ei, w in enumerate(pl.windows):
                    if not 0 <= w.pack_index < len(pool):
                        continue
                    k = ed.pack_key(pool[w.pack_index])
                    if not k or k.experience is None:
                        continue
                    other = PackKey(unit=k.unit, transporter=k.transporter,
                                    experience=3 if k.experience != 3 else 2,
                                    number=k.number)
                    if idx.find(other) is not None:
                        target = (b.deck_object_id, ci, si, ei, w, k, other)
                        break
                if target:
                    break
            if target:
                break
        if target:
            break
    check("found an entry with a same-unit variant", target is not None)
    if target:
        did2, ci2, si2, ei2, w2, k, other = target
        before_packs = len(d._packs)
        res = ed.set_entry_experience(did2, ci2, si2, ei2, other.experience)
        check("change accepted", res.ok, res.note)
        check("an existing pack was reused", res.reused,
              f"created={res.created_pack_id}")
        check("no new pack object", len(d._packs) == before_packs)
        pool2 = ed.pool_of(did2)
        levels = {ed.pack_key(pool2[sl]).experience for sl in w2.slots()}
        check("every slot of the entry carries the wanted level",
              levels == {other.experience}, str(levels))
        tmp.write_bytes(cndf_codec.rebuild_objects(d.doc, d.info))
        d2 = DeckFile(tmp)
        b2 = d2.find(d2.info["exports"].get(did2, "").rsplit("/", 1)[-1])
        check("survives a save/reload", b2 is not None and
              b2.window_sum == len(Retiler(d2).pool(b2.deck_object_id)),
              "W1 still holds")

    print()
    print("=== 3. set_experience to an unused level creates a pack ===")
    d, ed, tmp = fresh()
    b = d.battalions()[0]
    did = b.deck_object_id
    pool = Retiler(d).pool(did)
    k = ed.pack_key(pool[0])
    before_packs = len(d._packs)
    unused = next(x for x in (1, 2, 3)
                  if idx.find(PackKey(unit=k.unit, transporter=k.transporter,
                                      experience=x, number=k.number)) is None)
    res = ed.set_entry_experience(did, 0, 0, 0, unused)
    check("change accepted", res.ok, res.note)
    check("a pack object was created", not res.reused,
          f"created={res.created_pack_id}")
    ed.save(tmp)          # grows the IMPR trie if a new unit path appeared
    d2 = DeckFile(tmp)
    check("file grew by exactly one pack", len(d2._packs) == before_packs + 1,
          f"{before_packs} -> {len(d2._packs)}")
    b2 = d2.find(d.info["exports"].get(did, "").rsplit("/", 1)[-1])
    check("W1 still holds after the new pack",
          b2.window_sum == len(Retiler(d2).pool(b2.deck_object_id)))

    print()
    print("=== 4. set_transporter both ways ===")
    d, ed, tmp = fresh()
    r = Retiler(d)
    hit = None
    for bb in d.battalions():
        pool = r.pool(bb.deck_object_id)
        for ci, c in enumerate(bb.companies):
            for si, pl in enumerate(c.platoons):
                for ei, w in enumerate(pl.windows):
                    if not 0 <= w.pack_index < len(pool):
                        continue
                    k = ed.pack_key(pool[w.pack_index])
                    if k and k.transporter:
                        hit = (bb.deck_object_id, ci, si, ei, w, k)
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            break
    check("found an entry that carries a transporter", hit is not None)
    if hit:
        did, ci, si, ei, w, cur = hit
        res = ed.set_entry_transporter(did, ci, si, ei, None)
        check("dropping the transporter accepted", res.ok, res.note)
        p_after = ed.pool_of(did)
        after = ed.pack_key(p_after[w.pack_index])
        check("entry no longer has a transporter",
              after is not None and after.transporter is None,
              after.describe() if after else "pack is gone")
        check("unit was preserved",
              after is not None and after.unit == cur.unit)
        check("the whole window dropped it, not one slot",
              {ed.pack_key(p_after[sl]).transporter for sl in w.slots()}
              == {None})
        res2 = ed.set_entry_transporter(did, ci, si, ei, cur.transporter)
        check("re-adding it accepted", res2.ok, res2.note)
        p_back = ed.pool_of(did)
        back = ed.pack_key(p_back[w.pack_index])
        check("entry has the transporter back",
              back is not None and back.transporter == cur.transporter,
              back.describe())
        bb2 = d.find(d.info["exports"].get(did, "").rsplit("/", 1)[-1])
        check("W1 still holds on that deck",
              bb2.window_sum == len(r.pool(did)),
              f"sum={bb2.window_sum} pool={len(r.pool(did))}")

    print()
    print("=== 5. guards ===")
    d, ed, _ = fresh()
    b = d.battalions()[0]
    did = b.deck_object_id
    n = len(Retiler(d).pool(did))
    res = ed.set_entry_experience(did, 0, 0, 9999, 2)
    check("a non-existent entry is refused", not res.ok, res.note)

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("PASS — all variant-field checks green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())