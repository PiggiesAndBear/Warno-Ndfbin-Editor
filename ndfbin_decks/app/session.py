"""Editor session — the object every GUI panel talks to.

Holds one loaded mod plus the three editors that mutate it, so panels can read
a consistent view and any change is visible everywhere at once.

The three editors deliberately share **one** decoded graph (see
:class:`~ndfbin_decks.edit.equip.DeckEditor`): a private copy per editor would
let a later save silently overwrite an earlier one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..core import cndf_codec
from ..core.deckmodel import DeckFile
from ..core import validator
from ..edit.bulk import ScopedReplace
from ..edit.equip import DeckEditor, PackKey
from ..edit.quantity import RetileResult, Retiler
from ..edit.structure import StructureEditor
from ..io import agfilter, divisions, localisation, modlocator, unit_universe
from .catalog import PackCatalogue, ag_pack_ids
from ..i18n import tr


@dataclass
class Division:
    """One 师: a ``DeckDivision`` and the battalions (营) that belong to it."""

    path: str
    label: str
    battalions: list = field(default_factory=list)
    #: country code parsed off the label — see :mod:`ndfbin_decks.io.divisions`
    country: str = ""

    def __len__(self) -> int:
        return len(self.battalions)


@dataclass
class Country:
    """One 国家: the divisions whose label starts with the same code.

    Not a game concept — the file has no country table for decks — but it is
    how every WARNO player and modder groups a roster, and it is what makes
    "replace this equipment across the whole Soviet contingent" expressible.
    """

    code: str
    divisions: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return divisions.country_name(self.code)

    @property
    def battalions(self) -> list:
        return [b for d in self.divisions for b in d.battalions]

    def label(self) -> str:
        return divisions.country_label(self.code, len(self.divisions),
                                       len(self.battalions))

    def __len__(self) -> int:
        return len(self.divisions)


@dataclass
class Session:
    """A loaded ``Deck.ndfbin`` and the editors bound to it."""

    path: Path
    deck: DeckFile
    retiler: Retiler
    packs: DeckEditor
    structure: StructureEditor
    #: packs this file already contains — the *only* thing the picker may offer
    _catalogue: PackCatalogue | None = None
    _catalogue_stale: bool = False
    #: AG-referenced pack ids at load time; keeps options alive after a batch
    #: replace empties them out of every deck
    _ag_baseline: set[int] = field(default_factory=set)
    #: the mod this file belongs to; ``None`` for a loose copy
    _mod: object = None
    #: every unit this mod can field — built lazily, see :meth:`get_units`
    _units: object = None
    resolver: localisation.NameResolver | None = None
    dirty: bool = False
    #: deck object id -> human label for the change log
    log: list[str] = field(default_factory=list)

    # -- construction -----------------------------------------------------

    @classmethod
    def load(cls, path: Path, *, lang: str = "SC",
             resolve_names: bool = True,
             mod=None) -> "Session":
        path = Path(path)
        deck = DeckFile(path, lang=lang)
        packs = DeckEditor(deck)
        if mod is None:
            # A loose copy (a temp file in a test, a file copied off the deploy
            # folder) has no owning mod; both the name resolver and the unit
            # universe degrade to "answer from the file alone".
            mod = _owning_mod(path)
        resolver = None
        if resolve_names:
            resolver = _build_resolver(path, lang, mod=mod)
        # Captured before any edit: once a pack has been fielded by an AG deck
        # in this session it stays selectable, even if a later batch replace
        # empties it out of every deck.
        baseline = ag_pack_ids(deck)
        return cls(
            path=path,
            deck=deck,
            retiler=Retiler(deck),
            packs=packs,
            structure=StructureEditor(deck, packs),
            _ag_baseline=baseline,
            _catalogue=PackCatalogue.build(deck, baseline=baseline),
            _mod=mod,
            resolver=resolver,
        )

    def get_units(self) -> unit_universe.UnitUniverse:
        """Every unit this mod can field, from its definition files.

        Lazy because one source is a 27 MB text file inside ``base.zip`` and
        another is a 650 KB ``Unit.ndfbin`` that costs 2 s to decode — neither
        belongs in the path of "open a mod and look at its tree".
        """
        if self._units is None:
            # ``referenced`` is the fourth source: a mod with neither a project
            # tree nor a ``Unit.ndfbin`` still owns everything its own file
            # already points at.
            referenced = {p.get("UnitDescriptor", {}).get("value")
                          for p in self.deck._packs.values()}
            referenced |= {p.get("TransporterDescriptor", {}).get("value")
                           for p in self.deck._packs.values()}
            self._units = unit_universe.build(
                self._mod, referenced={r for r in referenced if r})
        return self._units


    def refresh_catalogue(self) -> None:
        """Re-index packs; cheap enough to call after any edit."""
        self._catalogue = PackCatalogue.build(self.deck,
                                              baseline=self._ag_baseline)
        self._catalogue_stale = False

    def get_catalogue(self) -> PackCatalogue:
        """Packs this file contains, rebuilt only after an edit.

        Lazy because a swap usually does **not** add a pack (it repoints at one
        that already exists), and re-indexing 5000+ packs per keystroke would
        be wasted work.
        """
        if self._catalogue is None or self._catalogue_stale:
            self.refresh_catalogue()
        return self._catalogue

    # -- read-only views --------------------------------------------------

    def battalions(self):
        return self.deck.battalions()

    def battalion(self, needle: str):
        return self.deck.find(needle)

    def divisions(self) -> list[Division]:
        """AG battalions grouped into divisions (师), biggest first.

        ``DeckDivision`` is present on every AG deck — measured 2208/2208 on
        Test, zero missing — so the 师 level can be rebuilt from the file
        itself rather than guessed from a name prefix.  233 divisions on Test,
        each holding 3-57 battalions.
        """
        groups: dict[str, list] = {}
        for b in self.battalions():
            groups.setdefault(b.division or "", []).append(b)
        out = [Division(path=key, label=_division_label(key),
                        battalions=sorted(val, key=lambda x: x.export_name),
                        country=divisions.country_code(key))
               for key, val in groups.items()]
        out.sort(key=lambda d: (-len(d.battalions), d.label))
        return out

    def countries(self) -> list[Country]:
        """AG divisions grouped by the country their label encodes.

        Biggest first, same as :meth:`divisions`, so the picker opens on the
        contingent the file is mostly made of.  A division with no underscore
        in its label has no country and is skipped rather than lumped into a
        fake one.
        """
        groups: dict[str, list] = {}
        for d in self.divisions():
            if d.country:
                groups.setdefault(d.country, []).append(d)
        out = [Country(code=code, divisions=divs)
               for code, divs in groups.items()]
        out.sort(key=lambda c: (-len(c.battalions), c.code))
        return out

    def composition(self, deck, ci: int, si: int | None, limit: int = 3) -> str:
        """What a platoon (or a whole company) fields: ``T72M1 ×4 · BMP-2 ×2``.

        ``si=None`` aggregates every platoon of the company, which is what a
        company node in the tree needs.

        Most mods ship no COMPANIES / PLATOONS dictionary — Test has 6 entries,
        Berlin 865 — so an unresolved name is the common case, not the
        exception.  A modder picking a unit to edit cares *what is in it*, and
        that is always answerable, so it is the better label.
        """
        try:
            company = deck.companies[ci]
            platoons = ([company.platoons[si]] if si is not None
                        else list(company.platoons))
        except (IndexError, AttributeError):
            return ""
        pool = self.retiler.pool(deck.deck_object_id)
        tally: dict[str, int] = {}
        for platoon in platoons:
            for w in platoon.windows:
                if not (0 <= w.pack_index < len(pool)):
                    continue
                key = self.packs.pack_key(pool[w.pack_index])
                if key is None:
                    continue
                name = self.display(key.unit)
                tally[name] = tally.get(name, 0) + max(int(w.count or 0), 1)
        if not tally:
            return ""
        parts = [f"{name} ×{n}" for name, n in
                 sorted(tally.items(), key=lambda kv: -kv[1])[:limit]]
        text = " · ".join(parts)
        return text + (" …" if len(tally) > limit else "")

    def display(self, unit_path: str | None) -> str:
        """``$/GFX/Unit/Descriptor_Unit_X`` -> ``X``.

        Equipment names are *not* dictionary lookups: a pack's unit is a path
        reference, while ``NameResolver`` only resolves ``loc_hash`` keys found
        in the PLATOONS / COMPANIES / UNITS tables.  The vanilla naming rule
        ``Descriptor_Unit_<name>`` is reliable (measured 0 exceptions across
        three mods), so stripping the prefix is the honest answer.
        """
        if not unit_path:
            return "—"
        leaf = unit_path.rsplit("/", 1)[-1]
        return leaf[len("Descriptor_Unit_"):] if leaf.startswith("Descriptor_Unit_") else leaf

    def platoon_name(self, name_hash: bytes | None) -> str:
        """Platoon display name, falling back to ``⟨未知 hash⟩``."""
        if name_hash is None:
            return tr("⟨无名⟩")
        if self.resolver is not None:
            return self.resolver.display(name_hash)
        return tr("⟨未知 {v0}⟩", v0=name_hash.hex()[:8])

    def validate(self) -> validator.ValidationResult:
        return validator.validate_file(self.deck)

    def note(self, message: str) -> None:
        self.log.append(message)

    # -- mutations --------------------------------------------------------

    #: An entry address, ``(combat_group, smart_group, entry)``.
    def swap_entry(self, deck_object_id: int, ci: int, si: int, ei: int,
                   want: PackKey):
        """Change **one entry's** equipment, re-laying the deck under W1-W4.

        The entry — not the slot — is the atom of editing.  ``DeckPackList`` is
        a *sequence of windows*: entry ``(ci, si, ei)`` owns ``count``
        consecutive slots, and W3 requires all of them to carry the same pack.
        An earlier build exposed a ``swap_slot`` that rewrote a single pool
        slot in place; on the Berlin file that turned a legal window of 3x
        ``BM27_Uragan_SOV`` into ``[T72_CMD_SOV, BM27, BM27]`` — a mixed
        window, which is both illegal and silently lossy, because the next
        re-tile collapses a window onto its first slot and deletes the rest.

        Routing through :meth:`Retiler.relay` removes the possibility: the pool
        is re-emitted from each entry's pack, so a change is uniform by
        construction and ``Σ(Number) == len(DeckPackList)`` still holds.
        """
        res = self.packs.swap_entry(deck_object_id, ci, si, ei, want)
        self.note_change(res, tr("换装备 cg{ci}/sg{si}/e{ei}", ci=ci, si=si, ei=ei))
        return res

    def heal_window(self, deck_object_id: int, ci: int, si: int, ei: int,
                    keep_pack: int):
        """Repair a mixed window by choosing which pack survives.

        Only needed for files already damaged by the old slot-level swap; a
        healthy file never produces a mixed window, and :meth:`swap_entry`
        cannot create one.
        """
        res = self.retiler.heal_window(deck_object_id, ci, si, ei, keep_pack)
        self.note_change(res, tr("修复混合窗口 cg{ci}/sg{si}/e{ei}", ci=ci, si=si, ei=ei))
        return res

    def set_entry_experience(self, deck_object_id: int, ci: int, si: int,
                             ei: int, level: int | None):
        """Repoint one entry at the same unit carrying ``level``."""
        res = self.packs.set_entry_experience(deck_object_id, ci, si, ei,
                                              level)
        self.note_change(res, tr("改经验 cg{ci}/sg{si}/e{ei} -> {level}", ci=ci, si=si, ei=ei, level=level))
        return res

    def set_entry_transporter(self, deck_object_id: int, ci: int, si: int,
                              ei: int, unit: str | None):
        """Repoint one entry at the same unit riding ``unit``."""
        res = self.packs.set_entry_transporter(deck_object_id, ci, si, ei,
                                               unit)
        self.note_change(res, tr("改运输 cg{ci}/sg{si}/e{ei}", ci=ci, si=si, ei=ei))
        return res

    def _deck_name(self, deck_object_id: int) -> str:
        return self.deck.info["exports"].get(
            deck_object_id, f"deck#{deck_object_id}").rsplit("/", 1)[-1]

    def set_count(self, deck_object_id: int, ci: int, si: int, ei: int,
                  count: int):
        res = self.retiler.set_count(deck_object_id, ci, si, ei, count)
        self.note_change(res, tr("改数量 cg{ci}/sg{si}/e{ei} -> {count}", ci=ci, si=si, ei=ei, count=count))
        return res

    def insert_window(self, deck_object_id: int, ci: int, si: int,
                      **kwargs):
        res = self.structure.insert_window(deck_object_id, ci, si, **kwargs)
        self.note_change(res, tr("插入条目 cg{ci}/sg{si}", ci=ci, si=si))
        return res

    def remove_window(self, deck_object_id: int, ci: int, si: int, ei: int):
        res = self.structure.remove_window(deck_object_id, ci, si, ei)
        self.note_change(res, tr("删除条目 cg{ci}/sg{si}/e{ei}", ci=ci, si=si, ei=ei))
        return res

    # -- batch (see edit.bulk) -------------------------------------------

    def scoped_replace(self, scope, from_packs: int | Iterable[int] = -1):
        """A :class:`~ndfbin_decks.edit.bulk.ScopedReplace` bound to this file.

        ``from_packs`` is one pack object id, or several — a many-to-one
        replace is the same executor with a wider set.  ``-1`` means "no
        filter", which is how the picker lists everything the scope fields.

        Returned un-executed: the caller surveys the blast radius, shows it,
        and only then applies.
        """
        return ScopedReplace(self.deck, self.packs, self.retiler, scope,
                             from_packs)

    def note_change(self, res, what: str) -> None:
        """Record a successful edit and refresh every derived view."""
        if getattr(res, "ok", False):
            self.dirty = True
            # The Battalion tree is a snapshot; every edit rewrites the very
            # fields it copied, so it must be rebuilt or the UI would keep
            # showing pre-edit counts next to a post-edit slot pool.
            self.deck.invalidate()
            self._catalogue_stale = True
            self.note(f"{what}: {res.summary()}")

    # -- persistence ------------------------------------------------------

    def save(self, path: Path | None = None) -> bytes:
        target = Path(path) if path else self.path
        data = self.packs.save(target)
        self.dirty = False
        self.note(tr("已保存 -> {target}（{v1:,} 字节）", target=target, v1=len(data)))
        return data


def _deck_candidates(mod) -> list[Path]:
    """Every path a mod's compiled deck could be at."""
    out: list[Path] = []
    # ``deck_bin`` raises for a mod with no ``Gen/`` — several workshop mods
    # ship that way — and asking must not raise here, because a name is a
    # nicety, never a requirement.
    try:
        if mod.deploy:
            out.append(Path(mod.deploy) / "Gen" / "NDF" / "GFX" / "Deck.ndfbin")
        if mod.deck_bin:
            out.append(Path(mod.deck_bin))
    except (modlocator.ModNotFound, OSError):
        pass
    return out


