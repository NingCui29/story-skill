"""Shelf progress describes only checked, local formal chapter registrations."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchShelfProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-shelf-progress-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.w = base.story.workbench
        self.root = self.book('作品')

    def book(self, name, kind='long'):
        root = self.area / name
        base.story.Book.create(root, '同名作品', kind)
        return root

    def entry(self):
        return self.w._library_entry(self.root)

    def adopt(self, chapter=1):
        draft = self.area / ('独立导入稿-' + str(chapter) + '.txt')
        draft.write_text('第' + str(chapter) + '章 门前\n她留下钥匙，决定自己去查证。\n', encoding='utf-8')
        book = base.story.Book(self.root)
        try:
            result = book.adopt(chapter, draft, '留下钥匙后独自查证', book.meta('revision'),
                                volume_dir='第一卷 雨夜')
            self.assertTrue(result['exports_complete'], result)
        finally:
            book.close()

    def edit_meta(self, value):
        book = base.story.Book(self.root)
        try:
            with book.transaction(book.meta('revision')):
                book.set_meta('last_chapter', value)
        finally:
            book.close()

    def mutate_fixture(self, sql, parameters=()):
        book = base.story.Book(self.root)
        try:
            with book.transaction(book.meta('revision')):
                book.db.execute(sql, parameters)
        finally:
            book.close()

    def assert_progress(self, row, count, last, next_chapter):
        progress = row['progress']
        self.assertIs(progress['verified'], True)
        self.assertEqual(progress['formal_chapters'], count)
        self.assertEqual(progress['last_chapter'], last)
        self.assertEqual(progress['next_chapter'], next_chapter)
        self.assertNotIn('completed', progress)
        self.assertNotIn('published', progress)
        return progress

    def assert_unknown(self, row):
        progress = row['progress']
        self.assertIs(progress['verified'], False)
        self.assertTrue(isinstance(progress.get('note'), str) and progress['note'].strip())
        # A missing or inconsistent ledger must not impersonate an empty book
        # or a verified number of formal chapters.
        self.assertIsNone(progress.get('formal_chapters'))
        self.assertIsNone(progress.get('last_chapter'))
        self.assertIsNone(progress.get('next_chapter'))

    def file_state(self, root):
        return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                              path.stat().st_mtime_ns)
                for path in root.rglob('*') if path.is_file()}

    def test_empty_book_has_zero_formal_chapters_and_next_chapter_one(self):
        self.assert_progress(self.entry(), 0, 0, 1)

    def test_one_registered_formal_chapter_reports_local_registration(self):
        self.adopt()
        progress = self.assert_progress(self.entry(), 1, 1, 2)
        self.assertNotIn('candidate_chapters', progress)

    def test_sparse_import_uses_registered_count_and_max_number_separately(self):
        self.adopt(8)
        self.assert_progress(self.entry(), 1, 8, 9)

    def test_plans_candidates_recovery_and_unregistered_files_do_not_count_as_formal(self):
        book = base.story.Book(self.root)
        try:
            plan = {'title': '留下钥匙', 'volume_dir': '第一卷 雨夜', 'goal': '查证',
                    'stop': '发现入口', 'constraints': [], 'requires': [], 'tags': [],
                    'length': list(base.story.DEFAULT_CHAPTER_LENGTH),
                    'beats': [{'choice': '交出钥匙', 'change': '进入大门'}]}
            book.save_plan(1, plan, book.meta('revision'))
        finally:
            book.close()
        for relative in ('.story/drafts/workbench/第1章_候选.txt',
                         '.story/drafts/workbench/自动恢复/第1章_恢复.txt',
                         'chapters/0002.md', '01_大纲细纲/第3章_候选细纲.md'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('候选材料；未采用。', encoding='utf-8')
        self.assert_progress(self.entry(), 0, 0, 1)

    def test_last_chapter_above_or_below_formal_registration_is_unverified(self):
        self.adopt()
        for value in (0, 2):
            with self.subTest(last_chapter=value):
                self.edit_meta(value)
                self.assert_unknown(self.entry())

    def test_nonempty_last_chapter_without_any_registration_is_unverified(self):
        self.edit_meta(5)
        self.assert_unknown(self.entry())

    def test_last_chapter_requires_a_nonnegative_integer_not_boolean_or_text(self):
        for value in (True, False, -1, '0', None, 1.5):
            with self.subTest(last_chapter=value):
                self.edit_meta(value)
                self.assert_unknown(self.entry())

    def test_missing_legacy_metadata_is_unknown_instead_of_default_zero(self):
        self.mutate_fixture("DELETE FROM meta WHERE key='last_chapter'")
        row = self.entry()
        self.assertTrue(row['kind_verified'])
        self.assert_unknown(row)

    def test_unreadable_legacy_chapter_ledger_does_not_block_book_identity(self):
        self.mutate_fixture('ALTER TABLE chapter_state RENAME TO old_chapter_state')
        row = self.entry()
        self.assertTrue(row['book_id'])
        self.assertTrue(row['kind_verified'])
        self.assert_unknown(row)

    def test_invalid_registered_chapter_number_is_unverified(self):
        self.adopt(8)
        self.mutate_fixture('INSERT INTO chapter_state(chapter,sha,summary,receipt,input_hash,imported) '
                            'SELECT 0,sha,summary,receipt,input_hash,imported FROM chapter_state WHERE chapter=8')
        self.assert_unknown(self.entry())

    def test_initial_shelf_and_live_catalog_propagate_actual_progress(self):
        self.adopt()
        library = self.w._library_roots(self.root)
        initial = next(iter(library.values()))
        self.assert_progress(initial, 1, 1, 2)
        row = self.w._library_catalog(library)['books'][0]
        self.assertTrue(row['available'])
        self.assert_progress(row, 1, 1, 2)

    def test_unavailable_book_does_not_keep_a_cached_verified_progress(self):
        self.adopt()
        entry = self.entry()
        self.root.rename(self.area / '作品已移走')
        row = self.w._library_catalog({entry['key']: entry})['books'][0]
        self.assertFalse(row['available'])
        self.assert_unknown(row)

    def test_legacy_registry_progress_cache_is_ignored_and_registry_unchanged(self):
        self.adopt()
        entry = self.entry()
        state = self.area / '书架'
        state.mkdir()
        legacy = {key: entry[key] for key in ('key', 'root', 'book_id', 'title')}
        legacy['progress'] = {'verified': True, 'formal_chapters': 999, 'last_chapter': 999,
                              'next_chapter': 1000, 'completed': True}
        raw = json.dumps({'version': 1, 'books': [legacy]}, ensure_ascii=False).encode('utf-8')
        path = state / 'library.json'
        path.write_bytes(raw)
        before = self.file_state(self.root), self.file_state(state)
        library, _ = self.w._library_load(state)
        row = self.w._library_catalog(library)['books'][0]
        self.assert_progress(row, 1, 1, 2)
        self.assertNotIn('progress', library[entry['key']])
        self.assertEqual((self.file_state(self.root), self.file_state(state)), before)

    def test_progress_read_does_not_read_prose_or_modify_book_files(self):
        self.adopt()
        before = self.file_state(self.root)
        with patch.object(self.w, '_author_read', side_effect=AssertionError('progress must not read prose')):
            self.assert_progress(self.entry(), 1, 1, 2)
        self.assertEqual(self.file_state(self.root), before)


if __name__ == '__main__':
    unittest.main()
