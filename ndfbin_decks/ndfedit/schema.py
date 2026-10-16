"""Schema 驱动内核：任何 .ndfbin 都能打开、浏览、改数值。

**为什么需要这一层.** 第一版把「可改的9 个字段」硬编码在 ``FIELD_SPECS`` 里，
那只对 ``Weapon.ndfbin`` 的 ``TAmmunitionDescriptor`` 成立。用户明确指出 UI
应当兼容所有 ndfbin —— 而每个 ndfbin 的类与属性都不一样：

===================  ========  ==========================================
文件                 对象数   主要类
===================  ========  ==========================================
Weapon.ndfbin           24168   TMountedWeaponDescriptor / ... / TAmmunitionDescriptor
Unit.ndfbin             56223   TEntityDescriptor / ...
Deck.ndfbin             59947   TDeckSmartGroupDescriptor / ...
Depiction.ndfbin        77923   TDepictionDescriptor / ...
Division.ndfbin          6282   TDeckDivisionDescriptor / ...
===================  ========  ==========================================

硬编码字段在换文件的那一刻就失效了。

**关键洞察.** 每个 .ndfbin 自带 PROP 表，而 PROP 表里每条记录都带
``class`` —— 也就是说**「这个类有哪些属性」这件事，文件自己知道**。
所以「某文件里某类有哪些数值字段可以改」不需要维护白名单，
遍历 PROP 表就能得到，且永远和文件一致。

**只暴露标量.** 只有 int / float / bool 进了编辑器。数组、loc_hash
这些即使「能改」也没有可理解的语义，改了只会把 mod 弄坏。

**例外：引用.** ``obj_ref`` / ``trans_ref`` 也是可编辑的，但它们不走
``Field`` 这条路——值不是数字而是「另一个对象」。它们由
:mod:`ndfbin_decks.ndfedit.refs` 管，挂在 ``ObjEntry.refs`` 上。
"""

from __future__ import annotations

import copy
import struct
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..core import cndf_codec, cndf_layout
from .refs import RefField, RefIndex, RefListField, describe, is_reference
from .strfields import (STR_REF_TYPE_IDS, HashField, StrField, StrListField,
                        StringPool, is_loc_hash, is_str_list, is_str_ref)

#: NDF 标量 type_id -> (Python 类型, 控件类型)
#: 只有这些会进编辑器——理由见模块 docstring。
SCALARS = {
    0x00: (bool, "bool"),# bool
    0x01: (int, "int"),      # int8
    0x02: (int, "int"),      # int32
    0x03: (int, "int"),      # uint32
    0x04: (int, "int"),      # uint64
    0x05: (float, "float"),  # float32
    0x06: (float, "float"),  # float64
    0x18: (int, "int"),      # int16
    0x19: (int, "int"),      # uint16
    0x13: (int, "int"),      # int64
}

#: 显然不该让玩家改的属性名（小写比较）。
#: 这些是资源标识 / GUID / 内部索引，改了必然导致 mod 崩溃。
DENY_SUBSTRINGS = (
    "descriptorid", "guid", "hash", "token", "texture",
    "name", "path", "reference", "mesh",
)

#: 这些名字虽然含上面某些词，但实际是可玩数值——白名单。
ALLOW_EXACT = ("Name",)


def _editable(prop_name: str) -> bool:
    """这个属性名值不值得暴露给玩家。

    **``id`` 不能当作普通子串来挡.** 早先的版本把 ``"id"`` 塞进
    ``DENY_SUBSTRINGS`` 做子串匹配，结果连 ``ResistanceSides``
    （装甲**侧面**，"s**id**es"）都被挡掉了——而这是装甲面板四个方向里
    最常用的一个。``Valid``、``Rigid`` 之类同理。

    真正要挡的是 ``DescriptorId`` / ``ClassId`` / ``object_id`` 这种
    **标识符**，它们的共同点是 ``id`` 落在 camelCase 或下划线边界上，
    所以只匹配 ``Id`` / ``ID`` / ``_id`` 结尾。
    """
    if prop_name in ALLOW_EXACT:
        return False
    low = prop_name.lower()
    if any(bad in low for bad in DENY_SUBSTRINGS):
        return False
    if name_is_identifier(prop_name):
        return False
    return True


#: 文本 / 哈希字段里显然不该给玩家改的资源名。
#: 比数值那套宽松得多——数值字段挡的是「改了会崩」的标识符，
#: 而这一类字段本身就是名字，``name`` / ``token`` 正是要开放的东西。
TEXT_DENY_SUBSTRINGS = ("texture", "sprite", "mesh", "guid")


def _editable_text(prop_name: str) -> bool:
    """文本 / 哈希字段值不值得暴露给玩家。

    刻意**不复用** :func:`_editable`：那套的 ``DENY_SUBSTRINGS`` 里有
    ``token`` 与 ``name``，正好把最需要开放的两个字段
    （``TraitsToken``、``TypeCategoryName``）挡在门外——
    而它们恰恰是「UI 上显示什么」的决定者，是 mod 作者真正要改的东西。
    """
    if name_is_identifier(prop_name):
        return False
    low = prop_name.lower()
    return not any(bad in low for bad in TEXT_DENY_SUBSTRINGS)


def name_is_identifier(prop_name: str) -> bool:
    """属性名是不是一个「标识符」而不是一个数值。

    分三种写法：

    ====================  ==========  ====================================
    名字                  判定        说明
    ====================  ==========  ====================================
    ``DescriptorId``      标识符      ``Id`` 在词尾
    ``object_id``         标识符      ``_id`` 在词尾
    ``DivisionIds``       标识符      复数形式，实测是标识符列表
    ``DeckIdentifier``    标识符      全称
    ``ResistanceSides``   数值        ``id`` 在词中间（si-des）
    ``Valid``             数值        以 ``id`` 结尾但不是 ``Id``
    ====================  ==========  ====================================
    """
    low = prop_name.lower()
    if low.endswith("identifier") or low.endswith("ids"):
        return True
    if not low.endswith("id"):
        return False
    return (prop_name.endswith("Id") or prop_name.endswith("ID")
            or low.endswith("_id") or low.endswith("-id"))


