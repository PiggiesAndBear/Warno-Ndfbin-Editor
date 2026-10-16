"""Classify deck objects as vanilla / promoted / mod-authored.

Two traps, both hit during recon:

**Naming prefixes do not work.**  AGF-authored objects happen to be named
``AGF_<hex>``, and grepping for that finds them.  But the ``Test`` mod
modifies only *original* ``pion_*`` decks and adds nothing at all — a
prefix-based scan reports 0 changes for a mod that rewrote 39 decks.

**Two tiers are not enough.**  Comparing against vanilla
``StrategicDecks.ndf`` alone marks 128 skirmish decks as "mod new", because
AGF mods pull them in from vanilla ``Decks.ndf``.  They are neither vanilla AG
decks nor new content, so the classification has three buckets:

===============  =========================================
``vanilla``      present in vanilla ``StrategicDecks.ndf``
``promoted``     absent there, present in vanilla ``Decks.ndf``
``mod_new``      in neither
===============  =========================================

With the correct criterion Berlin reports 186 new decks (58 authored + 128
promoted) instead of 58.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

#: Top-level NDF object: ``export Name is Type`` followed by a ``(...)`` body.
NDF_OBJECT = re.compile(
    r'^(?:export\s+)?(\S+)\s+is\s+(\S+)\s*\n\(\s*\n(.*?)^\)',
    re.M | re.S,
)

#: Vanilla deck sources inside ``ModData/base.zip``.
VANILLA_STRATEGIC = "GameData/Generated/Gameplay/Decks/StrategicDecks.ndf"
VANILLA_SKIRMISH = "GameData/Generated/Gameplay/Decks/Decks.ndf"


class Origin(str, Enum):
    VANILLA = "vanilla"
    PROMOTED = "promoted"
    MOD_NEW = "mod_new"


@dataclass
class VanillaIndex:
    """Names of every vanilla deck, split by the file they live in."""

    strategic: set[str] = field(default_factory=set)
    skirmish: set[str] = field(default_factory=set)

    @property
    def all(self) -> set[str]:
        return self.strategic | self.skirmish

    def classify(self, name: str) -> Origin:
        if name in self.strategic:
            return Origin.VANILLA
        if name in self.skirmish:
            return Origin.PROMOTED
        return Origin.MOD_NEW


def load_vanilla(base_zip: Path) -> VanillaIndex:
    """Read both vanilla deck files out of ``ModData/base.zip``.

    Note that the archive stores **plain NDF text**, not compiled data, which is
    what makes a semantic diff against a mod possible at all.
    """
    idx = VanillaIndex()
    with zipfile.ZipFile(base_zip) as z:
        for entry, sink in ((VANILLA_STRATEGIC, idx.strategic),
                            (VANILLA_SKIRMISH, idx.skirmish)):
            if entry not in z.namelist():
                continue
            text = z.read(entry).decode("utf-8", "replace")
            for m in NDF_OBJECT.finditer(text):
                if m.group(2) == "TDeckDescriptor":
                    sink.add(m.group(1))
    return idx


def find_base_zip(explicit: Path | None = None) -> Path:
    """Locate ``ModData/base.zip`` — the only reliable vanilla baseline.

    It lives under the *install* directory, not the mod's own folder, and a
    per-mod snapshot at ``<mod>/base.zip`` is an acceptable fallback.
    """
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"base.zip not found at {explicit}")
        return explicit

    candidates = [
        Path("D:/Steam/steamapps/common/WARNO/Mods/ModData/base.zip"),
        Path("C:/Program Files (x86)/Steam/steamapps/common/WARNO/Mods/ModData/base.zip"),
    ]
    for c in candidates:
        if c.is_file():
            return c

    import os
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    ws = home / "Saved Games" / "EugenSystems" / "WARNO" / "mod"
    if ws.is_dir():
        for d in sorted(ws.iterdir()):
            snap = d / "base.zip"
            if snap.is_file():
                return snap
    raise FileNotFoundError(
        "Cannot find ModData/base.zip. Pass an explicit path."
    )


@dataclass
class DeckProvenance:
    """Per-deck origin, keyed by export leaf name."""

    origins: dict[str, Origin] = field(default_factory=dict)
    vanilla: VanillaIndex = field(default_factory=VanillaIndex)
    #: decks the mod *removed* relative to vanilla
    removed: set[str] = field(default_factory=set)
    #: deck names carrying an author prefix (a hint, never a criterion)
    prefixed: set[str] = field(default_factory=set)

    def of(self, name: str) -> Origin:
        return self.origins.get(name, Origin.MOD_NEW)

    def counts(self) -> dict[Origin, int]:
        c = Counter(self.origins.values())
        return {o: c.get(o, 0) for o in Origin}

    def by_origin(self, origin: Origin) -> list[str]:
        return sorted(n for n, o in self.origins.items() if o is origin)

    def summary(self) -> str:
        c = self.counts()
        bits = [f"{o.value}={c[o]}" for o in Origin]
        if self.removed:
            bits.append(f"removed={len(self.removed)}")
        return "  ".join(bits)


def classify(deck_names, vanilla: VanillaIndex) -> DeckProvenance:
    """Bucket a mod's deck names against the vanilla index."""
    prov = DeckProvenance(vanilla=vanilla)
    seen_v = set()
    for name in deck_names:
        prov.origins[name] = vanilla.classify(name)
        if "AGF_" in name or name.startswith("AG"):
            prov.prefixed.add(name)
        o = prov.origins[name]
        if o is Origin.VANILLA:
            seen_v.add(name)
    prov.removed = vanilla.strategic - seen_v
    return prov


# --------------------------------------------------------------------------
# NDF text helpers (project tree / vanilla source)
# --------------------------------------------------------------------------

def objects_of(text: str) -> dict[str, tuple[str, str]]:
    """``{name: (type, body)}`` for every top-level object in NDF text."""
    return {m.group(1): (m.group(2), m.group(3)) for m in NDF_OBJECT.finditer(text)}


def modified_objects(current: str, vanilla: str) -> tuple[list[str], list[str], list[str]]:
    """``(added, removed, modified)`` between two NDF texts."""
    a = objects_of(current)
    b = objects_of(vanilla)
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    modified = sorted(k for k in (set(a) & set(b)) if a[k][1] != b[k][1])
    return added, removed, modified
