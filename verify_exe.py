# -*- coding: utf-8 -*-
"""Prove a built EXE actually works before it is allowed to become the release.

    python verify_exe.py            # verifies dist_<ver>/
    python verify_exe.py 0.2

A GUI built with ``--windowed`` never exits on its own, so the usual
"``subprocess.run`` and check the return code" tells you nothing: it waits
forever and then reports a timeout.  What this does instead:

1. launches the EXE in a **throwaway working directory**, with the data
   directory pointed inside it (see :data:`ndfbin_decks.paths.HOME_ENV`) — so
   the run cannot touch the real user profile and the log it produces is
   isolated and readable;
2. waits for the startup line to appear in that log;
3. asserts four things: the log exists, it names the expected version, it
   contains no error, and the process is still alive;
4. hands it a real ``.ndfbin`` and checks it survives opening it — startup
   alone never runs the codec, the schema or the reference index, so it cannot
   tell you the product works, only that the imports resolved;
5. kills the process tree and reports the **measured startup time**, which is
   the number that decides whether a one-file build is acceptable.

Step 2 is why ``main.py`` writes a version header.  "The process started" is
not evidence: with its payload missing an app starts perfectly and silently
skips whatever needed the payload.  The version string proves both that it got
as far as the GUI and that the number compiled into the bundle is the one this
script expected.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ndfbin_decks import APP_NAME, APP_TITLE      # noqa: E402
from ndfbin_decks import paths                    # noqa: E402

#: Generous: this polls a one-file build, which unpacks its payload to a fresh
#: temporary directory on every launch.
STARTUP_DEADLINE = 120.0


def read_version() -> str:
    import re
    src = (ROOT / "ndfbin_decks" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', src, re.M).group(1)


def kill_tree(pid: int) -> None:
    """Kill the process *and its children*.

    A one-file EXE is a bootloader that unpacks itself and then spawns the real
    process, so terminating the pid we hold would leave the actual GUI running
    forever with nothing to shut it down.
    """
    # ``text=True`` alone decodes with the locale codec, and a Chinese Windows
    # answers ``taskkill`` in GBK — which raises inside subprocess' reader
    # thread and buries the real output.  Nothing here reads the text anyway.
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_for(proc, log: Path, needle: str, budget: float) -> tuple[bool, str]:
    """Poll the run's log until ``needle`` shows up or the budget runs out."""
    deadline = time.time() + budget
    text = ""
    while time.time() < deadline:
        if log.is_file():
            text = log.read_text(encoding="utf-8", errors="replace")
            if needle in text:
                return True, text
        if proc.poll() is not None:
            break                       # died early; the caller reports that
        time.sleep(0.25)
    if log.is_file():
        text = log.read_text(encoding="utf-8", errors="replace")
    return False, text


def run_once(exe: Path, platform: str) -> dict:
    run_dir = Path(tempfile.mkdtemp(prefix="ndfbin_exe_"))
    home = run_dir / "home"
    env = dict(os.environ)
    env[paths.HOME_ENV] = str(home)
    env["QT_QPA_PLATFORM"] = platform
    log = home / "app.log"

    started = time.time()
    proc = subprocess.Popen([str(exe)], cwd=str(run_dir), env=env)
    # Two waits, because they prove different things *and* because the split
    # between them is the number that matters when deciding whether a one-file
    # build is pleasant to use: everything before the first line is the
    # bootloader unpacking ~90 MB of payload to a temporary directory, and
    # everything between the lines is our own import cost.
    found, text = _wait_for(proc, log, "Starting ", STARTUP_DEADLINE)
    t_boot = time.time() - started
    marker_ver = (text.split("Starting ", 1)[1].split("\n", 1)[0]
                  if found else None)
    _, text = _wait_for(proc, log, "GUI loaded", 30.0)
    t_gui = time.time() - started
    elapsed = t_gui
    time.sleep(2.0)                     # let the GUI settle before judging it
    alive = proc.poll() is None
    if log.is_file():
        text = log.read_text(encoding="utf-8", errors="replace")
    kill_tree(proc.pid)
    time.sleep(1.0)
    return {"dir": run_dir, "log": log, "text": text, "marker": marker_ver,
            "elapsed": elapsed, "boot": t_boot, "gui": t_gui,
            "alive": alive, "rc": proc.poll()}


