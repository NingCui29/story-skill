"""Whole-book backup safety and recovery visibility using synthetic books."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_workbench as base


class WorkbenchArchivesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-book-archives-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.root = self.directory / '书稿 中文'
        base.story.Book.create(self.root, '恢复测试作品', 'long')
        self.archives = base.story.workbench_archives
        self.output = self.directory / '整书备份.zip'
        self.destination = self.directory / '恢复副本'
        self.write('正文.md', '这是已保存的正文。\n'.encode())
        self.write('.story/drafts/候选.md', '这是尚未正式采用的候选。'.encode())
        with self._book() as book:
            self.book_id = book.meta('id')

    def _book(self):
        from contextlib import contextmanager

        @contextmanager
        def open_book():
            book = base.story.workbench._ReadOnlyBook(self.root)
            try:
                yield book
            finally:
                book.close()
        return open_book()

    def write(self, relative, content):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def archive(self):
        return self.archives.create_archive(self.root, self.output, self.book_id)

    def error(self, code, callback):
        with self.assertRaises(base.story.StoryError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def rewrite(self, transform):
        with zipfile.ZipFile(self.output) as source:
            entries = [(info, source.read(info)) for info in source.infolist()]
        entries = transform(entries)
        changed = self.directory / '修改备份.zip'
        with zipfile.ZipFile(changed, 'w') as destination:
            for info, body in entries:
                destination.writestr(info, body)
        return changed

    def test_roundtrip_keeps_identity_candidates_and_binary_materials(self):
        binary = bytes(range(256)) * 200
        self.write('封面/封面.png', binary)
        packet = self.archive()
        self.assertTrue(packet['ok'])
        self.assertEqual(packet['book_id'], self.book_id)
        self.assertEqual(packet['sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        restored = self.archives.restore_archive(self.output, self.destination)
        self.assertEqual(restored['book_id'], self.book_id)
        self.assertEqual((self.destination / '.story/drafts/候选.md').read_bytes(),
                         (self.root / '.story/drafts/候选.md').read_bytes())
        self.assertEqual((self.destination / '封面/封面.png').read_bytes(), binary)
        self.assertFalse((self.destination / '.story-restore-incomplete.json').exists())
        self.assertEqual((self.destination / '.story/state.sqlite3').stat().st_nlink, 1)
        with sqlite3.connect(self.destination / '.story/state.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_backup_reads_wal_commits_into_database_snapshot(self):
        with sqlite3.connect(self.root / '.story/state.sqlite3') as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('UPDATE meta SET value=? WHERE key=?', ('9', 'revision'))
            writer.commit()
            packet = self.archive()
            self.assertEqual(packet['revision'], 9)
            self.archives.restore_archive(self.output, self.destination)
        with sqlite3.connect(self.destination / '.story/state.sqlite3') as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0], '9')
        self.assertFalse((self.destination / '.story/state.sqlite3-wal').exists())

    def test_publishing_ledger_snapshot_is_included_and_validated(self):
        with self._book() as book:
            with base.story.publish._ledger(book, True, True):
                pass
        packet = self.archive()
        self.archives.restore_archive(self.output, self.destination)
        self.assertTrue((self.destination / '.story/publishing.sqlite3').is_file())
        self.assertGreater(packet['files'], 3)

    def test_service_tokens_cache_and_external_link_are_excluded(self):
        self.write('.story/workbench-service.json', b'SECRET-LOOPBACK-TOKEN')
        self.write('.story/.workbench-backups/one/service.json', b'OLD-SECRET-TOKEN')
        self.write('.git/config', b'SECRET-GIT-CONFIG')
        self.write('.story/workbench/index.html', b'GENERATED-SNAPSHOT')
        external = self.directory / '关联材料'
        external.mkdir()
        (external / '资料.txt').write_text('不属于本书', encoding='utf-8')
        try:
            (self.root / '链接资料').symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest('Creating links is unavailable')
        packet = self.archive()
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertFalse(any('workbench-service' in name or '.git/' in name or '链接资料/' in name for name in names))
            self.assertFalse(any(b'SECRET-' in archive.read(name) for name in names))
        self.assertIn({'path': '链接资料', 'reason': 'linked_material'}, packet['excluded'])

    def test_hardlinked_local_file_blocks_backup(self):
        os.link(self.root / '正文.md', self.root / '正文硬链接.md')
        self.error('archive_linked_file', self.archive)
        self.assertFalse(self.output.exists())

    def test_backup_requires_identity_and_explicit_external_new_path(self):
        self.error('archive_identity', lambda: self.archives.create_archive(self.root, self.output, 'other-book'))
        self.error('archive_path', lambda: self.archives.create_archive(self.root, self.root / '备份.zip'))
        self.output.write_bytes(b'prior backup')
        self.error('archive_exists', self.archive)
        self.assertEqual(self.output.read_bytes(), b'prior backup')

    def test_restore_never_overwrites_existing_destination(self):
        self.archive()
        self.destination.mkdir()
        preserved = self.destination / '正文.md'
        preserved.write_bytes(b'preserved')
        self.error('archive_exists', lambda: self.archives.restore_archive(self.output, self.destination))
        self.assertEqual(preserved.read_bytes(), b'preserved')

    def test_competing_backup_filename_is_preserved(self):
        original = base.story._publish_no_replace

        def compete(source, target):
            if Path(str(target)) == self.output:
                self.output.write_bytes(b'competing backup')
            return original(source, target)

        with patch.object(self.archives.api, '_publish_no_replace', side_effect=compete):
            self.error('archive_exists', self.archive)
        self.assertEqual(self.output.read_bytes(), b'competing backup')

    def test_partial_restore_keeps_marker_and_never_changes_original(self):
        self.archive()
        original = base.story._publish_no_replace

        def fail_during_publication(source, target):
            if Path(str(target)) == self.destination / '.story/state.sqlite3':
                raise OSError('synthetic publication failure')
            return original(source, target)

        with patch.object(self.archives.api, '_publish_no_replace', side_effect=fail_during_publication):
            self.error('archive_partial_restore', lambda: self.archives.restore_archive(self.output, self.destination))
        self.assertTrue((self.destination / '.story-restore-incomplete.json').is_file())
        self.assertTrue((self.destination / '.story/drafts/候选.md').is_file())
        self.assertEqual((self.root / '正文.md').read_text(), '这是已保存的正文。\n')

    def test_competing_restore_child_is_never_overwritten(self):
        self.archive()
        original = base.story._publish_no_replace

        def compete(source, target):
            if Path(str(target)) == self.destination / '正文.md':
                (self.destination / '正文.md').write_bytes(b'competing author text')
            return original(source, target)

        with patch.object(self.archives.api, '_publish_no_replace', side_effect=compete):
            self.error('archive_partial_restore', lambda: self.archives.restore_archive(self.output, self.destination))
        self.assertEqual((self.destination / '正文.md').read_bytes(), b'competing author text')
        self.assertTrue((self.destination / '.story-restore-incomplete.json').exists())

    def test_file_change_during_backup_prevents_publication(self):
        original = self.archives._read_file
        changed = False

        def reader(root, relative, writer=None):
            nonlocal changed
            result = original(root, relative, writer)
            if writer is not None and not changed:
                changed = True
                self.write('正文.md', '备份期间的新内容。'.encode())
            return result

        with patch.object(self.archives, '_read_file', side_effect=reader):
            self.error('archive_changed', self.archive)
        self.assertFalse(self.output.exists())

    def test_revision_change_during_backup_prevents_publication(self):
        original = self.archives._read_file
        changed = False

        def reader(root, relative, writer=None):
            nonlocal changed
            result = original(root, relative, writer)
            if writer is not None and not changed:
                changed = True
                with sqlite3.connect(self.root / '.story/state.sqlite3') as db:
                    db.execute("UPDATE meta SET value='1' WHERE key='revision'")
            return result

        with patch.object(self.archives, '_read_file', side_effect=reader):
            self.error('archive_changed', self.archive)
        self.assertFalse(self.output.exists())

    def test_corrupt_checksum_is_rejected_before_destination_creation(self):
        self.archive()
        changed = self.rewrite(lambda entries: [(info, b'changed' if info.filename == 'book/正文.md' else body)
                                               for info, body in entries])
        self.error('archive_manifest', lambda: self.archives.restore_archive(changed, self.destination))
        self.assertFalse(self.destination.exists())

    def test_same_size_corruption_is_rejected(self):
        self.archive()
        changed = self.rewrite(lambda entries: [(info, b'X' * len(body) if info.filename == 'book/正文.md' else body)
                                               for info, body in entries])
        self.error('archive_checksum', lambda: self.archives.restore_archive(changed, self.destination))
        self.assertFalse(self.destination.exists())

    def test_path_traversal_is_rejected(self):
        self.archive()
        changed = self.rewrite(lambda entries: entries + [('../outside.txt', b'escape')])
        self.error('archive_unsafe_path', lambda: self.archives.inspect_archive(changed))
        self.assertFalse((self.directory / 'outside.txt').exists())

    def test_zip_symlink_is_rejected(self):
        self.archive()
        linked = zipfile.ZipInfo('book/link')
        linked.external_attr = (stat.S_IFLNK | 0o777) << 16
        changed = self.rewrite(lambda entries: entries + [(linked, b'../../outside')])
        self.error('archive_unsafe_archive', lambda: self.archives.inspect_archive(changed))

    def test_case_colliding_zip_entries_are_rejected(self):
        self.archive()
        changed = self.rewrite(lambda entries: entries + [('book/Case.md', b'one'), ('book/case.md', b'two')])
        self.error('archive_unsafe_archive', lambda: self.archives.inspect_archive(changed))

    def test_unicode_normalization_collision_is_rejected(self):
        self.archive()
        changed = self.rewrite(lambda entries: entries + [('book/café.md', b'one'), ('book/cafe\u0301.md', b'two')])
        self.error('archive_unsafe_archive', lambda: self.archives.inspect_archive(changed))

    def test_windows_unsafe_filenames_are_rejected(self):
        self.archive()
        for name in ('book/stream:alternate', 'book/CON.txt', 'book/name?.md', 'book/trailing. '):
            with self.subTest(name=name):
                changed = self.rewrite(lambda entries: entries + [(name, b'unsafe')])
                self.error('archive_unsafe_path', lambda: self.archives.inspect_archive(changed))

    def test_unlisted_extra_entry_is_rejected(self):
        self.archive()
        changed = self.rewrite(lambda entries: entries + [('book/extra.md', b'extra')])
        self.error('archive_manifest', lambda: self.archives.inspect_archive(changed))

    def test_restore_detects_manifest_database_identity_mismatch(self):
        self.archive()

        def change_identity(entries):
            result = []
            for info, body in entries:
                if info.filename == self.archives.MANIFEST_NAME:
                    manifest = json.loads(body)
                    manifest['book_id'] = 'wrong-identity'
                    body = json.dumps(manifest).encode()
                result.append((info, body))
            return result

        changed = self.rewrite(change_identity)
        self.error('archive_identity', lambda: self.archives.restore_archive(changed, self.destination))
        self.assertFalse(self.destination.exists())

    def test_limits_block_oversized_book_and_zip(self):
        with patch.object(self.archives, 'MAX_FILE_BYTES', 1):
            self.error('archive_limit', self.archive)
        self.archive()
        with patch.object(self.archives, 'MAX_TOTAL_BYTES', 1):
            self.error('archive_limit', lambda: self.archives.inspect_archive(self.output))

    def test_recovery_catalog_surfaces_pending_records_without_adoption(self):
        self.write('.story/drafts/workbench/自动恢复/第1章_a.txt', b'recovery')
        self.write('.story/drafts/workbench/第2章.md.pending.json', b'{}')
        packet = self.archives.recovery_catalog(self.root, self.book_id)
        self.assertEqual(packet['count'], 2)
        self.assertEqual({row['id'].split(':', 1)[0] for row in packet['recoveries']}, {'file', 'pending'})
        self.assertEqual(packet['revision'], 0)
        self.assertFalse(any('adopted' in row for row in packet['recoveries']))


if __name__ == '__main__':
    unittest.main()
