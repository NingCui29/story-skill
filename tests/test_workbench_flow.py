"""Chapter-centric author workflow: real state, candidate boundaries and UI utilities."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch
import test_workbench as base

story = base.story
w = story.workbench


class WorkbenchFlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.book = self.fixture.root, self.fixture.book

    def material(self, relative, text):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        # Fixtures compare exact bytes; do not let Windows translate LF to CRLF.
        p.write_bytes(text.encode('utf-8'))
        return 'file:' + relative

    def test_groups_include_future_plans_without_claiming_prose_or_adoption(self):
        plan = json.loads(self.book.db.execute('SELECT data FROM plans WHERE chapter=2').fetchone()[0])
        self.book.save_plan(3, {**plan, 'title': '第三夜'}, self.book.meta('revision'))
        path = '01_大纲细纲/第一卷 雨夜/第3章 未定.md'
        self.material(path, '# 候选细纲\n未采用')
        c = w._editor_catalog(self.root, limit=2)
        self.assertEqual(c['workspace']['formal_total'], 2)
        self.assertEqual(c['workspace']['planned_total'], 3)
        self.assertTrue(c['workspace']['has_more'])
        last = w._editor_catalog(self.root, offset=2, limit=2)['workspace']['groups'][0]
        self.assertEqual(last['chapter'], 3)
        self.assertEqual(last['status'], '已规划，未提交')
        self.assertIn('采用关系须另核对', last['items'][-1]['relation'])
        doc = w._editor_document(self.root, 'plan:3')
        self.assertFalse(doc['editable'])
        self.assertTrue(doc['render_markdown'])
        self.assertIn('### 变化节点', doc['text'])
        self.assertNotIn('### 场景', doc['text'])
        self.assertNotIn('formal:3', [r['id'] for r in last['items']])

    def test_context_uses_actual_receipt_and_prior_summary(self):
        before = self.fixture.authoritative_state()
        d = w._editor_document(self.root, 'formal:2')
        self.assertIn('第1章', d['context']['previous_summary'])
        self.assertIn('checks', d['context']['review'])
        self.assertEqual(d['context']['plan']['title'], '雨夜 & 账本#2%')
        catalog = w._editor_catalog(self.root, context_chapter=2)
        self.assertEqual(catalog['workspace']['context_snapshot']['context_sha256'], d['context']['context_sha256'])
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_same_manuscript_hash_can_have_a_new_matching_review(self):
        formal = w._editor_document(self.root, 'formal:2')
        text = formal['prefix'] + formal['text']
        draft = self.root / '.story/drafts/同稿复核.md'
        draft.write_bytes(text.encode('utf-8'))
        delta = {'book_id': self.book.meta('id'), 'base_revision': self.book.meta('revision'),
                 'summary': formal['summary'], 'changes': [],
                 'review': {'draft_sha256': story.digest(text), 'issues': [], 'checks': {
                     key: {'note': '同一正文再次核对钥匙交接。', 'quote': '沈禾把唯一的钥匙交给守门人。'}
                     for key in story.CHECKS}}}
        self.assertTrue(self.book.commit(2, draft, delta, replace_last=True)['exports_complete'])
        before = self.fixture.authoritative_state()
        fresh = w._editor_document(self.root, 'formal:2')
        self.assertEqual(fresh['sha256'], formal['sha256'])
        self.assertEqual(fresh['context']['review']['draft_sha256'], fresh['sha256'])
        self.assertIn('同一正文再次核对', fresh['context']['review']['checks']['causality']['note'])
        self.assertNotEqual(fresh['context']['context_sha256'], formal['context']['context_sha256'])
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_plan_document_keeps_the_context_used_to_render_its_text(self):
        original = self.book.get_plan(2)
        read_context = w._chapter_context

        def change_after_read(root, chapter):
            context = read_context(root, chapter)
            self.book.save_plan(2, {**original, 'goal': '保留钥匙，改走侧门'}, self.book.meta('revision'))
            return context

        with patch.object(w, '_chapter_context', side_effect=change_after_read) as read:
            doc = w._editor_document(self.root, 'plan:2')
        self.assertEqual(read.call_count, 1)
        self.assertIn(original['goal'], doc['text'])
        self.assertEqual(doc['context']['plan']['goal'], original['goal'])
        current = w._editor_catalog(self.root, context_chapter=2)['workspace']['context_snapshot']
        self.assertNotEqual(current['context_sha256'], doc['context']['context_sha256'])

    def test_formal_change_during_open_never_pairs_old_text_with_new_review(self):
        formal = w._editor_document(self.root, 'formal:2')
        text = formal['prefix'] + formal['text'] + '她改从侧门离开。\n'
        draft = self.root / '.story/drafts/读取期间修订.md'
        draft.write_bytes(text.encode('utf-8'))
        delta = {'book_id': self.book.meta('id'), 'base_revision': self.book.meta('revision'),
                 'summary': '沈禾交出钥匙，随后从侧门离开。', 'changes': [],
                 'review': {'draft_sha256': story.digest(text), 'issues': [], 'checks': {
                     key: {'note': '新版正式稿已核对侧门离开。', 'quote': '她改从侧门离开。'}
                     for key in story.CHECKS}}}
        read_context = w._chapter_context

        def replace_before_context(root, chapter):
            self.assertTrue(self.book.commit(2, draft, delta, replace_last=True)['exports_complete'])
            return read_context(root, chapter)

        with patch.object(w, '_chapter_context', side_effect=replace_before_context):
            self.fixture.assert_story_error('workbench_changed', w._editor_document, self.root, 'formal:2')
        fresh = w._editor_document(self.root, 'formal:2')
        self.assertIn('她改从侧门离开。', fresh['text'])
        self.assertEqual(fresh['sha256'], fresh['context']['review']['draft_sha256'])

    def test_context_refresh_marks_changed_data_without_replacing_cached_documents(self):
        formal = w._editor_document(self.root, 'formal:2')
        planned = w._editor_document(self.root, 'plan:2')
        original = self.book.get_plan(2)
        before = w._editor_catalog(self.root, context_chapter=2)
        context_hash = formal['context']['context_sha256']
        self.assertEqual(before['workspace']['context_snapshot']['context_sha256'], context_hash)

        unrelated = self.book.get_plan(1)
        self.book.save_plan(1, {**unrelated, 'goal': '另一章的规划调整'}, self.book.meta('revision'))
        unchanged = w._editor_catalog(self.root, context_chapter=2)
        self.assertGreater(unchanged['workspace']['revision'], before['workspace']['revision'])
        self.assertEqual(unchanged['workspace']['context_snapshot']['context_sha256'], context_hash)

        self.book.save_plan(2, {**original, 'goal': '保留钥匙，改走侧门', 'stop': '停在侧门前',
                               'constraints': ['钥匙不得交出']}, self.book.meta('revision'))
        # The current document need not belong to the filtered or paged directory.
        changed = w._editor_catalog(self.root, query='没有匹配文件', limit=1, context_chapter=2)
        self.assertEqual(changed['workspace']['groups'], [])
        self.assertNotEqual(changed['workspace']['context_snapshot']['context_sha256'], context_hash)
        self.assertEqual(w._editor_document(self.root, 'formal:2')['context']['plan']['goal'], '保留钥匙，改走侧门')

        self.book.save_plan(2, original, self.book.meta('revision'))
        restored = w._editor_catalog(self.root, context_chapter=2)
        self.assertEqual(restored['workspace']['context_snapshot']['context_sha256'], context_hash)

        text = formal['prefix'] + formal['text'] + '她改从侧门离开。\n'
        draft = self.root / '.story/drafts/辅助栏修订.md'
        draft.write_bytes(text.encode('utf-8'))
        delta = {'book_id': self.book.meta('id'), 'base_revision': self.book.meta('revision'),
                 'summary': '沈禾交出钥匙，随后从侧门离开。', 'changes': [],
                 'review': {'draft_sha256': story.digest(text), 'issues': [], 'checks': {
                     key: {'note': '新版正式稿已核对侧门离开。', 'quote': '她改从侧门离开。'}
                     for key in story.CHECKS}}}
        self.assertTrue(self.book.commit(2, draft, delta, replace_last=True)['exports_complete'])
        revised = w._editor_catalog(self.root, context_chapter=2)
        fresh = w._editor_document(self.root, 'formal:2')
        self.assertNotEqual(fresh['context']['formal_sha256'], formal['context']['formal_sha256'])

        script = self.script()
        source = script[script.index('function showChapterInfo'):script.index('function scheduleMetrics')]
        source += script[script.index('function drawCatalog'):script.index('// Paragraph LCS')]
        source += script[script.index('async function openDoc'):script.index('function pendingEdits')]
        source += script[script.index('async function catalog'):script.index('async function recover')]
        source = w._book_display_script() + script[script.index('let bookChoices='):script.index('function chapterNumber')] + source
        fixture = json.dumps({'formal': formal, 'planned': planned, 'fresh': fresh,
                              'before': before, 'unchanged': unchanged, 'changed': changed,
                              'restored': restored, 'revised': revised}, ensure_ascii=False)
        mock = r"""
