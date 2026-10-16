"""弹药视图：给 ``TAmmunitionDescriptor`` 一个玩家能认出来的标题。

**问题的由来.** 第一版直接把 ``_ShortDatabaseName``（``2K11 KRUG DDR``）当标题，
用户指出这是**装备名不是弹药名**——这是对的。同一门炮可能配多种弹药，
反过来多种弹药也可能被不同装备共用（实测一种弹药被 605 把武器引用），
用装备名当标题必然张冠李戴。

**弹药自己的Name 字段用不了.** ``TAmmunitionDescriptor.Name`` 的类型是
``loc_hash``（``type_id == 0x1d``，8 字节哈希），不是字符串：

.. code-block:: text

    Name          t=0x001d hex=cc7739d752499c08
    Caliber       t=0x001d hex=e4339110488e4d03

要还原成文本必须反查游戏的本地化词典。已实测：Test mod 下 12 个``.dic``
确实是 hash -> UTF-16 文本的映射，但表头是**哈希表**而非顺序表，
且 1548 个弹药 Name 一个都没命中（Test mod 的词典里没有弹药条目）。
本体词典在 ``Data\\PC\\<build>\\*.dat`` 里，已加密。

⇒ **本阶段无法就地还原弹药真名。** 硬凑一个假名字比老实显示 ID 更糟——
玩家会以为那就是游戏里的名字。策略：

1. 标题用**弹药自身的可辨识特征**：弹种（ProjectileType）+ GUID 前缀
2. 副标题列出**引用它的装备**，并明确标注这是装备名
3. 界面顶部说明「弹药真名保存在游戏词典中，本工具暂不解析」

这样标题不会张冠李戴，玩家也能定位到自己要改的那一条。
"""

from __future__ import annotations

from .schema import NdfDocument, collect_refs, resolve_text

AMMO_CLASS = "TAmmunitionDescriptor"
MODULE_CLASS = "TWeaponManagerModuleDescriptor"
P_MODULE_NAME = "_ShortDatabaseName"
P_TURRETS = "TurretDescriptorList"
P_MOUNTED = "MountedWeaponDescriptorList"
P_AMMUNITION = "Ammunition"

#: ``ProjectileType`` 的实测枚举（1548 个弹药里的实际取值）。
#: 语义来自 Eugen 的 ``EProjectileType``，名称按常见英文译法给出，
#: 未在官方文档中逐条核实——所以界面标注为「推测」。
PROJECTILE_TYPES = {
    0: "Unspecified",
    1: "Shell",          # 857个，最常见
    2: "Rocket",         # 257
    3: "Missile",        # 71
    4: "Bomb",           # 3
    5: "AA Missile",     # 345
}


def projectile_label(value) -> str:
    if value is None:
        return "未知弹种"
    return PROJECTILE_TYPES.get(int(value), f"弹种#{int(value)}")


def _guid_of(obj) -> str:
    """取 ``DescriptorId`` GUID 的前 8 位十六进制。

    这是弹药在 mod 内唯一稳定的身份标识——即使真名解析不出来，
    玩家也能靠它和别的工具/日志对上号。
    """
    for prop in obj.get("properties", []):
        if prop["property_name"] == "DescriptorId":
            hexs = prop["value"].get("value_hex")
            if hexs:
                return hexs[:8].upper()
    return ""


def _pretty(name: str) -> str:
    for prefix in ("WeaponDescriptor_", "WeaponDescriptor"):
        if name.startswith(prefix):
            return name[len(prefix):].replace("_", " ").strip() or name
    return name


class AmmoTitles:
    """给弹药条目生成 (标题, 副标题)，并统计共用情况。"""

    def __init__(self, nd: NdfDocument):
        self.nd = nd
        self.decoded = nd.decoded
        self.strings = decoded_strings = nd.decoded["strings"]
        self._ammo = nd._by_class.get(AMMO_CLASS, [])
        #: object_id -> 使用它的装备名列表
        self.used_by: dict[int, list[str]] = {e.object_id: [] for e in self._ammo}
        self._index()

    def _index(self):
        by_id = {o["id"]: o for o in self.decoded["objects"]}
        ammo_ids = set(self.used_by)
        for obj in self.decoded["objects"]:
            if obj["class"] != MODULE_CLASS:
                continue
            label = self._module_label(obj)
            for aid in self._walk(obj, by_id):
                if aid in ammo_ids and label not in self.used_by[aid]:
                    self.used_by[aid].append(label)
        for names in self.used_by.values():
            names.sort()

    def _module_label(self, obj) -> str:
        for prop in obj["properties"]:
            if prop["property_name"] == P_MODULE_NAME:
                text = resolve_text(prop["value"], self.strings)
                if text:
                    return _pretty(text)
        return f"module#{obj['id']}"

    def _walk(self, obj, by_id) -> list[int]:
        """沿 炮塔 -> 挂载武器 -> 弹药 走引用链。"""
        found: list[int] = []
        seen: set[int] = set()
        frontier = [obj["id"]]
        for _ in range(4):
            nxt = []
            for oid in frontier:
                node = by_id.get(oid)
                if node is None:
                    continue
                for prop in node["properties"]:
                    if prop["property_name"] == P_AMMUNITION:
                        found.extend(i for i in collect_refs(prop["value"]))
                    elif prop["property_name"] in (P_TURRETS, P_MOUNTED):
                        for ref in collect_refs(prop["value"]):
                            if ref not in seen:
                                seen.add(ref)
                                nxt.append(ref)
            frontier = nxt
            if not frontier:
                break
        return found

    def apply(self) -> None:
        """把标题写进 ``NdfDocument`` 的条目里。"""
        by_id = {o["id"]: o for o in self.decoded["objects"]}
        ammo_ids = set(self.used_by)
        for entry in self._ammo:
            obj = by_id.get(entry.object_id)
            ptype = None
            if obj:
                for prop in obj["properties"]:
                    if prop["property_name"] == "ProjectileType":
                        ptype = prop["value"].get("value")
                        break
            guid = _guid_of(obj) if obj else ""
            names = self.used_by.get(entry.object_id, [])

            bits = [projectile_label(ptype)]
            if guid:
                bits.append(guid)
            entry.title = " · ".join(bits)

            if names:
                shown = "、".join(names[:4])
                more = f" 等 {len(names)} 种装备" if len(names) > 4 else ""
                entry.subtitle = f"装备：{shown}{more}"
            else:
                entry.subtitle = "没有被任何装备引用"
            entry.ref_count = len(names)


def load_with_ammo_titles(path) -> NdfDocument:
    """打开 Weapon.ndfbin 并给弹药套上标题。"""
    nd = NdfDocument.load(path)
    if AMMO_CLASS in nd.class_names:
        AmmoTitles(nd).apply()
    return nd