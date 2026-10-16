"""Standalone CNDF codec — no framework, no campaign, no build pipeline.

This is a trimmed copy of ``warno_ag/cndf.py``.  The decode/encode/rebuild core
is byte-for-byte the same logic; only the pack-extraction helper (``inspect_pack``)
and its archive/storage imports were removed, because a deck editor never unpacks
a ``.pack``.  ``build_reference_table_sections`` is the import-trie writer that
used to live in ``bruderkrieg`` — that function is self-contained once the TRAN
table is in hand, so it travels here instead of dragging in the campaign builder.

Only third-party requirement: ``zstandard`` (plus ``lz4`` for LZ4-packed files).
"""
from pathlib import Path
import hashlib
import struct
import zlib
import zstandard
import lz4.block
import sys as _sys
from pathlib import Path as _Path

if __package__:
    from . import cndf_layout as ndf
else:                                   # flat folder / frozen exe
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from . import cndf_layout as ndf


# zstd level: 22 (the library default) spends ~6s on an 8MB deck for a mere
# 1KB gain over level 6.  Level 6 costs ~70ms and produces a smaller file than
# the low levels, so it is the right trade for an interactive editor.
ZSTD_LEVEL = 6


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def path_tree(full, section, segments):
    if section is None or not section.size:
        return {}
    data = full[section.offset:section.offset + section.size]
    result, occupied = {}, set()
    def visit(offset, prefix, depth=0):
        if depth > 100 or offset < 0 or offset + 12 > len(data):
            raise ValueError("Invalid CNDF path tree")
        segment, index, count = struct.unpack_from("<III", data, offset)
        end = offset + 12 + count*4
        if segment >= len(segments) or end > len(data):
            raise ValueError("Invalid CNDF path tree node")
        positions = set(range(offset,end))
        if positions & occupied:
            raise ValueError("Overlapping/cyclic CNDF path tree")
        occupied.update(positions)
        path = prefix + [segments[segment]]
        if index != 0xFFFFFFFF:
            if index in result:
                raise ValueError("Duplicate CNDF path target")
            result[index] = "/".join(path)
        for i in range(count):
            child = offset + 12 + struct.unpack_from("<I",data,offset+12+4*i)[0]
            visit(child,path,depth+1)
    visit(0,[])
    if len(occupied) != len(data):
        raise ValueError("Unvisited CNDF path tree bytes")
    return result


