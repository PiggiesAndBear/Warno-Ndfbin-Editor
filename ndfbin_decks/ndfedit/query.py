"""按字段值筛选对象，并批量改写。

**实测依据**（``tools/probe_bulk.py``，Weapon.ndfbin 的 1548 条弹药）：

========================  ========  ========  ==================
字段                      有值条数  不同取值  被 ≥2 条共用的占比
========================  ========  ========  ==================
``MaximumRangeGRU``          1396        74              99.2%
``PhysicalDamages``          1547       135              97.9%
========================  ========  ========  ==================

三个结论决定了这里的实现：

1. **按精确值筛是有效的。** 射程 ``1925`` 一条就命中 152 条弹药
   （其中 Shell 150 条），不是「等于按对象筛」。
2. **匹配方式必须按值类型自适应。** ``PhysicalDamages`` 的 float32 实测值是
   ``0.13199999928474426``——玩家在界面上看到 ``0.1320``、照着输入 ``0.132``，
   用 ``==`` 比较会**一条都命不中**。所以 float 一律按显示精度比较。
3. **低基数字段要当枚举处理。** 弹种只有 6 种取值，玩家想的是
   「Shell 和 AA Missile 都要」，让他手输 ``1`` 是不合理的。
   取值种类不超过 :data:`ENUM_MAX` 的字段自动提供多选。

设计约束：条件可任意增删，条件之间是 AND。
OR 暂不支持——实测的组合筛选场景（弹种 + 射程）都是 AND，
先做 OR 只会让界面多一层玩家理解不了的结构。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field as dc_field

#: float 比较与显示共用的精度。与 ``gui.DECIMALS`` 保持一致——
#: 玩家看到的和筛选用的是同一个口径，才不会有「明明显示相等却筛不到」。
FLOAT_DIGITS = 4

#: 取值种类不超过这个数的字段，UI 会把它当枚举，提供「是其中之一」+ 多选。
ENUM_MAX = 12

# 匹配方式
MATCH_EQUAL = "equal"
MATCH_GE = "ge"
MATCH_LE = "le"
MATCH_RANGE = "range"
MATCH_IN = "in"

MATCH_LABELS = {
    MATCH_EQUAL: "等于",
    MATCH_GE: "不小于",
    MATCH_LE: "不大于",
    MATCH_RANGE: "在区间内",
    MATCH_IN: "是其中之一",
}


def coerce_value(value, kind: str):
    """把玩家输入转成字段实际需要的 Python 类型。

    与 ``schema.NdfDocument._coerce`` 同源，但那个是按模板的 ``type_id``，
    这个按已经推断好的 ``kind``——筛选界面手上只有 ``kind``。
    """
    if kind == "bool":
        return bool(value)
    if kind == "float":
        return float(value)
    return int(round(float(value)))


def values_equal(a, b, kind: str) -> bool:
    """按字段类型比较两个值是否「玩家看起来相等」。"""
    if kind == "bool":
        return bool(a) == bool(b)
    if kind == "float":
        try:
            return (round(float(a), FLOAT_DIGITS)
                    == round(float(b), FLOAT_DIGITS))
        except (TypeError, ValueError):
            return False
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return a == b


@dataclass
class Condition:
    """一个筛选条件。

    ``op`` 决定读哪几个字段：``value`` 用于等值 / 比较，``low`` ``high``
    用于区间，``values`` 用于多选。
    """

    field: str
    op: str = MATCH_EQUAL
    value: object = None
    low: float | None = None
    high: float | None = None
    values: tuple = ()
    #: 比较用的类型（由界面从字段表填入）。缺省按 float 处理。
    kind: str = "float"

    # ---- 匹配 ----

    def match(self, v) -> bool:
        """这个值满不满足条件。字段不存在时由调用方负责返回 False。"""
        if v is None:
            return False
        if self.op == MATCH_IN:
            try:
                return v in self.values
            except TypeError:
                return False
        if self.op == MATCH_RANGE:
            if self.low is None or self.high is None:
                return False
            try:
                x = float(v)
            except (TypeError, ValueError):
                return False
            lo, hi = sorted((float(self.low), float(self.high)))
            return lo <= x <= hi
        if self.op == MATCH_GE:
            try:
                return float(v) >= float(self.value)
            except (TypeError, ValueError):
                return False
        if self.op == MATCH_LE:
            try:
                return float(v) <= float(self.value)
            except (TypeError, ValueError):
                return False
        return values_equal(v, self.value, self.kind)

    # ---- 描述 ----

    def describe(self, humanize=None, labeler=None) -> str:
        """``labeler(field, value)`` 可把原始值换成人读的标签。

        没有它的话弹种条件会写成「ProjectileType 是 1」——玩家不知道 1 是
        Shell。内核不认识任何枚举，标签由界面注入（弹药页传
        ``projectile_label``），这样换一个文件也不会失效。
        """
        name = humanize(self.field) if humanize else self.field

        def show(x):
            return labeler(self.field, x) if labeler else str(x)

        if self.op == MATCH_IN:
            vals = "、".join(show(x) for x in self.values) or "（未选）"
            return f"{name} 是 {vals}"
        if self.op == MATCH_RANGE:
            return f"{name} 在 {self.low} ~ {self.high}"
        label = MATCH_LABELS.get(self.op, self.op)
        return f"{name} {label} {show(self.value)}"

    def is_usable(self) -> bool:
        """条件填完整了吗。没填完的条件不该参与筛选。

        界面刚建出一行时字段和值都是空的——如果把它算作「等于 0」，
        筛选立刻命中 0 条，看起来像功能坏了。
        """
        if not self.field:
            return False
        if self.op == MATCH_IN:
            return bool(self.values)
        if self.op == MATCH_RANGE:
            return self.low is not None and self.high is not None
        return self.value is not None


def value_counts(entries, field_name: str) -> Counter:
    """该字段在各对象上的取值分布。

    界面用它决定「这是枚举还是连续量」——取值种类少的多选更好用。
    """
    c: Counter = Counter()
    for e in entries:
        f = e.fields.get(field_name)
        if f is not None:
            try:
                c[f.value] += 1
            except TypeError:                     # 不可哈希的值，跳过
                continue
    return c


def is_enum_like(entries, field_name: str) -> bool:
    """这个字段该不该当枚举处理。"""
    counts = value_counts(entries, field_name)
    return 0 < len(counts) <= ENUM_MAX


@dataclass
class Query:
    """一组 AND 条件。"""

    conditions: list[Condition] = dc_field(default_factory=list)

    def usable(self) -> list[Condition]:
        return [c for c in self.conditions if c.is_usable()]

    def matches(self, entry) -> bool:
        for c in self.usable():
            f = entry.fields.get(c.field)
            if f is None:
                # 对象没有这个字段 = 不匹配。这也是「只改有射程的那些」
                # 能成立的原因：152 条缺 MaximumRangeGRU 的弹药自然被排除。
                return False
            if not c.match(f.value):
                return False
        return True

    def select(self, entries) -> list:
        return [e for e in entries if self.matches(e)]

    def describe(self, humanize=None, labeler=None) -> str:
        cs = self.usable()
        if not cs:
            return "（无条件，命中全部对象）"
        return " 且 ".join(c.describe(humanize, labeler) for c in cs)


@dataclass
class BulkResult:
    """一次批量改写的账。"""

    field: str
    matched: int = 0
    changed: int = 0
    unchanged: int = 0
    new_value: object = None
    #: 改前的取值分布，按条数降序。用于「这次改动到底动了什么」。
    before: Counter = dc_field(default_factory=Counter)
    #: 实际被改动的对象 id
    touched: list[int] = dc_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.changed > 0

    def summary(self) -> str:
        if not self.matched:
            return "没有对象满足筛选条件。"
        if not self.changed:
            return (f"命中 {self.matched} 条，但它们本来就都是 "
                    f"{self.new_value}，无需改动。")
        bits = [f"命中 {self.matched} 条，改动 {self.changed} 条"]
        if self.unchanged:
            bits.append(f"{self.unchanged} 条原本就是这个值")
        return "，".join(bits) + "。"

    def before_text(self, limit: int = 4, labeler=None) -> str:
        """改前的值分布，让玩家确认「我要动的是这些」。"""
        if not self.before:
            return ""
        items = self.before.most_common(limit)
        text = "、".join(
            f"{labeler(self.field, v) if labeler else v} × {n}"
            for v, n in items)
        if len(self.before) > limit:
            text += f" …共 {len(self.before)} 种取值"
        return text


def preview(entries, query: Query, field_name: str, new_value,
            kind: str) -> BulkResult:
    """算一遍而不落笔——界面在确认前先拿它显示「将改动 N 条」。"""
    hits = query.select(entries)
    res = BulkResult(field=field_name, matched=len(hits),
                     new_value=coerce_value(new_value, kind))
    for e in hits:
        f = e.fields.get(field_name)
        if f is None:
            continue
        cur = f.value
        res.before[cur] += 1
        if values_equal(cur, res.new_value, kind):
            res.unchanged += 1
        else:
            res.changed += 1
            res.touched.append(e.object_id)
    return res


def apply_value(entries, query: Query, field_name: str, new_value,
                kind: str) -> BulkResult:
    """筛选并批量设为固定值。

    只改 ``Field.leaf["value"]``——那是 ``decoded["objects"]`` 里的原位 dict，
    改它就是改数据，与手动逐个编辑走的是同一条路，dirty 与撤销逻辑自动生效。
    """
    res = preview(entries, query, field_name, new_value, kind)
    touched = set(res.touched)          # 先建集合：循环里现建是 O(n²)
    for e in entries:
        if e.object_id not in touched:
            continue
        f = e.fields.get(field_name)
        if f is not None:
            f.leaf["value"] = res.new_value
    return res