class E {constructor(){this.children=[];this.dataset={};this.value='';this.parentElement={scrollTop:0};this.textContent='';}replaceChildren(){this.children=[];this.textContent='';}append(...x){this.children.push(...x);}setAttribute(k,v){this[k]=v;}querySelectorAll(){return [];}}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
const document={createElement:()=>new E()},docs=new Map();
let currentCatalog,viewMode='chapters',loading=false,reloadPending=false,pendingFocus=false,offset=0,active='formal:2',LIMIT=10,openSequence=0,openCalls=0,queryTimer;
const updateChapterPicker=()=>{},location={hash:''},chapterNumber=d=>d.context?.chapter||d.chapter,badge=()=>{},item=r=>r,note=e=>{if(e.includes('失败'))throw Error(e);};
const primaryChapterDocument=g=>g.items[0],locateActive=()=>{},closePanels=()=>{},displayTitle=d=>d.title,versionLabel=d=>d.kind;
const show=d=>{active=d.id;showChapterInfo(d);};
const shown=()=>{const read=e=>(e.textContent||'')+' '+(e.children||[]).map(read).join(' ');return read($('chapter-info'));};
let packet;
const call=async(action,payload)=>{if(action==='open'){openCalls++;return fixture.fresh;}assert.equal(payload.context_chapter,2);return packet;};
"""
        self.js('const fixture=' + fixture + ';\n' + mock + source, r"""
