"""Division labels and the country they encode.

A division export looks like

``$/GFX/Division/Descriptor_Deck_Division_RFA_TK_Nord_solo``

and every part of that carries meaning the editor needs: ``RFA`` is the
country, ``_solo`` says how the division is fielded in a campaign rather than
what it is.  Stripping the two is a small job, but three callers need the same
answer — the 师 picker, the 国家 picker, and a country-wide batch replace — so
it lives here rather than in any one of them.

**The country is the label's first token, and that is measured, not guessed.**
All 233 Test divisions, all 238 Berlin ones and all 233 in A World in Flames
have an underscore in their label and a non-empty label, so ``label.split("_")[0]``
is unambiguous.  Berlin adds ``AGF`` (AG Framework's generated decks), which is
why the list is derived from the file instead of hard-coded.

**Codes are not the names modders use.**  Eugen is a French studio, so East
Germany is ``RDA`` (République démocratique allemande) in the division export
while every unit descriptor calls it ``DDR``.  Someone hunting for East German
divisions will type ``DDR``, so :func:`country_names` carries both spellings and
the picker matches either.
"""

from __future__ import annotations

import re
from ..i18n import live, tr

#: Prefix the game stamps on every division export name.
DIVISION_PREFIX = "Descriptor_Deck_Division_"

#: Campaign-specific suffixes that say how the division is fielded, not what
#: it is.  ``_solo`` appears on 233/233 Test divisions.
DIVISION_SUFFIXES = ("_solo", "_coop", "_multi")

#: Display names for the codes the shipped campaigns use.  Anything missing
#: falls back to the bare code, so a mod that invents ``XYZ`` still shows up.
COUNTRY_NAMES: dict[str, str] = live(globals(), "COUNTRY_NAMES", lambda: {
    "SOV": tr("苏联"),
    "US": tr("美国"),
    "RFA": tr("西德"),
    "RDA": tr("东德"),
    "POL": tr("波兰"),
    "TCH": tr("捷克斯洛伐克"),
    "FR": tr("法国"),
    "UK": tr("英国"),
    "NL": tr("荷兰"),
    "BEL": tr("比利时"),
    "CAN": tr("加拿大"),
    "ESP": tr("西班牙"),
    "NATO": tr("北约"),
    "AGF": tr("AGF 自动生成"),
    "YUG": tr("南斯拉夫"),
    "JPN": tr("日本"),
    "ROK": tr("韩国"),
    "PRC": tr("中国"),
})

#: Extra ASCII spellings that should find a code, shown in the picker's label
#: so its own type-to-filter reaches them.  ``RDA``/``DDR`` is the pair that
#: matters in practice: the division exports say RDA (Eugen is a French studio),
#: every unit descriptor says DDR, and a modder types the one they have seen.
COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "RDA": ("DDR",),
    "RFA": ("FRG",),
    "SOV": ("USSR",),
    "US": ("USA",),
    "TCH": ("CSK",),
    "FR": ("FRA",),
    "UK": ("GBR",),
    "NL": ("NLD",),
    "ESP": ("SPN",),
    "YUG": ("SFRJ",),
}

#: ``Descriptor_Deck_Division_`` then a run of word characters.
_DIVISION_LEAF_RE = re.compile(r"^" + re.escape(DIVISION_PREFIX) + r"(?P<leaf>.+)$")


def division_label(path: str | None) -> str:
    """``$/GFX/Division/Descriptor_Deck_Division_RFA_TK_Nord_solo`` → ``RFA_TK_Nord``.

    Purely cosmetic.  An empty path (no ``DeckDivision``) becomes a placeholder
    rather than an empty string, so it stays visible in the picker.
    """
    if not path:
        return tr("（未标师的营）")
    leaf = path.rsplit("/", 1)[-1]
    m = _DIVISION_LEAF_RE.match(leaf)
    if m:
        leaf = m.group("leaf")
    for suffix in DIVISION_SUFFIXES:
        if leaf.endswith(suffix):
            leaf = leaf[: -len(suffix)]
            break
    return leaf or tr("（无名师）")


def country_code(path_or_label: str | None) -> str:
    """The country a division belongs to, or ``""``.

    Accepts a full export path or an already-stripped label, because both
    spellings reach it and re-deriving one from the other at each call site is
    where they would drift.
    """
    if not path_or_label:
        return ""
    text = path_or_label
    if "/" in text:
        text = division_label(text)
    if not text or "_" not in text:
        return ""
    head = text.split("_", 1)[0].strip()
    return head.upper() if head else ""


def country_name(code: str) -> str:
    """Display name for a code; the code itself when it is unknown."""
    return COUNTRY_NAMES.get((code or "").upper(), code)


def country_label(code: str, divisions: int, battalions: int | None = None) -> str:
    """``RDA / DDR  东德  (18 师 · 190 营)`` — what the 国家 picker shows.

    The ASCII aliases are part of the visible text on purpose: the picker's
    type-to-filter runs over the label, so a modder typing ``DDR`` has to land
    on ``RDA`` without knowing the French abbreviation.
    """
    c = (code or "").upper()
    head = " / ".join([c, *COUNTRY_ALIASES.get(c, ())])
    tail = tr("  ({divisions} 师", divisions=divisions)
    tail += tr(" · {battalions} 营)", battalions=battalions) if battalions is not None else ")"
    return f"{head}  {country_name(c)}{tail}"