def find_sample() -> Path | None:
    """A real ``.ndfbin`` to open, or ``None`` when there is none to find.

    Searched, never pinned: mods get renamed and workshop folders get
    renumbered, and a hard-coded path turns into a check that fails while
    nothing is actually wrong.
    """
    try:
        from ndfbin_decks.io import modlocator
        from ndfbin_decks.ndfedit import modpicker
        mods = modlocator.discover()
    except Exception:                              # noqa: BLE001
        return None
    for mod in mods:
        try:
            files = modpicker.scan_ndfbin(mod)
        except Exception:                          # noqa: BLE001
            continue
        if files:
            return Path(files[0].path)
    return None


def open_smoke(exe: Path, sample: Path) -> dict:
    """Launch the EXE **with a file** and check it survives opening it.

    The startup run above never touches the load path, so a bundle missing the
    codec, the schema or the interface table would pass it and still fail on
    the first file a user double-clicks — which is the entire product.  Here
    the path is handed over on the command line and the app has to reach the
    end of it: the evidence is that the process is still alive *and* that no
    ``crash.log`` was written, which is what :func:`main.install_excepthook`
    does with anything raised on the event loop.
    """
    run_dir = Path(tempfile.mkdtemp(prefix="ndfbin_open_"))
    home = run_dir / "home"
    env = dict(os.environ)
    env[paths.HOME_ENV] = str(home)
    env["QT_QPA_PLATFORM"] = "offscreen"      # no window flashes for this one
    log = home / "app.log"

    started = time.time()
    proc = subprocess.Popen([str(exe), str(sample)], cwd=str(run_dir), env=env)
    found, text = _wait_for(proc, log, "GUI loaded", STARTUP_DEADLINE)
    # ``load_path`` runs off a zero-delay timer once the window is up, and a
    # large file takes a while to decode, so the wait is deliberate rather than
    # optimistic.
    time.sleep(10.0)
    alive = proc.poll() is None
    if log.is_file():
        text = log.read_text(encoding="utf-8", errors="replace")
    crash = home / "crash.log"
    crash_text = (crash.read_text(encoding="utf-8", errors="replace")
                  if crash.is_file() else "")
    kill_tree(proc.pid)
    time.sleep(1.0)
    return {"sample": sample, "loaded": found, "elapsed": time.time() - started,
            "alive": alive, "crash": crash_text, "text": text,
            "rc": proc.poll()}