def is_scalar(value: dict):
    """返回 ``(python_type, kind)``，非标量返回 ``None``。"""
    return SCALARS.get(value.get("type_id"))


def _scalar_kind(objs, pname: str) -> str:
    """一个标量属性在这一类里实际是什么类型（float 优先，其次 bool）。

    同一个属性名在不同对象上类型必然一致，所以取第一个命中的即可；
    用集合只是为了对「个别对象异常」保持宽容。
    """
    kinds = set()
    for obj in objs:
        for prop in obj["properties"]:
            if prop["property_name"] == pname:
                got = is_scalar(prop["value"])
                if got:
                    kinds.add(got[1])
                break
    return "float" if "float" in kinds else (
        "bool" if kinds == {"bool"} else "int")


def _ref_kind(objs, pname: str) -> str:
    """一个引用属性是 ``objref``（本文件对象）还是 ``pathref``（跨文件路径）。"""
    for obj in objs:
        for prop in obj["properties"]:
            if prop["property_name"] == pname:
                got = is_reference(prop["value"])
                if got:
                    return got
                break
    return "objref"


def _list_ref_kind(objs, pname: str) -> str:
    """列表型引用：统一的 ``objlist`` / ``pathlist``。"""
    for obj in objs:
        for prop in obj["properties"]:
            if prop["property_name"] != pname:
                continue
            for it in prop["value"].get("items", ()):
                got = is_reference(it)
                if got:
                    return "pathlist" if got == "pathref" else "objlist"
            break
    return "objlist"


def _all_items_are_refs(value: dict) -> bool:
    """列表的每一项都是引用（且不为空）。

    **只要混进一个非引用项就整个不合格.** ``WeaponShootDataPropertyName``
    这类列表里塞的是字符串引用，做成「逐项换目标」没有意义；半懂不懂的
    编辑器比没有更危险。
    """
    items = value.get("items")
    if not items:
        return False
    return all(isinstance(it, dict) and is_reference(it) is not None
               for it in items)


def _new_count_map():
    """``类名 -> 属性名 -> 出现次数``。

    用 ``defaultdict(Counter)`` 而不是普通 ``Counter``：统计时写的是
    ``counts.scalar[cname][pname] += 1``，第一层缺失必须**自动建一个
    Counter**，否则 ``Counter[不存在的键]`` 返回的是 ``0``，
    再对它取下标就是 ``'int' object is not subscriptable``。
    """
    return defaultdict(Counter)


@dataclass
class _Counts:
    """一个类里六种字段各自的出现次数。

    六个容器一一对应 :class:`ObjEntry` 的六个字典。做成一个对象而不是
    六个位置参数——``_entry_from`` 要在 ``_build`` 与克隆路径之间共用，
    参数一多就会有人接错顺序，而接错的后果是**界面悄悄少一个控件**，
    这类缺陷极难发现。
    """

    scalar: "defaultdict[str, Counter]" = field(default_factory=_new_count_map)
    ref: "defaultdict[str, Counter]" = field(default_factory=_new_count_map)
    ref_list: "defaultdict[str, Counter]" = field(default_factory=_new_count_map)
    text: "defaultdict[str, Counter]" = field(default_factory=_new_count_map)
    text_list: "defaultdict[str, Counter]" = field(default_factory=_new_count_map)
    hash: "defaultdict[str, Counter]" = field(default_factory=_new_count_map)


@dataclass
class Field:
    """一个可编辑的数值字段。

    ``leaf`` 直接指向 ``decoded["objects"]`` 里的原位dict——改它就是改数据。
    """

    name: str
    kind: str
    leaf: dict
    original: object
    #: 该属性在这一类里出现的比例（0~1），用来提示「这是少数弹药才有的字段」。
    coverage: float = 1.0
    #: True = 这个属性原本不在这个对象上，是玩家通过「添加」补进去的。
    #: 撤销时需要把它整个摘掉，而不是恢复原值。
    added: bool = False

    @property
    def value(self):
        return self.leaf["value"]

    @property
    def dirty(self) -> bool:
        return self.leaf["value"] != self.original

    def reset(self) -> None:
        self.leaf["value"] = self.original


@dataclass
class ObjEntry:
    """一个对象实例。"""

    object_id: int
    class_name: str
    fields: dict[str, Field] = field(default_factory=dict)
    #: 指向别的对象的字段。和 ``fields`` 并列而不是混在一起——它们的值
    #: 不是数字，编辑方式也完全不同（选目标 vs 填数字）。
    refs: dict[str, RefField] = field(default_factory=dict)
    #: 指向**一串**对象的字段（``ModulesDescriptors``、``DeckPackList`` …）。
    ref_lists: dict[str, RefListField] = field(default_factory=dict)
    #: 字符串引用字段（``ImpactHappening`` = 爆炸特效名、
    #: ``_ShortDatabaseName`` = 武器名）。值既不是数字也不是对象，
    #: 而是 STRG 表里的一个下标——编辑语义是「换成表里已有的另一个字符串」。
    strs: dict[str, StrField] = field(default_factory=dict)
    #: 字符串引用列表（``TraitsToken`` = UI 上那排标签）。
    #: 与 ``ref_lists`` 的区别是**允许增删项**：对象引用列表的长度往往受
    #: 游戏侧约束，而字符串列表的长度只是个计数。
    str_lists: dict[str, StrListField] = field(default_factory=dict)
    #: 本地化哈希（``Name`` / ``Caliber`` / ``TypeCategoryName``）。
    #: **不可逆**——没有「输入新值」这种操作，只能从别的对象身上照抄。
    hashes: dict[str, HashField] = field(default_factory=dict)
    #: 供人辨认的附加信息，如「被 605 把武器引用」。
    subtitle: str = ""
    #: 玩家真正看到的名字。
    title: str = ""
    #: 引用计数，用于共享提示。
    ref_count: int = 0

    @property
    def dirty(self) -> bool:
        return (any(f.dirty for f in self.fields.values())
                or any(r.dirty for r in self.refs.values())
                or any(l.dirty for l in self.ref_lists.values())
                or any(s.dirty for s in self.strs.values())
                or any(l.dirty for l in self.str_lists.values())
                or any(h.dirty for h in self.hashes.values()))

    @property
    def dirty_names(self) -> list[str]:
        return ([f.name for f in self.fields.values() if f.dirty]
                + [r.name for r in self.refs.values() if r.dirty]
                + [f"{l.name}[{i}]" for l in self.ref_lists.values()
                   for i in l.dirty_indices()]
                + [s.name for s in self.strs.values() if s.dirty]
                + [f"{l.name}[{i}]" for l in self.str_lists.values()
                   for i in l.dirty_indices()]
                + [h.name for h in self.hashes.values() if h.dirty])

    def added_fields(self) -> list["Field"]:
        return [f for f in self.fields.values() if f.added]

    def ref_of(self, name: str) -> "RefField | None":
        return self.refs.get(name)

    def reset(self) -> None:
        """撤销这个对象上的全部改动。

        对 ``added`` 字段不能只恢复原值——它本来就不在这个对象上，
        必须从 ``fields`` 里整个摘掉，否则会留下一个玩家从没添加过的属性。
        """
        for name in [f.name for f in self.fields.values() if f.added]:
            self.fields.pop(name, None)
        for f in self.fields.values():
            f.reset()
        for r in self.refs.values():
            r.reset()
        for l in self.ref_lists.values():
            l.reset()
        for s in self.strs.values():
            s.reset()
        for l in self.str_lists.values():
            l.reset()
        for h in self.hashes.values():
            h.reset()


