"""Batch equipment replace over a chosen slice of the organisation.

A single swap re-points one slot.  A *replace* answers a different question —
"this division fields 40 T-72M1 across six battalions; make them all T-72B" —
and it needs the blast radius up front, because the answer is a rewrite of
dozens of decks at once.

**The scope is an explicit choice, never an accident.**  Widening from one
platoon to a whole division is the entire point of the feature, so the caller
must name the level; there is no "apply to everything" fallback that a slip of
the mouse could reach.

**The operation is deliberately conservative.**  It only changes *which pack a
window points at* — never the count, never the window order, never a company's
membership.  Because the relayout is re-emitted from the substituted packs,
``Σ(Number) == len(DeckPackList)`` still holds by construction, so a replace
cannot make a deck non-canonical and needs no re-tiling or re-validation dance
of its own.  That is what makes it safe to run across a whole division.

**Several sources may fold into one target.**  "Make every T-72M, T-72M1 and
T-72B in this division a T-72B" is one question, not three, and answering it in
three passes would re-tile the same battalions three times.  The set of sources
is therefore the input; :meth:`ScopedReplace.apply` groups the edits per deck
exactly as it already did, so widening from one source to many changes one
predicate and no other line of the write path.

**The target is never one of its own sources.**  Selecting three sources and
then picking one of them as the target means "fold the *others* into this one",
so the target is dropped from the set and the exclusion is reported rather than
silently turning the whole request into a no-op.

Scope levels mirror the organisation the UI shows:

===========  =============================================
``platoon``  one platoon (排) of one company of one battalion
``company``  every platoon of one company (连)
``battalion`` every company of one battalion (营)
``division`` every battalion of one division (师)
``country``  every battalion of every division of one 国家
===========  =============================================

The country level is derived, not stored: no deck in the file records a
country, so :func:`~ndfbin_decks.io.divisions.country_code` reads it off the
division label.  That is enough for a replace, which only ever asks "is this
window inside my slice".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..core.deckmodel import DeckFile
from ..edit.equip import DeckEditor, PackKey
from ..edit.quantity import Retiler
from ..io.divisions import country_code
from ..i18n import live, tr

#: Widest first — the UI shows them in this order so the user reads the scope
#: as a widening, not a list of unrelated buttons.
LEVELS = ("platoon", "company", "battalion", "division", "country")

LEVEL_LABELS = live(globals(), "LEVEL_LABELS", lambda: {
    "platoon": tr("本排 Pl"),
    "company": tr("本连 Co"),
    "battalion": tr("本营 Bn"),
    "division": tr("本师 Div"),
    "country": tr("本国 Nat"),
})


@dataclass(frozen=True)
class Scope:
    """A slice of the organisation, named by level."""

    kind: str
    #: ``$/GFX/Division/...`` when ``kind == "division"``
    division: str | None = None
    #: country code when ``kind == "country"``, and also carried on the narrower
    #: levels so the UI can widen a selection to the country it sits in
    country: str | None = None
    deck_id: int | None = None
    ci: int | None = None
    si: int | None = None

    def contains(self, division: str | None, deck_id: int,
                 ci: int, si: int) -> bool:
        if self.kind == "country":
            # Derived from the label, exactly as the picker derived it, so the
            # two can never disagree about which divisions are Soviet.
            return bool(self.country) and country_code(division) == self.country
        if self.kind == "division":
            return division == self.division
        if deck_id != self.deck_id:
            return False
        if self.kind == "battalion":
            return True
        if ci != self.ci:
            return False
        if self.kind == "company":
            return True
        return si == self.si

    def label(self) -> str:
        return LEVEL_LABELS.get(self.kind, self.kind)


@dataclass
class ReplaceSurvey:
    """The blast radius, computed before anything is written."""

    decks: set[int] = field(default_factory=set)
    companies: int = 0
    platoons: int = 0
    entries: int = 0
    #: sum of the counts of the affected windows — how many in-game units move
    units: int = 0
    #: how many distinct source packs were actually hit
    sources: int = 0

    def summary(self) -> str:
        if not self.entries:
            return tr("范围内没有用到该装备")
        head = (tr("{sources} 种装备", sources=self.sources) if self.sources > 1 else "")
        return ((head + " · " if head else "")
                + tr("{v0} 个营 · {companies} 个连 · {platoons} 个排 · {entries} 条装备 · {units} 件单位",
                    v0=len(self.decks),
                    companies=self.companies,
                    platoons=self.platoons,
                    entries=self.entries,
                    units=self.units,
                ))


@dataclass
class ReplaceResult:
    ok: bool = False
    note: str = ""
    survey: ReplaceSurvey = field(default_factory=ReplaceSurvey)
    #: pack object id every affected window now points at
    to_pack_id: int | None = None
    #: ``True`` when an existing pack was reused rather than created
    reused: bool = True
    created_pack_id: int | None = None
    decks: list[str] = field(default_factory=list)
    #: the target was also selected as a source, so it was dropped from the set
    target_also_a_source: bool = False
    #: how many source packs actually moved something
    sources: int = 0

    def summary(self) -> str:
        if not self.ok:
            return self.note or tr("未执行")
        bits = [tr("{entries} 条装备 → pack#{to_pack_id}",
            entries=self.survey.entries,
            to_pack_id=self.to_pack_id,
        )]
        if self.sources > 1:
            bits.insert(0, tr("{sources} 种来源", sources=self.sources))
        bits.append(tr("复用既有装备包") if self.reused
                    else tr("新建 pack#{created_pack_id}", created_pack_id=self.created_pack_id))
        bits.append(tr("（{v0} 个营）", v0=len(self.decks)))
        if self.target_also_a_source:
            bits.append(tr("目标本身也是来源，已从来源中剔除"))
        return "，".join(bits)


class ScopedReplace:
    """Swap one pack for another across a :class:`Scope`.

    Reads the live graph on every call, so a survey taken before an unrelated
    edit and an apply after it cannot disagree.
    """

    def __init__(self, deck: DeckFile, packs: DeckEditor, retiler: Retiler,
                 scope: Scope, from_packs: int | Iterable[int] = -1):
        self.deck = deck
        self.packs = packs
        self.retiler = retiler
        self.scope = scope
        # Normalised once, here, so nothing downstream has to ask whether it
        # was handed one id or several.  A bare ``-1`` means "no filter", which
        # is how the picker asks for the list of everything the scope fields.
        self.from_pack_ids: frozenset[int] = (
            frozenset({from_packs}) if isinstance(from_packs, int)
            else frozenset(from_packs))

    @property
    def from_pack_id(self) -> int | None:
        """The single source, when there is exactly one.

        Kept for the callers that replace one pack; ``None`` when this executor
        was built for several, so a caller cannot quietly take the first one
        and believe it replaced the lot.
        """
        if len(self.from_pack_ids) == 1:
            return next(iter(self.from_pack_ids))
        return None

    # -- reading -----------------------------------------------------------

    def _division_of(self, deck_id: int) -> str | None:
        obj = self.deck._by_id.get(deck_id)
        if obj is None:
            return None
        props = {p["property_name"]: p["value"] for p in obj["properties"]}
        return self.deck._str(props, "DeckDivision") or None

    def _windows(self):
        """``(division, deck_id, ci, si, ei, pack_id, count)`` for every AG window.

        Skips skirmish decks: a replace is an Army General operation, and the
        PvP decks share the file but never a pack with AG.
        """
        from ..io import agfilter
        for o in self.deck.info["objects"]:
            if o["class"] != "TDeckDescriptor":
                continue
            props = {p["property_name"]: p["value"] for p in o["properties"]}
            if not agfilter.is_ag_pawn_deck(props):
                continue
            did = o["id"]
            division = self.deck._str(props, "DeckDivision") or None
            pool = [it.get("object_id") for it in
                    props.get("DeckPackList", {}).get("items", []) or []]
            for ci, cgref in enumerate(
                    props.get("DeckCombatGroupList", {}).get("items", []) or []):
                cg = self.deck._by_id.get(cgref.get("object_id"))
                if cg is None:
                    continue
                cg_props = self.deck._props(cg)
                for si, sgref in enumerate(
                        cg_props.get("SmartGroupList", {}).get("items", []) or []):
                    sg = self.deck._by_id.get(sgref.get("object_id"))
                    if sg is None:
                        continue
                    sg_props = self.deck._props(sg)
                    for ei, tup in enumerate(
                            sg_props.get("PackIndexUnitNumberList", {})
                            .get("items", []) or []):
                        idx = tup.get("key", {}).get("value")
                        count = tup.get("value", {}).get("value") or 0
                        if idx is None or not (0 <= idx < len(pool)):
                            continue
                        yield (division, did, ci, si, ei, pool[idx], count)

    def _in_scope(self, sources: frozenset[int] | None = None):
        """``(deck_id, ci, si, ei, pack_id, count)`` for windows in scope.

        ``sources`` overrides which packs count as a source — :meth:`apply`
        passes the set with the target removed, so a survey taken after the
        target is known describes exactly what will move.
        """
        wanted = self.from_pack_ids if sources is None else sources
        if not wanted:
            return
        for division, did, ci, si, ei, pack_id, count in self._windows():
            if not self.scope.contains(division, did, ci, si):
                continue
            if pack_id not in wanted:
                continue
            yield did, ci, si, ei, pack_id, count

    # -- planning ----------------------------------------------------------

    def usage(self, limit: int = 400) -> list[tuple[int, int, int]]:
        """``(pack_id, entries, units)`` for every pack the scope fields.

        This is the "which equipment do you want to replace" list.  Sorted by
        how much the scope uses it, because the pack a modder is hunting for is
        almost always one of the heavily used ones.
        """
        tally: dict[int, list[int]] = {}
        for did, ci, si, _ei, pid, count in self._in_scope_all():
            row = tally.setdefault(pid, [0, 0])
            row[0] += 1
            row[1] += count
        rows = [(pid, e, u) for pid, (e, u) in tally.items()]
        rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
        return rows[:limit]

    def _in_scope_all(self):
        """Every in-scope window, regardless of which pack it uses."""
        for division, did, ci, si, ei, pack_id, count in self._windows():
            if self.scope.contains(division, did, ci, si):
                yield did, ci, si, ei, pack_id, count

    def survey(self, sources: Iterable[int] | None = None) -> ReplaceSurvey:
        """What an apply would touch.  Cheap enough to call on every keystroke."""
        wanted = (self.from_pack_ids if sources is None
                  else frozenset(sources))
        out = ReplaceSurvey()
        companies: set[tuple] = set()
        platoons: set[tuple] = set()
        hit: set[int] = set()
        for did, ci, si, _ei, pid, count in self._in_scope(wanted):
            out.decks.add(did)
            companies.add((did, ci))
            platoons.add((did, ci, si))
            hit.add(pid)
            out.entries += 1
            out.units += count
        out.companies = len(companies)
        out.platoons = len(platoons)
        out.sources = len(hit)
        return out

    # -- writing -----------------------------------------------------------

    def apply(self, to_key: PackKey) -> ReplaceResult:
        """Repoint every in-scope window at a pack offering ``to_key``."""
        res = ReplaceResult()
        res.survey = self.survey()
        if not res.survey.entries:
            res.note = tr("范围内没有用到该装备，无需替换")
            return res

        to_pack_id, reused = self.packs.ensure_pack(to_key)
        if to_pack_id < 0:
            res.note = tr("无法创建目标装备包")
            return res
        res.to_pack_id, res.reused = to_pack_id, reused
        if not reused:
            res.created_pack_id = to_pack_id

        # "Fold the others into this one" is the only reading of a target that
        # is also a source that is not a no-op, so drop it and say so.
        #
        # Compared by *content*, not by object id: a file can carry two pack
        # objects with the same signature, and ``ensure_pack`` resolves the key
        # to whichever it likes — quite possibly not the one the picker showed.
        # Asking "is this id the target?" would then miss the case and quietly
        # rewrite a pack into itself; asking "does this one offer the target?"
        # is what the user actually meant, and is id-independent.
        sources = {p for p in self.from_pack_ids
                   if self.packs.pack_key(p) != to_key}
        res.target_also_a_source = len(sources) != len(self.from_pack_ids)
        if not sources:
            res.note = tr("目标与来源相同")
            return res

        # Re-measured over what will actually move, so the result never
        # reports the target's own windows as if they had been rewritten.
        res.survey = self.survey(sources)
        if not res.survey.entries:
            res.note = tr("范围内没有用到该装备，无需替换")
            return res
        res.sources = res.survey.sources

        # Grouped per deck: the relayout is a whole-deck operation, so one
        # relay per affected battalion is both correct and the minimum work.
        # Several sources collapse into the same edit map — that is the whole
        # reason many-to-one costs no more than one-to-one.
        per_deck: dict[int, dict[tuple[int, int, int], int]] = {}
        for did, ci, si, ei, _pid, _count in self._in_scope(frozenset(sources)):
            per_deck.setdefault(did, {})[(ci, si, ei)] = to_pack_id

        for did, edits in per_deck.items():
            out = self.retiler.relay(did, pack_edits=edits)
            if not out.ok:
                res.note = f"{self.retiler._deck_name(did)}: {out.note}"
                return res
            res.decks.append(self.retiler._deck_name(did))

        # The relayout changed pools and pair lists; every derived view (the
        # battalion tree, the catalogue's usage counts) must be rebuilt.
        self.deck.invalidate()
        res.ok = True
        return res
