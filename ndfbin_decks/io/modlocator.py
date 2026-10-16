"""WARNO mod discovery and deployment-path resolution.

Two kinds of directories matter and they are easy to confuse:

``project``  ``<WARNO>/Mods/<Name>/``            — the *authoring* tree.
             Holds plain NDF text sources plus ``GameData/Localisation/*.csv``.
             **A token appears in plaintext here** (e.g. ``Name = "PBJRGWGPAZ"``).

``deploy``   ``Saved Games/EugenSystems/WARNO/mod/<Name>/``
             — the ModGen workspace the game actually loads.
             Holds ``Gen/NDF/**/*.ndfbin`` (compiled) and
             ``Gen/Localisation/**/*.dic``.

Both hold a ``Config.ini``; only the deployed one carries the ``[Config]``
MD5 block used for mod compatibility checks.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from ..i18n import live, tr

# ``[Properties]`` -> ``Name`` value.  Kept deliberately tolerant: the deployed
# copies carry ``Name = AG Framework - Berlin 1989: The Encirclement`` and the
# project copies are often empty.
_NAME_RE = re.compile(r"^\s*Name\s*=\s*(.+?)\s*$", re.M)
# ``[Config]`` -> ``GFX/Deck=md5`` style entries.
_CONFIG_MD5_RE = re.compile(r"^([A-Za-z0-9_/]+)=([0-9a-fA-F]{32})\s*$", re.M)

#: Keys that may appear in the ``[Config]`` block.  A mod that ships more than
#: one of these touches more than just the deck tables (units, weapons, AI...).
NDF_SLOT_KEYS = (
    "GFX/Deck",
    "GFX/Division",
    "GFX/Pawn",
    "UI/BattleOrder",
    "GFX/Unit",
    "GFX/Weapon",
    "GFX/Skirmish",
    "GFX/Constantes",
)

#: WARNO's Steam application id.  The workshop content folder is keyed by it:
#: ``<steamapps>/workshop/content/1611600/<published_file_id>/``.
WORKSHOP_APP_ID = "1611600"

#: Where a mod's deployment came from.  Shown in the picker, and the reason a
#: clear-one-and-edit-the-other mistake stays visible.
SOURCE_DEPLOY = "deploy"      # ``Saved Games/.../mod/<Name>/`` — the local workspace
SOURCE_WORKSHOP = "workshop"  # ``<steamapps>/workshop/content/1611600/<id>/``
SOURCE_MANUAL = "manual"      # a folder the user opened by hand

#: Human labels for the picker.
SOURCE_LABELS = live(globals(), "SOURCE_LABELS", lambda: {
    SOURCE_DEPLOY: tr("本机"),
    SOURCE_WORKSHOP: tr("创意工坊"),
    SOURCE_MANUAL: tr("手动"),
})


class ModNotFound(FileNotFoundError):
    pass


@dataclass(frozen=True)
class ConfigInfo:
    """Parsed ``Config.ini``."""

    name: str
    path: Path
    version: int
    deck_format_version: int
    modgen_version: int
    md5: dict[str, str] = field(default_factory=dict)

    @property
    def is_workshop(self) -> bool:
        """A non-zero Steam ID means it came from the workshop."""
        text = self.path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^\s*ID\s*=\s*(\d+)", text, re.M)
        return bool(m and m.group(1) != "0")


def read_config(config_path: Path) -> ConfigInfo:
    text = config_path.read_text(encoding="utf-8", errors="replace")

    def _int(key: str, default: int) -> int:
        m = re.search(rf"^\s*{key}\s*=\s*(-?\d+)", text, re.M)
        return int(m.group(1)) if m else default

    m = _NAME_RE.search(text)
    name = m.group(1).strip() if m else config_path.parent.name

    # Only the block after ``[Config]`` counts as the MD5 manifest.
    md5: dict[str, str] = {}
    parts = re.split(r"^\s*\[", text, flags=re.M)
    for part in parts:
        if part.startswith("Config]"):
            md5 = dict(_CONFIG_MD5_RE.findall(part))
            break

    return ConfigInfo(
        name=name,
        path=config_path,
        version=_int("Version", 0),
        deck_format_version=_int("DeckFormatVersion", 0),
        modgen_version=_int("ModGenVersion", 0),
        md5=md5,
    )


def deploy_root() -> Path:
    """The ModGen workspace the game actually loads from.

    This lives under the *user profile*, not the install directory.
    """
    return (
        Path(os.environ.get("USERPROFILE", Path.home()))
        / "Saved Games" / "EugenSystems" / "WARNO" / "mod"
    )


def _steam_install_candidates() -> list[Path]:
    """Plausible Steam roots, best evidence first.

    The registry is the only source that is right on a machine that put Steam
    somewhere unusual, but it must not be a hard requirement: this runs on
    non-Windows dev boxes too and a missing ``winreg`` cannot be allowed to
    break discovery.  The literal paths are the common installs and cover the
    case where the registry key is absent.
    """
    out: list[Path] = []
    try:                                                # pragma: no cover - Windows only
        import winreg                                   # type: ignore

        for hive, key in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    for value in ("SteamPath", "InstallPath"):
                        try:
                            p = Path(winreg.QueryValueEx(handle, value)[0])
                        except OSError:
                            continue
                        if p.is_dir() and p not in out:
                            out.append(p)
            except OSError:
                continue
    except Exception:                                   # noqa: BLE001
        pass

    for guess in ("D:/Steam", "C:/Program Files (x86)/Steam",
                  "C:/Program Files/Steam", "C:/Steam", "E:/Steam",
                  "F:/Steam"):
        p = Path(guess)
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def steam_libraries() -> list[Path]:
    """Every ``steamapps`` directory Steam knows about.

    ``libraryfolders.vdf`` is the whole point: the Steam client usually lives
    on ``C:`` while the games — and therefore their workshop content — sit on
    another drive.  Hard-coding ``C:\\Program Files (x86)\\Steam`` misses those
    installs, which is exactly the case this function exists to handle.
    """
    seen: list[Path] = []
    for steam in _steam_install_candidates():
        apps = steam / "steamapps"
        if apps.is_dir() and apps not in seen:
            seen.append(apps)
        vdf = apps / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s*"([^"]+)"', text):
            # The VDF escapes backslashes; ``$`` in the replacement is literal
            # only if spelled as a plain string, hence the lambda-free form.
            lib = Path(m.group(1).replace("\\\\", "/")) / "steamapps"
            if lib.is_dir() and lib not in seen:
                seen.append(lib)
    return seen


def workshop_roots() -> list[Path]:
    """``<library>/workshop/content/1611600`` for every Steam library."""
    out: list[Path] = []
    for apps in steam_libraries():
        d = apps / "workshop" / "content" / WORKSHOP_APP_ID
        if d.is_dir() and d not in out:
            out.append(d)
    return out


def warno_root() -> Path:
    """The WARNO install that owns the Saved Games workspace.

    Public because the install tree is also where ModGen keeps its baseline
    data (``Mods/ModData/``), which is the only plaintext copy of the game's
    own unit definitions on disk.
    """
    return _warno_root()


def _warno_root() -> Path:
    """Locate the install whose ``Mods/`` tree holds the NDF sources."""
    saved = deploy_root()
    # ``Saved Games`` normally sits on another drive from the install, so check
    # every Steam library — including the ones only ``libraryfolders.vdf``
    # knows about — before falling back.
    for apps in steam_libraries():
        c = apps / "common" / "WARNO"
        if (c / "Mods").is_dir():
            return c
    for c in (Path("D:/Steam/steamapps/common/WARNO"),
              Path("C:/Program Files (x86)/Steam/steamapps/common/WARNO"),
              Path("C:/Program Files/Steam/steamapps/common/WARNO")):
        if (c / "Mods").is_dir():
            return c
    # No install found; the project tree is optional anyway, so return a
    # harmless placeholder instead of failing here.
    return saved


@dataclass
class ModPaths:
    """Resolved locations for one mod, project tree and/or deployment."""

    name: str
    deploy: Path | None = None
    project: Path | None = None
    config: ConfigInfo | None = None
    #: :data:`SOURCE_DEPLOY` / :data:`SOURCE_WORKSHOP` / :data:`SOURCE_MANUAL`
    source: str = SOURCE_DEPLOY
    #: what to show a human — the ``Config.ini`` ``Name`` when there is one
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.config.name if self.config else self.name

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    # -- convenience ------------------------------------------------------
    @property
    def deck_bin(self) -> Path:
        """Compiled ``Deck.ndfbin`` — the thing the game reads."""
        return self._deploy_gen() / "NDF" / "GFX" / "Deck.ndfbin"

    @property
    def division_bin(self) -> Path:
        return self._deploy_gen() / "NDF" / "GFX" / "Division.ndfbin"

    @property
    def battleorder_bin(self) -> Path:
        return self._deploy_gen() / "NDF" / "UI" / "BattleOrder.ndfbin"

    @property
    def localisation_gen(self) -> Path:
        return self._deploy_gen() / "Localisation" / "Localisation"

    def localisation_namespaces(self) -> list[str]:
        """Namespaces under ``Gen/Localisation/Localisation/`` holding ``*.dic``.

        A deployed AGF mod can carry several.  ``Core`` is the game's own base
        dictionary, not the mod's, so it is excluded — otherwise the editor
        would attribute vanilla entries to the mod.
        """
        d = self.localisation_gen_dir()
        if d is None:
            return []
        return [p.name for p in sorted(d.iterdir())
                if p.is_dir() and p.name != "Core" and any(p.glob("*.dic"))]

    def localisation_namespace(self) -> str | None:
        """Preferred namespace: the mod's own one with the most ``.dic`` bytes."""
        names = self.localisation_namespaces()
        d = self.localisation_gen_dir()
        if not names or d is None:
            return None
        best, best_n = None, -1
        for n in names:
            size = sum(f.stat().st_size for f in (d / n).glob("*.dic"))
            if size > best_n:
                best, best_n = n, size
        return best

    def localisation_gen_dir(self) -> Path | None:
        """``Gen/Localisation/Localisation`` or ``None`` when absent."""
        if self.deploy is None:
            return None
        gen = self.deploy / "Gen"
        if not gen.is_dir():
            return None
        d = gen / "Localisation" / "Localisation"
        return d if d.is_dir() else None

    def csv_dir(self) -> Path | None:
        """Project-side localisation inputs (``GameData/Localisation/<ns>/``).

        Returns the sub-directory that holds ``PLATOONS.csv`` when there is
        exactly one, otherwise the parent directory.
        """
        base = None
        if self.project is not None:
            base = self.project / "GameData" / "Localisation"
        if base is None or not base.is_dir():
            return None
        hits = [p for p in sorted(base.iterdir())
                if p.is_dir() and (p / "PLATOONS.csv").is_file()]
        if len(hits) == 1:
            return hits[0]
        return base if hits else None

    def has(self, slot: str) -> bool:
        """Does the ``[Config]`` block mention this data slot?"""
        return bool(self.config and slot in self.config.md5)

    def declared_slots(self) -> list[str]:
        return sorted(self.config.md5) if self.config else []

    def _deploy_gen(self) -> Path:
        if self.deploy is None:
            raise ModNotFound(f"Mod '{self.name}' has no deployed Gen/ directory")
        gen = self.deploy / "Gen"
        if not gen.is_dir():
            raise ModNotFound(f"Mod '{self.name}' has no Gen/ under {self.deploy}")
        return gen

    def readme(self) -> str:
        bits = [f"mod: {self.name}  [{self.source_label}]"]
        if self.label != self.name:
            bits.append(f"  display name   {self.label}")
        if self.config:
            bits.append(f"  Config.ini     {self.config.path}")
            bits.append(f"  DeckFormatVer  {self.config.deck_format_version}")
            bits.append(f"  Version        {self.config.version}")
            slots = self.declared_slots()
            bits.append(f"  data slots     {', '.join(slots) if slots else '(none)'}")
        if self.deploy:
            bits.append(f"  deployed       {self.deploy}")
        if self.project:
            bits.append(f"  project source {self.project}")
        return "\n".join(bits)