@dataclass
class ClassInfo:
    """一个类的概要。"""

    name: str
    count: int
    #: 该类所有对象上出现过的可编辑字段名（并集），带覆盖率。
    fields: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def field_count(self) -> int:
        return len(self.fields)


def _fmt_value(v) -> str:
    """改动预览里的值显示。float 按显示精度截断——
    ``0.13199999928474426`` 这种东西玩家看不懂，界面上本来就是 0.1320。"""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{round(v, 4):g}"
    return str(v)


def _fmt_ref_before(index: RefIndex, ref: RefField) -> str:
    """引用字段「改之前」指向什么。"""
    if ref.original_kind == "null":
        return "(无)"
    if ref.original_kind == "obj":
        return index.label_of(ref.original_key)
    path = index.imports.get(ref.original_key)
    return (path.rsplit("/", 1)[-1] if path else f"IMPR#{ref.original_key}")


@dataclass
class ChangeRow:
    """改动预览里的一行。"""

    object_id: int
    class_name: str
    title: str
    field: str
    before: str
    after: str
    #: ``field`` | ``added`` | ``ref`` | ``list``
    kind: str = "field"

    def describe(self, humanize=None) -> str:
        name = humanize(self.field) if humanize else self.field
        if self.kind == "added":
            return f"{name}（原本没有） → {self.after}"
        if self.kind == "ref":
            return f"{name} 指向：{self.before} → {self.after}"
        return f"{name}: {self.before} → {self.after}"


