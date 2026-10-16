# -*- coding: utf-8 -*-
"""Tests for core.writer -- the safe write-back layer.

What is actually promised, in order of importance:

  1. A no-op save does not touch the file at all -- not "writes identical
     bytes", it does not write.
  2. A real edit round-trips: decode -> replace -> write -> decode returns the
     edited value.
  3. A rebuild that would produce a broken file is refused, and the original
     survives untouched.
  4. The pristine backup is written once and never overwritten.

These run against real WARNO files.  That is deliberate: the whole value of
this layer is what it does to files the game actually ships, and a synthetic
fixture would let a format regression pass unnoticed.  On a machine without
WARNO the suite skips, per tests/_env.py.
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ndfbin_decks.core import cndf_codec, cndf_layout, writer   # noqa: E402

try:
    from tests import _env
except Exception:                                               # pragma: no cover
    _env = None


def _sample_file(limit_bytes=400_000):
    """The smallest real CNDF file available, or None.

    Small keeps the suite quick; real keeps it meaningful.  ``BaseWeapon``
    (3 objects) and the map metadata files are typical winners.
    """
    if _env is None or not hasattr(_env, 'cndf_files'):
        return None
    try:
        candidates = _env.cndf_files(limit=60)
    except Exception:
        return None
    usable = []
    for p in candidates:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if 40 < size <= limit_bytes:
            usable.append((size, p))
    if not usable:
        return None
    return min(usable)[1]


@unittest.skipUnless(_env is not None, 'test environment unavailable')
class _RealFileBase(unittest.TestCase):
    """Shared fixture: a real CNDF copied into a temp dir we may write to."""

    @classmethod
    def setUpClass(cls):
        src = _sample_file()
        if src is None:
            raise unittest.SkipTest('no suitable CNDF file on this machine')
        cls.source = src

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / self.source.name
        self.raw = self.source.read_bytes()
        self.path.write_bytes(self.raw)
        self.doc, self.decoded = cndf_codec.decode(self.raw,
                                                   source=str(self.path))

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers -----------------------------------------------------------

    def _editable_section(self):
        """Pick a section we can safely mutate and return its new bytes.

        STRG is the safe choice: it is a run of ``u32 length + bytes`` records,
        so appending one string changes the file without shifting any index an
        existing object already refers to.  Mutating TOPO instead would add a
        top-object index that points at nothing, and the validator would
        rightly reject the result.
        """
        sec = cndf_layout.section_map(self.doc.sections)
        if 'STRG' not in sec:
            raise unittest.SkipTest('sample file has no STRG section')
        old = self.doc.full_data[sec['STRG'].offset:
                                 sec['STRG'].offset + sec['STRG'].size]
        marker = b'writer-roundtrip'
        return 'STRG', old + struct.pack('<I', len(marker)) + marker


class NoOpSaveTests(_RealFileBase):
    """The headline promise: an unchanged save is a real no-op."""

    def test_write_bytes_returns_original_identity(self):
        out = writer.write_bytes(self.doc, {})
        self.assertEqual(out, self.raw)
        self.assertLessEqual(len(out), len(self.raw))

    def test_write_bytes_no_replacements_is_original(self):
        self.assertEqual(writer.write_bytes(self.doc), self.raw)

    def test_verbatim_sections_are_not_a_change(self):
        repl = writer.sections_of(self.doc)
        self.assertEqual(writer.changed_sections(self.doc, repl), set())
        self.assertEqual(writer.write_bytes(self.doc, repl), self.raw)

    def test_force_recompress_rebuilds_but_stays_decodable(self):
        out = writer.write_bytes(self.doc, {}, force_recompress=True)
        cndf_codec.decode(out)                      # must still parse
        self.assertEqual(len(writer.sections_of(self.doc)),
                         len(cndf_codec.decode(out)[0].sections))

    def test_no_op_save_does_not_rewrite_the_file(self):
        before_stat = self.path.stat()
        before_hash = writer.sha256(self.path.read_bytes())

        res = writer.write_file(self.path, self.doc, {})

        self.assertTrue(res.byte_identical)
        self.assertEqual(writer.sha256(self.path.read_bytes()), before_hash)
        self.assertIsNone(res.backup, 'a no-op save must not create a backup')
        self.assertFalse(
            self.path.with_name(self.path.name + '.bak').exists())
        # mtime unchanged is the strongest statement that nothing was written
        self.assertEqual(self.path.stat().st_mtime_ns,
                         before_stat.st_mtime_ns)


class EditAndBackupTests(_RealFileBase):
    """A real edit writes, is reversible, and is backed up once."""

    def test_edit_round_trips(self):
        name, new = self._editable_section()
        res = writer.write_file(self.path, self.doc, {name: new})

        self.assertFalse(res.byte_identical)
        self.assertGreater(res.size_after, 0)

        doc2, _ = cndf_codec.decode(self.path.read_bytes(),
                                    source=str(self.path))
        sec2 = cndf_layout.section_map(doc2.sections)[name]
        got = doc2.full_data[sec2.offset:sec2.offset + sec2.size]
        self.assertEqual(got, new)

    def test_backup_holds_the_pristine_bytes(self):
        name, new = self._editable_section()
        res = writer.write_file(self.path, self.doc, {name: new})

        self.assertIsNotNone(res.backup)
        self.assertTrue(res.backup.exists())
        self.assertEqual(res.backup.read_bytes(), self.raw)

    def test_backup_is_written_once_and_never_clobbered(self):
        """A second save must not disturb the pristine backup."""
        name, first = self._editable_section()
        writer.write_file(self.path, self.doc, {name: first})
        bak = self.path.with_name(self.path.name + '.bak')
        pristine = bak.read_bytes()

        # Re-decode (as the editor would), then make a second, different edit.
        raw2 = self.path.read_bytes()
        doc2, _ = cndf_codec.decode(raw2, source=str(self.path))
        sec2 = cndf_layout.section_map(doc2.sections)[name]
        current = doc2.full_data[sec2.offset:sec2.offset + sec2.size]
        writer.write_file(self.path, doc2,
                          {name: current + struct.pack('<I', 1) + b'x'})

        self.assertEqual(bak.read_bytes(), pristine,
                         'the backup must still hold the ORIGINAL bytes')
        self.assertEqual(bak.read_bytes(), self.raw)

    def test_dry_run_touches_nothing(self):
        name, new = self._editable_section()
        before_hash = writer.sha256(self.path.read_bytes())

        res = writer.write_file(self.path, self.doc, {name: new},
                                dry_run=True)

        self.assertFalse(res.byte_identical)        # it WOULD have changed
        self.assertEqual(writer.sha256(self.path.read_bytes()), before_hash)
        self.assertFalse(self.path.with_name(self.path.name + '.bak').exists())

    def test_no_temp_files_left_behind(self):
        name, new = self._editable_section()
        writer.write_file(self.path, self.doc, {name: new})
        leftovers = [p.name for p in Path(self.tmp.name).iterdir()
                     if p.suffix == '.tmp']
        self.assertEqual(leftovers, [])


class StaleSnapshotTests(_RealFileBase):
    """A snapshot must never silently roll the file back.

    This is the bug class that motivated the guard: after a successful save,
    ``doc.raw_data`` still describes the pre-save file.  A second no-op save
    using that same doc would therefore "restore" the old bytes -- undoing the
    edit the user just made, with no error.
    """

    def test_second_save_with_stale_doc_is_refused(self):
        name, new = self._editable_section()
        writer.write_file(self.path, self.doc, {name: new})
        edited = self.path.read_bytes()

        with self.assertRaises(writer.StaleDocumentError):
            writer.write_file(self.path, self.doc, {})

        # The edit must still be on disk.
        self.assertEqual(self.path.read_bytes(), edited)

    def test_stale_doc_cannot_undo_an_edit(self):
        name, new = self._editable_section()
        writer.write_file(self.path, self.doc, {name: new})
        edited = self.path.read_bytes()

        try:
            writer.write_file(self.path, self.doc, {})
        except writer.StaleDocumentError:
            pass

        doc2, _ = cndf_codec.decode(self.path.read_bytes())
        sec = cndf_layout.section_map(doc2.sections)[name]
        self.assertEqual(doc2.full_data[sec.offset:sec.offset + sec.size], new)

    def test_redecoding_makes_the_second_save_work(self):
        """The documented remedy: re-decode, then saving is a clean no-op."""
        name, new = self._editable_section()
        writer.write_file(self.path, self.doc, {name: new})

        raw2 = self.path.read_bytes()
        doc2, _ = cndf_codec.decode(raw2, source=str(self.path))
        res = writer.write_file(self.path, doc2, {})

        self.assertTrue(res.byte_identical)
        self.assertEqual(self.path.read_bytes(), raw2)

    def test_no_bypass_flag_exists(self):
        """Staleness is not overridable -- that is the design, not an oversight."""
        import inspect
        params = inspect.signature(writer.write_file).parameters
        for forbidden in ('allow_stale', 'force', 'overwrite'):
            self.assertNotIn(forbidden, params,
                             f'write_file must not expose {forbidden!r}')

    def test_dry_run_also_reports_staleness(self):
        """A dry run must not lie about what a real save would do."""
        name, new = self._editable_section()
        writer.write_file(self.path, self.doc, {name: new})
        with self.assertRaises(writer.StaleDocumentError):
            writer.write_file(self.path, self.doc, {}, dry_run=True)


class RefusalTests(_RealFileBase):
    """When the rebuild is bad, nothing is written."""

    def test_broken_rebuild_is_refused(self):
        import ndfbin_decks.core.writer as W
        original = W._codec.rebuild_sections

        def broken(doc, repl):
            return b'EUG0' + b'\x00' * 300      # not a valid CNDF

        W._codec.rebuild_sections = broken
        try:
            with self.assertRaises(ValueError) as ctx:
                name, new = self._editable_section()
                writer.write_file(self.path, self.doc, {name: new})
        finally:
            W._codec.rebuild_sections = original

        # The message should name the file, so a user can act on it.
        self.assertIn(self.path.name, str(ctx.exception))
        # And the original must be untouched.
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_unknown_section_is_refused(self):
        with self.assertRaises(ValueError):
            writer.write_bytes(self.doc, {'NOSUCH': b'x'})

    def test_non_bytes_replacement_is_refused(self):
        with self.assertRaises(ValueError):
            writer.write_bytes(self.doc, {'TOPO': 'a string'})


class SectionHelperTests(_RealFileBase):
    """sections_of / changed_sections behave sanely."""

    def test_sections_of_covers_every_section(self):
        repl = writer.sections_of(self.doc)
        self.assertEqual(set(repl), {s.name for s in self.doc.sections})
        for name, data in repl.items():
            self.assertIsInstance(data, bytes)
            sec = cndf_layout.section_map(self.doc.sections)[name]
            self.assertEqual(len(data), sec.size)

    def test_changed_sections_reports_only_real_changes(self):
        repl = writer.sections_of(self.doc)
        repl['TOPO'] = repl['TOPO'] + b'\x00\x00\x00\x00'
        self.assertEqual(writer.changed_sections(self.doc, repl), {'TOPO'})


if __name__ == '__main__':
    unittest.main(verbosity=2)