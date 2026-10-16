"""The interface translation: tables, placeholders, and a live switch.

Run with ``python -m tests.test_i18n``.  Exits 0 on PASS.

What is actually worth asserting here is not that a translation is *good* — no
test can tell — but that the machinery cannot ship a broken one:

* **Coverage.** English is the reference language and must be complete; the
  others are allowed to lag, but the gap has to be visible rather than silent.
* **Placeholders.** ``tr("…{n}…", n=3)`` formats at render time, so a
  translation that drops a placeholder is a ``KeyError`` in the middle of a
  dialog — the worst place to find out.  Caught here instead.
* **The live switch.** A language change has to reach the module-level
  constants too, or half the interface stays behind.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _env                                              # noqa: E402

from ndfbin_decks import paths                           # noqa: E402

# The settings file goes to the real LOCALAPPDATA otherwise.  The variable name
# is read from ``paths`` instead of repeated here, so renaming it cannot leave
# this test quietly writing into the user's real profile — which is exactly
# what happened when the name changed away from NDFDECKS_HOME.
os.environ.setdefault(paths.HOME_ENV,
                      tempfile.mkdtemp(prefix="ndfbin-i18n-"))

from ndfbin_decks import i18n                            # noqa: E402
from ndfbin_decks.i18n import settings, tr               # noqa: E402

_checks: list[tuple[bool, str, str]] = []


def check(what: str, ok: bool, detail: str = "") -> None:
    _checks.append((bool(ok), what, detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {what}"
          + (f"  — {detail}" if detail else ""))


def fields(text: str) -> set[str]:
    import string
    out = set()
    for _lit, name, _spec, _conv in string.Formatter().parse(text):
        if name:
            out.add(name.split(".")[0].split("[")[0])
    return out


def main() -> int:
    i18n.set_language(i18n.SOURCE_LANG)

    print("=== 1. the source language needs no table ===")
    check("zh_CN serves the Chinese source through untouched",
          tr("保存") == "保存", tr("保存"))
    check("and formats its placeholders",
          tr("共 {n} 个营", n=3) == "共 3 个营", tr("共 {n} 个营", n=3))

    print()
    print("=== 2. every key the code asks for is in English ===")
    sys.path.insert(0, str(ROOT / "tools"))
    from i18n_sync import keys_in_sources
    keys = keys_in_sources()
    check("the scanner found the keys", len(keys) > 100, f"{len(keys)} keys")
    done, total = i18n.coverage("en", keys)
    check("English is complete", done == total, f"{done}/{total}")
    missing = [k for k in keys if not i18n.load("en").get(k)][:3]
    if missing:
        print("       missing:", *[repr(m[:40]) for m in missing], sep="\n         ")

    print()
    print("=== 3. no translation breaks a placeholder ===")
    for code in i18n.TABLE_CODES:
        table = i18n.load(code)
        if not table:
            print(f"       {code}: no table yet (skipped)")
            continue
        bad = [k for k, v in table.items() if v and fields(v) != fields(k)]
        check(f"{code} keeps every placeholder", not bad,
              f"{len(table)} entries, {len(bad)} broken")
        for k in bad[:3]:
            print(f"         {k!r} -> {table[k]!r}")

    print()
    print("=== 4. switching language reaches everything ===")
    i18n.set_language("en")
    check("tr() follows the switch", tr("保存") == "Save", tr("保存"))
    check("and so does a formatted string",
          tr("{v0} 个师 · {v1} 个营 · {v2}", v0=2, v1=3, v2="x")
          == "2 divisions · 3 battalions · x",
          tr("{v0} 个师 · {v1} 个营 · {v2}", v0=2, v1=3, v2="x"))
    # The point of ``live``: a module-level constant is evaluated once, at
    # import time, and would otherwise stay in whichever language that was.
    from ndfbin_decks.edit import bulk
    check("a module-level constant follows too",
          bulk.LEVEL_LABELS["division"] == "This division",
          bulk.LEVEL_LABELS["division"])
    check("and a country name with it",
          __import__("ndfbin_decks.io.divisions", fromlist=["x"])
          .COUNTRY_NAMES["SOV"] == "Soviet Union",
          __import__("ndfbin_decks.io.divisions", fromlist=["x"])
          .COUNTRY_NAMES["SOV"])

    print()
    print("=== 5. the choice survives a restart ===")
    settings.save_language("en")
    check("settings round-trips", settings.saved_language() == "en",
          settings.saved_language())
    check("and the file is plain JSON a human can edit",
          "{"
          in settings.path().read_text(encoding="utf-8")[:1],
          str(settings.path()))
    settings.save_language("zh_CN")
    check("put back to the source language",
          settings.saved_language() == "zh_CN", settings.saved_language())

    print()
    print("=== 6. an unknown code falls back, never blanks ===")
    i18n.set_language("xx")
    check("unknown code is refused, not rendered empty",
          i18n.language() == i18n.SOURCE_LANG
          and tr("保存") == "保存", f"{i18n.language()} / {tr('保存')}")

    i18n.set_language(i18n.SOURCE_LANG)   # leave the process as we found it
    bad = [c for c in _checks if not c[0]]
    print()
    if bad:
        print(f"FAIL — {len(bad)} of {len(_checks)} checks failed")
        for _ok, what, detail in bad:
            print(f"   {what}: {detail}")
        return 1
    print(f"PASS — {len(_checks)} checks: the translation machinery holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