def _open_containers(raw, source="<memory>"):
    """Header and tables of a CNDF, stopping short of the object graph.

    Split out of :func:`decode` so a caller that only needs to know *what a
    file declares* can skip the expensive half.  A mod's ``Unit.ndfbin`` holds
    56223 objects and takes ~2 s to parse; its export table is a few hundred
    nodes and costs milliseconds.  Everything here is validation that has to
    happen before either half can trust the file.
    """
    if len(raw) < 44:
        raise ValueError("Truncated CNDF header")
    h = ndf.parse_cndf_header(raw)
    if h.version != 1 or h.header_size != 40 or h.full_size > 256 * 1024 * 1024:
        raise ValueError("Unsupported CNDF header")
    if h.compression_mode in (1, 2, 3, 128):
        size = struct.unpack_from("<I", raw, 40)[0]
        if size + 40 != h.full_size or size > 256*1024*1024:
            raise ValueError("CNDF compressed size mismatch")
        if h.compression_mode == 2:
            if zstandard.frame_content_size(raw[44:]) != size:
                raise ValueError("CNDF/zstd size mismatch")
            body = zstandard.ZstdDecompressor(max_window_size=256 * 1024).decompress(
                raw[44:], max_output_size=size, allow_extra_data=False)
        elif h.compression_mode == 1:
            try:
                body = lz4.block.decompress(raw[44:], uncompressed_size=size)
            except lz4.block.LZ4BlockError as exc:
                raise ValueError("Invalid CNDF/LZ4 block") from exc
            if len(body) != size:
                raise ValueError("CNDF/LZ4 size mismatch")
        else:
            stream = zlib.decompressobj()
            try:
                body = stream.decompress(raw[44:], size + 1)
            except zlib.error as exc:
                raise ValueError("Invalid CNDF/zlib stream") from exc
            if (len(body) != size or not stream.eof or stream.unused_data
                    or stream.unconsumed_tail):
                raise ValueError("CNDF/zlib size or trailing data mismatch")
        full = raw[:40] + body
        compressed = True
    else:
        raise ValueError("Unsupported CNDF compression mode; legacy guessing is disabled")
    if len(full) != h.full_size:
        raise ValueError("CNDF full size mismatch")
    sections = ndf.parse_cndf_sections(full, h.footer_offset)
    if h.footer_offset + 8 + 24*len(sections) != len(full):
        raise ValueError("Trailing CNDF footer bytes")
    if len({s.name for s in sections}) != len(sections):
        raise ValueError("Duplicate CNDF section")
    ordered = sorted(sections, key=lambda s: s.offset)
    for i, section in enumerate(ordered):
        if section.offset < h.header_size or section.offset + section.size > h.footer_offset:
            raise ValueError("CNDF section outside body")
        if i and ordered[i-1].offset + ordered[i-1].size > section.offset:
            raise ValueError("Overlapping CNDF sections")
    doc = ndf.CndfFile(Path(source), raw, full, h, size, compressed, sections)
    sec = ndf.section_map(sections)
    if not {"OBJE", "CHNK", "CLAS", "PROP"} <= set(sec):
        raise ValueError("Missing CNDF graph section")
    classes = ndf.parse_classes(full, sec)
    props = ndf.parse_properties(full, sec, classes)
    strings = ndf.read_len_prefixed_strings(full, sec["STRG"]) if "STRG" in sec else []
    trans = ndf.read_len_prefixed_strings(full, sec["TRAN"]) if "TRAN" in sec else []
    if any(p['class_id'] >= len(classes) for p in props):
        raise ValueError("Invalid CNDF property owner class")
    if sec['CHNK'].size != 8:
        raise ValueError("Unsupported CNDF CHNK layout")
    count = ndf.parse_chunk_count(full, sec['CHNK'])
    top = ndf.parse_u32_list(full, sec.get('TOPO'))
    if len(top) != len(set(top)) or any(i >= count for i in top):
        raise ValueError("Invalid CNDF top object index")
    top = set(top)
    return doc, sec, classes, props, strings, trans, count, top


def read_exports(raw, source="<memory>"):
    """``{object_id: '$/…'}`` for a file's exported paths, without decoding it.

    Only ``EXPR`` and ``TRAN`` are walked.  This is what lets the equipment
    dialog ask a 650 KB ``Unit.ndfbin`` for its unit list while the dialog is
    opening instead of stalling for two seconds on a graph nobody reads.
    """
    doc, sec, _classes, _props, _strings, trans, count, _top = \
        _open_containers(raw, source)
    exports = path_tree(doc.full_data, sec.get("EXPR"), trans)
    if any(i >= count for i in exports):
        raise ValueError("Invalid CNDF export object index")
    return exports


