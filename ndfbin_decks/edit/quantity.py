"""Change a window's unit count — the operation that must re-tile the deck.

Why this is the delicate one
----------------------------
``DeckPackList`` is not a pool of free slots; it is a **sequence of windows**.
Every ``(PackIndex, Number)`` pair in a smart group claims ``Number``
consecutive slots starting at ``PackIndex``, and the game allocates
``min(Number, len(DeckPackList) - PackIndex)`` units.  Anything past the end of
the pool is **silently dropped** — no error, no log entry.  The canonical
failure this module exists to prevent: a user sets "3 squads", the pool tail
only ever had room for 2, and the game quietly fields 2.

Measured invariants (all three mods, 6811 AG Pawn decks, 100% each)
--------------------------------------------------------------------
===  =======================================================  ============
W1   ``Σ Number == len(DeckPackList)``                        6811/6811
W2   windows never overlap                                   6811/6811
W3   all slots inside one window carry the same pack          6811/6811
W4   ``PackIndex`` equals the running offset when laid out in  6811/6811
     deck → combat group → smart group → entry order
===  =======================================================  ============

W4 is what makes the fix well-defined: a window's start index is **not stored
state**, it is a derived cumulative offset.  There is therefore exactly one
legal layout, and the editor's job is to regenerate it.  ``retile`` does that
for the whole deck at once, which is also the only correct scope — a window's
offset depends on every window before it, across combat-group boundaries
(measured: 2160 of 2208 Test decks have entries whose count affects a later
combat group).

Blast radius
------------
Changing one count shifts the ``PackIndex`` of every downstream window —
measured 18 windows on average, up to 88.  This is inherent, not a defect, and
is why the operation is reported as a diff rather than a silent fix.

Pack choice for the added / removed slots
-----------------------------------------
A window of ``N`` occupies ``N`` slots holding the **same** pack object ``N``
times (W3, zero exceptions).  Growing a window therefore appends another copy
of the pack it already points at; shrinking removes one.  No pack object is
created or destroyed, and because 97.2% of packs are shared across decks,
dropping a reference never orphans an object.

The re-tile is idempotent: running it on an untouched deck reproduces the
original bytes, so an edit to one deck never perturbs its neighbours.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.deckmodel import DeckFile
from ..i18n import tr

#: Official ``GroupRules`` caps, used as **advisories only**.  Unmodified
#: vanilla already exceeds them — measured: 7 smart groups in one company,
#: 4 packs in one smart group, 6 combat groups in one deck — so enforcing them
#: as hard limits would refuse legitimate edits to official data.
MAX_SMART_GROUPS_PER_COMBAT_GROUP = 6
MAX_COMBAT_GROUPS_PER_DECK = 5
MAX_PACKS_PER_SMART_GROUP = 3

WINDOW_CLASS = "TDeckSmartGroupDescriptor"
LIST_FIELD = "PackIndexUnitNumberList"


class RetileError(Exception):
    """A change was rejected because it would produce an illegal deck."""


#: Marker key used by the structural editor for a window that exists logically
#: but has no offset yet — the re-tile is what assigns one.
PENDING = "__pending__"


def _read_pair(item: dict) -> tuple[int, int]:
    """``(start, count)`` of a window item, tolerating a pending placeholder.

    A freshly inserted entry carries ``{PENDING: (count, pack_id)}`` instead of
    the ``(key, value)`` pair: it has no offset because laying out the deck is
    precisely what will give it one.  ``start`` comes back as ``-1``, which
    every caller must treat as "ask the pack list", never as a slot index.
    """
    if PENDING in item:
        count, pack_id = item[PENDING]
        return -1, count
    return item["key"]["value"], item["value"]["value"]


@dataclass
class Layout:
    """One window's recomputed position."""

    deck_object_id: int
    combat_group_index: int
    smart_group_index: int
    entry_index: int
    smart_group_object_id: int
    old_start: int
    old_count: int
    new_start: int = -1
    new_count: int = 0
    pack_object_id: int | None = None

    @property
    def moved(self) -> bool:
        return self.new_start != self.old_start or self.new_count != self.old_count

    def describe(self) -> str:
        return (f"cg{self.combat_group_index}/sg{self.smart_group_index}"
                f"/e{self.entry_index}: "
                f"[{self.old_start}..{self.old_start + self.old_count - 1}]"
                f"x{self.old_count} -> "
                f"[{self.new_start}..{self.new_start + self.new_count - 1}]"
                f"x{self.new_count}")


