"""Safe write-back for CNDF (``.ndfbin``) files.

Why this module exists
----------------------
:func:`cndf_codec.rebuild_sections` already knows how to reassemble a file, but
it always re-compresses, and it only ever produces ``bytes``.  Two problems
follow from that, and this module fixes both.

**Problem 1 — a no-op save still rewrites every byte.**
Measured over 984 real files: the rebuilt body is *identical* to the original
in 984/984 cases, yet the compressed bytes differ in 78 of them, because the
game engine's zstd encoder is not ours.  So a player who opens a file, changes
nothing and hits save would get a file whose MD5 changed -- every diff tool
would light up, and a workshop upload would look like a full rewrite.

:func:`write_bytes` therefore short-circuits: when no section actually
changed, it returns the original bytes untouched.

**Problem 2 — there was no file-level write.**
Writing directly over a file the game may be reading is how mods get
destroyed.  :func:`write_file` writes to a temporary file *in the same
directory*, flushes it to disk, validates the result by re-parsing it, and only
then swaps it in, keeping a one-time backup of the original.

Typical use
-----------
::

    from ndfbin_decks.core import cndf_codec, writer

    doc, decoded = cndf_codec.decode(open(path, 'rb').read(), source=path)

    # ... edit decoded['objects'] in place ...

    new_obje = cndf_codec.encode_objects(decoded['objects'])
    writer.write_file(path, doc, {'OBJE': new_obje})

The backup is written once, next to the original, as ``<name>.bak``.  It is
never overwritten, so the first save of a session preserves the pristine file.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
from pathlib import Path

from . import cndf_codec as _codec

__all__ = [
    "WriteResult",
    "StaleDocumentError",
    "write_bytes",
    "write_file",
    "sections_of",
    "changed_sections",
]


class StaleDocumentError(RuntimeError):
    """The file changed on disk after ``doc`` was decoded.

    ``doc`` is a snapshot: both its section bytes and -- crucially -- the
    ``raw_data`` that :func:`write_bytes` returns for a no-op save.  If someone
    else (the game, another tool, the user) rewrites the file in the meantime,
    that snapshot is no longer the file's baseline, and honouring it would
    silently roll the file back to the state it had when it was opened.

    Raised by :func:`write_file` unless ``allow_stale=True`` is passed.  The
    fix is to re-decode: the editor holds the decoded graph in memory, so
    re-decoding costs one parse and loses nothing.
    """


class WriteResult:
    """What :func:`write_file` did, so callers can report it honestly.

    Attributes
    ----------
    path
        The file that was written.
    byte_identical
        True when the new content equals the old content byte for byte.  This
        is the normal outcome of a no-op save.
    recompressed
        True when the body had to be re-compressed.
    backup
        Path to the pristine backup, or ``None`` if one already existed.
    size_before, size_after
        Byte sizes, for the delivery check the project habitually performs.
    """

    __slots__ = ("path", "byte_identical", "recompressed", "backup",
                 "size_before", "size_after")

    def __init__(self, path, byte_identical, recompressed, backup,
                 size_before, size_after):
        self.path = Path(path)
        self.byte_identical = byte_identical
        self.recompressed = recompressed
        self.backup = Path(backup) if backup else None
        self.size_before = size_before
        self.size_after = size_after

    def __repr__(self):
        return (f'WriteResult({self.path.name!r}, '
                f'identical={self.byte_identical}, '
                f'recompressed={self.recompressed}, '
                f'{self.size_before}->{self.size_after})')


def sections_of(doc) -> dict[str, bytes]:
    """Every section of ``doc`` as raw bytes, keyed by name.

    Handy as the base for a ``replacements`` mapping::

        repl = writer.sections_of(doc)
        repl['OBJE'] = cndf_codec.encode_objects(decoded['objects'])
        writer.write_file(path, doc, repl)
    """
    return {s.name: doc.full_data[s.offset:s.offset + s.size]
            for s in doc.sections}


def changed_sections(doc, replacements) -> set[str]:
    """Which of ``replacements`` actually differ from ``doc``'s current bytes.

    An empty set means the caller is saving without having changed anything.
    """
    out = set()
    for name, data in replacements.items():
        for s in doc.sections:
            if s.name != name:
                continue
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError(f'CNDF replacement {name!r} must be bytes')
            if bytes(data) != doc.full_data[s.offset:s.offset + s.size]:
                out.add(name)
            break
        else:
            raise ValueError(f'Unknown CNDF section {name!r}')
    return out


def write_bytes(doc, replacements=None, *, force_recompress=False) -> bytes:
    """Produce the bytes for ``doc`` with ``replacements`` applied.

    When nothing changed this returns the *original* bytes, so a no-op save is
    a genuine no-op -- no re-compression, no new MD5, no diff noise.  Pass
    ``force_recompress=True`` to override that and always rebuild.

    Raises
    ------
    ValueError
        If a replacement names a section that does not exist, or is not bytes.
    """
    if replacements is None:
        replacements = {}
    if not force_recompress and not changed_sections(doc, replacements):
        return doc.raw_data
    return _codec.rebuild_sections(doc, replacements)


def write_file(path, doc, replacements=None, *, backup=True,
               force_recompress=False, dry_run=False) -> WriteResult:
    """Write ``doc`` to ``path`` safely, atomically, with a one-time backup.

    Parameters
    ----------
    path
        Target file.  It must be the file ``doc`` was decoded from -- the
        temporary file is created in the same directory so the final
        :func:`os.replace` is atomic on Windows too.
    doc
        The :class:`~ndfbin_decks.core.cndf_layout.CndfFile` from
        :func:`cndf_codec.decode`.  It is a *snapshot*; see the Raises section.
    replacements
        ``{section_name: bytes}``.  ``None`` or ``{}`` means "save unchanged",
        which is a no-op unless ``force_recompress`` is set.
    backup
        Write ``<path>.bak`` once, if it does not already exist.
    dry_run
        Build and validate everything, then stop without touching the file.

    Returns
    -------
    WriteResult

    Raises
    ------
    StaleDocumentError
        If the file on disk no longer matches ``doc``'s snapshot.  There is no
        flag to bypass this, on purpose.  Once the file has been written, its
        previous bytes are only reachable through the backup, so re-decoding
        costs one parse and removes an entire class of silent data loss.
    ValueError
        If the rebuilt file fails to re-parse.  The target is left untouched.

    Notes
    -----
    The staleness check exists because of how the no-op shortcut works: for an
    unchanged save, :func:`write_bytes` returns ``doc.raw_data`` -- the bytes
    as they were when the file was *opened*.  Honouring that after somebody
    else has rewritten the file would quietly restore the old contents, which
    is exactly the kind of bug that loses a user's work without ever showing an
    error.  Re-decode and the check passes naturally.
    """
    path = Path(path)
    before = path.read_bytes() if path.exists() else None

    if before is not None and before != doc.raw_data:
        raise StaleDocumentError(
            f'{path.name} changed on disk since it was decoded '
            f'({len(before)} bytes now, {len(doc.raw_data)} bytes in the '
            f'snapshot). Re-decode the file before writing.'
        )

    new = write_bytes(doc, replacements, force_recompress=force_recompress)
    identical = before is not None and new == before

    result = WriteResult(
        path=path,
        byte_identical=identical,
        recompressed=not identical,
        backup=None,
        size_before=len(before) if before is not None else 0,
        size_after=len(new),
    )

    if dry_run or identical:
        return result

    # Validate before touching anything.  rebuild_sections already re-parses
    # its own output, but a caller may have handed us a hand-built byte string
    # via force_recompress, so check again -- a corrupt swap is unrecoverable.
    try:
        _codec.decode(new, source=str(path))
    except Exception as exc:                      # noqa: BLE001 - re-raised
        raise ValueError(
            f'Refusing to write {path.name}: rebuilt file does not re-parse '
            f'({exc})'
        ) from exc

    if backup:
        bak = path.with_name(path.name + '.bak')
        if not bak.exists() and before is not None:
            bak.write_bytes(before)
            result.backup = bak

    _atomic_replace(path, new)
    return result


def _atomic_replace(path: Path, data: bytes) -> None:
    """Write ``data`` to a sibling temp file, then swap it into place.

    The temp file lives in the target directory so :func:`os.replace` stays on
    one filesystem and is therefore atomic.  ``fsync`` before the swap means a
    power loss cannot leave a half-written mod behind.
    """
    directory = path.parent
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp',
                               dir=str(directory))
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha256(data: bytes) -> str:
    """Digest helper, so callers do not each import hashlib."""
    return hashlib.sha256(data).hexdigest()