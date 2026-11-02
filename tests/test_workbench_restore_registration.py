"""Restoring a book cannot silently replace an existing shelf registration."""
import shutil
import unittest
from unittest.mock import patch

import test_workbench_library as library


class RestoreRegistrationTests(unittest.TestCase):
    setUp = library.WorkbenchLibraryTests.setUp
    start = library.WorkbenchLibraryTests.start
    serve = library.WorkbenchLibraryTests.serve
    request = library.WorkbenchLibraryTests.request
    post = library.WorkbenchLibraryTests.post
    book = library.WorkbenchLibraryTests.book

    def prepared(self, full=False):
        source = self.book('原登记作品')
        roots = [source]
        if full:
            roots += [self.book(f'其他登记作品 {index}') for index in range(29)]
        server = self.start(books=roots)
        entry = self.w._library_entry(source)
        status, marked = self.post(server, 'set-book-status', {
            'key': entry['key'], 'expected_book_id': entry['book_id'], 'value': 'completed'})
        self.assertEqual(status, 200, marked)
        archive = self.root / '原作品备份.zip'
        library.base.story.workbench_archives.create_archive(source, archive, entry['book_id'])
        return server, source, entry, archive

    def restore(self, server, archive, destination):
        return self.post(server, 'restore-backup', {
            'archive': str(archive), 'destination': str(destination)})

    def test_same_identity_preserves_manual_status_and_registration_properties(self):
        server, source, entry, archive = self.prepared()
        before, _ = self.w._library_load(self.state)
        source.rename(self.root / '保留原稿')
        status, result = self.restore(server, archive, source)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['registered'])
        after, _ = self.w._library_load(self.state)
        self.assertEqual(after, before)
        row = result['books'][0]
        self.assertTrue(row['available'])
        self.assertEqual(row['book_id'], entry['book_id'])
        self.assertEqual(row['writing_status']['value'], 'completed')
        self.assertEqual(row['writing_status']['source'], 'author_mark')

    def test_different_identity_is_rejected_before_restoring_any_files(self):
        server, source, entry, archive = self.prepared()
        other = self.book('另一个身份')
        other_archive = self.root / '另一作品备份.zip'
        library.base.story.workbench_archives.create_archive(other, other_archive)
        source.rename(self.root / '保留原稿')
        before = (self.state / 'library.json').read_bytes()
        with patch.object(library.base.story.workbench_archives, 'restore_archive',
                          side_effect=AssertionError('must reject before restoration')):
            status, result = self.restore(server, other_archive, source)
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error'], 'workbench_book_changed')
        self.assertFalse(source.exists())
        self.assertEqual((self.state / 'library.json').read_bytes(), before)

    def test_new_destination_registers_normally_without_changing_other_entries(self):
        server, source, entry, archive = self.prepared()
        before, _ = self.w._library_load(self.state)
        destination = self.root / '恢复副本'
        status, result = self.restore(server, archive, destination)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['registered'])
        self.assertNotIn('warning', result)
        self.assertEqual(result['book_id'], entry['book_id'])
        self.assertEqual(len(result['books']), 2)
        after, _ = self.w._library_load(self.state)
        self.assertEqual(after[entry['key']], before[entry['key']])
        self.assertEqual(self.w._library_entry(destination)['book_id'], entry['book_id'])

    def test_registration_write_failure_keeps_restored_copy_and_old_registry(self):
        server, source, entry, archive = self.prepared()
        before = (self.state / 'library.json').read_bytes()
        destination = self.root / '已恢复但未登记'
        with patch.object(self.w, '_library_store', side_effect=OSError('合成登记写入失败')):
            status, result = self.restore(server, archive, destination)
        self.assertEqual(status, 200, result)
        self.assertFalse(result['registered'])
        self.assertIn('副本已恢复', result['warning'])
        self.assertIn('登记未完成', result['warning'])
        self.assertEqual((self.state / 'library.json').read_bytes(), before)
        self.assertEqual(self.w._library_entry(destination)['book_id'], entry['book_id'])

    def test_same_path_write_failure_preserves_original_marked_registration(self):
        server, source, entry, archive = self.prepared()
        before = (self.state / 'library.json').read_bytes()
        source.rename(self.root / '保留原稿')
        with patch.object(self.w, '_library_store', side_effect=OSError('合成登记写入失败')):
            status, result = self.restore(server, archive, source)
        self.assertEqual(status, 200, result)
        self.assertFalse(result['registered'])
        self.assertEqual((self.state / 'library.json').read_bytes(), before)
        self.assertTrue(result['books'][0]['available'])
        self.assertEqual(result['books'][0]['writing_status']['value'], 'completed')
        self.assertEqual(self.w._library_entry(source)['book_id'], entry['book_id'])

    def test_full_shelf_allows_same_identity_at_its_registered_path(self):
        server, source, entry, archive = self.prepared(full=True)
        source.rename(self.root / '保留原稿')
        status, result = self.restore(server, archive, source)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['registered'])
        self.assertEqual(len(result['books']), 30)
        row = next(book for book in result['books'] if book['key'] == entry['key'])
        self.assertTrue(row['available'])
        self.assertEqual(row['book_id'], entry['book_id'])
        self.assertEqual(row['writing_status']['value'], 'completed')

    def test_full_shelf_rejects_a_new_path_before_restoring(self):
        server, source, entry, archive = self.prepared(full=True)
        destination = self.root / '不应创建的副本'
        before = (self.state / 'library.json').read_bytes()
        with patch.object(library.base.story.workbench_archives, 'restore_archive',
                          side_effect=AssertionError('must reject a full shelf first')):
            status, result = self.restore(server, archive, destination)
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error'], 'invalid_input')
        self.assertFalse(destination.exists())
        self.assertEqual((self.state / 'library.json').read_bytes(), before)

    def test_archive_changed_after_inspection_cannot_replace_old_registration(self):
        server, source, entry, archive = self.prepared()
        other = self.book('预检后替换的备份作品')
        other_archive = self.root / '替换备份.zip'
        library.base.story.workbench_archives.create_archive(other, other_archive)
        other_id = self.w._library_entry(other)['book_id']
        source.rename(self.root / '保留原稿')
        before = (self.state / 'library.json').read_bytes()
        restore_archive = library.base.story.workbench_archives.restore_archive

        def replace_after_inspection(path, destination):
            shutil.copyfile(other_archive, path)
            return restore_archive(path, destination)

        with patch.object(library.base.story.workbench_archives, 'restore_archive',
                          side_effect=replace_after_inspection):
            status, result = self.restore(server, archive, source)
        self.assertEqual(status, 200, result)
        self.assertFalse(result['registered'])
        self.assertIn('原登记保持不变', result['warning'])
        self.assertEqual((self.state / 'library.json').read_bytes(), before)
        self.assertEqual(result['books'][0]['book_id'], entry['book_id'])
        self.assertEqual(self.w._library_entry(source)['book_id'], other_id)


if __name__ == '__main__':
    unittest.main()
