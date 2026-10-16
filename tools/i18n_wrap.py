"""Wrap every Chinese literal in the sources in a ``tr()`` call.

Run it with no arguments first (a report only), read the diff, then ``--write``.

The point of doing this with a script instead of by hand is not speed, it is
that the script can be *checked*: ~295 call sites — plain strings plus 88
implicitly-concatenated f-string groups — is far past the point where hand
editing can be trusted, and one missed ``f`` prefix or one spliced quote is a
bug that only shows up in a language nobody on the team reads.

Pass A — the strings
--------------------

* A plain string literal becomes ``tr("…")``.  The original source span is
  reused verbatim, so no escaping can be mangled.
* An f-string becomes ``tr("…{name}…", name=expr)``, with implicitly
  concatenated pieces **merged into one key** first:
  ``f"影响 {a} 个营" f"，{b} 个连。"`` becomes the single sentence
  ``"影响 {a} 个营，{b} 个连。"`` rather than two fragments.  Fragment-level keys
  would hand a translator ``"，{b} 个连。"`` with no way to render it.
* Field names come from the expression where it can be read off (``name``,
  ``self.count``) and fall back to ``v0``/``v1`` for anything cleverer.  The
  tool never guesses a meaning.

Pass B — the module-level constants
-----------------------------------

A module-level constant is evaluated once, at import time, so ``tr()`` there
would freeze it in the startup language.  Those sites get wrapped in
``live(globals(), "NAME", lambda: …)`` instead, which re-runs the factory on
every language change.

What it refuses to touch
------------------------

* **Docstrings** — prose for whoever reads the code, not for the user.
* **Log lines** — a diagnostic is easier to read when it stays in one language.
  Matched on the *receiver* as well as the method name, because
  ``QMessageBox.critical`` is a dialog the user reads, not a log line.
* Anything a translation function already owns.

Verification
------------

Every rewritten file is re-parsed and checked to hold every Chinese string it
held before — as itself for a plain string, as a substring for a merged
f-string.  If that fails the file is not written.  It is a weak proof (it
cannot see a string that moved to the wrong place) but it catches the two
failures that actually happen: a string dropped and a string re-escaped.
"""

from __future__ import annotations

import argparse
import ast
import keyword
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_METHODS = {"debug", "info", "warning", "warn", "error",
               "exception", "critical", "log"}
#: Only a call *on something that looks like a logger* counts as a log line.
#: Matching on the method name alone would swallow ``QMessageBox.critical``.
LOGGER_NAMES = {"log", "logger", "_log", "_logger", "logging"}
OWNERS = {"tr", "_", "trplural", "gettext", "live"}
#: Beyond this, an f-string's fields are broken onto their own lines.
WIDTH = 96


def _cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _line_starts(data: bytes) -> list[int]:
    starts = [0]
    for line in data.split(b"\n"):
        starts.append(starts[-1] + len(line) + 1)
    return starts


def _span(node: ast.AST, starts: list[int]) -> tuple[int, int]:
    """Byte range of ``node``'s source.  ``col_offset`` is a UTF-8 byte offset."""
    return (starts[node.lineno - 1] + node.col_offset,
            starts[node.end_lineno - 1] + node.end_col_offset)


def _indent_of(data: bytes, starts: list[int], lineno: int) -> int:
    line = data[starts[lineno - 1]:starts[lineno] - 1]
    return len(line) - len(line.lstrip())


def _quote(text: str) -> bytes:
    out = (text.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return ('"' + out + '"').encode("utf-8")


def _field_name(node: ast.AST, used: set[str], seq: int) -> str:
    """A placeholder name for ``node``'s expression, safe to reuse in a format."""
    name = node.id if isinstance(node, ast.Name) else (
        node.attr if isinstance(node, ast.Attribute) else f"v{seq}")
    while name in used or keyword.iskeyword(name) or not name.isidentifier():
        name = f"{name}_{seq}"
    return name


def _spec_text(node: ast.AST) -> str:
    """The ``:>8`` part of a replacement field, as plain text."""
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant)
                       and isinstance(v.value, str))
    return ""


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in LOG_METHODS:
        return False
    recv = func.value
    tail = recv.attr if isinstance(recv, ast.Attribute) else (
        recv.id if isinstance(recv, ast.Name) else "")
    return tail.lower() in LOGGER_NAMES


def _preorder(node: ast.AST):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _preorder(child)


def _skip_set(tree: ast.Module) -> set[int]:
    skip: set[int] = set()

    def mark(node: ast.AST) -> None:
        skip.add(id(node))
        for child in ast.iter_child_nodes(node):
            mark(child)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                skip.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id in OWNERS)
                or (isinstance(node.func, ast.Attribute)
                    and node.func.attr in OWNERS)
                or _is_log_call(node)):
            for arg in node.args:
                mark(arg)
    return skip


def _rewrite_joined(node: ast.JoinedStr, data: bytes, starts: list[int],
                    indent: int) -> tuple[bytes, list[str]]:
    parts: list[tuple[bool, str]] = []      # (is_literal, text)
    args: list[bytes] = []
    used: set[str] = set()
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append((True, value.value))
        elif isinstance(value, ast.FormattedValue):
            expr = value.value
            name = _field_name(expr, used, len(args))
            used.add(name)
            a, b = _span(expr, starts)
            spec = _spec_text(value.format_spec) if value.format_spec else ""
            parts.append((False, "{" + name + (":" + spec if spec else "")
                          + "}"))
            args.append(f"{name}=".encode("ascii") + data[a:b])

    # Doubling a literal brace is only right when something actually calls
    # .format() — a key with no fields is served as-is, and would show "{{{".
    key = "".join(text.replace("{", "{{").replace("}", "}}") if literal
                  and args else text for literal, text in parts)
    head = b"tr(" + _quote(key)
    if not args:
        return head + b")", [key]
    flat = head + b", " + b", ".join(args) + b")"
    if indent + len(flat) <= WIDTH:
        return flat, [key]
    pad = b" " * (indent + 4)
    broken = b"\n".join([head + b","] + [pad + a + b"," for a in args]
                        + [b" " * indent + b")"])
    return broken, [key]


