"""Author statements remain displayable without invented genre/platform claims."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workbench_classification as fixtures


class WorkbenchClassificationRecordTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkbenchClassificationTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = self.fixture.w

    def classify(self, text, relative='创作约定.md'):
        self.fixture.source(text, relative)
        return self.fixture.classify()

    def test_genre_tags_keep_priority_and_other_records_do_not_add_tags(self):
        result = self.classify('题材：都市悬疑\n定位：家庭里的具体选择。\n'
                               '## 番茄作品分类（拟选）\n主分类：婚姻家庭\n')
        self.assertEqual(result['tags'], ['都市悬疑'])
        self.assertEqual(result['display_kind'], 'genre')
        self.assertEqual(result['primary_record']['kind'], 'genre')
        self.assertTrue(any(item['kind'] == 'positioning' for item in result['records']))
        self.assertTrue(any(item['kind'] == 'platform_suggestion' for item in result['records']))

    def test_positioning_is_an_original_quote_not_genre_tags(self):
        value = '现实都市家庭喜剧、旅行享受、中奖后的开心爽文。读者看到具体乐趣。'
        result = self.classify('# 创作约定\n- 定位：' + value + '\n')
        record = result['primary_record']
        self.assertEqual((result['status'], result['display_kind'], result['tags']), ('recorded', 'positioning', []))
        self.assertEqual((record['path'], record['line'], record['field']), ('创作约定.md', 2, '定位'))
        self.assertEqual(record['value'], value)
        self.assertEqual(record['display_text'], '现实都市家庭喜剧、旅行享受、中奖后的开心爽文。')
        self.assertEqual(record['backend_status'], 'not_claimed')

    def test_explicit_non_platform_content_keywords_are_not_discarded(self):
        value = '彩票中奖、末班车、夜班群像、事业经营、母女关系。'
        result = self.classify('## 作品标签\n投稿参考：番茄作品分类（拟选）。\n主分类：女性成长\n'
                               '内容关键词（非平台分类）：' + value + '\n')
        self.assertEqual(result['display_kind'], 'content_keywords')
        self.assertEqual(result['tags'], [])
        self.assertEqual(result['primary_record']['value'], value)
        self.assertEqual(result['primary_record']['field'], '内容关键词（非平台分类）')

    def test_audience_with_platform_brand_remains_an_author_statement(self):
        value = '番茄短篇里看都市职场、系统流、打脸逆袭与轻喜剧的读者。'
        result = self.classify('- 目标读者：' + value + '\n')
        self.assertEqual(result['display_kind'], 'audience')
        self.assertEqual(result['primary_record']['display_text'], value)
        self.assertEqual(result['tags'], [])

    def test_current_positioning_paragraph_is_preserved_without_splitting_tags(self):
        value = '都市天医强者起步的男频长篇，单女主，逐步展开有限空间能力和大场面。'
        result = self.classify('状态：已采用的开书材料补充。\n## 定位、读者与持续阅读理由\n\n' + value + '\n',
                               '00_项目策划/开书策划.md')
        self.assertEqual(result['display_kind'], 'positioning')
        self.assertEqual(result['primary_record']['value'], value)
        self.assertEqual(result['primary_record']['line'], 4)
        self.assertEqual(result['primary_record']['field'], '定位、读者与持续阅读理由')
        self.assertEqual(result['tags'], [])

    def test_one_sentence_story_is_synopsis_instead_of_genre(self):
        value = '她开了一间换货的小铺，亲手组织的生意却遇到了新的阻力。'
        result = self.classify('状态：已采用。\n## 一句话故事\n\n' + value + '\n', '00_项目策划/开书策划.md')
        self.assertEqual(result['display_kind'], 'synopsis')
        self.assertEqual(result['primary_record']['display_text'], value)
        self.assertEqual(result['primary_record']['field'], '一句话故事')
        self.assertEqual(result['tags'], [])

    def test_story_core_keeps_plot_and_creative_approach_in_separate_records(self):
        core = '她离开旧宅，替有具体牵挂的人找回能安睡的家。'
        method = '轻惊悚先提出“这间屋为什么不让人安稳”，人情与现实修缮给出答案。每个重要单元都有当事人的选择：留下、迁走或重新分配。'
        result = self.classify('状态：已采用。\n## 故事核心\n\n' + core + '\n\n' + method + '\n',
                               '00_项目策划/开书策划.md')
        self.assertEqual(result['display_kind'], 'positioning')
        self.assertEqual(result['primary_record']['display_text'], '轻惊悚先提出“这间屋为什么不让人安稳”，人情与现实修缮给出答案。')
        self.assertEqual(result['primary_record']['field'], '故事核心')
        self.assertEqual(result['primary_record']['line'], 6)
        self.assertTrue(any(item['kind'] == 'synopsis' and item['value'] == core for item in result['records']))

    def test_explicit_creative_description_inside_status_is_not_guessed_tags(self):
        sentence = '每章独立文件，单卷十章，第一人称当代现实题材。'
        result = self.classify('- 状态：本轮正式创作完整短篇；计划与正文均已获授权。' + sentence + '\n')
        self.assertEqual(result['display_kind'], 'positioning')
        self.assertEqual(result['primary_record']['display_text'], sentence)
        self.assertEqual(result['primary_record']['field'], '状态')
        self.assertEqual(result['tags'], [])

    def test_platform_proposals_keep_later_scope_qualification_and_never_become_genre(self):
        text = ('状态：已采用；正文已完成。\n## 作品标签\n'
                '投稿参考：番茄短篇作品分类。\n主分类：女性成长。\n'
                '以上是创作阶段的拟选项；投稿时以平台实际页面为准。\n')
        result = self.classify(text)
        self.assertEqual((result['status'], result['display_kind'], result['tags']), ('recorded', 'platform_suggestion', []))
        record = result['primary_record']
        self.assertEqual(record['platform'], 'fanqie')
        self.assertEqual(record['local_status'], 'proposed')
        self.assertEqual(record['backend_status'], 'unverified')
        self.assertEqual(record['display_text'], '女性成长。')
        self.assertEqual(record['scope_sources'][0]['line'], 5)
        self.assertEqual(record['scope_sources'][0]['path'], '创作约定.md')

    def test_platform_sibling_does_not_borrow_another_platforms_proposal(self):
        result = self.classify('状态：已采用。\n## 番茄作品分类（拟选）\n主分类：女性成长\n'
                               '## 七猫作品分类\n主分类：现实故事\n')
        records = [item for item in result['records'] if item['kind'] == 'platform_suggestion']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['platform'], 'fanqie')
        self.assertEqual(records[0]['value'], '女性成长')
        self.assertEqual(records[0]['scope_sources'][0]['line'], 2)

    def test_document_adoption_does_not_promote_proposal_rows(self):
        result = self.classify('状态：已采用。\n## 作品定位与平台分类\n'
                               '目标平台：番茄小说\n平台分类：逐栏拟选值如下。\n主分类：男频脑洞\n')
        self.assertTrue(result['records'])
        self.assertTrue(all(item['local_status'] == 'proposed' for item in result['records']))
        self.assertTrue(all(item['backend_status'] == 'unverified' for item in result['records']))
        self.assertEqual(result['tags'], [])

    def test_backend_unverified_status_does_not_negate_current_local_statement(self):
        result = self.classify('状态：已采用；平台分类尚未确认。\n定位：现实家庭喜剧。\n')
        self.assertEqual(result['display_kind'], 'positioning')
        self.assertEqual(result['primary_record']['local_status'], 'declared')
        self.assertEqual(result['primary_record']['display_text'], '现实家庭喜剧。')
        self.assertEqual(result['tags'], [])

    def test_candidate_document_and_historical_examples_do_not_supply_records(self):
        for text in ('状态：候选，尚未采用\n定位：都市家庭喜剧。\n',
                     '# 历史\n定位：都市家庭喜剧。\n', '# 参考\n定位：都市家庭喜剧。\n',
                     '# 示例\n## 一句话故事\n她开小铺。\n',
                     '```md\n定位：都市家庭喜剧。\n```\n', '> 定位：都市家庭喜剧。\n'):
            with self.subTest(text=text):
                result = self.classify(text)
                self.assertEqual(result['records'], [])
                self.assertEqual(result['status'], 'missing')

    def test_reference_advice_and_proposed_positioning_do_not_become_current_quotes(self):
        for text in ('状态：已采用。\n## 参考定位\n定位：都市家庭喜剧。\n',
                     '状态：已采用。\n## 建议定位\n定位：都市家庭喜剧。\n',
                     '定位：拟用都市家庭喜剧。\n'):
            with self.subTest(text=text):
                result = self.classify(text)
                self.assertEqual(result['records'], [])
                self.assertEqual(result['status'], 'missing')
        result = self.classify('## 番茄分类建议\n主分类：都市日常\n')
        self.assertEqual(result['display_kind'], 'platform_suggestion')
        self.assertEqual(result['primary_record']['local_status'], 'proposed')

    def test_denied_platform_scope_does_not_remain_an_active_proposal(self):
        for qualification in ('以上分类已否决，本组拟选仅作历史记录。',
                              '分类核对状态：本组已废弃，不再采用。'):
            with self.subTest(qualification=qualification):
                result = self.classify('状态：已采用。\n## 番茄作品分类（拟选）\n'
                                       '主分类：女性成长\n' + qualification + '\n')
                self.assertEqual(result['records'], [])
                self.assertEqual(result['status'], 'missing')

    def test_record_budget_is_visible_without_hiding_existing_genre(self):
        result = self.classify('题材：都市\n## 番茄作品分类（拟选）\n' + '主分类：都市日常\n' * 20)
        self.assertEqual(result['tags'], ['都市'])
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['display_kind'], 'genre')
        self.assertFalse(result['records_complete'])
        self.assertTrue(any(item['reason'] == 'record_limit' for item in result['details']))

    def test_large_valid_and_invalid_category_groups_complete_within_bounded_time(self):
        # The previous per-row group rebuild was quadratic: even 1000 rows
        # took over a second. Both 114 KiB cases must finish in one short child.
        source = r'''
import test_workbench as base
w=base.story.workbench
for heading,expected in [('## 番茄作品分类（拟选）\n',12),('## 作品分类\n',0)]:
 text=heading+'主分类：都市\n'*6000
 assert len(text.encode('utf-8'))<w.CLASSIFICATION_TEXT_LIMIT
 diagnostics=[];records=w._classification_records(text,diagnostics)
 assert len(records)==expected,(heading,len(records))
 assert bool(diagnostics)==bool(expected)
 if expected:assert diagnostics[0]['reason']=='record_limit'
'''
        result = subprocess.run([sys.executable, '-B', '-c', source],
                                cwd=Path(__file__).resolve().parent, capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_bounded_current_metadata_is_read(self):
        self.fixture.source('定位：都市家庭喜剧。\n')
        self.fixture.source('定位：不应读取的正文。\n', 'chapters/0001.md')
        self.fixture.source('定位：不应读取的候选。\n', '.story/drafts/创作约定.md')
        with patch.object(self.w, '_author_read', wraps=self.w._author_read) as reader:
            result = self.fixture.classify()
        self.assertEqual(result['primary_record']['value'], '都市家庭喜剧。')
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(reader.call_args.args[1:], ('创作约定.md', self.w.CLASSIFICATION_TEXT_LIMIT))
        self.assertEqual(self.w.CLASSIFICATION_RECORD_PATHS, self.w.CLASSIFICATION_PATHS)

    def test_empty_current_material_remains_missing(self):
        result = self.classify('状态：当前作品正在创作。\n正文不能代替作品定位元数据。\n')
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['records'], [])
        self.assertIsNone(result['primary_record'])
        self.assertIsNone(result['display_kind'])


if __name__ == '__main__':
    unittest.main()