def decode(raw, source="<memory>"):
    doc, sec, classes, props, strings, trans, count, top = _open_containers(
        raw, source)
    full = doc.full_data
    obje = sec['OBJE']
    cur = ndf.ValueCursor(full[obje.offset:obje.offset+obje.size], 0)
    parsed = []
    try:
        for object_id in range(count):
            start = cur.pos + obje.offset
            class_id = cur.read_u32()
            if class_id >= len(classes):
                raise ValueError("Invalid CNDF object class")
            values, seen = [], set()
            while True:
                prop_id = cur.read_u32()
                if prop_id == ndf.OBJECT_END_MARKER:
                    break
                if prop_id >= len(props) or prop_id in seen:
                    raise ValueError("Invalid/duplicate CNDF object property")
                seen.add(prop_id)
                value = ndf.parse_cndf_value(cur, strings, trans, 100)
                values.append({'property_id':prop_id, 'property_name':props[prop_id]['name'], 'value':value})
            parsed.append({'id':object_id, 'offset':start, 'class_id':class_id,
                           'class':classes[class_id], 'is_top_object':object_id in top, 'properties':values})
    except ndf.ParseError as e:
        raise ValueError(f"Invalid CNDF object section: {e}") from e
    if cur.pos != len(cur.data):
        raise ValueError("Unconsumed CNDF object section bytes")
    objects = {'objects':parsed, 'objects_parsed':len(parsed)}
    imports = path_tree(full, sec.get("IMPR"), trans)
    exports = path_tree(full, sec.get("EXPR"), trans)
    if any(i >= count for i in exports):
        raise ValueError("Invalid CNDF export object index")
    # Walk the tree iteratively with an explicit stack.  A recursive closure
    # costs ~4s on a full campaign deck because it is re-entered several million
    # times; the iterative form touches each node exactly once.
    #
    # Leaf nodes are dicts whose own values are scalars, so only containers need
    # to be pushed.  ``type_id`` is read once per dict instead of three times.
    object_class_ids = [o["class_id"] for o in objects["objects"]]
    total_objects = len(object_class_ids)
    stack = [objects["objects"]]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        type_id = node.get("type_id")
        if type_id is not None:
            if type_id == 7 or type_id == 0x1c:
                if node["index"] >= len(strings):
                    raise ValueError("Invalid CNDF string table reference")
            elif type_id == ndf.TRANS_REFERENCE:
                index = node["index"]
                path = imports.get(index)
                if path is None:
                    raise ValueError("Unresolved CNDF import index")
                node["value"] = path
            elif type_id == ndf.OBJECT_REFERENCE:
                index = node["object_id"]
                if index != 0xffffffff or node["class_id"] != 0xffffffff:
                    if (index >= total_objects
                            or object_class_ids[index] != node["class_id"]):
                        raise ValueError("Unresolved CNDF object/class reference")
        stack.extend(node.values())
    return doc, {"source": str(source), "sha256": sha256(raw), "classes": classes,
                 "properties": props, "strings": strings, "translations": trans,
                 "imports": imports, "exports": exports,
                 "objects": objects["objects"], "object_count": objects["objects_parsed"]}


def encode_value(v):
    t = v["type_id"]
    u32 = lambda x: struct.pack("<I",x)
    prefix = (u32(9) if v["reference_prefix"] else b"") + u32(t)
    scalar_formats = {0:"?",1:"b",2:"i",3:"I",4:"Q",5:"f",6:"d",0x13:"q",0x18:"h",0x19:"H"}
    if t in scalar_formats:
        return prefix + struct.pack("<"+scalar_formats[t],v["value"])
    if t in (7,0x1c,ndf.TRANS_REFERENCE):
        return prefix + u32(v["index"])
    if t == ndf.OBJECT_REFERENCE:
        return prefix + u32(v["object_id"]) + u32(v["class_id"])
    if t in (0x0c,0x1a,0x1d,0x25):
        return prefix + bytes.fromhex(v["value_hex"])
    if t in (0x1f,0x21,0x0b,0x0e):
        return prefix + struct.pack({0x1f:"<2i",0x21:"<2f",0x0b:"<3f",0x0e:"<3i"}[t],*v["value"])
    if t == 0x0d:
        return prefix + bytes(v["value"][k] for k in "rgba")
    if t == 0x08:
        data = v["value"].encode("utf-16le")
        return prefix + u32(len(data)) + data
    if t in (0x11,0x12):
        if len(v["items"]) != v["length"]:
            raise ValueError("NDF collection length mismatch")
        return prefix + u32(v["length"]) + b"".join(encode_value(x) if t==0x11 else encode_value(x["key"])+encode_value(x["value"]) for x in v["items"])
    if t == 0x22:
        return prefix + encode_value(v["key"]) + encode_value(v["value"])
    raise ValueError(f"No lossless writer for CNDF value type {t:#x}")


