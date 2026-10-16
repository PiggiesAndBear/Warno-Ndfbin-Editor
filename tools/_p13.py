import io

p = "ndfbin_decks/ndfedit/gui.py"
s = io.open(p, encoding="utf-8", newline="").read()

# 修损坏的字符（此前某次 sed 替换留下的 mojibake）
bad = 'tip += f"\\n只有 {coverage * 100:.1f}% 的同类对象有���字段"'
good = 'tip += f"\\n只有 {coverage * 100:.1f}% 的同类对象有这个字段"'
if bad in s:
    s = s.replace(bad, good, 1)
    print("fixed mojibake")

# ---- show_entry：按字段是否存在切换「添加」按钮 ----
old = '''        # 只给这个对象实际拥有的字段建编辑器，其余禁用。
        # 早期版本让 specialValueText 显示「无此字段」，但它会盖住左侧标签
        # 文字并且在滚动区里造成视觉噪音——改成禁用 + tooltip + 顶部汇总。
        for name, box in self._editors.items():
            fld = entry.fields.get(name)
            if fld is None:
                _mark_missing(box, True)
                continue
            _mark_missing(box, False)'''
new = '''        # 逐字段决定状态：有就启用，没有就置灰并在旁边露出「+ 添加」。
        # 早期版本让 specialValueText 显示「无此字段」，但它会盖住左侧标签
        # 文字并且在滚动区里造成视觉噪音——改成置灰 + tooltip + 顶部汇总
        # +一个明确的添加入口。
        for name, box in self._editors.items():
            fld = entry.fields.get(name)
            add = self._add_buttons.get(name)
            if fld is None:
                _mark_missing(box, True)
                if add is not None:
                    add.setVisible(True)
                    add.setText("+ 添加")
                continue
            _mark_missing(box, False)
            if add is not None:
                if fld.added:
                    # 玩家补加的字段给一个「移除」出口，否则只能整体撤销
                    add.setVisible(True)
                    add.setText("− 移除")
                    add.setToolTip(
                        f"{humanize(name)} 是你补加的字段。\\n"
                        f"点一下把它整个去掉，恢复成「原本就没有」的样子。")
                else:
                    add.setVisible(False)'''
assert old in s, "show_entry"
s = s.replace(old, new, 1)

# 未选中时全部隐藏
old2 = '''            for box in self._editors.values():
                _mark_missing(box, True)
            self._loading = False
            return'''
new2 = '''            for box in self._editors.values():
                _mark_missing(box, True)
            for add in self._add_buttons.values():
                add.setVisible(False)
            self._loading = False
            return'''
assert old2 in s, "none branch"
s = s.replace(old2, new2, 1)

# ---- 新增 _on_add 逻辑 ----
old3 = '''    def _revert_current(self):'''
new3 = '''    def _on_add(self, name: str):
        """给当前对象补上一个它原本没有的字段。"""
        if self._loading or self._entry is None or self._document is None:
            return
        fld = self._entry.fields.get(name)
        if fld is not None and fld.added:
            # 同一个按钮兼做「移除」
            self._document.remove_field(self._entry, name)
            self.show_entry(self._entry, self._info)
            self.dirtyChanged.emit()
            return
        if fld is not None:
            return
        try:
            self._document.add_field(self._entry, name)
        except KeyError as exc:
            QMessageBox.warning(self, "添加不了", str(exc))
            return
        self.show_entry(self._entry, self._info)
        self.dirtyChanged.emit()

    def _revert_current(self):'''
assert old3 in s, "revert anchor"
s = s.replace(old3, new3, 1)

# ---- 绑定 document ----
old4 = '''        self._info: ClassInfo | None = None
        self._document = None          # 需要它来调 add_field'''
assert old4 in s
old5 = '''    def set_info(self, info):
        """记住当前类信息——回退后要用它重新装载编辑器状态。"""
        self._info = info'''
new5 = '''    def set_document(self, nd):
        """注入 NdfDocument——添加 / 移除字段需要它来真正改对象图。"""
        self._document = nd'''
assert old5 in s, "set_info"
s = s.replace(old5, new5, 1)

# 汇总句：区分「原本没有」与「补加的」
old6 = '''        have = len(entry.fields)
        total = len(self._editors)
        if total and have < total:
            self.summary.setText(
                f"该对象有 {have} / {total} 个可改字段，"
                f"其余 {total - have} 个在这一类里不是每条都有（已置灰）。")
        elif total:
            self.summary.setText(f"该对象有 {have} 个可改字段。")
        else:
            self.summary.setText("")'''
new6 = '''        have = len(entry.fields)
        total = len(self._editors)
        added = len(entry.added_fields())
        parts = []
        if have < total:
            parts.append(f"该对象原本有 {have} / {total} 个可改字段")
        else:
            parts.append(f"该对象有 {have} 个可改字段")
        if added:
            parts.append(f"其中 {added} 个是你补加的")
        if total - have - added > 0:
            parts.append(f"还有 {total - have - added} 个在这一类里不是每条都有，"
                         f"已置灰（点右侧「+ 添加」可以补上）")
        self.summary.setText("，".join(parts) + "。")'''
assert old6 in s, "summary"
s = s.replace(old6, new6, 1)

io.open(p, "w", encoding="utf-8", newline="").write(s)
print("patched ok")