def _looks_like_mod(p: Path) -> bool:
    """A directory that is *one mod*, not a container of mods.

    Deliberately narrow: ``Config.ini`` or a ``Gen/`` folder.  Checking for a
    nested ``NDF/`` folder would also match ``<mod>/Gen/`` — which is a child
    of a mod, not a mod — and the "is this one mod or a folder of mods" test
    would then answer wrongly for every mod that ships compiled data.
    """
    try:
        return (p / "Config.ini").is_file() or (p / "Gen").is_dir()
    except OSError:
        return False


def _mod_at(d: Path, source: str, project: Path | None = None) -> ModPaths:
    """Build a :class:`ModPaths` for a directory that holds one deployment."""
    cfg = d / "Config.ini"
    return ModPaths(
        name=d.name,
        deploy=d,
        project=project,
        config=read_config(cfg) if cfg.is_file() else None,
        source=source,
    )


def _match(mod: ModPaths, only: str | None) -> bool:
    """``only`` matches the directory name, the display name or the workshop id."""
    if not only:
        return True
    n = only.lower()
    return n in mod.name.lower() or n in (mod.label or "").lower()


def discover(only: str | None = None) -> list[ModPaths]:
    """Every mod the editor can open, from **two** deployment roots.

    * the Saved Games ModGen workspace — mods authored or copied locally;
    * Steam workshop content, ``<library>/workshop/content/1611600/<id>/``.

    The workshop half matters because a downloaded mod is *not* copied into the
    Saved Games workspace: it is loaded from the workshop folder, keyed by its
    published file id.  Without it the editor could only ever see the user's
    own mods, and "why is my subscription not in the list" would be the first
    question every time.

    ``only`` filters by directory name, display name or workshop id
    (case-insensitive substring).
    """
    out: list[ModPaths] = []
    # Two roots can describe the same folder (a mod copied into the workspace
    # *and* subscribed); resolved paths keep the list free of duplicates.
    seen: set[Path] = set()

    root = _warno_root()
    droot = deploy_root()
    if droot.is_dir():
        for d in sorted(droot.iterdir()):
            try:
                if not d.is_dir() or d.name == "Config.ini":
                    continue
                project = None
                proj = root / "Mods" / d.name
                if proj.is_dir() and (proj / "GameData").is_dir():
                    project = proj
                mod = _mod_at(d, SOURCE_DEPLOY, project)
            except OSError:
                continue
            key = _resolved(d)
            if key is not None:
                seen.add(key)
            out.append(mod)

    for wroot in workshop_roots():
        for d in sorted(wroot.iterdir()):
            try:
                if not d.is_dir():
                    continue
                key = _resolved(d)
                if key is not None and key in seen:
                    continue
                if not _looks_like_mod(d):
                    continue
                mod = _mod_at(d, SOURCE_WORKSHOP)
            except OSError:
                continue
            if key is not None:
                seen.add(key)
            out.append(mod)

    if not droot.is_dir() and not workshop_roots():
        raise ModNotFound(
            f"No mod workspace at {droot}, and no WARNO workshop content found "
            f"under any Steam library"
        )
    return [m for m in out if _match(m, only)]


