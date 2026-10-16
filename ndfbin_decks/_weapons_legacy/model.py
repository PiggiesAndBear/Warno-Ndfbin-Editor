"""武器索引：把 Weapon.ndfbin 的对象图变成玩家看得懂的列表。

**问题.** 24168 个对象、57 个类，没有一个玩家能认得。真正想改的东西是
"2K11 的穿甲弹伤害 5改成 20"，而这个信息散在四层引用里::

    TWeaponManagerModuleDescriptor      # 3228 个，_ShortDatabaseName 可读
      .TurretDescriptorList[list]
        TTurretTwoAxisDescriptor        # 3323 个
          .MountedWeaponDescriptorList[list]
            TMountedWeaponDescriptor    # 8995 个
              .Ammunition[obj_ref]
                TAmmunitionDescriptor    # 1548 个  ← 数值都在这

只有 1548 个弹药，但有 8995 个挂载武器 —— 也就是说**多个武器共用同一种弹药**。
所以编辑必须以 *弹药* 为单位，回填时自动影响所有引用它的武器，否则玩家改了
一个数值却在游戏里发现三把枪一起变了却不知道为什么。

**为什么不按文件顺序展示.** ``decoded["objects"]`` 是扁平列表，object_id 递增，
相邻对象之间毫无语义关系。按弹药聚合才能给玩家一份"每行是一件能改的东西"的表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core import cndf_codec

#: 类名常量——集中在这里，避免散落的魔法字符串。
CLASS_MODULE = "TWeaponManagerModuleDescriptor"
CLASS_AMMO = "TAmmunitionDescriptor"

#: 属性名沿引用链的名字。
P_MODULE_NAME = "_ShortDatabaseName"
P_TURRETS = "TurretDescriptorList"
P_MOUNTED = "MountedWeaponDescriptorList"
P_AMMUNITION = "Ammunition"

#: 玩家真正关心的数值字段。``label`` 是界面上显示的名字。
#:
#: ``unit`` 只用于显示与合法性提示，不参与编码——NDF 里存的是裸数值。
#: ``kind`` 指��编辑器控件类型：float / int / bool。
FIELD_SPECS = (
    ("PhysicalDamages",        "物理伤害",       "float"),
    ("SuppressDamages",        "压制伤害",       "float"),
    ("MaximumRangeGRU",        "最大射程",       "float"),
    ("MinimumRangeGRU",        "最小射程",       "float"),
    ("TimeBetweenTwoShots",    "两发间隔(秒)",   "float"),
    ("TimeBetweenTwoSalvos",   "两轮间隔(秒)",   "float"),
    ("ShotsCountPerSalvo",     "每轮弹量",       "int"),
    ("SimultaneousShotsCount", "同时弹丸数",     "int"),
    ("SupplyCost",             "补给消耗",       "float"),
)

FIELD_NAMES = tuple(spec[0] for spec in FIELD_SPECS)
FIELD_LABELS = {spec[0]: spec[1] for spec in FIELD_SPECS}
FIELD_KINDS = {spec[0]: spec[2] for spec in FIELD_SPECS}

#: NDF 标量 type_id -> Python 类型。用于判断某个叶子能不能进数值编辑框。
SCALAR_TYPES = {1: int, 2: int, 3: int, 4: int, 5: float, 6: float,
                0x18: int, 0x19: int, 0x13: int}


def resolve_text(value, strings):
    """把 strg_ref / trans / pair 引用解成可读文本，非引用返回 ``None``。"""
    if not isinstance(value, dict):
        return None
    t = value.get("type_id")
    if t in (7, 0x1C) and "index" in value:
        i = value["index"]
        return strings[i] if 0 <= i < len(strings) else f"<str#{i}>"
    if t == 0x22:
        k = resolve_text(value.get("key"), strings)
        v = resolve_text(value.get("value"), strings)
        if k is not None or v is not None:
            return f"{k}={v}"
    return None


def collect_refs(value):
    """收集一个属性值里引用的全部 ``object_id``，穿透 list / pair 嵌套。

    注意不能只读 ``value["object_id"]``：list 型字段（type_id 0x11）的引用
    在 ``items`` 里，标量引用才在顶层。这是本项目最容易写错的地方。
    """
    ids = []

    def walk(v):
        if isinstance(v, dict):
            if v.get("type_id") == 0xBBBBBBBB:
                oid = v.get("object_id")
                if oid != 0xFFFFFFFF:
                    ids.append(oid)
            for item in v.get("items", []):
                walk(item)
            for key in ("key", "value"):
                walk(v.get(key))
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return ids


def iter_scalars(value):
    """递归产出属性值里的全部标量叶子 dict。"""
    if isinstance(value, dict):
        if value.get("type_id") in SCALAR_TYPES and "value" in value:
            yield value
        for item in value.get("items", []):
            yield from iter_scalars(item)
        for key in ("key", "value"):
            if isinstance(value.get(key), dict):
                yield from iter_scalars(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from iter_scalars(item)


@dataclass
class AmmoSlot:
    """一个可编辑的数值字段。

    ``leaf`` 直接指向 ``decoded["objects"]`` 里的原位dict——改它就是改数据，
    不需要任何回写映射。这是整个编辑闭环最关键的设计。
    """

    name: str
    leaf: dict
    original: float | int | bool

    @property
    def label(self) -> str:
        return FIELD_LABELS.get(self.name, self.name)

    @property
    def kind(self) -> str:
        return FIELD_KINDS.get(self.name, "float")

    @property
    def dirty(self) -> bool:
        return self.leaf["value"] != self.original

    def reset(self) -> None:
        self.leaf["value"] = self.original


@dataclass
class AmmoEntry:
    """一种弹药 = 一个 ``TAmmunitionDescriptor``。"""

    object_id: int
    slots: dict[str, AmmoSlot] = field(default_factory=dict)
    #: 使用这种弹药的武器显示名，按字典序去重。
    weapons: list[str] = field(default_factory=list)
    #: 该弹药对象的 ``Name``（list of string），用来给表格一个好认的标题。
    display: str = ""

    @property
    def dirty(self) -> bool:
        return any(s.dirty for s in self.slots.values())

    @property
    def dirty_names(self) -> list[str]:
        return [s.label for s in self.slots.values() if s.dirty]

    def reset(self) -> None:
        for s in self.slots.values():
            s.reset()

    def get(self, name):
        slot = self.slots.get(name)
        return slot.leaf["value"] if slot else None


class WeaponFile:
    """打开的 Weapon.ndfbin。"""

    def __init__(self, path: Path, doc, decoded):
        self.path = Path(path)
        self.doc = doc
        self.decoded = decoded
        self.strings = decoded["strings"]
        self.ammo: list[AmmoEntry] = []
        self._by_id: dict[int, AmmoEntry] = {}
        self._build()

    # ---- 加载 ----

    @classmethod
    def load(cls, path) -> "WeaponFile":
        path = Path(path)
        doc, decoded = cndf_codec.decode(path.read_bytes(), source=path)
        return cls(path, doc, decoded)

    def _build(self):
        objects = self.decoded["objects"]
        by_id = {o["id"]: o for o in objects}

        # 第一遍：把每个弹药对象变成一个可编辑条目
        for obj in objects:
            if obj["class"] != CLASS_AMMO:
                continue
            entry = AmmoEntry(object_id=obj["id"])
            entry.display = self._ammo_name(obj)
            for prop in obj["properties"]:
                name = prop["property_name"]
                if name not in FIELD_NAMES:
                    continue
                # 取第一个标量叶子：这些字段都是单值 float/int
                for leaf in iter_scalars(prop["value"]):
                    if isinstance(leaf.get("value"), (int, float, bool)):
                        entry.slots[name] = AmmoSlot(
                            name=name, leaf=leaf, original=leaf["value"])
                        break
            self.ammo.append(entry)
            self._by_id[obj["id"]] = entry

        # 第二遍：反向索引——谁在用这种弹药
        for obj in objects:
            if obj["class"] != CLASS_MODULE:
                continue
            label = self._module_name(obj)
            for ammo_id in self._walk_to_ammo(obj, by_id):
                entry = self._by_id.get(ammo_id)
                if entry is not None and label not in entry.weapons:
                    entry.weapons.append(label)

        for entry in self.ammo:
            entry.weapons.sort()
            if not entry.display:
                entry.display = self._fallback_name(entry)

    def _module_name(self, obj) -> str:
        for prop in obj["properties"]:
            if prop["property_name"] == P_MODULE_NAME:
                text = resolve_text(prop["value"], self.strings)
                if text:
                    return _pretty(text)
        return f"module#{obj['id']}"

    def _ammo_name(self, obj) -> str:
        """弹药自己的 ``Name``——通常是 list of string。"""
        for prop in obj["properties"]:
            if prop["property_name"] != "Name":
                continue
            parts = [resolve_text(v, self.strings) for v in iter_scalars(prop["value"])]
            parts = [p for p in parts if p]
            if parts:
                return " / ".join(parts)
        return ""

    def _fallback_name(self, entry: AmmoEntry) -> str:
        if entry.weapons:
            return f"{entry.weapons[0]}"
        return f"ammo#{entry.object_id}"

    def _walk_to_ammo(self, obj, by_id):
        """沿 炮塔 -> 挂载武器 -> 弹药 走引用链，收集可达的弹药 id。

        深度上限 4 层，且每层都做 visited 保护：真实数据里存在
        ``TTurretBombardierDescriptor`` 这类更深的结构，不能假设链是直的。
        """
        seen = set()
        found = []
        frontier = [obj["id"]]
        for _ in range(4):
            nxt = []
            for oid in frontier:
                node = by_id.get(oid)
                if node is None:
                    continue
                for prop in node["properties"]:
                    if prop["property_name"] == P_AMMUNITION:
                        found.extend(self._by_id and
                                     [i for i in collect_refs(prop["value"])
                                      if i in self._by_id])
                    elif prop["property_name"] in (P_TURRETS, P_MOUNTED):
                        for ref in collect_refs(prop["value"]):
                            if ref not in seen:
                                seen.add(ref)
                                nxt.append(ref)
            frontier = nxt
            if not frontier:
                break
        return found

    # ---- 查询 ----

    @property
    def dirty_count(self) -> int:
        return sum(1 for a in self.ammo if a.dirty)

    def entries(self) -> list[AmmoEntry]:
        """列表顺序：改过的排最前，然后按显示名。"""
        return sorted(self.ammo,
                      key=lambda a: (not a.dirty, a.display.lower()))

    def search(self, text: str) -> list[AmmoEntry]:
        """按弹药名 / 武器名过滤，空字符串返回全部。"""
        if not text.strip():
            return self.entries()
        needle = text.strip().lower()
        return [a for a in self.entries()
                if needle in a.display.lower()
                or any(needle in w.lower() for w in a.weapons)]

    def reset_all(self) -> None:
        for entry in self.ammo:
            entry.reset()

    def replacements(self) -> dict:
        """交给 writer 的 section 替换表。"""
        from ..core import writer
        repl = writer.sections_of(self.doc)
        repl["OBJE"] = cndf_codec.encode_objects(self.decoded["objects"])
        return repl

    def dry_run(self):
        from ..core import writer
        return writer.write_file(self.path, self.doc, self.replacements(),
                                 dry_run=True)

    def save(self):
        from ..core import writer
        return writer.write_file(self.path, self.doc, self.replacements())


def _pretty(name: str) -> str:
    """``WeaponDescriptor_2K11_KRUG_DDR`` -> ``2K11 KRUG DDR``。

    玩家在游戏里看到的是 ``2K11 KRUG DDR``，界面上就该长这样；
    数据库名只作为 tooltip 的补充信息。
    """
    out = name
    for prefix in ("WeaponDescriptor_", "WeaponDescriptor"):
        if out.startswith(prefix):
            out = out[len(prefix):]
            break
    return out.replace("_", " ").strip() or name