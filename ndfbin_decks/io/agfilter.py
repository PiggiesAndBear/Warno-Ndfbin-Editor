"""Decide which decks are in scope for this editor.

Scope is Army General *Pawn* decks.  Skirmish / PvP decks share the same
``TDeckDescriptor`` class and sit in the same compiled file, so they must be
filtered out explicitly.

The discriminator is whether ``DeckName`` is present:

============================  =========  =======  ==========
source                        decks     no name  has name
============================  =========  =======  ==========
vanilla ``StrategicDecks``       2208     2208         0
vanilla ``Decks.ndf``             128        0       128
Berlin mod                       2394     2266       128
Northern Thunder                 2465     2337       128
============================  =========  =======  ==========

Zero overlap across four datasets, and the reason is structural: an AG deck
takes its display name from ``DeckDivision`` while a PvP deck uses
``DeckName``.

**A second condition is required.**  Seventeen of those 128 PvP decks *do* carry
``DeckCombatGroupList``, so "has combat groups" alone would pull them in.  Both
conditions together give the right answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeckKind(str, Enum):
    """What a ``TDeckDescriptor`` actually is."""

    AG_PAWN = "ag_pawn"      # in scope
    SKIRMISH = "skirmish"    # out of scope (PvP / challenge / _multi)


def is_ag_pawn_deck(props: dict) -> bool:
    """Is this an Army General Pawn deck?

    ``props`` is the decoded property bag of a ``TDeckDescriptor``.
    """
    return "DeckName" not in props and "DeckCombatGroupList" in props


def classify(props: dict) -> DeckKind:
    return DeckKind.AG_PAWN if is_ag_pawn_deck(props) else DeckKind.SKIRMISH


@dataclass
class ScopeStats:
    """Counts for one deck file."""

    total: int = 0
    ag: int = 0
    skirmish: int = 0
    #: AG decks that carry no ``Name`` on some smart group (16 in vanilla)
    ag_without_name: int = 0

    def __str__(self) -> str:
        return (f"{self.total} decks: {self.ag} AG pawn (in scope), "
                f"{self.skirmish} skirmish/PvP (filtered out)")


def filter_ag(records) -> list:
    """``records`` is an iterable of ``(name, props)``; keeps only AG decks."""
    return [r for r in records if is_ag_pawn_deck(r[1])]


def survey(deck_objects, smart_group_props=None) -> ScopeStats:
    """Tally the scope split.

    ``deck_objects`` yields the decoded property bags of ``TDeckDescriptor``.
    ``smart_group_props``, when given, yields ``TDeckSmartGroupDescriptor``
    property bags so the unnamed count can be reported.
    """
    st = ScopeStats()
    for props in deck_objects:
        st.total += 1
        if is_ag_pawn_deck(props):
            st.ag += 1
        else:
            st.skirmish += 1
    if smart_group_props is not None:
        from .localisation import name_hash
        st.ag_without_name = sum(
            1 for p in smart_group_props if name_hash(p) is None)
    return st
