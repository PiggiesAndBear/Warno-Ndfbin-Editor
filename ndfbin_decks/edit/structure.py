"""Add or remove a window inside a smart group.

This is the structural edit: it changes the **shape** of a smart group rather
than a number inside it.  The slot bookkeeping is not re-derived here — it is
delegated to :class:`~ndfbin_decks.edit.quantity.Retiler`, which already
re-lays a whole deck under W1-W4.

Why delegation is not just convenient
-------------------------------------
Measured: deleting a window shrinks ``Σ(Number)`` by exactly that window's
count, and the W4 re-tile then slides every downstream ``PackIndex`` forward
by the right amount.  So there is no separate "shift the indices" code path
that could disagree with the layout rules — one implementation, one truth.

Two user decisions shape the behaviour
--------------------------------------
**Never leave an empty smart group.**  Measured: 51.4% of smart groups hold
exactly one window, and the shipped data contains *zero* groups with none.
Removing the last window is therefore refused — the user must first add a
window.  Silently producing a group the game never emits is how you get a
platoon that fields nothing.

**Never garbage-collect packs.**  Removing a window drops one slot reference.
The pack object usually stays alive anyway — measured 97.2% of packs are
shared across decks — and the file already ships 2488 packs referenced by no
deck at all (Test), which the game tolerates.  Sweeping them would need a
whole-file scan and could delete objects an author parked for later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.deckmodel import DeckFile
from .equip import DeckEditor, PackKey
from .quantity import PENDING, Layout, Retiler, RetileResult
from ..i18n import tr

#: Advisory only — vanilla exceeds it (measured: 5 entries in one smart group).
MAX_PACKS_PER_SMART_GROUP = 3

SMART_GROUP_LIST = "SmartGroupList"
WINDOW_FIELD = "PackIndexUnitNumberList"


class StructureError(Exception):
    """The requested structural change is not legal."""


@dataclass
class EntryResult:
    """Outcome of adding or removing one window."""

    deck: str
    ok: bool = False
    action: str = ""                 # "insert" | "remove"
    note: str = ""
    pack_object_id: int | None = None
    pack_reused: bool = False
    retile: RetileResult | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def moved_windows(self) -> list[Layout]:
        return self.retile.moved_windows if self.retile else []

    def summary(self) -> str:
        if not self.ok:
            return f"{self.deck}: {self.note}"
        tail = f" | {self.retile.summary()}" if self.retile else ""
        return f"{self.deck}: {self.action} window -> pack#{self.pack_object_id}{tail}"


class StructureEditor:
    """Adds / removes windows, reusing the T4.2 layout engine."""

    def __init__(self, deck: DeckFile, packs: DeckEditor | None = None):
        self.deck = deck
        self.packs = packs if packs is not None else DeckEditor(deck)
        self.retiler = Retiler(deck)

    # -- internals ---------------------------------------------------------

    def _deck_name(self, deck_object_id: int) -> str:
        return self.deck.info["exports"].get(
            deck_object_id, f"deck#{deck_object_id}").rsplit("/", 1)[-1]

    def _relay(self, res: EntryResult, deck_object_id: int) -> EntryResult:
        """Re-lay the deck after a structural change and record the result.

        ``relay`` re-reads the deck's own structures, assigns every window a
        real offset and writes the lists back, so the structural edit only has
        to mutate the in-memory item list — no partial write of its own.
        """
        tile = self.retiler.relay(deck_object_id)
        res.retile = tile
        res.warnings.extend(tile.warnings)
        if tile.ok:
            res.ok = True
        else:
            res.ok = False
            res.note = tile.note
        return res

    # -- the two operations -----------------------------------------------

    def insert_window(self, deck_object_id: int, ci: int, si: int,
                      *, at: int | None = None, count: int = 1,
                      key: PackKey | None = None,
                      reuse_pack: int | None = None) -> EntryResult:
        """Add one window to smart group ``ci/si``.

        ``reuse_pack`` points at an existing pack object; ``key`` resolves
        through the content index, appending a pack when nothing matches
        (same pipeline the entry-level ``swap_entry`` uses).  Supply exactly one.
        """
        res = EntryResult(deck=self._deck_name(deck_object_id),
                          action="insert")
        if count < 1:
            res.note = f"count must be >= 1 (asked for {count})"
            return res
        if (key is None) == (reuse_pack is None):
            res.note = "give exactly one of key= or reuse_pack="
            return res

        groups = self.retiler._windows(deck_object_id)
        try:
            pairs_list = groups[ci][si]
        except IndexError:
            res.note = f"no such smart group: cg{ci}/sg{si}"
            return res
        if len(pairs_list) >= MAX_PACKS_PER_SMART_GROUP:
            res.warnings.append(
                tr("战斗群 {ci}/连排 {si} 将有 {v2} 条装备，超出官方建议值 {MAX_PACKS_PER_SMART_GROUP}",
                    ci=ci,
                    si=si,
                    v2=len(pairs_list) + 1,
                    MAX_PACKS_PER_SMART_GROUP=MAX_PACKS_PER_SMART_GROUP,
                ))

        if reuse_pack is not None:
            if reuse_pack not in self.deck._packs:
                res.note = f"pack#{reuse_pack} does not exist"
                return res
            pack_id, res.pack_reused = reuse_pack, True
        else:
            pack_id, res.pack_reused = self.packs.ensure_pack(key)
            if pack_id < 0:
                res.note = f"failed to create a pack for {key.describe()}"
                return res
        res.pack_object_id = pack_id

        pos = 0 if at is None else max(0, min(at, len(pairs_list)))
        # The new window's pair is a placeholder; the re-tile assigns the real
        # offsets right after, so any in-range value works here.
        pairs_list.insert(pos, {PENDING: (count, pack_id)})
        return self._relay(res, deck_object_id)

    def remove_window(self, deck_object_id: int, ci: int, si: int,
                      ei: int) -> EntryResult:
        """Remove window ``ei`` from smart group ``ci/si``.

        Refused when it is the group's only window — see the module docstring.
        """
        res = EntryResult(deck=self._deck_name(deck_object_id),
                          action="remove")
        groups = self.retiler._windows(deck_object_id)
        try:
            pairs_list = groups[ci][si]
        except IndexError:
            res.note = f"no such smart group: cg{ci}/sg{si}"
            return res
        if not (0 <= ei < len(pairs_list)):
            res.note = f"no such window: cg{ci}/sg{si}/e{ei}"
            return res
        if len(pairs_list) == 1:
            res.note = (tr("战斗群 {ci}/连排 {si} 只剩这1 条装备，删掉就成了空连排组（官方数据 0 例这种形态）。请先增加一条再删。",
                ci=ci,
                si=si,
            ))
            return res

        pool = self.retiler.pool(deck_object_id)
        start = pairs_list[ei]["key"]["value"]
        res.pack_object_id = pool[start] if 0 <= start < len(pool) else None
        del pairs_list[ei]
        return self._relay(res, deck_object_id)
