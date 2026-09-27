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
        # Fixtures compare exact bytes; do not let Windows translate LF to CRLF.
        p.write_bytes(text.encode('utf-8'))
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
        source += s[s.index('function primaryChapterDocument'):s.index('function locateActive')]
        mock = r"""
class E {constructor(){this.children=[];this.dataset={};this.open=false;this.value='';this.parentElement={scrollTop:0};}replaceChildren(){this.children=[];this.parentElement.scrollTop=0;}append(e){this.children.push(e);}setAttribute(k,v){this[k]=v;}querySelectorAll(){return this.children;}}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
const document={createElement:()=>new E()},docs=new Map([['plan:1',{id:'plan:1',value:'未保存编辑'}]]);
let currentCatalog,viewMode='chapters',loading=false,reloadPending=false,offset=0,active='plan:1',LIMIT=10;
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
