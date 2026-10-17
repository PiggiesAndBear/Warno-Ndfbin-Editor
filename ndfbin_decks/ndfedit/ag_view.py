# -*- coding: utf-8 -*-
"""将军模式牌组（AG pawn）的属性视图 —— 把模块属性投影成可筛选的字段。

**为什么需要这一层。** ``Pawn.ndfbin`` 里的一个营是一个
``TEntityDescriptor``，它自己几乎不带数值：行动点、移动方式、部队类型、
炮兵支援半径全都挂在 ``ModulesDescriptors`` 指向的**模块对象**上，而那些
模块是顶层的、被大量共用的对象。于是：

* 通用的「按字段筛对象」在这里**筛不到任何东西** —— pawn 身上没有这些字段；
* 改一个模块的属性会波及所有共用它的营（实测行动点模块被 **1637** 个营共用）。

本模块把这两件事一起解决：把模块属性**投影**成 pawn 的字段（形状与
``ObjEntry.fields`` 一致），于是筛选、结果表、批量改都能直接复用
:mod:`ndfedit.query`，一行不用改；同时把每个模块的**被引用次数**带出来，
写入层据此决定「原地改」还是「先克隆再改」。

实测依据（柏林 mod，2268 个 pawn / 18,285 个对象）：

============================  ========  ==========  ======
字段                           覆盖      取值域      共享
============================  ========  ==========  ======
``InitialActionPoint``        2265/2268     3 种     47 ★★
``_ShortDatabaseName``（移动） 2265/2268     6 种    378 ★★
``BackgroundTexture``（图标）  2265/2268    17 种      1 ★
``BattleSupportRadiusInAPCase`` 582/2268     6 种    206 ★★
``BattleRole``                1169/2268     3 种    206 ★★
``MotherCountry``             2268/2268    14 种      1 ★
============================  ========  ==========  ======

★★ = 被大量共用，改它必须先克隆；★ = 每营一份，可原地改。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dc_field

from .schema import NdfDocument, is_reference

#: 将军模式营的入口类。
PAWN_CLASS = "TEntityDescriptor"

#: 模块的挂载点。
MODULES_FIELD = "ModulesDescriptors"

#: ``DeckIdentifier`` 段 0 的两种取值。
#: 设施（机场、补给站）也带战略属性，但**不是营**，名称里也没有国家段。
KIND_UNIT = "pion"
KIND_FACILITY = "AGF"

#: 部队类型图标的宿主模块类。
ICON_HOST = "TStrategicLabelModuleDescriptor"

#: 图标值的前缀。实测 ``Values`` 是两个元素：
#: ``[Texture_STRATEGIC_RTS_H_x, Texture_STRATEGIC_x]`` —— 同一类型的两个变体，
#: 不是两种类型。去掉前缀就是类型名（``howitzer`` / ``apc`` / ``Armor_heavy`` …）。
ICON_PREFIXES = ("Texture_STRATEGIC_RTS_H_", "Texture_STRATEGIC_")


def _prop(obj: dict, name: str) -> dict | None:
    """对象上的一个属性值字典，没有则 ``None``。"""
    for p in obj.get("properties", ()):
        if p["property_name"] == name:
            return p["value"]
    return None


def _text(obj: dict, name: str) -> str | None:
    v = _prop(obj, name)
    if isinstance(v, dict):
        s = v.get("value")
        if isinstance(s, str):
            return s
    return None


def _ref_id(obj: dict, name: str) -> int | None:
    v = _prop(obj, name)
    if isinstance(v, dict) and is_reference(v) is not None:
        got = v.get("object_id")
        return got if isinstance(got, int) else None
    return None


def _module_ids(obj: dict) -> list[int]:
    """``ModulesDescriptors`` 里的 object_id 列表（按原顺序）。"""
    v = _prop(obj, MODULES_FIELD)
    if not isinstance(v, dict) or "items" not in v:
        return []
    out = []
    for it in v.get("items") or ():
        if isinstance(it, dict) and is_reference(it) is not None:
            got = it.get("object_id")
            if isinstance(got, int):
                out.append(got)
    return out


# ---------------------------------------------------------------------------
# 投影表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attr:
    """一个「用户概念 → 宿主模块 → 字段」的投影。

    ``kind`` 决定怎么读、以及界面给什么控件：

    ``float`` / ``int``    直接取标量值
    ``bool``               直接取
    ``str``                取 strg_ref 的可读文本
    ``icon``               obj_ref → 目标对象的 ``Values`` 列表 → 去掉前缀的类型名
    """

    key: str                       # 内部键，也是 PawnView.values 的键
    label: str                     # 界面显示名
    module: str                    # 宿主模块类
    field: str                     # 模块对象上的属性名
    kind: str = "float"
    #: 界面上的补充说明（为什么这个字段值得注意）
    note: str = ""


#: 用户会来改/看的属性。顺序就是结果表的列顺序 ——
#: 前四个是移交文档 §1 列的「需求」，其余是「顺带可做」。
ATTRS: tuple[Attr, ...] = (
    Attr("action_points", "行动点", "TActionPointsModuleDescriptor",
         "InitialActionPoint", "float", "初始 AP"),
    Attr("movement", "移动方式", "TStrategicMovementModuleDescriptor",
         "_ShortDatabaseName", "str", "机械化/牵引/空中…"),
    Attr("icon", "部队类型", "TStrategicLabelModuleDescriptor",
         "BackgroundTexture", "icon", "战略地图图标"),
    Attr("support_radius", "支援半径", "TStrategicBattleModuleDescriptor",
         "BattleSupportRadiusInAPCase", "float", "炮兵支援，单位 AP"),
    Attr("battle_role", "战斗角色", "TStrategicBattleModuleDescriptor",
         "BattleRole", "int", "整数，语义待游戏内验证"),
    Attr("ap_recovery", "AP 恢复", "TActionPointsModuleDescriptor",
         "ActionPointRecoveryPerTurn", "int", "每回合"),
    Attr("ap_produced", "生产兵 AP", "TActionPointsModuleDescriptor",
         "NbInitialActionsPointsForProducedPawn", "int", ""),
    Attr("max_speed", "速度", "TStrategicMovementModuleDescriptor",
         "MaxSpeed", "float", "战略地图移动"),
    Attr("fatigue", "疲劳上限", "TStrategicFatigueModuleDescriptor",
         "MaxFatigue", "int", "实测全为 8"),
    Attr("score", "分数", "TDeckModuleDescriptor", "Score", "int",
         "实测全为 1"),
    Attr("country", "国家", "TTypeUnitModuleDescriptor", "MotherCountry",
         "str", "结构化字段"),
)

#: 名称维度 —— 从 ``DeckIdentifier`` 切出来的三个虚拟字段。
NAME_KEYS = ("name_country", "parent", "unit")


@dataclass
class PawnView:
    """一个营在专用页里的样子。"""

    entity_id: int
    #: ``ClassNameForDebug``，如 ``Pawn_pion_BEL_11Sq``
    export_name: str = ""
    #: ``TDeckModuleDescriptor.DeckIdentifier``，如 ``pion_BEL_11Sq``。
    #: 这是 deck ↔ pawn 的连接键，**只读**。
    identifier: str = ""
    #: 段 0：``pion``（部队）/ ``AGF``（设施）
    kind: str = ""
    #: ``MotherCountry`` —— 游戏真正读的阵营字段（2268/2268 覆盖）。
    #: ⚠ 柏林 mod 里它与名字段 1 有 **139/2268** 处不一致，且成片出现
    #: （``pion_BEL_*`` 写着 ``US``、``pion_FR_*`` 写着 ``CAN``），
    #: 是复制模板没改留下的。**不要拿它当「国家前缀」用。**
    country: str = ""
    #: 名字段 1 —— 用户说的「国家前缀」就是它。成片命名里可信的那一个。
    name_country: str = ""
    #: 末段 = 营名
    unit: str = ""
    #: 中间段 = 上级编制链（师 / 团 / …）
    parents: tuple[str, ...] = ()
    #: 投影出来的属性值：``Attr.key -> value``，缺失时**不出现**这个键
    values: dict = dc_field(default_factory=dict)
    #: 模块类名 -> object_id（写入层要用）
    modules: dict = dc_field(default_factory=dict)
    #: 模块类名 -> 该模块对象被多少个营共用（1 = 独有，可原地改）
    sharing: dict = dc_field(default_factory=dict)

    @property
    def is_unit(self) -> bool:
        """机场/补给站这些不是营，名字是 ``AGF_<hash>``。"""
        return self.kind == "pion"

    def best_parent(self) -> str:
        """用于「上级编制」下拉的那一层。

        层级深度不固定（3~6 段），这里取**最靠近营的那一层**
        （``parents[-1]``）—— 它是最细的一级编制，与「我想改某个团下的营」
        这个真实意图最接近。
        """
        return self.parents[-1] if self.parents else ""


def parse_identifier(ident: str) -> tuple[str, str, str, tuple[str, ...]]:
    """``pion_BEL_1DI_12Linie`` → ``('pion', 'BEL', '12Linie', ('1DI',))``。

    实测段数 **2~6 不等**（编制层级深度不同），所以**不能按固定下标切**：

    ======  ========  ====================================
    段数    个数      形态
    ======  ========  ====================================
    2       58        ``AGF_<hash>`` —— 机场/补给站，不是营
    3       244       ``pion_<国家>_<营>`` 直属
    4       811       ``pion_<国家>_<师>_<营>``
    5       1141      ``pion_<国家>_<师>_<团>_<营>``
    6       11        ``pion_<国家>_<军>_<师>_<团>_<营>``
    ======  ========  ====================================

    稳定规律：段 0 = 类型、段 1 = 国家、**末段 = 营**、中间 = 上级链。

    ``AGF_<hash>``（机场/补给站）只有两段，第二段是哈希不是国家 —— 那时
    国家留空，而不是把一个 16 进制串当成国家塞进下拉框。
    """
    parts = ident.split("_")
    if len(parts) < 3:
        return (parts[0] if parts else "", "", parts[-1] if parts else "", ())
    return (parts[0], parts[1], parts[-1], tuple(parts[2:-1]))


def _read_attr(attr: Attr, module: dict, by_id: dict) -> object | None:
    """从一个模块对象上读出这个投影属性的值。"""
    if attr.kind == "icon":
        tgt_id = _ref_id(module, attr.field)
        tgt = by_id.get(tgt_id) if tgt_id is not None else None
        if tgt is None:
            return None
        vals = _prop(tgt, "Values")
        items = (vals or {}).get("items") or ()
        for it in items:
            if not isinstance(it, dict):
                continue
            s = it.get("value")
            if not isinstance(s, str):
                continue
            for prefix in ICON_PREFIXES:
                if s.startswith(prefix):
                    return s[len(prefix):]
            return s
        return None

    if attr.kind in ("str",):
        return _text(module, attr.field)

    v = _prop(module, attr.field)
    if not isinstance(v, dict):
        return None
    val = v.get("value")
    if attr.kind == "bool":
        return bool(val) if val is not None else None
    if attr.kind == "int":
        try:
            return int(val)
        except (TypeError, ValueError):
            return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def reference_counts(nd: NdfDocument) -> Counter:
    """每个 object_id 被引用多少次（共享度）。

    必须在模块对象上算 —— 「这个值被 1638 个营共用」是**写入前唯一的
    安全依据**，算错就等于误伤上千个营。
    """
    counts: Counter = Counter()
    for o in nd.decoded["objects"]:
        for p in o.get("properties", ()):
            v = p["value"]
            if not isinstance(v, dict):
                continue
            if "items" in v:
                for it in v.get("items") or ():
                    if isinstance(it, dict) and is_reference(it) is not None:
                        oid = it.get("object_id")
                        if isinstance(oid, int):
                            counts[oid] += 1
            elif is_reference(v) is not None:
                oid = v.get("object_id")
                if isinstance(oid, int):
                    counts[oid] += 1
    return counts


def build_pawns(nd: NdfDocument) -> list[PawnView]:
    """把整个文件扫成 ``PawnView`` 列表。

    为什么要绕过 ``nd.entries()``：``ObjEntry`` 把不可编辑的字段挡掉了，
    而这里**恰恰需要** ``DeckIdentifier``（被挡的）和 ``ClassNameForDebug``。
    投影层贴着 decoded 走，是它存在的理由之一。
    """
    objs = nd.decoded["objects"]
    by_id = {o["id"]: o for o in objs}
    counts = reference_counts(nd)
    attr_by_module: dict[str, list[Attr]] = {}
    for a in ATTRS:
        attr_by_module.setdefault(a.module, []).append(a)

    out: list[PawnView] = []
    for obj in objs:
        if obj.get("class") != PAWN_CLASS:
            continue
        mod_ids = _module_ids(obj)
        modules: dict[str, int] = {}
        sharing: dict[str, int] = {}
        values: dict = {}
        for mid in mod_ids:
            m = by_id.get(mid)
            if m is None:
                continue
            cname = m.get("class", "")
            modules.setdefault(cname, mid)
            sharing[cname] = counts.get(mid, 0)
            for attr in attr_by_module.get(cname, ()):
                got = _read_attr(attr, m, by_id)
                if got is not None:
                    values[attr.key] = got

        ident = ""
        for cname in ("TDeckModuleDescriptor",):
            mid = modules.get(cname)
            if mid is not None:
                ident = _text(by_id[mid], "DeckIdentifier") or ""
        kind, name_country, unit, parents = parse_identifier(ident)

        out.append(PawnView(
            entity_id=obj["id"],
            export_name=_text(obj, "ClassNameForDebug") or "",
            identifier=ident,
            kind=kind,
            country=str(values.get("country") or ""),
            name_country=name_country,
            unit=unit,
            parents=parents,
            values=values,
            modules=modules,
            sharing=sharing,
        ))
    return out


def choices(pawns: list[PawnView], key: str) -> list[tuple[str, int]]:
    """某个维度的取值分布，按条数降序。

    **「国家」有两个来源，不能混。** 实测柏林 mod 里
    ``MotherCountry`` 与名字段 1 有 **139 / 2268** 处不一致，而且成片出现：
    ``pion_BEL_*`` 的 43 个营 ``MotherCountry`` 全写着 ``US``，
    ``pion_FR_*`` 有一批写着 ``CAN`` —— 这是复制模板没改留下的。

    所以两个键分开给：

    ``country``  名字段 1。用户说的「按国家**前缀**筛选」指的就是它，
                 也是成片命名里真正可信的那一个。
    ``faction``  ``MotherCountry``。游戏真正读的阵营字段，改名字不会变它。
                 结果表里单独一列显示，两列不同的行要标出来。
    """
    if key == "country":
        got = Counter(p.name_country for p in pawns if p.name_country)
    elif key == "faction":
        got = Counter(p.country for p in pawns if p.country)
    elif key == "parent":
        got = Counter(p.best_parent() for p in pawns if p.best_parent())
    elif key == "kind":
        got = Counter(p.kind for p in pawns if p.kind)
    else:
        got = Counter()
    return got.most_common()
