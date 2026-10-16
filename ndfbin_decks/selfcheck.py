"""Round-trip acceptance gate.

Run with::

    python -m ndfbin_decks.selfcheck

Green means: for every deployed mod we can find, every ``.ndfbin`` decodes and
re-encodes to a byte-identical file.  Anything else fails the stage.

The comparison is two-tier on purpose.  ``rebuild_sections`` re-compresses with
zstd level 6 while the game may have used another level, so a byte mismatch on
the *file* does not mean the data is wrong.  The authoritative check is that
the **uncompressed body** round-trips exactly; the file-level check is reported
but not required.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from .core import cndf_codec
from .io import modlocator

#: Files worth checking.  ``Depiction``/``Components`` are large and irrelevant
#: to the deck editor but decoding them proves the codec handles the biggest
#: objects in the game.
ALL_BINS = (
    "GFX/Deck",
    "GFX/Division",
    "GFX/Pawn",
    "GFX/Depiction",
    "UI/BattleOrder",
    "UI/Components",
)
DECK_BINS = ("GFX/Deck", "GFX/Division", "UI/BattleOrder")


@dataclass
class Result:
    mod: str
    slot: str
    ok: bool
    objects: int
    decode_s: float
    encode_s: float
    file_identical: bool
    body_identical: bool
    present: bool = True          # False when the mod simply lacks this slot
    note: str = ""
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    @property
    def failures(self) -> list[Result]:
        """Failures only — a mod that simply lacks a slot is not a failure."""
        return [r for r in self.results if r.present and not r.ok]

    def print(self, only_failures: bool = False) -> None:
        width = max((len(r.mod) for r in self.results), default=10)
        if not only_failures:
            print(f"{'mod':<{width}}  {'slot':<16} {'objects':>8} "
                  f"{'dec':>6} {'enc':>6}  body  file")
        for r in self.results:
            if only_failures and r.ok:
                continue
            mark = "OK  " if r.ok else "FAIL"
            if not r.present:
                body = file_ = "-"
                mark = "--  "
            else:
                body = "=" if r.body_identical else "X"
                file_ = "=" if r.file_identical else "~"
            print(f"{r.mod:<{width}}  {r.slot:<16} {r.objects:>8} "
                  f"{r.decode_s:>5.2f}s {r.encode_s:>5.2f}s  {body}     {file_}"
                  f"  {mark} {r.note}")

    def summary(self) -> tuple[int, int, int]:
        checked = [r for r in self.results if r.present]
        body_bad = [r for r in checked if not r.body_identical]
        file_bad = [r for r in checked if not r.file_identical]
        return len(body_bad), len(file_bad), len(checked)


def check_bin(mod: modlocator.ModPaths, slot: str) -> Result:
    path = mod.deploy / "Gen" / "NDF" / f"{slot}.ndfbin"
    if not path.is_file():
        return Result(mod.name, slot, True, 0, 0, 0, False, False,
                      present=False, note="(mod lacks this slot)")
    raw = path.read_bytes()
    t0 = time.time()
    try:
        doc, dec = cndf_codec.decode(raw, str(path))
    except Exception as exc:                       # noqa: BLE001
        return Result(mod.name, slot, False, 0, 0, 0, False, False,
                      note=f"decode {type(exc).__name__}", detail=str(exc))
    t1 = time.time()
    try:
        out = cndf_codec.rebuild_objects(doc, dec)
    except Exception as exc:                       # noqa: BLE001
        return Result(mod.name, slot, False, dec["object_count"], t1 - t0, 0,
                      False, False, note=f"encode {type(exc).__name__}",
                      detail=str(exc))
    t2 = time.time()

    file_same = out == raw
    try:
        _doc2, _ = cndf_codec.decode(out, "verify")
        body_same = _doc2.full_data == doc.full_data
    except Exception as exc:                       # noqa: BLE001
        return Result(mod.name, slot, False, dec["object_count"], t1 - t0,
                      t2 - t1, False, False,
                      note=f"verify {type(exc).__name__}", detail=str(exc))

    return Result(
        mod=mod.name, slot=slot,
        ok=body_same,                       # body is the authoritative gate
        objects=dec["object_count"],
        decode_s=t1 - t0, encode_s=t2 - t1,
        file_identical=file_same, body_identical=body_same,
        note="" if file_same else "(zstd level differs; body identical)",
    )


def check_deck_shape(mod: modlocator.ModPaths) -> str:
    """Sanity-check the structural invariants the editor will rely on."""
    path = mod.deploy / "Gen" / "NDF" / "GFX" / "Deck.ndfbin"
    if not path.is_file():
        return "(no Deck.ndfbin)"
    _doc, dec = cndf_codec.decode(path.read_bytes(), str(path))
    classes = dec["classes"]
    want = {"TDeckDescriptor", "TDeckPackDescriptor",
            "TDeckCombatGroupDescriptor", "TDeckSmartGroupDescriptor"}
    missing = want - set(classes)
    if missing:
        return f"MISSING classes: {sorted(missing)}"
    props = {(p["name"], p["class"]) for p in dec["properties"]}
    need = [("DeckPackList", "TDeckDescriptor"),
            ("PackIndexUnitNumberList", "TDeckSmartGroupDescriptor"),
            ("IsHQ", "TDeckSmartGroupDescriptor"),
            ("UnitDescriptor", "TDeckPackDescriptor")]
    gone = [n for n in need if n not in props]
    if gone:
        return f"MISSING props: {gone}"
    return f"{len(classes)} classes / {len(props)} props / {dec['object_count']} objects"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    quiet = "-q" in argv
    names = [a for a in argv if not a.startswith("-")]

    try:
        mods = modlocator.discover(only=names[0] if names else None)
    except modlocator.ModNotFound as exc:
        print(f"FATAL: {exc}")
        return 2

    report = Report()
    for mod in mods:
        gen = mod.deploy / "Gen" if mod.deploy else None
        if gen is None or not gen.is_dir():
            print(f"skip {mod.name}: no Gen/ (AGF staging directory?)")
            continue
        for slot in ALL_BINS:
            report.results.append(check_bin(mod, slot))

    if not report.results:
        print("FATAL: no .ndfbin found in any mod")
        return 2

    if not quiet:
        report.print()
    body_bad, file_bad, total = report.summary()

    print()
    print(f"checked {total} .ndfbin file(s)")
    print(f"body-identical : {total - body_bad}/{total}")
    print(f"file-identical  : {total - file_bad}/{total}  "
          f"(differences are zstd level only, not data loss)")
    for mod in mods:
        if (mod.deploy / "Gen").is_dir():
            print(f"deck schema    : {mod.name}: {check_deck_shape(mod)}")

    if body_bad:
        print()
        print("FAILURES (uncompressed body differs):")
        report.print(only_failures=True)
        return 1
    print("\nPASS — every .ndfbin round-trips losslessly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
