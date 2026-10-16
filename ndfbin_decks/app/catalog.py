"""A searchable catalogue of the packs **this very file** may field in Army General.

Scope is deliberately narrow, and that is a correctness decision rather than a
limitation: the picker may only ever offer a pack object that exists in the
``Deck.ndfbin`` currently open.  Offering a unit from another mod — or from the
vanilla game, or from a sibling mod in the workshop folder — would produce a
pack whose ``$/GFX/Unit/...`` reference the target file's ``IMPR`` trie cannot
resolve and whose definition the game does not have.  The failure is silent:
the deck encodes, the game loads, and the unit simply is not there.

**The second gate is Army General provenance.**  ``Deck.ndfbin`` is shared by
two disjoint populations of decks: AG Pawn decks and the 128 skirmish / PvP
decks.  They share the pack pool object type but never a pack instance,
measured across datasets:

=================  =========  =======  =========  ==========
file               packs      AG       PvP        overlap
=================  =========  =======  =========  ==========
Test                   5178     2690       2483            0
Berlin                 5182     2687       2483            1
=================  =========  =======  =========  ==========

A pack some PvP deck fields and no AG deck fields is therefore not a safe AG
target: it has never been exercised by the AG code path.  ``has_xp`` was the
first guess at the discriminator and it is wrong in both directions — 311 of
the 833 packs without an ``ExperienceLevel`` are load-bearing in AG decks
(they are the ones that inherit the division's level), while 1961 PvP-only
packs *do* carry one.  Provenance is the only signal that partitions cleanly,
so provenance is what this module uses.  Experience stays visible and
searchable, and :meth:`PackCatalogue.search` can exclude the no-xp packs for
anyone who wants the belt-and-braces filter, but it is not the gate.

So the rules this module enforces:

* the catalogue is built from **one** :class:`~ndfbin_decks.core.deckmodel.DeckFile`
  and nothing else — no cross-mod merge, no game data, no sibling mods;
* a pack is offered only when an **AG Pawn deck** in that file references it;
* :meth:`PackCatalogue.owns` is the only sanctioned way to turn user input into
  a :class:`~ndfbin_decks.edit.equip.PackKey`, and it refuses anything the file
  does not already contain.

What the catalogue cannot show: packs the mod defines but never fields, since
``Deck.ndfbin`` only records packs some deck references.  Measured 2100 units on
Test, 2103 on Berlin — counts that already include mod-authored entries such as
``T72M1_TCH``, because that mod does field it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.deckmodel import unit_leaf
from ..edit.equip import PackKey
from ..io import agfilter
from ..i18n import live, tr

#: NATO-ish country suffixes that end most unit names.  Used only to group the
#: list, never to filter — an unknown suffix just lands in "other".
COUNTRY_SUFFIXES = (
    "SOV", "RFA", "UK", "POL", "TCH", "BEL", "ESP", "CAN", "FRA", "GER",
    "USA", "NLD", "DEN", "NOR", "SWE", "FIN", "ITA", "ROU", "BUL", "GRC",
    "TUR", "EGY", "CIV", "MLI", "NAM", "SYR", "IRQ", "KSA", "JPN", "KOR",
    "CHN", "PRK", "AUS", "NZL", "ZIM", "BRA", "ARG", "MAR", "TUN", "DZA",
    "LBY", "AGO", "NGA", "ETH", "GHA", "SEN", "MLI", "SPN", "YUG", "CSK",
    "CZE", "HUN", "ROM", "ALB", "MKD", "BIH", "HRV", "SVN", "EST", "LVA",
    "LTU", "UKR", "BLR", "GEO", "ARM", "AZE", "UZB", "KAZ", "MON", "TKM",
    "MNG", "LAO", "THA", "VNM", "PRK", "PHI", "MYS", "IDN", "PAK", "BGD",
)

#: Rough role hints, inferred from the name.  Purely cosmetic grouping.
TYPE_HINTS = live(globals(), "TYPE_HINTS", lambda: (
    ("Trans", tr("运输")),
    ("_trans_", tr("运输")),
    ("truck", tr("运输")),
    ("APC", tr("装甲")),
    ("IFV", tr("装甲")),
    ("MBT", tr("坦克")),
    ("Tank", tr("坦克")),
    ("Art", tr("炮兵")),
    ("arty", tr("炮兵")),
    ("MLRS", tr("炮兵")),
    ("AAA", tr("防空")),
    ("SAM", tr("防空")),
    ("Fighter", tr("空军")),
    ("Jet", tr("空军")),
    ("MiG", tr("空军")),
    ("Su_", tr("空军")),
    ("Inf", tr("步兵")),
    ("Rifle", tr("步兵")),
    ("Rifles", tr("步兵")),
    ("MotRifles", tr("步兵")),
    ("HMG", tr("步兵")),
    ("MG_", tr("步兵")),
    ("Engineer", tr("工兵")),
    ("Sniper", tr("步兵")),
    ("Rocket", tr("步兵")),
    ("Recon", tr("侦察")),
))

UNIT_RE = re.compile(r"$/GFX/Unit/Descriptor_Unit_(?P<leaf>.+)$")


def pack_ids_by_kind(deck) -> tuple[set[int], set[int]]:
    """``(ag, skirmish)`` pack ids, in one pass over the decks.

    Splitting by deck kind is what makes the AG gate possible: the two
    populations share the file and the object type but essentially never a pack
    instance, so "referenced by an AG deck" is answerable with a set lookup.
    """
    ag: set[int] = set()
    pvp: set[int] = set()
    for o in deck.info["objects"]:
        if o["class"] != "TDeckDescriptor":
            continue
        props = {p["property_name"]: p["value"] for p in o["properties"]}
        target = ag if agfilter.is_ag_pawn_deck(props) else pvp
        for item in props.get("DeckPackList", {}).get("items", []) or []:
            pid = item.get("object_id")
            if pid is not None:
                target.add(pid)
    return ag, pvp


def ag_pack_ids(deck) -> set[int]:
    """Packs some **AG Pawn deck** in ``deck`` references.

    The single source of truth for "may Army General field this?".  Deliberately
    ignores the skirmish decks, which share the file and the object type but
    never a pack instance with AG.
    """
    return pack_ids_by_kind(deck)[0]


def _skirmish_pack_ids(deck) -> set[int]:
    return pack_ids_by_kind(deck)[1]


def _ag_window_usage(deck) -> dict[int, int]:
    """pack id -> how many AG equipment entries (windows) point at it.

    This is the blast radius of an edit: re-pointing a pack that 40 windows
    share changes 40 places, which is exactly what the user must know first.
    """
    usage: dict[int, int] = {}
    for o in deck.info["objects"]:
        if o["class"] != "TDeckDescriptor":
            continue
        props = {p["property_name"]: p["value"] for p in o["properties"]}
        if not agfilter.is_ag_pawn_deck(props):
            continue
        pool = [it.get("object_id")
                for it in props.get("DeckPackList", {}).get("items", []) or []]
        for cgref in props.get("DeckCombatGroupList", {}).get("items", []) or []:
            cg = deck._by_id.get(cgref.get("object_id"))
            if cg is None:
                continue
            cg_props = deck._props(cg)
            for sgref in cg_props.get("SmartGroupList", {}).get("items", []) or []:
                sg = deck._by_id.get(sgref.get("object_id"))
                if sg is None:
                    continue
                sg_props = deck._props(sg)
                for tup in (sg_props.get("PackIndexUnitNumberList", {})
                            .get("items", []) or []):
                    idx = tup.get("key", {}).get("value")
                    if idx is not None and 0 <= idx < len(pool):
                        pid = pool[idx]
                        if pid is not None:
                            usage[pid] = usage.get(pid, 0) + 1
    return usage


@dataclass
class ScopeStats:
    """What the AG gate kept and what it dropped — shown in the UI."""

    total_packs: int = 0
    ag: int = 0
    skirmish_only: int = 0
    unreferenced: int = 0
    #: AG packs that carry no ``ExperienceLevel`` (311 on Test — legitimate)
    ag_without_xp: int = 0

    def summary(self) -> str:
        return (tr("{ag} 个将军模式可用装备包（已排除 {skirmish_only} 个 PvP/行动模式专用、{unreferenced} 个无人引用）",
            ag=self.ag,
            skirmish_only=self.skirmish_only,
            unreferenced=self.unreferenced,
        ))


@dataclass(frozen=True)
class PackChoice:
    """One selectable option: a real pack object plus how to label it."""

    pack_object_id: int
    unit: str | None
    transporter: str | None
    experience: object | None
    number: object
    #: AG equipment entries across the file pointing at this pack
    uses: int = 0

    @property
    def key(self) -> PackKey:
        return PackKey(unit=self.unit, transporter=self.transporter,
                       experience=self.experience, number=self.number)

    @property
    def unit_name(self) -> str:
        return unit_leaf(self.unit) if self.unit else "—"

    @property
    def has_xp(self) -> bool:
        """Whether the pack states an experience level.

        ``False`` is *not* an error: 311 such packs are load-bearing in AG
        decks, inheriting the division's level instead of overriding it.
        """
        return self.experience is not None

    @property
    def group(self) -> str:
        """Country suffix, then a rough role, for combo-box sections."""
        leaf = self.unit_name
        country = ""
        for code in COUNTRY_SUFFIXES:
            if leaf.endswith("_" + code):
                country = code
                break
        for token, label in TYPE_HINTS:
            if token.lower() in leaf.lower():
                return f"{label} · {country}" if country else label
        return country or tr("其他")

    def label(self) -> str:
        bits = [self.unit_name]
        if self.transporter:
            bits.append(f"[{unit_leaf(self.transporter)}]")
        if self.experience is not None:
            bits.append(tr("经验{experience}", experience=self.experience))
        else:
            bits.append(tr("继承经验"))
        bits.append(f"#{self.pack_object_id}")
        if self.uses:
            bits.append(f"×{self.uses}")
        return " ".join(bits)

    def search_text(self) -> str:
        """Everything a user might plausibly type, lower-cased.

        The experience level appears as the bare digit (``3``), as ``xp3`` and
        as ``经验3`` so all three habits reach the same pack.  A pack without
        one is reachable as ``继承`` / ``无xp``, which is how the UI labels it.
        """
        bits = [self.unit_name, unit_leaf(self.transporter) or "",
                str(self.pack_object_id)]
        if self.experience is not None:
            bits += [str(self.experience), f"xp{self.experience}",
                     tr("经验{experience}", experience=self.experience)]
        else:
            bits += [tr("继承"), tr("无xp"), "noxp"]
        return " ".join(b for b in bits if b).lower()


class PackCatalogue:
    """Army-General-usable packs of **one** file, ready to populate a picker.

    Construct through :meth:`build`; there is no constructor argument for
    "also include these other mods", by design.
    """

    def __init__(self) -> None:
        self.items: list[PackChoice] = []
        self.source_path: str = ""
        self.stats: ScopeStats = ScopeStats()
        #: ids admissible because an AG deck referenced them at load time
        self.baseline: set[int] = set()
        #: every unit / transporter path **any** pack in the file references,
        #: AG-usable or not.  A path in here resolves in the file's ``IMPR``
        #: trie; a path outside it does not, and can only be legitimate if the
        #: mod's own ``Unit.ndfbin`` defines it.
        self.file_units: set[str] = set()
        self.file_transporters: set[str] = set()

    @classmethod
    def build(cls, deck, *, baseline: set[int] | None = None) -> "PackCatalogue":
        """Index the AG-usable packs of ``deck`` and nothing else.

        ``baseline`` carries ids that were AG-referenced when the file was
        opened.  A batch replace can drop a pack out of every AG deck, and the
        user must still be able to pick it back; without the baseline an
        option would vanish the moment it stopped being used.
        """
        self = cls()
        self.source_path = str(getattr(deck, "path", ""))
        self.baseline = set(baseline or ())

        all_packs = set(deck._packs)
        # Two different questions, deliberately answered from two different
        # sets: "what may I *offer*" (AG provenance) and "what does the file
        # *know*" (the whole pack pool).  Building a brand-new combination needs
        # the second one — its unit may exist in the mod and simply never have
        # been fielded yet.
        for props in deck._packs.values():
            unit = props.get("UnitDescriptor", {}).get("value")
            if unit:
                self.file_units.add(unit)
            trans = props.get("TransporterDescriptor", {}).get("value")
            if trans:
                self.file_transporters.add(trans)

        ag = ag_pack_ids(deck)
        pvp = _skirmish_pack_ids(deck)
        usage = _ag_window_usage(deck)

        # Baseline keeps an option alive after a batch replace drops it out of
        # every AG deck, so the user can always put it back.
        eligible = ag | (self.baseline & all_packs)

        for oid in sorted(eligible):
            key = _key_of(deck, oid)
            if key is None:
                continue
            self.items.append(PackChoice(
                pack_object_id=oid, unit=key.unit,
                transporter=key.transporter, experience=key.experience,
                number=key.number, uses=usage.get(oid, 0)))

        self.items.sort(key=lambda c: (c.group, c.unit_name,
                                       str(c.transporter), str(c.experience)))
        self.stats = ScopeStats(
            total_packs=len(all_packs),
            ag=len(ag),
            skirmish_only=len(pvp - ag),
            unreferenced=len(all_packs - ag - pvp),
            ag_without_xp=sum(1 for c in self.items if not c.has_xp))
        return self

    def owns(self, pack_object_id: int) -> PackChoice | None:
        """The choice for a pack id, but only if **this file** has it.

        The single gate every id from the UI must pass.  A stale table row or a
        hand-typed number from another mod resolves to ``None`` here rather
        than silently pointing at whatever that id means elsewhere.
        """
        return self.by_id(pack_object_id)

    def owns_key(self, key: PackKey) -> PackChoice | None:
        """The choice matching ``key``, or ``None``.

        A ``None`` field on ``key`` means *"whatever this file happens to
        have"*, not *"must be None"*.  A user who types ``T72M1_TCH`` has named
        a unit, not a full pack signature; demanding an exact match would
        reject a pack that is right there in the file.  Supplying the field
        explicitly narrows the search instead.
        """
        hits = [c for c in self.items
                if c.unit == key.unit
                and (key.transporter is None or c.transporter == key.transporter)
                and (key.experience is None or c.experience == key.experience)]
        if not hits:
            return None
        # prefer the plainest variant: no transport, lowest experience
        hits.sort(key=lambda c: (c.transporter is not None,
                                 c.experience if c.experience is not None else 1))
        return hits[0]

    def __len__(self) -> int:
        return len(self.items)

    def search(self, text: str, limit: int = 200,
               *, require_xp: bool = False) -> list[PackChoice]:
        """Case-insensitive substring match over name, transport and id.

        ``require_xp`` is the optional belt-and-braces filter.  It is off by
        default because a missing ``ExperienceLevel`` is legal in AG — those
        packs inherit the division's level — and turning it on would hide real
        options such as the 311 on Test.
        """
        needle = (text or "").strip().lower()
        pool = [c for c in self.items if c.has_xp] if require_xp else self.items
        if not needle:
            return pool[:limit]
        hits = [c for c in pool if needle in c.search_text()]
        # exact / prefix matches first: typing "T72" should surface T72M1 before
        # T-72's dozen other relatives further down the alphabet.
        hits.sort(key=lambda c: (0 if c.unit_name.lower().startswith(needle) else 1,
                                 c.unit_name.lower()))
        return hits[:limit]

    def groups(self) -> list[str]:
        seen: list[str] = []
        for c in self.items:
            if c.group not in seen:
                seen.append(c.group)
        return seen

    def in_group(self, group: str) -> list[PackChoice]:
        return [c for c in self.items if c.group == group]

    def by_id(self, pack_object_id: int) -> PackChoice | None:
        for c in self.items:
            if c.pack_object_id == pack_object_id:
                return c
        return None

    # -- building a combination the file does not have yet -----------------

    def find_exact(self, key: PackKey) -> PackChoice | None:
        """The AG-usable pack whose content is *exactly* ``key``, or ``None``.

        Unlike :meth:`owns_key`, a ``None`` field here means **None**, not
        "whatever the file has": the caller is describing a combination it is
        about to create, so ``transporter=None`` must match a pack with no
        transporter rather than silently binding to one that has.
        """
        want = (key.unit, key.transporter or None, key.experience)
        for c in self.items:
            if (c.unit, c.transporter or None, c.experience) == want:
                return c
        return None

    def units(self) -> list[str]:
        """Distinct unit paths the AG data in this file fields, sorted.

        The picker's dropdown for "new combination".  Drawing it from the AG
        population rather than the whole pack pool is the same gate the list
        uses, so a unit that only PvP decks field is not offered as if it were
        proven.
        """
        return sorted({c.unit for c in self.items if c.unit})

    def transporters(self) -> list[str]:
        """Distinct transport paths the AG data in this file fields, sorted."""
        return sorted({c.transporter for c in self.items if c.transporter})

    def knows_unit(self, path: str | None) -> bool:
        """Does *any* pack in the file reference this unit path?

        The looser question that has to be asked before creating a pack: the
        path may exist in the mod's ``Unit.ndfbin`` and simply never have been
        fielded, which is legal — and is exactly what ``Deck.ndfbin`` alone
        cannot tell you.  A ``False`` here is therefore a prompt for the user,
        not a refusal.
        """
        return bool(path) and path in self.file_units

    def knows_transporter(self, path: str | None) -> bool:
        return bool(path) and path in self.file_transporters


def _key_of(deck, pack_object_id: int) -> PackKey | None:
    props = deck._packs.get(pack_object_id)
    if props is None:
        return None
    return PackKey(
        unit=props.get("UnitDescriptor", {}).get("value"),
        transporter=props.get("TransporterDescriptor", {}).get("value"),
        experience=props.get("ExperienceLevel", {}).get("value"),
        number=props.get("Number", {}).get("value", 1))


def parse_unit_path(text: str) -> str | None:
    """Normalise what the user typed into a full ``$/GFX/Unit/...`` path.

    Purely syntactic — it does **not** decide whether the unit exists.  That
    check belongs to :meth:`PackCatalogue.owns_key`, which can only say yes for
    units the open file already contains.  Keeping the two apart is what stops
    a typo (or another mod's id) from turning into an unresolvable reference.
    """
    s = (text or "").strip()
    if not s:
        return None
    m = UNIT_RE.match(s)
    if m:
        return f"$/GFX/Unit/Descriptor_Unit_{m.group('leaf')}"
    s = s.replace("\\", "/")
    if s.startswith("$/GFX/"):
        return s
    return f"$/GFX/Unit/Descriptor_Unit_{s}"
