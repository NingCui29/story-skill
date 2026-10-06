"""Shelf update times come from book activity, never service or directory mtimes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchUpdateTimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-update-time-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.w = base.story.workbench
        self.root = self.book('作品')

    def book(self, name, kind='long'):
        root = self.area / name
        base.story.Book.create(root, '同名作品', kind)
        return root

    def at(self, value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()

    def file(self, relative, time='2021-02-03T04:05:06Z', root=None):
        path = (root or self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('测试作者文件\n', encoding='utf-8')
        stamp = self.at(time)
        os.utime(path, (stamp, stamp))
        return path

    def event(self, created='2020-01-02 03:04:05', kind='notes', root=None):
        book = base.story.Book(root or self.root)
        try:
            with book.transaction(book.meta('revision')):
                book.event(kind, {'test': True})
                seq = book.db.execute('SELECT max(seq) FROM events').fetchone()[0]
                book.db.execute('UPDATE events SET created=? WHERE seq=?', (created, seq))
        finally:
            book.close()
        return seq

    def update(self):
        return self.w._book_update(self.root, self.w._library_entry(self.root))

    def file_state(self, root):
        return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in root.rglob('*') if path.is_file()}

    def formal(self):
        draft = self.area / '导入稿.txt'
        draft.write_text('第1章 门前\n她留下钥匙，决定自己去查证。\n', encoding='utf-8')
        book = base.story.Book(self.root)
        try:
            result = book.adopt(1, draft, '留下钥匙后独自查证', book.meta('revision'),
                                volume_dir='第一卷 雨夜')
            self.assertTrue(result['exports_complete'], result)
            relative = book.chapter_path(1)
            with book.transaction(book.meta('revision')):
                book.db.execute("UPDATE events SET created='2020-01-02 03:04:05'")
        finally:
            book.close()
        path = self.root / relative
        stamp = self.at('2023-04-05T06:07:08Z')
        os.utime(path, (stamp, stamp))
        return path, relative

    def test_recorded_state_event_time_is_utc_and_has_source(self):
        seq = self.event()
        result = self.update()
        self.assertTrue(result['updated_at_verified'])
        self.assertEqual(result['updated_at'], '2020-01-02T03:04:05Z')
        self.assertEqual(result['updated_at_source'], {
            'kind': 'state_event', 'event': 'notes', 'seq': seq, 'at': result['updated_at'],
        })

    def test_latest_event_is_compared_as_time_instead_of_text_or_seq(self):
        self.event('2025-01-01T08:00:00+08:00', kind='plan')
        seq = self.event('2025-01-01 01:00:00', kind='notes')
        self.event('2024-12-31 23:59:59', kind='world_save')
        result = self.update()
        self.assertEqual(result['updated_at'], '2025-01-01T01:00:00Z')
        self.assertEqual(result['updated_at_source']['seq'], seq)

    def test_author_file_time_can_be_newer_than_state_and_does_not_read_body(self):
        self.event()
        self.file('01_大纲细纲/近期细纲.md')
        with patch.object(self.w, '_author_read', side_effect=AssertionError('must not read author text')):
            result = self.update()
        self.assertTrue(result['updated_at_verified'])
        self.assertEqual(result['updated_at'], '2021-02-03T04:05:06Z')
        self.assertEqual(result['updated_at_source']['kind'], 'author_file')
        self.assertEqual(result['updated_at_source']['path'], '01_大纲细纲/近期细纲.md')

    def test_newer_state_event_wins_over_older_author_file(self):
        self.event('2024-01-02 03:04:05', kind='plan')
        self.file('创作约定.md')
        self.assertEqual(self.update()['updated_at'], '2024-01-02T03:04:05Z')

    def test_registered_formal_chapter_time_is_included(self):
        _, relative = self.formal()
        result = self.update()
        self.assertTrue(result['updated_at_verified'])
        self.assertEqual(result['updated_at'], '2023-04-05T06:07:08Z')
        self.assertEqual(result['updated_at_source']['path'], relative)

    def test_unregistered_chapter_file_does_not_create_a_formal_update(self):
        self.event()
        self.file('chapters/9999.md', '2025-01-01T00:00:00Z')
        self.assertEqual(self.update()['updated_at'], '2020-01-02T03:04:05Z')

    def test_saved_candidate_counts_but_recovery_and_pending_do_not(self):
        self.event()
        self.file('.story/drafts/workbench/第1章_候选.txt', '2022-01-01T00:00:00Z')
        self.file('.story/drafts/workbench/自动恢复/第1章_恢复.txt', '2025-01-01T00:00:00Z')
        self.file('.story/drafts/workbench/第1章_候选.txt.pending.json', '2025-02-01T00:00:00Z')
        result = self.update()
        self.assertTrue(result['updated_at_verified'])
        self.assertEqual(result['updated_at'], '2022-01-01T00:00:00Z')
        self.assertEqual(result['updated_at_source']['path'], '.story/drafts/workbench/第1章_候选.txt')

    def test_history_archive_backup_and_exports_are_skipped_before_traversal(self):
        self.event()
        self.file('创作约定.md')
        skipped = ('99_历史版本', '04_作品归档', '05_备份', '06_导出', '07_exports')
        for folder in skipped:
            for number in range(20):
                self.file(folder + '/旧资料' + str(number) + '.md', '2025-01-01T00:00:00Z')
        original_iterdir = Path.iterdir

        def guarded_iterdir(path):
            if path.name in skipped:
                raise AssertionError('excluded directory must not be traversed')
            return original_iterdir(path)

        with patch.object(Path, 'iterdir', guarded_iterdir), patch.object(self.w, 'AUTHOR_LIMIT', 3):
            result = self.update()
        self.assertTrue(result['updated_at_verified'])
        self.assertEqual(result['updated_at'], '2021-02-03T04:05:06Z')

    def test_service_open_refresh_and_stop_do_not_change_update_time(self):
        self.event()
        self.file('创作约定.md')
        before = self.update()
        server = self.w.editor_server(self.root)
        try:
            self.assertEqual(self.update(), before)
            self.w._editor_catalog(self.root)
            self.assertEqual(self.update(), before)
        finally:
            server.server_close()
        self.assertEqual(self.update(), before)

    def test_normal_author_leaf_title_with_archive_words_still_counts(self):
        relative = '02_正文/第1章 备份证据.md'
        self.file(relative, '2024-06-01T00:00:00Z')
        result = self.update()
        self.assertTrue(result['updated_at_verified'])
        self.assertEqual(result['updated_at'], '2024-06-01T00:00:00Z')
        self.assertEqual(result['updated_at_source']['path'], relative)

    def test_directory_state_file_and_service_mtimes_are_not_fallbacks(self):
        self.file('.story/workbench-service.json', '2025-01-01T00:00:00Z')
        self.file('.story/workbench/index.html', '2025-01-01T00:00:00Z')
        stamp = self.at('2025-03-01T00:00:00Z')
        for path in (self.root, self.root / '.story/state.sqlite3'):
            os.utime(path, (stamp, stamp))
        result = self.update()
        self.assertIsNone(result['updated_at'])
        self.assertFalse(result['updated_at_verified'])
        self.assertIsNone(result['updated_at_source'])

    def test_registered_short_assembly_is_excluded(self):
        self.root = self.book('短篇', 'short')
        self.event()
        book = base.story.Book(self.root)
        try:
            assembly = book.short_assembly_path()
        finally:
            book.close()
        self.file(assembly, '2025-01-01T00:00:00Z')
        self.assertEqual(self.update()['updated_at'], '2020-01-02T03:04:05Z')

    def test_missing_directory_or_linked_formal_file_makes_time_unverified(self):
        for replacement in ('missing', 'directory', 'symlink'):
            with self.subTest(replacement=replacement):
                self.root = self.book('正式路径-' + replacement)
                path, _ = self.formal()
                path.unlink()
                if replacement == 'directory':
                    path.mkdir()
                elif replacement == 'symlink':
                    target = self.file('外部文件.md', root=self.area)
                    path.symlink_to(target)
                result = self.update()
                self.assertFalse(result['updated_at_verified'])

    def test_latest_bad_event_time_is_not_silently_ignored(self):
        self.event()
        self.event('not a time')
        result = self.update()
        self.assertEqual(result['updated_at'], '2020-01-02T03:04:05Z')
        self.assertFalse(result['updated_at_verified'])
        self.assertIn('无法核对', result['updated_at_note'])

    def test_all_bad_event_times_are_unknown(self):
        self.event('not a time')
        result = self.update()
        self.assertIsNone(result['updated_at'])
        self.assertFalse(result['updated_at_verified'])

    def test_unreadable_or_over_limit_author_scan_does_not_break_shelf(self):
        self.event()
        entry = self.w._library_entry(self.root)
        with patch.object(self.w, '_author_files', side_effect=PermissionError('test inaccessible')):
            row = self.w._library_catalog({entry['key']: entry})['books'][0]
        self.assertTrue(row['available'])
        self.assertIsNone(row['updated_at'])
        self.assertFalse(row['updated_at_verified'])
        self.file('01_大纲细纲/细纲.md')
        with patch.object(self.w, 'AUTHOR_LIMIT', 1):
            row = self.w._library_catalog({entry['key']: entry})['books'][0]
        self.assertTrue(row['available'])
        self.assertFalse(row['updated_at_verified'])

    def test_identity_change_during_scan_does_not_attach_another_books_time(self):
        self.event()
        entry = self.w._library_entry(self.root)
        replacement = self.book('另一本书')
        self.file('创作约定.md', '2025-01-01T00:00:00Z', root=replacement)
        original_scanner = self.w._author_files

        def replace_and_scan(root, warnings=None, exclude=None):
            self.root.rename(self.area / '原书已移走')
            replacement.rename(self.root)
            return original_scanner(root, warnings, exclude)

        with patch.object(self.w, '_author_files', side_effect=replace_and_scan):
            result = self.w._book_update(self.root, entry)
        self.assertIsNone(result['updated_at'])
        self.assertFalse(result['updated_at_verified'])

    def test_unavailable_book_drops_cached_verified_time(self):
        self.event()
        entry = next(iter(self.w._library_roots(self.root).values()))
        self.assertTrue(entry['updated_at_verified'])
        self.root.rename(self.area / '已移走')
        row = self.w._library_catalog({entry['key']: entry})['books'][0]
        self.assertFalse(row['available'])
        self.assertIsNone(row['updated_at'])
        self.assertFalse(row['updated_at_verified'])

    def test_book_identity_guard_does_not_scan_update_times(self):
        entry = self.w._library_entry(self.root)
        with patch.object(self.w, '_book_update', side_effect=AssertionError('identity guard must not scan')):
            self.w._library_entry(self.root, entry)

    def test_time_metadata_is_read_only_and_legacy_registry_is_not_changed(self):
        self.event()
        self.file('创作约定.md')
        entry = self.w._library_entry(self.root)
        state = self.area / '书架'
        state.mkdir()
        legacy = {key: entry[key] for key in ('key', 'root', 'book_id', 'title')}
        (state / 'library.json').write_text(json.dumps({'version': 1, 'books': [legacy]}, ensure_ascii=False),
                                          encoding='utf-8')
        before = self.file_state(state), self.file_state(self.root)
        entries, _ = self.w._library_load(state)
        row = self.w._library_catalog(entries)['books'][0]
        initial = next(iter(self.w._library_roots(self.root).values()))
        self.assertTrue(row['updated_at_verified'])
        self.assertEqual(initial['updated_at'], row['updated_at'])
        self.assertNotIn('updated_at', entries[entry['key']])
        self.assertEqual((self.file_state(state), self.file_state(self.root)), before)


if __name__ == '__main__':
    unittest.main()
