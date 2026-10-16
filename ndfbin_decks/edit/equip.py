"""Swap a slot's equipment.

This is the lowest-risk edit in the editor, and it mirrors what a mod author
does by hand in the project tree:

* **NDF source** — rewrite ``StrategicDecks.ndf`` so the deck references a
  different pack, e.g. ``~/Descriptor_StrategicPack_T72M_TCH_1`` becomes
  ``~/Descriptor_StrategicPack_T72M1_TCH_1``.  If no such pack exists, append
  one to ``StrategicPacks.ndf`` first.
* **Compiled ``.ndfbin``** — the same two cases.  Pack objects live *inside*
  ``Deck.ndfbin`` and carry **no export names** (measured: 5182 objects,
  0 exported; only the 2394 decks are exported).  ``DeckPackList`` entries are
  uniform ``obj_ref`` values, so a swap is a rewrite of one ``object_id``:

  ==========  ====================================================
  reuse       a pack with the wanted content already exists in the
              file -> just repoint this slot at it
  create      nothing matches -> append a ``TDeckPackDescriptor``
              object, then repoint
  ==========  ====================================================

The only difference from the source workflow is the lookup key.  NDF resolves
``~/Name`` by name; the compiled form has no pack names, so the editor indexes
packs by **content** — the tuple ``(UnitDescriptor, TransporterDescriptor,
ExperienceLevel, Number)`` plays the role the name suffix plays in the source.

``Number`` is always 1 inside AG Pawn decks — measured across three mods,
182384 / 184982 / 190195 slots, zero exceptions.  The 21 other values in the
file belong exclusively to the 128 skirmish decks, which are out of scope.  So a
newly created pack is written with ``Number = 1`` and needs no default choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import cndf_codec
from ..core.deckmodel import DeckFile, unit_leaf
from .quantity import Retiler
from ..i18n import tr

PACK = "TDeckPackDescriptor"


@dataclass(frozen=True)
class PackKey:
    """Content identity of a pack — the compiled stand-in for an NDF pack name."""

    unit: str | None
    transporter: str | None = None
    experience: object = None
    number: object = 1

    def label(self) -> str:
        bits = [unit_leaf(self.unit)]
        if self.transporter:
            bits.append(f"[{unit_leaf(self.transporter)}]")
        if self.experience not in (None, 1):
            bits.append(f"xp{self.experience}")
        if self.number not in (None, 1):
            bits.append(f"x{self.number}")
        return " ".join(bits)

    def describe(self) -> str:
        return (f"{unit_leaf(self.unit)}"
                f"{' + ' + unit_leaf(self.transporter) if self.transporter else ''}"
                f"{' xp' + str(self.experience) if self.experience not in (None, 1) else ''}")


@dataclass
class PackIndex:
    """Content-addressed lookup over the packs inside one deck file."""

    #: PackKey -> pack object ids (a key can appear more than once)
    by_key: dict[PackKey, list[int]] = field(default_factory=dict)
    #: pack object id -> its key
    key_of: dict[int, PackKey] = field(default_factory=dict)

    @classmethod
    def build(cls, deck: DeckFile) -> "PackIndex":
        idx = cls()
        for oid, props in deck._packs.items():
            key = cls.key_of_props(props)
            idx.by_key.setdefault(key, []).append(oid)
            idx.key_of[oid] = key
        return idx

    @staticmethod
    def key_of_props(props: dict) -> PackKey:
        return PackKey(
            unit=props.get("UnitDescriptor", {}).get("value"),
            transporter=props.get("TransporterDescriptor", {}).get("value"),
            experience=props.get("ExperienceLevel", {}).get("value"),
            number=props.get("Number", {}).get("value"),
        )

    def find(self, key: PackKey) -> int | None:
        """An existing pack object id for this content, or ``None``."""
        ids = self.by_key.get(key)
        return ids[0] if ids else None

    def find_all(self, key: PackKey) -> list[int]:
        return list(self.by_key.get(key, ()))

    def describe(self, key: PackKey) -> str:
        ids = self.by_key.get(key, [])
        return f"{key.describe()} -> {len(ids)} existing pack object(s)"

    def __len__(self) -> int:
        return len(self.key_of)

    def signature_count(self) -> int:
        return len(self.by_key)


# --------------------------------------------------------------------------
# The editor session
# --------------------------------------------------------------------------

@dataclass
class SwapResult:
    """Outcome of one equipment swap."""

    ok: bool
    deck: str
    slot: int
    before: PackKey | None = None
    after: PackKey | None = None
    #: True when an existing pack was reused, False when one had to be created
    reused: bool = False
    created_pack_id: int | None = None
    #: how many slots in the whole file point at the old pack object
    blast_radius: int = 0
    note: str = ""
    #: non-blocking advisories, so the GUI can report it the same way it
    #: reports a :class:`~ndfbin_decks.edit.quantity.RetileResult`
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if not self.ok:
            return f"FAIL {self.deck} slot {self.slot}: {self.note}"
        how = "reused an existing pack" if self.reused else "created a new pack"
        return (f"{self.deck} slot {self.slot}: "
                f"{self.before.describe() if self.before else '?'} -> "
                f"{self.after.describe() if self.after else '?'}  [{how}]")

    def summary(self) -> str:
        """Same reporting contract as ``RetileResult.summary``."""
        return self.describe()


class DeckEditor:
    """Collects edits against a decoded ``Deck.ndfbin`` and writes them back.

    Nothing touches the file until :meth:`save` is called, so a user can try a
    swap, look at the diff, and discard it.
    """

    def __init__(self, deck: DeckFile):
        self.deck = deck
        # Work **in place** on the decoded graph.  A copy would silently
        # diverge from other editors sharing the same ``DeckFile`` -- e.g.
        # :class:`~ndfbin_decks.edit.quantity.Retiler`, which rewrites
        # ``PackIndexUnitNumberList`` and ``DeckPackList`` on this very graph.
        # Two private copies meant the second ``save()`` overwrote the first.
        self._objects = deck.info["objects"]
        self._by_id = {o["id"]: o for o in self._objects}
        self.index = PackIndex.build(deck)
        #: decks this session wrote to (for the summary only — the graph itself
        #: is edited in place, so there is nothing to flush at save time)
        self._decks_touched: set[int] = set()
        #: appended objects, not yet in the graph
        self._appended: list[dict] = []
        #: built on demand — see :attr:`retiler`
        self._retiler: Retiler | None = None
        #: class ids discovered while appending
        self._new_classes: list[str] = []

    # -- lookup ------------------------------------------------------------
    def prop_index(self, obj: dict, name: str) -> int | None:
        for p in obj["properties"]:
            if p["property_name"] == name:
                return p["property_id"]
        return None

    def pool_of(self, deck_obj_id: int) -> list[int]:
        """Current slot pool for a deck.

        Read straight off the graph — no private override copy.  A cached
        override would go stale as soon as another editor resized the pool
        (:class:`~ndfbin_decks.edit.quantity.Retiler`), and the stale copy
        would then overwrite the fresh one at save time.
        """
        deck_obj = self._by_id.get(deck_obj_id)
        if deck_obj is None:
            return []
        pl = next((p for p in deck_obj["properties"]
                   if p["property_name"] == "DeckPackList"), None)
        if pl is None:
            return []
        return [x.get("object_id") for x in pl["value"].get("items", [])]

    def _write_pool(self, deck_obj_id: int, pool: list[int]) -> None:
        deck_obj = self._by_id[deck_obj_id]
        pl = next(p for p in deck_obj["properties"]
                  if p["property_name"] == "DeckPackList")
        template = pl["value"]["items"][0] if pl["value"].get("items") else {}
        items = [{
            "type_id": 3149642683, "type": "obj_ref", "reference_prefix": True,
            "object_id": pid,
            "class_id": self._class_id(PACK)} for pid in pool]
        pl["value"]["items"] = items
        # ``length`` must track ``items`` or the encoder rejects the file.
        pl["value"]["length"] = len(items)

    @property
    def retiler(self) -> Retiler:
        """The layout engine, sharing this very decoded graph.

        Lazy only because building the id map costs a pass over ~61k objects,
        and a caller that never edits quantities should not pay for it.
        """
        if self._retiler is None:
            self._retiler = Retiler(self.deck)
        return self._retiler

    def swap_entry(self, deck_object_id: int, ci: int, si: int, ei: int,
                   want: PackKey, *, allow_create: bool = True) -> SwapResult:
        """Change **one entry's** equipment — the only legal swap.

        An entry owns ``count`` consecutive slots that W3 requires to carry one
        pack, so the deck is re-laid from the substituted pack rather than
        patched slot by slot.  That keeps ``Σ(Number) == len(DeckPackList)``
        and makes a half-converted window unrepresentable.
        """
        res = SwapResult(ok=False, deck=self._name_of(deck_object_id),
                         slot=-1, after=want)
        pool = self.pool_of(deck_object_id)
        deck = next((b for b in self.deck.battalions()
                     if b.deck_object_id == deck_object_id), None)
        if deck is None:
            res.note = f"no deck object #{deck_object_id}"
            return res
        try:
            window = deck.companies[ci].platoons[si].windows[ei]
        except (IndexError, AttributeError):
            res.note = tr("条目 cg{ci}/sg{si}/e{ei} 不存在", ci=ci, si=si, ei=ei)
            return res
        res.slot = window.pack_index
        if not 0 <= window.pack_index < len(pool):
            res.note = (tr("条目指向槽位 {pack_index}，超出 {v1} 槽的池子",
                pack_index=window.pack_index,
                v1=len(pool),
            ))
            return res
        old_pack = pool[window.pack_index]
        res.before = self.pack_key(old_pack)

        if allow_create:
            new_pack, reused = self.ensure_pack(want)
            if new_pack < 0:
                res.note = "failed to create a pack object"
                return res
        else:
            new_pack = self.index.find(want)
            if new_pack is None:
                res.note = (f"no pack matches {want.describe()} and "
                            f"creation was not allowed")
                return res
            reused = True
        res.reused = reused
        if not reused:
            res.created_pack_id = new_pack

        laid = self.retiler.relay(deck_object_id,
                                  pack_edits={(ci, si, ei): new_pack})
        res.warnings.extend(laid.warnings)
        if not laid.ok:
            res.note = laid.note
            return res
        res.ok = True
        res.blast_radius = window.count
        self._decks_touched.add(deck_object_id)
        return res

    def set_entry_experience(self, deck_object_id: int, ci: int, si: int,
                             ei: int, level: int | None, *,
                             allow_create: bool = True) -> SwapResult:
        """Repoint one entry at the same unit carrying ``level``.

        ``ExperienceLevel`` is part of a pack's content signature, so this is
        not an in-place field write: the whole entry is aimed at the pack that
        carries the wanted level, appending one when the file has none.
        """
        return self._repoint_entry(deck_object_id, ci, si, ei,
                                   "experience", level, allow_create)

    def set_entry_transporter(self, deck_object_id: int, ci: int, si: int,
                              ei: int, unit: str | None, *,
                              allow_create: bool = True) -> SwapResult:
        """Repoint one entry at the same unit riding ``unit`` (``None`` drops)."""
        return self._repoint_entry(deck_object_id, ci, si, ei,
                                   "transporter", unit, allow_create)

    def _repoint_entry(self, deck_object_id: int, ci: int, si: int, ei: int,
                       field: str, value, allow_create: bool) -> SwapResult:
        """Resolve the wanted variant, then swap the entry onto it."""
        res = SwapResult(ok=False, deck=self._name_of(deck_object_id),
                         slot=-1)
        pool = self.pool_of(deck_object_id)
        deck = next((b for b in self.deck.battalions()
                     if b.deck_object_id == deck_object_id), None)
        if deck is None:
            res.note = f"no deck object #{deck_object_id}"
            return res
        try:
            window = deck.companies[ci].platoons[si].windows[ei]
        except (IndexError, AttributeError):
            res.note = tr("条目 cg{ci}/sg{si}/e{ei} 不存在", ci=ci, si=si, ei=ei)
            return res
        if not 0 <= window.pack_index < len(pool):
            res.note = tr("条目指向槽位 {pack_index}，越界", pack_index=window.pack_index)
            return res
        cur = self.pack_key(pool[window.pack_index])
        if cur is None:
            res.note = tr("该条目没有可解析的 pack")
            return res
        want = PackKey(
            unit=cur.unit,
            transporter=value if field == "transporter" else cur.transporter,
            experience=value if field == "experience" else cur.experience,
            number=cur.number)
        return self.swap_entry(deck_object_id, ci, si, ei, want,
                               allow_create=allow_create)

    def _name_of(self, obj_id: int) -> str:
        return self.deck.info["exports"].get(
            obj_id, f"deck#{obj_id}").rsplit("/", 1)[-1]

    def pack_key(self, pack_object_id: int) -> PackKey | None:
        obj = self._by_id.get(pack_object_id)
        return None if obj is None else PackIndex.key_of_props(
            {p["property_name"]: p["value"] for p in obj["properties"]})

    def find_deck(self, needle: str):
        return self.deck.find(needle)

    # -- pack resolution ------------------------------------------------
    #
    # There is deliberately **no** ``swap_slot`` here any more.  A pool slot
    # is not a unit you can edit: ``DeckPackList`` is a sequence of windows,
    # and entry ``(cg, sg, e)`` owns ``count`` consecutive slots that W3
    # requires to carry one and the same pack.  Rewriting a single slot in
    # place produced ``[T72_CMD_SOV, BM27, BM27]`` on the Berlin file — a
    # mixed window, illegal, and silently lossy, because the next re-tile
    # collapses a window onto its first slot and deletes the remainder.
    #
    # Equipment changes now go through ``Retiler.relay(pack_edits=...)``,
    # which re-emits the whole pool from each entry's pack.  See
    # :meth:`~ndfbin_decks.app.session.Session.swap_entry`.
    def ensure_pack(self, key: PackKey) -> tuple[int, bool]:
        """Resolve ``key`` to a pack object id, appending one if needed.

        Returns ``(pack_object_id, reused)``.  This is the single place where
        pack objects come into existence, shared by
        :meth:`~ndfbin_decks.app.session.Session.swap_entry` and by
        :class:`~ndfbin_decks.edit.structure.StructureEditor` (which needs to
        add a brand-new entry to a smart group).  Keeping one implementation
        means the ``IMPR`` trie growth and ``CLAS``/``PROP`` bookkeeping cannot
        drift apart between the two callers.
        """
        existing = self.index.find(key)
        if existing is not None:
            return existing, True
        created = self._append_pack(key)
        return (created, False) if created is not None else (-1, False)

    def _append_pack(self, key: PackKey) -> int | None:
        """Append a ``TDeckPackDescriptor`` built from ``key``.

        Field order mirrors the source files: ``ExperienceLevel``,
        ``TransporterDescriptor``, ``UnitDescriptor``, ``Number``.
        """
        new_id = len(self._objects) + len(self._appended)
        props: list[dict] = []
        pid = self._prop_id(PACK, "ExperienceLevel")
        if key.experience is not None and pid is not None:
            props.append({"property_id": pid, "property_name": "ExperienceLevel",
                          "value": {"type_id": 2, "type": "int32",
                                    "reference_prefix": False,
                                    "value": key.experience}})
        pid = self._prop_id(PACK, "TransporterDescriptor")
        if key.transporter and pid is not None:
            props.append({"property_id": pid, "property_name": "TransporterDescriptor",
                          "value": {"type_id": 2863311530, "type": "trans_ref",
                                    "reference_prefix": True,
                                    "index": self._trans_index(key.transporter),
                                    "value": key.transporter}})
        pid = self._prop_id(PACK, "UnitDescriptor")
        if key.unit and pid is not None:
            props.append({"property_id": pid, "property_name": "UnitDescriptor",
                          "value": {"type_id": 2863311530, "type": "trans_ref",
                                    "reference_prefix": True,
                                    "index": self._trans_index(key.unit),
                                    "value": key.unit}})
        pid = self._prop_id(PACK, "Number")
        if pid is not None:
            props.append({"property_id": pid, "property_name": "Number",
                          "value": {"type_id": 2, "type": "int32",
                                    "reference_prefix": False,
                                    "value": 1 if key.number is None else key.number}})

        obj = {"id": new_id, "offset": 0, "class_id": self._class_id(PACK),
               "class": PACK, "is_top_object": False, "properties": props}
        self._appended.append(obj)
        # Register in ``_by_id`` as well as the index: a slot may already point
        # at this pack before ``save()`` runs, and callers (or a GUI previewing
        # pending edits) must be able to read it back immediately.
        self._by_id[new_id] = obj

        # register so later swaps can reuse it
        self.index.by_key.setdefault(key, []).append(new_id)
        self.index.key_of[new_id] = key
        return new_id

    # -- schema helpers ----------------------------------------------------
    def _class_id(self, name: str) -> int:
        classes = self.deck.info["classes"]
        if name not in classes:
            classes.append(name)
            self._new_classes.append(name)
        return classes.index(name)

    def _prop_id(self, cls: str, field_name: str) -> int | None:
        for p in self.deck.info["properties"]:
            if p["name"] == field_name and p["class"] == cls:
                return p["id"]
        return None

    def _trans_index(self, path: str) -> int:
        """The ``IMPR`` trie node id that a ``trans_ref`` must point at.

        ⚠ The index is **not** a ``TRAN`` position.  ``TRAN`` is the pool of
        path *segments*; ``IMPR`` is a prefix trie whose nodes are indexed by
        ``object_id``, and node 0 is the ``$`` root.  Existing packs prove it:
        the first four store 0, 1, 2, 3 and resolve to ``$``, ``GFX``,
        ``Unit``, ``Descriptor_Unit_2K11_KRUG_DDR`` respectively.

        So a path resolves by walking ``IMPR`` to its **leaf node id**.  For an
        existing path that id is already in ``imports``; for a brand-new path the
        trie has to grow first, which :meth:`_doc_with_imports` does before the
        graph is written.  Return ``-1`` here and let the caller substitute the
        freshly allocated id.
        """
        imports = self.deck.info["imports"]
        for node_id, value in imports.items():
            if value == path:
                return node_id
        return -1

    # -- write -------------------------------------------------------------
    def pending_summary(self) -> dict:
        return {
            "decks_touched": len(self._decks_touched),
            "packs_appended": len(self._appended),
        }

    def save(self, path=None) -> bytes:
        """Rebuild the file and return the new bytes (the file is written too).

        Two write paths exist and they are not interchangeable:

        * **no packs appended** — only ``object_id`` values inside existing
          ``DeckPackList`` arrays changed, so ``rebuild_objects`` suffices.
        * **packs appended** — the object table itself grew.  That needs
          ``append_graph_objects``, which extends ``CLAS`` / ``PROP`` / ``CHNK``
          together and re-runs the full graph validation.
        """
        target = path or self.deck.path
        objects = self._objects + self._appended

        # A brand-new unit path has no node in the IMPR trie, so its trans_ref
        # cannot resolve.  Order matters:
        #   1. grow the trie and learn the new node ids
        #   2. stamp those ids into the pending pack objects
        #   3. append the objects (append_graph_objects validates as it writes)
        stage = self._grow_imports()
        self._stamp_new_node_ids(stage)

        new = stage["bytes"] if stage else self.deck.doc
        stage_doc = stage["doc"] if stage else self.deck.doc
        if not stage:
            stage_doc = self.deck.doc

        if not self._appended:
            decoded = dict(self.deck.info)
            decoded["objects"] = objects
            new = cndf_codec.rebuild_objects(stage_doc, decoded)
        else:
            # A deck that now references an appended pack must be patched in the
            # same graph that receives the new object, otherwise the reference
            # would dangle.  ``append_graph_objects`` re-serialises
            # ``decoded['objects']`` and then extends it with the new list, so
            # both halves go in together.
            full = dict(stage["info"] if stage else self.deck.info)
            full["objects"] = self._objects
            new = cndf_codec.append_graph_objects(
                stage_doc, full, self._appended)

        with open(target, "wb") as fh:
            fh.write(new)
        return new

    # -- import-table maintenance -----------------------------------------
    def _new_paths(self) -> set[str]:
        """Whole ``$/…`` paths this session introduced that the trie lacks."""
        known = set(self.deck.info["imports"].values())
        out: set[str] = set()
        for obj in self._appended:
            for p in obj["properties"]:
                v = p["value"]
                if v.get("type") == "trans_ref" and v.get("value") not in known:
                    out.add(v["value"])
        return out

    def _grow_imports(self):
        """Rebuild ``IMPR`` so newly referenced paths resolve.

        Returns ``{"bytes", "doc", "info", "new_ids"}`` or ``None`` when the
        session introduced no paths.  ``new_ids`` maps each new path to the trie
        node id it was assigned, which the caller then stamps into the objects.
        """
        wanted = self._new_paths()
        if not wanted:
            return None
        imports = dict(self.deck.info["imports"])
        assigned = {}
        for path in wanted:
            imports[len(imports)] = path
            assigned[path] = len(imports) - 1
        raw = cndf_codec.rebuild_sections(
            self.deck.doc,
            cndf_codec.build_reference_table_sections(
                self.deck.doc, imports, "IMPR"))
        doc, info = cndf_codec.decode(raw, "grown-imports")
        # The trie builder may allocate shared segments first, so recover the
        # real ids from the rebuilt table rather than trusting the guess.
        real = {v: k for k, v in info["imports"].items()}
        return {
            "bytes": raw,
            "doc": doc,
            "info": info,
            "new_ids": {p: real.get(p) for p in wanted},
        }

    def _stamp_new_node_ids(self, stage) -> None:
        """Write the freshly allocated trie ids into the pending objects."""
        if not stage:
            return
        for obj in self._appended:
            for p in obj["properties"]:
                v = p["value"]
                if v.get("type") != "trans_ref":
                    continue
                node = stage["new_ids"].get(v.get("value"))
                if node is not None:
                    v["index"] = node