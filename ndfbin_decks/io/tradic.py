"""Reader for WARNO ``.dic`` localisation dictionaries (``TRAD`` container).

Layout (verified against Berlin 1989, Northern Thunder and Test — three mods,
nine dictionary families; all three agree byte for byte)::

    offset 0   "TRAD"
    offset 4   u32  entry_count + 1
    offset 8   u64  timestamp
    offset 16  u32  (unused)
    offset 20  u32  (unused)
    offset 24  entry_count x 16 bytes:  [key 8B][u32 off][u32 len][u32 unused]
    ...        UTF-16LE string pool

Three things that are easy to get wrong and were each wrong in an earlier
draft of this file:

* ``entry_count`` is ``u32@4 - 1``.  The record whose high 4 bytes are
  ``0x80000000`` **is a real entry**, not a sentinel — treating it as a
  terminator loses one entry.
* ``off`` is an **absolute file offset**.  The first entry's ``off`` happens to
  equal the pool start, which makes it look pool-relative.
* ``len`` is the **character count**, not bytes.  The pool visibly contains
  ``"Platoon 5"`` at length 9.

The 8-byte ``key`` is the same value that appears as ``loc_hash``
(``type_id 29``) inside a compiled ``.ndfbin`` — see
:mod:`ndfbin_decks.io.localisation`.  It is **not** the localisation token and
there is no known way to compute it from one.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"TRAD"
HEADER_SIZE = 24
ENTRY_SIZE = 16
#: Languages WARNO ships.  ``DEV`` is the untranslated source form.
LANGUAGES = ("SC", "RU", "US", "FR", "GER", "SPA", "POL", "DEV")

#: The final index record has its high 4 bytes set to ``0x80000000`` and its low
#: 4 bytes zero, i.e. the key is ``00 00 00 00 00 00 00 80``.  Its "text" is
#: whatever happens to follow the pool and decodes to garbage — for Berlin
#: ``'вдоз й-елнаипрУ12ы3П.мётТуРОВМгяжк4'``, a reversed byte-swap of Russian.
#:
#: ★ It is **not** a sentinel (the entry count already accounts for it), so the
#: record must be parsed — but it must never reach the caller as a name.
SENTINEL_KEY = b"\x00\x00\x00\x00\x00\x00\x00\x80"


class TradError(ValueError):
    """A ``.dic`` file is truncated or has an unexpected layout."""


@dataclass(frozen=True)
class Entry:
    key: bytes            # 8 raw bytes == the ndfbin loc_hash
    offset: int           # absolute file offset of the UTF-16LE text
    length: int           # character count


def is_trad(path: Path) -> bool:
    """Cheap magic probe — ``TRAD`` plus the 4-byte count sanity range."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
        if len(head) < 8 or head[:4] != MAGIC:
            return False
        count = struct.unpack_from("<I", head, 4)[0]
        return 0 < count < 1 << 24
    except OSError:
        return False


def read(path: Path, *, drop_sentinel: bool = True) -> dict[bytes, str]:
    """Decode a ``.dic`` into ``{8-byte key: text}``.

    An empty dictionary (CSV with only a header) decodes to ``{}``; the file is
    still 24 bytes, so ``read`` returns an empty mapping rather than raising.

    ``drop_sentinel`` removes the trailing ``00…80`` record, whose text is
    garbage.  Pass ``False`` only when auditing the raw layout.
    """
    raw = Path(path).read_bytes()
    if len(raw) < HEADER_SIZE:
        raise TradError(f"{path}: shorter than a TRAD header ({len(raw)} B)")
    if raw[:4] != MAGIC:
        raise TradError(f"{path}: bad magic {raw[:4]!r}")

    count = struct.unpack_from("<I", raw, 4)[0]
    if count == 0:
        return {}
    n = count - 1                      # the +1 is part of the encoding
    if n < 0:
        raise TradError(f"{path}: implausible entry count {count}")
    if HEADER_SIZE + ENTRY_SIZE * n > len(raw):
        raise TradError(f"{path}: entry table overruns file")

    out: dict[bytes, str] = {}
    for i in range(n):
        at = HEADER_SIZE + ENTRY_SIZE * i
        key = raw[at:at + 8]
        if drop_sentinel and key == SENTINEL_KEY:
            continue
        off, length = struct.unpack_from("<II", raw, at + 8)
        if off + length * 2 > len(raw):
            raise TradError(
                f"{path}: entry {i} text at {off}+{length} chars "
                f"overruns file ({len(raw)} B)"
            )
        out[key] = raw[off:off + length * 2].decode("utf-16-le", errors="replace") \
            if length else ""
    return out


def find_dictionaries(root: Path, family: str = "PLATOONS") -> list[Path]:
    """All ``<family>-<LANG>.dic`` under ``root``, DEV first.

    ``root`` is typically ``Gen/Localisation/Localisation/<namespace>/``.
    """
    if not root.is_dir():
        return []
    hits = list(root.glob(f"{family}-*.dic"))
    order = {lang: i for i, lang in enumerate(LANGUAGES)}
    hits.sort(key=lambda p: (order.get(p.stem.split("-")[-1], 99), p.stem))
    return hits


def load_family(root: Path, family: str, lang: str) -> dict[bytes, str]:
    """Read one dictionary, preferring ``<family>-<lang>.dic`` then ``-DEV``."""
    for suffix in (lang, "DEV"):
        p = root / f"{family}-{suffix}.dic"
        if p.is_file():
            return read(p)
    return {}


def language_of(path: Path) -> str:
    """``PLATOONS-SC.dic`` -> ``SC``."""
    return path.stem.split("-")[-1]


def family_of(path: Path) -> str:
    """``PLATOONS-SC.dic`` -> ``PLATOONS``."""
    return path.stem.rsplit("-", 1)[0] if "-" in path.stem else path.stem
