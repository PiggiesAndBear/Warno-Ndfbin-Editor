# writer 使用指南

`ndfbin_decks/core/writer.py` 是把解码后的对象图**安全写回** `.ndfbin` 的唯一入口。
它负责备份、原子替换、重建校验、过期检测；你只管改数据。

## 1. 最小可用模板

```python
from pathlib import Path
from ndfbin_decks.core import cndf_codec, writer

path = Path(r"D:\...\Gen\NDF\GFX\Weapon.ndfbin")

raw = path.read_bytes()
doc, decoded = cndf_codec.decode(raw, source=path)

# ... 改 decoded["objects"] 里的值（见第 3 节）...

replacements = writer.sections_of(doc)                      # 拿全部 section 原始字节
replacements["OBJE"] = cndf_codec.encode_objects(decoded["objects"])   # 只替换 OBJE

result = writer.write_file(path, doc, replacements)
print(result)
```

跑一下就能看到效果：

```bash
python tools/writer_demo.py
```

它会自动复制一份 `Weapon_writerDemo.ndfbin` 再改，**不碰原文件**。

## 2. API 一览

| 函数 | 作用 |
|---|---|
| `writer.sections_of(doc)` | 返回 `{section_name: bytes}`，作为 `replacements` 的基底 |
| `writer.changed_sections(doc, repl)` | 告诉你哪些 section 真的变了；空集合 = 空写 |
| `writer.write_bytes(doc, repl, force_recompress=False)` | 只生成字节，不落盘 |
| `writer.write_file(path, doc, repl, backup=True, force_recompress=False, dry_run=False)` | 落盘，返回 `WriteResult` |

`WriteResult` 字段：
`path` / `byte_identical` / `recompressed` / `backup` / `size_before` / `size_after`

## 3. 怎么改字段

`decoded["objects"]` 是**扁平的 24k+ 元素列表**，每个元素：

```python
{
  "id": 37,
  "class_id": 12,
  "class": "TAmmunitionDescriptor",   # 类名，直接可读
  "is_top_object": True,
  "properties": [
      {"property_id": 5, "property_name": "PhysicalDamages", "value": {...}},
  ],
}
```

所以定位方式是**按 `class` + `property_name` 筛，不要硬编码 ID**（ID 随文件变化）：

```python
for obj in decoded["objects"]:
    if obj["class"] != "TAmmunitionDescriptor":
        continue
    for prop in obj["properties"]:
        if prop["property_name"] == "PhysicalDamages":
            prop["value"]["value"] = 99.0     # 直接改这个标量
```

### 关键坑：list 类型字段

`type_id` 为 `0x11`（list）或 `0x12`（list of pair）的字段，标量不在 `value` 里，
而在 `items` 里。必须递归展开：

```python
def iter_values(v):
    if isinstance(v, dict):
        yield v
        for item in v.get("items", []):
            yield from iter_values(item)
        for k in ("key", "value"):
            if k in v:
                yield from iter_values(v[k])
```

同样地，`ObjectReference`（`type_id == 0xBBBBBBBB`）标量是 `object_id`，
list 里则要用 `items[*]["object_id"]`。

## 4. 安全机制（为什么可以放心用）

1. **空写是真空写** — `replacements={}` 或内容与原文逐字节相同，直接返回 `doc.raw_data`，
   不重新压缩、不换 MD5、不产生 diff 噪音。
2. **过期检测** — 如果文件在 decode 之后被别的东西改过，`write_file` 抛
   `StaleDocumentError`，而不是默默用旧快照覆盖回去。**这个没有绕过开关，是故意的。**
   报错就重新 `decode()` 一次。
3. **重建后二次校验** — 写盘前先 `decode()` 一遍新字节，解析失败直接拒绝写、原文件不动。
4. **一次性备份** — 首次保存生成 `<name>.bak`，已存在则**永不覆盖**，所以 `.bak`
   永远是原始 pristine 文件。
5. **原子替换** — 同目录临时文件 + `fsync` + `os.replace`，断电不会留半截文件；
   临时文件异常时清理。

推荐流程：**先 `dry_run=True` 预演，确认 `size_after` 合理再真写。**

```python
preview = writer.write_file(path, doc, replacements, dry_run=True)  # 不碰文件
result  = writer.write_file(path, doc, replacements)                # 真写
```

## 5. 已验证的交付校验

沿用项目惯例，写完做三项核对：

```python
r = writer.write_file(path, doc, replacements)
print(r.size_before, r.size_after)     # 体积变化
print(r.backup)                        # 备份路径，应指向原始文件
doc2, decoded2 = cndf_codec.decode(path.read_bytes())   # 重新读，确认落盘
assert decoded2["object_count"] == decoded["object_count"]  # 对象数不变
```

## 6. 测试

```bash
python -m tests.test_writer      # 20 个用例
```

注意用 `python -m tests.test_writer`，`unittest discover` 找不到这个目录结构。