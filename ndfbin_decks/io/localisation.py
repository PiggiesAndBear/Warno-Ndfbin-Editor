"""Resolve ``loc_hash`` values to human-readable names.

Background
----------
Inside a compiled ``.ndfbin``, every display name is an 8-byte ``loc_hash``
(``type_id 29``) rather than a plaintext token::

    TDeckSmartGroupDescriptor ( Name = 1f3992d5f3405906 )

Those 8 bytes are **exactly** the key used by the ``.dic`` dictionaries.  That
was verified by set intersection: 5 of Test's 7 dictionary keys appear
verbatim as ``SmartGroup.Name`` values in its ``Deck.ndfbin``.  So resolving a
name is a plain dictionary lookup — no token needs to be recovered.

What is *not* recoverable is the mapping **token → hash**.  Roughly forty hash
algorithms times six encodings were tried and none reproduces it, so a brand new
name cannot be minted.  The editor therefore offers a picker over existing
hashes instead of free text.

Coverage is inherently low: the game's own dictionaries are sealed inside
``Data/PC/<build>/*.dat`` and out of scope, so only the mod's own additions are
readable.  Measured: Test 5/8064 unique hashes (0.06%), Berlin 865/8924 (9.7%).
Everywhere else must degrade gracefully, which is what :class:`NameResolver`
is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import tradic
from ..i18n import live, tr

#: Dictionary families the deck editor actually needs.
FAMILIES = ("PLATOONS", "COMPANIES", "UNITS")

#: Shown when a hash resolves in no dictionary.
UNKNOWN_TEMPLATE = live(globals(), "UNKNOWN_TEMPLATE", lambda: tr("⟨未知 {key}⟩"))
#: Shown when the object carries no ``Name`` at all (16 vanilla smart groups do).
ANONYMOUS = live(globals(), "ANONYMOUS", lambda: tr("⟨无名⟩"))


@dataclass(frozen=True)
class Resolved:
    """One lookup outcome."""

    key: bytes | None
    text: str | None
    family: str | None = None
    lang: str | None = None

    @property
    def found(self) -> bool:
        return self.text is not None

    @property
    def key_hex(self) -> str:
        return self.key.hex() if self.key else ""

    def display(self) -> str:
        """Text to show, degrading rather than lying."""
        if self.key is None:
            return ANONYMOUS
        if self.text is not None:
            return self.text
        return UNKNOWN_TEMPLATE.format(key=self.key.hex())


@dataclass
class Coverage:
    """How much of a deck file can be named at all."""

    total: int = 0
    resolved: int = 0
    anonymous: int = 0

    @property
    def pct(self) -> float:
        return (self.resolved / self.total * 100) if self.total else 0.0

    def __str__(self) -> str:
        if not self.total:
            return "no name-bearing objects"
        return (f"{self.resolved}/{self.total} named "
                f"({self.pct:.2f}%), {self.anonymous} unnamed")


@dataclass
class NameResolver:
    """Resolve ``loc_hash`` values against a mod's own dictionaries.

    ``lang`` selects the language file; a missing language silently falls back to
    ``DEV`` (the untranslated source), which is always present.
    """

    lang: str = "SC"
    #: family -> key -> text
    tables: dict[str, dict[bytes, str]] = field(default_factory=dict)
    #: where the dictionaries came from, for the UI to show
    source: Path | None = None

    @classmethod
    def from_mod(cls, mod, lang: str = "SC") -> "NameResolver":
        """Build a resolver from a mod's deployed dictionaries.

        Returns an empty resolver when the mod ships none — every lookup will
        then degrade to ``⟨未知 …⟩``, which is correct rather than broken.
        """
        self = cls(lang=lang)
        gen = mod.deploy / "Gen" if mod.deploy else None
        if gen is None or not gen.is_dir():
            return self
        ns = mod.localisation_namespace()
        if ns is None:
            return self
        root = gen / "Localisation" / "Localisation" / ns
        for fam in FAMILIES:
            table = tradic.load_family(root, fam, lang)
            if table:
                self.tables[fam] = table
        self.source = root
        return self

    @property
    def has_any(self) -> bool:
        return bool(self.tables)

    def resolve(self, key: bytes | None) -> Resolved:
        if key is None:
            return Resolved(None, None)
        for fam in FAMILIES:
            table = self.tables.get(fam)
            if table:
                text = table.get(key)
                if text is not None:
                    return Resolved(key, text, fam, self.lang)
        return Resolved(key, None)

    def display(self, key: bytes | None) -> str:
        return self.resolve(key).display()

    def resolve_hex(self, key_hex: str | None) -> str:
        """Convenience wrapper for values already stored as hex text."""
        if not key_hex:
            return ANONYMOUS
        try:
            return self.display(bytes.fromhex(key_hex))
        except ValueError:
            return ANONYMOUS

    def all_names(self, family: str = "PLATOONS") -> list[tuple[str, str]]:
        """``[(text, key_hex)]`` sorted by text — the source for a name picker.

        Deduplicated: 43,365 vanilla smart groups share only 8,924 distinct
        hashes, so this list is the realistic set of names a user can assign.
        """
        table = self.tables.get(family, {})
        seen: dict[str, str] = {}
        for key, text in table.items():
            seen.setdefault(text, key.hex())
        return sorted(seen.items(), key=lambda kv: kv[0])

    def family_stats(self) -> dict[str, int]:
        return {fam: len(t) for fam, t in sorted(self.tables.items())}


# --------------------------------------------------------------------------
# Hash extraction from decoded objects
# --------------------------------------------------------------------------

#: Value types that carry a localisation reference.
LOC_HASH = 29


def name_hash(props: dict) -> bytes | None:
    """Pull the ``loc_hash`` out of a decoded property bag.

    Returns ``None`` when the field is absent — 16 vanilla smart groups carry no
    ``Name`` at all, and several thousand carry one that no dictionary covers.
    Both cases are legitimate and must stay distinguishable.
    """
    for field in ("Name", "NameToken", "DeckName"):
        v = props.get(field)
        if isinstance(v, dict) and v.get("type_id") == LOC_HASH:
            hx = v.get("value_hex")
            if hx:
                try:
                    return bytes.fromhex(hx)
                except ValueError:
                    return None
    return None


def is_hq(props: dict) -> bool:
    """Whether a smart group is a headquarters group.

    ``IsHQ`` is a **presence flag**, not a boolean value: across 43,365 + 44,697
    smart groups in two mods there is not a single ``IsHQ = False``.  Writing
    ``False`` would leave a stray attribute the game may still treat as HQ.
    """
    return "IsHQ" in props
