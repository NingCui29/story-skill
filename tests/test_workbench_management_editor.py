"""Author handoff provenance and editor safety across every open buffer."""
import hashlib
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from outline_fixture import bind_adopted_outline
import test_workbench as base


story = base.story
w = story.workbench


class WritingTaskManagementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.book = self.fixture.root, self.fixture.book

    def plan_next(self, bind=True):
        plan = {**self.book.get_plan(2), 'title': '门后的雨'}
        self.book.save_plan(3, plan, self.book.meta('revision'))
        if bind:
            bind_adopted_outline(story, self.book, 3)
        return plan

    def disk_snapshot(self):
        return {path.relative_to(self.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def assert_error(self, code, operation):
        with self.assertRaises(story.StoryError) as error:
            operation()
        self.assertEqual(error.exception.code, code)

    def test_task_reuses_bound_brief_sources_revision_and_review_provenance(self):
        self.plan_next()
        before = self.disk_snapshot()
        result = w._editor_writing_task(self.root)
        self.assertTrue(result['ok'])
        self.assertEqual(result['chapter'], 3)
        self.assertEqual(result['book_id'], self.book.meta('id'))
        brief = result['brief']
        self.assertTrue(brief['read_only'])
        self.assertEqual(brief['revision'], self.book.meta('revision'))
        self.assertEqual(brief['canonical_book_root'], str(self.root))
        self.assertEqual(brief['context']['previous']['chapter'], 2)
        self.assertEqual(brief['context']['plan'], self.book.get_plan(3))
        self.assertEqual(brief['context']['outline']['status'], 'adopted')
        source = brief['adopted_outline']
        raw = (self.root / source['path']).read_bytes()
        self.assertEqual(source['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(source['sha256'], brief['context']['outline']['sha256'])
        self.assertEqual(source['text'], raw.decode('utf-8-sig'))
        self.assertEqual(brief['semantic_review']['status'], 'pending')
        self.assertEqual(brief['delivery_scope']['mode'], 'next_chapter_draft')
        self.assertIn('等待交给助手执行', result['status'])
        self.assertIn(source['sha256'], result['prompt'])
        self.assertIn('revision', result['prompt'])
        self.assertIn('不要将此任务已生成视为已执行', result['prompt'])
        self.assertEqual(before, self.disk_snapshot())

    def test_task_opens_core_in_query_only_mode(self):
        self.plan_next()
        original = story.Book
        opened = []

        def opening(*args, **kwargs):
            book = original(*args, **kwargs)
            opened.append((kwargs.get('read_only'), book.db.execute('PRAGMA query_only').fetchone()[0]))
            return book

        before = self.disk_snapshot()
        with patch.object(w.api, 'Book', side_effect=opening):
            w._editor_writing_task(self.root)
        self.assertTrue(opened)
        self.assertTrue(all(row == (True, 1) for row in opened))
        self.assertEqual(before, self.disk_snapshot())

    def test_missing_plan_blocks_without_creating_a_plan_or_draft(self):
        before = self.disk_snapshot()
        self.assert_error('plan_missing', lambda: w._editor_writing_task(self.root))
        self.assertEqual(before, self.disk_snapshot())

    def test_unbound_plan_and_generated_candidate_cannot_become_adopted(self):
        self.plan_next(bind=False)
        candidate = self.root / '01_大纲细纲/第3章 候选.md'
        candidate.write_bytes(self.book.plan_render(3)['text'].encode('utf-8'))
        before = self.disk_snapshot()
        self.assert_error('outline_unbound', lambda: w._editor_writing_task(self.root))
        self.assertIsNone(story.outline.binding_for(self.book, 3))
        self.assertEqual(before, self.disk_snapshot())

    def test_outline_drift_blocks_without_rebinding_or_overwriting(self):
        self.plan_next()
        source = story.outline.binding_for(self.book, 3)['path']
        (self.root / source).write_text('# 已在外部更改的细纲\n状态：已采用\n', encoding='utf-8')
        before = self.disk_snapshot()
        self.assert_error('outline_plan_drift', lambda: w._editor_writing_task(self.root))
        self.assertEqual(before, self.disk_snapshot())

    def test_changed_revision_after_brief_is_rejected_as_stale_handoff(self):
        plan = self.plan_next()
        original = story.Book.writing_brief

        def change_after_assembly(book, *args, **kwargs):
            brief = original(book, *args, **kwargs)
            self.book.save_plan(3, {**plan, 'goal': '下一轮已改变的目标'}, self.book.meta('revision'))
            return brief

        with patch.object(story.Book, 'writing_brief', new=change_after_assembly):
            with self.assertRaises(story.StoryError) as error:
                w._editor_writing_task(self.root)
        self.assertIn(error.exception.code, ('stale_snapshot', 'workbench_changed', 'workbench_book_changed'))

    def test_changed_outline_bytes_after_brief_are_rejected_without_rebinding(self):
        self.plan_next()
        source = story.outline.binding_for(self.book, 3)['path']
        original = story.Book.writing_brief
        revision = self.book.meta('revision')

        def change_after_assembly(book, *args, **kwargs):
            brief = original(book, *args, **kwargs)
            with (self.root / source).open('ab') as output:
                output.write('生成简报后外部追加的细纲。'.encode())
            return brief

        with patch.object(story.Book, 'writing_brief', new=change_after_assembly):
            self.assert_error('stale_snapshot', lambda: w._editor_writing_task(self.root))
        self.assertEqual(self.book.meta('revision'), revision)
        self.assertNotEqual(story.outline.binding_for(self.book, 3)['sha256'],
                            hashlib.sha256((self.root / source).read_bytes()).hexdigest())


class EditorManagementJavascriptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        page = w._editor_page(w.snapshot(self.fixture.book), {}, 'management-test-token').decode()
        self.script = page.split('<script>', 1)[1].split('</script>', 1)[0]

    def js(self, source, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        result = subprocess.run([node, '-'], input="const assert=require('assert');\n" + source + '\n' + test,
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def harness(self):
        return r'''
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.value='';this.textContent='';this.scrollTop=0;this.selectionStart=0;this.selectionEnd=0;this.hidden=false;this.disabled=false;this.classList={values:new Set(),add(...xs){xs.forEach(x=>this.values.add(x));},remove(...xs){xs.forEach(x=>this.values.delete(x));},contains(x){return this.values.has(x);}};}
 append(...children){this.children.push(...children);}setAttribute(key,value){this[key]=value;}addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
 showModal(){this.open=true;}close(){this.closed=true;this.open=false;for(const listener of this.listeners.close||[])listener();}remove(){this.removed=true;}
 scrollIntoView(){}focus(){this.focused=true;}setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
const main=new E('main'),document={body:new E('body'),createElement:tag=>new E(tag),querySelector:tag=>{assert.equal(tag,'main');return main;}};
const listeners={},window={addEventListener:(name,callback)=>listeners[name]=callback};
const docs=new Map(),notes=[],note=text=>notes.push(text),requests=[],fetched=new Map(),drawn=[];
let active=null,currentCatalog={chapters:[],files:[]},compareSequence=0,diffTargets=[],diffIndex=-1;
let call=async(action,data={})=>{requests.push({action,...data});if(!fetched.has(data.id))throw Error('missing '+data.id);return fetched.get(data.id);};
const openDoc=async()=>{},drawDiff=(...text)=>{drawn.push(text);diffTargets=['changed'];};
function doc(id,text,change={}){const result={id,title:id,path:id+'.md',text,value:text,editable:true,kind:'candidate',...change};docs.set(id,result);return result;}
function compareDialog(left,right){$('compare-choose').onclick();const dialog=document.body.children.at(-1),content=dialog.children[1],selects=content.children.filter(e=>e.tag==='label').map(label=>label.children[0]);selects[0].value=left;selects[1].value=right;return {dialog,content,run:content.children.find(e=>e.tag==='button'),error:content.children.at(-1)};}
async function flush(){for(let i=0;i<8;i++)await Promise.resolve();}
'''

    def tools_source(self):
        script = self.script
        helpers = script[script.index('function chapterNumber'):script.index('function documentIdentity')]
        dirty = next(line for line in script.splitlines() if line.startswith('function dirty'))
        leave = script[script.index('function leaveComparison'):script.index('function stepDiff')]
        return self.harness() + helpers + dirty + '\n' + leave + '\n' + w._editor_tools_script()

    def test_full_emitted_editor_script_parses_with_all_management_tools(self):
        self.js('', 'new Function(' + json.dumps(self.script) + ');')

    def test_arbitrary_compare_uses_unsaved_cached_buffers_and_restores_view(self):
        self.js(self.tools_source(), r'''
(async()=>{
 const left=doc('file:left','左稿磁盘',{value:'左稿未保存',prefix:'第1章 左稿\n'}),right=doc('file:right','右稿磁盘',{value:'右稿未保存',prefix:'第1章 右稿\n',editing:true});active=right.id;
 main.scrollTop=148;$('text').scrollTop=63;$('text').selectionStart=2;$('text').selectionEnd=7;
 const state=JSON.stringify([...docs]);const comparison=compareDialog(left.id,right.id);await comparison.run.onclick();
 assert.deepEqual(drawn,[['第1章 左稿\n左稿未保存','第1章 右稿\n右稿未保存']]);assert.equal(requests.length,0);
 assert.equal(active,right.id);assert.equal(docs.get(left.id),left);assert.equal(docs.get(right.id),right);assert.equal(left.value,'左稿未保存');assert.equal(right.value,'右稿未保存');
 assert.equal($('document-body').hidden,true);assert.equal($('difference').hidden,false);assert.ok(comparison.dialog.closed);assert.ok(document.body.classList.contains('comparing'));
 leaveComparison();assert.equal(main.scrollTop,148);assert.equal($('text').scrollTop,63);assert.equal($('text').selectionStart,2);assert.equal($('text').selectionEnd,7);assert.equal(JSON.stringify([...docs]),state);
 assert.equal($('document-body').hidden,false);assert.equal($('difference').hidden,true);
})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_uncached_comparison_reads_files_without_replacing_open_buffers(self):
        self.js(self.tools_source(), r'''
(async()=>{
 const current=doc('file:current','原始文本',{value:'当前未保存文字',editing:true});active=current.id;doc('file:hidden','隐藏原稿',{value:'隐藏文件未保存'});
 fetched.set('file:one',{id:'file:one',title:'一稿',text:'远程一稿'});fetched.set('file:two',{id:'file:two',title:'二稿',text:'远程二稿'});
 currentCatalog.files=[...fetched.values()];const before=JSON.stringify([...docs]);
 const comparison=compareDialog('file:one','file:two');await comparison.run.onclick();assert.deepEqual(drawn,[['远程一稿','远程二稿']]);assert.equal(requests.length,2);assert.ok(requests.every(r=>r.action==='open'));
 leaveComparison();assert.equal(JSON.stringify([...docs]),before);assert.equal(active,current.id);assert.equal(docs.size,2);
})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_switching_active_document_cancels_pending_comparison(self):
        self.js(self.tools_source(), r'''
(async()=>{
 const first=doc('file:first','第一稿',{value:'第一份未保存'}),second=doc('file:second','第二稿',{value:'第二份未保存'});active=first.id;
 currentCatalog.files=[{id:'file:remote',title:'待加载'}];let resolve;call=()=>new Promise(done=>{resolve=done;});
 const comparison=compareDialog('file:remote',second.id);const running=comparison.run.onclick();await flush();active=second.id;resolve({id:'file:remote',text:'已加载远程稿'});await running;
 assert.equal(drawn.length,0);assert.ok(comparison.error.textContent.includes('当前文件已切换'));assert.equal(active,second.id);assert.equal(first.value,'第一份未保存');assert.equal(second.value,'第二份未保存');assert.ok(!comparison.dialog.closed);
})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_comparing_same_document_is_blocked_without_loading(self):
        self.js(self.tools_source(), r'''
(async()=>{const first=doc('file:one','一稿');doc('file:two','二稿');active=first.id;const comparison=compareDialog(first.id,first.id);await comparison.run.onclick();assert.equal(drawn.length,0);assert.equal(requests.length,0);assert.ok(comparison.error.textContent.includes('不同稿件'));})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_closing_comparison_dialog_invalidates_late_response_without_changing_current_edit(self):
        self.js(self.tools_source(), r'''
(async()=>{
 const current=doc('file:current','原稿',{value:'未保存编辑',editing:true});active=current.id;
 currentCatalog.files=[{id:'file:remote',title:'未缓存稿件'}];let resolve;
 call=()=>new Promise(done=>{resolve=done;});
 const comparison=compareDialog('file:remote',current.id),running=comparison.run.onclick();await flush();
 assert.equal(comparison.run.disabled,true);comparison.dialog.children[2].onclick();assert.equal(comparison.dialog.removed,true);
 current.value='返回稿件后继续写下的新文字';$('document-body').hidden=false;$('difference').hidden=true;
 resolve({id:'file:remote',text:'迟到的另稿'});await running;
 assert.equal(drawn.length,0);assert.equal($('document-body').hidden,false);assert.equal($('difference').hidden,true);
 assert.equal(active,current.id);assert.equal(current.value,'返回稿件后继续写下的新文字');assert.equal(document.body.classList.contains('comparing'),false);
})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_cancelled_comparison_cannot_replace_a_later_dialog_result(self):
        self.js(self.tools_source(), r'''
(async()=>{
 const current=doc('file:current','原稿'),other=doc('file:other','另一稿');active=current.id;
 currentCatalog.files=[{id:'file:remote',title:'未缓存稿件'}];let resolve;
 call=()=>new Promise(done=>{resolve=done;});
 const old=compareDialog('file:remote',current.id),running=old.run.onclick();await flush();
 for(const listener of old.dialog.listeners.cancel||[])listener();old.dialog.close();
 const latest=compareDialog(current.id,other.id);await latest.run.onclick();assert.deepEqual(drawn,[['原稿','另一稿']]);
 resolve({id:'file:remote',text:'过期响应'});await running;
 assert.deepEqual(drawn,[['原稿','另一稿']]);assert.equal($('before-label').textContent,current.title);assert.equal($('after-label').textContent,other.title);
})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_failed_comparison_leaves_both_buffers_and_retry_available(self):
        self.js(self.tools_source(), r'''
(async()=>{
 const first=doc('file:first','第一稿',{value:'未保存第一稿'}),second=doc('file:second','第二稿',{value:'未保存第二稿'});active=first.id;
 currentCatalog.files=[{id:'file:missing',title:'读取失败稿件'}];const state=JSON.stringify([...docs]);const comparison=compareDialog(first.id,'file:missing');await comparison.run.onclick();
 assert.equal(JSON.stringify([...docs]),state);assert.equal(drawn.length,0);assert.equal(active,first.id);assert.equal(comparison.run.disabled,false);assert.ok(comparison.error.textContent.includes('missing'));assert.ok(!comparison.dialog.closed);
})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_writing_task_button_checks_dirty_saving_and_recovery_in_hidden_buffers(self):
        self.js(self.tools_source(), r'''
(async()=>{
 const current=doc('file:current','已保存');active=current.id;const hidden=doc('file:hidden','另一份已保存');
 for(const state of [{value:'另一份未保存'},{value:hidden.text,saving:true},{saving:false,needs_recovery:true}]){Object.assign(hidden,state);await $('writing-task').onclick();assert.equal(requests.length,0);assert.ok(notes.at(-1).includes('先保存全部候选'));}
})().catch(error=>{console.error(error);process.exit(1);});
''')

    def test_beforeunload_protects_hidden_saves_and_incomplete_recoveries(self):
        script = self.script
        source = script[script.index("window.addEventListener('beforeunload'"):script.index("window.addEventListener('hashchange'")]
        dirty = next(line for line in script.splitlines() if line.startswith('function dirty'))
        self.js(self.harness() + dirty + source, r'''
const current=doc('file:current','已保存');active=current.id;const hidden=doc('file:hidden','隐藏稿');
function unload(){let prevented=false;const event={preventDefault(){prevented=true;}};listeners.beforeunload(event);return {prevented,event};}
assert.equal(unload().prevented,false);
for(const state of [{value:'隐藏未保存'},{value:hidden.text,saving:true},{saving:false,needs_recovery:true}]){Object.assign(hidden,state);const result=unload();assert.equal(result.prevented,true);assert.equal(result.event.returnValue,'');assert.equal(active,current.id);}
assert.equal(hidden.needs_recovery,true);
''')


if __name__ == '__main__':
    unittest.main()
