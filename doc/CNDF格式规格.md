# CNDF（ndfbin）格式规格速查

> **权威实现**：`ndfbin_decks/core/cndf_codec.py` + `cndf_layout.py`
> **实测验证**：60/60 真实文件解码成功；OBJE 段往返字节一致
> **样本**：WARNO 原版 `Gen/` + workshop mod `GFX/`，共 61 个文件

---

## 0. 压缩算法映射

| compression_mode | 算法 | 实现 |
|---|---|---|
| `1` | LZ4 block | `lz4.block.decompress(raw[44:])` |
| **`2`** | **zstd** | ⚠️ **实测主力模式** |
| `3` | zlib | `zlib.decompressobj()` |
| `128` | zlib | 三部曲遗留标记 |

> **⚠️ 常见错误**：`28 B5 2F FD` 是 **zstd frame magic**（小端 `0xFD2FB528`），
> **不是** LZ4 legacy frame magic。用 lz4 库解必然失败。
>
> 压缩数据起点是 **offset 44**，不是 headerSize(40)。
> `raw[40:44]` 是 u32 解压长度。

## 1. Header（44 字节有效）

```
0x00  char[4]  'EUG0'
0x04  u32      version (= 1)
0x08  char[4]  'CNDF'
0x0C  u32      compression_mode
0x10  u64      footer_offset      TOC 位置（在解压流内）
0x18  u64      header_size        (= 40)
0x20  u64      full_size          (= 40 + u32@40)
0x28  u32      body_uncompressed_size
0x2C  ...      compressed stream
```

**决定性校验**：`u32@40 + 40 == full_size`

## 2. 解压

```python
import struct, zstandard

mode = struct.unpack_from('<I', raw, 0x0C)[0]
size = struct.unpack_from('<I', raw, 40)[0]
full = struct.unpack_from('<Q', raw, 0x20)[0]
assert size + 40 == full, "size mismatch"

if mode == 2:
    assert zstandard.frame_content_size(raw[44:]) == size
    body = zstandard.ZstdDecompressor(max_window_size=256*1024).decompress(
        raw[44:], max_output_size=size, allow_extra_data=False)
elif mode == 1:
    body = lz4.block.decompress(raw[44:], uncompressed_size=size)
else:
    st = zlib.decompressobj()
    body = st.decompress(raw[44:], size + 1)
    assert len(body) == size and st.eof and not st.unused_data and not st.unconsumed_tail

logical = raw[:40] + body      # 逻辑流 = header + body
```

写出用 **zstd level 6**（level 22 在 8 MB 文件上多花 6 s 只省 1 KB）。

## 3. Section / TOC0

9 个 section，写入与逻辑顺序一致：

```
OBJE → TOPO → CHNK → CLAS → PROP → STRG → TRAN → IMPR → EXPR
```

TOC 条目 **24 字节**，字段顺序 name 在前：

```
u8  name[8]      ASCII, 零填充（如 'OBJE\0\0\0\0'）
u64 offset       相对解压流起点的绝对偏移
u32 size
```

Footer 结构：`'TOC0' | u32 count | count × 24B 条目`
长度校验：`footer_offset + 8 + 24 × len(sections) == len(logical)`

## 4. Section 内容编码

| Section | 编码 |
|---|---|
| CLAS / PROP | `i32 nameLen` + 编码字节 × N |
| STRG / TRAN | `i32 charLen` + 编码字节 × N |
| OBJE | `i32 classId` + 逐属性（`i32 propId`, `i32 typeHash`, bytes） |
| TOPO / CHNK / IMPR / EXPR | `u32` 数组 |

**编码**：手册要求 UTF-8 或纯 ASCII。**不要硬编码 ISO-8859-1**
（三部曲工具的 7 处硬编码会导致中文损坏）。

## 5. 值类型（typeHash）

| 宽度 | 类型（hash） |
|---|---|
| 1 | Boolean(0), Int8(1) |
| 2 | Int16(24), UInt16(25) |
| 4 | Int32(2), UInt32(3), Float32(5), TableString(7), WideString(8), Color32(13), List(17), MapList(18), TableStringFile(28) |
| 8 | Time64(4), Float64(6), LocalisationHash(29), EugInt2(31), EugFloat2(33) |
| 12 | Vector(11), TrippleInt(14) |
| 16 | Color128(12), Guid(26), Hash(37) |
| 变长 | Reference(9), Blob(20), Map(34), ZipBlob(30) |

哨兵：
- `0xAAAAAAAA` = TransTableReference
- `0xBBBBBBBB` = ObjectReference
- `0xEEEEEEEE` = Unset（写入时跳过）
- `0xFFFFFFFF` = Unknown → **应透传原始字节，不要抛异常**

## 6. 解码结果的引用约定（`cndf_codec` 实际输出）

```python
# 标量 obj_ref
{'type_id': 3149642683, 'type': 'obj_ref',
 'reference_prefix': True, 'object_id': 1506, 'class_id': 16}

# list（元素在 'items' 下，各自带 object_id + class_id）
{'type_id': 17, 'type': 'list', 'reference_prefix': False,
 'length': 1, 'items': [{'type_id': 3149642683, 'type': 'obj_ref', 'object_id': 4122, 'class_id': 52}]}

# strg_ref（可直接显示）
{'type_id': 7, 'type': 'strg_ref', 'index': 1307, 'value': 'WeaponAlternative_1'}

# loc_hash（★ 需查 TRAN 表才能显示文本）
{'type_id': 29, 'type': 'loc_hash', 'value': ...}
```

> ⚠️ **UI 封装注意**：标量 obj_ref 的 id 在 `object_id`；
> list 元素的 id 也在 `object_id`，但要先取 `items` 列表。
> 键名相同、层级不同，容易写错。

## 7. Class / Property Id 语义

**Class Id 与 Property Id 不落盘**，由 CLAS/PROP 表的列表索引隐含决定。

⚠️ 后果：插入/删除/重排类或属性会让 OBJE 中所有引用失效。
现有实现靠「不重排」规避。写回时应确保列表顺序稳定。

---

**实测样本**：

| 文件 | mode | 磁盘 | 解压后 | 对象数 | 耗时 |
|---|---|---|---|---|---|
| GFX/Weapon.ndfbin | 2 | 265,675 | 4,016,444 | 24,168 | 0.87 s |
| GFX/Unit.ndfbin | 2 | 650,564 | — | — | — |
| ResourceFile/Catalog.cat | 2 | 528,184 | — | 23,598 | 0.44 s |