def _edits_for_strings(data: bytes, starts: list[int], tree: ast.Module):
    skip = _skip_set(tree)
    edits: list[tuple[int, int, bytes]] = []
    keys: list[str] = []
    originals: list[str] = []
    done: set[int] = set()
    for node in _preorder(tree):
        if id(node) in skip or id(node) in done:
            continue
        if isinstance(node, ast.JoinedStr):
            pieces = [v for v in node.values
                      if isinstance(v, ast.Constant)
                      and isinstance(v.value, str)]
            if not any(_cjk(p.value) for p in pieces):
                continue
            # An f-string's literal pieces report the span of the *whole*
            # f-string (CPython quirk), so without this the same bytes would be
            # rewritten twice — once as the f-string, once as its piece.
            for inner in _preorder(node):
                done.add(id(inner))
            a, b = _span(node, starts)
            call, new = _rewrite_joined(node, data, starts,
                                        _indent_of(data, starts, node.lineno))
            edits.append((a, b, call))
            keys.extend(new)
            for piece in pieces:
                if _cjk(piece.value):
                    originals.append(piece.value)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and _cjk(node.value)):
            a, b = _span(node, starts)
            edits.append((a, b, b"tr(" + data[a:b] + b")"))
            keys.append(node.value)
            originals.append(node.value)
    return edits, keys, originals


def _module_constants(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Module-level assignments whose value now holds a ``tr(...)`` call."""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            # ``COUNTRY_NAMES: dict[str, str] = {...}`` is annotated, and that
            # is no reason for it to stay frozen in one language.
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or value is None:
            continue
        for sub in ast.walk(value):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "tr"):
                out.append((target.id, node.value))
                break
    return out


def _apply(data: bytes, edits) -> bytes:
    """Splice ``edits`` in reverse order so earlier offsets stay valid."""
    out = bytearray(data)
    for a, b, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        out[a:b] = rep
    return bytes(out)


def verify(before: bytes, after: bytes, originals: list[str]) -> None:
    """Every Chinese text present before must still be present after."""
    texts = [n.value for n in ast.walk(ast.parse(after))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    joined = "\n".join(texts)
    missing = [t for t in originals if t not in joined]
    if missing:
        raise ValueError(f"{len(missing)} Chinese strings vanished, first:"
                         f" {missing[0]!r}")
    ast.parse(before)


def import_prefix(path: Path) -> bytes:
    rel = path.resolve().relative_to(ROOT).parts
    if rel[0] == "main.py":
        return b"ndfbin_decks."
    return b".." if len(rel) == 3 else b"."


def _import_tail(lines: list[bytes]) -> int:
    tree = ast.parse(b"\n".join(lines))
    return max((n.end_lineno for n in tree.body
                if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)


def process(path: Path, write: bool):
    data = path.read_bytes()
    try:
        tree = ast.parse(data)
    except SyntaxError as exc:
        return [], f"SKIP  {path.name}: {exc}"

    starts = _line_starts(data)
    edits, keys, originals = _edits_for_strings(data, starts, tree)
    if not edits:
        return [], None
    plain = sum(1 for e in edits
                if data[e[0]:e[0] + 1] in (b'"', b"'", b'b', b'r'))
    joined = len(edits) - plain
    out = _apply(data, edits)

    # Pass B, on the result: the constants can only be located once pass A has
    # put a tr() call inside them.
    starts2 = _line_starts(out)
    live_edits = []
    for name, value in _module_constants(ast.parse(out)):
        a, b = _span(value, starts2)
        live_edits.append((a, b, b'live(globals(), "'
                           + name.encode("utf-8")
                           + b'", lambda: ' + out[a:b] + b")"))
    out = _apply(out, live_edits)

    lines = out.split(b"\n")
    lines.insert(_import_tail(lines), b"from " + import_prefix(path)
                 + (b"i18n import live, tr" if live_edits
                    else b"i18n import tr"))
    out = b"\n".join(lines)
    try:
        verify(data, out, originals)
    except (ValueError, SyntaxError) as exc:
        return [], f"FAIL  {path.name}: {exc}"

    rel = path.resolve().relative_to(ROOT).as_posix()
    note = (f"{'WRITE' if write else 'would '} {rel:34s} "
            f"{plain:3d} 串 + {joined:3d} f串"
            + (f" + {len(live_edits)} 常量" if live_edits else "")
            + f" -> {len(keys):3d} 键")
    if write:
        path.write_bytes(out)
    return keys, note


def main() -> int:
    ap = argparse.ArgumentParser(description="wrap Chinese literals in tr()")
    ap.add_argument("--write", action="store_true",
                    help="rewrite the files (default is a report only)")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args()

    files = [Path(p).resolve() for p in args.paths] or sorted(
        list((ROOT / "ndfbin_decks").rglob("*.py")) + [ROOT / "main.py"])
    files = [f for f in files if "i18n" not in f.parts and f.exists()]

    total: list[str] = []
    for path in files:
        keys, note = process(path, args.write)
        if note:
            print(note)
        total.extend(keys)
    print(f"\n合计 {len(total)} 处调用，去重后 {len(set(total))} 条待译")
    return 0


if __name__ == "__main__":
    sys.exit(main())