def _visible_window_titles() -> list[str]:
    """Titles of every visible top-level window, via ``GetWindowText``."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out: list[str] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                   wintypes.LPARAM)

    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                out.append(buf.value)
        return True

    user32.EnumWindows(proc_type(visit), 0)
    return out


def check_native_title(exe: Path, title: str) -> str | None:
    """Launch on the real Windows platform and read the native window title.

    This is the only honest way to answer "what does the taskbar hint say".
    An offscreen run has no window for the shell to read, and Qt is documented
    to append the application display name to a window's title — which would
    turn the hint into ``<title> — <name>``.  ``startswith`` is used for the
    search precisely so that a suffix is caught rather than missed.

    A window does appear on screen for a few seconds while this runs.
    """
    run_dir = Path(tempfile.mkdtemp(prefix="ndfbin_title_"))
    env = dict(os.environ)
    env[paths.HOME_ENV] = str(run_dir / "home")
    env.pop("QT_QPA_PLATFORM", None)        # the real shell, a real window
    proc = subprocess.Popen([str(exe)], cwd=str(run_dir), env=env)
    found = None
    deadline = time.time() + 90
    while time.time() < deadline and proc.poll() is None:
        found = next((t for t in _visible_window_titles()
                      if t.startswith(title)), None)
        if found:
            break
        time.sleep(0.5)
    kill_tree(proc.pid)
    time.sleep(1.0)
    return found


def shell_icon(exe: Path, out: Path) -> tuple[bool, str]:
    """Save the icon the *shell* sees for this EXE, and prove it is not the default.

    Reads it back through ``QFileIconProvider`` rather than trusting the source
    ``.ico``: PyInstaller rewrites the icon into an ``RT_ICON`` resource, and
    that round trip is the only thing that decides what the taskbar actually
    draws.

    Two traps here, both of which produced a wrong answer before:

    * **The offscreen platform has no shell.**  Under
      ``QT_QPA_PLATFORM=offscreen`` this call quietly returns Qt's built-in
      fallback theme, whose first entry is a generic document glyph — so it
      "succeeds" while telling you nothing about the EXE.  The platform is
      therefore cleared, and a window may flash while this runs.
    * **A missing icon is not an error.**  If the resource were absent the call
      still returns the generic file icon, so the result is compared against
      the icon of a file that certainly has none.  Identical output means the
      EXE has no icon of its own.
    """
    try:
        os.environ.pop("QT_QPA_PLATFORM", None)
        from PySide6.QtCore import QFileInfo
        from PySide6.QtGui import QImage
        # ``QFileIconProvider`` lives in QtWidgets, so it needs a full
        # ``QApplication``; a ``QGuiApplication`` is not enough and the call
        # raises rather than returning an empty icon.
        from PySide6.QtWidgets import QApplication, QFileIconProvider
        if QApplication.instance() is not None:
            return False, ("a Qt application was already created — the shell "
                           "icon cannot be read through it")
        QApplication([])
        provider = QFileIconProvider()
        icon = provider.icon(QFileInfo(str(exe)))
        if icon.isNull():
            return False, "the shell returned no icon at all"
        size = max(icon.availableSizes(), key=lambda s: s.width())
        got = icon.pixmap(size)

        blank = Path(tempfile.mkdtemp(prefix="ndfbin_noicon_")) / "no.icox"
        blank.write_bytes(b"\0" * 32)
        default = provider.icon(QFileInfo(str(blank))).pixmap(size)

        def pixels(pm):
            img = pm.toImage().convertToFormat(QImage.Format_RGBA8888)
            return bytes(img.constBits())

        if pixels(got) == pixels(default):
            return False, ("it is the shell's generic \"unknown file\" icon — "
                           "the EXE carries no icon resource")
        got.save(str(out))
        return True, (f"{size.width()}x{size.height()} "
                      f"(of {len(icon.availableSizes())} sizes)")
    except Exception as exc:                       # noqa: BLE001
        return False, f"could not read it: {exc}"


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    ver = argv[0] if argv else read_version()
    exe = ROOT / f"dist_{ver}" / f"{APP_NAME} v{ver}.exe"
    if not exe.is_file():
        print(f"FAILED: no EXE at {exe}")
        return 1
    print(f"verifying {exe}")
    print(f"  size {exe.stat().st_size:,} bytes")
    print(f"  md5  {hashlib.md5(exe.read_bytes()).hexdigest()}")

    result = run_once(exe, "offscreen")
    log, text = result["log"], result["text"]

    print("\n  --- assertions ---")
    problems = []
    print(f"  [{'OK ' if log.is_file() else 'FAIL'}] a log was written at all"
          f"  ({log})")
    if not log.is_file():
        problems.append("no log produced — the EXE did not get as far as "
                        f"main(); exit code {result['rc']}")
    print(f"  [{'OK ' if result['marker'] else 'FAIL'}] it reports its version "
          f" {result['marker']!r}")
    if not result["marker"]:
        problems.append("no startup line — see the log above")
    elif f"v{ver}" not in result["marker"]:
        problems.append(f"the bundle reports {result['marker']!r}, expected "
                        f"v{ver}")
    bad = [ln for ln in text.splitlines()
           if "ERROR" in ln.upper() or "Traceback" in ln]
    print(f"  [{'OK ' if not bad else 'FAIL'}] no errors in the log"
          f"  ({len(bad)} bad lines)")
    problems += [f"log: {ln}" for ln in bad[:5]]
    loaded = "GUI loaded" in text
    print(f"  [{'OK ' if loaded else 'FAIL'}] the frozen module graph resolved"
          f"  (codec + dialogs + editors)")
    if not loaded:
        problems.append("the bundled modules did not all import — the running "
                        "process may just be an error dialog")
    print(f"  [{'OK ' if result['alive'] else 'FAIL'}] still running after "
          f"startup")
    if not result["alive"]:
        problems.append(f"process exited during startup (code {result['rc']})")
    print(f"  startup to GUI reached: {result['elapsed']:.1f}s"
          f"   (window shown, mod scan starting)")
    print(f"      of which {result['boot']:.1f}s is the one-file bootloader"
          f" unpacking, {result['gui'] - result['boot']:.1f}s is our imports")

    print("\n  --- opening a real file, which is the whole point ---")
    sample = find_sample()
    if sample is None:
        print("  --    no .ndfbin found on this machine, so the load path "
              "was not exercised")
    else:
        print(f"  sample: {sample}")
        opened = open_smoke(exe, sample)
        print(f"  [{'OK ' if opened['loaded'] else 'FAIL'}] the GUI started "
              f"({opened['elapsed']:.1f}s including the open)")
        if not opened["loaded"]:
            problems.append("the GUI never reached 'GUI loaded' when handed "
                            "a file to open")
        print(f"  [{'OK ' if opened['alive'] else 'FAIL'}] still running after "
              f"opening it")
        if not opened["alive"]:
            problems.append(f"the EXE exited while opening {sample.name} "
                            f"(code {opened['rc']})")
        clean = not opened["crash"]
        print(f"  [{'OK ' if clean else 'FAIL'}] nothing raised on the "
              f"event loop")
        if not clean:
            head = opened["crash"].strip().splitlines()
            problems.append("opening a file raised: "
                            + (head[-1] if head else "see crash.log"))
            print("        " + "\n        ".join(head[-6:]))

    print("\n  --- the window the shell will draw for it ---")
    title = check_native_title(exe, APP_TITLE)
    print(f"  [{'OK ' if title is not None else 'FAIL'}] a window titled after "
          f"APP_TITLE appeared")
    print(f"  [{'OK ' if title == APP_TITLE else 'FAIL'}] the native title "
          f"is exactly APP_TITLE  -> {title!r}")
    if title is None:
        problems.append("no window carrying the application title appeared")
    elif title != APP_TITLE:
        problems.append(f"the native title is {title!r}, not {APP_TITLE!r} — "
                        f"something is appending to it")

    print("\n  --- the icon the shell will actually draw ---")
    icon_out = ROOT / f"build_{ver}" / "exe-icon.png"
    icon_out.parent.mkdir(parents=True, exist_ok=True)
    ok_icon, detail = shell_icon(exe, icon_out)
    print(f"  [{'OK ' if ok_icon else 'FAIL'}] the EXE carries its own icon"
          f"  [{detail}]")
    if ok_icon:
        print(f"        extracted -> {icon_out}")
    else:
        problems.append(f"icon: {detail}")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        print(f"  log kept at {log}")
        return 1
    print("\nPASS — candidate is good; promote it with:")
    print(f'  cp "dist_{ver}/{exe.name}" "dist/{exe.name}"')
    print(f'  md5sum "dist_{ver}/{exe.name}" "dist/{exe.name}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