def encode_strings(values):
    output=bytearray()
    for value in values:
        if not isinstance(value,str): raise ValueError('CNDF string must be text')
        try: raw=value.encode('latin1')
        except UnicodeEncodeError as exc: raise ValueError('CNDF STRG text must be latin-1') from exc
        if len(raw)>16*1024*1024: raise ValueError('CNDF string is unreasonably large')
        output.extend(struct.pack('<I',len(raw)))
        output.extend(raw)
    return bytes(output)


def encode_objects(objects):
    output = bytearray()
    for expected,obj in enumerate(objects):
        if obj["id"] != expected:
            raise ValueError("NDF object numbering changed")
        output.extend(struct.pack("<I",obj["class_id"]))
        for prop in obj["properties"]:
            output.extend(struct.pack("<I",prop["property_id"]))
            output.extend(encode_value(prop["value"]))
        output.extend(struct.pack("<I",ndf.OBJECT_END_MARKER))
    return bytes(output)


def rebuild_objects(doc, decoded):
    """Replace a FULL reserialized OBJE section. Other sections remain opaque."""
    objects = encode_objects(decoded["objects"])
    original = next(s for s in doc.sections if s.name == "OBJE")
    if objects == doc.full_data[original.offset:original.offset+original.size]:
        return doc.raw_data
    return rebuild_sections(doc,{'OBJE':objects})


def empty_objects(doc):
    """Remove every object while retaining the verified CNDF schema tables."""
    if any(section.name not in {"OBJE", "TOPO", "CHNK", "CLAS", "PROP", "STRG", "TRAN", "IMPR", "EXPR"}
           for section in doc.sections):
        raise ValueError('Unsupported CNDF section in empty-object rewrite')
    chnk = struct.pack('<II', 0, 0)
    return rebuild_sections(doc, {'OBJE': b'', 'TOPO': b'', 'CHNK': chnk})


def append_graph_objects(doc, decoded, new_objects, new_classes=(), new_properties=()):
    """Append typed graph objects and extend CLAS/PROP/CHNK coherently.

    This is intentionally append-only: existing object/class/property indices
    and opaque sections never move semantically.  ``new_properties`` contains
    ``(name, class_name)`` pairs for classes in either the old or appended
    class table.  The full decoder validates the resulting graph.
    """
    if not isinstance(new_objects, list) or not new_objects:
        raise ValueError('At least one appended graph object is required')
    existing_classes = list(decoded['classes'])
    if len(set(new_classes)) != len(new_classes) or any(not isinstance(x, str) or not x for x in new_classes):
        raise ValueError('Invalid appended CNDF classes')
    if set(new_classes) & set(existing_classes):
        raise ValueError('Appended CNDF class already exists')
    classes = existing_classes + list(new_classes)
    class_ids = {name:index for index,name in enumerate(classes)}
    existing_props = {(x['name'], x['class']) for x in decoded['properties']}
    if len(set(new_properties)) != len(new_properties):
        raise ValueError('Duplicate appended CNDF property')
    for name, owner in new_properties:
        if not isinstance(name, str) or not name or owner not in class_ids or (name, owner) in existing_props:
            raise ValueError('Invalid appended CNDF property')
    objects = decoded['objects'] + list(new_objects)
    for index, obj in enumerate(objects):
        if obj.get('id') != index or obj.get('class') not in class_ids:
            raise ValueError('Invalid appended CNDF object identity')
        obj['class_id'] = class_ids[obj['class']]
    clas_section=next(s for s in doc.sections if s.name=='CLAS')
    clas=bytearray(doc.full_data[clas_section.offset:clas_section.offset+clas_section.size])
    for value in new_classes:
        raw=value.encode('latin1'); clas.extend(struct.pack('<I',len(raw))+raw)
    prop_section=next(s for s in doc.sections if s.name=='PROP')
    prop=bytearray(doc.full_data[prop_section.offset:prop_section.offset+prop_section.size])
    for name,owner in new_properties:
        raw=name.encode('latin1'); prop.extend(struct.pack('<I',len(raw))+raw+struct.pack('<I',class_ids[owner]))
    chnk_section=next(s for s in doc.sections if s.name=='CHNK')
    chnk=bytearray(doc.full_data[chnk_section.offset:chnk_section.offset+chnk_section.size])
    struct.pack_into('<I',chnk,4,len(objects))
    return rebuild_sections(doc, {'CLAS':bytes(clas), 'PROP':bytes(prop), 'CHNK':bytes(chnk),
                                  'OBJE':encode_objects(objects)})