def _owning_mod(deck_path: Path):
    """The mod whose deployment contains ``deck_path``, or ``None``.

    One lookup, two consumers: the localisation resolver and the unit universe.
    A loose copy matches nothing and simply gets neither.
    """
    try:
        mods = modlocator.discover()
    except modlocator.ModNotFound:
        return None
    try:
        deck_path = Path(deck_path).resolve()
    except (OSError, ValueError):
        return None
    for mod in mods:
        for cand in _deck_candidates(mod):
            try:
                if cand.exists() and cand.resolve() == deck_path:
                    return mod
            except (OSError, ValueError):
                continue
    return None


def _build_resolver(deck_path: Path, lang: str, mod=None):
    """Best-effort display names from the mod that owns ``deck_path``."""
    if mod is None:
        mod = _owning_mod(deck_path)
    if mod is None:
        # A mod with no dictionary simply has no names to resolve; that is not
        # an error, and it must not stop the file from opening.
        return None
    try:
        return localisation.NameResolver.from_mod(mod, lang=lang)
    except (modlocator.ModNotFound, OSError):
        return None


def _division_label(path: str) -> str:
    """Kept as a module-local alias — the parsing now lives in ``io.divisions``.

    Three callers need it (the 师 picker, the 国家 picker and a country-wide
    batch replace), and only one of them is in this package, so the rule moved
    down a layer instead of being re-derived in each.
    """
    return divisions.division_label(path)


def open_mod(mod, lang: str = "SC") -> Session:
    """Load a deployed mod.

    ``mod`` may be a name (resolved through :func:`io.modlocator.discover`) or
    an already-resolved :class:`~ndfbin_decks.io.modlocator.ModPaths`.  The
    second form is what the window uses: a mod opened from a folder the user
    picked by hand is not in the discovered list, so passing the object through
    is the only way to open it.
    """
    paths = (mod if isinstance(mod, modlocator.ModPaths)
             else modlocator.resolve(mod))
    try:
        deck_bin = paths.deck_bin
    except modlocator.ModNotFound as exc:
        raise FileNotFoundError(str(exc)) from exc
    if not Path(deck_bin).is_file():
        raise FileNotFoundError(
            tr("{label} 没有 Gen/NDF/GFX/Deck.ndfbin（{deploy}）",
                label=paths.label,
                deploy=paths.deploy,
            ))
    # Hand the resolved ModPaths down: it is what tells the session which
    # ``base.zip`` / ``Unit.ndfbin`` belong to this mod's load order.
    return Session.load(Path(deck_bin), lang=lang, mod=paths)