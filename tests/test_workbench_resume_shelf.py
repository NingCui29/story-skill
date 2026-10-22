"""The shelf advertises verified last-document positions without navigating by hash."""
import json
import unittest
from unittest.mock import patch

import test_workbench_resume as resume_fixture
import test_workbench_shelf_layout as shelf_fixture


class WorkbenchResumeShelfTests(unittest.TestCase):
    def setUp(self):
        self.fixture = resume_fixture.WorkbenchResumeTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w, self.root = self.fixture.w, self.fixture.root

    def catalog(self):
        entry = self.w._library_entry(self.root)
        return self.w._library_catalog({entry['key']: entry})['books'][0]

    def test_catalog_exposes_only_fresh_document_summary_not_capabilities_or_position(self):
        self.fixture.save()
        before = self.fixture.path.read_bytes(), self.fixture.meta(), self.fixture.source.read_bytes()
        row = self.catalog()
        summary = row['resume']
        self.assertTrue(row['available'])
        self.assertEqual(summary['status'], 'ready')
        self.assertEqual(summary['document'], {'title': '创作约定', 'kind': 'material', 'chapter': None})
        self.assertEqual(set(summary), {'status', 'document', 'updated_at', 'buffer_dirty', 'note'})
        for field in ('document_id', 'document_sha256', 'cursor', 'scroll', 'state_sha256', 'token', 'root'):
            self.assertNotIn(field, summary)
        self.assertIsNotNone(summary['updated_at'])
        self.assertEqual((self.fixture.path.read_bytes(), self.fixture.meta(), self.fixture.source.read_bytes()), before)

    def test_missing_changed_and_corrupt_records_do_not_make_book_unavailable(self):
        self.assertEqual(self.catalog()['resume']['status'], 'empty')
        self.fixture.save()
        self.fixture.source.write_text('新的现行内容。', encoding='utf-8')
        self.assertEqual(self.catalog()['resume']['status'], 'changed')
        self.fixture.source.unlink()
        row = self.catalog()
        self.assertTrue(row['available'])
        self.assertEqual(row['resume']['status'], 'missing')
        self.assertIsNone(row['resume']['document'])
        self.fixture.path.write_text('{invalid', encoding='utf-8')
        row = self.catalog()
        self.assertTrue(row['available'])
        self.assertEqual(row['resume']['status'], 'invalid')

    def test_identity_guard_precedes_resume_and_drops_any_cached_summary(self):
        entry = self.w._library_entry(self.root)
        entry['book_id'] = 'another-book'
        entry['resume'] = {'status': 'ready', 'document': {'title': '不得保留', 'kind': 'formal'}}
        with patch.object(self.w, '_editor_resume_summary') as reader:
            row = self.w._library_catalog({entry['key']: entry})['books'][0]
        reader.assert_not_called()
        self.assertFalse(row['available'])
        self.assertEqual(row['resume']['status'], 'unavailable')
        self.assertIsNone(row['resume']['document'])

    def test_current_document_summary_is_resolved_from_reader_instead_of_stored_title(self):
        self.fixture.save()
        original = self.w._editor_resume_document
        def resolved(root, record):
            document, summary = original(root, record)
            return document, {**summary, 'title': '当前文稿真实标题'}
        with patch.object(self.w, '_editor_resume_document', side_effect=resolved):
            summary = self.w._editor_resume_summary(self.root, self.fixture.book_id)
        self.assertEqual(summary['document']['title'], '当前文稿真实标题')
        raw = json.loads(self.fixture.path.read_text())
        raw['title'] = '伪造保存的标题'
        self.fixture.path.write_text(json.dumps(raw), encoding='utf-8')
        self.assertEqual(self.w._editor_resume_summary(self.root, self.fixture.book_id)['status'], 'invalid')

    def test_actual_saved_candidate_resume_title_matches_the_human_source_name(self):
        original = self.w._editor_document(self.root, 'file:创作约定.md')
        saved = self.w._editor_save(self.root, {**original, 'text': '这是已保存的新候选内容。'})
        document = self.w._editor_document(self.root, saved['id'])
        self.assertIn('_候选_', document['title'])
        self.fixture.save(self.fixture.payload(document_id=document['id'], document_sha256=document['sha256']))
        summary = self.catalog()['resume']['document']
        self.assertEqual(summary['title'], '创作约定')
        self.assertEqual(summary['kind'], 'candidate')

    def test_managed_recovery_and_repeated_suffixes_are_cleaned_but_normal_material_names_stay(self):
        cases = [
            ('.story/drafts/workbench/续写材料_候选_123456789abc_候选_abcdef123456_2.md', '续写材料'),
            ('.story/drafts/workbench/自动恢复/续写材料_' + 'a' * 32 + '.txt', '续写材料'),
            ('.story/drafts/workbench/自动恢复/续写材料_12345678-1234-1234-1234-123456789abc.txt', '续写材料'),
            ('00_项目策划/正常材料_候选_123456789abc.md', '正常材料_候选_123456789abc'),
            ('正常材料_' + 'a' * 32 + '.md', '正常材料_' + 'a' * 32),
        ]
        for relative, expected in cases:
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('这是隔离的文稿标题验收内容。', encoding='utf-8')
                document = self.w._editor_document(self.root, 'file:' + relative)
                state = self.fixture.read()
                self.fixture.save(self.fixture.payload(state['state_sha256'], document_id=document['id'],
                                                       document_sha256=document['sha256']))
                self.assertEqual(self.catalog()['resume']['document']['title'], expected)

    def js(self, case):
        fixture = shelf_fixture.WorkbenchShelfLayoutTests('runTest')
        fixture.setUp()
        fixture.js(r'''
const resume={status:'ready',document:{title:'第12章 新候选 <img onerror=bad>',kind:'candidate',chapter:12},
 updated_at:'2026-10-20T12:00:00Z',buffer_dirty:false,note:'只恢复文稿位置'};
const nodesText=node=>node.textContent+(node.children||[]).map(nodesText).join('');
''' + case)

    def test_ready_card_continues_with_last_document_caption_without_inventing_genre(self):
        self.js(r'''
render({books:[{...books[0],resume}]});const row=$('books').children[0],button=row.children.find(node=>node.tag==='button');
assert.equal(button.textContent,'继续编辑');assert.ok(button.attributes['aria-label'].includes('继续编辑'));
const caption=row.children.find(node=>node.className==='book-resume');assert.equal(row.dataset.hasResume,'true');
assert.equal(caption.textContent,'上次文稿：第12章 新候选 <img onerror=bad> · 候选稿');
assert.ok(!caption.children.some(node=>node.tag==='img'));assert.ok(caption.title.includes('只恢复文稿位置'));
const genres=row.children.find(node=>node.className==='classification');assert.ok(nodesText(genres).includes('都市'));assert.ok(nodesText(genres).includes('悬疑'));
await button.onclick();const link=$('message').children[0];assert.equal(link.href,'http://127.0.0.1:1234/confirmed-editor/');
assert.ok(!link.href.includes('#'));assert.deepEqual(rememberedShelfBooks,['long']);
''')

    def test_stale_or_unknown_position_keeps_normal_open_and_does_not_auto_navigate(self):
        self.js(r'''
for(const status of ['changed','missing','invalid','empty','unavailable']){
 render({books:[{...books[0],resume:{...resume,status}}]});const row=$('books').children[0];
 assert.equal(row.children.find(node=>node.tag==='button').textContent,'打开作品');
 assert.deepEqual(rememberedShelfBooks,[]);assert.equal($('message').children.length,0);
 const caption=row.children.find(node=>node.className==='book-resume');if(['empty','unavailable'].includes(status))assert.equal(caption,undefined);else assert.ok(caption.textContent.includes('从目录'));
}
render({books:[{...books[0],available:false,resume}]});assert.equal($('books').children[0].children.find(node=>node.tag==='button').disabled,true);
assert.equal(shelfResumeReady({...books[0],resume:{...resume,document:{title:'bad',kind:'unknown'}}}),false);
''')

    def test_dirty_position_only_reminds_recovery_and_keeps_plain_author_records(self):
        self.js(r'''
const value='现实都市家庭喜剧。',record={kind:'positioning',path:'创作约定.md',line:2,field:'定位',value,display_text:value};
render({books:[{...books[0],resume:{...resume,buffer_dirty:true},classification:{status:'recorded',tags:[],records:[record]}}]});
const row=$('books').children[0],caption=row.children.find(node=>node.className==='book-resume');assert.ok(caption.textContent.includes('有待核对输入'));
const genre=row.children.find(node=>node.className==='classification');assert.ok(nodesText(genre).includes(value));assert.ok(!nodesText(genre).includes('题材待核对'));
''')


if __name__ == '__main__':
    unittest.main()
