"""实体聚合视图：把「一辆坦克」的血量/装甲/速度收在一处。

**为什么不能给 TEntityDescriptor 做字段面板.** 实测（``tools/probe_refs4.py``）：
``Unit.ndfbin`` 里 4626 个 ``TEntityDescriptor``，每个身上只有 4 个属性——

======================  ====================================================
DescriptorId            guid，不可改
ModulesDescriptors      list，18 个 obj_ref 指向各种 Module
ClassNameForDebug       strg_ref，可读的调试名
World                   只有 936 个有
======================  ====================================================

也就是说**实体自己没有任何数值**。血量在 ``TBaseDamageModuleDescriptor``、
装甲在 ``TDamageModuleDescriptor`` + ``TBlindageProperties``、速度在
``TLandMovementModuleDescriptor`` / ``TGenericMovementModuleDescriptor``。

所以「实体视图」的本质是**顺着 ModulesDescriptors 把这几个模块的字段捞出来
放在一起**，而不是给实体本身做面板。

**共享是这里最危险的事.** 同一个 Module 会被很多实体共用——实测 ``#76`` 这样的
低 id 模块（``TBaseDamageModuleDescriptor``）被反复引用。改它等于改**所有**
共用它的实体。所以每一行都要标出「这个模块被 N 个实体共用」。

**分组是提示，不是过滤器.** 分组规则按属性名子串匹配（``blindage`` 归装甲、
``speed`` 归机动…），匹配不上的一律进「其他模块字段」。**没有任何字段会被丢掉**，
所以换一个 mod、换一个文件，视图不会因为它没见过的字段名而静默变空。
这也是本项目的既有原则：白名单会在换文件的那一刻失效。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from .refs import ENTITY_MODULES_FIELD

#: 判据来源见 :data:`refs.ENTITY_MODULES_FIELD`。
MODULES_FIELD = ENTITY_MODULES_FIELD

#: 分组规则：分组名 -> 属性名里出现这些子串就归进去（小写比较）。
#: 顺序即优先级，第一个命中的赢。
GROUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("生存 / 血量", (
        "maxphysicaldamages", "maxstundamages", "maxsuppressiondamages",
        "stundamagesregen", "suppressdamagesregen", "killwhendamagesreachmax",
        "damagelevels", "damagelevel", "revenge", "critical",
    )),
    ("装甲", (
        "blindage", "resistance", "explosivereactivearmor", "usetoparmor",
        "armor", "penetration",
    )),
    ("机动 / 速度", (
        "speed", "acceleration", "terrain", "pitch", "roll", "rotation",
        "yul", "turret", "brake", "steer",
    )),
    ("武器 / 射击", (
        "weapon", "ammunition", "fire", "reload", "shots", "salvo",
        "dispersion", "aiming", "suppressdamage", "nbsoldier",
    )),
    ("侦察 / 视野", (
        "vision", "optical", "scanner", "visibility", "detection",
        "identification", "range",
    )),
    ("隐蔽 / 电子战", (
        "stealth", "jammer", "ecm", "camouflage", "mimeti", "blind",
    )),
    ("后勤 / 补给", (
        "supply", "fuel", "upkeep", "production", "ressources", "commandpoint",
    )),
    ("乘员 / 步兵", (
        "soldier", "squad", "infantry", "crew", "transporter", "transportable",
    )),
)

OTHER_GROUP = "其他模块字段"

#: 这些字段名出现在聚合视图里会被标成「关键」，排在各组最前面。
#: 不是为了筛选，只是为了好看——它们都还在。
HIGHLIGHT = (
    "MaxPhysicalDamages", "MaxStunDamages", "MaxSuppressionDamages",
    "BlindageProperties", "ResistanceFront", "ResistanceSides",
    "ResistanceRear", "ResistanceTop", "ExplosiveReactiveArmor",
    "MaxSpeedInKmph", "DisplayRoadSpeedInKmph", "UpwardSpeedInKmph",
    "MaxAccelerationGRU", "SpeedBonusFactorOnRoad",
)


def group_of(prop_name: str) -> str:
    low = prop_name.lower()
    for title, keys in GROUP_RULES:
        if any(k in low for k in keys):
            return title
    return OTHER_GROUP


@dataclass
class EntityRow:
    """聚合视图里的一行。"""

    group: str
    name: str
    #: 拥有这个字段的模块条目 —— 它的 ``fields`` / ``refs`` 是**共享的**
    #: 那一个 ``ObjEntry``，所以在这里改就等于在原位改。
    entry: "object"
    module_id: int
    module_class: str
    module_title: str
    #: 有多少个实体共用这个模块。1 表示这条改动只影响当前实体。
    shared_by: int = 0
    #: "field" | "ref"
    kind: str = "field"
    #: True 表示字段确实存在但只读（例如模块在别的文件里）。
    readonly: bool = False

    @property
    def shared(self) -> bool:
        return self.shared_by > 1

    @property
    def warning(self) -> str:
        if not self.shared:
            return ""
        return f"这个模块被 {self.shared_by} 个实体共用，改它会影响全部"


@dataclass
class EntityModule:
    """实体引用的一个模块。"""

    module_id: int
    module_class: str
    title: str
    #: ``obj_ref`` -> 本文件，可编辑；``trans_ref`` -> 外部文件，只读
    kind: str = "obj"
    path: str = ""
    shared_by: int = 0
    editable_fields: int = 0


@dataclass
class EntityView:
    """一个实体的聚合视图。"""

    entity_id: int
    title: str
    modules: list[EntityModule] = dc_field(default_factory=list)
    rows: list[EntityRow] = dc_field(default_factory=list)
    #: 聚合视图能不能用（实体没有 ModulesDescriptors 时为 False）
    ok: bool = True
    note: str = ""

    def groups(self) -> dict[str, list[EntityRow]]:
        out: dict[str, list[EntityRow]] = {}
        for r in self.rows:
            out.setdefault(r.group, []).append(r)
        # 关键字段排前，其余保持 NDF 里的原始顺序
        for rows in out.values():
            rows.sort(key=lambda r: (r.name not in HIGHLIGHT,))
        ordered = {}
        for title, _ in GROUP_RULES:
            if title in out:
                ordered[title] = out[title]
        if OTHER_GROUP in out:
            ordered[OTHER_GROUP] = out[OTHER_GROUP]
        for title in out:                      # 防漏（未来改了规则表）
            ordered.setdefault(title, out[title])
        return ordered

    def key_rows(self) -> list[EntityRow]:
        """只挑关键字段，给「概览」用。"""
        return [r for r in self.rows if r.name in HIGHLIGHT]

    def groups_by_module(self):
        """分组 -> ``[(模块, 该模块贡献的行)]``。

        一个分组里的字段往往只来自两三个模块（血量的三个数都在
        ``TBaseDamageModuleDescriptor`` 上），所以把模块提成小标题、
        字段挂在它下面，比在每一行右侧重复一遍模块名清楚得多。
        """
        mod_of = {m.module_id: m for m in self.modules if m.kind == "obj"}
        out = {}
        for gname, rows in self.groups().items():
            buckets: dict[int, list[EntityRow]] = {}
            for r in rows:
                buckets.setdefault(r.module_id, []).append(r)
            out[gname] = [(mod_of.get(mid), rs) for mid, rs in buckets.items()]
        return out

    def shared_modules(self) -> list[EntityModule]:
        return [m for m in self.modules if m.shared_by > 1]

    def summary(self) -> str:
        obj_mods = [m for m in self.modules if m.kind == "obj"]
        ext = len(self.modules) - len(obj_mods)
        bits = [f"{len(self.modules)} 个模块"]
        if ext:
            bits.append(f"其中 {ext} 个在别的文件里（只读）")
        shared = self.shared_modules()
        if shared:
            bits.append(f"{len(shared)} 个模块是共用的")
        return "，".join(bits)


def _build_shared_lookup(nd):
    """``{模块 object_id: 引用它的实体数}``。

    用引用反向索引算——纯文件内可推导，不需要任何外部数据。
    """
    rev = nd.ref.reverse
    counts: dict[int, int] = {}
    for module_id, users in rev.items():
        n = sum(1 for owner_id, _ in users
                if nd.ref.class_of(owner_id) == "TEntityDescriptor")
        if n:
            counts[module_id] = n
    return counts


def build_entity_view(nd, entry, *, shared_lookup=None,
                      title: str | None = None) -> EntityView:
    """构造一个实体的聚合视图。

    Parameters
    ----------
    nd : NdfDocument
    entry : ObjEntry
        被选中的实体条目。
    shared_lookup
        预计算好的共享计数，避免每次切换对象都重算。GUI 里缓存一份。
    """
    view = EntityView(entity_id=entry.object_id,
                      title=title or entry.title
                      or nd.ref.label_of(entry.object_id))
    obj = nd._object_by_id(entry.object_id)
    if obj is None:
        view.ok = False
        view.note = f"找不到对象 #{entry.object_id}"
        return view

    mods_prop = next((p for p in obj["properties"]
                      if p["property_name"] == MODULES_FIELD), None)
    if mods_prop is None:
        view.ok = False
        view.note = (f"{entry.class_name} 没有 {MODULES_FIELD}，"
                     f"这不是一个实体对象，直接用下面的字段面板即可。")
        return view

    if shared_lookup is None:
        shared_lookup = _build_shared_lookup(nd)

    index = nd.ref
    for item in mods_prop["value"].get("items", []):
        t = item.get("type_id")
        if t == 0xBBBBBBBB:                                  # obj_ref
            mid = item.get("object_id")
            if mid is None or mid == 0xFFFFFFFF:
                continue
            mclass = index.class_of(mid)
            module_entry = _entry_of(nd, mclass, mid)
            shared = shared_lookup.get(mid, 0)
            view.modules.append(EntityModule(
                module_id=mid, module_class=mclass,
                title=index.label_of(mid), kind="obj",
                shared_by=shared,
                editable_fields=(len(module_entry.fields)
                                 + len(module_entry.refs))
                if module_entry else 0))
            if module_entry is None:
                continue
            for name, f in module_entry.fields.items():
                view.rows.append(EntityRow(
                    group=group_of(name), name=name, entry=module_entry,
                    module_id=mid, module_class=mclass,
                    module_title=index.label_of(mid),
                    shared_by=shared, kind="field"))
            for name, r in module_entry.refs.items():
                view.rows.append(EntityRow(
                    group=group_of(name), name=name, entry=module_entry,
                    module_id=mid, module_class=mclass,
                    module_title=index.label_of(mid),
                    shared_by=shared, kind="ref"))
        elif t == 0xAAAAAAAA:                                # trans_ref
            path = index.imports.get(item.get("index"), "")
            if not path:
                continue
            view.modules.append(EntityModule(
                module_id=-1, module_class="", title=index.imports.get(
                    item.get("index"), "").rsplit("/", 1)[-1],
                kind="path", path=path))
    return view


def _entry_of(nd, class_name: str, object_id: int):
    """在 ``nd`` 的条目表里按 id 找那个**共享的** ObjEntry。

    必须是同一个对象——聚合视图里改字段要改到原位上，复制一份就白改了。
    """
    for e in nd._by_class.get(class_name, ()):
        if e.object_id == object_id:
            return e
    return None


# --------------------------------------------------------------------------
# 哪些类配得上这个视图
# --------------------------------------------------------------------------

def looks_like_entity(nd, class_name: str) -> bool:
    """这个类是不是「有模块的实体」。

    **查 PROP 表而不是类里实际出现的字段.** ``ModulesDescriptors`` 是个 list，
    列表型字段不进 ``ClassInfo.fields``（改「第 N 项」没有可理解的语义），
    所以按字段表判断永远为假。PROP 表才是文件自己声明的权威清单——
    这也是本项目一贯的原则：能问文件就别写死。

    同样刻意**不按类名判断**：写死 ``TEntityDescriptor`` 会在 mod 改名或
    换文件时静默失效。
    """
    cache = getattr(nd, "_modules_declaring_classes", None)
    if cache is None:
        cache = {p["class"] for p in nd.decoded["properties"]
                 if p["name"] == MODULES_FIELD}
        nd._modules_declaring_classes = cache
    return class_name in cache
