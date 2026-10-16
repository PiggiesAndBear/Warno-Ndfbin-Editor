"""文本 / 哈希字段：``strg_ref``、``strg_ref`` 列表、``loc_hash``。

**为什么单列一类.** 编辑器原先只认两种非数值字段：``obj_ref``（指向本文件的
对象）与 ``trans_ref``（跨文件路径）。但实测还有三样东西既不是数字、
也不是「指向某个对象」：

============  ==================  ==========================================
type_id       名字                二进制里存什么
============  ==================  ==========================================
``0x07``      ``strg_ref``        STRG 表下标（4 字节），解码时附带文本
``0x1C``      ``file_strg_ref``   同上，另一张表
``0x1D``      ``loc_hash``        8 字节哈希，**不可逆**
============  ==================  ==========================================

它们在 ``TAmmunitionDescriptor`` 上恰好是「游戏怎么描述这个弹药」：

- ``ImpactHappening``  —— 命中时的爆炸特效名
- ``TraitsToken``      —— UI 上那排标签（``HE`` / ``HEAT`` / ``TANDEM`` …）
- ``TypeCategoryName`` —— UI 上的弹药类别名（决定显示 HEAT 还是 HE）
- ``Name`` / ``Caliber`` —— 弹药真名与口径（哈希，不可逆）

**三条编码规则决定了改法**（读 ``cndf_codec.encode_value`` 得到，必须遵守）：

1. ``strg_ref`` 编码**只写 ``index``**，``value`` 只是解码时从 STRG 表查出来的
   缓存 —— 所以改 index 时必须**同步改 value**，否则界面显示与文件内容不一致
2. ``loc_hash`` 编码**只写 ``value_hex``**
3. list 编码会**校验** ``len(items) == length``，不匹配直接抛
   ``NDF collection length mismatch`` —— 所以增删项必须同步改 ``length``

**只从已有字符串里选，不新增.** 新增字符串要重建 STRG 分区，而
``NdfDocument.replacements()`` 对 STRG 是原样透传的。所以本模块的编辑语义是
「换成一个文件里已经存在的字符串」——对 FX 名与标签来说这正好够用。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

#: 字符串引用的 type_id。``0x1C`` 是 ``file_strg_ref``，编码路径与 ``0x07``
#: 相同（都只写 index），只是指向另一张表，所以一起处理。
STR_REF_TYPE_IDS = (0x07, 0x1C)

#: 本地化哈希。
LOC_HASH_TYPE_IDS = (0x1D,)

#: 列表容器的 type_id（0x11 = list，0x12 = map_list）。
LIST_TYPE_IDS = (0x11, 0x12)


def is_str_ref(value) -> bool:
    """单个字符串引用。"""
    return (isinstance(value, dict)
            and value.get("type_id") in STR_REF_TYPE_IDS
            and "index" in value)


def is_loc_hash(value) -> bool:
    """本地化哈希。不可逆——只能从别处照抄。"""
    return (isinstance(value, dict)
            and value.get("type_id") in LOC_HASH_TYPE_IDS
            and "value_hex" in value)


def is_str_list(value) -> bool:
    """整条都是字符串引用的列表。

    **要求每一项都合格，且不为空.** 混着别的类型的列表（``map_list``、
    或者里面掺了标量的）语义不明，做成「逐项换字符串」只会造成误操作。
    空列表也不收——没有项可改，界面上的按钮永远是死的。
    """
    if not isinstance(value, dict) or value.get("type_id") not in LIST_TYPE_IDS:
        return False
    items = value.get("items")
    if not items:
        return False
    return all(is_str_ref(it) for it in items)


def _str_ref_node(index: int, text: str, type_id: int = 0x07) -> dict:
    """造一个新的 ``strg_ref`` 节点，键序与解码器产出的保持一致。"""
    return {"type_id": type_id, "type": "strg_ref",
            "reference_prefix": False, "index": index, "value": text}


class StringPool:
    """STRG 表的可搜索视图，附带「这个字符串被用了多少次」。

    使用次数不是装饰：``Weapon.ndfbin`` 的 STRG 表有 5734 项，
    但弹药特效名只有几十个。按使用次数降序排，玩家要改的那个名字
    基本都在最前面。

    使用次数由 :class:`NdfDocument` 扫一遍全文件的 ``strg_ref`` 得到，
    惰性构建、随对象增删作废。
    """

    def __init__(self, strings, usage=None):
        self.strings: list[str] = list(strings or ())
        #: index -> 被引用次数
        self.usage: dict[int, int] = dict(usage or {})
        self._lower: list[str] | None = None
        self._by_text: dict[str, int] | None = None

    # ---- 基本 ----

    def __len__(self) -> int:
        return len(self.strings)

    def text(self, index) -> str | None:
        if isinstance(index, int) and 0 <= index < len(self.strings):
            return self.strings[index]
        return None

    def index_of(self, text: str) -> int | None:
        """精确匹配（大小写敏感）。同名重复时取下标最小的那个。"""
        if self._by_text is None:
            self._by_text = {}
            for i, s in enumerate(self.strings):
                self._by_text.setdefault(s, i)
        return self._by_text.get(text)

    def uses(self, index: int) -> int:
        return self.usage.get(index, 0)

    # ---- 搜索 ----

    def search(self, needle: str, limit: int = 400,
               prefer: tuple[int, ...] = ()) -> list[tuple[int, str, int]]:
        """模糊搜索，返回 ``[(index, text, uses)]``。

        排序：``prefer`` 里点名要的排最前，然后按使用次数降序，
        再按文本长度升序（短名字通常更通用），最后按下标保证稳定。

        **不排序成字母序**：玩家找 ``RoquetteRecoilessGun`` 时，
        他其实想先看到「同类的其它弹药在用什么」，而那些名字恰好
        都是高频的。
        """
        if self._lower is None:
            self._lower = [s.lower() for s in self.strings]
        low = (needle or "").strip().lower()
        want = list(prefer)
        hits: list[tuple[int, str, int]] = []
        for i, s in enumerate(self.strings):
            if low and low not in self._lower[i]:
                continue
            hits.append((i, s, self.usage.get(i, 0)))
        hits.sort(key=lambda t: (0 if t[0] in want else 1,
                                 -t[2], len(t[1]), t[0]))
        return hits[:limit]

    def common(self, limit: int = 60) -> list[tuple[int, str, int]]:
        """最常被引用的字符串——对话框打开时的默认列表。"""
        items = [(i, s, self.usage.get(i, 0)) for i, s in enumerate(self.strings)]
        items.sort(key=lambda t: (-t[2], len(t[1]), t[0]))
        return [t for t in items if t[2] > 0][:limit]


@dataclass
class StrField:
    """一个 ``strg_ref`` 字段。

    ``leaf`` 直接指向 ``decoded["objects"]`` 里的原位 dict——改它就是改数据。
    """

    name: str
    leaf: dict
    original_index: int
    original_text: str
    coverage: float = 1.0

    @property
    def index(self) -> int | None:
        return self.leaf.get("index")

    @property
    def value(self) -> str:
        """当前字符串。解码时写进 ``value``，改 index 时必须同步维护。"""
        return self.leaf.get("value") or ""

    @property
    def dirty(self) -> bool:
        return self.index != self.original_index

    def set_index(self, index: int, text: str) -> None:
        """换字符串。

        两处一起改：``index`` 是编码真正写入的东西，``value`` 是解码缓存。
        只改一个的话，文件内容变了但界面还显示旧名字——比不能改更糟。
        """
        self.leaf["index"] = index
        self.leaf["value"] = text

    def set_text(self, pool: "StringPool", text: str) -> bool:
        """按文本换。表里没有这个字符串就拒绝（不改数据）。"""
        i = pool.index_of(text)
        if i is None:
            return False
        self.set_index(i, text)
        return True

    def reset(self) -> None:
        self.leaf["index"] = self.original_index
        self.leaf["value"] = self.original_text


@dataclass
class StrListField:
    """一个「整条都是字符串引用」的列表字段，逐项可改、可增删。

    **与** :class:`RefListField` **不同，这里允许增删项.** 对象引用列表的长度
    往往受游戏侧约束（一个槽位对一个 pack），改长度要连带重新编号后续对象；
    而字符串列表的长度只是个计数，``TraitsToken`` 少一个 ``TANDEM``
    本来就是 mod 作者最想做的事。前提是同步改 ``length``——编码器会校验。
    """

    name: str
    leaf: dict
    #: 打开文件时的 ``[(index, text), ...]``，撤销时按它整条重建。
    original: list = dc_field(default_factory=list)
    coverage: float = 1.0

    # ---- 读 ----

    @property
    def items(self) -> list:
        return self.leaf.get("items") or []

    @property
    def length(self) -> int:
        return len(self.items)

    def pairs(self) -> list[tuple[int, str]]:
        return [(it.get("index"), it.get("value") or "") for it in self.items]

    def texts(self) -> list[str]:
        return [it.get("value") or "" for it in self.items]

    def indices(self) -> list[int]:
        return [it.get("index") for it in self.items]

    @property
    def dirty(self) -> bool:
        return self.pairs() != [tuple(p) for p in self.original]

    def dirty_indices(self) -> list[int]:
        """哪些位置与打开时不同（长度变化时，多出来的位置也算）。"""
        now = self.pairs()
        return [i for i in range(max(len(now), len(self.original)))
                if i >= len(self.original) or i >= len(now)
                or now[i] != tuple(self.original[i])]

    # ---- 写 ----

    def _node_template(self) -> int:
        """沿用既有项的 type_id——``strg_ref`` 与 ``file_strg_ref`` 编码路径
        相同但不能互换，新增项必须跟同列表里的其它项一致。"""
        for it in self.items:
            if it.get("type_id") in STR_REF_TYPE_IDS:
                return it["type_id"]
        return 0x07

    def _sync_length(self) -> None:
        """编码器会校验 ``len(items) == length``，不同步直接抛异常。"""
        self.leaf["length"] = len(self.items)

    def set_item(self, i: int, index: int, text: str) -> None:
        it = self.items[i]
        it["index"] = index
        it["value"] = text

    def set_text(self, i: int, pool: "StringPool", text: str) -> bool:
        j = pool.index_of(text)
        if j is None:
            return False
        self.set_item(i, j, text)
        return True

    def add(self, index: int, text: str) -> int:
        """追加到末尾，返回新项的位置。"""
        self.items.append(_str_ref_node(index, text, self._node_template()))
        self._sync_length()
        return len(self.items) - 1

    def remove(self, i: int) -> None:
        del self.items[i]
        self._sync_length()

    def move(self, i: int, delta: int) -> int:
        """上移/下移一项。标签的**顺序**在 UI 上是可见的，所以顺序有意义。"""
        j = i + delta
        if not (0 <= j < len(self.items)):
            return i
        items = self.items
        items[i], items[j] = items[j], items[i]
        return j

    def set_pairs(self, pairs) -> None:
        """整条替换成 ``[(index, text), ...]``。

        **撤销必须走这一条路.** 增删项没法逐项回退——原列表有 3 项、
        现在是 2 项，第 3 项是「被删掉」还是「本来就没有」在逐项比较里
        分不出来。整条替换没有这个歧义。
        """
        tmpl = self._node_template()
        self.leaf["items"] = [_str_ref_node(i, t, tmpl) for i, t in pairs]
        self._sync_length()

    def reset(self) -> None:
        """按打开时的样子整条重建——这样增删也能退回去。"""
        self.set_pairs(self.original)


@dataclass
class HashField:
    """一个 ``loc_hash`` 字段。

    **哈希不可逆**，所以这里没有「输入一个新值」这种操作——只有
    :meth:`take_from`：从**另一个对象**身上照抄。对 ``TypeCategoryName``
    这种「UI 类别名」来说，这正是玩家需要的：他不知道哈希对应的文本，
    但他知道「我要变得和那个炮弹一样」。
    """

    name: str
    leaf: dict
    original_hex: str
    coverage: float = 1.0

    @property
    def hex(self) -> str:
        return (self.leaf.get("value_hex") or "").lower()

    @property
    def dirty(self) -> bool:
        return self.hex != (self.original_hex or "").lower()

    def set_hex(self, hexs: str) -> None:
        """直接写 ``value_hex``——编码器只认它。"""
        self.leaf["value_hex"] = hexs

    def take_from(self, other: "HashField") -> None:
        """照抄另一个字段的哈希。"""
        self.set_hex(other.original_hex if not other.dirty else other.hex)

    def reset(self) -> None:
        self.leaf["value_hex"] = self.original_hex