def rebuild_sections(doc, replacements):
    if set(replacements)-{s.name for s in doc.sections}:
        raise ValueError('Unknown CNDF replacement section')
    full = bytearray(doc.full_data[:doc.header.header_size])
    toc=[]
    for section in doc.sections:
        data=replacements.get(section.name,doc.full_data[section.offset:section.offset+section.size])
        if not isinstance(data,bytes): raise ValueError('CNDF replacement section must be bytes')
        toc.append((section.name,len(full),len(data)))
        full.extend(data)
    footer=len(full)
    full.extend(b'TOC0'+struct.pack('<I',len(toc)))
    for name,offset,size in toc:
        full.extend(name.encode('ascii')[:8].ljust(8,b'\0')+struct.pack('<QQ',offset,size))
    struct.pack_into('<Q',full,16,footer)
    struct.pack_into('<Q',full,32,len(full))
    body=bytes(full[doc.header.header_size:])
    if doc.header.compression_mode == 2:
        packed=zstandard.ZstdCompressor(level=ZSTD_LEVEL).compress(body)
    elif doc.header.compression_mode == 1:
        packed=lz4.block.compress(body,store_size=False)
    elif doc.header.compression_mode == 3:
        packed=zlib.compress(body)
    else:
        raise ValueError('Writer supports explicit CNDF zstd(2), LZ4(1) and zlib(3) only')
    result=bytes(full[:doc.header.header_size])+struct.pack('<I',len(body))+packed
    # Independent full parser validates section ranges and the complete object graph.
    decode(result)
    return result


def build_reference_table_sections(document, imports, section_name='IMPR'):
    """Rebuild a CNDF import trie, appending any new path segments to TRAN.

    Each distinct path gets exactly one trie leaf, so passing an import table
    that already contains everything means untouched paths keep their index.
    """
    translations = []
    for section in document.sections:
        if section.name == "TRAN":
            translations = ndf.read_len_prefixed_strings(document.full_data, section)
            break
    if not translations:
        raise ValueError("GDScript has no TRAN table")
    segment_index = {value: index for index, value in enumerate(translations)}

    def segment(value):
        if value not in segment_index:
            segment_index[value] = len(translations)
            translations.append(value)
        return segment_index[value]

    class Node:
        def __init__(self, name):
            self.name = name
            self.index = 0xFFFFFFFF
            self.children = {}

    roots = {}
    for index, path in imports.items():
        children = roots
        node = None
        for part in path.split("/"):
            node = children.setdefault(part, Node(segment(part)))
            children = node.children
        if node.index != 0xFFFFFFFF:
            raise ValueError("Duplicate rebuilt import path")
        node.index = index
    ordered = []

    def visit(node):
        ordered.append(node)
        for child in node.children.values():
            visit(child)

    for root in roots.values():
        visit(root)
    offsets = {}
    offset = 0
    for node in ordered:
        offsets[id(node)] = offset
        offset += 12 + 4 * len(node.children)
    encoded = bytearray(offset)
    for node in ordered:
        children = list(node.children.values())
        position = offsets[id(node)]
        struct.pack_into("<III", encoded, position, node.name, node.index, len(children))
        for number, child in enumerate(children):
            struct.pack_into("<I", encoded, position + 12 + 4 * number,
                             offsets[id(child)] - (position + 12))
    return {"TRAN": encode_strings(translations), section_name: bytes(encoded)}
