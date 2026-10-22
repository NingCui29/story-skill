"""Visible author records and whole-book status remain within their scopes."""
import json
from pathlib import Path
import tempfile
import unittest

import test_workbench as base
import test_workbench_shelf_feedback as shelf


class WorkbenchMetadataScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-metadata-scope-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / '合成作品'
        base.story.Book.create(self.root, '元数据范围验收', 'long')
        self.w = base.story.workbench

    def source(self, text, relative='创作约定.md'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def catalog(self):
        entry = self.w._library_entry(self.root)
        return self.w._library_catalog({entry['key']: entry})['books'][0]

    def test_local_section_completion_does_not_complete_the_book(self):
        for heading in ('第一卷 收束', '卷一 归家', '第十一章', '分卷进度',
                        '本卷进度', '章节状态', '番外', '序章', '场景一'):
            with self.subTest(heading=heading):
                text = '# 全书总纲\n## ' + heading + '\n### 当前有效状态（完本）\n状态：已完结。\n'
                path = self.source(text, '01_大纲细纲/全书总纲.md')
                self.assertEqual(self.catalog()['writing_status']['value'], 'unknown')
                self.assertEqual(path.read_text(encoding='utf-8'), text)
                self.source(text + '\n## 当前全书进度\n写作状态：连载中\n',
                            '01_大纲细纲/全书总纲.md')
                status = self.catalog()['writing_status']
                self.assertEqual(status['value'], 'serializing')
                self.assertEqual([row['line'] for row in status['sources']], [7])

    def test_local_subject_in_status_value_does_not_complete_the_book(self):
        for value in ('第一卷已完结，第二卷正在写作。', '本卷正文完结。',
                      '第十二章已完结。', '番外已完结，正文正在写作。',
                      '现行版第一卷已完本核验。'):
            with self.subTest(value=value):
                self.source('状态：' + value + '\n')
                status = self.catalog()['writing_status']
                self.assertEqual(status['value'], 'unknown')
                self.assertFalse(status['verified'])
                self.assertEqual(status['sources'], [])

    def test_local_status_and_local_draft_do_not_override_explicit_book_status(self):
        self.source('作品状态：连载中\n## 第一卷\n状态：已完结\n'
                    '## 第二卷\n状态：草稿\n')
        status = self.catalog()['writing_status']
        self.assertEqual(status['value'], 'serializing')
        self.assertEqual([row['line'] for row in status['sources']], [1])

    def test_explicit_whole_book_declarations_survive_local_context(self):
        cases = (
            ('## 第一卷\n全书状态：已完本\n', 'completed', 2),
            ('## 第一卷\n状态：全书已完本\n', 'completed', 2),
            ('## 第一卷\n状态：本书连载中\n', 'serializing', 2),
            ('状态：第一卷已完结，全书连载中。\n', 'serializing', 1),
            ('## 第一卷\n### 全书状态\n写作状态：连载中\n', 'serializing', 3),
            ('状态：现行版五卷已完本核验。\n', 'completed', 1),
            ('状态：全书已完本（含番外）。\n', 'completed', 1),
            ('## 第一卷\n状态：全书已完本（含番外）。\n', 'completed', 2),
        )
        for text, expected, line in cases:
            with self.subTest(text=text):
                self.source(text)
                status = self.catalog()['writing_status']
                self.assertEqual(status['value'], expected)
                self.assertTrue(status['verified'])
                self.assertEqual(status['sources'][0]['line'], line)
                self.assertEqual(status['sources'][0]['value'],
                                 text.splitlines()[line - 1].split('：', 1)[1])

    def test_explicit_whole_book_denial_still_conflicts_with_completion(self):
        self.source('作品状态：已完本\n## 第一卷\n状态：全书尚未完本\n')
        status = self.catalog()['writing_status']
        self.assertEqual(status['value'], 'unknown')
        self.assertFalse(status['verified'])
        self.assertEqual([row['line'] for row in status['sources']], [1, 3])
        self.source('全书状态：已完本\n状态：草稿\n')
        self.assertEqual(self.catalog()['writing_status']['value'], 'unknown')

    def test_comments_quotes_and_code_cannot_supply_genre_or_author_records(self):
        wrappers = (
            lambda value: '<!--\n' + value + '\n-->\n',
            lambda value: '> 引用其他作品\n' + value + '\n',
            lambda value: '> > 引用其他作品\n' + value + '\n',
            lambda value: '```md\n' + value + '\n```\n',
        )
        for wrap in wrappers:
            for value in ('题材：星际科幻', '定位：都市家庭喜剧。',
                          '内容关键词：古琴、非遗', '目标读者：成年女性'):
                with self.subTest(value=value, wrapped=wrap(value)):
                    text = '# 当前约定\n' + wrap(value) + '\n当前材料尚在整理。\n'
                    path = self.source(text)
                    result = self.catalog()['classification']
                    self.assertEqual(result['tags'], [])
                    self.assertEqual(result['records'], [])
                    self.assertEqual(result['status'], 'missing')
                    self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_hidden_status_and_headings_do_not_poison_visible_current_records(self):
        hidden = ('<!--\n# 历史记录\n题材：星际科幻\n定位：隐藏的定位\n状态：草稿\n-->\n'
                  '> 参考其他作品\n题材：玄幻修仙\n定位：引用中的定位\n\n')
        text = hidden + '题材：都市悬疑\n定位：街坊之间的互助故事。\n'
        self.source(text)
        result = self.catalog()['classification']
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['tags'], ['都市悬疑'])
        self.assertEqual([(row['kind'], row['line']) for row in result['records']],
                         [('genre', 11), ('positioning', 12)])
        self.assertEqual(result['sources'][0]['line'], 11)
        self.assertFalse(result['details'])

    def test_hidden_platform_proposals_do_not_create_current_records(self):
        self.source('<!--\n## 番茄分类建议\n主分类：都市\n本组值为拟选。\n-->\n'
                    '\n## 当前作品定位\n定位：街坊之间的互助故事。\n')
        result = self.catalog()['classification']
        self.assertEqual([row['kind'] for row in result['records']], ['positioning'])
        self.assertEqual(result['records'][0]['line'], 8)

    def test_actual_catalog_and_card_filters_exclude_local_completion_and_hidden_genre(self):
        self.source('# 全书总纲\n## 第一卷 收束\n状态：已完结。\n'
                    '## 第二卷\n本卷正在撰写第十一章。\n',
                    '01_大纲细纲/全书总纲.md')
        self.source('<!--\n题材：星际科幻\n定位：都市家庭喜剧。\n-->\n'
                    '> 参考作品\n题材：玄幻修仙\n\n当前资料尚在整理。\n')
        book = self.catalog()
        self.source('<!--\n题材：星际科幻\n-->\n\n题材：都市悬疑\n全书状态：连载中\n')
        current = self.catalog()
        runner = shelf.WorkbenchShelfFeedbackTests('runTest')
        runner.setUp()
        runner.js('const actual=' + json.dumps(book, ensure_ascii=False) + ';const current=' +
                  json.dumps(current, ensure_ascii=False) + ';' + r'''
render({books:[actual]});
assert.equal(allChildren($('books')).find(node=>node.className==='book-status').textContent,'状态待确认');
for(const term of ['星际科幻','都市家庭喜剧','玄幻修仙']){
 assert.ok(!$('books').textContent.includes(term));query(term);
 assert.equal($('books').children.length,0);query('');
}
filter('completed');assert.equal($('books').children.length,0);
filter('unknown');assert.equal($('books').children.length,1);
render({books:[current]});filter('serializing');query('都市悬疑');
assert.equal($('books').children.length,1);
assert.equal(allChildren($('books')).find(node=>node.className==='book-status').textContent,'连载中');
assert.ok($('books').textContent.includes('都市悬疑'));
''')


if __name__ == '__main__':
    unittest.main()
