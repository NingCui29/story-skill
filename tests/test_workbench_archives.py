"""Whole-book backup safety and recovery visibility using synthetic books."""
import hashlib
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import uuid
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
        with closing(sqlite3.connect(self.destination / '.story/state.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def assert_snapshot_connections_closed_before_cleanup(self, failure=None):
        """Retain real handles so garbage collection cannot conceal a leak."""
        original_connect = sqlite3.connect
        original_temporary = tempfile.TemporaryDirectory
        captured, cleanup_checks = [], []

        class FailingBackupConnection(sqlite3.Connection):
            def backup(self, *args, **kwargs):
                raise sqlite3.DatabaseError('synthetic source backup failure')

        class FailingIntegrityConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.strip().upper() == 'PRAGMA INTEGRITY_CHECK':
                    return super().execute("SELECT 'synthetic integrity failure'")
                return super().execute(sql, *args, **kwargs)

        def connect(database, *args, **kwargs):
            snapshot = Path(str(database)).name == 'database-0.sqlite3'
            if snapshot and failure == 'integrity':
                kwargs['factory'] = FailingIntegrityConnection
            elif not snapshot and failure == 'backup':
                kwargs['factory'] = FailingBackupConnection
            connection = original_connect(database, *args, **kwargs)
            if snapshot:
                captured.append(connection)
            return connection

        @contextmanager
        def temporary(*args, **kwargs):
            with original_temporary(*args, **kwargs) as location:
                try:
                    yield location
                finally:
                    if kwargs.get('prefix') == '.story-archive-':
                        self.assertTrue(captured, 'The archive must create a real SQLite snapshot')
                        leaked = []
                        for connection in captured:
                            try:
                                connection.execute('SELECT 1')
                            except sqlite3.ProgrammingError:
                                cleanup_checks.append('closed')
                            else:
                                leaked.append(connection)
                        # Preserve an informative assertion on Windows without
                        # allowing this test's own cleanup to leave open files.
                        for connection in leaked:
                            connection.close()
                        self.assertEqual(leaked, [], 'Snapshot connections must close before temporary-directory removal')

        with patch.object(self.archives.sqlite3, 'connect', side_effect=connect), \
                patch.object(self.archives.tempfile, 'TemporaryDirectory', side_effect=temporary):
            if failure == 'backup':
                with self.assertRaisesRegex(sqlite3.DatabaseError, 'synthetic source backup failure'):
                    self.archive()
            elif failure == 'integrity':
                self.error('archive_state', self.archive)
            else:
                self.assertTrue(self.archive()['ok'])
        self.assertEqual(cleanup_checks, ['closed'])
        # The caller still owns strong references after the complete operation.
        for connection in captured:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')
        self.assertFalse(any(path.name.startswith('.story-archive-') for path in self.directory.iterdir()))

    def test_snapshot_connection_closed_before_successful_archive_cleanup(self):
        self.assert_snapshot_connections_closed_before_cleanup()

    def test_snapshot_connection_closed_when_source_backup_raises(self):
        self.assert_snapshot_connections_closed_before_cleanup('backup')
        self.assertFalse(self.output.exists())

    def test_snapshot_connection_closed_when_integrity_check_fails(self):
        self.assert_snapshot_connections_closed_before_cleanup('integrity')
        self.assertFalse(self.output.exists())

    def test_backup_reads_wal_commits_into_database_snapshot(self):
        with closing(sqlite3.connect(self.root / '.story/state.sqlite3')) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('UPDATE meta SET value=? WHERE key=?', ('9', 'revision'))
            writer.commit()
            packet = self.archive()
            self.assertEqual(packet['revision'], 9)
            self.archives.restore_archive(self.output, self.destination)
        with closing(sqlite3.connect(self.destination / '.story/state.sqlite3')) as db:
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
        self.write('.backups/old/service.json', b'OLD-SECRET-LIBRARY-TOKEN')
        self.write('.story/.backups/old/service.json', b'OLD-SECRET-SERVICE-TOKEN')
        self.write('.story/workbench-server.lock', b'0')
        self.write('server.lock', b'0')
        self.write('service.json', b'SECRET-SHELF-TOKEN')
        self.write('.story/drafts/workbench/.backups/old/completed.json', b'preserved-candidate-recovery')
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
            self.assertIn('book/.story/drafts/workbench/.backups/old/completed.json', names)
            self.assertFalse(any(name.endswith('server.lock') for name in names))
        self.assertIn({'path': '链接资料', 'reason': 'linked_material'}, packet['excluded'])

    def test_hardlinked_local_file_blocks_backup(self):
        os.link(self.root / '正文.md', self.root / '正文硬链接.md')
        self.error('archive_linked_file', self.archive)
        self.assertFalse(self.output.exists())

    @contextmanager
    def zeroed_direntry_identity(self):
        """Reproduce documented Windows DirEntry.stat identity placeholders."""
        original = os.scandir

        class ZeroedEntry:
            def __init__(self, entry):
                self.entry, self.name = entry, entry.name

            def stat(self, *, follow_symlinks=True):
                value = self.entry.stat(follow_symlinks=follow_symlinks)
                return SimpleNamespace(st_mode=value.st_mode, st_size=value.st_size,
                                       st_mtime_ns=value.st_mtime_ns, st_dev=0, st_ino=0,
                                       st_nlink=0, st_file_attributes=getattr(value, 'st_file_attributes', 0))

        @contextmanager
        def scandir(*args, **kwargs):
            with original(*args, **kwargs) as entries:
                yield iter(ZeroedEntry(entry) for entry in entries)

        class ScanOS:
            def __getattr__(self, name):
                return getattr(os, name)

        scan_os = ScanOS()
        scan_os.scandir = scandir
        # Scope Windows-like DirEntry objects to the archive scanner, leaving
        # tempfile/shutil's independent inode checks and cleanup untouched.
        with patch.object(self.archives, 'os', scan_os):
            yield

    def assert_real_scan_identity(self, inventory):
        for relative, row in inventory.items():
            value = os.stat(self.root / relative, follow_symlinks=False)
            self.assertEqual(row, (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns))
            self.assertNotEqual(row[:2], (0, 0), relative)
            self.assertEqual(value.st_nlink, 1)

    def test_zeroed_direntry_metadata_uses_real_stat_identity(self):
        with self.zeroed_direntry_identity():
            inventory, _ = self.archives._scan(self.root)
        self.assert_real_scan_identity(inventory)
        with self.zeroed_direntry_identity():
            self.assertTrue(self.archive()['ok'])
        self.assertTrue(self.output.is_file())

    def test_path_pinned_windows_scan_keeps_real_inode_device_and_link_metadata(self):
        # Exercise the fd=None branch with genuine no-follow metadata, while
        # retaining POSIX pins beneath the mock for safe local fixture reads.
        original_pin = self.archives.api._pinned_directory
        original_verify = self.archives.api._verify_bound_directory

        @contextmanager
        def path_pin(*args, **kwargs):
            with original_pin(*args, **kwargs) as pinned:
                yield SimpleNamespace(path=pinned.path, fd=None, original=pinned)

        def verify(directory):
            original_verify(directory.original)

        with self.zeroed_direntry_identity(), \
                patch.object(self.archives.api, '_pinned_directory', side_effect=path_pin), \
                patch.object(self.archives.api, '_verify_bound_directory', side_effect=verify):
            inventory, _ = self.archives._scan(self.root)
        self.assert_real_scan_identity(inventory)

    def test_rotating_windows_directory_guards_are_silently_ignored(self):
        original_pin = self.archives.api._pinned_directory
        created = []

        @contextmanager
        def guarded_pin(*args, **kwargs):
            with original_pin(*args, **kwargs) as pinned:
                guard = pinned.path / ('.story-pin-' + uuid.uuid4().hex)
                guard.write_bytes(b'windows-delete-on-close-guard')
                created.append(guard.name)
                try:
                    yield pinned
                finally:
                    guard.unlink()

        # Nonreserved lookalikes remain ordinary author files.
        self.write('.story-pin-author-notes.md', b'author notes')
        self.write('.story/drafts/.story-pin-1234', b'author draft')
        with patch.object(self.archives.api, '_pinned_directory', side_effect=guarded_pin):
            first, first_excluded = self.archives._scan(self.root)
            second, second_excluded = self.archives._scan(self.root)
            self.assertEqual(first, second)
            self.assertEqual(first_excluded, second_excluded)
            packet = self.archive()
        self.assertGreater(len(created), 2)
        self.assertTrue(packet['ok'])
        self.assertFalse(any(Path(row['path']).name in created for row in packet['excluded']))
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertFalse(any(Path(name).name in created for name in names))
            self.assertIn('book/.story-pin-author-notes.md', names)
            self.assertIn('book/.story/drafts/.story-pin-1234', names)
        self.archives.restore_archive(self.output, self.destination)
        self.assertEqual((self.destination / '.story-pin-author-notes.md').read_bytes(), b'author notes')

    def test_archive_stage_fsync_uses_writable_handle_and_keeps_zip_valid(self):
        original_fsync = os.fsync
        flushed_sizes = []

        def flush_writable(fd):
            # Zero-byte writes validate access without changing content or
            # offsets. A read-only descriptor raises EBADF on POSIX and cannot
            # provide Windows FlushFileBuffers' required write access.
            self.assertEqual(os.write(fd, b''), 0)
            flushed_sizes.append(os.fstat(fd).st_size)
            original_fsync(fd)

        with patch.object(self.archives.os, 'fsync', side_effect=flush_writable):
            result = self.archive()
        self.assertEqual(len(flushed_sizes), 1)
        self.assertGreater(flushed_sizes[0], 0)
        verified = self.archives.inspect_archive(self.output)
        self.assertEqual(verified['book_id'], self.book_id)
        self.assertEqual(result['sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_zeroed_direntry_metadata_still_rejects_actual_hardlinks(self):
        linked = self.root / '正文硬链接.md'
        os.link(self.root / '正文.md', linked)
        original_stat = os.stat
        checked_link_counts = []

        def checked_stat(*args, **kwargs):
            value = original_stat(*args, **kwargs)
            if value.st_nlink == 2 and stat.S_ISREG(value.st_mode):
                checked_link_counts.append(value.st_nlink)
            return value

        with self.zeroed_direntry_identity(), patch.object(self.archives.os, 'stat', side_effect=checked_stat):
            self.error('archive_linked_file', lambda: self.archives._scan(self.root))
        self.assertTrue(checked_link_counts, 'Actual no-follow metadata must detect the hardlink count')

    def test_nested_initialized_book_is_never_absorbed_into_backup(self):
        nested = self.root / '另一部独立作品'
        base.story.Book.create(nested, '独立作品', 'short')
        self.error('archive_nested_book', self.archive)
        self.assertFalse(self.output.exists())
        self.assertTrue((nested / '.story/state.sqlite3').is_file())

    def test_existing_output_alias_cannot_overwrite_source(self):
        original = (self.root / '正文.md').read_bytes()
        os.link(self.root / '正文.md', self.output)
        self.error('archive_exists', self.archive)
        self.assertEqual((self.root / '正文.md').read_bytes(), original)

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
                with closing(sqlite3.connect(self.root / '.story/state.sqlite3')) as db:
                    db.execute("UPDATE meta SET value='1' WHERE key='revision'")
                    db.commit()
            return result

        with patch.object(self.archives, '_read_file', side_effect=reader):
            self.error('archive_changed', self.archive)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == 'posix', 'Windows pins prevent renaming open directories')
    def test_source_tree_replacement_never_publishes_success(self):
        original = self.archives._scan
        calls = 0
        moved = self.directory / '保留原作品'

        def scan(root):
            nonlocal calls
            result = original(root)
            calls += 1
            if calls == 2:
                self.root.rename(moved)
                base.story.Book.create(self.root, '替换作品', 'long')
            return result

        with patch.object(self.archives, '_scan', side_effect=scan):
            with self.assertRaises(base.story.StoryError):
                self.archive()
        self.assertFalse(self.output.exists())
        self.assertEqual((moved / '正文.md').read_text(), '这是已保存的正文。\n')
        self.assertTrue((self.root / '.story/state.sqlite3').is_file())

    @unittest.skipUnless(os.name == 'posix', 'Windows denies replacing the pinned archive')
    def test_archive_path_replacement_during_inspection_is_detected(self):
        self.archive()
        original = self.archives._hash_stream
        changed = False

        def hash_stream(stream, *args, **kwargs):
            nonlocal changed
            result = original(stream, *args, **kwargs)
            if isinstance(stream, zipfile.ZipExtFile) and not changed:
                changed = True
                old_bytes = self.output.read_bytes()
                self.output.rename(self.directory / '保留原备份.zip')
                self.output.write_bytes(old_bytes)
            return result

        with patch.object(self.archives, '_hash_stream', side_effect=hash_stream):
            self.error('archive_changed', lambda: self.archives.inspect_archive(self.output))

    @unittest.skipUnless(os.name == 'posix', 'Windows denies replacing the pinned archive')
    def test_archive_path_replacement_during_restore_prevents_target_creation(self):
        self.archive()
        original = self.archives._hash_stream
        changed = False

        def hash_stream(stream, output=None, *args, **kwargs):
            nonlocal changed
            result = original(stream, output, *args, **kwargs)
            if output is not None and isinstance(stream, zipfile.ZipExtFile) and not changed:
                changed = True
                old_bytes = self.output.read_bytes()
                self.output.rename(self.directory / '保留原备份.zip')
                self.output.write_bytes(old_bytes)
            return result

        with patch.object(self.archives, '_hash_stream', side_effect=hash_stream):
            self.error('archive_changed', lambda: self.archives.restore_archive(self.output, self.destination))
        self.assertFalse(self.destination.exists())

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

    def test_nested_book_in_manifest_is_rejected_before_restoration(self):
        self.archive()

        def nest_entry(entries):
            result = []
            for info, body in entries:
                if info.filename == self.archives.MANIFEST_NAME:
                    manifest = json.loads(body)
                    for row in manifest['files']:
                        if row['path'] == '正文.md':
                            row['path'] = '另一部作品/.story/state.sqlite3'
                    body = json.dumps(manifest).encode()
                elif info.filename == 'book/正文.md':
                    info = zipfile.ZipInfo('book/另一部作品/.story/state.sqlite3')
                result.append((info, body))
            return result

        changed = self.rewrite(nest_entry)
        self.error('archive_nested_book', lambda: self.archives.restore_archive(changed, self.destination))
        self.assertFalse(self.destination.exists())

    def test_reserved_directory_guard_in_manifest_cannot_be_restored(self):
        self.archive()
        guard_path = '.story/drafts/.story-pin-' + 'a' * 32

        def replace_with_guard(entries):
            result = []
            for info, body in entries:
                if info.filename == self.archives.MANIFEST_NAME:
                    manifest = json.loads(body)
                    for row in manifest['files']:
                        if row['path'] == '正文.md':
                            row['path'] = guard_path
                    body = json.dumps(manifest).encode()
                elif info.filename == 'book/正文.md':
                    info = zipfile.ZipInfo('book/' + guard_path)
                result.append((info, body))
            return result

        changed = self.rewrite(replace_with_guard)
        self.error('archive_manifest', lambda: self.archives.restore_archive(changed, self.destination))
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