def _resolved(p: Path) -> Path | None:
    try:
        return p.resolve()
    except (OSError, ValueError):
        return None


def discover_in_folder(root: Path) -> list[ModPaths]:
    """Mods inside a user-chosen directory — the manual fallback.

    Accepts either **one** mod folder (it has ``Gen/`` or ``Config.ini``) or a
    **container** whose children are mods.  That covers every layout a user is
    likely to point at: a second Steam library, another machine's workspace, a
    folder of extracted mods, or a single mod copied off a USB stick.

    Children win over the folder itself, so pointing at a workspace root (which
    has its own ``Config.ini``) lists the mods inside it rather than the root.
    """
    root = Path(root)
    try:
        if not root.is_dir():
            return []
        children = [d for d in sorted(root.iterdir())
                    if d.is_dir() and _looks_like_mod(d)]
    except OSError:
        return []
    if children:
        return [_mod_at(d, SOURCE_MANUAL) for d in children]
    # A ``Mods/`` folder marks the game's own install root, which has a ``Gen/``
    # of its own and would otherwise be reported as a single mod called
    # "WARNO".  Excluding it keeps the common "oops, wrong folder" from
    # polluting the list with a mod that cannot be opened.
    if _looks_like_mod(root) and not (root / "Mods").is_dir():
        return [_mod_at(root, SOURCE_MANUAL)]
    return []


def resolve(name: str) -> ModPaths:
    """Resolve one mod by directory name, display name or workshop id."""
    hits = discover(only=name)
    exact = [m for m in hits if m.name.lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ModNotFound(f"No mod named '{name}'")
    raise ModNotFound(
        f"'{name}' is ambiguous: {[m.name for m in hits]}"
    )
