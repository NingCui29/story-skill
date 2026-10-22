"""Read-only, explicit subject declarations stay separate from book identity."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-classification-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.w = base.story.workbench
        self.root = self.book('作品')

    def book(self, name, kind='long'):
        root = self.area / name
        base.story.Book.create(root, '都市修仙悬疑书名不能作为题材依据', kind)
        return root

    def source(self, text, relative='创作约定.md', root=None):
        path = (root or self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def classify(self):
        return self.w._book_classification(self.root)

    def file_state(self, root):
        # Active directory pins are transient exclusive locks, not authored files.
        return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in root.rglob('*')
                if not path.name.startswith('.story-pin-') and path.is_file()}

    def test_explicit_short_labels_include_source_field_and_line(self):
        self.source('# 创作约定\n\n- **题材与读者期待**：都市悬疑＋修仙；读者期待逐步查明真相。\n')
        result = self.classify()
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['tags'], ['都市悬疑', '修仙'])
        self.assertEqual(result['sources'], [{
            'path': '创作约定.md', 'field': '题材与读者期待',
            'value': '都市悬疑＋修仙；读者期待逐步查明真相。', 'line': 3,
        }])

    def test_supported_explicit_fields_do_not_depend_on_filename_title(self):
        for field in ('题材与期待', '类型与读者期待', '题材方向', '故事题材', '作品类型'):
            with self.subTest(field=field):
                self.source(field + '：架空古言\n')
                result = self.classify()
                self.assertEqual(result['status'], 'recorded')
                self.assertEqual(result['tags'], ['架空古言'])
                self.assertEqual(result['sources'][0]['field'], field)

    def test_real_current_promise_and_boundary_fields_preserve_explicit_tags(self):
        examples = (
            ('类型与承诺', '当代隐秘恋爱、创作者行业与边界重建。读者期待关系推进。', ['当代隐秘恋爱', '创作者行业与边界重建']),
            ('类型与承诺', '架空古风世情、婚嫁身份被降格、女主独立经营。', ['架空古风世情', '婚嫁身份被降格', '女主独立经营']),
            ('类型与承诺', '当代女性现实婚姻；读者期待具体选择与回应。', ['当代女性现实婚姻']),
            ('类型与边界', '纯爽文、剑道、双强、HE。边界在人物选择中落实。', ['纯爽文', '剑道', '双强', 'HE']),
        )
        for field, value, tags in examples:
            with self.subTest(field=field, tags=tags):
                self.source('# 创作约定\n\n- ' + field + '：' + value + '\n')
                result = self.classify()
                self.assertEqual((result['status'], result['reason']), ('recorded', 'recorded'))
                self.assertEqual(result['tags'], tags)
                self.assertEqual(result['sources'], [{'path': '创作约定.md', 'line': 3, 'field': field, 'value': value}])

    def test_explicit_core_genre_in_known_mixed_heading_is_local_evidence(self):
        for heading in ('作品定位与平台分类', '作品题材及平台分类', '创作约定和平台分类'):
            with self.subTest(heading=heading):
                self.source('# 全书总纲\n## ' + heading + '\n- 核心类型：架空仙侠同人、规则悬疑、双强言情、HE。\n'
                            '- 类型：平台女频\n- 主分类：婚姻家庭\n- 题材：平台悬疑\n'
                            '### 番茄平台分类\n核心类型：现代都市\n', '01_大纲细纲/全书总纲.md')
                result = self.classify()
                self.assertEqual(result['tags'], ['架空仙侠同人', '规则悬疑', '双强言情', 'HE'])
                self.assertEqual(len(result['sources']), 1)
                self.assertEqual(result['sources'][0]['path'], '01_大纲细纲/全书总纲.md')
                self.assertEqual(result['sources'][0]['line'], 3)

    def test_mixed_heading_never_escapes_candidate_history_or_platform_sections(self):
        for heading in ('历史', '候选方案', '未采用的旧规划', '平台资料', '番茄作品类型', '参考示例'):
            with self.subTest(heading=heading):
                self.source('# ' + heading + '\n## 作品定位与平台分类\n核心类型：修仙\n')
                result = self.classify()
                self.assertEqual(result['tags'], [])
                self.assertEqual(result['sources'], [])
                self.assertEqual(result['status'], 'missing')
        self.source('# 作品定位与平台分类\n核心类型：修仙\n状态：草稿\n')
        self.assertEqual(self.classify()['reason'], 'unconfirmed')

    def test_missing_diagnostics_distinguish_absent_sources_and_no_declaration(self):
        result = self.classify()
        self.assertEqual(result['reason'], 'no_sources')
        self.assertEqual(result['checked_paths'], [])
        self.assertNotIn('待分类', result['note'])
        self.source('# 创作约定\n本书规则只说明人物行动。\n')
        result = self.classify()
        self.assertEqual(result['reason'], 'no_declaration')
        self.assertEqual(result['checked_paths'], ['创作约定.md'])
        self.assertEqual(result['sources'], [])

    def test_positioning_and_narrative_genre_hints_explain_unsupported_without_guessing(self):
        for value in ('定位：现实都市家庭喜剧、旅行享受、中奖后的开心爽文。',
                      '定位：古代背景／婚姻背叛／灾后生还／女性脱身／追悔不复合。',
                      '| 题材 | 都市悬疑 |', '1. 题材：都市悬疑', '## 题材\n都市悬疑'):
            with self.subTest(value=value):
                self.source('# 创作约定\n' + value + '\n')
                result = self.classify()
                expected = ('recorded', 'recorded') if value.startswith('定位：') else ('missing', 'unsupported_format')
                self.assertEqual((result['status'], result['reason']), expected)
                self.assertEqual(result['tags'], [])
                self.assertEqual(result['sources'], [])
                source = result['primary_record'] if value.startswith('定位：') else result['details'][0]
                self.assertEqual(source['path'], '创作约定.md')
                self.assertEqual(source['line'], 2)

    def test_explicit_genre_sentence_keeps_its_source_phrase_and_ignores_secondary_asides(self):
        for prefix in ('', '本书', '作品'):
            with self.subTest(prefix=prefix):
                value = '都市记忆觉醒、强者归来与逆袭，兼有国安旧案和感情线。'
                self.source('# 开书策划\n主角职业另见人物卡。' + prefix + '题材为' + value + '\n', '00_项目策划/开书策划.md')
                result = self.classify()
                self.assertEqual(result['status'], 'recorded')
                self.assertEqual(result['tags'], ['都市记忆觉醒', '强者归来与逆袭'])
                self.assertEqual(result['sources'], [{'path': '00_项目策划/开书策划.md', 'line': 2,
                                                     'field': prefix + '题材为', 'value': value}])

    def test_genre_sentence_does_not_read_questions_negation_or_non_current_sections(self):
        for declaration in ('是否题材为都市？', '题材拟为都市。', '不是题材为都市。',
                            '题材为都市吗？', '题材为都市还是悬疑。', '题材为都市，并非修仙。',
                            '备选方向，尚未采用。题材为都市悬疑。',
                            '以下只是参考示例。题材为都市悬疑。', '不是本书现行定位。题材为都市悬疑。'):
            with self.subTest(declaration=declaration):
                self.source(declaration + '\n')
                self.assertEqual(self.classify()['tags'], [])
        for prefix in ('# 候选\n', '# 历史\n', '# 平台分类\n', '> ', '    ', '```md\n'):
            with self.subTest(prefix=prefix):
                self.source(prefix + '题材为都市。\n')
                self.assertEqual(self.classify()['tags'], [])

    def test_current_adopted_status_is_not_negated_by_explicit_old_candidate_reference(self):
        status = ('v1.0写作基线，2026年9月13日由用户的全书完本授权采用。'
                  '原v0.2候选保留于历史版本；尚未写到的情节仍是计划，无有效正文时不记为已发生。')
        self.source('状态：' + status + '\n题材为都市记忆觉醒、强者归来与逆袭，兼有国安旧案和感情线。\n')
        self.assertEqual(self.classify()['tags'], ['都市记忆觉醒', '强者归来与逆袭'])
        for status in ('尚未采用。旧版已采用', '草稿；此前版本已采用', '已采用；当前题材尚未确认'):
            with self.subTest(status=status):
                self.source('状态：' + status + '\n题材：都市\n')
                self.assertEqual(self.classify()['reason'], 'unconfirmed')

    def test_pending_qualifier_after_semicolon_is_not_discarded_with_narrative_tail(self):
        for field, value in (('题材', '都市；尚未采用'), ('类型', '悬疑（建议，未确认）'),
                             ('核心类型', '都市（拟选）'), ('题材', '悬疑（建议）'),
                             ('题材为', '都市；当前仍是候选。')):
            with self.subTest(field=field, value=value):
                line = field + ('' if field == '题材为' else '：') + value
                self.source(line + '\n')
                result = self.classify()
                self.assertEqual(result['reason'], 'unconfirmed')
                self.assertEqual(result['tags'], [])
                self.assertEqual(result['details'][0]['field'], field)
                self.assertEqual(result['details'][0]['value'], value)
        self.source('# 作品定位与平台分类\n核心类型：都市（拟选）\n')
        self.assertEqual(self.classify()['reason'], 'unconfirmed')

    def test_old_candidate_reference_after_a_current_genre_does_not_negate_it(self):
        self.source('题材：都市；旧版候选修仙尚未采用。\n')
        result = self.classify()
        self.assertEqual(result['tags'], ['都市'])
        self.assertEqual(result['sources'][0]['value'], '都市；旧版候选修仙尚未采用。')
        self.source('题材：都市；已采用；不写支线。\n')
        self.assertEqual(self.classify()['tags'], ['都市'])
        self.source('题材：不是都市\n')
        self.assertEqual(self.classify()['tags'], [])

    def test_positioning_record_keeps_its_literal_value_without_tags(self):
        value = '现实都市家庭喜剧、旅行享受、中奖后的开心爽文。读者期待可见的回报。'
        self.source('定位：' + value + '\n')
        result = self.classify()
        self.assertEqual(result['display_kind'], 'positioning')
        self.assertEqual(result['primary_record']['field'], '定位')
        self.assertEqual(result['primary_record']['value'], value)
        self.assertEqual(result['tags'], [])

    def test_candidate_value_cannot_partly_become_a_confirmed_tag(self):
        for value in ('都市、候选', '修仙（未采用）', '待核对：悬疑', '都市＋草稿方向'):
            with self.subTest(value=value):
                self.source('题材：' + value + '\n')
                result = self.classify()
                self.assertEqual(result['reason'], 'unconfirmed')
                self.assertEqual(result['tags'], [])
                self.assertEqual(result['sources'], [])

    def test_read_failure_and_limits_have_specific_reasons_without_raw_errors(self):
        path = self.source('题材：都市\n')
        with patch.object(self.w, '_author_read', side_effect=PermissionError('PRIVATE manuscript details')):
            result = self.classify()
        self.assertEqual((result['status'], result['reason']), ('needs_review', 'read_error'))
        self.assertEqual(result['checked_paths'], ['创作约定.md'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        path.write_bytes(b'x' * (self.w.CLASSIFICATION_TEXT_LIMIT + 1))
        self.assertEqual(self.classify()['reason'], 'limit')
        self.source('题材：都市\n' * (self.w.CLASSIFICATION_FIELD_LIMIT + 1))
        self.assertEqual(self.classify()['reason'], 'limit')

    def test_mixed_local_core_and_current_declaration_conflict_is_reported(self):
        self.source('题材：都市悬疑\n')
        self.source('# 作品定位与平台分类\n核心类型：修仙\n', '01_大纲细纲/全书总纲.md')
        result = self.classify()
        self.assertEqual((result['status'], result['reason']), ('needs_review', 'conflict'))
        self.assertEqual(result['tags'], [])
        self.assertEqual(len(result['sources']), 2)
        self.assertTrue(any(item['reason'] == 'conflict' for item in result['details']))

    def test_explicit_local_female_genre_is_preserved_without_platform_inference(self):
        self.source('- 类型：女频现代言情\n')
        self.assertEqual(self.classify()['tags'], ['女频现代言情'])
        self.source('- 类型：女频\n- 题材：短故事\n')
        self.assertEqual(self.classify()['status'], 'missing')

    def test_names_narrative_positioning_and_manuscript_width_do_not_become_tags(self):
        self.source('# 定位与标签\n定位：都市修仙悬疑\n阅读期待：现代都市里的解谜。\n'
                    '类型：短篇；单卷，八章。\n')
        book = base.story.Book(self.root)
        try:
            plan = {'title': '找到入口', 'volume_dir': '第一卷 雨夜', 'goal': '找到入口',
                    'stop': '门前停下', 'constraints': [], 'requires': [],
                    'tags': ['都市悬疑', '修仙'], 'length': [1000, 3000],
                    'length_exception': {'source': 'user_request', 'quote': '测试使用1000至3000字。'},
                    'beats': [{'choice': '选择查证', 'change': '获得线索'}]}
            book.save_plan(1, plan, book.meta('revision'))
        finally:
            book.close()
        result = self.classify()
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['display_kind'], 'positioning')
        self.assertEqual(result['tags'], [])
        self.assertEqual(result['sources'], [])

    def test_only_three_fixed_current_files_are_considered(self):
        for relative in ('99_历史/创作约定.md', '.story/drafts/创作约定.md',
                         '00_项目策划/候选开书策划.md', '题材.md'):
            self.source('题材：都市悬疑\n', relative)
        self.assertEqual(self.classify()['status'], 'missing')
        for relative in self.w.CLASSIFICATION_PATHS:
            self.source('题材：都市悬疑\n', relative)
        with patch.object(self.w, '_author_read', wraps=self.w._author_read) as reader:
            result = self.classify()
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(reader.call_count, 3)
        self.assertEqual({call.args[1] for call in reader.call_args_list}, set(self.w.CLASSIFICATION_PATHS))
        self.assertTrue(all(call.args[2] == 256 * 1024 for call in reader.call_args_list))

    def test_code_blocks_indentation_and_quotes_are_not_live_declarations(self):
        self.source('```md\n题材：都市\n状态：草稿\n```\n'
                    '~~~\n类型：修仙\n~~~\n    题材：玄幻\n\t类型：武侠\n'
                    '> 题材：历史\n  > 类型：奇幻\n\n题材方向：悬疑\n')
        result = self.classify()
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['tags'], ['悬疑'])

    def test_examples_history_candidates_and_platform_sections_are_skipped(self):
        sections = ('历史记录', '旧版规划', '候选方案', '草稿', '示例', '参考资料',
                    '备选', '平台分类', '番茄作品分类', '七猫作品类型')
        text = '\n'.join('# ' + title + '\n题材：修仙\n## 下级\n类型：都市' for title in sections)
        self.source(text + '\n# 当前创作约定\n题材：悬疑\n')
        self.assertEqual(self.classify()['tags'], ['悬疑'])

    def test_platform_inline_categories_do_not_become_subjects(self):
        self.source('作品类型：七猫女频短故事\n类型：番茄婚姻家庭\n'
                    '题材：平台入口\n主分类：女性成长\n')
        result = self.classify()
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['tags'], [])

    def test_unconfirmed_status_discards_fields_before_and_after_it(self):
        for status in ('候选', '草稿', '未采用', '待定', '待核对', '待确认', '未确定'):
            for status_first in (False, True):
                with self.subTest(status=status, status_first=status_first):
                    lines = ['题材：都市悬疑', '状态：' + status]
                    self.source('\n'.join(reversed(lines) if status_first else lines) + '\n')
                    result = self.classify()
                    self.assertEqual(result['status'], 'missing')
                    self.assertEqual(result['tags'], [])
                    self.assertEqual(result['sources'], [])

    def test_current_adopted_source_is_not_discarded_by_historical_status(self):
        self.source('题材：都市悬疑\n状态：已采用\n# 历史\n状态：草稿\n题材：修仙\n')
        self.assertEqual(self.classify()['tags'], ['都市悬疑'])

    def test_conflicting_explicit_declarations_require_review(self):
        self.source('题材：都市悬疑\n')
        self.source('题材：修仙\n', '01_大纲细纲/全书总纲.md')
        result = self.classify()
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['tags'], [])
        self.assertEqual(len(result['sources']), 2)
        self.assertIn('不一致', result['note'])

    def test_matching_declarations_in_different_order_keep_all_sources(self):
        self.source('题材：都市、悬疑\n')
        self.source('类型：悬疑＋都市\n', '00_项目策划/开书策划.md')
        result = self.classify()
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['tags'], ['都市', '悬疑'])
        self.assertEqual(len(result['sources']), 2)

    def test_symlink_source_is_not_followed_or_treated_as_confirmed(self):
        target = self.area / '外部题材.md'
        target.write_text('题材：修仙\n', encoding='utf-8')
        (self.root / '创作约定.md').symlink_to(target)
        result = self.classify()
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['tags'], [])
        self.assertEqual(result['sources'], [])
        self.assertIn('创作约定.md', result['note'])

    def test_read_failure_does_not_break_books_or_chapter_catalog(self):
        self.source('题材：都市悬疑\n')
        entry = self.w._library_entry(self.root)
        with patch.object(self.w, '_author_read', side_effect=PermissionError('test source inaccessible')):
            row = self.w._library_catalog({entry['key']: entry})['books'][0]
            workspace = self.w._workspace_index(self.root, [])
        self.assertTrue(row['available'])
        self.assertEqual(row['classification']['status'], 'needs_review')
        self.assertEqual(workspace['classification']['status'], 'needs_review')
        self.assertEqual(workspace['groups'], [])
        self.assertEqual(workspace['book_id'], entry['book_id'])

    def test_invalid_utf8_and_oversized_source_require_review(self):
        path = self.source('题材：都市\n')
        for raw in (b'\xff\xfe', b'x' * (256 * 1024 + 1)):
            with self.subTest(size=len(raw)):
                path.write_bytes(raw)
                result = self.classify()
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(result['tags'], [])

    def test_field_tag_and_value_caps_require_review(self):
        for text in ('题材：都市\n' * 13,
                     '题材：' + '、'.join('标签' + str(n) for n in range(13)) + '\n',
                     '题材：' + '甲' * 1001 + '\n'):
            with self.subTest(text=text[:30]):
                self.source(text)
                result = self.classify()
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(result['tags'], [])

    def test_missing_book_classification_is_unavailable(self):
        entry = self.w._library_entry(self.root)
        self.root.rename(self.area / '已移走')
        row = self.w._library_catalog({entry['key']: entry})['books'][0]
        self.assertFalse(row['available'])
        self.assertEqual(row['classification']['status'], 'unavailable')
        self.assertEqual(row['classification']['tags'], [])

    def test_identity_guard_does_not_read_classification_sources(self):
        entry = self.w._library_entry(self.root)
        with patch.object(self.w, '_book_classification', side_effect=AssertionError('must not scan sources')):
            self.assertEqual(self.w._library_entry(self.root, entry)['book_id'], entry['book_id'])

    def test_initial_library_and_workspace_include_classification_without_writes(self):
        self.source('题材：都市悬疑\n')
        before = self.file_state(self.root)
        entry = next(iter(self.w._library_roots(self.root).values()))
        catalog = self.w._editor_catalog(self.root)
        self.assertEqual(entry['classification']['tags'], ['都市悬疑'])
        self.assertEqual(catalog['workspace']['classification']['tags'], ['都市悬疑'])
        self.assertEqual(catalog['workspace']['book_id'], entry['book_id'])
        self.assertEqual(self.file_state(self.root), before)

    def test_legacy_registry_stays_identical_and_subjects_are_display_only(self):
        self.source('题材：都市悬疑\n')
        state = self.area / '书架'
        state.mkdir()
        entry = self.w._library_entry(self.root)
        legacy = {key: entry[key] for key in ('key', 'root', 'book_id', 'title')}
        raw = json.dumps({'version': 1, 'books': [legacy]}, ensure_ascii=False).encode('utf-8')
        path = state / 'library.json'
        path.write_bytes(raw)
        before = self.file_state(state), self.file_state(self.root)
        entries, digest = self.w._library_load(state)
        row = self.w._library_catalog(entries)['books'][0]
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(row['classification']['tags'], ['都市悬疑'])
        self.assertNotIn('classification', entries[legacy['key']])
        self.assertEqual((self.file_state(state), self.file_state(self.root)), before)

    def test_workspace_discards_replacement_book_sources_without_losing_groups(self):
        replacement = self.book('替换作品', 'short')
        self.source('题材：修仙\n', root=replacement)
        original_id = self.w._library_entry(self.root)['book_id']
        book = base.story.Book(self.root)
        try:
            plan = {'title': '原书章计划', 'volume_dir': '第一卷 雨夜', 'goal': '查证',
                    'stop': '发现入口', 'constraints': [], 'requires': [], 'tags': [],
                    'length': [1000, 3000],
                    'length_exception': {'source': 'user_request', 'quote': '测试使用1000至3000字。'},
                    'beats': [{'choice': '交出钥匙', 'change': '进入大门'}]}
            book.save_plan(1, plan, book.meta('revision'))
        finally:
            book.close()
        original_classifier = self.w._book_classification
        before = self.file_state(self.root), self.file_state(replacement)

        def replace_and_read(root):
            if os.name == 'nt':
                # The open database prevents replacing its directory on Windows.
                with self.assertRaises(PermissionError) as caught:
                    self.root.rename(self.area / '移走原书')
                self.assertEqual(caught.exception.winerror, 32)
                self.assertFalse((self.area / '移走原书').exists())
                self.assertEqual((self.file_state(self.root), self.file_state(replacement)), before)
                return original_classifier(root)
            self.root.rename(self.area / '移走原书')
            replacement.rename(self.root)
            return original_classifier(root)

        with patch.object(self.w, '_book_classification', side_effect=replace_and_read):
            workspace = self.w._workspace_index(self.root, [])
        self.assertEqual(workspace['book_id'], original_id)
        self.assertEqual(workspace['kind'], 'long')
        self.assertEqual(workspace['planned_total'], 1)
        self.assertEqual(workspace['groups'][0]['title'], '第1章 原书章计划')
        self.assertEqual(workspace['classification']['status'], 'missing' if os.name == 'nt' else 'unavailable')
        self.assertEqual(workspace['classification']['tags'], [])
        if os.name == 'nt':
            self.assertEqual(self.w._library_entry(self.root)['book_id'], original_id)
            self.assertEqual((self.file_state(self.root), self.file_state(replacement)), before)


if __name__ == '__main__':
    unittest.main()
