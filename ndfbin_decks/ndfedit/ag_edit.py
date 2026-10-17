# -*- coding: utf-8 -*-
"""AG pawn 属性的写入 —— clone-on-write。

**为什么不能直接改。** 一个模块对象被成百个营共用（实测：行动点 47、
移动方式 378、战斗角色 206、防空半径 126）。原地改一个数字，等于一次性
改掉那一整批营 —— 这是这个功能唯一会出大事故的地方。

**实测前提**（2026-10-17，柏林 mod，`Pawn.ndfbin`）：

* **模块对象没有 GUID.** 全文件只有 ``TEntityDescriptor`` 带 ``DescriptorId``
  （``type_id 0x1A``），所有模块类都没有。所以克隆一个模块**不会**触发换
  身份的逻辑 —— 模块本来就没有身份，这在语义上是对的。
* **重指向只能走** :meth:`RefIndex.repoint`：它会连 ``class_id`` 一起改。
  只改 ``object_id`` 会让 ``decode`` 直接拒绝整个文件。
  :meth:`RefListField.nodes` 返回的是**快照**，改它没有任何效果。
* **保存会重新压缩.** 解压后只 +54 B，但压缩后的体积会变 —— 游戏引擎的
  zstd 编码器与我们不同（见 :mod:`core.writer` 的模块说明）。数据本身完整，
  这一点已用「重载后逐对象核对」验证过。

**为什么批量改要按模块分组.** 命中的营常常共用同一个模块：

============================  ================================
情况                          做法
============================  ================================
整组营 == 该模块的全部用户      直接改模块（克隆是浪费）
整组营 < 该模块的全部用户      每个营克隆一份（否则会误伤没命中的）
============================  ================================
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field as dc_field

from .ag_view import PAWN_CLASS, Attr, PawnView, module_ids
from .schema import NdfDocument


class AgEditError(Exception):
    """写入失败，且原因对玩家有意义（不是程序 bug）。"""


@dataclass
class EditResult:
    """改一个营的一个属性的账。"""

    attr: str
    #: 是否克隆了新模块（共享才有）
    cloned: bool = False
    #: 模块类名
    module: str = ""
    old_module: int = 0
    new_module: int = 0
    #: 原模块被多少个营共用（1 = 独占）
    shared: int = 1
    #: 值是否真的变了（原本就相等时为 False，界面要说「没变」）
    changed: bool = False
    old_value: object = None
    new_value: object = None

    def summary(self) -> str:
        if not self.changed:
            return "值没有变化。"
        bits = [f"{self.attr}: {self.old_value} → {self.new_value}"]
        if self.cloned:
            bits.append(f"克隆了新模块 #{self.new_module}"
                        f"（原 #{self.old_module} 仍被 {self.shared - 1} 个营使用）")
        return "；".join(bits)


@dataclass
class BatchResult:
    """批量改的账。"""

    attr: str
    new_value: object = None
    matched: int = 0
    changed: int = 0
    unchanged: int = 0
    cloned: int = 0
    #: 直接改模块（整组命中）的次数
    in_place: int = 0
    #: 没有这个模块/字段、因而跳过的营数
    skipped: int = 0
    details: list[EditResult] = dc_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.changed > 0

    def summary(self) -> str:
        if not self.matched:
            return "没有营满足筛选条件。"
        if not self.changed:
            return f"命中 {self.matched} 个营，但它们本来就是这个值。"
        bits = [f"命中 {self.matched} 个营，改动 {self.changed} 个"]
        if self.unchanged:
            bits.append(f"{self.unchanged} 个原本就是这个值")
        if self.cloned:
            bits.append(f"克隆了 {self.cloned} 个模块（其余共用原模块的营未受影响）")
        if self.in_place:
            bits.append(f"{self.in_place} 个模块是整组命中、直接改的")
        if self.skipped:
            bits.append(f"{self.skipped} 个营没有这个字段、已跳过")
        return "，".join(bits) + "。"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def module_users(nd: NdfDocument) -> Counter:
    """每个模块 object_id 被多少个营引用。

    用**营的引用数**而不是全文件引用数：写入时要判断的是「改这个模块会影响
    多少个营」，别的对象引用它不影响这个判断。
    """
    users: Counter = Counter()
    for obj in nd.decoded["objects"]:
        if obj.get("class") != PAWN_CLASS:
            continue
        for mid in module_ids(obj):
            users[mid] += 1
    return users


def repoint_module(nd: NdfDocument, pawn_id: int, old_id: int,
                   new_id: int) -> bool:
    """把一个营的 ``ModulesDescriptors`` 里指向 ``old_id`` 的那一条改到
    ``new_id``。

    走 :meth:`RefIndex.repoint`，它会连 ``class_id`` 一起改 —— 只改
    ``object_id`` 会让整个文件解不开。返回是否真的改到了。
    """
    entry = nd.entry_of(pawn_id)
    if entry is None:
        return False
    field = entry.ref_lists.get("ModulesDescriptors")
    if field is None:
        return False
    for rf in field.items:
        if rf.key == old_id:
            field.index.repoint(rf.leaf, "obj", new_id)
            return True
    return False


def _coerce(value, kind: str):
    """把界面上的值转成字段要的类型。"""
    if kind == "bool":
        return bool(value)
    if kind == "int":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            raise AgEditError(f"{value!r} 不是整数")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AgEditError(f"{value!r} 不是数字")


def _same(a, b, kind: str) -> bool:
    if kind == "float":
        try:
            return round(float(a), 4) == round(float(b), 4)
        except (TypeError, ValueError):
            return False
    return a == b


# ---------------------------------------------------------------------------
# 单个营
# ---------------------------------------------------------------------------


def set_attr(nd: NdfDocument, pawn: PawnView, attr: Attr, value,
             *, users: Counter | None = None) -> EditResult:
    """改一个营的一个标量属性。

    模块被别的营共用时**先克隆、只改这一个营的引用**，再改克隆体 ——
    这样其余营一个字节不动。

    ``users`` 可传入 :func:`module_users` 的结果以省掉重复扫描；批量改时
    必须传，否则每个营都会重扫一遍全文件。
    """
    if attr.kind not in ("float", "int", "bool"):
        raise AgEditError(
            f"{attr.label} 是「换引用」型字段（{attr.kind}），不走这条路径")

    mod_id = pawn.modules.get(attr.module)
    if mod_id is None:
        raise AgEditError(
            f"这个营没有 {attr.module}，也就没有「{attr.label}」")

    entry = nd.entry_of(mod_id)
    if entry is None:
        raise AgEditError(f"找不到模块对象 #{mod_id}")

    field = entry.fields.get(attr.field)
    if field is None:
        raise AgEditError(
            f"这个营的 {attr.module} 上没有「{attr.label}」字段"
            f"（该字段只覆盖部分营）")

    new_value = _coerce(value, attr.kind)
    old_value = field.value
    if _same(old_value, new_value, attr.kind):
        return EditResult(attr=attr.key, module=attr.module,
                          old_module=mod_id, new_module=mod_id,
                          old_value=old_value, new_value=new_value,
                          changed=False)

    if users is None:
        users = module_users(nd)
    shared = users.get(mod_id, 1)

    cloned = False
    target = mod_id
    if shared > 1:
        fresh = nd.clone_object(entry)
        if not repoint_module(nd, pawn.entity_id, mod_id, fresh.object_id):
            # 克隆出来了却没接上 —— 把它撤掉，否则会留下一个谁都不指向的
            # 孤儿对象，而且对象数会莫名其妙多一个。
            nd.set_appended_count(max(0, nd.appended_count() - 1))
            raise AgEditError("克隆成功，但没能把这个营的引用改过去")
        target = fresh.object_id
        cloned = True
        # 克隆体的字段对象是新的，必须重新取
        field = nd.entry_of(target).fields.get(attr.field)
        if field is None:
            raise AgEditError("克隆体上找不到目标字段")

    field.leaf["value"] = new_value
    return EditResult(attr=attr.key, module=attr.module, cloned=cloned,
                      old_module=mod_id, new_module=target, shared=shared,
                      changed=True, old_value=old_value, new_value=new_value)


# ---------------------------------------------------------------------------
# 批量
# ---------------------------------------------------------------------------


def apply_attr(nd: NdfDocument, pawns: list[PawnView], attr: Attr,
               value) -> BatchResult:
    """把一批营的同一个属性改成同一个值。

    **按模块分组，这是效率与安全的同一条线**：

    * 命中的营如果正好是某模块的**全部**用户 → 直接改那个模块，
      一个克隆都不用（「把所有 AA 营的 12 改成 15」就是这种情况）；
    * 只命中一部分 → 每个营克隆一份再改，否则会误伤没命中的营。
    """
    if attr.kind not in ("float", "int", "bool"):
        raise AgEditError(
            f"{attr.label} 是「换引用」型字段（{attr.kind}），不走这条路径")

    new_value = _coerce(value, attr.kind)
    result = BatchResult(attr=attr.key, new_value=new_value,
                         matched=len(pawns))
    users = module_users(nd)

    # 按「当前指向的模块」分组
    groups: dict[int, list[PawnView]] = defaultdict(list)
    for p in pawns:
        mid = p.modules.get(attr.module)
        if mid is None:
            result.skipped += 1
            continue
        entry = nd.entry_of(mid)
        if entry is None or entry.fields.get(attr.field) is None:
            result.skipped += 1
            continue
        groups[mid].append(p)

    for mod_id, members in groups.items():
        total = users.get(mod_id, 1)
        entry = nd.entry_of(mod_id)
        if entry is None:
            result.skipped += len(members)
            continue

        if len(members) >= total:
            # 整组命中：直接改模块，所有用户一起变，语义上就是玩家想要的
            field = entry.fields.get(attr.field)
            before = field.value
            if _same(before, new_value, attr.kind):
                result.unchanged += len(members)
                continue
            field.leaf["value"] = new_value
            result.in_place += 1
            result.changed += len(members)
            result.details.append(EditResult(
                attr=attr.key, module=attr.module, old_module=mod_id,
                new_module=mod_id, shared=total, changed=True,
                old_value=before, new_value=new_value))
            continue

        # 部分命中：每个营要自己的一份
        for p in members:
            try:
                one = set_attr(nd, p, attr, new_value, users=users)
            except AgEditError:
                result.skipped += 1
                continue
            result.details.append(one)
            if not one.changed:
                result.unchanged += 1
                continue
            result.changed += 1
            if one.cloned:
                result.cloned += 1
            # 克隆后引用计数变了：原模块少一个用户，克隆体多一个
            users[one.old_module] = max(0, users.get(one.old_module, 1) - 1)
            users[one.new_module] = 1
    return result


def missing_field_pawns(pawns: list[PawnView], attr: Attr) -> list[PawnView]:
    """这批营里**没有**这个属性的那些。

    给「把一个本来没有的属性赋予某个营」这个需求用 —— 那种情况要克隆
    模块再追加字段，与「改已有字段」是两条路。
    """
    return [p for p in pawns
            if p.modules.get(attr.module) is not None
            and attr.key not in p.values]