(async()=>{
 const d={...fixture.formal,value:fixture.formal.text+'未保存编辑',editing:true};
 const plan={...fixture.planned,value:fixture.planned.text};docs.set(d.id,d);docs.set(plan.id,plan);
 const originalContext=JSON.stringify(d.context),originalValue=d.value,originalReview=d.context.review;
 packet=fixture.before;await catalog();assert.ok(!shown().includes('辅助信息已过期'));
 packet=fixture.unchanged;await catalog();assert.ok(!shown().includes('辅助信息已过期'));
 packet=fixture.changed;await catalog();assert.ok(shown().includes('辅助信息已过期'));assert.ok(shown().includes('用钥匙换取入口'));
 assert.equal(d.value,originalValue);assert.equal(JSON.stringify(d.context),originalContext);assert.strictEqual(d.context.review,originalReview);
 await openDoc('plan:2');assert.ok(shown().includes('中栏计划和以下辅助信息仍为载入时版本'));assert.equal(plan.value,fixture.planned.text);
 viewMode='files';await openDoc('formal:2');assert.ok(shown().includes('辅助信息已过期'));assert.equal(openCalls,0);assert.equal(d.value,originalValue);
 packet=fixture.restored;await catalog();assert.ok(!shown().includes('辅助信息已过期'));
 packet=fixture.revised;await catalog();assert.ok(shown().includes('辅助信息已过期'));assert.ok(!shown().includes('新版正式稿已核对侧门离开'));assert.strictEqual(d.context.review,originalReview);
 // A late response from an older catalog must not mark a freshly loaded document stale.
 docs.set('formal:2',{...fixture.fresh,value:fixture.fresh.text});packet=fixture.before;await catalog();assert.ok(!shown().includes('辅助信息已过期'));
 packet=fixture.revised;await catalog();assert.ok(!shown().includes('辅助信息已过期'));assert.ok(shown().includes('新版正式稿已核对侧门离开'));
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_legacy_candidate_uses_recorded_chapter_in_navigation_and_filtered_picker(self):
        formal = w._editor_document(self.root, 'formal:1')
        old_path, legacy_path = formal['path'], 'chapters/0001.md'
        (self.root / old_path).rename(self.root / legacy_path)
        with self.book.transaction():
            self.book.set_meta('chapter_path:1', legacy_path)
            self.book.db.execute('UPDATE artifact_state SET path=? WHERE path=?', (legacy_path, old_path))
        formal = w._editor_document(self.root, 'formal:1')
        before = self.fixture.authoritative_state()
        saved = self.save_copy(formal, formal['text'] + '她停在门外。\n')
        self.assertTrue(Path(saved['path']).name.startswith('0001_候选_'))
        catalog = w._editor_catalog(self.root)
        group = next(g for g in catalog['workspace']['groups'] if g['chapter'] == 1)
        candidate = next(r for r in group['items'] if r['id'] == saved['id'])
        self.assertEqual(candidate['chapter'], 1)
        self.assertIn('仅用于导航', candidate['relation'])
        self.assertIn('采用关系待核对', w._editor_document(self.root, saved['id'])['status'])
        # A filtered-out group must not remove the candidate from the current
        # chapter picker; the full related-file list retains its recorded chapter.
        filtered = w._editor_catalog(self.root, query='不存在的目录筛选')
        self.assertEqual(filtered['workspace']['groups'], [])
        script = self.script()
        source = script[script.index('function chapterNumber'):script.index('function updateChapterPicker')]
        self.js(source, 'const catalog=' + json.dumps(filtered, ensure_ascii=False) + ';\n'
                + 'const formal=' + json.dumps(formal, ensure_ascii=False) + ';\n'
                + 'const candidateId=' + json.dumps(saved['id']) + ';\n'
                + "assert.ok(chapterChoices(catalog,formal).some(r=>r.id===candidateId));\n"
                + "assert.ok(!chapterChoices(catalog,{id:'formal:2',chapter:2}).some(r=>r.id===candidateId));")
        (self.root / (saved['path'] + '.meta.json')).unlink()
        unregistered = w._editor_catalog(self.root)
        self.assertFalse(any(r['id'] == saved['id'] for g in unregistered['workspace']['groups']
                             for r in g['items']))
        self.assertIn(saved['id'], [r['id'] for r in unregistered['files']])
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_navigation_validates_metadata_and_keeps_material_and_filename_fallback(self):
        relative = '.story/drafts/workbench/第1章 细纲_候选_旧.md'
        docid = self.material(relative, '# 细纲\n计划材料')
        metadata = self.root / (relative + '.meta.json')
        metadata.write_text(json.dumps({'source': 'file:01_大纲细纲/旧细纲.md',
                                        'chapter': 2, 'content_kind': 'material'}), encoding='utf-8')
        before = self.fixture.authoritative_state()
        catalog = w._editor_catalog(self.root)
        by_chapter = {g['chapter']: [r['id'] for r in g['items']] for g in catalog['workspace']['groups']}
        self.assertIn(docid, by_chapter[2])
        self.assertNotIn(docid, by_chapter[1])
        self.assertFalse(w._editor_document(self.root, docid)['is_prose'])
        script = self.script()
        source = script[script.index('function chapterNumber'):script.index('function updateChapterPicker')]
        self.js(source, 'const catalog=' + json.dumps(catalog, ensure_ascii=False) + ';\n'
                + 'const materialId=' + json.dumps(docid) + ';\n'
                + "const material=chapterChoices(catalog,{id:'formal:2',chapter:2}).find(r=>r.id===materialId);\n"
                + "assert.equal(material.content_kind,'material');assert.ok(!material.manuscript);")
        text = (self.root / relative).read_bytes().decode('utf-8')
        pending = w._start_pending_save(self.root, relative, text, text,
                                       json.loads(metadata.read_text(encoding='utf-8')))
        blocked = w._editor_catalog(self.root)
        self.assertFalse(any(r['id'] == docid for g in blocked['workspace']['groups']
                             if g['chapter'] == 2 for r in g['items']))
        self.assertEqual(w._editor_document(self.root, docid)['metadata_error']['code'],
                         'workbench_metadata_pending')
        w._finish_pending_save(self.root, pending)
        for content in (None, '{broken', json.dumps({'chapter': True})):
            with self.subTest(metadata=content):
                if content is None:
                    metadata.unlink()
                else:
                    metadata.write_text(content, encoding='utf-8')
                catalog = w._editor_catalog(self.root)
                by_chapter = {g['chapter']: g['items'] for g in catalog['workspace']['groups']}
                fallback = next(r for r in by_chapter[1] if r['id'] == docid)
                self.assertIn('同章号文件', fallback['relation'])
                self.assertNotIn(docid, [r['id'] for r in by_chapter[2]])
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_equal_draft_is_not_claimed_as_adopted(self):
        formal = w._editor_document(self.root, 'formal:1')
        body = (self.root / formal['path']).read_text()
        docid = self.material('.story/drafts/第1章 原稿.md', body)
        d = w._editor_document(self.root, docid)
        self.assertIn('逐字一致', d['status'])
        self.assertIn('不据此推断采用历史', d['status'])

    def test_history_banner_does_not_rewrite_old_report(self):
        docid = self.material('03_测试记录/报告.md', '# 历史\n正式正文0章')
        d = w._editor_document(self.root, docid)
        self.assertIn('当前进度', d['historical_note'])
        self.assertIn('正式正文0章', d['text'])
        self.assertEqual(w._editor_catalog(self.root)['workspace']['formal_total'], 2)

    def test_metrics_use_plan_count_method_title_and_selection(self):
        p = json.loads(self.book.db.execute('SELECT data FROM plans WHERE chapter=2').fetchone()[0])
        p.update(count_method='letters_numbers_v1', count_title=True)
        self.book.save_plan(2, p, self.book.meta('revision'))
        d = w._editor_document(self.root, 'formal:2')
        result = w._editor_metrics(self.root, {'id': d['id'], 'text':'甲A1。\n\u200b', 'selection':'甲A。'})
        expected = story.manuscript_counts(d['prefix']+'甲A1。\n\u200b', True)['letters_numbers_v1']
        self.assertEqual(result['count'], expected)
        self.assertEqual(result['selection'], 2)
        self.assertTrue(result['include_title'])
        m = self.material('01_大纲细纲/第2章 细纲.md', '第2章 细纲\n甲。')
        counted = w._editor_metrics(self.root, {'id':m,'text':'第2章 细纲\n甲。'})
        self.assertFalse(counted['is_prose'])
        self.assertIsNone(counted['target'])
        self.assertEqual(counted['count'], 7)

    def test_metrics_do_not_call_combining_mark_padding_within_chapter_length(self):
        plan = json.loads(self.book.db.execute('SELECT data FROM plans WHERE chapter=2').fetchone()[0])
        plan.update(count_method='visible_nonspace_v1', length=[2400, 2800])
        self.book.save_plan(2, plan, self.book.meta('revision'))
        doc = w._editor_document(self.root, 'formal:2')
        body = '甲' + '\u0301' * 2399
        result = w._editor_metrics(self.root, {'id': doc['id'], 'text': body})
        self.assertEqual(result['count'], 2400)
        self.assertTrue(result['within_target'])
        self.assertFalse(result['in_range'])
        self.assertEqual(result['length_issues'], ['invisible_padding'])
        official = story.lint_text(doc['prefix'] + body, plan)
        self.assertIn('invisible_padding', {issue['code'] for issue in official['errors']})
        page = w._editor_page(w.snapshot(self.book), {}, 'test').decode()
        self.assertIn('章幅检查未通过：附着标记和空白填充符不计下限', page)
        self.assertIn('仅核对章幅，章头等另查', page)

    def test_metrics_reject_named_blank_glyph_padding_and_keep_ordinary_unicode(self):
        plan = json.loads(self.book.db.execute('SELECT data FROM plans WHERE chapter=2').fetchone()[0])
        plan.update(count_method='visible_nonspace_v2', length=[2400, 2800])
        self.book.save_plan(2, plan, self.book.meta('revision'))
        doc = w._editor_document(self.root, 'formal:2')
        for filler in '\u115f\u1160\u2800\u3164\uffa0':
            with self.subTest(codepoint=f'U+{ord(filler):04X}'):
                body = '甲' + filler * 2399
                result = w._editor_metrics(self.root, {
                    'id': doc['id'], 'text': body, 'selection': filler})
                self.assertEqual(result['method'], 'visible_nonspace_v2')
                self.assertEqual(result['count'], 1)
                self.assertEqual(result['selection'], 0)
                self.assertFalse(result['within_target'])
                self.assertFalse(result['in_range'])
                self.assertEqual(result['length_issues'], ['length'])
        meaningful = '甲한가⠁🙂。'
        result = w._editor_metrics(self.root, {
            'id': doc['id'], 'text': meaningful, 'selection': meaningful})
        self.assertEqual(result['selection'], len(meaningful))

        # A historical v1 plan still reports its old count, but the editor
        # must not describe a padded 2400 as a valid chapter length.
        plan['count_method'] = 'visible_nonspace_v1'
        self.book.save_plan(2, plan, self.book.meta('revision'))
        legacy = w._editor_metrics(self.root, {
            'id': doc['id'], 'text': '甲' + '\u3164' * 2399})
        self.assertEqual(legacy['count'], 2400)
        self.assertTrue(legacy['within_target'])
        self.assertFalse(legacy['in_range'])
        self.assertEqual(legacy['length_issues'], ['invisible_padding'])

    def test_metrics_keep_saved_v1_boundary_but_recheck_changed_body(self):
        formal = w._editor_document(self.root, 'formal:2')
        original = (self.root / formal['path']).read_bytes().decode('utf-8') + '\ufe0f'
        plan = self.book.get_plan(2)
        plan['count_method'] = 'visible_nonspace_v1'
        self.book.save_plan(2, plan, self.book.meta('revision'))
        binding = story.outline.binding_for(self.book, 2)
        story.outline.bind(self.book, 2, binding['path'], self.book.meta('revision'),
                           hashlib.sha256((self.root / binding['path']).read_bytes()).hexdigest())
        draft = self.root / '.story/drafts/第2章 历史口径.md'
        draft.write_bytes(original.encode('utf-8'))
        delta = {'book_id': self.book.meta('id'), 'base_revision': self.book.meta('revision'),
                 'summary': '沈禾交出钥匙。', 'changes': [],
                 'review': {'draft_sha256': story.digest(original), 'issues': [], 'checks': {
                     key: {'note': '已核对交出钥匙的动作及后果。', 'quote': '沈禾把唯一的钥匙交给守门人。'}
                     for key in story.CHECKS}}}
        self.assertTrue(self.book.commit(2, draft, delta, replace_last=True)['exports_complete'])
        plan['length'] = [story.manuscript_counts(original)['visible_nonspace_v1'], 1000]
        self.book.save_plan(2, plan, self.book.meta('revision'))
        formal = w._editor_document(self.root, 'formal:2')
        candidate = self.material('.story/drafts/第2章 BOM原文.md', '\ufeff' + original)
        for document_id in ('formal:2', candidate):
            with self.subTest(document=document_id):
                doc = w._editor_document(self.root, document_id)
                unchanged = w._editor_metrics(self.root, {'id': document_id, 'text': doc['text']})
                self.assertTrue(unchanged['in_range'])
                changed = w._editor_metrics(self.root, {
                    'id': document_id, 'text': doc['text'].replace('唯一', '原有', 1)})
                self.assertFalse(changed['in_range'])
                self.assertEqual(changed['length_issues'], ['invisible_padding'])

    def test_metrics_panel_explains_padding_without_calling_it_out_of_range(self):
        script = self.script()
        source = script[script.index('function scheduleMetrics'):script.index('function drawCatalog')]
        mock = r"""
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{textContent:''});return elements.get(id);};
const docs=new Map([['draft',{id:'draft',editable:true,editing:false,value:'甲'}]]);
const active='draft',readableMethod={visible_nonspace_v1:'可见非空白字符'};
let metricTimer,metricSequence=0;
const clearTimeout=()=>{},setTimeout=fn=>{fn();return 1;};
const call=async()=>({count:2400,target:[2400,2800],in_range:false,within_target:true,
 length_issues:['invisible_padding'],is_prose:true,include_title:false,selection:0,method:'visible_nonspace_v1'});
"""
        self.js(mock + source, r"""
(async()=>{scheduleMetrics();await new Promise(setImmediate);
 assert.ok($('word-count').textContent.includes('附着标记和空白填充符不计下限'));
 assert.ok(!$('word-count').textContent.includes('范围外'));
 assert.ok($('count-method').textContent.includes('仅核对章幅，章头等另查'));
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_review_task_is_read_only_and_identifies_saved_source(self):
        before = self.fixture.authoritative_state()
        d = w._editor_document(self.root, 'formal:1')
        r = w._editor_review_task(self.root, d['id'])
        self.assertIn(d['sha256'], r['prompt'])
        self.assertIn('不自动修改或正式采用', r['prompt'])
        self.assertIn('尚未交给助手', r['status'])
        self.assertIsNotNone(r['lint'])
        self.assertEqual(r['check_scope'], 'complete_chapter')
        self.assertIn('只读完整章节工具检查', r['prompt'])
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_review_task_accepts_actual_bom_candidate_and_preserves_raw_snapshot(self):
        formal = w._editor_document(self.root, 'formal:1')
        original = (self.root / formal['path']).read_bytes()
        candidate = self.material('.story/drafts/第1章 BOM候选.md', '\ufeff' + original.decode('utf-8'))
        opened = w._editor_document(self.root, candidate)
        raw = (self.root / opened['path']).read_bytes()
        before = self.fixture.authoritative_state()
        result = w._editor_review_task(self.root, candidate, opened['sha256'])
        self.assertEqual(result['check_scope'], 'complete_chapter')
        self.assertTrue(result['lint']['ok'])
        self.assertEqual(opened['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(result['lint']['draft_sha256'], opened['sha256'])
        self.assertEqual(result['lint']['draft_sha256'], story.digest(original.decode('utf-8')))
        self.assertIn(opened['sha256'], result['prompt'])
        self.assertEqual((self.root / opened['path']).read_bytes(), raw)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_review_task_rejects_bom_only_change_during_lint(self):
        formal = w._editor_document(self.root, 'formal:1')
        original = (self.root / formal['path']).read_bytes()
        candidate = self.material('.story/drafts/第1章 BOM竞态.md', '\ufeff' + original.decode('utf-8'))
        opened = w._editor_document(self.root, candidate)
        before = self.fixture.authoritative_state()
        original_lint = story.Book.lint

        def lint_then_remove_bom(book, chapter, path):
            result = original_lint(book, chapter, path)
            path.write_bytes(path.read_bytes().removeprefix(b'\xef\xbb\xbf'))
            return result

        with patch.object(story.Book, 'lint', new=lint_then_remove_bom):
            self.fixture.assert_story_error('stale_snapshot', w._editor_review_task,
                                            self.root, candidate, opened['sha256'])
        self.assertEqual((self.root / opened['path']).read_bytes(), original)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_review_task_uses_full_chapter_lint_and_marks_fragment_scope(self):
        wrong = self.material('.story/drafts/第1章 错题.md',
                              '第2章 错题\n沈禾把唯一的钥匙交给守门人。\n')
        checked = w._editor_review_task(self.root, wrong)
        self.assertEqual(checked['check_scope'], 'complete_chapter')
        self.assertFalse(checked['lint']['ok'])
        self.assertIn('chapter_heading_number', {issue['code'] for issue in checked['lint']['errors']})
        self.assertIn('若本文件只是章中片段', checked['check_note'])

        no_plan = self.material('.story/drafts/第3章 片段.md', '沈禾摸了摸钥匙。')
        scoped = w._editor_review_task(self.root, no_plan)
        self.assertIsNone(scoped['lint'])
        self.assertEqual(scoped['check_scope'], 'no_plan')
        self.assertIn('未运行完整章节检查', scoped['prompt'])

    def test_review_panel_renders_full_lint_heading_errors(self):
        script = self.script()
        source = script[script.index(" $('review-task').onclick=async()=>"):
                        script.index(" $('copy-review').onclick=async()=>")]
        mock = r"""
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{textContent:'',hidden:true,
 scrollIntoView(){}});return elements.get(id);};
const docs=new Map([['draft',{id:'draft',editable:true,needs_recovery:false,sha256:'abc'}]]);
const active='draft',dirty=()=>false,leaveComparison=()=>{};let warning='';
const note=x=>warning=x;
const call=async()=>({prompt:'请审稿',status:'审稿任务已生成',check_note:'只读完整章节工具检查。',
 lint:{ok:false,errors:[{code:'chapter_heading_title',expected:'第1章 正确',actual:'第1章 错题'}],warnings:[]}});
"""
        self.js(mock + source, r"""
(async()=>{await $('review-task').onclick();
 assert.equal(warning,'');
 assert.ok($('review-task-status').textContent.includes('完整章节工具检查'));
 assert.ok($('review-findings').textContent.includes('第1章 正确'));
 assert.ok($('review-findings').textContent.includes('第1章 错题'));
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_fulltext_finds_contents_and_excludes_changed_formal(self):
        docid = self.material('00_项目策划/人物.md', '目标：云雀在岸边等船。')
        result = w._editor_search(self.root, '云雀')
        self.assertEqual([r['id'] for r in result['results']], [docid])
        self.assertTrue(result['complete'])
        formal = w._editor_document(self.root, 'formal:1')
        (self.root/formal['path']).write_text('云雀')
        result = w._editor_search(self.root, '云雀')
        self.assertNotIn('formal:1', [r['id'] for r in result['results']])
        self.assertFalse(result['complete'])
        self.assertIn('正式稿外改', ' '.join(result['warnings']))

    def test_library_only_opens_registered_roots_and_reuses_service(self):
        library = w._library_roots(self.root)
        key = next(iter(library))
        with patch.object(w, '_service_request', side_effect=AssertionError('must not request itself')):
            self.assertEqual(w._library_open(library,key,self.root,'http://127.0.0.1/self/')['url'], 'http://127.0.0.1/self/')
        with patch.object(w, '_service_request', return_value={'running':True,'url':'http://127.0.0.1:1234/token/', 'book_id':library[key]['book_id']}):
            self.assertEqual(w._library_open(library,key)['url'], 'http://127.0.0.1:1234/token/')
        with self.assertRaises(story.StoryError):
            w._library_open(library,'../../outside')
        with patch.object(w, '_service_request', return_value={'running':None}):
            (self.root/'.story/workbench-service.json').write_text(json.dumps({'stopped':False}))
            with self.assertRaises(story.StoryError) as error:
                w._library_open(library,key)
            self.assertEqual(error.exception.code,'workbench_unreachable')

    def js(self, source, test):
        node = shutil.which('node')
        if not node: self.skipTest('Node.js required')
        result = subprocess.run([node, '-'], input="const assert=require('assert');\n"+source+'\n'+test,
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def script(self):
        return w._editor_page(w.snapshot(self.book),{},'test').decode().split('<script>',1)[1].split('</script>',1)[0]

    def test_diff_preserves_all_text_and_only_marks_changes(self):
        script=self.script();source=script[script.index('function diffLines'):script.index('function drawDiff')]
        self.js(source, r"""
for(const [a,b] of [['甲\n\n乙','甲\n\n丙'],['','甲'],['重复\n重复','重复'],['a\n','a']]){
 const d=diffLines(a,b);assert.equal(d.left.map(x=>x.text).join('\n'),a);assert.equal(d.right.map(x=>x.text).join('\n'),b);
}
let d=diffLines('相同\n旧文','相同\n新文');assert.equal(d.left[0].change,'');assert.equal(d.left[1].change,'removed');assert.equal(d.right[1].change,'added');
const long=Array(800).fill('相同').join('\n');assert.ok(diffLines(long,long).left.every(x=>!x.change));
""")

    def test_markdown_builds_text_nodes_not_executable_html(self):
        s=self.script();source=s[s.index('function inlineText'):s.index('function readingView')]
        mock=r"""
class E{constructor(tag){this.tag=tag;this.children=[];this.textContent='';}append(...x){this.children.push(...x);}replaceChildren(){this.children=[];}}
const document={createElement:t=>new E(t),createTextNode:t=>({tag:'text',textContent:t})};let opened=null;const openDoc=id=>opened=id,note=()=>{};
"""
        test=r"""
const root=new E('root');renderMarkdown(root,'# 标题\n\n|项|值|\n|---|---|\n|甲|乙|\n\n<script>alert(1)</script>\n\n[跳转](第1章%20细纲.md) [坏](javascript:alert(1))','01_大纲细纲/总纲.md');
const all=[];function walk(e){all.push(e);for(const c of e.children||[])walk(c);}walk(root);
assert.ok(all.some(e=>e.tag==='table'));assert.ok(all.some(e=>e.tag==='h1'));assert.ok(!all.some(e=>e.tag==='script'));
const link=all.find(e=>e.tag==='a');link.onclick({preventDefault(){}});assert.equal(opened,'file:01_大纲细纲/第1章 细纲.md');assert.ok(!all.some(e=>e.href?.startsWith('javascript:')));
"""
        self.js(mock+source,test)

    def save_copy(self, doc, text):
        return w._editor_save(self.root, {'id': doc['id'], 'text': text,
            'sha256': doc['sha256'], 'snapshot': doc['snapshot'],
            'metadata_sha256': doc.get('metadata_sha256')})

    def test_outline_candidate_and_descendants_keep_material_type_and_heading(self):
        before = self.fixture.authoritative_state()
        docid = self.material('01_大纲细纲/第1章 细纲.md', '# 第1章 细纲\n\n## 目标\n原计划')
        text = '# 第1章 细纲\n\n## 目标\n修改计划'
        doc = w._editor_document(self.root, docid)
        for version in range(2):
            saved = self.save_copy(doc, text)
            doc = w._editor_document(self.root, saved['id'])
            self.assertEqual(doc['text'], text)
            self.assertFalse(doc['is_prose'])
            self.assertTrue(doc['render_markdown'])
            self.assertNotIn('comparison', doc)
            self.assertIsNone(w._editor_metrics(self.root, {'id': doc['id'], 'text': text})['target'])
            self.assertIsNone(w._editor_review_task(self.root, doc['id'])['lint'])
            text += '\n补充'
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_material_recovery_and_pending_preserve_type_even_without_source(self):
        source = self.material('01_大纲细纲/第1章 细纲.md', '# 第1章 细纲\n原计划')
        doc = w._editor_document(self.root, source)
        (self.root / doc['path']).unlink()
        text = '# 第1章 细纲\n待恢复计划'
        payload = {'id': source, 'text': text, 'title': doc['title'],
                   'recovery_key': 'material-recovery-key-123', 'chapter': 1,
                   'content_kind': 'material', 'sha256': doc['sha256'], 'prefix': ''}
        saved = w._editor_save(self.root, payload, recovery=True)
        recovered = w._editor_document(self.root, 'file:' + saved['path'])
        self.assertFalse(recovered['is_prose'])
        self.assertEqual(recovered['text'], text)
        self.assertNotIn('comparison', recovered)
        candidate = self.save_copy(recovered, text + '\n补充')
        self.assertFalse(w._editor_document(self.root, candidate['id'])['is_prose'])
        meta = json.loads((self.root / (saved['path'] + '.meta.json')).read_text())
        pending, _ = w._start_pending_save(self.root, saved['path'], text, text, meta)
        d = w._editor_document(self.root, 'pending:' + pending)
        self.assertFalse(d['is_prose'])
        self.assertEqual(d['text'], text)
        self.assertNotIn('comparison', d)
        self.assertTrue(d['needs_recovery'])

    def test_legacy_material_origin_is_recognized_and_invalid_type_rejected(self):
        relative = '.story/drafts/workbench/第1章 细纲_候选_旧.md'
        docid = self.material(relative, '# 第1章 细纲\n计划')
        meta = {'source': 'file:01_大纲细纲/已不存在.md', 'chapter': 1}
        path = self.root / (relative + '.meta.json')
        path.write_text(json.dumps(meta))
        d = w._editor_document(self.root, docid)
        self.assertFalse(d['is_prose'])
        self.assertTrue(d['text'].startswith('# 第1章'))
        meta['content_kind'] = 'unknown'
        path.write_text(json.dumps(meta))
        self.assertFalse(w._editor_document(self.root, docid)['editable'])

    def test_directory_query_keeps_related_materials_independent(self):
        docid = self.material('01_大纲细纲/第1章 细纲.md', '计划')
        c = w._editor_catalog(self.root, query='完全不匹配')
        self.assertEqual(c['files'], [])
        self.assertIn(docid, [r['id'] for r in c['related_files']])

    def test_catalog_refresh_preserves_collapsed_groups_scroll_and_plan_only_open(self):
        s = self.script()
        source = s[s.index('function drawCatalog'):s.index('// Paragraph LCS')]
        source += s[s.index('async function catalog'):s.index('async function recover')]
        source += s[s.index('function primaryChapterDocument'):s.index('function locateActive')]
        source = w._book_display_script() + s[s.index('let bookChoices='):s.index('function chapterNumber')] + source
        mock = r"""
class E {constructor(){this.children=[];this.dataset={};this.open=false;this.value='';this.parentElement={scrollTop:0};}replaceChildren(){this.children=[];this.parentElement.scrollTop=0;}append(e){this.children.push(e);}setAttribute(k,v){this[k]=v;}querySelectorAll(){return this.children;}}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
const document={createElement:()=>new E()},docs=new Map([['plan:1',{id:'plan:1',value:'未保存编辑'}]]);
let currentCatalog,viewMode='chapters',loading=false,reloadPending=false,pendingFocus=false,offset=0,active='plan:1',LIMIT=10,queryTimer;
const updateChapterPicker=()=>{};const location={hash:''},chapterNumber=()=>1,showChapterInfo=()=>{},badge=()=>{},item=r=>r,note=e=>{throw Error(e);};let opened=null;
const openDoc=async id=>{opened=id;};
const packet={chapters:[],files:[],warnings:[],total:0,workspace:{formal_total:0,planned_total:1,next_chapter:1,captured_at:'2026-09-26',groups:[{chapter:1,title:'第1章 计划',status:'已规划',items:[{id:'plan:1'}]}],total:1,offset:0,limit:10,has_more:false}};
const call=async()=>packet;
"""
        self.js(mock + source, r"""
(async()=>{
 const group=new E();group.dataset.group='全书材料';group.open=false;$('list').append(group);$('list').parentElement.scrollTop=149;
 await catalog();assert.equal($('list').children.find(g=>g.dataset.group===group.dataset.group).open,false);assert.equal($('list').parentElement.scrollTop,149);assert.equal(docs.get(active).value,'未保存编辑');assert.equal($('list').children.find(x=>x.dataset.chapter==='1').dataset.doc,'plan:1');
 active=null;await catalog();assert.equal(opened,'plan:1');
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def catalog_race_js(self, test):
        script = self.script()
        source = script[script.index('function drawCatalog'):script.index('// Paragraph LCS')]
        source += script[script.index('function primaryChapterDocument'):script.index('function locateActive')]
        source += script[script.index('async function catalog'):script.index('async function recover')]
        source += script[script.index("$('search').oninput="):script.index("window.addEventListener('beforeunload'")]
        source += script[script.index(" $('locate').onclick="):script.index(" $('compare-back').onclick=")]
        source = w._book_display_script() + script[script.index('let bookChoices='):script.index('function chapterNumber')] + source
        mock = r"""
class E {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this.open=false;this.parentElement={scrollTop:0};this.textContent='';}
 replaceChildren(){this.children=[];}append(...children){this.children.push(...children);}
 setAttribute(k,v){this[k]=v;}querySelectorAll(tag){return this.children.filter(e=>e.tagName===tag.toUpperCase());}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const document={createElement:tag=>new E(tag)},location={hash:''};
const docs=new Map([['formal:1',{id:'formal:1',chapter:1,value:'未保存编辑'}],['formal:21',{id:'formal:21',chapter:21,value:'另一章未保存编辑'}]]);
let currentCatalog,viewMode='chapters',loading=false,reloadPending=false,pendingFocus=false,offset=0,active='formal:1',LIMIT=10,queryTimer,searchTimer;
const chapterNumber=d=>d.chapter,updateChapterPicker=()=>{},showChapterInfo=()=>{},badge=()=>{},item=row=>row;
const notes=[],note=text=>notes.push(text),locations=[],locateActive=()=>locations.push(active),openDoc=async()=>{};
const setTimeout=fn=>{searchTimer=fn;return 1;},clearTimeout=()=>{searchTimer=null;};
const requests=[],responses=[];
function packet(request){
 const page=request.focus?Math.floor((request.focus-1)/LIMIT)*LIMIT:request.offset;
 const groups=Array.from({length:10},(_,i)=>({chapter:page+i+1,title:'第'+(page+i+1)+'章',status:'已有正式稿',items:[{id:'formal:'+(page+i+1)}]}));
 return {warnings:[],chapters:[],files:[],related_files:[],total:40,offset:page,has_more:page+LIMIT<40,
 workspace:{formal_total:40,planned_total:40,next_chapter:41,captured_at:'2026-10-05',groups,total:40,offset:page,limit:LIMIT,has_more:page+LIMIT<40}};
}
const call=(action,data)=>new Promise(resolve=>{assert.equal(action,'catalog');requests.push({...data});responses.push(()=>resolve(packet(data)));});
async function flush(){for(let i=0;i<8;i++)await Promise.resolve();}
async function respond(index){responses[index]();await flush();}
"""
        self.js(mock + source, '(async()=>{\n' + test + r"""
assert.equal(docs.get('formal:1').value,'未保存编辑');
assert.equal(docs.get('formal:21').value,'另一章未保存编辑');
assert.deepEqual(notes,[]);
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_pending_catalog_preserves_repeated_user_paging(self):
        self.catalog_race_js(r"""
const first=catalog();$('older').onclick();$('older').onclick();
assert.equal(requests.length,1);assert.equal(offset,20);
await respond(0);await first;
assert.equal(requests.length,2);assert.equal(requests[1].offset,20);assert.equal(currentCatalog,undefined);
await respond(1);assert.equal(offset,20);assert.equal(currentCatalog.workspace.offset,20);assert.ok(!loading);
""")

    def test_pending_catalog_search_resets_page_and_supersedes_old_focus(self):
        self.catalog_race_js(r"""
offset=20;active='formal:21';const first=catalog(true);
$('search').value='新搜索词';$('search').oninput();searchTimer();
await respond(0);await first;
assert.equal(requests[1].offset,0);assert.equal(requests[1].query,'新搜索词');assert.equal(requests[1].focus,null);
assert.equal(currentCatalog,undefined);await respond(1);
assert.equal(offset,0);assert.deepEqual(locations,[]);
""")

    def test_pending_catalog_navigation_cancels_previous_chapter_focus(self):
        self.catalog_race_js(r"""
active='formal:21';const first=catalog(true);$('older').onclick();await respond(0);await first;
assert.equal(requests[1].offset,10);assert.equal(requests[1].focus,null);await respond(1);
assert.equal(offset,10);assert.deepEqual(locations,[]);
""")

    def test_pending_catalog_background_refresh_keeps_latest_chapter_focus(self):
        self.catalog_race_js(r"""
const first=catalog(true);active='formal:21';catalog(true);catalog();await respond(0);await first;
assert.equal(requests[1].focus,21);assert.equal(requests[1].context_chapter,21);await respond(1);
assert.equal(offset,20);assert.deepEqual(locations,['formal:21']);
""")

    def test_pending_catalog_mode_switch_ignores_old_directory(self):
        self.catalog_race_js(r"""
const first=catalog();viewMode='files';offset=0;catalog(true,true);await respond(0);await first;
assert.equal(currentCatalog,undefined);await respond(1);
assert.equal(viewMode,'files');assert.ok($('range').textContent.includes('正式章节'));
""")

    def test_catalog_response_during_search_debounce_does_not_render_old_results(self):
        self.catalog_race_js(r"""
offset=20;const first=catalog();$('search').value='输入中的搜索词';$('search').oninput();
await respond(0);await first;assert.equal(currentCatalog,undefined);assert.equal(offset,0);
searchTimer();assert.equal(requests[1].offset,0);assert.equal(requests[1].query,'输入中的搜索词');
await respond(1);assert.equal(offset,0);
""")

    def test_new_chapter_location_cancels_older_search_debounce(self):
        self.catalog_race_js(r"""
active='formal:21';$('search').value='搜索中的旧词';$('search').oninput();
assert.equal(typeof searchTimer,'function');$('locate').onclick();
assert.equal(searchTimer,null);assert.equal(requests[0].query,'');assert.equal(requests[0].focus,21);
await respond(0);assert.equal(offset,20);assert.deepEqual(locations,['formal:21']);
""")

    def test_opening_chapter_cancels_older_search_debounce(self):
        self.catalog_race_js(r"""
$('search').value='旧搜索词';$('search').oninput();active='formal:21';const opened=catalog(true);
assert.equal(searchTimer,null);assert.equal(requests[0].focus,21);
await respond(0);await opened;assert.equal(offset,20);assert.deepEqual(locations,['formal:21']);
""")

    def test_comparison_is_a_mode_and_returns_without_losing_editor_position(self):
        s=self.script()
        source=s[s.index('function leaveComparison'):s.index('function stepDiff')]
        source+=s[s.index("$('compare').onclick"):s.index("$('download').onclick")]
        self.js(r"""
const nodes={},$=id=>nodes[id]||(nodes[id]={hidden:false,textContent:'',scrollTop:0,selectionStart:2,selectionEnd:5,focus(){},setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}});
const main={scrollTop:400},classes=new Set();const document={querySelector:()=>main,body:{classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)}}};
let active='formal:1',compareSequence=0,diffTargets=[],diffIndex=-1;
const d={id:active,kind:'formal',value:'尚未保存的编辑',editing:true},docs=new Map([[active,d]]);
const drawDiff=()=>{diffTargets=[{}];},note=e=>{throw Error(e);};
let call=async()=>({text:'正式原文',sha256:'base'});$('text').scrollTop=99;
"""+source,r"""
(async()=>{
 await $('compare').onclick();assert.ok($('document-body').hidden);assert.ok(!$('difference').hidden);assert.equal(main.scrollTop,0);assert.ok(classes.has('comparing'));
 leaveComparison();assert.equal(d.value,'尚未保存的编辑');assert.equal(main.scrollTop,400);assert.equal($('text').scrollTop,99);assert.equal($('text').selectionStart,2);assert.ok(!$('document-body').hidden);
 let resolve;call=()=>new Promise(r=>resolve=r);const pending=$('compare').onclick();leaveComparison();resolve({text:'迟到的响应'});await pending;assert.ok($('difference').hidden);
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_shortcut_saves_only_when_available_and_escape_closes_panels(self):
        s=self.script();source=s[s.index("document.addEventListener('keydown'"):s.index("document.addEventListener('pointerdown'")]
        self.js(r"""
const trapPanelFocus=()=>false;const handlers={},document={querySelector:()=>({inert:false}),addEventListener:(k,fn)=>handlers[k]=fn,body:{classList:{contains:k=>k==='nav-open'}}};
const nodes={},$=id=>nodes[id]||(nodes[id]={open:true,disabled:false,click(){this.clicks=(this.clicks||0)+1;},focus(){this.focused=true;}});let closes=0,prevented=0;const closePanels=()=>closes++;
"""+source,r"""
const key={key:'s',metaKey:true,preventDefault(){prevented++;}};handlers.keydown(key);assert.equal($('save').clicks,1);assert.equal(prevented,1);
$('save').disabled=true;handlers.keydown(key);assert.equal($('save').clicks,1);
handlers.keydown({...key,isComposing:true});assert.equal(prevented,2);
handlers.keydown({key:'Escape'});assert.equal(closes,1);assert.equal($('maintenance').open,false);assert.equal($('more-actions').open,false);
""")

    def test_display_names_preserve_real_names_and_download_identity(self):
        s = self.script()
        source = s[s.index('function displayTitle'):s.index('function locateActive')]
        self.js(source, r"""
const formal={title:'第1章 原名',kind:'formal',path:'chapters/第1章 原名.md'};
assert.equal(downloadName(formal),'第1章 原名.txt');
assert.equal(downloadName({...formal,editable:true,text:'原文',value:'改文'}),'第1章 原名-未保存候选.txt');
assert.equal(downloadName({title:'总纲',kind:'material',external:true}),'总纲.txt');
const draft={title:'第1章 原名_候选_abcdef123456',kind:'candidate',path:'.story/drafts/workbench/第1章 原名_候选_abcdef123456.md'};
assert.equal(displayTitle(draft),'第1章 原名');assert.equal(downloadName(draft),'第1章 原名-候选.txt');
assert.equal(displayTitle({...draft,path:'.story/drafts/作者自命名.md'}),draft.title);
assert.equal(draft.title,'第1章 原名_候选_abcdef123456');
""")

    def test_download_preserves_complete_prose_and_current_unsaved_text(self):
        formal = w._editor_document(self.root, 'formal:1')
        raw = (self.root / formal['path']).read_bytes().decode('utf-8')
        candidate = self.save_copy(formal, formal['text'] + '她仍握着门环。\n')
        candidate = w._editor_document(self.root, candidate['id'])
        recovery = w._editor_save(self.root, {**formal, 'text': formal['text'] + '恢复的末句。\n',
                                  'recovery_key': 'download-recovery-key-1234'}, recovery=True)
        recovery = w._editor_document(self.root, recovery['id'])
        bom_id = self.material('.story/drafts/' + formal['title'] + '.md', '\ufeff' + raw)
        bom = w._editor_document(self.root, bom_id)
        material_id = self.material('01_大纲细纲/全书总纲.md', '# 总纲\n\n原计划\n')
        material = w._editor_document(self.root, material_id)
        cases = [
            {'doc': {**formal, 'value': formal['text']}, 'expected': raw},
            {'doc': {**formal, 'value': formal['text'] + '未保存的末句。\n'},
             'expected': raw + '未保存的末句。\n'},
            {'doc': {**candidate, 'value': candidate['text']},
             'expected': (self.root / candidate['path']).read_bytes().decode('utf-8')},
            {'doc': {**recovery, 'value': recovery['text']}, 'expected': raw + '恢复的末句。\n'},
            {'doc': {**bom, 'value': bom['text']}, 'expected': '\ufeff' + raw},
            {'doc': {**material, 'value': material['text']}, 'expected': material['text']},
        ]
        script = self.script()
        source = script[script.index("$('download').onclick"):script.index('async function reloadDocuments')]
        mock = r"""
let active,blob,clicked=0;const docs=new Map(),button={},$=()=>button;
const document={createElement:()=>({click(){clicked++;}})},downloadName=()=> '当前稿件.txt';
const URL={createObjectURL(value){blob=value;return 'blob:download';},revokeObjectURL(){}},setTimeout=()=>{};
"""
        self.js(mock + source, 'const cases=' + json.dumps(cases, ensure_ascii=False) + ';\n' + r"""
(async()=>{for(const {doc,expected}of cases){active=doc.id;docs.set(active,doc);button.onclick();
 const content=Buffer.from(await blob.arrayBuffer()).toString('utf8');assert.equal(content,expected);}
 assert.equal(clicked,cases.length);
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_directory_refresh_does_not_reload_or_mutate_unsaved_text(self):
        s = self.script()
        source = s[s.index("$('refresh').onclick"):s.index("$('search').oninput")]
        self.js(r"""
const nodes={},$=id=>nodes[id]||(nodes[id]={});const draft={text:'磁盘内容',value:'未保存修改'};
let catalogs=0,reloads=[];const catalog=()=>catalogs++,reloadDocuments=p=>reloads.push(p);
"""+source, r"""
$('refresh').onclick();assert.equal(catalogs,1);assert.deepEqual(reloads,[]);assert.equal(draft.value,'未保存修改');
$('reload-source').onclick();$('recover-reload').onclick();assert.deepEqual(reloads,[false,true]);
""")

    def test_focus_supports_both_directory_orders(self):
        c=w._editor_catalog(self.root,limit=1,focus=1)
        self.assertEqual(c['offset'],1)
        self.assertEqual(c['chapters'][0]['id'],'formal:1')
        self.assertEqual(c['workspace']['offset'],0)
        self.assertEqual(c['workspace']['groups'][0]['chapter'],1)

    def test_empty_fulltext_search_clears_status_and_paging(self):
        s = self.script()
        source = s[s.index('async function fullSearch'):s.index('function applyReading')]
        self.js(r"""
let searchSequence=0,fullSearchOffset=30;const nodes={};const $=id=>nodes[id]||(nodes[id]={value:'',textContent:'旧结果',disabled:false,replaceChildren(){this.cleared=true;}});
""" + source, r"""
(async()=>{await fullSearch();assert.equal(fullSearchOffset,0);assert.equal($('search-status').textContent,'');assert.ok($('search-results').cleared);assert.ok($('search-prev').disabled);assert.ok($('search-next').disabled);})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_search_results_preserve_candidate_and_formal_identity(self):
        docid=self.material('.story/drafts/第8章 检索候选.md','独有定位词')
        row=w._editor_search(self.root,'独有定位词')['results'][0]
        self.assertEqual(row['id'],docid)
        self.assertEqual(row['category'],'候选与草稿')
        self.assertIn('modified',row)
        formal=w._editor_document(self.root,'formal:1')
        results=w._editor_search(self.root,formal['text'].strip()[:8])['results']
        row=next(r for r in results if r['id']=='formal:1')
        self.assertEqual(row['kind'],'formal')
        self.assertEqual(row['category'],'正式正文')

    def test_per_document_positions_and_mode_positions_remain_independent(self):
        s=self.script();source=s[s.index('function rememberView'):s.index('function searchRanges')]
        self.js(r"""
let searchPreview=false,active='a';const a={id:'a',editing:true},b={id:'b',editing:true},docs=new Map([['a',a],['b',b]]);
const main={scrollTop:144},text={hidden:false,scrollTop:65,selectionStart:2,selectionEnd:8,selectionDirection:'backward',setSelectionRange(a,b,d){this.selectionStart=a;this.selectionEnd=b;this.selectionDirection=d;}};
const $=()=>text,document={querySelector:()=>main};
"""+source,r"""
rememberView();restoreView(b);assert.equal(text.selectionStart,0);assert.equal(main.scrollTop,0);
active='b';text.selectionStart=1;text.selectionEnd=1;main.scrollTop=500;rememberView();
restoreView(a);assert.equal(text.selectionStart,2);assert.equal(text.selectionEnd,8);assert.equal(text.selectionDirection,'backward');assert.equal(text.scrollTop,65);assert.equal(main.scrollTop,144);
a.editing=false;text.hidden=true;main.scrollTop=900;rememberView(a);a.editing=true;restoreView(a);assert.equal(main.scrollTop,144);
searchPreview=true;main.scrollTop=9999;rememberView(a);restoreView(a);assert.equal(main.scrollTop,144);
""")

    def test_search_preview_uses_live_buffer_literal_unicode_and_safe_marks(self):
        s=self.script();source=s[s.index('function searchRanges'):s.index('let closePanels')]
        self.js(r"""
class E{constructor(){this.children=[];this.hidden=false;this.classList={toggle(){}};}append(...a){this.children.push(...a);}replaceChildren(){this.children=[];}scrollIntoView(){this.scrolled=true;}}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E()),document={createTextNode:t=>({textContent:t}),createElement:()=>new E()};
let active='d',searchPreview=false,searchMarks=[],searchIndex=-1,remembered=0;
const d={value:'新稿 <script> *灯😀灯',text:'磁盘旧稿',prefix:'第1章 灯\n\n',editing:true},docs=new Map([['d',d]]),dirty=x=>x.value!==x.text,rememberView=()=>remembered++;
"""+source,r"""
assert.deepEqual(searchRanges('灯😀灯','灯'),[{start:0,end:1},{start:3,end:4}]);
assert.deepEqual(searchRanges('a+b A+B','a+b'),[{start:0,end:3},{start:4,end:7}]);
assert.equal(searchRanges('[x].*','[x].*').length,1);assert.equal(searchRanges('ABC','abc').length,1);
previewSearch('灯');assert.equal(searchMarks.length,3);assert.ok(searchMarks[0].scrolled);assert.ok($('match-query').textContent.includes('未保存'));stepSearch(1);assert.ok(searchMarks[1].scrolled);
assert.equal($('match-text').children.map(x=>x.textContent).join(''),d.prefix+d.value);assert.equal(d.value,'新稿 <script> *灯😀灯');assert.ok(d.editing);
previewSearch('已删除的词');assert.equal(searchMarks.length,0);assert.ok($('match-position').textContent.includes('未找到'));assert.ok($('match-prev').disabled);assert.ok($('match-next').disabled);
""")

    def test_drawer_tab_wraps_both_directions_and_ignores_hidden_items(self):
        s=self.script();source=s[s.index('function panelFocusables'):s.index('let compareSequence')]
        self.js(r"""
let opened=true,prevented=0;const first={tabIndex:0,getClientRects:()=>[1],focus(){document.activeElement=this;}},last={...first},hidden={...first,getClientRects:()=>[]},disabled={...first,disabled:true};
const panel={querySelectorAll:()=>[first,hidden,disabled,last]},$=()=>panel;
const document={activeElement:last,body:{classList:{contains:k=>opened&&k==='nav-open'}}};
"""+source,r"""
assert.equal(panelFocusables(panel).length,2);
const e={key:'Tab',preventDefault(){prevented++;}};assert.ok(trapPanelFocus(e));assert.equal(document.activeElement,first);
trapPanelFocus({...e,shiftKey:true});assert.equal(document.activeElement,last);
document.activeElement={};trapPanelFocus(e);assert.equal(document.activeElement,first);
opened=false;assert.equal(trapPanelFocus(e),false);assert.equal(prevented,3);
""")

    def test_prose_spacing_preserves_exact_text_and_special_blank_lines(self):
        s=self.script();source=s[s.index('function renderProse'):s.index('function readingView')]
        self.js(r"""
class E{constructor(){this.children=[];this.style={};}replaceChildren(){this.children=[];}append(x){this.children.push(x);}}
const document={createElement:()=>new E()};
"""+source,r"""
const box=new E();for(const text of ['甲\n\n乙','甲\r\n \r\n乙\n\n\n丙','日期：今天\n署名：某人','\n\n特殊开头\t \n','<script>文本</script>']){renderProse(box,text);assert.equal(box.children.map(x=>x.textContent).join(''),text);}
renderProse(box,'甲\n\n乙\n\n\n丙');assert.equal(box.children[1].style.height,'calc(var(--reader-size) * 0.8)');assert.equal(box.children[3].style.height,'calc(var(--reader-size) * 1.6)');
""")

    def test_file_purpose_does_not_claim_adoption_from_directory(self):
        s=self.script();source=s[s.index('function versionLabel'):s.index('function downloadName')]
        self.js(source,r"""
const outline={path:'01_大纲细纲/第一卷/第1章.md',category:'创作材料'};
const manuscript={path:'02_正文/第一卷/第1章.md',category:'创作材料'};
assert.equal(filePurpose(outline),'细纲材料');assert.equal(filePurpose(manuscript),'正文目录文件');assert.equal(versionLabel(manuscript),'材料');
assert.equal(filePurpose({path:'.story/drafts/第1章.md',category:'候选与草稿'}),'候选稿');
assert.equal(filePurpose({...manuscript,kind:'formal'}),'正式正文');
""")

    def test_chapter_picker_keeps_versions_materials_and_current_unsaved_buffer(self):
        s=self.script();source=s[s.index('function displayTitle'):s.index('function downloadName')]
        source+=s[s.index('function chapterChoices'):s.index('function updateChapterPicker')]
        source+=s[s.index('function primaryChapterDocument'):s.index('function locateActive')]
        self.js("const chapterNumber=d=>d.context?.chapter||d.chapter;\n"+source,r"""
const formal={id:'formal:1',category:'正式正文',title:'正式正文',path:'chapters/第1章.md'},plan={id:'plan:1',category:'计划',title:'已保存章计划'},draft={id:'file:draft',category:'候选与草稿',path:'.story/drafts/第1章 候选.md',title:'候选'},outline={id:'file:outline',path:'01_大纲细纲/第1章 细纲.md',title:'细纲'};
const c={workspace:{groups:[{chapter:1,items:[plan,draft,formal]}]},related_files:[draft,outline,{id:'other',path:'第2章.md'},{id:'external',external:true,path:'第1章.md'}]};
const d={...draft,kind:'candidate',context:{chapter:1},value:'未保存文字'};
const rows=chapterChoices(c,d);assert.equal(rows.length,4);assert.ok(rows.find(x=>x.id==='file:outline').label.startsWith('细纲材料'));assert.equal(rows.find(x=>x.id==='file:draft').value,'未保存文字');assert.ok(rows.find(x=>x.id==='formal:1').manuscript);assert.ok(!rows.find(x=>x.id==='plan:1').manuscript);
assert.equal(primaryChapterDocument(c.workspace.groups[0]).id,'formal:1');assert.equal(primaryChapterDocument({chapter:1,items:[draft,plan]}).id,'plan:1');assert.deepEqual(chapterChoices(c,{id:'global'}),[]);
""")

    def test_column_sizes_preserve_reading_space_and_reject_invalid_preferences(self):
        s=self.script();source=s[s.index('function fitColumns'):s.index('function setupColumns')]
        self.js(source,r"""
assert.deepEqual(fitColumns(undefined,null,1065),{left:236,right:248});assert.deepEqual(fitColumns(-10,900,1200),{left:180,right:360});
for(const width of [821,900,1065,1440]){const p=fitColumns(360,360,width);assert.ok(p.left>=180&&p.right>=200);assert.ok(width-p.left-p.right>=360);}
assert.deepEqual(fitColumns(NaN,Infinity,1065),{left:236,right:248});
""")