@dataclass
class RetileResult:
    """Outcome of one or more count changes on a single deck."""

    deck: str
    ok: bool = False
    layouts: list[Layout] = field(default_factory=list)
    pool_before: int = 0
    pool_after: int = 0
    note: str = ""
    #: non-blocking advisories, e.g. an official cap that vanilla itself breaks
    warnings: list[str] = field(default_factory=list)

    @property
    def moved_windows(self) -> list[Layout]:
        return [x for x in self.layouts if x.moved]

    @property
    def shift(self) -> int:
        return self.pool_after - self.pool_before

    def summary(self) -> str:
        if not self.ok:
            return f"{self.deck}: {self.note}"
        n = len(self.moved_windows)
        return (f"{self.deck}: pool {self.pool_before}->{self.pool_after} "
                f"({self.shift:+d}), {n} window(s) re-tiled")


class Retiler:
    """Recomputes a deck's whole slot layout under W1-W4.

    Usage mirrors :class:`~ndfbin_decks.edit.equip.DeckEditor`: nothing is
    written until the caller persists the result, so a user can preview the
    diff and back it out.
    """

    def __init__(self, deck: DeckFile):
        self.deck = deck
        self._by_id = {o["id"]: o for o in deck.info["objects"]}

    # -- reading -----------------------------------------------------------

    def _deck_name(self, deck_object_id: int) -> str:
        return self.deck.info["exports"].get(
            deck_object_id, f"deck#{deck_object_id}").rsplit("/", 1)[-1]

    def _prop(self, obj_id: int, name: str) -> dict | None:
        obj = self._by_id.get(obj_id)
        if obj is None:
            return None
        return next((p for p in obj["properties"]
                     if p["property_name"] == name), None)

    def pool(self, deck_object_id: int) -> list[int]:
        """``DeckPackList`` as a plain list of pack object ids."""
        pl = self._prop(deck_object_id, "DeckPackList")
        if pl is None:
            return []
        return [x.get("object_id") for x in pl["value"].get("items", [])]

    def _windows(self, deck_object_id: int) -> list[list[list[dict]]]:
        """``[[[pair, ...], ...], ...]`` — combat groups of smart group pairs."""
        cg_field = self._prop(deck_object_id, "DeckCombatGroupList")
        out: list[list[list[dict]]] = []
        for cg_ref in (cg_field["value"].get("items", []) if cg_field else []):
            cg_id = cg_ref.get("object_id")
            cgroup: list[list[dict]] = []
            sg_field = self._prop(cg_id, "SmartGroupList")
            for sg_ref in (sg_field["value"].get("items", []) if sg_field else []):
                sg_id = sg_ref.get("object_id")
                sg_field2 = self._prop(sg_id, LIST_FIELD)
                # The **live** list, not a copy: the structural editor inserts
                # and deletes straight into it, and ``relay`` reads the same
                # list back to assign real offsets.  Copying here silently
                # discarded every structural edit.
                cgroup.append(
                    sg_field2["value"]["items"] if sg_field2 else [])
            out.append(cgroup)
        return out

    # -- writing -----------------------------------------------------------

    def _set_pairs(self, smart_group_object_id: int, pairs: list[tuple[int, int]],
                   template: dict) -> None:
        """Write ``(start, count)`` pairs back, cloning ``template``'s shape."""
        items: list[dict] = []
        for start, count in pairs:
            item = {"type_id": template.get("type_id"),
                    "type": template.get("type"),
                    "reference_prefix": template.get("reference_prefix", True),
                    "key": {"type_id": template.get("key", {}).get("type_id"),
                            "type": template.get("key", {}).get("type"),
                            "reference_prefix": template.get("key", {}).get(
                                "reference_prefix"),
                            "value": start},
                    "value": {"type_id": template.get("value", {}).get("type_id"),
                              "type": template.get("value", {}).get("type"),
                              "reference_prefix": template.get("value", {}).get(
                                  "reference_prefix"),
                              "value": count}}
            items.append(item)
        field = self._prop(smart_group_object_id, LIST_FIELD)
        field["value"]["items"] = items
        field["value"]["length"] = len(items)

    def _set_pool(self, deck_object_id: int, pool: list[int],
                  template: dict | None) -> None:
        field = self._prop(deck_object_id, "DeckPackList")
        items = []
        for pid in pool:
            items.append({
                "type_id": template.get("type_id") if template else None,
                "type": template.get("type") if template else None,
                "reference_prefix": template.get("reference_prefix", True)
                if template else True,
                "object_id": pid,
                "class_id": template.get("class_id") if template else None,
            })
        field["value"]["items"] = items
        field["value"]["length"] = len(items)

    # -- the operation -----------------------------------------------------

    def set_count(self, deck_object_id: int, combat_group_index: int,
                  smart_group_index: int, entry_index: int,
                  new_count: int) -> RetileResult:
        """Set one window's unit count and re-tile the whole deck.

        ``new_count`` must be >= 1.  The window keeps the pack it already
        points at; the slot pool grows or shrinks by ``ΔN`` copies of it.
        """
        res = RetileResult(deck=self._deck_name(deck_object_id))
        if new_count < 1:
            res.note = f"count must be >= 1 (asked for {new_count})"
            return res

        groups = self._windows(deck_object_id)
        try:
            cgroup = groups[combat_group_index]
            pairs_list = cgroup[smart_group_index]
            pair = pairs_list[entry_index]
        except IndexError:
            res.note = (f"no such window: cg{combat_group_index}/"
                        f"sg{smart_group_index}/e{entry_index}")
            return res

        old_start, _old_count = _read_pair(pair)
        pool = self.pool(deck_object_id)
        res.pool_before = len(pool)
        pack_id = pool[old_start] if 0 <= old_start < len(pool) else None
        if pack_id is None:
            res.note = (f"window starts at slot {old_start}, outside the "
                        f"{len(pool)}-slot pool — cannot identify its pack")
            return res

        # Official caps are advisory only: vanilla itself ships decks with 7
        # smart groups in one company and 4 packs in one smart group, so a hard
        # limit would refuse legitimate edits.
        res.warnings.extend(self._limit_notes(groups))

        # -- commit: hand off to the shared whole-deck re-tile -------------
        res = self.relay(deck_object_id, edits={
            (combat_group_index, smart_group_index, entry_index): new_count})
        res.pool_before = len(pool)
        return res

    def relay(self, deck_object_id: int,
              edits: dict[tuple[int, int, int], int] | None = None,
              pack_edits: dict[tuple[int, int, int], int] | None = None
              ) -> RetileResult:
        """Re-lay an entire deck under W1-W4 and write it back.

        The single place where a deck's slot layout is produced.  Both
        :meth:`set_count`, the structural editor and the batch equipment
        replace route through here, so the layout rules exist once and cannot
        drift between operations.

        ``edits`` maps ``(combat_group, smart_group, entry)`` to a replacement
        count.  A window listed there must already be present in the graph with
        a resolvable pack; a window **absent** from ``edits`` keeps its
        current count.  Callers that inserted a placeholder pass the pending
        count through the same map.

        ``pack_edits`` maps the same key to a replacement **pack object id**.
        It is what makes a batch replace possible without a second layout
        engine: the caller names the windows whose equipment should change, and
        the single relayout below re-emits the pool from the substituted packs.
        Counts are never touched here, so ``Σ(Number) == len(DeckPackList)``
        still holds by construction — the whole reason a replace needs no
        re-validation dance of its own.
        """
        res = RetileResult(deck=self._deck_name(deck_object_id))
        edits = edits or {}
        pack_edits = pack_edits or {}
        groups = self._windows(deck_object_id)
        pool = self.pool(deck_object_id)
        res.pool_before = len(pool)

        # W3 guard.  A window is "N units of *this* pack"; this pass rebuilds
        # the pool from ``pool[start]`` alone, so a window whose slots disagree
        # would be collapsed onto its first pack and everything else deleted
        # without a word.  Refuse instead — losing two BM-27 Uragans to a
        # no-op edit is exactly the silent failure this editor exists to avoid.
        for ci, cgroup in enumerate(groups):
            for si, pairs_list2 in enumerate(cgroup):
                for ei, pair2 in enumerate(pairs_list2):
                    start, count = _read_pair(pair2)
                    if start < 0:
                        continue
                    ids = {pool[s] for s in range(start, start + count)
                           if 0 <= s < len(pool)}
                    if len(ids) > 1:
                        res.note = (
                            f"cg{ci}/sg{si}/e{ei} (slot {start}, x{count}) holds "
                            f"{len(ids)} different packs {sorted(ids)} — a window "
                            f"must hold one kind of equipment; fix this window "
                            f"before re-tiling, or the others would be deleted")
                        return res

        pos = 0
        new_pool: list[int] = []
        for ci, cgroup in enumerate(groups):
            for si, pairs_list2 in enumerate(cgroup):
                for ei, pair2 in enumerate(pairs_list2):
                    start, count = _read_pair(pair2)
                    # Capture what the file said *before* this pass applies an
                    # edit, otherwise the diff view would report every count
                    # change as "2 -> 2" and the edit would look like a no-op.
                    prior_count = count
                    if (ci, si, ei) in edits:
                        count = edits[(ci, si, ei)]
                    if start < 0:
                        # pending window: its pack rides along in the marker
                        pack_id = pair2[PENDING][1]
                    else:
                        pack_id = (pool[start]
                                   if 0 <= start < len(pool) else None)
                    if pack_id is None:
                        res.note = (f"cg{ci}/sg{si}/e{ei} points at slot "
                                    f"{start}, outside the {len(pool)}-slot pool")
                        return res
                    if (ci, si, ei) in pack_edits:
                        # A batch replace: same window, same count, different
                        # equipment.  Applied here so the pool below is emitted
                        # from the substituted pack and stays canonical.
                        pack_id = pack_edits[(ci, si, ei)]
                    res.layouts.append(Layout(
                        deck_object_id=deck_object_id,
                        combat_group_index=ci, smart_group_index=si,
                        entry_index=ei, smart_group_object_id=0,
                        old_start=start, old_count=prior_count,
                        new_start=pos, new_count=count,
                        pack_object_id=pack_id))
                    new_pool.extend([pack_id] * count)
                    pos += count

        res.warnings.extend(self._limit_notes(groups))

        self._set_pool(deck_object_id, new_pool,
                       self._pool_template(deck_object_id))
        for ci, cgroup in enumerate(groups):
            for si, pairs_list2 in enumerate(cgroup):
                pairs = [(x.new_start, x.new_count)
                         for x in res.layouts
                         if x.combat_group_index == ci
                         and x.smart_group_index == si]
                sg_id = self._smart_group_id(deck_object_id, ci, si)
                self._set_pairs(sg_id, pairs, self._window_template(sg_id))

        res.pool_after = len(new_pool)
        res.ok = True
        return res

    def heal_window(self, deck_object_id: int, ci: int, si: int, ei: int,
                    keep_pack: int) -> RetileResult:
        """Force a mixed window onto one pack, then re-lay the deck.

        The caller must name the surviving pack.  That is the whole point:
        ``relay`` collapses a window onto its first slot, and before the W3
        guard existed that silently turned ``[BM27, BM27, BM27]`` into
        ``[T72_CMD_SOV, BM27, BM27]`` and then deleted the two Uragans on the
        next edit.  A repair that guesses would repeat the same damage with a
        cleaner conscience, so this refuses to guess — ``keep_pack`` has to be
        one of the packs the window actually holds.
        """
        res = RetileResult(deck=self._deck_name(deck_object_id))
        pool = self.pool(deck_object_id)
        groups = self._windows(deck_object_id)
        try:
            pair = groups[ci][si][ei]
        except (IndexError, TypeError, KeyError):
            res.note = tr("条目 cg{ci}/sg{si}/e{ei} 不存在", ci=ci, si=si, ei=ei)
            return res
        start, count = _read_pair(pair)
        if start < 0 or start + count > len(pool):
            res.note = (tr("条目 cg{ci}/sg{si}/e{ei} 指向槽位 {start}+{count}，超出 {v5} 槽的池子",
                ci=ci,
                si=si,
                ei=ei,
                start=start,
                count=count,
                v5=len(pool),
            ))
            return res

        covered = {pool[s] for s in range(start, start + count)}
        if len(covered) <= 1:
            return self.relay(deck_object_id)
        if keep_pack not in covered:
            res.note = (tr("pack#{keep_pack} 不在这个窗口里 （窗口持有 {v1}，必须从中挑一个）",
                keep_pack=keep_pack,
                v1=sorted(covered),
            ))
            return res

        for s in range(start, start + count):
            pool[s] = keep_pack
        self._set_pool(deck_object_id, pool,
                       self._pool_template(deck_object_id))
        res = self.relay(deck_object_id)
        if res.ok:
            res.note = (tr("条目 cg{ci}/sg{si}/e{ei}（{count} 件）已统一为 pack#{keep_pack}",
                ci=ci,
                si=si,
                ei=ei,
                count=count,
                keep_pack=keep_pack,
            ))
        return res

    # -- helpers -----------------------------------------------------------

    def _window_template(self, smart_group_object_id: int) -> dict:
        """A representative item to copy the wire shape from.

        Skips a pending placeholder: it carries ``(count, pack_id)`` rather than
        a ``(key, value)`` pair and has no wire shape to clone.  A group made
        entirely of placeholders falls back to ``{}`` — the encoder supplies
        the defaults.
        """
        field = self._prop(smart_group_object_id, LIST_FIELD)
        items = field["value"].get("items", []) if field else []
        for item in items:
            if PENDING not in item:
                return item
        return {}

    def _pool_template(self, deck_object_id: int) -> dict | None:
        field = self._prop(deck_object_id, "DeckPackList")
        items = field["value"].get("items", []) if field else []
        return items[0] if items else None

    def _smart_group_id(self, deck_object_id: int, ci: int, si: int) -> int:
        cg_field = self._prop(deck_object_id, "DeckCombatGroupList")
        cg_id = cg_field["value"]["items"][ci]["object_id"]
        sg_field = self._prop(cg_id, "SmartGroupList")
        return sg_field["value"]["items"][si]["object_id"]

    def _limit_notes(self, groups) -> list[str]:
        """Advisories for decks that sit outside the official ``GroupRules`` caps.

        Never blocking.  Measured on unmodified files: Test ships a company
        with 7 smart groups, Berlin a smart group with 4 packs, Northern
        Thunder a deck with 6 combat groups — so ``MAX_NB_*`` describe the
        intended design, not an enforced constraint.
        """
        notes: list[str] = []
        if len(groups) > MAX_COMBAT_GROUPS_PER_DECK:
            notes.append(tr("牌组有 {v0} 个战斗群，超出官方建议值 {MAX_COMBAT_GROUPS_PER_DECK}",
                v0=len(groups),
                MAX_COMBAT_GROUPS_PER_DECK=MAX_COMBAT_GROUPS_PER_DECK,
            ))
        for ci, cgroup in enumerate(groups):
            if len(cgroup) > MAX_SMART_GROUPS_PER_COMBAT_GROUP:
                notes.append(tr("战斗群 {ci} 有 {v1} 个连排，超出官方建议值 {MAX_SMART_GROUPS_PER_COMBAT_GROUP}",
                    ci=ci,
                    v1=len(cgroup),
                    MAX_SMART_GROUPS_PER_COMBAT_GROUP=MAX_SMART_GROUPS_PER_COMBAT_GROUP,
                ))
            for si, pairs in enumerate(cgroup):
                if len(pairs) > MAX_PACKS_PER_SMART_GROUP:
                    notes.append(tr("战斗群 {ci}/连排 {si} 有 {v2} 条装备，超出官方建议值 {MAX_PACKS_PER_SMART_GROUP}",
                        ci=ci,
                        si=si,
                        v2=len(pairs),
                        MAX_PACKS_PER_SMART_GROUP=MAX_PACKS_PER_SMART_GROUP,
                    ))
        return notes


def retile(deck_file: DeckFile, deck_object_id: int, combat_group_index: int,
           smart_group_index: int, entry_index: int,
           new_count: int) -> RetileResult:
    """Convenience wrapper around :meth:`Retiler.set_count`."""
    return Retiler(deck_file).set_count(deck_object_id, combat_group_index,
                                        smart_group_index, entry_index,
                                        new_count)