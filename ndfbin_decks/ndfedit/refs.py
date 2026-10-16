"""引用字段：把「指向另一个对象的字段」变成人能选的东西。

**问题.** ``TAmmunitionDescriptor.Arme``、``TBlindageProperties.ResistanceFront``、
``TMountedWeaponDescriptor.Ammunition`` 这些字段的值不是数字，而是**另一个对象**。
它们在二进制里只存一个下标，直接给玩家看就是 ``#547``，等于不可编辑。

**两类引用，语义完全不同**（实测数据支撑）：

============  ======================  =========================================
type_id       名字                    含义
============  ======================  =========================================
``0xBBBBBBBB`` ``obj_ref``            **本文件内**的对象下标 + 它的类
``0xAAAAAAAA`` ``trans_ref``          **跨文件**的符号路径 ``$/GFX/…``
============  ======================  =========================================

- ``obj_ref`` 编码为 ``object_id`` + ``class_id`` 两个 u32。改它**必须同时改
  class_id**，否则 ``cndf_codec.decode`` 会抛 "Unresolved CNDF object/class
  reference"。这是最容易踩的坑。
- ``trans_ref`` 编码为 IMPR 前缀树的一个 **节点下标**（不是 TRAN 表位置，
  这一点在 NdfbinDecksEditor 的 ``equip.py`` 里已经用现成数据验证过）。
  解码时已经还原成 ``$/GFX/Unit/Descriptor_Unit_UAZ_469_SOV`` 这样的文本，
  所以它天然可读。

**两种引用不能互换.** 把 obj_ref 改成 trans_ref 不只是换个值——编码格式、
解析路径、语义（本文件 vs 外部文件）全都不同。所以选择器只在同类引用内换目标。

**标签怎么来.** 大多数目标对象没有名字（Weapon.ndfbin 的 24168 个对象里只有
3564 个有 export 名）。实测各目标类的可读性：

- ``TAmmunitionDescriptor``      有 ``Name``/``Caliber``（loc_hash，只能显示 hex）
- ``TMountedWeaponDescriptor``   有 ``EffectTag`` = ``FireEffect_SAM_9M8M3_x2`` ← 完整可读
- ``TTurretTwoAxisDescriptor``   有 ``Tag`` = ``tourelle1``
- ``TResistanceTypeRTTI``        只有 ``Family`` + ``Index`` 两个 int，没名字
- ``TTurretIdleBehaviourDescriptor`` 全是 float，没名字

所以标签不是一个固定字段，而是**逐级回退**的：
export 名 → 文本字段 → Name 哈希的 hex → ``Family/Index`` 结构化摘要 →
借被引用者的标签 → 兜底 ``#id``。

**第二个维度同样重要：谁在用我.** ``TResistanceTypeRTTI #547`` 读不出来，
但「有 12 个实体在用它」能帮玩家判断。这是反向索引，纯文件内可推导。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from ..core.cndf_layout import OBJECT_REFERENCE, TRANS_REFERENCE

#: 引用类型 -> 编辑器里的 kind 名
REF_KINDS = {OBJECT_REFERENCE: "objref", TRANS_REFERENCE: "pathref"}

#: 空引用（游戏里的 None）。``decode`` 明确允许它。
NULL_REF = 0xFFFFFFFF

#: 一个引用节点的完整状态。**必须带上 ``type_id`` / ``type`` /
#: ``reference_prefix``** ——它们是编码依据，只存 ``object_id`` / ``class_id``
#: 的话，用快照重建出来的节点不再被认作引用
#: （``repoint`` 会抛 "not a reference node"）。
_NODE_KEYS = ("type_id", "type", "reference_prefix",
              "object_id", "class_id", "index", "value")


def _node_state(leaf: dict) -> dict:
    """一个引用节点的快照。整条列表比较与撤销都靠它。

    深拷贝到只含标量的一层——引用节点没有嵌套结构，所以浅拷贝就够，
    而且必须是拷贝：递一个活 dict 出去，等于把「撤销时的原值」和
    「当前值」绑成同一个对象。
    """
    return {k: leaf[k] for k in _NODE_KEYS if k in leaf}

#: 判据：哪个属性说明「这个类是个实体」。
#: ``entity.py`` 从这里取，避免两处各写一遍字符串。
ENTITY_MODULES_FIELD = "ModulesDescriptors"

#: 文本字段的优先顺序。``EffectTag`` 排在前面是因为实测它带完整的弹药标识
#: （``FireEffect_SAM_9M8M3_x2``），而 ``Tag`` 往往只是 ``tourelle1``。
TEXT_PRIORITY = (
    "EffectTag", "ClassNameForDebug", "Tag", "HandheldEquipmentKey",
    "DescriptorId", "Label", "DisplayName", "Name",
)

#: 递归借标签时优先穿透的字段——这些字段名本身就说明「我代表那个东西」。
BORROW_PRIORITY = (
    "Ammunition", "Descriptor", "UnitDescriptor", "MissileDescriptor",
    "FireDescriptor", "WeaponDescriptor", "TurretDescriptor",
    "DamageTypeEvolutionOverRangeDescriptor", "HitRollRuleDescriptor",
    "BlindageProperties", "PhysicalDamageLevelsPack",
)

#: 想要结构化摘要时优先展示的字段。
SUMMARY_FIELDS = (
    "Family", "Index", "BaseHitValueModifiers", "BaseCriticModifier",
    "DamageLevels", "MaxPhysicalDamages", "MaxSpeedInKmph",
)

#: 候选集超过这个数就不放下拉框，改走搜索对话框。
DROPDOWN_MAX = 400

#: 标签缓存深度上限。递归借标签超过这个深度就放弃。
MAX_BORROW_DEPTH = 3


def is_reference(value) -> str | None:
    """值本身是不是一个引用。返回 kind，不是引用返回 ``None``。"""
    if not isinstance(value, dict):
        return None
    return REF_KINDS.get(value.get("type_id"))


def ref_points_to(value):
    """引用当前指向什么。返回 ``(kind, key)`` 或 ``None``。"""
    kind = is_reference(value)
    if kind is None:
        return None
    key = value.get("object_id") if kind == "objref" else value.get("index")
    if key is None or key == NULL_REF:
        return None
    return kind, key


def short_name(path: str) -> str:
    """``$/GFX/Unit/Descriptor_Unit_UAZ_469_SOV`` -> ``Descriptor_Unit_UAZ_469_SOV``。

    **保留 ``Descriptor_`` 前缀，不美化.** 这个前缀是 mod 作者在 NDF 源码里
    实际写的东西，去掉之后两边对不上，反而找不到人。
    """
    return path.rsplit("/", 1)[-1] if path else path


#: 只在「谁在用它」这种辅助语境里剥掉的前缀。主标签一律保留全名。
_FRIENDLY_PREFIXES = ("Descriptor_Unit_", "Descriptor_District_",
                      "Descriptor_Deck_")


def friendly(name: str) -> str:
    """``Descriptor_Unit_2S19_MstaS_SOV`` -> ``2S19_MstaS_SOV``。

    **只用在辅助语境.** 主标签（对象列表、按钮上的当前值）保留 ``Descriptor_``
    前缀，因为那是 mod 作者在 NDF 源码里实际敲的字符串，去掉就对不上了。
    但「被 450 个实体用到，包括 …」这种句子里塞十个
    ``Descriptor_Unit_`` 会把有用的部分挤掉，所以那里剥掉。
    """
    for p in _FRIENDLY_PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name


def describe(index: "RefIndex", ref: "RefField") -> str:
    """一个引用字段当前指向什么——**唯一正确的读法**。

    ``obj_ref`` 的 key 是 ``object_id``，``trans_ref`` 的 key 是 IMPR 的
    **节点下标**。两者都是整数，所以很容易把节点号当 object_id 送进
    ``label_of``，得到「``#73``（某个毫不相干的对象）」这种看似合理、
    实则张冠李戴的结果。实测踩过：``TerrainSpeedFactors`` 明明是
    ``$/GFX/Terrains/TrackVehicleTerrainSpeedFactors``，却显示成对象 #73。

    所有读引用的地方都走这里，不要再自己拼。
    """
    if ref is None or ref.is_null:
        return "(无)"
    if ref.kind == "objref":
        return index.label_of(ref.key)
    path = index.imports.get(ref.key)
    return short_name(path) if path else f"IMPR#{ref.key}"


def describe_full(index: "RefIndex", ref: "RefField") -> str:
    """带完整路径 / 类名的说明（tooltip 用）。"""
    if ref is None or ref.is_null:
        return "(无)"
    if ref.kind == "objref":
        return (f"{index.label_of(ref.key)}   "
                f"(object #{ref.key}, {index.class_of(ref.key)})")
    return index.imports.get(ref.key, f"IMPR#{ref.key}")


@dataclass(frozen=True)
class RefTarget:
    """一个可选的引用目标。"""

    kind: str                    # "obj" | "path" | "null"
    key: int                     # obj -> object_id ; path -> IMPR 节点下标
    label: str                   # 给玩家看的名字
    class_name: str = ""         # obj -> 目标类 ; path -> ""
    detail: str = ""             # 补充说明，如「被 12 个实体引用」
    uses: int = 0                # 被引用次数（0 表示目前没人用）

    @property
    def is_null(self) -> bool:
        return self.kind == "null"


@dataclass
class RefField:
    """一个引用字段的当前状态。"""

    name: str
    kind: str                    # "objref" | "pathref"
    leaf: dict                   # 原位 dict —— 改它就是改数据
    original_kind: str           # "obj" | "path" | "null"
    original_key: int
    #: 目标类的名字（objref 才有）。
    target_class: str = ""
    #: 该字段在这一类里的覆盖率。
    coverage: float = 1.0
    #: 目标类的候选总数。决定 UI 用下拉还是搜索框。
    pool_size: int = 0
    #: 原始键值快照，撤销时直接恢复——不需要回头找 RefIndex。
    snapshot: dict = dc_field(default_factory=dict)

    @property
    def key(self) -> int | None:
        if self.kind == "objref":
            k = self.leaf.get("object_id")
        else:
            k = self.leaf.get("index")
        return None if k is None or k == NULL_REF else k

    @property
    def is_null(self) -> bool:
        return self.key is None

    @property
    def current_kind(self) -> str:
        if self.is_null:
            return "null"
        return "obj" if self.kind == "objref" else "path"

    @property
    def dirty(self) -> bool:
        return (self.current_kind, self.key) != (self.original_kind,
                                                 self.original_key)

    def reset(self) -> None:
        """把引用恢复成打开文件时的样子。

        ``object_id`` 和 ``class_id`` 必须**一起**恢复：只恢复一个会留下
        一个自相矛盾的引用，``decode`` 直接拒绝。
        """
        for k, v in self.snapshot.items():
            self.leaf[k] = v


@dataclass
class RefListField:
    """一个 list 型字段里内嵌的引用，逐项可改、**可增删**。

    实测这些字段都是这种形态：``MountedWeaponDescriptorList``（武器挂什么）、
    ``DeckPackList``（牌组里有哪些载具）、``ModulesDescriptors``
    （实体由哪些模块组成）。

    **增删是安全的.** 早先的版本只允许换目标，理由是「改 ``length`` 要连带改
    ``CHNK`` 计数、重新编号后续对象」。实测那条理由**不成立**：列表元素是
    *引用*，不是一个被引用的对象——增删只改变宿主对象自己的 OBJE 字节，
    对象编号与 CHNK 计数都不动。编码器只校验 ``len(items) == length``，
    两个一起改就行（``tools/probe_listadd.py`` 跑通了删一项再加两项的完整往返）。

    仍然要小心的是**语义**：``DeckPackList`` 这类列表的长度受游戏侧约束
    （一个槽位对应一个 pack），删多了游戏里就对不上。所以界面允许增删，
    但把这件事说清楚，而不是替玩家做决定。
    """

    name: str
    #: list 的 value dict（``length`` / ``items`` 都在这里）
    leaf: dict
    items: "list[RefField]" = dc_field(default_factory=list)
    coverage: float = 1.0
    #: 打开时每一项的节点快照。**dirty 判定与撤销都靠它整条比较**——
    #: 逐项比较表达不了增删（第 3 项是「被删掉」还是「本来就没有」分不出来）。
    original: "list[dict]" = dc_field(default_factory=list)
    #: 重建项时要用的索引。由 :meth:`RefIndex.list_field_for` 注入——
    #: 没有它就没法从 ``object_id`` 反查 ``class_id``。
    index: "RefIndex | None" = None

    # ---- 读 ----

    @property
    def length(self) -> int:
        return len(self.items)

    def nodes(self) -> "list[dict]":
        """当前每一项的节点快照。"""
        return [_node_state(r.leaf) for r in self.items]

    @property
    def dirty(self) -> bool:
        return self.nodes() != self.original

    def dirty_indices(self) -> list[int]:
        """哪些位置与打开时不同（长度变了的话，多出来的位置也算）。"""
        now = self.nodes()
        n = max(len(now), len(self.original))
        return [i for i in range(n)
                if i >= len(self.original) or i >= len(now)
                or now[i] != self.original[i]]

    def target_classes(self) -> list[str]:
        """这个列表里出现过的目标类（``ModulesDescriptors`` 会有很多个）。

        每一项的目标类可能不同，所以「换第 i 项」的候选集必须按**该项**
        当前的目标类算——否则会把一个 flags 模块换成弹药，编码合法但语义
        完全乱套。
        """
        seen: list[str] = []
        for r in self.items:
            if r.target_class and r.target_class not in seen:
                seen.append(r.target_class)
        return seen

    def uniform_class(self) -> str:
        """所有项的目标类一致时返回它，否则返回空串。"""
        tc = self.target_classes()
        return tc[0] if len(tc) == 1 else ""

    def summary(self, index: "RefIndex", limit: int = 3) -> str:
        """一句话描述当前内容，给按钮上的文字用。

        走 :func:`describe` 而不是自己读 ``key`` ——列表里也可能混着
        路径引用，节点号当 object_id 用会显示成毫不相干的对象。
        再用 :func:`friendly` 剥掉 ``Descriptor_Unit_``，
        否则一个 35 项的 deck 按钮上全是这个前缀。
        """
        bits = [friendly(describe(index, r)) for r in self.items[:limit]]
        text = "、".join(bits)
        if self.length > limit:
            text += f" …（共 {self.length} 项）"
        return text

    # ---- 写 ----

    def _sync_length(self) -> None:
        """编码器会校验 ``len(items) == length``，不同步直接抛
        ``NDF collection length mismatch``。"""
        self.leaf["length"] = len(self.leaf.get("items") or [])

    def _node_template(self) -> dict:
        """新节点的骨架。

        **沿用既有项的类型标记**——``obj_ref`` 与 ``trans_ref`` 的编码格式
        不同（前者两个 u32，后者一个索引），混用会直接解不出来。
        空列表按 ``obj_ref`` 算，那是这一层唯一会遇到的形态
        （``list_field_for`` 只收全 obj_ref 的列表）。
        """
        if self.items:
            src = self.items[0].leaf
            node = {k: src[k] for k in ("type_id", "type", "reference_prefix")
                    if k in src}
        else:
            node = {"type_id": OBJECT_REFERENCE, "type": "obj_ref",
                    "reference_prefix": False}
        if node.get("type_id") == OBJECT_REFERENCE:
            node["object_id"] = NULL_REF
            node["class_id"] = NULL_REF
        else:
            node["index"] = NULL_REF
            node["value"] = None
        return node

    def add(self, kind: str, key: int) -> "RefField":
        """在末尾加一项，返回它的 ``RefField``。

        ``class_id`` 由 :meth:`RefIndex.repoint` 从目标对象反查——手填一个
        错的会直接触发 ``Unresolved CNDF object/class reference``。

        列表原本只指向一个类时**不允许跨类**；``ModulesDescriptors`` 这种
        本来就混着 77 种模块的例外（它没有统一的类可守）。
        """
        if self.index is None:
            raise RuntimeError("这个列表没有绑定索引，加不了项")
        node = self._node_template()
        self.index.repoint(node, kind, key,
                           allow_class_change=not bool(self.uniform_class()))
        items = self.leaf.setdefault("items", [])
        items.append(node)
        self._sync_length()
        rf = self.index._ref_field_from_node(
            f"{self.name}[{len(items) - 1}]", node, self.coverage)
        self.items.append(rf)
        return rf

    def remove(self, i: int) -> None:
        """删掉第 i 项。"""
        items = self.leaf.get("items") or []
        if not (0 <= i < len(items)):
            return
        del items[i]
        del self.items[i]
        self._sync_length()

    def move(self, i: int, delta: int) -> int:
        """上移 / 下移一项，返回它新的位置。

        顺序是有意义的：``MountedWeaponDescriptorList`` 里第 0 项是主武器，
        ``DeckPackList`` 的顺序决定牌组里的排列。
        """
        j = i + delta
        items = self.leaf.get("items") or []
        if not (0 <= i < len(items) and 0 <= j < len(items)):
            return i
        items[i], items[j] = items[j], items[i]
        self.items[i], self.items[j] = self.items[j], self.items[i]
        return j

    def set_nodes(self, nodes) -> None:
        """整条替换成 ``nodes``（每项是一个节点快照 dict）。

        **撤销走这一条路.** 增删没法逐项回退——逐项比较分不出「这一项是
        新加的」和「这一项原来就在这」。整条替换没有这个歧义。
        """
        self.leaf["items"] = [dict(n) for n in nodes]
        self._sync_length()
        self.rebuild()

    def rebuild(self) -> None:
        """按 ``leaf`` 的当前内容重建 ``RefField`` 列表。

        增删或整条替换之后必须调——``items`` 是界面读的东西，
        和 ``leaf`` 脱节就会「改了这个、显示那个」。
        """
        if self.index is None:
            return
        self.items = [
            self.index._ref_field_from_node(
                f"{self.name}[{i}]", node, self.coverage)
            for i, node in enumerate(self.leaf.get("items") or ())]

    def reset(self) -> None:
        """恢复成打开时的样子——**包括长度**（增删也能退回去）。"""
        if self.index is None:
            return
        self.set_nodes(self.original)


class RefIndex:
    """一个文件里全部引用的标签 + 反向索引 + 候选集。

    构造成本主要在反向索引：Unit.ndfbin 有 213086 处 obj_ref，全量扫一遍
    约 1 秒。所以是**惰性构建**——只有真的用到「谁在用我」时才走这一趟，
    浏览数值字段的普通流程不付这个钱。
    """

    def __init__(self, decoded):
        self.decoded = decoded
        self.objects: list[dict] = decoded["objects"]
        self.classes: list[str] = decoded["classes"]
        self.strings: list[str] = decoded.get("strings", [])
        self.exports: dict[int, str] = decoded.get("exports", {})
        self.imports: dict[int, str] = decoded.get("imports", {})
        self._by_id = {o["id"]: o for o in self.objects}
        self._pool: dict[str, list[int]] = {}
        for o in self.objects:
            self._pool.setdefault(o["class"], []).append(o["id"])
        self._labels: dict[int, str] = {}
        self._rev: dict[int, list[tuple[int, str]]] | None = None
        self._path_uses_cache: dict | None = None
        self._entity_classes: set[str] | None = None
        self._entity_users_cache: dict[int, tuple[list[str], int]] = {}

    # ---- 对象表的增减 ----

    def register(self, obj: dict) -> None:
        """登记一个**新追加**的对象（克隆）。

        ``self.objects`` 就是 ``decoded["objects"]`` 本身（同一个 list），
        所以追加立刻可见；但 ``_by_id`` 与 ``_pool`` 是构造时的快照。
        不做这一步，克隆出来的对象就查不到，``repoint`` 会报
        「本文件里没有 object_id=…」。
        """
        oid = obj["id"]
        self._by_id[oid] = obj
        pool = self._pool.setdefault(obj["class"], [])
        if oid not in pool:
            pool.append(oid)
        self._labels.pop(oid, None)
        self._invalidate_lookups()

    def unregister(self, oid: int) -> None:
        """撤销克隆时把一个对象从索引里摘掉。"""
        obj = self._by_id.pop(oid, None)
        if obj is None:
            return
        pool = self._pool.get(obj["class"])
        if pool and oid in pool:
            pool.remove(oid)
        self._labels.pop(oid, None)
        self._invalidate_lookups()

    def _invalidate_lookups(self) -> None:
        """对象增减后要作废的缓存。

        只清「谁在用我」的结果缓存，**不清反向索引**：新对象还没有任何
        引用者，旧对象的引用者不受影响，所以 ``_rev`` 依然是对的。
        （``_rev`` 本身在 ``repoint`` 之后就不刷新，这是原有设计——
        界面上显示「现在指向谁」一律读节点本身，不读反向索引。）
        """
        self._entity_users_cache = {}

    # ---- 惰性反向索引 ----

    @property
    def reverse(self) -> dict[int, list[tuple[int, str]]]:
        """``{目标 object_id: [(引用者 object_id, 字段名), …]}``。"""
        if self._rev is None:
            rev: dict[int, list[tuple[int, str]]] = {}
            for o in self.objects:
                for p in o["properties"]:
                    v = p["value"]
                    for tgt in self._obj_refs_in(v):
                        rev.setdefault(tgt, []).append(
                            (o["id"], p["property_name"]))
            self._rev = rev
        return self._rev

    @staticmethod
    def _obj_refs_in(value):
        """一个属性值里引用的全部 object_id，穿透 list / map / pair。

        **顶层和 list 都要看**：标量引用在顶层，集合引用在 ``items`` 里。
        这是本文件最容易写错的地方——只读 ``value["object_id"]`` 会漏掉
        ``ModulesDescriptors`` 这类集合型引用。
        """
        stack = [value]
        while stack:
            v = stack.pop()
            if isinstance(v, list):
                stack.extend(v)
                continue
            if not isinstance(v, dict):
                continue
            if v.get("type_id") == OBJECT_REFERENCE:
                oid = v.get("object_id")
                if oid is not None and oid != NULL_REF:
                    yield oid
            for item in v.get("items", ()):
                stack.append(item)
            for k in ("key", "value"):
                if k in v:
                    stack.append(v[k])

    # ---- 反向追查：这个对象最终被哪些实体用到 ----

    def _entity_class_names(self) -> set[str]:
        """哪些类是「实体」——判据是声明了 ``ModulesDescriptors``。

        查 PROP 表而不是类名：写死 ``TEntityDescriptor`` 在换文件、换 mod
        或游戏改了类名时会静默失效，而这个视图的价值恰恰在于「不用认识
        这个文件也能用」。
        """
        if self._entity_classes is None:
            self._entity_classes = {
                p["class"] for p in self.decoded["properties"]
                if p["name"] == ENTITY_MODULES_FIELD}
        return self._entity_classes

    def entity_users(self, obj_id: int, *, max_hops: int = 4,
                     max_named: int = 12, node_budget: int = 40000
                     ) -> tuple[list[str], int]:
        """哪些实体**间接**用到了这个对象。返回 ``(实体名列表, 实体总数)``。

        **为什么需要爬链.** 引用选择框里最好用的判断依据是「谁在用它」，
        但一个装甲等级 ``TResistanceTypeRTTI #547`` 的直接引用者只是
        ``TBlindageProperties``——那不是玩家认得的东西。要变成
        「T-72M1、T-80B 用的装甲」，得顺着
        ``RTTI ← TBlindageProperties ← TDamageModuleDescriptor ← 实体``
        爬三四跳。

        ``ModulesDescriptors`` 是 list，里面的引用在 ``items`` 里，
        而 :attr:`reverse` 已经穿透了 list，所以这里不用特殊处理。

        代价控制：``max_named`` 限制算标签的数量（算标签要遍历属性，
        4626 个实体全算不划算），``node_budget`` 限制 BFS 访问的节点数
        ——``TFlagsModuleDescriptor`` 这种模块被 1753 个实体共用，
        不设上限会顺着图跑飞。
        """
        cached = self._entity_users_cache.get(obj_id)
        if cached is not None:
            return cached

        rev = self.reverse
        entities = self._entity_class_names()
        seen = {obj_id}
        frontier = [obj_id]
        #: 实体 -> 被命中的次数（热度近似）
        found: dict[int, int] = {}
        visited = 0
        for _hop in range(max_hops):
            if not frontier:
                break
            nxt: list[int] = []
            for oid in frontier:
                for owner_id, _field in rev.get(oid, ()):
                    if owner_id in seen:
                        continue
                    seen.add(owner_id)
                    visited += 1
                    if self.class_of(owner_id) in entities:
                        found[owner_id] = found.get(owner_id, 0) + 1
                    else:
                        nxt.append(owner_id)
            if visited > node_budget:
                break
            frontier = nxt

        ranked = sorted(found, key=lambda i: (-found[i], i))[:max_named]
        result = ([friendly(self.label_of(i)) for i in ranked], len(found))
        self._entity_users_cache[obj_id] = result
        return result

    def describe_users(self, obj_id: int, limit: int = 4) -> str:
        """一句「谁在用它」，直接给 UI 显示。没被用到时返回空串。"""
        names, total = self.entity_users(obj_id)
        if not names:
            return ""
        shown = names[:limit]
        head = "、".join(shown)
        if total > len(shown):
            return f"{head} 等 {total} 个实体"
        return f"{head}（{total} 个实体）"

    # ---- 标签 ----

    def label_of(self, obj_id: int, depth: int = 0) -> str:
        """给一个对象起个人能读的名字。逐级回退，见模块 docstring。"""
        if obj_id is None or obj_id == NULL_REF:
            return "(无)"
        if depth == 0 and obj_id in self._labels:
            return self._labels[obj_id]
        obj = self._by_id.get(obj_id)
        if obj is None:
            return f"#{obj_id}"

        label = self._label_uncached(obj, depth)
        if depth == 0:
            self._labels[obj_id] = label
        return label

    def _label_uncached(self, obj: dict, depth: int) -> str:
        oid = obj["id"]

        # 1) export 名 —— 最可靠，mod 作者在 NDF 里写的就是它
        path = self.exports.get(oid)
        if path:
            return short_name(path)

        props = obj["properties"]
        texts: list[tuple[int, str, str]] = []
        hashes: list[tuple[str, str]] = []
        refs: list[tuple[int, str, int]] = []
        scalars: dict[str, object] = {}

        for p in props:
            name, v = p["property_name"], p["value"]
            t = v.get("type_id")
            if t in (7, 0x1C):                       # strg_ref / file_strg_ref
                idx = v.get("index")
                s = self.strings
                if idx is not None and 0 <= idx < len(s) and s[idx]:
                    rank = TEXT_PRIORITY.index(name) if name in TEXT_PRIORITY \
                        else len(TEXT_PRIORITY)
                    texts.append((rank, name, s[idx]))
            elif t == 0x1D:                          # loc_hash
                hexv = v.get("value_hex", "")
                if hexv:
                    low = name.lower()
                    rank = 1 if any(k in low for k in ("name", "caliber",
                                                       "type")) else 2
                    hashes.append((f"{rank}{name}", hexv))
            elif t in (OBJECT_REFERENCE, TRANS_REFERENCE):
                prio = (BORROW_PRIORITY.index(name) if name in BORROW_PRIORITY
                        else len(BORROW_PRIORITY))
                refs.append((prio, name, v))
            elif "value" in v and "items" not in v:
                scalars[name] = v.get("value")

        # 2) 文本字段
        if texts:
            texts.sort()
            return texts[0][2]

        # 3) Name 型 loc_hash —— 只能给 hex，但至少唯一且稳定
        if hashes:
            hashes.sort()
            return f"{hashes[0][1][:8].upper()}"

        # 4) 借被引用者的标签（有限深度，防止环）
        #
        # **必须排在「字段组合摘要」前面.** ``TDeckPackDescriptor`` 只有
        # ExperienceLevel / Number / 两个 trans_ref，如果先走组合摘要就得到
        # ``ExperienceLevel=1 Number=999``——对玩家毫无意义。而它的
        # ``UnitDescriptor`` 指着一个真实单位，借过来就是
        # ``Descriptor_Unit_UAZ_469_SOV``。这正是 NdfbinDecksEditor 的
        # ``equip.py`` 用「内容签名」标识 pack 的同一件事。
        if refs and depth < MAX_BORROW_DEPTH:
            refs.sort()
            for _, name, v in refs:
                kind = REF_KINDS.get(v.get("type_id"))
                if kind == "objref":
                    oid2 = v.get("object_id")
                    if oid2 is None or oid2 == NULL_REF or oid2 == oid:
                        continue
                    got = self.label_of(oid2, depth + 1)
                else:
                    p = self.imports.get(v.get("index"))
                    got = short_name(p) if p else None
                if got and not got.startswith("#"):
                    return got
                if got:
                    return f"{name}: {got}"

        # 5) Family + Index 这类枚举，拼个结构化摘要
        if "Family" in scalars and "Index" in scalars:
            return f"F{scalars['Family']}-{scalars['Index']}"
        if len(scalars) <= 3 and scalars:
            bits = [f"{k}={scalars[k]}" for k in sorted(scalars)]
            if len(" ".join(bits)) <= 34:
                return " ".join(bits)

        # 6) 兜底 —— 但至少把类名带上，比裸 id 强
        return f"#{oid} ({obj['class']})"

    def detail_of(self, obj_id: int) -> str:
        """副标题：形式为「被 N 处引用」或「没人用」。"""
        uses = self.reverse.get(obj_id, ())
        if not uses:
            return "暂时没有被引用"
        owners = {o for o, _ in uses}
        fields = sorted({f for _, f in uses})
        head = f"{len(uses)} 处引用（{len(owners)} 个对象）"
        if fields:
            head += "：" + "、".join(fields[:3])
            if len(fields) > 3:
                head += f" 等 {len(fields)} 个字段"
        return head

    def uses_of(self, obj_id: int) -> int:
        return len(self.reverse.get(obj_id, ()))

    # ---- 候选集 ----

    def pool_of_class(self, class_name: str) -> list[int]:
        return self._pool.get(class_name, [])

    def class_of(self, obj_id: int) -> str:
        o = self._by_id.get(obj_id)
        return o["class"] if o else ""

    def candidates_for_class(self, class_name: str, *, limit: int | None = None
                             ) -> list[RefTarget]:
        """某个类的全部对象，当作候选目标。

        **候选集就是「同文件里这个类的所有对象」** —— 因为 obj_ref 是按
        文件内下标寻址的，跨文件的对象根本指不到。
        """
        ids = self.pool_of_class(class_name)
        if limit is not None:
            ids = ids[:limit]
        out = [RefTarget(kind="obj", key=oid, label=self.label_of(oid),
                         class_name=class_name, uses=self.uses_of(oid))
               for oid in ids]
        out.sort(key=lambda t: (-t.uses, t.label.lower()))
        return out

    def candidates_for_paths(self, *, prefix: str = "") -> list[RefTarget]:
        """IMPR 里的全部路径。``prefix`` 用来先按前缀收窄。

        实测 Unit.ndfbin 有 6728 条路径，直接铺开没法用。**默认按当前值的
        父路径收窄**：「同目录下的兄弟资源」是唯一一个有意义的启发式，
        因为 NDF 里同类资源总是放在同一个前缀下。
        """
        pools: dict[str, list[int]] = {}
        for node_id, path in self.imports.items():
            pools.setdefault(path.rsplit("/", 1)[0], []).append(node_id)
        out = []
        for parent, nodes in pools.items():
            if prefix and not parent.startswith(prefix):
                continue
            for node_id in nodes:
                path = self.imports[node_id]
                out.append(RefTarget(kind="path", key=node_id,
                                     label=short_name(path),
                                     detail=path, uses=self._path_uses(node_id)))
        out.sort(key=lambda t: (-t.uses, t.label.lower()))
        return out

    def _path_uses(self, node_id: int) -> int:
        """一条 IMPR 路径被引用了多少次（惰性建一次全表）。"""
        if self._path_uses_cache is None:
            cache: dict[tuple, int] = {}
            for o in self.objects:
                for p in o["properties"]:
                    stack = [p["value"]]
                    while stack:
                        v = stack.pop()
                        if isinstance(v, list):
                            stack.extend(v)
                            continue
                        if not isinstance(v, dict):
                            continue
                        if v.get("type_id") == TRANS_REFERENCE:
                            k = ("path", v.get("index"))
                            cache[k] = cache.get(k, 0) + 1
                        for it in v.get("items", ()):
                            stack.append(it)
                        for kk in ("key", "value"):
                            if kk in v:
                                stack.append(v[kk])
            self._path_uses_cache = cache
        return self._path_uses_cache.get(("path", node_id), 0)

    def siblings_of(self, node) -> str:
        """当前 trans_ref 值的父路径——搜索框的默认收窄前缀。"""
        path = self.imports.get(node.get("index"))
        return path.rsplit("/", 1)[0] if path and "/" in path else ""

    # ---- 改写 ----

    def repoint(self, node: dict, kind: str, key: int | None,
                *, allow_class_change: bool = False) -> None:
        """把引用指向新目标。``key=None`` 表示置空。

        **obj_ref 必须连 class_id 一起改.** ``cdfn_codec.decode`` 会校验
        ``object_class_ids[object_id] == class_id``，只改一半会让整个文件
        解不开。而 ``class_id`` 只能从目标对象现查——不能沿用旧值。

        **默认禁止改变目标类.** 实测踩过：把 ``TBlindageProperties.ResistanceFront``
        指向一个 ``TTagsModuleDescriptor`` —— ``class_id`` 写对了，所以
        **编码完全合法、decode 也通过**，但语义彻底崩坏，游戏读到的是
        「这块装甲等于一个标签模块」。编码层的校验抓不到这种错，
        所以必须在这里拦。要显式跨类（例如指向派生类）得传
        ``allow_class_change=True``。
        """
        ref_kind = is_reference(node)
        if ref_kind is None:
            raise ValueError("not a reference node")
        if key is None:
            if ref_kind == "objref":
                node["object_id"] = NULL_REF
                node["class_id"] = NULL_REF
            else:
                node["index"] = NULL_REF
                node["value"] = None
            return
        if ref_kind == "objref":
            if kind not in ("obj", "objref"):
                raise ValueError("objref 只能指向本文件内的对象")
            target = self._by_id.get(key)
            if target is None:
                raise KeyError(f"本文件里没有 object_id={key}")
            new_cid = self._class_id_of(target["class"])
            cur_cid = node.get("class_id")
            if (not allow_class_change and cur_cid is not None
                    and cur_cid != NULL_REF and cur_cid != new_cid):
                raise ValueError(
                    f"这个字段原本指向 "
                    f"{self._class_name(cur_cid)}，不能改成 "
                    f"{target['class']}——换目标类会让游戏读不出来。")
            node["object_id"] = key
            node["class_id"] = new_cid
        else:
            if kind not in ("path", "pathref"):
                raise ValueError("pathref 只能指向 IMPR 里的路径")
            if key not in self.imports:
                raise KeyError(f"IMPR 里没有节点 {key}")
            node["index"] = key
            node["value"] = self.imports[key]

    def _class_name(self, class_id: int) -> str:
        if 0 <= class_id < len(self.classes):
            return self.classes[class_id]
        return f"#{class_id}"

    def _class_id_of(self, class_name: str) -> int:
        try:
            return self.classes.index(class_name)
        except ValueError as exc:
            raise KeyError(f"CLAS 表里没有 {class_name}") from exc

    # ---- 字段构造 ----

    def field_for(self, prop: dict, coverage: float = 1.0) -> RefField | None:
        """把一个 PROP 记录变成 ``RefField``，不是引用就返回 ``None``。"""
        v = prop["value"]
        if is_reference(v) is None or "items" in v:
            return None
        return self._ref_field_from_node(prop["property_name"], v, coverage)

    def list_field_for(self, prop: dict, coverage: float = 1.0
                       ) -> "RefListField | None":
        """把一个 list 型属性变成 ``RefListField``（逐项可改）。

        **要求整个列表都是引用.** 只要有一项不是 obj_ref（比如
        ``WeaponShootDataPropertyName`` 里塞的是 strg_ref），就返回 ``None``
        ——半懂不懂的列表编辑器比没有更危险。

        实测长度分布（``tools/probe_listrefs.py``）：

        ==========================================  ======  ==========
        字段                                        中位数  目标类数
        ==========================================  ======  ==========
        ``MountedWeaponDescriptorList``                  1  1
        ``SmartGroupList``                               4  1
        ``ModulesDescriptors``（Unit）                  46  77
        ``DeckPackList``                                77  1
        ==========================================  ======  ==========

        ``DeckPackList`` 最大到 268 项，所以 UI 那边不能直接在面板里铺开，
        必须走独立的列表对话框。
        """
        v = prop["value"]
        if not isinstance(v, dict) or v.get("type_id") != 0x11:
            return None
        raw_items = v.get("items", ())
        if not raw_items:
            return None
        built: list[RefField] = []
        for i, it in enumerate(raw_items):
            if not isinstance(it, dict) or it.get("type_id") != OBJECT_REFERENCE:
                return None
            built.append(self._ref_field_from_node(
                f"{prop['property_name']}[{i}]", it, coverage))
        return RefListField(name=prop["property_name"], leaf=v,
                            items=built, coverage=coverage,
                            original=[_node_state(it) for it in raw_items],
                            index=self)

    def _ref_field_from_node(self, name: str, v: dict,
                             coverage: float) -> RefField:
        kind = is_reference(v)
        snap = {k: v[k] for k in ("object_id", "class_id", "index", "value")
                if k in v}
        if kind == "objref":
            oid = v.get("object_id")
            cid = v.get("class_id")
            tclass = (self.classes[cid]
                      if cid is not None and 0 <= cid < len(self.classes)
                      else "")
            if oid is None or oid == NULL_REF:
                orig_kind, orig_key = "null", -1
            else:
                orig_kind, orig_key = "obj", oid
            return RefField(
                name=name, kind=kind, leaf=v,
                original_kind=orig_kind, original_key=orig_key,
                target_class=tclass, coverage=coverage, snapshot=snap,
                pool_size=len(self.pool_of_class(tclass)))
        idx = v.get("index")
        if idx is None or idx == NULL_REF:
            orig_kind, orig_key = "null", -1
        else:
            orig_kind, orig_key = "path", idx
        return RefField(
            name=name, kind=kind, leaf=v,
            original_kind=orig_kind, original_key=orig_key,
            target_class="", coverage=coverage, snapshot=snap,
            pool_size=len(self.imports))


def set_target(ref: RefField, index: "RefIndex", kind: str, key: int | None
               ) -> None:
    """改一个引用字段的目标（供 UI 调用）。"""
    index.repoint(ref.leaf, kind, key)
