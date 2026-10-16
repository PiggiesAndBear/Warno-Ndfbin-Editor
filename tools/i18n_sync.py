"""Keep the translation tables in step with the code.

``python tools/i18n_sync.py`` reports, per language: how many keys the code
asks for, how many are translated, which are new and which are orphans.
``--write`` rewrites the JSON files, keeping every translation already made.

Exit status is non-zero when a language is short of ``--min`` coverage, so this
can sit in a CI step and fail a release that quietly ships half-translated.

A key is the Chinese source text, so the report is just a set difference — no
message catalogue to maintain, and a string that stops being used shows up as
an orphan rather than rotting in a table forever.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "ndfbin_decks" / "i18n"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def keys_in_sources() -> list[str]:
    """Every ``tr("…")`` first argument, in source order, de-duplicated."""
    found: list[str] = []
    seen: set[str] = set()
    paths = sorted((ROOT / "ndfbin_decks").rglob("*.py")) + [ROOT / "main.py"]
    for path in paths:
        if "i18n" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_bytes())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"tr", "trplural"} and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                key = node.args[0].value
                if key not in seen:
                    seen.add(key)
                    found.append(key)
    return found


def placeholders(text: str) -> set[str]:
    import string
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="rewrite the tables, keeping existing translations")
    ap.add_argument("--min", type=float, default=0.0,
                    help="fail if a language covers less than this (0-100)")
    ap.add_argument("--lang", action="append", default=None,
                    help="restrict to this code (repeatable)")
    args = ap.parse_args()

    # Read from the package rather than hard-coded: the shipped languages are
    # the ones LANGUAGES lists, so a dropped language stops being reported as a
    # table that is 0% translated instead of as a decision.
    from ndfbin_decks import i18n
    keys = keys_in_sources()
    codes = args.lang or list(i18n.TABLE_CODES)
    print(f"code asks for {len(keys)} keys")

    worst = 100.0
    for code in codes:
        path = I18N / f"{code}.json"
        table = {}
        if path.exists():
            table = json.loads(path.read_text(encoding="utf-8"))
        done = [k for k in keys if table.get(k)]
        new = [k for k in keys if not table.get(k)]
        orphan = [k for k in table if k not in keys]
        # A translation that drops a placeholder crashes at render time, which
        # is the worst possible moment — catch it here instead.
        broken = [k for k in done
                  if placeholders(table[k]) != placeholders(k)]
        pct = 100.0 * len(done) / max(len(keys), 1)
        worst = min(worst, pct)
        print(f"  {code}: {len(done)}/{len(keys)} ({pct:.0f}%)"
              f"  待译 {len(new)}  孤儿 {len(orphan)}"
              + (f"  ★占位符不符 {len(broken)}" if broken else ""))
        for k in broken[:3]:
            print(f"      {k!r} -> {table[k]!r}")
        if args.write:
            fresh = {k: table.get(k, "") for k in keys}
            for k, v in table.items():
                if k not in fresh:
                    fresh[k] = v
            path.write_text(json.dumps(fresh, ensure_ascii=False, indent=2,
                                       sort_keys=False) + "\n",
                            encoding="utf-8")
    if worst < args.min:
        print(f"\nFAIL: worst coverage {worst:.0f}% < --min {args.min:.0f}%")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
