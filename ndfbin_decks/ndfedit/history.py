"""撤销 / 重做。

## 为什么不做「命令对象」

最自然的做法是命令模式：每个改动点记一条 ``(目标, 旧值, 新值)``，
``undo()`` 把旧值写回去。但本项目里能改数据的落点有六处——标量编辑、
实体聚合行编辑、换引用目标、换「一串引用」里的某一项、补加/摘掉字段、
批量设值——任何一处忘了上报，栈就和真实状态错开了。
而错开的后果是**撤销之后文件停在一个谁都没预料到的状态**，
比没有撤销更糟：玩家会以为已经退回去了，然后再保存一次。

所以这里用**脏状态差分**：每次改动后重新扫一遍「当前有哪些字段偏离了
原值」，与上一次快照比对，差出来的就是这一步。换来三件事：

- **不可能失同步**：差分直接读文档本身，不依赖任何上报点。
- **批量改 150 条天然是一步**，不需要手工包一层复合命令。
- **结构改动自动包含**：补加/摘掉字段在快照里以「这个键在不在」表达。

代价是每次提交要扫一遍全文件。实测 Weapon.ndfbin（24168 个对象、
122621 个字段）约 50ms，Unit.ndfbin 约 65ms。这个扫描顺带算出了界面
状态栏需要的全部计数，所以它替代了原先分开跑的五次全量扫描
（``dirty_count`` / ``dirty_total`` / ``added_total`` / ``ref_dirty_total`` /
``ref_list_dirty_total``），净代价接近零。

## 撤销的语义边界

- 只能退回到**打开这个文件时**的样子，不跨文件、不跨保存。
- 保存后文档会重新加载（``writer`` 需要新的 ``raw_data`` 才能继续编辑），
  历史随之清空。这是刻意的：保存是提交点，不是可撤销操作。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field as dc_field

from .schema import humanize

__all__ = ["History", "Step", "Stats", "MISSING", "MERGE_WINDOW"]

#: 快照里的「这个键不存在」。
#: 必须用独立哨兵而不是 ``None``——路径引用的 ``value`` 本身就可能是 ``None``，
#: 用 ``None`` 表示缺失会把「值恰好是空」和「根本没有这个键」混为一谈。
MISSING = object()

#: 一个引用节点的状态就是这几个键。必须与 ``RefField.snapshot`` 取同一组：
#: ``repoint`` 也只动这几个，两边不一致就会撤销出「半个引用」
#: （``object_id`` 与 ``class_id`` 不匹配，``decode`` 直接拒绝）。
_REF_KEYS = ("object_id", "class_id", "index", "value")

_FIELD, _REF, _LIST = "field", "ref", "list"
#: 文本 / 哈希字段。
#: 注意 ``_STRLIST`` 的状态值是**整条列表**（``((index, text), ...)``），
#: 不是逐项——增删项无法用逐项比较表达（原列表有 3 项、现在 2 项时，
#: 第 3 项是「被删掉」还是「本来就没有」分不出来）。
_STR, _STRLIST, _HASH = "str", "strlist", "hash"
#: 「追加了几个对象」也是一个状态。追加不是字段值的改变，脏状态差分看不见，
#: 所以克隆之后必须靠这个键才能退回来。占用 object_id = -1 这个保留槽位
#: （真实对象的编号都 >= 0）。
_APPEND = "append"
_APPEND_SLOT = -1

#: 连续微调的合并窗口（秒）。
#: 按住方向键连点 20 下把射程从 1925 调到 2100，不该要按 20 次 Ctrl+Z。
#: 只有**键集完全相同**且在窗口内的连续两步才合并，所以「改完 A 改 B
#: 再回来改 A」仍是三步（键集不同），不会糊成一团。
MERGE_WINDOW = 1.5

#: 状态键：(类别, 字段名, 下标)。下标只对列表项有意义，其余恒为 0。
Key = tuple[str, str, int]
#: {object_id: {状态键: 值}}。值对字段是标量，对引用是叶子字典的一份拷贝。
State = dict[int, dict[Key, object]]


@dataclass
class Stats:
    """状态栏要的计数——和快照同一次遍历算出，省掉五遍全量扫描。"""

    objects: int = 0        # 有改动的对象数（含只补加了字段的）
    fields: int = 0         # 值被改过的字段数
    added: int = 0          # 玩家补加的字段数
    refs: int = 0           # 换过目标的引用字段数
    lists: int = 0          # 换过目标的「一串引用」项数
    strs: int = 0           # 换过字符串的文本字段数
    str_lists: int = 0      # 改过的字符串列表数（含增删项）
    hashes: int = 0         # 照抄过的哈希字段数
    #: 新追加进来的对象数（克隆）。刻意**不进** :attr:`items`——那不是
    #: 「改了一处」，是「多了个对象」。但它必须算作未保存改动：不加的话，
    #: 克隆完一个字段都没改时 ``dirty_count()`` 是 0，保存按钮是灰的，
    #: 克隆体根本存不下去。
    new_objects: int = 0

    @property
    def items(self) -> int:
        """状态键总数，用来给一步改动量级定称呼。"""
        return (self.fields + self.added + self.refs + self.lists
                + self.strs + self.str_lists + self.hashes)


@dataclass
class Step:
    """一步改动。``delta`` 就是「撤销它需要写回什么」。"""

    label: str
    #: {object_id: {状态键: (改前, 改后)}}，两侧都可能是 :data:`MISSING`。
    delta: dict[int, dict[Key, tuple[object, object]]]
    at: float = dc_field(default_factory=time.monotonic)

    @property
    def objects(self) -> int:
        return len(self.delta)

    @property
    def items(self) -> int:
        return sum(len(v) for v in self.delta.values())

    def keys(self) -> set[tuple[int, Key]]:
        return {(oid, k) for oid, ks in self.delta.items() for k in ks}


# ---- 纯函数：快照比对 ----


def _leaf_state(leaf: dict) -> dict:
    return {k: leaf[k] for k in _REF_KEYS if k in leaf}


def _same(a, b) -> bool:
    """MISSING 只等于 MISSING；其余按值比。"""
    if a is MISSING or b is MISSING:
        return a is b
    return a == b


def _delta(before: State, after: State) -> dict:
    """两次快照之间真正变了的东西。"""
    out: dict = {}
    for oid in set(before) | set(after):
        b = before.get(oid, {})
        a = after.get(oid, {})
        changed = {}
        for key in set(b) | set(a):
            bv = b.get(key, MISSING)
            av = a.get(key, MISSING)
            if not _same(bv, av):
                changed[key] = (bv, av)
        if changed:
            out[oid] = changed
    return out


def _compose(first: dict, second: dict) -> dict:
    """把相邻两步合成一步（``first`` 在前）。

    合成后净效果为零的键会被丢掉——「改出去又改回来」不该在历史里
    留下一格什么也不干的记录。
    """
    out: dict = {}
    for oid in set(first) | set(second):
        f = first.get(oid, {})
        s = second.get(oid, {})
        merged = {}
        for key in set(f) | set(s):
            fv = f.get(key)
            sv = s.get(key)
            b = fv[0] if fv else sv[0]
            a = sv[1] if sv else fv[1]
            if not _same(b, a):
                merged[key] = (b, a)
        if merged:
            out[oid] = merged
    return out


class History:
    """一个打开文档的撤销历史。

    由 :attr:`NdfDocument.history` 惰性创建，随文档一起丢失——
    换文件或保存后重新加载就是一份新历史。
    """

    def __init__(self, nd, *, limit: int = 200):
        self.nd = nd
        self.limit = limit
        self._undo: list[Step] = []
        self._redo: list[Step] = []
        # 条目一律现查（``nd.entry_of`` 带 id 索引缓存），**不缓存到本对象里**。
        # 早期版本在这里存了一份 {object_id: ObjEntry}，于是克隆出来的对象
        # 不在里面——玩家改克隆体的字段，差分看不见，撤不回来。
        self._stats: Stats | None = None
        #: 下一次提交是否禁止合并（见 :meth:`break_merge`）。
        self._no_merge = False
        self._state, self._stats = self._scan()

    # ---- 扫描 ----

    def _scan(self) -> tuple[State, Stats]:
        """一趟扫完「脏状态快照」和「状态栏计数」。"""
        state: State = {}
        stats = Stats()
        # 遍历活数据而不是构造时的快照——克隆出来的对象也在里面。
        for es in self.nd._by_class.values():
            for e in es:
                got: dict = {}
                for name, f in e.fields.items():
                    if f.added:
                        stats.added += 1
                    if f.dirty:
                        stats.fields += 1
                    # 补加字段即使值等于模板也算改动：对象结构变了，
                    # 游戏会开始读这个属性，所以必须进快照（撤销时要整个摘掉）。
                    if f.added or f.dirty:
                        got[(_FIELD, name, 0)] = f.value
                for name, r in e.refs.items():
                    if r.dirty:
                        stats.refs += 1
                        got[(_REF, name, 0)] = _leaf_state(r.leaf)
                for name, l in e.ref_lists.items():
                    if l.dirty:
                        # 整条列表，不是逐项。列表现在可以增删，而增删会
                        # 移动后面所有项的索引——逐项状态在那种情况下
                        # 既表达不了「多出来的一项」，也退不回去。
                        # 快照存**整份** leaf：`type_id` 是编码依据，
                        # 少了它重建出来的节点就不被认作引用了。
                        stats.lists += len(l.dirty_indices())
                        got[(_LIST, name, 0)] = tuple(
                            dict(r.leaf) for r in l.items)
                for name, s in e.strs.items():
                    if s.dirty:
                        stats.strs += 1
                        # 只存下标。文本是它的函数（STRG 表里查出来的），
                        # 写回时现查即可——存进去反而多一处可能不一致的副本。
                        got[(_STR, name, 0)] = s.index
                for name, l in e.str_lists.items():
                    if l.dirty:
                        stats.str_lists += 1
                        # 整条列表，不是逐项：见 ``_STRLIST`` 的说明。
                        got[(_STRLIST, name, 0)] = tuple(l.pairs())
                for name, h in e.hashes.items():
                    if h.dirty:
                        stats.hashes += 1
                        got[(_HASH, name, 0)] = h.hex
                if got:
                    state[e.object_id] = got
                    stats.objects += 1
        # 追加的对象数单独占一个槽位。值为 0 时不写进去——「没有克隆体」
        # 和「字段干净」一样属于默认态，进快照只会让每个 delta 都多一条。
        appended = self.nd.appended_count()
        if appended:
            state[_APPEND_SLOT] = {(_APPEND, "", 0): appended}
            stats.new_objects = appended
        return state, stats

    def invalidate(self) -> None:
        """让缓存的状态栏计数作废——由界面在「改了数据但没提交」时调用。

        正常路径不需要它：所有改动都会经过 :meth:`commit`。留着是为了
        万一有改动绕过了提交，界面宁可多扫一遍，也不要显示一个假的计数。
        """
        self._stats = None

    def break_merge(self) -> None:
        """让下一次提交一定开新的一步。

        「撤销这一条的全部改动」这种明确动作需要它。不加的话：玩家刚撤销完
        一次改动、紧接着点「撤销这一条」，两下的净效果为零，那一步就被
        合并规则吃掉了——记录里什么都不剩，这一次点击变得无法回退，
        而这恰恰是撤销功能最该守住的那种操作。
        """
        self._no_merge = True

    def stats(self) -> Stats:
        """状态栏计数。命中缓存时零开销。"""
        if self._stats is None:
            _, self._stats = self._scan()
        return self._stats

    def snapshot(self) -> State:
        """当前「偏离原值」的全部状态。

        只收脏字段与补加字段——没动过的字段必然等于 ``Field.original``，
        撤销时一句 ``reset()`` 就够，不必占快照。所以快照大小只和
        **被改过的东西**成正比，与文件规模无关。
        """
        return self._scan()[0]

    # ---- 读 ----

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def depth(self) -> tuple[int, int]:
        """(可撤销步数, 可重做步数)。"""
        return len(self._undo), len(self._redo)

    def steps(self) -> list[str]:
        """已记录的步骤说明，最近的在最后（给测试与调试看）。"""
        return [s.label for s in self._undo]

    # ---- 写 ----

    def commit(self) -> Step | None:
        """把「自上次提交以来的改动」压成一步。

        没有实际变化时返回 ``None``——界面每次改动信号都会调它，
        空调用必须是无害的。

        合并规则：与栈顶那一步**状态键完全相同**且在 :data:`MERGE_WINDOW`
        秒内，就压成一步（连点方向键微调）。
        """
        now = time.monotonic()
        force = self._no_merge
        self._no_merge = False
        cur, stats = self._scan()
        self._stats = stats
        delta = _delta(self._state, cur)
        self._state = cur
        if not delta:
            return None

        new_keys = {(oid, k) for oid, ks in delta.items() for k in ks}
        top = self._undo[-1] if self._undo else None
        if (top is not None and not force and top.keys() == new_keys
                and now - top.at < MERGE_WINDOW):
            merged = _compose(top.delta, delta)
            self._undo.pop()
            self._redo.clear()
            if not merged:
                return None                 # 改出去又改回来，净效果为零
            step = Step(top.label, merged, now)
            self._undo.append(step)
            return step

        self._redo.clear()                  # 有了新改动，原来的重做分支作废
        step = Step(self._label(delta), delta, now)
        self._undo.append(step)
        if len(self._undo) > self.limit:
            del self._undo[0]
        return step

    def undo(self) -> Step | None:
        if not self._undo:
            return None
        step = self._undo.pop()
        self._apply(step, forward=False)
        self._state, self._stats = self._scan()
        self._redo.append(step)
        return step

    def redo(self) -> Step | None:
        if not self._redo:
            return None
        step = self._redo.pop()
        self._apply(step, forward=True)
        self._state, self._stats = self._scan()
        self._undo.append(step)
        return step

    # ---- 应用一步 ----

    def _apply(self, step: Step, *, forward: bool) -> None:
        i = 1 if forward else 0
        # 顺序有语义，不能依赖 dict 的偶然次序。:func:`_delta` 是用
        # ``set(before) | set(after)`` 攒出来的，那个顺序由哈希决定，
        # 每一步都可能不同。
        #
        # 重做：先让克隆体存在，再让引用指向它。
        # 撤销：先把引用指回去，最后才砍掉克隆体——``set_appended_count``
        # 会拒绝砍掉一个还被引用着的对象，而那句拒绝是对的（它挡住的正是
        # 「文件里留下一个指向不存在对象的引用」）。
        #
        # 只有一步里同时含「克隆」和「改指向」时才会踩到。AG 页的批量改
        # 正是这种：一次 ``apply_attr`` 就克隆 + 重指向 + 改值，只提交一次。
        # 通用编辑器那边克隆与替换是分开的两步，顺序天然正确，所以这个
        # 缺陷一直没露出来。
        def order(item: tuple[int, dict]) -> int:
            if item[0] == _APPEND_SLOT:
                return 0 if forward else 1
            return 1 if forward else 0

        for oid, keys in sorted(step.delta.items(), key=order):
            if oid == _APPEND_SLOT:
                for key, pair in keys.items():
                    self._write(None, key, pair[i])
                continue
            entry = self.nd.entry_of(oid)
            if entry is None:               # 对象被整个删掉——目前不可能，但别炸
                continue
            for key, pair in keys.items():
                self._write(entry, key, pair[i])

    def _write(self, entry, key: Key, val) -> None:
        kind, name, idx = key
        if kind == _APPEND:
            # MISSING 表示「快照里没有这个键」= 一个克隆体都没有。
            self.nd.set_appended_count(0 if val is MISSING else int(val))
            return
        if kind == _FIELD:
            self._write_field(entry, name, val)
            return
        if kind == _STR:
            sf = entry.strs.get(name)
            if sf is None:
                return
            if val is MISSING:
                sf.reset()
            else:
                # index 与 value 必须一起写：编码只认 index，界面读 value。
                # 只写一个就会「文件变了、界面还显示旧名字」。
                sf.leaf["index"] = val
                sf.leaf["value"] = self.nd.strings_pool.text(val) or ""
            return
        if kind == _STRLIST:
            lf = entry.str_lists.get(name)
            if lf is None:
                return
            if val is MISSING:
                lf.reset()
            else:
                lf.set_pairs(val)
            return
        if kind == _HASH:
            hf = entry.hashes.get(name)
            if hf is None:
                return
            if val is MISSING:
                hf.reset()
            else:
                hf.set_hex(val)
            return
        if kind == _LIST:
            lf = entry.ref_lists.get(name)
            if lf is None:
                return
            if val is MISSING:
                lf.reset()          # 含长度：删掉的项要能加回来
            else:
                lf.set_nodes(val)
            return
        if kind == _REF:
            rf = entry.refs.get(name)
        else:
            lf = entry.ref_lists.get(name)
            rf = (lf.items[idx]
                  if lf is not None and idx < len(lf.items) else None)
        if rf is None:
            return
        if val is MISSING:
            rf.reset()
        else:
            for k, v in val.items():
                rf.leaf[k] = v

    def _write_field(self, entry, name: str, val) -> None:
        f = entry.fields.get(name)
        if val is MISSING:
            # MISSING 的定义就是「不脏也不是补加的」——那它必然等于原值。
            if f is None:
                return
            if f.added:
                self.nd.remove_field(entry, name)
            else:
                f.reset()
            return
        if f is None:
            # 快照里有、对象上却没有，只可能是「补加过又被摘掉」。
            # 走 add_field 而不是手工塞 dict：它会带上正确的 type_id。
            self.nd.add_field(entry, name, val)
        else:
            f.leaf["value"] = val

    # ---- 步骤说明 ----

    def _label(self, delta) -> str:
        n_obj = len(delta)
        n_item = sum(len(v) for v in delta.values())
        if _APPEND_SLOT in delta:
            after = next(iter(delta[_APPEND_SLOT].values()))[1]
            base = ("克隆对象" if after is MISSING
                    else f"克隆对象（第 {after} 个）")
            rest = n_item - len(delta[_APPEND_SLOT])
            if rest:
                base += f"，另有 {rest} 处字段改动"
            return base
        if n_obj == 1:
            oid = next(iter(delta))
            keys = delta[oid]
            if n_item == 1:
                return f"{self._who(oid)} ▸ {self._what(next(iter(keys)))}"
            names = sorted({self._what(k) for k in keys})
            if len(names) == 1:
                return f"{self._who(oid)} ▸ {names[0]}（{n_item} 处）"
            return f"{self._who(oid)} 的 {n_item} 处字段"
        return f"{n_obj} 个对象共 {n_item} 处"

    def _who(self, oid: int) -> str:
        e = self.nd.entry_of(oid)
        if e is None:
            return f"#{oid}"
        return e.title or f"#{oid}"

    @staticmethod
    def _what(key: Key) -> str:
        kind, name, idx = key
        if kind == _APPEND:
            return "克隆对象"
        # 列表类现在都是整条记录（增删会移动索引），不再说「第几项」。
        return humanize(name)
