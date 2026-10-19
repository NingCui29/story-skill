"""Book management changes shelf records or uses the native reversible trash."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchBookManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-book-manager-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / '书架'
        self.state.mkdir()
        self.w = base.story.workbench
        self.module = base.story.workbench_books
        self.manager = self.module.BookManager(self.w)
        self.entries = {}
        self.digest = None

    def book(self, name='原作品'):
        path = self.root / name
        base.story.Book.create(path, name, 'long')
        entry = self.w._library_entry(path)
        self.entries[entry['key']] = entry
        self.digest = self.w._library_store(self.state, self.entries, self.digest)
        return path, entry

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(base.story.StoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def preview(self, entry):
        return self.manager.trash_preview(self.state, self.entries, entry['key'])['confirmation']

    def trash(self, entry, confirmation=None, **kwargs):
        return self.manager.trash(self.state, self.entries, self.digest, entry['key'],
                                  confirmation or self.preview(entry), saved=True, **kwargs)

    def fake_trash(self, path):
        target = self.root / '合成回收站'
        path.rename(target)
        return {'system': 'fixture', 'trash_path': str(target)}

    def test_create_new_empty_book_registers_identity_and_no_formal_text(self):
        path = self.root / '新作品 中文'
        entries, digest, result = self.manager.create(self.state, {}, None, str(path), '测试作品', 'short')
        self.assertTrue(result['created'])
        self.assertTrue(result['registered'])
        self.assertEqual(len(entries), 1)
        self.assertEqual(next(iter(entries.values()))['progress']['formal_chapters'], 0)
        self.assertEqual(self.w._library_load(self.state)[1], digest)
        self.assertEqual(list(path.glob('*.md')), [])

    def test_create_existing_directory_never_initializes_it(self):
        path = self.root / '已存在'
        path.mkdir()
        self.error('workbench_create_exists', self.manager.create,
                   self.state, {}, None, str(path), '新书', 'long')
        self.assertFalse((path / '.story').exists())

    def test_create_rejects_existing_book_as_parent(self):
        path, _ = self.book()
        self.error('workbench_nested_book', self.manager.create,
                   self.state, self.entries, self.digest, str(path / '子书'), '子书', 'long')
        self.assertFalse((path / '子书').exists())

    def test_create_failed_registry_write_reports_created_book(self):
        path = self.root / '未登记的新书'
        with patch.object(self.w, '_library_store', side_effect=OSError('disk write failed')):
            entries, digest, result = self.manager.create(self.state, {}, None, str(path), '新书', 'long')
        self.assertTrue(result['created'])
        self.assertFalse(result['registered'])
        self.assertTrue((path / '.story/state.sqlite3').exists())
        self.assertEqual(entries, {})
        self.assertIsNone(digest)

    def test_create_stale_registry_does_not_create_book(self):
        self.book()
        path = self.root / '不应创建'
        self.error('workbench_library_changed', self.manager.create,
                   self.state, {}, None, str(path), '新书', 'long')
        self.assertFalse(path.exists())

    def test_remove_only_mutates_shelf_and_returns_durable_undo(self):
        path, entry = self.book()
        before = {p.relative_to(path): p.read_bytes() for p in path.rglob('*') if p.is_file()}
        with patch.object(self.w, '_service_request') as service:
            entries, digest, result = self.manager.remove(self.state, self.entries, self.digest, entry['key'])
        service.assert_not_called()
        self.assertEqual(entries, {})
        self.assertFalse(result['files_changed'])
        self.assertEqual(before, {p.relative_to(path): p.read_bytes() for p in path.rglob('*') if p.is_file()})
        self.assertTrue((self.state / 'operations' / (result['undo_id'] + '.json')).is_file())
        restored, _, undo = self.manager.undo_remove(self.state, entries, digest, result['undo_id'])
        self.assertEqual(restored[entry['key']]['book_id'], entry['book_id'])
        self.assertTrue(undo['restored'])

    def test_remove_unavailable_book_keeps_its_files(self):
        path, entry = self.book()
        moved = self.root / '已迁移'
        path.rename(moved)
        entries, _, result = self.manager.remove(self.state, self.entries, self.digest, entry['key'])
        self.assertEqual(entries, {})
        self.assertTrue(moved.is_dir())
        self.assertTrue(result['removed'])

    def test_undo_refuses_replaced_book_identity(self):
        path, entry = self.book()
        entries, digest, result = self.manager.remove(self.state, self.entries, self.digest, entry['key'])
        path.rename(self.root / '原作品备份')
        base.story.Book.create(path, '不同作品', 'long')
        self.error('workbench_book_changed', self.manager.undo_remove,
                   self.state, entries, digest, result['undo_id'])

    def test_remove_receipt_failure_still_reports_successful_shelf_change(self):
        _, entry = self.book()
        with patch.object(self.manager, '_receipt', side_effect=OSError('full')):
            entries, _, result = self.manager.remove(self.state, self.entries, self.digest, entry['key'])
        self.assertEqual(entries, {})
        self.assertTrue(result['removed'])
        self.assertIn('warning', result)

    def test_relink_only_matches_original_book_id(self):
        path, entry = self.book()
        moved = self.root / '改名后作品'
        path.rename(moved)
        before = (moved / '.story/state.sqlite3').read_bytes()
        entries, _, result = self.manager.relink(self.state, self.entries, self.digest, entry['key'], str(moved))
        self.assertNotIn(entry['key'], entries)
        self.assertEqual(next(iter(entries.values()))['book_id'], entry['book_id'])
        self.assertEqual(result['root'], str(moved))
        self.assertEqual((moved / '.story/state.sqlite3').read_bytes(), before)

    def test_relink_refuses_different_book_and_available_original_copy(self):
        path, entry = self.book()
        other, _ = self.book('另一部作品')
        self.error('workbench_book_changed', self.manager.relink,
                   self.state, self.entries, self.digest, entry['key'], str(other))
        copy = self.root / '复制本'
        shutil.copytree(path, copy)
        self.error('workbench_original_available', self.manager.relink,
                   self.state, self.entries, self.digest, entry['key'], str(copy))

    def test_preview_discloses_whole_directory_and_has_no_move(self):
        path, entry = self.book()
        (path / '正文.md').write_text('测试正文', encoding='utf-8')
        with patch.object(self.module, 'move_to_trash') as native:
            result = self.manager.trash_preview(self.state, self.entries, entry['key'])
        native.assert_not_called()
        self.assertEqual(result['confirmation']['book_id'], entry['book_id'])
        self.assertEqual(result['confirmation']['root'], str(path))
        self.assertGreater(result['bytes'], 0)
        self.assertIn('恢复稿', result['scope'])

    def test_trash_requires_explicit_saved_and_exact_confirmation(self):
        path, entry = self.book()
        confirmation = self.preview(entry)
        self.error('workbench_unsaved', self.manager.trash,
                   self.state, self.entries, self.digest, entry['key'], confirmation)
        wrong = {**confirmation, 'title': '不同标题'}
        with patch.object(self.module, 'move_to_trash') as native:
            self.error('workbench_delete_confirmation', self.trash, entry, wrong)
        native.assert_not_called()
        self.assertTrue(path.exists())

    def test_running_or_unknown_editor_blocks_trash_without_stop(self):
        path, entry = self.book()
        for running in (True, None):
            with self.subTest(running=running), patch.object(self.w, '_service_request', return_value={'running': running}) as service, patch.object(self.module, 'move_to_trash') as native:
                self.error('workbench_running', self.trash, entry)
                service.assert_called_once_with(path)
                native.assert_not_called()
        self.assertTrue(path.exists())

    def test_held_editor_lease_blocks_even_without_service_record(self):
        path, entry = self.book()
        with self.w._editor_lease(path), patch.object(self.module, 'move_to_trash') as native:
            self.error('workbench_running', self.trash, entry)
        native.assert_not_called()
        self.assertTrue(path.exists())

    def test_core_writer_lock_blocks_trash(self):
        path, entry = self.book()
        book = base.story.Book(path, read_only=True)
        self.addCleanup(book.close)
        with base.story.storage.operation_lock(book), patch.object(self.module, 'move_to_trash') as native:
            self.error('book_busy', self.trash, entry)
        native.assert_not_called()
        self.assertTrue(path.exists())

    def test_lifecycle_guard_is_exclusive_and_outside_book(self):
        path, _ = self.book()
        with self.module.lifecycle_guard(base.story, path):
            self.error('workbench_book_busy', lambda: self.module.lifecycle_guard(base.story, path).__enter__())
        self.assertEqual(len(list(self.root.glob('.story-workbench-*.lock'))), 1)
        self.assertEqual(list(path.glob('.story-workbench-*.lock')), [])

    def test_native_failure_never_removes_shelf_or_falls_back_to_delete(self):
        path, entry = self.book()
        before = (path / '.story/state.sqlite3').read_bytes()
        with patch.object(self.module, 'move_to_trash', side_effect=OSError('Recycle Bin disabled')), patch.object(shutil, 'rmtree') as remove:
            self.error('workbench_trash_failed', self.trash, entry)
        remove.assert_not_called()
        self.assertEqual((path / '.story/state.sqlite3').read_bytes(), before)
        self.assertIn(entry['key'], self.w._library_load(self.state)[0])

    def test_trash_success_moves_only_book_directory_then_updates_shelf(self):
        path, entry = self.book()
        external = self.root / '外部材料'
        external.mkdir()
        (external / '材料.md').write_text('外部内容', encoding='utf-8')
        with patch.object(self.module, 'move_to_trash', side_effect=self.fake_trash):
            entries, _, result = self.trash(entry)
        self.assertTrue(result['trashed'])
        self.assertTrue(result['shelf_removed'])
        self.assertEqual(entries, {})
        self.assertFalse(path.exists())
        self.assertTrue((self.root / '合成回收站/.story/state.sqlite3').exists())
        self.assertEqual((external / '材料.md').read_text(encoding='utf-8'), '外部内容')

    def test_post_trash_registry_failure_reports_completed_move(self):
        path, entry = self.book()
        with patch.object(self.module, 'move_to_trash', side_effect=self.fake_trash), patch.object(self.w, '_library_store', side_effect=OSError('disk full')):
            entries, digest, result = self.trash(entry)
        self.assertTrue(result['trashed'])
        self.assertFalse(result['shelf_removed'])
        self.assertEqual(entries, self.entries)
        self.assertEqual(digest, self.digest)
        self.assertFalse(path.exists())

    def test_system_success_with_original_path_present_is_not_confirmed(self):
        path, entry = self.book()
        with patch.object(self.module, 'move_to_trash', return_value={'system': 'fixture'}):
            self.error('workbench_trash_unconfirmed', self.trash, entry)
        self.assertTrue(path.exists())
        self.assertIn(entry['key'], self.w._library_load(self.state)[0])

    def test_same_book_copied_into_replaced_directory_invalidates_preview(self):
        path, entry = self.book()
        confirmation = self.preview(entry)
        original = self.root / '原目录'
        path.rename(original)
        shutil.copytree(original, path)
        with patch.object(self.module, 'move_to_trash') as native:
            self.error('workbench_delete_confirmation', self.trash, entry, confirmation)
        native.assert_not_called()

    def test_nested_unregistered_book_blocks_trash(self):
        path, entry = self.book()
        base.story.Book.create(path / '另一部作品', '嵌套作品', 'short')
        with patch.object(self.module, 'move_to_trash') as native:
            self.error('workbench_nested_book', self.preview, entry)
        native.assert_not_called()

    def test_symlinks_inside_book_or_root_block_trash(self):
        path, entry = self.book()
        try:
            (path / '外部链接').symlink_to(self.state, target_is_directory=True)
        except OSError:
            self.skipTest('platform does not permit test symlinks')
        self.error('workbench_linked_path', self.preview, entry)

    def test_book_containing_shelf_state_is_protected(self):
        path, entry = self.book()
        nested_state = path / '书架数据'
        self.error('workbench_unsafe_root', self.manager.trash_preview,
                   nested_state, self.entries, entry['key'])

    def test_unknown_platform_cannot_fall_back_to_filesystem_removal(self):
        with patch.object(self.module.sys, 'platform', 'linux'), patch.object(self.module, '_macos_trash') as mac, patch.object(self.module, '_windows_trash') as windows:
            with self.assertRaises(OSError):
                self.module.move_to_trash(self.root)
        mac.assert_not_called()
        windows.assert_not_called()

    def test_native_platform_dispatch_requires_absolute_path(self):
        with self.assertRaises(ValueError):
            self.module.move_to_trash(Path('relative'))
        with patch.object(self.module.sys, 'platform', 'darwin'), patch.object(self.module, '_macos_trash', return_value={'system': 'macos'}) as native:
            self.assertEqual(self.module.move_to_trash(self.root), {'system': 'macos'})
        native.assert_called_once_with(self.root)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires macOS Foundation')
    def test_foundation_missing_path_errors_without_moving_any_directory(self):
        with self.assertRaises(OSError):
            self.module._macos_trash(self.root / 'does-not-exist')
        self.assertTrue(self.root.exists())

    @unittest.skipUnless(sys.platform == 'win32', 'requires Windows Shell')
    def test_windows_shell_missing_path_errors_without_moving_any_directory(self):
        with self.assertRaises(OSError):
            self.module._windows_trash(self.root / 'does-not-exist')
        self.assertTrue(self.root.exists())


if __name__ == '__main__':
    unittest.main()
