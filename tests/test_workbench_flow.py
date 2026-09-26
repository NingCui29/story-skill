"""Chapter-centric author workflow: real state, candidate boundaries and UI utilities."""
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
        p.write_text(text, encoding='utf-8')
        return 'file:' + relative

    def test_groups_include_future_plans_without_claiming_prose_or_adoption(self):
        plan = json.loads(self.book.db.execute('SELECT data FROM plans WHERE chapter=2').fetchone()[0])
        self.book.save_plan(3, plan, self.book.meta('revision'))
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
        self.assertNotIn('formal:3', [r['id'] for r in last['items']])

    def test_context_uses_actual_receipt_and_prior_summary(self):
        d = w._editor_document(self.root, 'formal:2')
        self.assertIn('第1章', d['context']['previous_summary'])
        self.assertIn('checks', d['context']['review'])
        self.assertEqual(d['context']['plan']['title'], '雨夜 & 账本#2%')

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

    def test_review_task_is_read_only_and_identifies_saved_source(self):
        before = self.fixture.authoritative_state()
        d = w._editor_document(self.root, 'formal:1')
        r = w._editor_review_task(self.root, d['id'])
        self.assertIn(d['sha256'], r['prompt'])
        self.assertIn('不自动修改或正式采用', r['prompt'])
        self.assertIn('尚未交给助手', r['status'])
        self.assertIsNotNone(r['lint'])
        self.assertEqual(before, self.fixture.authoritative_state())

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
        with patch.object(w, '_service_request', return_value={'running':True,'url':'http://127.0.0.1:1234/token/'}):
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
        result = subprocess.run([node,'-e',"const assert=require('assert');\n"+source+'\n'+test],capture_output=True,text=True,timeout=15)
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
        mock = r"""
class E {constructor(){this.children=[];this.dataset={};this.open=false;this.value='';this.parentElement={scrollTop:0};}replaceChildren(){this.children=[];this.parentElement.scrollTop=0;}append(e){this.children.push(e);}querySelectorAll(){return this.children;}}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
const document={createElement:()=>new E()},docs=new Map([['plan:1',{id:'plan:1',value:'未保存编辑'}]]);
let currentCatalog,viewMode='chapters',loading=false,reloadPending=false,offset=0,active='plan:1',LIMIT=10;
const location={hash:''},chapterNumber=()=>1,showChapterInfo=()=>{},badge=()=>{},item=r=>r,note=e=>{throw Error(e);};let opened=null;
const openDoc=async id=>{opened=id;};
const packet={chapters:[],files:[],warnings:[],total:0,workspace:{formal_total:0,planned_total:1,next_chapter:1,captured_at:'2026-09-26',groups:[{chapter:1,title:'第1章 计划',status:'已规划',items:[{id:'plan:1'}]}],total:1,offset:0,limit:10,has_more:false}};
const call=async()=>packet;
"""
        self.js(mock + source, r"""
(async()=>{
 const group=new E();group.dataset.group='第1章 计划 · 已规划';group.open=false;$('list').append(group);$('list').parentElement.scrollTop=149;
 await catalog();assert.equal($('list').children.find(g=>g.dataset.group===group.dataset.group).open,false);assert.equal($('list').parentElement.scrollTop,149);assert.equal(docs.get(active).value,'未保存编辑');
 active=null;await catalog();assert.equal(opened,'plan:1');
})().catch(e=>{console.error(e);process.exit(1);});
""")

    def test_empty_fulltext_search_clears_status_and_paging(self):
        s = self.script()
        source = s[s.index('async function fullSearch'):s.index('function applyReading')]
        self.js(r"""
let searchSequence=0,fullSearchOffset=30;const nodes={};const $=id=>nodes[id]||(nodes[id]={value:'',textContent:'旧结果',disabled:false,replaceChildren(){this.cleared=true;}});
""" + source, r"""
(async()=>{await fullSearch();assert.equal(fullSearchOffset,0);assert.equal($('search-status').textContent,'');assert.ok($('search-results').cleared);assert.ok($('search-prev').disabled);assert.ok($('search-next').disabled);})().catch(e=>{console.error(e);process.exit(1);});
""")