class NdfDocument:
    """打开的任意 .ndfbin。"""

    def __init__(self, path: Path, doc, decoded):
        self.path = Path(path)
        self.doc = doc
        self.decoded = decoded
        self.classes: list[ClassInfo] = []
        self._by_class: dict[str, list[ObjEntry]] = {}
        #: 自定义标题提供者，由具体视图注入（如弹药视图用 ProjectileType）。
        self._titler = None
        #: 引用标签 / 候选集索引，惰性构建——遍历全部对象建反向索引
        #: 在 Unit.ndfbin 上要一秒，浏览数值字段的人不该付这个钱。
        self._ref: RefIndex | None = None
        #: STRG 表的可搜索视图（带使用次数）。同样惰性——扫一遍全文件的
        #: 字符串引用在 Unit.ndfbin 上要几十毫秒，不看文本字段的人不必付。
        self._string_pool: StringPool | None = None
        #: 撤销历史。与文档一对一——换文件就是一个新文档、一份新历史，
        #: 不需要也不应该把旧文件的步骤带过来。
        self._history = None
        #: object_id -> ObjEntry。惰性构建；引用字段要能「进到」目标对象，
        #: 而 ``_by_class`` 是按类分组的，没有 id 索引。克隆对象后作废重建。
        self._by_id: dict[int, "ObjEntry"] | None = None
        #: 每个类的属性统计表，克隆对象时为新对象建条目要用。
        self._counters: dict[str, "_Counts"] = {}
        #: TOPO 原文（顶层对象索引）。克隆要往它里面追加。
        self._topo: list[int] = []
        #: 克隆体的**载荷**登记表。只增不减——撤销是把对象表截断，
        #: 重做要能照原样加回来，所以载荷必须留着。
        self._clones: list[dict] = []
        #: 当前已经物化进对象表的克隆体数量。撤销栈把它当作一个状态键。
        self._appended = 0
        #: 上一次克隆换了哪些 guid 型字段的名字——界面要如实告诉玩家
        #: 「新对象换了身份」，而 loc_hash 字段换不了也一并说明。
        self.last_clone_guid_fields: list[str] = []
        self._build()

    def entry_of(self, object_id: int) -> "ObjEntry | None":
        """按 object_id 找条目；不在本文件里则返回 ``None``。

        **跨文件引用必须靠这个返回 None 来识别**：``ModulesDescriptors``
        里的模块可能住在 ``Unit.ndfbin``，本编辑器只打开了当前这一个文件，
        那种目标没有字段可编辑。
        """
        if self._by_id is None:
            self._by_id = {e.object_id: e
                           for es in self._by_class.values() for e in es}
        return self._by_id.get(object_id)


    @property
    def history(self):
        """本文件的撤销历史（惰性创建，见 :mod:`ndfbin_decks.ndfedit.history`）。

        惰性导入而不是模块顶层导入：``history`` 需要 ``humanize``，
        顶层互相 import 会成环。
        """
        if self._history is None:
            from .history import History
            self._history = History(self)
        return self._history

    # ---- 加载 ----

    @classmethod
    def load(cls, path) -> "NdfDocument":
        path = Path(path)
        doc, decoded = cndf_codec.decode(path.read_bytes(), source=path)
        return cls(path, doc, decoded)

    @property
    def ref(self) -> RefIndex:
        if self._ref is None:
            self._ref = RefIndex(self.decoded)
        return self._ref

    @property
    def strings_pool(self) -> StringPool:
        """STRG 表的可搜索视图，带「每个字符串被用了多少次」。

        使用次数是排序依据：STRG 表有 5734 项，而弹药特效名只有几十个。
        按常用度降序，玩家要找的那个名字基本就在最前面——这比字母序有用，
        因为**同类的弹药在用什么**天然就是最可能的答案。
        """
        if self._string_pool is None:
            usage: Counter = Counter()
            for obj in self.decoded["objects"]:
                for prop in obj["properties"]:
                    count_str_refs(prop["value"], usage)
            self._string_pool = StringPool(self.decoded["strings"], usage)
        return self._string_pool

    def hash_choices(self, class_name: str, field_name: str
                     ) -> list[tuple[str, int, str]]:
        """同类对象的某个哈希字段都取过哪些值。

        返回 ``[(hex, 用它的对象数, 一个示例对象的标题)]``，按使用数降序。

        这是 ``loc_hash`` **唯一**的可编辑方式：哈希不可逆，没法让玩家输入
        文本，但他完全可以说「跟那个炮弹一样」。只给一堆 16 进制数字让人挑
        是无意义的，配上「有 124 个同类对象在用」才有判断依据。
        """
        counter: Counter = Counter()
        sample: dict[str, str] = {}
        for e in self._by_class.get(class_name, ()):
            h = e.hashes.get(field_name)
            if h is None:
                continue
            counter[h.hex] += 1
            sample.setdefault(h.hex, e.title or f"#{e.object_id}")
        return [(h, n, sample[h]) for h, n in counter.most_common()]

    def _build(self):
        # TOPO 是「顶层对象索引表」。它不在 decoded 里（decode 只把它转成
        # 一个 set 用来标记 is_top_object），所以这里单独取原文。
        # 克隆对象必须往它里面追加——实测弹药 1548/1548 全在 TOPO 里，
        # 而 cndf_codec.append_graph_objects 并不动它。
        _sec = cndf_layout.section_map(self.doc.sections)
        self._topo = list(cndf_layout.parse_u32_list(
            self.doc.full_data, _sec.get("TOPO")))

        # 先统计每个类里每个可编辑属性的出现次数，用于覆盖率。
        # 标量和引用分开统计——它们进的是不同的容器。
        counts_by_class: dict[str, _Counts] = {}
        objects_by_class: dict[str, list[dict]] = {}
        for obj in self.decoded["objects"]:
            cname = obj["class"]
            objects_by_class.setdefault(cname, []).append(obj)
            counts = counts_by_class.setdefault(cname, _Counts())
            for prop in obj["properties"]:
                pname = prop["property_name"]
                v = prop["value"]
                if is_reference(v) is not None:
                    # 只收**顶层**引用。列表里的引用走 ref_list，
                    # 两者编辑方式不同（点一个按钮选目标 vs 打开列表逐项改）。
                    if "items" not in v and _editable(pname):
                        counts.ref[cname][pname] += 1
                    continue
                if isinstance(v, dict) and v.get("type_id") == 0x11:
                    # 列表分两种。整条都是 obj_ref 的走 ref_list；整条都是
                    # 字符串引用的走 text_list（TraitsToken 是典型）。
                    # 混着别的类型的列表语义不明，两边都不收。
                    if _all_items_are_refs(v):
                        if _editable(pname):
                            counts.ref_list[cname][pname] += 1
                    elif is_str_list(v) and _editable_text(pname):
                        counts.text_list[cname][pname] += 1
                    continue
                # 文本 / 哈希字段用**另一套**判定：它们本身就是名字，
                # 数值那套的 name / token 黑名单在这里正好挡错东西。
                if is_str_ref(v):
                    if _editable_text(pname):
                        counts.text[cname][pname] += 1
                    continue
                if is_loc_hash(v):
                    if _editable_text(pname):
                        counts.hash[cname][pname] += 1
                    continue
                if is_scalar(v) is None:
                    continue
                if not _editable(pname):
                    continue
                # 只接受「顶层就是标量」的属性。map 型字段（type_id 0x12）
                # 的叶子也是标量，但改「映射第 N 项」没有可理解的语义，跳过。
                if "value" not in v or "items" in v:
                    continue
                counts.scalar[cname][pname] += 1

        index = self.ref

        for cname, objs in objects_by_class.items():
            counts = counts_by_class.get(cname, _Counts())
            total = len(objs)
            infos = []
            for bucket, kind_of in (
                (counts.scalar[cname], _scalar_kind),
                (counts.ref[cname], _ref_kind),
                (counts.ref_list[cname], _list_ref_kind),
            ):
                for pname, n in bucket.most_common():
                    infos.append((pname, kind_of(objs, pname), n / total))
            for pname, n in counts.text[cname].most_common():
                infos.append((pname, "str", n / total))
            for pname, n in counts.text_list[cname].most_common():
                infos.append((pname, "strlist", n / total))
            for pname, n in counts.hash[cname].most_common():
                infos.append((pname, "hash", n / total))

            entries = [self._entry_from(obj, cname, counts, total, index)
                       for obj in objs]

            self.classes.append(ClassInfo(name=cname, count=total, fields=infos))
            self._by_class[cname] = entries
            # 克隆对象时要按同一套规则为一个新对象建条目，而那时没有
            # 现成的统计表可算（重算一遍是 O(全文件)）。留一份。
            self._counters[cname] = counts

        self.classes.sort(key=lambda c: (-c.count, c.name))

    def _entry_from(self, obj: dict, cname: str, counts: "_Counts",
                    total: int, index) -> "ObjEntry":
        """按类的统计表把一个对象变成 ``ObjEntry``。

        ``_build`` 与克隆路径共用这一份——两处各写一遍的话，克隆出来的
        对象会有一点点不一样（少一个按钮、覆盖率分母不同），
        而这种差异很难被发现。
        """
        fields: dict[str, Field] = {}
        refs: dict[str, RefField] = {}
        ref_lists: dict[str, RefListField] = {}
        strs: dict[str, StrField] = {}
        str_lists: dict[str, StrListField] = {}
        hashes: dict[str, HashField] = {}
        # 每一层都要取到本类的那张表——``counts.xxx`` 的第一层键是类名。
        # defaultdict 会在取的时候顺手建一个空表，所以克隆一个从未见过的
        # 类也不会炸（只是那个对象的字段表是空的）。
        s_cnt = counts.scalar[cname]
        r_cnt = counts.ref[cname]
        rl_cnt = counts.ref_list[cname]
        t_cnt = counts.text[cname]
        tl_cnt = counts.text_list[cname]
        h_cnt = counts.hash[cname]
        for prop in obj["properties"]:
            pname = prop["property_name"]
            v = prop["value"]
            if pname in r_cnt:
                got = index.field_for(prop, r_cnt[pname] / total)
                if got is not None:
                    refs[pname] = got
                continue
            if pname in rl_cnt:
                got = index.list_field_for(prop, rl_cnt[pname] / total)
                if got is not None:
                    ref_lists[pname] = got
                continue
            if pname in t_cnt:
                if is_str_ref(v):
                    strs[pname] = StrField(
                        name=pname, leaf=v,
                        original_index=v.get("index"),
                        original_text=v.get("value") or "",
                        coverage=t_cnt[pname] / total)
                continue
            if pname in tl_cnt:
                if is_str_list(v):
                    str_lists[pname] = StrListField(
                        name=pname, leaf=v,
                        original=[(it.get("index"), it.get("value") or "")
                                  for it in v.get("items") or ()],
                        coverage=tl_cnt[pname] / total)
                continue
            if pname in h_cnt:
                if is_loc_hash(v):
                    hashes[pname] = HashField(
                        name=pname, leaf=v,
                        original_hex=(v.get("value_hex") or "").lower(),
                        coverage=h_cnt[pname] / total)
                continue
            if pname not in s_cnt:
                continue
            got = is_scalar(v)
            if got is None:
                continue
            if "value" not in v or "items" in v:
                continue
            fields[pname] = Field(
                name=pname, kind=got[1], leaf=v,
                original=v["value"],
                coverage=s_cnt[pname] / total)
        return ObjEntry(object_id=obj["id"], class_name=cname,
                        fields=fields, refs=refs, ref_lists=ref_lists,
                        strs=strs, str_lists=str_lists, hashes=hashes)

    # ---- 定制钩子 ----

    def set_titler(self, func) -> None:
        """注入标题/副标题提供者。

        ``func(entry, obj) -> (title, subtitle)``。这样具体视图（弹药、载具）
        可以给出「玩家认得的名字」，而通用视图退回 object_id。
        """
        self._titler = func
        if func is None:
            return
        by_id = {o["id"]: o for o in self.decoded["objects"]}
        for entries in self._by_class.values():
            for e in entries:
                title, subtitle = func(e, by_id.get(e.object_id))
                e.title, e.subtitle = title, subtitle

    # ---- 查询 ----

    @property
    def class_names(self) -> list[str]:
        return [c.name for c in self.classes]

    def class_info(self, name: str) -> ClassInfo | None:
        return next((c for c in self.classes if c.name == name), None)

    def entries(self, class_name: str) -> list[ObjEntry]:
        return list(self._by_class.get(class_name, []))

    def dirty_count(self) -> int:
        """有改动的对象数。

        补加属性即使值恰好等于模板，也算改动——因为对象结构确实变了，
        游戏会开始读这个字段。把它算进未保存状态，玩家才不会以为白加了。
        """
        return sum(1 for es in self._by_class.values() for e in es
                   if e.dirty or e.added_fields())

    def dirty_total(self) -> int:
        return sum(1 for es in self._by_class.values()
                   for e in es for f in e.fields.values() if f.dirty)

    def ref_dirty_total(self) -> int:
        """改过目标的引用字段数——单独计数，因为它改的是对象图而不是值。"""
        return sum(1 for es in self._by_class.values()
                   for e in es for r in e.refs.values() if r.dirty)

    def ref_list_dirty_total(self) -> int:
        """改过目标的「列表里的引用项」总数。"""
        return sum(1 for es in self._by_class.values()
                   for e in es for l in e.ref_lists.values()
                   for r in l.items if r.dirty)

    def text_dirty_total(self) -> int:
        """改过的「名字 / 标签 / 哈希」字段数。

        三类合并成一个数：对玩家来说它们是同一件事——「改了这个对象怎么
        被描述」。分成三个数字只会让保存确认框更长，信息量并不增加，
        而真正重要的是下面那张逐字段对照表。
        """
        return sum(1 for es in self._by_class.values() for e in es
                   for bucket in (e.strs, e.str_lists, e.hashes)
                   for item in bucket.values() if item.dirty)

    def changes(self, *, limit: int | None = None) -> list["ChangeRow"]:
        """逐字段列出全部改动，供保存前预览。

        **为什么不能只给数量。** 「已改动 150 个对象」说服不了一个要按保存的
        玩家——他需要看到 ``MaximumRangeGRU: 1925 → 2100`` 这样的对照，
        才能确认自己改的是想改的那批东西。批量操作尤其如此：
        条件写错一个数字，改掉的可能是另外 150 条。
        """
        out: list[ChangeRow] = []
        index = self.ref
        for cname, entries in self._by_class.items():
            for e in entries:
                if not (e.dirty or e.added_fields()):
                    continue
                title = e.title or f"#{e.object_id}"
                for f in e.fields.values():
                    if f.added:
                        out.append(ChangeRow(
                            e.object_id, cname, title, f.name,
                            "（原本没有）", _fmt_value(f.value), "added"))
                    elif f.dirty:
                        out.append(ChangeRow(
                            e.object_id, cname, title, f.name,
                            _fmt_value(f.original), _fmt_value(f.value)))
                for r in e.refs.values():
                    if r.dirty:
                        out.append(ChangeRow(
                            e.object_id, cname, title, r.name,
                            _fmt_ref_before(index, r), describe(index, r),
                            "ref"))
                for l in e.ref_lists.values():
                    for i in l.dirty_indices():
                        rf = l.items[i]
                        out.append(ChangeRow(
                            e.object_id, cname, title, f"{l.name}[{i}]",
                            _fmt_ref_before(index, rf), describe(index, rf),
                            "list"))
                for nm, sf in e.strs.items():
                    if sf.dirty:
                        # 惰性取字符串池：只有真的有文本字段被改时才去建它。
                        before = self.strings_pool.text(sf.original_index)
                        out.append(ChangeRow(
                            e.object_id, cname, title, nm,
                            before if before is not None
                            else f"#{sf.original_index}",
                            sf.value, "str"))
                for nm, lf in e.str_lists.items():
                    if lf.dirty:
                        out.append(ChangeRow(
                            e.object_id, cname, title, nm,
                            "、".join(t for _i, t in lf.original) or "（空）",
                            "、".join(lf.texts()) or "（空）", "strlist"))
                for nm, hf in e.hashes.items():
                    if hf.dirty:
                        out.append(ChangeRow(
                            e.object_id, cname, title, nm,
                            hf.original_hex, hf.hex, "hash"))
                if limit is not None and len(out) >= limit:
                    return out
        return out

    def added_total(self) -> int:
        """玩家补加了多少个属性——保存确认时要单独告知，因为它改的是结构。"""
        return sum(1 for es in self._by_class.values()
                   for e in es for f in e.fields.values() if f.added)

    def ensure_titles(self, class_name: str) -> None:
        """给还没有 title 的条目补上能认得出的名字。

        **不要覆盖已有 title** —— 弹药视图注入的「弹种 · GUID」比什么都强，
        而这里填的是引用标签系统给的通用名字。之前 GUI 里写的是无条件赋值，
        导致弹药标题全部退化成 ``#1000``。

        惰性按类填充：Unit.ndfbin 有 56223 个对象，全量算标签要好几秒，
        而玩家一次只看一个类。
        """
        index = self.ref
        for e in self._by_class.get(class_name, []):
            if not e.title:
                e.title = index.label_of(e.object_id)

    def search(self, class_name: str, text: str) -> list[ObjEntry]:
        items = self.entries(class_name)
        if not text.strip():
            return sorted(items, key=lambda e: (not e.dirty, (e.title or "").lower()))
        needle = text.strip().lower()
        hit = [e for e in items
               if needle in (e.title or "").lower()
               or needle in (e.subtitle or "").lower()
               or str(e.object_id) == needle]
        return sorted(hit, key=lambda e: (not e.dirty, (e.title or "").lower()))

    # ---- 补加属性 ----

    def field_template(self, class_name: str, name: str) -> Field | None:
        """找同类里一个已经拥有该属性的对象，取它的值作为模板。

        **为什么必须有模板.** NDF 的属性值不只是裸数字，它带 ``type_id``、
        ``reference_prefix`` 等编码元数据。一个 float 属性补进去时必须
        带上正确的 ``type_id``（float=0x5 / int32=0x2 / bool=0x0 …），
        写错游戏就读不出来。而同一个类里同一属性在所有对象上的类型必然一致，
        所以拿现成的当模板既安全又省事。

        选模板时挑**中位数**那一行的值而不是第一个：默认值取自某个具体对象，
        可能恰好是极端值（0或 9999），玩家看到会困惑。
        """
        values = []
        for e in self._by_class.get(class_name, []):
            f = e.fields.get(name)
            if f is not None:
                values.append(f.leaf)          # leaf 本身就是一个 value dict
        if not values:
            return None
        return Field(name=name, kind="", leaf=values[len(values) // 2],
                     original=0, coverage=0.0)

    def add_field(self, entry: "ObjEntry", name: str, value=None) -> Field:
        """给 ``entry`` 补上一个它原本没有的属性。

        已实测可行：PROP 表里所有属性都预先注册（``TAmmunitionDescriptor``
        声明 75 个属性、实际也只用到 75 个），``encode_objects`` 逐条写
        ``property_id`` + 值，往``properties`` 列表追加即可；编码-解码往返后
        新属性稳定保留，其它对象零影响，对象总数不变。

        Parameters
        ----------
        value
            缺省时取同类中位数值；有值时按模板的类型转换后写入。
        """
        if name in entry.fields:
            return entry.fields[name]

        tpl = self.field_template(entry.class_name, name)
        if tpl is None:
            raise KeyError(
                f"{entry.class_name} 里没有任何对象拥有 {name}，"
                f"无法确定它的编码类型。")

        obj = self._object_by_id(entry.object_id)
        if obj is None:
            raise KeyError(f"找不到 object_id={entry.object_id}")

        pid = next((p["id"] for p in self.decoded["properties"]
                    if p["name"] == name), None)
        if pid is None:
            raise KeyError(f"PROP 表里没有 {name}，不能引用")

        # 复制模板的 value dict，保留 type_id 等元数据，只换value
        new_value = dict(tpl.leaf)
        if value is None:
            value = tpl.leaf["value"]
        new_value["value"] = self._coerce(value, tpl.leaf)

        obj["properties"].append(
            {"property_id": pid, "property_name": name, "value": new_value})

        info = self.class_info(entry.class_name)
        cov = 0.0
        kind = "float"
        if info:
            for n, k, c in info.fields:
                if n == name:
                    kind, cov = k, c
                    break

        fld = Field(name=name, kind=kind, leaf=new_value,
                    original=value, coverage=cov, added=True)
        entry.fields[name] = fld
        return fld

    def remove_field(self, entry: "ObjEntry", name: str) -> bool:
        """摘掉一个玩家补加的属性，回到「原本就没有」的状态。"""
        fld = entry.fields.get(name)
        if fld is None or not fld.added:
            return False
        obj = self._object_by_id(entry.object_id)
        if obj is None:
            return False
        obj["properties"] = [p for p in obj["properties"]
                             if p["property_name"] != name]
        entry.fields.pop(name, None)
        return True

    def _object_by_id(self, object_id: int):
        for o in self.decoded["objects"]:
            if o["id"] == object_id:
                return o
        return None

    @staticmethod
    def _coerce(value, template: dict):
        """把玩家输入的值转成模板对应的Python 类型。

        QDoubleSpinBox 给的是 float，写进 int 属性会让``struct.pack(">i")``
        抛异常，或者更糟——静默截断。用模板的类型决定目标类型。
        """
        t = template.get("type_id")
        if t in (0x00,):
            return bool(value)
        if t in SCALARS:
            want_float = SCALARS[t][0] is float
            if want_float:
                return float(value)
            return int(round(float(value)))
        return value

    def reset_all(self) -> None:
        for es in self._by_class.values():
            for e in es:
                e.reset()

    # ---- 克隆对象（往对象表里追加） ----

    def appended_count(self) -> int:
        """当前已经物化进对象表的克隆体数量。

        撤销栈把它当成一个状态键。追加对象**不是字段值的改变**，
        脏状态差分看不见，所以必须单独表示——否则克隆之后就退不回来了。
        """
        return self._appended

    def set_appended_count(self, n: int) -> None:
        """把克隆体数量调到 ``n``（撤销往回退，重做往前加）。

        只允许动**尾部**：克隆体一律追加在对象表末尾，所以截断永远是
        「删掉最后 k 个」。不能删中间的对象——那会平移后面所有对象的编号，
        而编号被引用到处指着。
        """
        if not 0 <= n <= len(self._clones):
            raise ValueError(f"克隆体数量 {n} 越界（已登记 "
                             f"{len(self._clones)} 个）")
        cur = self._appended
        if n == cur:
            return
        objs = self.decoded["objects"]

        if n < cur:
            doomed = set(range(len(objs) - (cur - n), len(objs)))
            users = self.references_to_any(doomed)
            if users:
                who = "、".join(
                    f"{self._describe_id(o)} 的 {humanize(f)}"
                    for o, f in users[:3])
                raise ValueError(
                    f"还有 {len(users)} 处引用指着要撤掉的对象（{who}"
                    + ("　等" if len(users) > 3 else "")
                    + "）。\n先把那些「替换」改回去，或先撤销它们，再撤销克隆。")
            for obj in objs[len(objs) - (cur - n):]:
                lst = self._by_class.get(obj["class"]) or []
                if lst and lst[-1].object_id == obj["id"]:
                    lst.pop()
                self._sync_class(obj["class"])
                if self._topo and self._topo[-1] == obj["id"]:
                    self._topo.pop()
                if self._ref is not None:
                    self._ref.unregister(obj["id"])
            del objs[len(objs) - (cur - n):]
        else:
            index = self.ref
            for i in range(cur, n):
                payload = copy.deepcopy(self._clones[i])
                payload["id"] = len(objs)
                payload["is_top_object"] = True
                objs.append(payload)
                cname = payload["class"]
                counts = self._counters.get(cname, _Counts())
                # 分母用加上这个克隆体之后的总数，与 _build 的口径一致。
                total = len(self._by_class.get(cname, [])) + 1
                entry = self._entry_from(payload, cname, counts, total, index)
                self._by_class.setdefault(cname, []).append(entry)
                self._topo.append(payload["id"])
                self._sync_class(cname)
                index.register(payload)

        self._appended = n
        self.decoded["object_count"] = len(objs)
        self._by_id = None

    def clone_object(self, entry: "ObjEntry") -> "ObjEntry":
        """克隆一个对象并追加到文件末尾，返回新条目。

        **克隆体必须换 GUID。** 实测 ``DescriptorId``（``type_id 0x1A``，
        16 字节）在 1548 个弹药上取值**全部唯一**——它是身份标识，
        照抄就等于新对象和原件共用一个身份。

        **四个 loc_hash 字段换不了。** ``Name`` / ``Caliber`` /
        ``TypeCategoryName`` / ``WeaponDescriptionToken`` 都是 8 字节
        loc_hash，不可逆，算不出新值。所以克隆体在游戏里会沿用原件的
        名字与分类。实测 ``Name`` 本来就有 520 个重复取值，说明游戏容忍
        重名，所以这不是致命问题——但它是个必须说清楚的限制。

        真正的对象追加发生在 :meth:`set_appended_count` 里，这样撤销栈
        能把它当成一步正常的状态变化。
        """
        src = self._object_by_id(entry.object_id)
        if src is None:
            raise KeyError(f"找不到 object_id={entry.object_id}")
        # 有「已撤销但载荷还留着」的克隆体时先丢掉。再做一个新克隆意味着
        # 重做分支已经作废（和 history 清 redo 是同一件事）；留着的话，
        # 这次物化会把它们**一起加回来**——玩家会看到几个自己早就撤掉的
        # 旧对象凭空复活，而且对象数一次跳好几格。
        if self._appended < len(self._clones):
            del self._clones[self._appended:]
        payload = copy.deepcopy(src)
        payload.pop("offset", None)
        payload["is_top_object"] = True
        replaced: list[str] = []
        for prop in payload["properties"]:
            value = prop["value"]
            if isinstance(value, dict) and value.get("type_id") == 0x1A:
                value["value_hex"] = uuid.uuid4().bytes.hex()
                replaced.append(prop["property_name"])
        self._clones.append(payload)
        try:
            self.set_appended_count(len(self._clones))
        except Exception:
            self._clones.pop()          # 别留下一个登记了却没物化的载荷
            raise
        self.last_clone_guid_fields = replaced
        return self._by_class[entry.class_name][-1]
    def _sync_class(self, cname: str) -> None:
        """对象的增减会改变类计数，而它显示在类列表里。"""
        for info in self.classes:
            if info.name == cname:
                info.count = len(self._by_class.get(cname, ()))
                return

    def _describe_id(self, object_id: int) -> str:
        e = self.entry_of(object_id)
        if e is None:
            return f"#{object_id}"
        return e.title or f"#{object_id}"

    def references_to_any(self, oids: set[int]) -> list[tuple[int, str]]:
        """哪些 ``(对象 id, 字段名)`` 指着这批 id 里的某一个。

        穿透 list —— 只读 ``value["object_id"]`` 会漏掉
        ``ModulesDescriptors`` 这类集合型引用。
        """
        out: list[tuple[int, str]] = []
        for obj in self.decoded["objects"]:
            for prop in obj["properties"]:
                for node in obj_ref_nodes(prop["value"]):
                    if node.get("object_id") in oids:
                        out.append((obj["id"], prop["property_name"]))
                        break
        return out

    def referrers_for_class(self, class_name: str
                            ) -> list[tuple[int, str, int, dict]]:
        """全部指向「这个类」的引用字段实例。

        返回 ``(引用者 id, 字段名, 当前目标 id, 引用节点本身)``。

        判定直接用节点自带的 ``class_id``——obj_ref 的节点里就写着目标类，
        不必回头查对象表。顺带覆盖了指向**本文件外**对象的引用（那些 id
        查不到，但 class_id 仍在）。

        把**节点本身**一起返回：多选窗口改指向时直接写它，不必按
        ``(对象, 字段)`` 再扫一遍对象表去找那个 dict——弹药一个类有
        9001 条候选，重扫一次就是白花 34ms。

        不返回 ``ObjEntry``：为显示三列字符串建 9001 个对象是浪费。
        """
        cid = None
        for i, name in enumerate(self.decoded["classes"]):
            if name == class_name:
                cid = i
                break
        if cid is None:
            return []
        out: list[tuple[int, str, int, dict]] = []
        for obj in self.decoded["objects"]:
            for prop in obj["properties"]:
                for node in obj_ref_nodes(prop["value"]):
                    if node.get("class_id") == cid:
                        out.append((obj["id"], prop["property_name"],
                                    node.get("object_id"), node))
                        break
        return out

    # ---- 保存 ----

    def replacements(self) -> dict:
        from ..core import writer
        repl = writer.sections_of(self.doc)
        repl["OBJE"] = cndf_codec.encode_objects(self.decoded["objects"])
        if self._appended:
            # 追加对象会连带动两个分区，少改一个文件就读不回来：
            #   CHNK 偏移 4 是对象总数（decode 就按它循环读对象）；
            #   TOPO 是顶层对象索引表，实测弹药 1548/1548 全在里面。
            chnk = bytearray(repl["CHNK"])
            struct.pack_into("<I", chnk, 4, len(self.decoded["objects"]))
            repl["CHNK"] = bytes(chnk)
            repl["TOPO"] = struct.pack(f"<{len(self._topo)}I", *self._topo)
        return repl

    def dry_run(self):
        from ..core import writer
        return writer.write_file(self.path, self.doc, self.replacements(),
                                 dry_run=True)

    def save(self):
        from ..core import writer
        return writer.write_file(self.path, self.doc, self.replacements())


def humanize(prop_name: str) -> str:
    """``PhysicalDamages`` -> ``Physical Damages``。

    刻意**不**翻译成中文：属性名来自 NDF 本身，游戏里玩家看到的也是英文，
    两边对得上才不会找错字段。中文说明放在文档里。
    """
    out = []
    for i, ch in enumerate(prop_name):
        if ch.isupper() and i and not prop_name[i - 1].isupper():
            out.append(" ")
        out.append(ch)
    return "".join(out)

OBJECT_REFERENCE = 0xBBBBBBBB
STRING_REF_TYPES = (0x07, 0x1C)


def resolve_text(value, strings):
    """把 strg_ref / trans / pair 引用解成可读文本，非引用返回 ``None``。"""
    if not isinstance(value, dict):
        return None
    t = value.get("type_id")
    if t in STRING_REF_TYPES and "index" in value:
        i = value["index"]
        return strings[i] if 0 <= i < len(strings) else None
    if t == 0x22:
        k = resolve_text(value.get("key"), strings)
        v = resolve_text(value.get("value"), strings)
        if k is not None or v is not None:
            return f"{k}={v}"
    return None


def obj_ref_nodes(value):
    """属性值里全部 ``obj_ref`` 的**节点本身**（不是 id），穿透嵌套。

    与 :func:`collect_refs` 的差别：那个只回收 id，用于「谁引用了我」；
    这里要节点，因为调用方需要读 ``class_id``、也要能**写回去**。
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
            yield v
        for item in v.get("items", ()):
            stack.append(item)
        for key in ("key", "value"):
            if key in v:
                stack.append(v[key])


def count_str_refs(value, usage: Counter) -> None:
    """统计一个属性值里每个字符串下标各出现了多少次。

    **必须穿透 list 与 map.** ``TraitsToken`` 这类列表里的下标同样要算，
    否则「按常用度排序」会把最该出现在最前面的那几个名字排到后面——
    而这个词表恰恰是玩家要改的字段之一。
    """
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, list):
            stack.extend(v)
            continue
        if not isinstance(v, dict):
            continue
        if v.get("type_id") in STR_REF_TYPE_IDS:
            i = v.get("index")
            if isinstance(i, int):
                usage[i] += 1
        for item in v.get("items", ()):
            stack.append(item)
        for key in ("key", "value"):
            sub = v.get(key)
            if isinstance(sub, (dict, list)):
                stack.append(sub)


def collect_refs(value):
    """收集属性值里引用的全部 object_id，穿透 list / pair 嵌套。

    注意不能只读 ``value["object_id"]``：list 型字段的引用在 ``items`` 里，
    标量引用才在顶层。这是本项目最容易写错的地方。
    """
    ids = []

    def walk(v):
        if isinstance(v, dict):
            if v.get("type_id") == OBJECT_REFERENCE:
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
