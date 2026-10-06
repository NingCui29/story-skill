"""Explicit local writing status and identity-checked shelf-only author marks."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_workbench as base
import test_workbench_library as library


class WorkbenchWritingStatusTests(unittest.TestCase):
    start = library.WorkbenchLibraryTests.start
    serve = library.WorkbenchLibraryTests.serve
    stop = library.WorkbenchLibraryTests.stop
    request = library.WorkbenchLibraryTests.request
    post = library.WorkbenchLibraryTests.post
    add = library.WorkbenchLibraryTests.add

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-writing-status-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.state = self.area / '书架登记'
        self.w = base.story.workbench
        self.root = self.book('第一本书')

    def book(self, name, kind='long'):
        path = self.area / name
        base.story.Book.create(path, '同名作品', kind)
        return path

    def source(self, text, relative='创作约定.md', root=None):
        path = (root or self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def status(self, root=None, entry=None):
        root = root or self.root
        return self.w._book_writing_status(root, entry or self.w._library_entry(root))

    def file_state(self, root):
        return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                              path.stat().st_mtime_ns)
                for path in root.rglob('*') if path.is_file()}

    def seed(self, entry):
        self.state.mkdir(parents=True, exist_ok=True)
        raw = json.dumps({'version': 1, 'books': [entry]}, ensure_ascii=False).encode()
        (self.state / 'library.json').write_bytes(raw)
        return raw

    def test_empty_long_and_short_books_are_unknown_without_inference(self):
        for root in (self.root, self.book('短篇', 'short')):
            with self.subTest(root=root):
                result = self.status(root)
                self.assertEqual(result['value'], 'unknown')
                self.assertFalse(result['verified'])
                self.assertEqual(result['source'], 'unknown')
                self.assertTrue(result['note'])

    def test_explicit_local_status_fields_preserve_source_and_line(self):
        for declaration, value in (
                ('作品状态：连载中', 'serializing'), ('写作状态：正在连载', 'serializing'),
                ('当前状态：已完本。正文已经保存。', 'completed'),
                ('状态：已采用，正文完结（十章）；平台分类待核对。', 'completed'),
                ('状态：现行版五卷已完本核验。', 'completed'),
                ('状态：本轮采用的原创规划基线。', 'unknown'),
                ('状态：本轮采用的原创规划基线，正文均已提交，故事完结。', 'completed'),
                ('## 当前有效状态（完本）', 'completed')):
            with self.subTest(declaration=declaration):
                self.source('# 本书约定\n' + declaration + '\n')
                result = self.status()
                self.assertEqual(result['value'], value)
                if value != 'unknown':
                    self.assertTrue(result['verified'])
                    self.assertEqual(result['source'], 'recorded')
                    self.assertEqual(result['sources'][0]['path'], '创作约定.md')
                    self.assertEqual(result['sources'][0]['line'], 2)
                    self.assertIn('不代表平台状态', result['note'])

    def test_targets_width_platform_fields_and_negative_statements_do_not_complete(self):
        for text in ('完本目标：第十章完结\n', '篇幅：短篇，六章完结\n',
                     '作品阶段：本地完稿、拟投\n', '状态：计划完结\n',
                     '写作状态：尚未完本\n', '状态：并非已完本\n',
                     '状态：平台已完结\n', '状态：用户授权全书完本\n',
                     '状态：如果已完本则交付\n', '状态：若已完本则复核\n',
                     '状态：尚未宣布已完本\n', '状态：没有确认已完本\n',
                     '状态：历史版已完本\n', '状态：旧版已完本\n',
                     '状态：参考作品已完本\n', '状态：是否已完本\n',
                     '状态：准备连载\n', '八章正式正文全部完成，结局已收束。\n'):
            with self.subTest(text=text):
                self.source(text)
                self.assertEqual(self.status()['value'], 'unknown')

    def test_history_candidate_example_platform_and_code_regions_are_skipped(self):
        text = ('```md\n写作状态：已完本\n```\n> 作品状态：已完本\n'
                '    当前状态：已完本\n\t状态：已完本\n')
        for heading in ('历史版本', '候选方案', '草稿', '示例', '平台信息', '番茄作品阶段'):
            text += '# ' + heading + '\n作品状态：已完本\n'
        self.source(text + '# 当前作品\n写作状态：连载中\n')
        result = self.status()
        self.assertEqual(result['value'], 'serializing')
        self.assertEqual(len(result['sources']), 1)

    def test_unconfirmed_document_status_discards_preceding_fields(self):
        for status in ('候选方案', '草稿', '未采用', '待定', '待核对', '待确认', '未确定', '已完本（候选版本）'):
            for before in (True, False):
                with self.subTest(status=status, before=before):
                    lines = ['作品状态：已完本', '状态：' + status]
                    self.source('\n'.join(lines if before else reversed(lines)))
                    self.assertEqual(self.status()['value'], 'unknown')

    def test_conflicting_current_statuses_are_unknown_with_both_sources(self):
        self.source('作品状态：连载中\n')
        self.source('状态：已完本\n', '01_大纲细纲/全书总纲.md')
        result = self.status()
        self.assertEqual(result['value'], 'unknown')
        self.assertFalse(result['verified'])
        self.assertEqual(len(result['sources']), 2)
        self.assertIn('不一致', result['note'])

    def test_comments_and_lazy_quotes_cannot_declare_writing_status(self):
        for hidden in ('<!--\n作品状态：已完本\n-->\n',
                       '> 旧状态示例\n作品状态：已完本\n',
                       '> > 旧状态示例\n作品状态：已完本\n'):
            with self.subTest(hidden=hidden):
                self.source(hidden)
                self.assertEqual(self.status()['value'], 'unknown')
                text = hidden + '\n作品状态：连载中\n'
                self.source(text)
                result = self.status()
                self.assertEqual(result['value'], 'serializing')
                self.assertEqual(len(result['sources']), 1)
                self.assertEqual(result['sources'][0]['line'], len(text.splitlines()))

    def test_planned_status_sections_do_not_override_current_outline_status(self):
        relative = '01_大纲细纲/全书总纲.md'
        for heading in ('完本计划', '目标状态'):
            with self.subTest(heading=heading):
                planned = '# 全书总纲\n## ' + heading + '\n作品状态：已完本\n'
                self.source(planned, relative)
                self.assertEqual(self.status()['value'], 'unknown')
                self.source(planned + '### 交付时状态\n状态：已完本\n'
                            '## 当前作品\n作品状态：连载中\n', relative)
                result = self.status()
                self.assertEqual(result['value'], 'serializing')
                self.assertEqual([source['line'] for source in result['sources']], [7])
                self.source('# 全书总纲\n作品状态：已完本\n' + planned, relative)
                self.assertEqual(self.status()['value'], 'completed')

    def test_negative_current_completion_is_preserved_for_same_or_other_source_conflicts(self):
        for negative in ('尚未完本', '未完本', '正文尚未完结'):
            for separate in (False, True):
                with self.subTest(negative=negative, separate=separate):
                    self.source('作品状态：已完本\n')
                    self.source('', '01_大纲细纲/全书总纲.md')
                    if separate:
                        self.source('当前状态：' + negative + '\n', '01_大纲细纲/全书总纲.md')
                    else:
                        self.source('作品状态：已完本\n当前状态：' + negative + '\n')
                    result = self.status()
                    self.assertEqual(result['value'], 'unknown')
                    self.assertFalse(result['verified'])
                    self.assertEqual(len(result['sources']), 2)
                    self.assertIn(negative, [source['value'] for source in result['sources']])
                    self.assertIn('不一致', result['note'])

    def test_unfinished_record_does_not_invent_serialization_or_conflict_with_it(self):
        self.source('当前状态：尚未完本\n')
        result = self.status()
        self.assertEqual(result['value'], 'unknown')
        self.assertFalse(result['verified'])
        self.assertEqual(result['sources'][0]['value'], '尚未完本')
        self.source('写作状态：连载中\n', '01_大纲细纲/全书总纲.md')
        self.assertEqual(self.status()['value'], 'serializing')

    def test_negative_serialization_conflicts_only_with_serializing(self):
        for separate in (False, True):
            with self.subTest(separate=separate):
                self.source('作品状态：连载中\n')
                self.source('', '01_大纲细纲/全书总纲.md')
                if separate:
                    self.source('当前状态：尚未连载\n', '01_大纲细纲/全书总纲.md')
                else:
                    self.source('作品状态：连载中\n当前状态：尚未连载\n')
                result = self.status()
                self.assertEqual(result['value'], 'unknown')
                self.assertFalse(result['verified'])
                self.assertEqual(len(result['sources']), 2)
                self.assertIn('不一致', result['note'])
        self.source('当前状态：尚未连载\n')
        self.source('', '01_大纲细纲/全书总纲.md')
        result = self.status()
        self.assertEqual(result['value'], 'unknown')
        self.assertFalse(result['verified'])
        self.assertEqual(result['sources'][0]['value'], '尚未连载')
        self.source('作品状态：已完本\n', '01_大纲细纲/全书总纲.md')
        self.assertEqual(self.status()['value'], 'completed')

    def test_explicit_denials_conflict_but_unannounced_or_unconfirmed_is_not_a_denial(self):
        for prefix in ('不是', '并非', '不算'):
            for positive, negative in (('已完本', '已完本'), ('已完本', '完结'),
                                       ('连载中', '连载中')):
                with self.subTest(prefix=prefix, negative=negative):
                    self.source('作品状态：' + positive + '\n当前状态：' + prefix + negative + '\n')
                    result = self.status()
                    self.assertEqual(result['value'], 'unknown')
                    self.assertFalse(result['verified'])
                    self.assertEqual(len(result['sources']), 2)
                    self.assertIn('不一致', result['note'])
        for qualified in ('尚未宣布已完本', '未确认已完本'):
            with self.subTest(qualified=qualified):
                self.source('作品状态：已完本\n当前状态：' + qualified + '\n')
                result = self.status()
                self.assertEqual(result['value'], 'completed')
                self.assertEqual(len(result['sources']), 1)

    def test_cover_clause_cannot_hide_a_following_candidate_status_qualification(self):
        for comma in ('，', ','):
            with self.subTest(comma=comma):
                self.source('作品状态：已完本；封面待确认' + comma + '该状态仅为候选记录。\n')
                result = self.status()
                self.assertEqual(result['value'], 'unknown')
                self.assertFalse(result['verified'])
                self.source('状态：已采用，正文完结；封面为候选稿' + comma + '平台分类待核对。\n')
                result = self.status()
                self.assertEqual(result['value'], 'completed')
                self.assertTrue(result['verified'])

    def test_candidate_qualification_after_first_clause_cannot_confirm_completion(self):
        for separator in ('；', '。'):
            for qualification in ('此为候选记录，尚未采用', '该状态待确认'):
                with self.subTest(separator=separator, qualification=qualification):
                    self.source('作品状态：已完本' + separator + qualification + '。\n')
                    result = self.status()
                    self.assertEqual(result['value'], 'unknown')
                    self.assertFalse(result['verified'])

    def test_completion_followed_by_cover_or_classification_tasks_remains_recorded(self):
        for tasks in ('平台分类待核对', '封面待确认', '封面为候选稿', '封面候选待确认；分类待确定',
                      '平台分类待核对；封面草稿未采用'):
            with self.subTest(tasks=tasks):
                self.source('状态：已采用，正文完结；' + tasks + '。\n')
                result = self.status()
                self.assertEqual(result['value'], 'completed')
                self.assertTrue(result['verified'])
                self.assertEqual(len(result['sources']), 1)

    def test_only_bounded_current_sources_are_read_and_identity_check_does_not_scan(self):
        for relative in self.w.CLASSIFICATION_PATHS:
            self.source('作品状态：已完本\n', relative)
        self.source('作品状态：连载中\n', '99_历史版本/创作约定.md')
        self.source('作品状态：连载中\n', 'chapters/完本.md')
        original = self.w._author_read
        with patch.object(self.w, '_author_read', wraps=original) as reader:
            entry = self.w._library_entry(self.root)
            self.assertEqual(reader.call_count, 0)
            result = self.status(entry=entry)
        self.assertEqual(result['value'], 'completed')
        self.assertEqual(reader.call_count, 3)
        self.assertTrue(all(call.args[2] == 256 * 1024 for call in reader.call_args_list))

    def test_unreadable_or_oversized_status_material_does_not_claim_verified(self):
        self.source('作品状态：已完本\n')
        for method in ('oversized', 'symlink'):
            with self.subTest(method=method):
                path = self.root / '01_大纲细纲/全书总纲.md'
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() or path.is_symlink():
                    path.unlink()
                if method == 'oversized':
                    path.write_bytes(b'a' * (256 * 1024 + 1))
                else:
                    outside = self.area / '外部.txt'
                    outside.write_text('作品状态：已完本\n', encoding='utf-8')
                    path.symlink_to(outside)
                result = self.status()
                self.assertEqual(result['value'], 'unknown')
                self.assertFalse(result['verified'])

    def test_ordinary_library_roots_exposes_status_without_writing_book(self):
        self.source('当前状态：已完本\n')
        before = self.file_state(self.root)
        row = next(iter(self.w._library_roots(self.root).values()))
        self.assertEqual(row['writing_status']['value'], 'completed')
        self.assertEqual(before, self.file_state(self.root))

    def test_old_registry_computed_cache_is_ignored_and_optional_mark_is_validated(self):
        entry = self.w._library_entry(self.root)
        entry.pop('kind')
        entry['writing_status'] = {'value': 'completed', 'verified': True}
        raw = self.seed(entry)
        entries, _ = self.w._library_load(self.state)
        row = self.w._library_catalog(entries)['books'][0]
        self.assertEqual(row['writing_status']['value'], 'unknown')
        self.assertEqual((self.state / 'library.json').read_bytes(), raw)
        entry['shelf_status'] = 'made-up'
        self.seed(entry)
        with self.assertRaises(base.story.StoryError) as caught:
            self.w._library_load(self.state)
        self.assertEqual(caught.exception.code, 'workbench_library_invalid')

    def test_manual_override_and_clear_restore_recorded_status_across_reload(self):
        self.source('作品状态：已完本\n')
        before = self.file_state(self.root)
        server = self.start(books=[self.root])
        key = self.post(server, 'books')[1]['books'][0]['key']
        for value in ('serializing', 'completed'):
            status, result = self.post(server, 'set-book-status', {'key': key, 'value': value})
            self.assertEqual(status, 200, result)
            row = result['books'][0]
            self.assertEqual(row['writing_status']['value'], value)
            self.assertEqual(row['writing_status']['source'], 'author_mark')
            entries, _ = self.w._library_load(self.state)
            self.assertEqual(entries[key]['shelf_status'], value)
            self.assertEqual(self.w._library_catalog(entries)['books'][0]['writing_status']['value'], value)
        status, result = self.post(server, 'set-book-status', {'key': key, 'value': 'unknown'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['books'][0]['writing_status']['value'], 'completed')
        self.assertEqual(result['books'][0]['writing_status']['source'], 'recorded')
        entries, _ = self.w._library_load(self.state)
        self.assertNotIn('shelf_status', entries[key])
        self.assertEqual(self.w._library_catalog(entries)['books'][0]['writing_status']['source'], 'recorded')
        saved = json.loads((self.state / 'library.json').read_bytes())
        self.assertEqual(saved['version'], 1)
        self.assertNotIn('writing_status', saved['books'][0])
        self.assertNotIn('progress', saved['books'][0])
        self.assertTrue(list((self.state / '.backups').rglob('library.json')))
        self.assertEqual(before, self.file_state(self.root))

    def test_readding_and_restart_with_same_book_preserve_manual_mark(self):
        server = self.start(books=[self.root])
        key = self.post(server, 'books')[1]['books'][0]['key']
        self.assertEqual(self.post(server, 'set-book-status', {'key': key, 'value': 'serializing'})[0], 200)
        self.assertEqual(self.add(server, self.root)['books'][0]['writing_status']['source'], 'author_mark')
        self.stop(server)
        restarted = self.start(books=[self.root])
        row = self.post(restarted, 'books')[1]['books'][0]
        self.assertEqual(row['writing_status']['value'], 'serializing')
        self.assertEqual(row['writing_status']['source'], 'author_mark')

    def test_same_titles_keep_independent_manual_status(self):
        second = self.book('第二本书', 'short')
        server = self.start(books=[self.root, second])
        rows = self.post(server, 'books')[1]['books']
        key = next(row['key'] for row in rows if row['root'] == str(second))
        status, result = self.post(server, 'set-book-status', {'key': key, 'value': 'completed'})
        self.assertEqual(status, 200, result)
        values = {row['root']: row['writing_status']['value'] for row in result['books']}
        self.assertEqual(values, {str(self.root): 'unknown', str(second): 'completed'})

    def test_analysis_not_applicable_and_cannot_be_marked_even_in_old_registry(self):
        analysis = self.book('分析书', 'analysis')
        entry = self.w._library_entry(analysis)
        entry.pop('kind')
        entry['shelf_status'] = 'completed'
        self.seed(entry)
        server = self.start()
        row = self.post(server, 'books')[1]['books'][0]
        self.assertEqual(row['writing_status']['value'], 'not_applicable')
        before = (self.state / 'library.json').read_bytes()
        for value in ('serializing', 'completed', 'unknown'):
            self.assertEqual(self.post(server, 'set-book-status', {'key': row['key'], 'value': value})[0], 409)
        self.assertEqual((self.state / 'library.json').read_bytes(), before)

    def test_unavailable_and_replaced_books_cannot_change_mark(self):
        server = self.start(books=[self.root])
        key = self.post(server, 'books')[1]['books'][0]['key']
        self.assertEqual(self.post(server, 'set-book-status', {'key': key, 'value': 'completed'})[0], 200)
        before = (self.state / 'library.json').read_bytes()
        self.root.rename(self.area / '原作品已移走')
        row = self.post(server, 'books')[1]['books'][0]
        self.assertFalse(row['available'])
        self.assertEqual(row['writing_status']['value'], 'unknown')
        self.assertFalse(row['writing_status']['verified'])
        self.assertEqual(self.post(server, 'set-book-status', {'key': key, 'value': 'serializing'})[0], 409)
        base.story.Book.create(self.root, '同名作品', 'long')
        replacement = self.file_state(self.root)
        status, result = self.post(server, 'set-book-status', {'key': key, 'value': 'unknown'})
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error'], 'workbench_book_changed')
        self.assertEqual((self.state / 'library.json').read_bytes(), before)
        self.assertEqual(self.file_state(self.root), replacement)

    def test_authentication_invalid_value_and_unknown_key_do_not_write(self):
        server = self.start(books=[self.root])
        key = self.post(server, 'books')[1]['books'][0]['key']
        before = (self.state / 'library.json').read_bytes()
        for headers in ({'X-Story-Token': ''}, {'Origin': 'https://evil.example'}, {'Host': 'evil.example'}):
            self.assertEqual(self.post(server, 'set-book-status', {'key': key, 'value': 'completed'}, headers)[0], 403)
        for payload in ({'key': 'unknown', 'value': 'completed'}, {'key': key, 'value': 'published'},
                        {'key': key, 'value': None}, {'key': key, 'value': {}}, {'value': 'completed'}):
            self.assertEqual(self.post(server, 'set-book-status', payload)[0], 409)
        self.assertEqual((self.state / 'library.json').read_bytes(), before)

    def test_registry_cas_rejects_external_changes(self):
        server = self.start(books=[self.root])
        key = self.post(server, 'books')[1]['books'][0]['key']
        path = self.state / 'library.json'
        changed = path.read_bytes() + b'\n'
        path.write_bytes(changed)
        status, result = self.post(server, 'set-book-status', {'key': key, 'value': 'completed'})
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error'], 'workbench_library_changed')
        self.assertEqual(path.read_bytes(), changed)

    def test_identity_is_rechecked_before_registry_write(self):
        entry = self.w._library_entry(self.root)
        self.seed(entry)
        entries, digest = self.w._library_load(self.state)
        before = (self.state / 'library.json').read_bytes()
        original, calls = self.w._library_entry, []

        def replace_at_second_check(root, expected=None):
            calls.append(root)
            if len(calls) == 2:
                self.root.rename(self.area / '原书')
                base.story.Book.create(self.root, '同名作品', 'long')
            return original(root, expected)

        with patch.object(self.w, '_library_entry', side_effect=replace_at_second_check):
            with self.assertRaises(base.story.StoryError) as caught:
                self.w._set_shelf_status(self.state, entries, entry['key'], 'completed', digest)
        self.assertEqual(caught.exception.code, 'workbench_book_changed')
        self.assertEqual((self.state / 'library.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
