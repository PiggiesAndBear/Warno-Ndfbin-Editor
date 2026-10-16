"""Structural validation of AG Pawn decks.

The thresholds are not guesses — each was calibrated against the vanilla game
plus three mods, then split by whether vanilla itself satisfies the rule.

**Hard errors** (vanilla and all three mods agree, so a violation is a real bug):

* ``sum_neq_pool`` — measured 2208/2208 vanilla, 2266/2266 Berlin, 2337/2337
  Northern Thunder, 2208/2208 Test.
* ``pack_index_tiles`` — the ``(PackIndex, Number)`` pairs must tile the slot
  pool with **no hole and no overlap**.  Measured 13505 decks / 427451 windows
  across seven files, 0 gaps, 0 overlaps.
* ``window_out_of_range`` — ``PackIndex + Number <= len(DeckPackList)``.
  ⚠ Measured **0 violations** across those same 427451 windows.  The 57
  "overruns" seen during recon are all in *skirmish* decks, which the game never
  indexes; they are not a counter-example.
* every platoon referenced by exactly one company — zero orphans everywhere.
* every ``obj_ref`` in range with a matching ``class_id``.

⚠ **``pack_count`` is not the pool length**, and conflating the two is what made
``window_out_of_range`` lie: a deck with an unreferenced slot has
``pack_count == Σ(Number) < len(DeckPackList)``, so a window that fits the file
perfectly well would be reported as "exceeding the pool" — pointing the reader at
the wrong thing entirely.  The range check now compares against
:attr:`~.deckmodel.Battalion.pool_slots`, and the hole has its own rule.

**Soft warnings** (vanilla violates these too, so blocking on them would reject
official data):

* HQ platoons per company != 1.  Berlin's 255 AG companies have **zero** HQ
  platoons; Northern Thunder has one in 66% of them.  Neither is wrong.
* platoons per company above ``MaxNbSmartGroupByCombatGroup`` (6).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .deckmodel import Battalion, DeckFile, Issue

ERROR = "error"
WARN = "warn"

#: Vanilla agreement, recorded so a future threshold change can be audited.
CALIBRATION = {
    "sum_neq_pool": "2208/2208 vanilla, 2266/2266 Berlin, "
                    "2337/2337 N.Thunder, 2208/2208 Test",
    "pack_index_tiles": "0 gaps / 0 overlaps in 13505 decks, 427451 windows",
    "window_in_range": "0 violations in all seven files",
    "no_orphan_platoon": "0 orphans in all four",
    "hq_per_company": "vanilla itself has 68 companies with != 1 -> warning only",
}


@dataclass
class ValidationResult:
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == WARN]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return (f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)")


def validate_battalion(bat: Battalion, group_rules: dict | None = None) -> list[Issue]:
    """Structural checks for one deck."""
    out: list[Issue] = []
    where = bat.export_name

    pool = sum(len(p.entries) for c in bat.companies for p in c.platoons)
    total = sum(w.count for c in bat.companies for p in c.platoons
                for w in p.windows)

    if total != pool:
        out.append(Issue(
            ERROR, "sum_neq_pool",
            f"Σ(Number)={total}, but the windows only resolve {pool} slot(s) "
            f"inside the {bat.pool_slots or pool}-slot pool",
            where))

    seen_platoons: Counter[int] = Counter()
    #: ``len(DeckPackList)``.  Falls back to ``pack_count`` for a hand-built
    #: Battalion that never went through ``_build`` — in a well-formed deck the
    #: two are equal, so the fallback cannot mask anything but damage.
    slots = bat.pool_slots or pool
    #: Running end of the windows seen so far, in deck → company → platoon →
    #: entry order.  W4 makes this the *only* legal value for the next
    #: ``PackIndex``, so it is also the yardstick for finding holes.
    run = 0
    for c in bat.companies:
        for p in c.platoons:
            # Counted once per platoon, not once per window: a platoon may
            # legitimately carry several ``(PackIndex, Number)`` pairs.
            seen_platoons[p.object_id] += 1
            for w in p.windows:
                if w.pack_index < 0:
                    out.append(Issue(
                        ERROR, "negative_index",
                        f"negative PackIndex {w.pack_index}", f"{where}/{c.index}"))
                if w.count < 1:
                    out.append(Issue(
                        ERROR, "bad_count",
                        f"Number is {w.count}, must be >= 1",
                        f"{where}/{c.index}/{p.index}"))
                if w.end > slots:
                    out.append(Issue(
                        ERROR, "window_out_of_range",
                        f"window {w} exceeds the {slots}-slot pool "
                        f"(it reaches slot {w.end - 1})",
                        f"{where}/{c.index}/{p.index}"))
                # W2/W4 — the pairs must *tile* the pool.  A hole is the one
                # layout defect that used to surface as "exceeds the pool"
                # while nothing was actually out of range: an unreferenced slot
                # pushes every later window one place along, so the last one
                # runs past the end of what the windows cover.
                if w.pack_index > run:
                    n = w.pack_index - run
                    out.append(Issue(
                        ERROR, "pool_gap",
                        f"{n} slot(s) {run}..{w.pack_index - 1} belong to no "
                        f"platoon; every window after them is shifted {n} "
                        f"slot(s) late ({w} starts at {w.pack_index})",
                        f"{where}/{c.index}/{p.index}"))
                elif w.pack_index < run:
                    n = run - w.pack_index
                    out.append(Issue(
                        ERROR, "pool_overlap",
                        f"window {w} starts at {w.pack_index}, but the windows "
                        f"before it already reach slot {run - 1}: {n} slot(s) "
                        f"claimed twice",
                        f"{where}/{c.index}/{p.index}"))
                run = max(run, w.end)
                # W3 — a window means "N units of *this* pack", so every slot it
                # covers must hold the same one.  Measured 0 violations in
                # 70300 vanilla windows.  This is a hard error rather than a
                # warning because the re-tiler *depends* on it: :meth:`Retiler.
                # relay` re-emits the pool from ``pool[start]``, so a mixed
                # window would be collapsed onto its first pack and the rest
                # silently deleted.
                covered = {e.pack_object_id for e in p.entries
                           if e.slot in w.slots()}
                if len(covered) > 1:
                    out.append(Issue(
                        ERROR, "mixed_window",
                        f"window at slot {w.pack_index} (x{w.count}) covers "
                        f"{len(covered)} different packs {sorted(covered)}; "
                        f"a window must hold one kind of equipment",
                        f"{where}/{c.index}/{p.index}"))

        n_hq = c.hq_platoons
        if n_hq != 1:
            out.append(Issue(
                WARN, "hq_count",
                f"{n_hq} HQ platoon(s); vanilla data varies, this is a warning",
                f"{where}/{c.index}"))

        limit = (group_rules or {}).get("MaxNbSmartGroupByCombatGroup")
        if limit and len(c.platoons) > limit:
            out.append(Issue(
                WARN, "too_many_platoons",
                f"{len(c.platoons)} platoons exceeds the declared max of {limit}",
                f"{where}/{c.index}"))

    orphans = [pid for pid, n in seen_platoons.items() if n != 1]
    for pid in orphans:
        out.append(Issue(
            ERROR, "orphan_platoon",
            f"platoon object {pid} is claimed {seen_platoons[pid]} time(s)",
            where))

    return out


def validate_file(deck: DeckFile, group_rules: dict | None = None,
                  limit_to: set[str] | None = None) -> ValidationResult:
    """Validate the whole file, or just the named exports."""
    issues: list[Issue] = []
    for bat in deck.battalions():
        if limit_to is not None and bat.export_name not in limit_to:
            continue
        issues.extend(validate_battalion(bat, group_rules))
    return ValidationResult(issues)


def validate_references(deck: DeckFile) -> ValidationResult:
    """Check every object reference in the decoded graph."""
    issues: list[Issue] = []
    total = len(deck.info["objects"])
    class_of = {o["id"]: o["class_id"] for o in deck.info["objects"]}

    def walk(node, path):
        if isinstance(node, list):
            for i, x in enumerate(node):
                walk(x, f"{path}[{i}]")
        elif isinstance(node, dict):
            if "object_id" in node and "class_id" in node:
                oid = node["object_id"]
                cid = node["class_id"]
                if oid == 0xFFFFFFFF:
                    return
                if oid >= total:
                    issues.append(Issue(ERROR, "ref_out_of_range",
                                        f"object_id {oid} >= {total}", path))
                elif class_of.get(oid) != cid:
                    issues.append(Issue(
                        ERROR, "ref_class_mismatch",
                        f"object {oid} is class {class_of.get(oid)}, "
                        f"reference says {cid}", path))
            for k, v in node.items():
                if k not in ("object_id", "class_id"):
                    walk(v, f"{path}.{k}")

    for o in deck.info["objects"]:
        for p in o["properties"]:
            walk(p["value"], f"{o['class']}#{o['id']}.{p['property_name']}")
    return ValidationResult(issues)


def load_group_rules(deck: DeckFile) -> dict:
    """The single ``TDeckGroupRules`` object, if the file carries one."""
    for o in deck.info["objects"]:
        if o["class"] != "TDeckGroupRules":
            continue
        out = {}
        for p in o["properties"]:
            if p["value"].get("value") is not None:
                out[p["property_name"]] = p["value"]["value"]
        return out
    return {}