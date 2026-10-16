"""Structural diff between two compiled ``Deck.ndfbin`` files.

Why this module exists
----------------------
A naive fingerprint over ``TDeckDescriptor`` properties reports **6** changed
decks for the ``Test`` mod.  The truth is **39**.  The reason: ``DeckPackList``
holds *object references* into a pack table, and a straight swap
(``T55A_TCH`` -> ``T72_TCH``) leaves the reference sequence — and often the list
length — completely unchanged.  Only expanding to the pack's ``UnitDescriptor``
reveals the edit.

Two more traps:

**Reference indices shift wholesale.**  ``Test`` has 58 fewer decks than Berlin,
so every ``obj_ref`` index moves.  Comparing raw indices produces **2213** false
differences.  :func:`canonical` therefore records reference *type* but never
its numeric target.

**One diff is not enough.**  ``DeckCombatGroupList`` points at combat groups
whose smart groups hold the ``(PackIndex, Number)`` intervals.  A deck whose pack
count changed almost always has retiled intervals too, and reporting only the
deck level makes it look like indices changed for no reason.

So the diff runs two passes: deck-level, then a descent through
``DeckCombatGroupList`` -> ``SmartGroup.PackIndexUnitNumberList``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .cndf_codec import decode

DECK = "TDeckDescriptor"
COMBAT_GROUP = "TDeckCombatGroupDescriptor"
SMART_GROUP = "TDeckSmartGroupDescriptor"
PACK = "TDeckPackDescriptor"

#: Properties that are pure references.  Their indices are positional, so they
#: are compared structurally (how many, and what they point at by class) rather
#: than numerically.
REFERENCE_FIELDS = ("DeckCombatGroupList", "DeckPackList")


def canonical(value) -> tuple:
    """Structure-aware canonical form of a decoded CNDF value.

    Reference values collapse to ``('ref', class_id)``: the index is an
    implementation detail that shifts whenever an unrelated object is added.
    """
    if not isinstance(value, dict):
        return ("lit", repr(value))
    t = value.get("type_id")
    if t == 17:                                   # list
        return ("list",) + tuple(canonical(x) for x in value.get("items", []))
    if t == 34:                                   # map
        return ("map",) + tuple((canonical(x["key"]), canonical(x["value"]))
                                for x in value.get("items", []))
    if t == 22:
        return ("map2", canonical(value["key"]), canonical(value["value"]))
    if "value_hex" in value:                      # loc_hash
        return ("hash", value["value_hex"])
    if "value" in value:
        val = value["value"]
        if isinstance(val, (bytes, bytearray)):
            return ("bytes", bytes(val).hex())
        return ("scalar", repr(val))
    if "object_id" in value:                      # obj_ref
        return ("ref", value.get("class_id"))
    if "index" in value:                          # strg_ref / trans_ref
        return ("strref", value.get("value"))
    return ("unknown", t)


def fingerprint(props: dict, skip: tuple[str, ...] = ()) -> tuple:
    """Canonical signature of a property bag, minus ``skip``ped reference fields.

    Sorted by field name so the result does not depend on the order the
    properties happened to be decoded in.
    """
    return tuple(sorted(
        (name, canonical(value))
        for name, value in props.items()
        if name not in skip))


def field_diff(before: dict, after: dict,
               skip: tuple[str, ...] = ()) -> list[str]:
    """Names of properties that differ between two property bags."""
    fb = fingerprint(before, skip)
    fa = fingerprint(after, skip)
    db, da = dict(fb), dict(fa)
    changed = set(db) ^ set(da)
    changed |= {k for k in set(db) & set(da) if db[k] != da[k]}
    return sorted(changed)


def _props(obj: dict) -> dict:
    return {p["property_name"]: p["value"] for p in obj["properties"]}


@dataclass
class UnitSlot:
    """One resolved ``DeckPackList`` entry."""

    index: int
    unit: str | None
    xp: object = None
    transport: str | None = None
    number: object = None

    def key(self) -> tuple:
        return (self.unit, self.number, self.transport,
                None if self.xp is None else str(self.xp))

    def label(self) -> str:
        u = (self.unit or "?").rsplit("/", 1)[-1].replace("Descriptor_Unit_", "")
        parts = [u]
        if self.number not in (None, 1):
            parts.append(f"x{self.number}")
        if self.transport:
            parts.append(f"[{(self.transport.rsplit('/',1)[-1])}]")
        return " ".join(parts)


@dataclass
class SmartGroupChange:
    name_hex: str | None
    is_hq: bool
    before: tuple[tuple[int, int], ...]
    after: tuple[tuple[int, int], ...]


@dataclass
class DeckDiff:
    name: str
    changed_props: list[str] = field(default_factory=list)
    added_units: list[UnitSlot] = field(default_factory=list)
    removed_units: list[UnitSlot] = field(default_factory=list)
    npacks_before: int = 0
    npacks_after: int = 0
    group_changes: list[SmartGroupChange] = field(default_factory=list)

    @property
    def pack_count_changed(self) -> bool:
        return self.npacks_before != self.npacks_after

    def describe(self) -> str:
        bits = [f"{self.name}"]
        if self.changed_props:
            bits.append(f"props={self.changed_props}")
        bits.append(f"packs {self.npacks_before}->{self.npacks_after}")
        if self.added_units:
            top = ", ".join(u.label() for u in self.added_units[:3])
            bits.append(f"+{len(self.added_units)} units ({top}"
                        f"{'…' if len(self.added_units) > 3 else ''})")
        if self.removed_units:
            top = ", ".join(u.label() for u in self.removed_units[:3])
            bits.append(f"-{len(self.removed_units)} units ({top}"
                        f"{'…' if len(self.removed_units) > 3 else ''})")
        if self.group_changes:
            bits.append(f"{len(self.group_changes)} smart groups retiled")
        return "  ".join(bits)


@dataclass
class DeckFileView:
    """Indexed view over a decoded ``Deck.ndfbin``."""

    decks: dict[str, dict] = field(default_factory=dict)
    packs: dict[int, dict] = field(default_factory=dict)
    groups: dict[int, dict] = field(default_factory=dict)
    smart: dict[int, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path) -> "DeckFileView":
        _doc, dec = decode(open(path, "rb").read(), str(path))
        v = cls()
        for o in dec["objects"]:
            c = o["class"]
            if c == PACK:
                v.packs[o["id"]] = _props(o)
            elif c == COMBAT_GROUP:
                v.groups[o["id"]] = _props(o)
            elif c == SMART_GROUP:
                v.smart[o["id"]] = _props(o)
            elif c == DECK and o["is_top_object"]:
                v.decks[dec["exports"].get(o["id"], "").rsplit("/", 1)[-1]] = _props(o)
        return v

    def slots(self, deck_props: dict) -> list[UnitSlot]:
        """``DeckPackList`` expanded to units — the comparison that matters."""
        out: list[UnitSlot] = []
        for i, ref in enumerate(deck_props.get("DeckPackList", {}).get("items", [])):
            pk = self.packs.get(ref.get("object_id"), {})
            out.append(UnitSlot(
                index=i,
                unit=pk.get("UnitDescriptor", {}).get("value"),
                xp=pk.get("ExperienceLevel", {}).get("value"),
                transport=pk.get("TransporterDescriptor", {}).get("value"),
                number=pk.get("Number", {}).get("value"),
            ))
        return out

    def intervals(self, deck_props: dict) -> list[SmartGroupChange]:
        """Every smart group reachable from the deck, with its interval list."""
        out: list[SmartGroupChange] = []
        for cref in deck_props.get("DeckCombatGroupList", {}).get("items", []):
            cg = self.groups.get(cref.get("object_id"), {})
            for sref in cg.get("SmartGroupList", {}).get("items", []):
                sg = self.smart.get(sref.get("object_id"), {})
                pil = sg.get("PackIndexUnitNumberList", {}).get("items", [])
                pairs = tuple(
                    (x.get("key", {}).get("value"), x.get("value", {}).get("value"))
                    for x in pil)
                out.append(SmartGroupChange(
                    name_hex=sg.get("Name", {}).get("value_hex"),
                    is_hq="IsHQ" in sg,
                    before=(), after=pairs))
        return out


def _intervals(v: DeckFileView, props: dict) -> dict[str, tuple]:
    """``{name_hex: intervals}`` — keyed by name so the two sides can be zipped."""
    out: dict[str, tuple] = {}
    for chg in v.intervals(props):
        out[chg.name_hex or f"<anon@{id(chg)}>"] = chg.after
    return out


def _net_units(a_slots, b_slots) -> tuple[list, list]:
    """Slots that genuinely appeared / disappeared, counted **net per unit**.

    The obvious one-liner — "a slot is removed if its unit's count dropped" —
    is wrong: swapping a 3-unit window out of 27 T-72Ms drops the count to 24,
    so all 27 before-slots match the test and get listed as removed.  That
    overstates the edit ninefold, and this line is what a modder reads before
    deciding to save.  Grouping by unit and taking the difference of the two
    counts reports exactly the three that went away.
    """
    groups_a: dict = {}
    for s in a_slots:
        groups_a.setdefault(s.key(), []).append(s)
    groups_b: dict = {}
    for s in b_slots:
        groups_b.setdefault(s.key(), []).append(s)

    added: list = []
    removed: list = []
    for key in set(groups_a) | set(groups_b):
        ga, gb = groups_a.get(key, []), groups_b.get(key, [])
        if len(ga) > len(gb):
            added.extend(ga[:len(ga) - len(gb)])
        elif len(gb) > len(ga):
            removed.extend(gb[:len(gb) - len(ga)])
    return added, removed


def diff(before: DeckFileView, after: DeckFileView) -> list[DeckDiff]:
    """Compare two deck files at both deck and smart-group level.

    A deck is reported when its expanded unit slots differ, which is strictly
    stronger than comparing property bags: it catches same-count equipment
    swaps that leave every index untouched.
    """
    out: list[DeckDiff] = []
    shared = sorted(set(before.decks) & set(after.decks))
    for name in shared:
        bp, ap = before.decks[name], after.decks[name]

        bs, as_ = before.slots(bp), after.slots(ap)
        cb, ca = Counter(s.key() for s in bs), Counter(s.key() for s in as_)
        if cb == ca:
            continue                      # identical equipment composition

        added, removed = _net_units(as_, bs)

        bi = _intervals(before, bp)
        ai = _intervals(after, ap)
        gchg = [SmartGroupChange(k, False, bi.get(k, ()), v)
                for k, v in ai.items() if bi.get(k, ()) != v]

        out.append(DeckDiff(
            name=name,
            changed_props=field_diff(bp, ap, REFERENCE_FIELDS),
            added_units=added, removed_units=removed,
            npacks_before=len(bs), npacks_after=len(as_),
            group_changes=gchg))
    return out


def diff_files(path_before, path_after) -> list[DeckDiff]:
    return diff(DeckFileView.load(path_before), DeckFileView.load(path_after))
