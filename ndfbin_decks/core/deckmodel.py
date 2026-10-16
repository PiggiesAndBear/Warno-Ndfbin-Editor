"""Deck data model for Army General Pawn decks.

Shape follows the previous implementation (``旧文件/deckedit/deck_core.py``) —
``DeckFile`` → ``Battalion`` → ``Company`` → ``Platoon`` → ``Entry`` — because
that structure was already proven against three mods.  What changed:

* :meth:`DeckFile.battalions` returns **AG Pawn decks**, not ``AGF_``-prefixed
  ones.  The old code filtered on a naming prefix, which misses every mod that
  edits vanilla decks in place (``Test`` adds nothing yet rewrites 39 of them).
* ``IsHQ`` is read as a **presence flag**.  Across 43,365 + 44,697 smart groups
  in two mods there is not one ``IsHQ = False``; the field simply appears or not.
* Platoon windows keep their ``(pack_index, count)`` pair instead of being
  flattened into a count.  The pair is what the game reads, and losing it is
  how silent truncation happens on edit.
* Names stay as ``loc_hash`` bytes.  A display string is only attached when a
  dictionary resolves it; otherwise ``None`` and the UI shows the hash.

Nothing here writes.  Editing lands in ``ndfbin_decks.edit``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import cndf_codec
from ..io import agfilter, localisation
from ..io.localisation import ANONYMOUS, UNKNOWN_TEMPLATE

DECK = "TDeckDescriptor"
COMBAT_GROUP = "TDeckCombatGroupDescriptor"
SMART_GROUP = "TDeckSmartGroupDescriptor"
PACK = "TDeckPackDescriptor"


def unit_leaf(path: str | None) -> str:
    """``$/GFX/Unit/Descriptor_Unit_M1A1_Abrams_US`` → ``M1A1_Abrams_US``."""
    if not path:
        return "?"
    return path.rsplit("/", 1)[-1].replace("Descriptor_Unit_", "")


@dataclass
class Entry:
    """One ``TDeckPackDescriptor`` as referenced from a platoon window."""

    #: absolute index into ``DeckPackList``
    slot: int
    unit: str | None
    count: int | None
    transporter: str | None = None
    experience: int | None = None
    pack_object_id: int = -1
    #: ``AGF_`` decks reference shared pack objects; a pack used by only one
    #: slot could be edited in place, otherwise a new object must be created.
    shared: bool = False

    def label(self) -> str:
        bits = [unit_leaf(self.unit)]
        if self.count not in (None, 1):
            bits.append(f"x{self.count}")
        if self.transporter:
            bits.append(f"[{unit_leaf(self.transporter)}]")
        if self.experience not in (None, 1):
            bits.append(f"xp{self.experience}")
        return " ".join(bits)

    def key(self) -> tuple:
        """Identity for comparing compositions (count is deliberately excluded)."""
        return (self.unit, self.transporter, self.experience)


@dataclass
class Window:
    """One ``(PackIndex, Number)`` pair from ``PackIndexUnitNumberList``.

    It occupies the **contiguous** slots ``[pack_index, pack_index + count)``.
    The next window in the same platoon normally starts where this one ends, and
    across a whole deck the windows tile ``DeckPackList`` with no gaps.
    """

    pack_index: int
    count: int

    @property
    def end(self) -> int:
        """One past the last occupied slot."""
        return self.pack_index + self.count

    def slots(self) -> range:
        return range(self.pack_index, self.end)

    def overlaps(self, other: "Window") -> bool:
        return self.pack_index < other.end and other.pack_index < self.end

    def __str__(self) -> str:
        return f"[{self.pack_index}..{self.end - 1}]x{self.count}"


@dataclass
class Platoon:
    """``TDeckSmartGroupDescriptor`` — the group that actually fields units."""

    index: int
    object_id: int = -1
    name_hash: bytes | None = None
    display: str = ANONYMOUS
    is_hq: bool = False
    windows: list[Window] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(w.count for w in self.windows)

    @property
    def kinds(self) -> int:
        return len({e.unit for e in self.entries})

    def window_labels(self) -> list[str]:
        return [str(w) for w in self.windows]


@dataclass
class Company:
    """``TDeckCombatGroupDescriptor`` — a group of platoons."""

    index: int
    object_id: int = -1
    name_hash: bytes | None = None
    display: str = ANONYMOUS
    is_hq: bool = False
    platoons: list[Platoon] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(p.total for p in self.platoons)

    @property
    def hq_platoons(self) -> int:
        return sum(1 for p in self.platoons if p.is_hq)


@dataclass
class Battalion:
    """One AG Pawn deck: division link plus the company / platoon tree."""

    deck_object_id: int
    export_name: str
    identifier: str = ""
    division: str | None = None
    companies: list[Company] = field(default_factory=list)
    #: ``len(DeckPackList)`` — how long the slot pool actually is.
    pool_slots: int = 0
    #: filled by provenance: Origin value or None when not classified
    origin: str | None = None

    # -- aggregate views ---------------------------------------------------
    @property
    def total(self) -> int:
        return sum(c.total for c in self.companies)

    @property
    def pack_count(self) -> int:
        """How many slots the platoons actually claim.

        Not the same as :attr:`pool_slots` once a deck is damaged: a slot no
        platoon refers to is in the pool and not here, and a slot two platoons
        both claim is here twice.  The two agreeing *is* the well-formed case
        (W4), which is why they are kept apart rather than conflated.
        """
        return sum(len(p.entries) for c in self.companies for p in c.platoons)

    @property
    def kinds(self) -> int:
        return len({e.unit for c in self.companies
                    for p in c.platoons for e in p.entries})

    @property
    def window_sum(self) -> int:
        """``Σ(Number)`` — must equal the pool size for a well-formed deck."""
        return sum(w.count for c in self.companies for p in c.platoons for w in p.windows)

    def walk(self):
        """``(company, platoon, entry)`` in assembly order."""
        for c in self.companies:
            for p in c.platoons:
                for e in p.entries:
                    yield c, p, e

    def signature(self) -> tuple:
        """Stable composition key: sorted ``(unit, transport, xp)`` multiset.

        Counts are excluded so a same-count swap shows up as a difference while
        an identical reordering does not.
        """
        return tuple(sorted(e.key() for _c, _p, e in self.walk()))

    def describe(self) -> str:
        return (f"{len(self.companies)} companies / "
                f"{sum(len(c.platoons) for c in self.companies)} platoons / "
                f"{sum(len(p.entries) for c in self.companies for p in c.platoons)} entries / "
                f"{self.total} units")


@dataclass
class Issue:
    """A validation finding."""

    level: str            # "error" | "warn"
    code: str
    message: str
    where: str = ""


class DeckFile:
    """A decoded ``Deck.ndfbin`` with the battalion tree on top."""

    def __init__(self, path: Path, lang: str = "SC"):
        self.path = Path(path)
        self.raw = self.path.read_bytes()
        self.doc, self.info = cndf_codec.decode(self.raw, str(self.path))
        self.lang = lang
        self.names = localisation.NameResolver(lang=lang)
        self._by_id: dict[int, dict] = {}
        self._packs: dict[int, dict] = {}
        self._battalions: list[Battalion] | None = None
        self._skirmish: int = 0
        self.invalidate()

    # -- caches ------------------------------------------------------------
    def invalidate(self) -> None:
        """Rebuild indexes so the next read sees the current object graph.

        Also drops the cached :class:`Battalion` tree, which is a *snapshot*:
        it copies ``PackIndex``/``Number`` out of the graph at build time, so
        after an edit a stale tree would report the pre-edit ``window_sum``
        next to the post-edit slot pool.  Every mutating path must call this.
        """
        self._by_id = {o["id"]: o for o in self.info["objects"]}
        self._packs = {i: self._props(o) for i, o in self._by_id.items()
                       if o["class"] == PACK}
        self._battalions = None

    def _props(self, obj: dict) -> dict:
        return {p["property_name"]: p["value"] for p in obj["properties"]}

    def _str(self, props: dict, field_name: str, default: str = "") -> str:
        v = props.get(field_name)
        if isinstance(v, dict):
            val = v.get("value")
            return default if val is None else str(val)
        return default

    def _int(self, props: dict, field_name: str, default=None):
        v = props.get(field_name)
        if isinstance(v, dict):
            val = v.get("value")
            return default if val is None else val
        return default

    def _label(self, props: dict) -> tuple[bytes | None, str]:
        h = localisation.name_hash(props)
        return h, (self.names.display(h) if h else ANONYMOUS)

    # -- scope -------------------------------------------------------------
    def deck_objects(self, *, ag_only: bool = True) -> list[dict]:
        """Deck objects, AG Pawn only by default.

        ``ag_only`` applies :func:`agfilter.is_ag_pawn_deck`, i.e. *no*
        ``DeckName`` *and* has ``DeckCombatGroupList``.  The second condition
        matters: 17 of the 128 skirmish decks do carry combat groups.
        """
        out = []
        for o in self.info["objects"]:
            if o["class"] != DECK:
                continue
            props = self._props(o)
            if ag_only and not agfilter.is_ag_pawn_deck(props):
                continue
            out.append(o)
        return out

    def skirmish_count(self) -> int:
        """How many decks this file filters out (128 in every sample)."""
        if self._skirmish:
            return self._skirmish
        n = 0
        for o in self.info["objects"]:
            if o["class"] != DECK:
                continue
            if not agfilter.is_ag_pawn_deck(self._props(o)):
                n += 1
        self._skirmish = n
        return n

    # -- model -------------------------------------------------------------
    def battalions(self) -> list[Battalion]:
        """Every AG Pawn deck, sorted by export name."""
        if self._battalions is not None:
            return self._battalions
        out = [self._build(o) for o in self.deck_objects()]
        out.sort(key=lambda b: b.export_name)
        self._battalions = out
        return out

    def _build(self, deck_obj: dict) -> Battalion:
        props = self._props(deck_obj)
        export = self.info["exports"].get(deck_obj["id"], "").rsplit("/", 1)[-1]

        # DeckPackList is the shared slot pool; window indices point into it.
        pool = [r.get("object_id") for r in
                (props.get("DeckPackList", {}).get("items", []) or [])]

        bat = Battalion(
            deck_object_id=deck_obj["id"],
            export_name=export,
            identifier=self._str(props, "DeckIdentifier"),
            division=self._str(props, "DeckDivision") or None,
            pool_slots=len(pool),
        )

        used_by: dict[int, int] = {}
        for pid in pool:
            if pid is not None:
                used_by[pid] = used_by.get(pid, 0) + 1

        for ci, cgref in enumerate(props.get("DeckCombatGroupList", {}).get("items", [])):
            cg = self._by_id.get(cgref.get("object_id"))
            if cg is None:
                continue
            cg_props = self._props(cg)
            h, label = self._label(cg_props)
            company = Company(index=ci, object_id=cg["id"], name_hash=h,
                              display=label,
                              is_hq=localisation.is_hq(cg_props))

            for pi, sgref in enumerate(
                    cg_props.get("SmartGroupList", {}).get("items", [])):
                sg = self._by_id.get(sgref.get("object_id"))
                if sg is None:
                    continue
                sg_props = self._props(sg)
                sh, slabel = self._label(sg_props)
                platoon = Platoon(index=pi, object_id=sg["id"], name_hash=sh,
                                 display=slabel,
                                 is_hq=localisation.is_hq(sg_props))

                for tup in sg_props.get("PackIndexUnitNumberList", {}).get("items", []):
                    idx = tup.get("key", {}).get("value")
                    count = tup.get("value", {}).get("value")
                    if not isinstance(idx, int) or not isinstance(count, int):
                        continue
                    platoon.windows.append(Window(idx, count))
                    for slot in range(idx, idx + count):
                        if not (0 <= slot < len(pool)):
                            continue          # out of range: flagged by validate()
                        pid = pool[slot]
                        if pid is None:
                            continue
                        platoon.entries.append(self._entry(pid, slot, used_by))
                company.platoons.append(platoon)
            bat.companies.append(company)
        return bat

    def _entry(self, pack_id: int, slot: int, used_by: dict[int, int]) -> Entry:
        pp = self._packs.get(pack_id, {})
        return Entry(
            slot=slot,
            unit=pp.get("UnitDescriptor", {}).get("value"),
            count=pp.get("Number", {}).get("value"),
            transporter=pp.get("TransporterDescriptor", {}).get("value"),
            experience=pp.get("ExperienceLevel", {}).get("value"),
            pack_object_id=pack_id,
            shared=used_by.get(pack_id, 0) > 1,
        )

    def find(self, needle: str) -> Battalion | None:
        """Look a battalion up by export name, identifier, or unique prefix."""
        if not needle:
            return None
        for b in self.battalions():
            if needle in (b.export_name, b.identifier):
                return b
        hits = [b for b in self.battalions() if b.export_name.startswith(needle)]
        return hits[0] if len(hits) == 1 else None

    # -- inventory ---------------------------------------------------------
    def unit_pool(self) -> list[str]:
        """Distinct unit paths available for editing, sorted."""
        return sorted({e.unit for b in self.battalions() for _c, _p, e in b.walk()
                       if e.unit})

    def stats(self) -> dict:
        bats = self.battalions()
        return {
            "decks_total": len([o for o in self.info["objects"]
                                if o["class"] == DECK]),
            "ag_pawn": len(bats),
            "skirmish": self.skirmish_count(),
            "companies": sum(len(b.companies) for b in bats),
            "platoons": sum(len(c.platoons) for b in bats for c in b.companies),
            "entries": sum(len(p.entries) for b in bats for c in b.companies
                           for p in c.platoons),
            "distinct_units": len(self.unit_pool()),
        }

    def readme(self) -> str:
        s = self.stats()
        return (f"{self.path.name}: {s['ag_pawn']} AG pawn decks "
                f"(+{s['skirmish']} skirmish filtered), "
                f"{s['companies']} companies, {s['platoons']} platoons, "
                f"{s['entries']} entries, {s['distinct_units']} distinct units")