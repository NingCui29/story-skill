"""Identity-bound recycling and malformed compressed backup regressions.

All native trash calls use adapters that rename only within this temporary
fixture. No test in this module accesses the real system Trash or Recycle Bin.
"""
import http.client
import json
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import test_workbench as base


class WorkbenchManagementRaceFixes(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='story-management-fixes-')
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / '确认作品 A'
        self.state = self.parent / '书架'
        self.state.mkdir()
        self.w = base.story.workbench
        self.books = base.story.workbench_books
        self.archives = base.story.workbench_archives
        base.story.Book.create(self.root, '确认作品 A', 'long')
        (self.root / 'A-only.md').write_text('确认的原作品正文。', encoding='utf-8')
        self.entry = self.w._library_entry(self.root)
        self.entries = {self.entry['key']: self.entry}
        self.digest = self.w._library_store(self.state, self.entries, None)
        self.manager = self.books.BookManager(self.w)
        self.confirmation = self.manager.trash_preview(
            self.state, self.entries, self.entry['key'])['confirmation']
        service = patch.object(self.w, '_service_request', return_value={'running': False})
        service.start()
        self.addCleanup(service.stop)

    def trash(self):
        return self.manager.trash(self.state, self.entries, self.digest,
                                  self.entry['key'], self.confirmation, saved=True)

    def replacement(self):
        base.story.Book.create(self.root, '未确认作品 B', 'short')
        (self.root / 'B-only.md').write_text('B必须保留。', encoding='utf-8')

    def fake_trash(self, target):
        self.recycled = self.parent / '模拟回收站' / target.name
        self.recycled.parent.mkdir()
        target.rename(self.recycled)
        return {'system': 'fixture', 'trash_path': str(self.recycled)}

    def assert_registered(self):
        entries, digest = self.w._library_load(self.state)
        self.assertEqual(digest, self.digest)
        self.assertEqual(set(entries), {self.entry['key']})
        self.assertEqual(entries[self.entry['key']]['book_id'], self.entry['book_id'])

    def replace_before_capture(self, replace):
        capture = self.books._rename_directory_no_replace

        def inject(source, target):
            if source.path == self.root:
                # The intent is durable here and Windows book leases have been
                # released. Replacing inside _receipt instead is correctly
                # blocked by those handles before this race can occur.
                records = [json.loads(path.read_text(encoding='utf-8'))
                           for path in (self.state / 'operations').glob('*.json')]
                self.assertEqual([record['action'] for record in records], ['trash-intent'])
                self.assertEqual(records[0]['restore_root'], str(target.path))
                replace()
            return capture(source, target)

        return patch.object(self.books, '_rename_directory_no_replace', side_effect=inject)

    def test_replacement_after_intent_is_returned_without_calling_native_trash(self):
        preserved = self.parent / 'A原稿保留'

        def replace_after_receipt():
            self.root.rename(preserved)
            self.replacement()

        with self.replace_before_capture(replace_after_receipt), \
                patch.object(self.books, 'move_to_trash') as native:
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        self.assertEqual(caught.exception.code, 'workbench_book_changed')
        native.assert_not_called()
        self.assertEqual((self.root / 'B-only.md').read_text(), 'B必须保留。')
        self.assertTrue((preserved / 'A-only.md').is_file())
        self.assertEqual(list(self.parent.glob('工作台回收暂存-*')), [])
        self.assert_registered()

    def test_replacement_after_capture_cannot_become_native_target(self):
        def native(target):
            self.assertNotEqual(target, self.root)
            self.assertEqual(target.name, self.root.name)
            self.assertTrue(target.parent.name.startswith('工作台回收暂存-'))
            self.assertEqual(self.w._library_entry(target)['book_id'], self.entry['book_id'])
            self.replacement()
            return self.fake_trash(target)

        with patch.object(self.books, 'move_to_trash', side_effect=native):
            entries, _, result = self.trash()
        self.assertTrue(result['trashed'])
        self.assertEqual(entries, {})
        self.assertTrue((self.recycled / 'A-only.md').is_file())
        self.assertTrue((self.root / 'B-only.md').is_file())
        self.assertFalse((self.recycled / 'B-only.md').exists())

    def test_same_book_id_in_new_directory_after_intent_is_not_recycled(self):
        preserved = self.parent / 'A原目录'

        def copy_after_receipt():
            self.root.rename(preserved)
            shutil.copytree(preserved, self.root)

        with self.replace_before_capture(copy_after_receipt), \
                patch.object(self.books, 'move_to_trash') as native:
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        self.assertEqual(caught.exception.code, 'workbench_book_changed')
        native.assert_not_called()
        self.assertTrue((self.root / 'A-only.md').is_file())
        self.assertTrue((preserved / 'A-only.md').is_file())
        self.assert_registered()

    def test_capture_failure_leaves_original_and_never_calls_native(self):
        with patch.object(self.books, '_rename_directory_no_replace', side_effect=OSError('unavailable')), \
                patch.object(self.books, 'move_to_trash') as native:
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        self.assertEqual(caught.exception.code, 'workbench_trash_failed')
        native.assert_not_called()
        self.assertTrue((self.root / 'A-only.md').is_file())
        self.assertEqual(list(self.parent.glob('工作台回收暂存-*')), [])
        self.assert_registered()

    def test_native_restore_target_remains_available_and_receipt_names_both_paths(self):
        with patch.object(self.books, 'move_to_trash', side_effect=self.fake_trash):
            _, _, result = self.trash()
        restore = Path(result['restore_root'])
        self.assertTrue(restore.parent.is_dir())
        self.assertFalse(restore.exists())
        receipt = json.loads((self.state / 'operations' / (result['operation_id'] + '.json')).read_text())
        self.assertEqual(receipt['original_root'], str(self.root))
        self.assertEqual(receipt['restore_root'], str(restore))
        self.assertIn(str(self.root), result['restoration_note'])
        self.assertIn(str(restore), result['restoration_note'])
        # Model OS Restore/Put Back to the native operation's original path.
        self.recycled.rename(restore)
        self.assertEqual(self.w._library_entry(restore)['book_id'], self.entry['book_id'])
        self.assertTrue((restore / 'A-only.md').is_file())

    def test_native_failure_rolls_back_same_book_without_leaving_a_stage(self):
        before = (self.root / '.story/state.sqlite3').read_bytes()
        with patch.object(self.books, 'move_to_trash', side_effect=OSError('disabled')):
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        self.assertEqual(caught.exception.code, 'workbench_trash_failed')
        self.assertEqual((self.root / '.story/state.sqlite3').read_bytes(), before)
        self.assertEqual(list(self.parent.glob('工作台回收暂存-*')), [])
        self.assert_registered()

    def test_unconfirmed_native_success_rolls_back_and_keeps_registry(self):
        with patch.object(self.books, 'move_to_trash', return_value={'system': 'fixture'}):
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        self.assertEqual(caught.exception.code, 'workbench_trash_unconfirmed')
        self.assertTrue((self.root / 'A-only.md').is_file())
        self.assertEqual(list(self.parent.glob('工作台回收暂存-*')), [])
        self.assert_registered()

    def test_failed_native_move_does_not_overwrite_replacement_during_rollback(self):
        def fail_with_replacement(target):
            self.replacement()
            raise OSError('cancelled')

        with patch.object(self.books, 'move_to_trash', side_effect=fail_with_replacement):
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        error = caught.exception
        self.assertEqual(error.code, 'workbench_trash_recovery_required')
        self.assertTrue((self.root / 'B-only.md').is_file())
        recovery = Path(error.details['recovery_root'])
        self.assertEqual(self.w._library_entry(recovery)['book_id'], self.entry['book_id'])
        self.assertTrue((recovery / 'A-only.md').is_file())
        self.assert_registered()

    def test_rollback_cannot_replace_even_an_empty_new_directory(self):
        def fail_with_empty_replacement(target):
            self.root.mkdir()
            raise OSError('cancelled')

        with patch.object(self.books, 'move_to_trash', side_effect=fail_with_empty_replacement):
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        self.assertEqual(caught.exception.code, 'workbench_trash_recovery_required')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue((Path(caught.exception.details['recovery_root']) / 'A-only.md').exists())
        self.assert_registered()

    def test_native_error_after_move_retains_valid_system_restore_parent(self):
        captured = {}

        def moved_then_error(target):
            captured['target'] = target
            self.fake_trash(target)
            raise OSError('reply lost')

        with patch.object(self.books, 'move_to_trash', side_effect=moved_then_error):
            with self.assertRaises(base.story.StoryError) as caught:
                self.trash()
        self.assertEqual(caught.exception.code, 'workbench_trash_failed')
        self.assertTrue(captured['target'].parent.is_dir())
        self.recycled.rename(captured['target'])
        self.assertTrue((captured['target'] / 'A-only.md').is_file())
        self.assert_registered()

    def corrupt_archive(self, manifest=False):
        archive = self.parent / '有效备份.zip'
        self.archives.create_archive(self.root, archive)
        raw = bytearray(archive.read_bytes())
        with zipfile.ZipFile(archive) as source:
            info = source.getinfo(self.archives.MANIFEST_NAME if manifest else 'book/A-only.md')
            self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
            name, extra = struct.unpack_from('<HH', raw, info.header_offset + 26)
            start = info.header_offset + 30 + name + extra
            raw[start] = (raw[start] & ~7) | 7  # Reserved DEFLATE block type.
        corrupt = self.parent / '损坏备份.zip'
        corrupt.write_bytes(raw)
        return corrupt

    def test_invalid_deflate_payload_is_structured_for_inspect_and_restore(self):
        archive = self.corrupt_archive()
        for label, call in (
                ('inspect', lambda: self.archives.inspect_archive(archive)),
                ('restore', lambda: self.archives.restore_archive(archive, self.parent / '恢复副本'))):
            with self.subTest(label=label), self.assertRaises(base.story.StoryError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'archive_invalid_zip')
        self.assertFalse((self.parent / '恢复副本').exists())
        self.assertEqual(list(self.parent.glob('.story-restore-*')), [])
        self.assertTrue((self.root / 'A-only.md').is_file())

    def test_invalid_deflate_manifest_is_structured(self):
        archive = self.corrupt_archive(manifest=True)
        for label, call in (
                ('inspect', lambda: self.archives.inspect_archive(archive)),
                ('restore', lambda: self.archives.restore_archive(archive, self.parent / '恢复副本'))):
            with self.subTest(label=label), self.assertRaises(base.story.StoryError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'archive_invalid_zip')
        self.assertFalse((self.parent / '恢复副本').exists())
        self.assertEqual(list(self.parent.glob('.story-restore-*')), [])

    def test_http_restore_returns_json_error_and_cleans_staging(self):
        archive = self.corrupt_archive()
        server = self.w.library_server(self.parent / 'http-shelf', port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            connection.request('POST', '/api/restore-backup', json.dumps({
                'archive': str(archive), 'destination': str(self.parent / 'HTTP恢复')}), {
                    'Content-Type': 'application/json', 'X-Story-Token': server.library_token,
                    'Origin': 'http://127.0.0.1:' + str(server.server_port)})
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 409)
            self.assertEqual(body['error'], 'archive_invalid_zip')
        finally:
            connection.close()
            server.shutdown()
            thread.join(3)
            server.server_close()
        self.assertFalse(thread.is_alive())
        self.assertFalse((self.parent / 'HTTP恢复').exists())
        self.assertEqual(list(self.parent.glob('.story-restore-*')), [])


if __name__ == '__main__':
    unittest.main()
