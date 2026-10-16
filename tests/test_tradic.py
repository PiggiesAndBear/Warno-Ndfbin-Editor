"""Validate the ``.dic`` reader against plaintext CSV ground truth.

The ``Test`` mod ships both sides of the mapping:

* ``Mods/Test/GameData/Localisation/Test/PLATOONS.csv`` — ``TOKEN;REFTEXT``
* ``mod/Test/Gen/Localisation/Localisation/Test/PLATOONS-DEV.dic`` — the keys

Joining the two by *text* must reproduce every unique ``REFTEXT``.  Three of
Test's seven texts are unique (``Platoon 5`` / ``4`` / ``2``), which makes this
an unambiguous test: a wrong layout yields fragments like ``' PlatCo'``.

Run with::

    python -m tests.test_tradic
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ndfbin_decks.io import modlocator, tradic   # noqa: E402


def read_csv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip().rstrip(";").strip('"')
        if not line or line.upper().startswith("TOKEN;"):
            continue
        if ";" not in line:
            continue
        token, text = line.split(";", 1)
        rows.append((token.strip().strip('"'), text.strip().strip('"')))
    return rows


def main() -> int:
    failures = 0
    try:
        mods = modlocator.discover()
    except modlocator.ModNotFound as exc:
        print(f"FATAL: {exc}")
        return 2

    for mod in mods:
        gen = mod.deploy / "Gen" if mod.deploy else None
        if gen is None or not gen.is_dir():
            continue
        ns = mod.localisation_namespace()
        if ns is None:
            continue
        root = gen / "Localisation" / "Localisation" / ns
        dics = tradic.find_dictionaries(root, "PLATOONS")
        if not dics:
            print(f"{mod.name}: no PLATOONS-*.dic, skipping")
            continue

        csv_dir = mod.csv_dir()
        if csv_dir is None or not (csv_dir / "PLATOONS.csv").is_file():
            print(f"{mod.name}: no project-side PLATOONS.csv, "
                  f"cannot cross-check ({len(dics)} dictionaries read OK)")
            continue

        print(f"\n=== {mod.name}  (namespace {ns}) ===")
        rows = read_csv(csv_dir / "PLATOONS.csv")
        print(f"  CSV rows          : {len(rows)}")
        print(f"  unique REFTEXT    : {len({t for _, t in rows})}")
        print(f"  DEV dic entries   : {len(tradic.read(dics[0]))}")

        for dpath in dics:
            table = tradic.read(dpath)
            lang = tradic.language_of(dpath)
            by_text: dict[str, list[str]] = {}
            for key, text in table.items():
                by_text.setdefault(text, []).append(key.hex())

            # Every unique CSV text must appear verbatim as a dic value.
            missing = [t for t in {x for _, x in rows} if t not in by_text]
            # And no dic value may be a fragment of a CSV text (the classic
            # symptom of reading len-1 instead of len).
            csv_texts = {x for _, x in rows}
            frag = [v for v in table.values()
                    if v not in csv_texts and any(v in t for t in csv_texts)]

            status = "OK" if not missing and not frag else "FAIL"
            if status == "FAIL":
                failures += 1
            print(f"    {lang:>4s}  entries={len(table):5d}  "
                  f"missing_csv_texts={len(missing)}  fragments={len(frag)}  {status}")
            if missing:
                print(f"          missing e.g. {missing[:4]!r}")
            if frag:
                print(f"          fragments e.g. {frag[:4]!r}")

        # Show the actual mapping for the unambiguous texts.
        dev = tradic.load_family(root, "PLATOONS", "DEV")
        text_to_key = {}
        for k, v in dev.items():
            text_to_key.setdefault(v, []).append(k.hex())
        print("  unambiguous joins:")
        for token, text in rows:
            if len(text_to_key.get(text, [])) == 1:
                print(f"    {token:12s} {text!r:12s} -> {text_to_key[text][0]}")

    print()
    if failures:
        print(f"FAIL — {failures} dictionary/languages disagree with the CSV")
        return 1
    print("PASS — every dictionary matches its plaintext CSV source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
