"""Exercise retained editor entry points across real asynchronous boundaries."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base
from test_workbench_editor_async_safety import safety_payload
from outline_fixture import bind_adopted_outline


class WorkbenchEditorFollowupFixesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        original = self.w._editor_document(self.fixture.root, 'formal:1')
        saved = self.w._editor_save(self.fixture.root, {
            **original, 'text': original['prefix'] + '完整恢复的正文。\n',
            'recovery_key': 'followup-recovery-1234567890'
        }, recovery=True)
        recovery = self.w._editor_document(self.fixture.root, saved['id'])
        other = self.w._editor_document(self.fixture.root, 'formal:2')
        plan = {**self.fixture.book.get_plan(2), 'title': '下一道门'}
        self.fixture.book.save_plan(3, plan, self.fixture.book.meta('revision'))
        bind_adopted_outline(base.story, self.fixture.book, 3)
        task = self.w._editor_writing_task(self.fixture.root)
        self.backend = {'recovery': recovery, 'other': other, 'task': task}
        page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'followup-test').decode()
        self.script = page.split('<script>', 1)[1].split('</script>', 1)[0]

    def js(self, case):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        opening = self.script[self.script.index('async function openDoc('):self.script.index('function pendingEdits(')]
        task = self.w._editor_tools_script()
        task = task[task.index("$('writing-task').onclick="):task.index("$('compare-choose').onclick=")]
        review = self.script[self.script.index("$('review-task').onclick="):self.script.index("$('copy-review').onclick=")]
        download = self.script[self.script.index('function downloadDocument('):self.script.index('async function reloadDocuments(')]
        helper = next(line for line in self.script.splitlines() if line.startswith('function downloadName('))
        harness = r'''
const assert=require('assert'),vm=require('vm'),webcrypto=require('crypto').webcrypto;
function page(options={}){
 const context=vm.createContext({URL,Map,TextEncoder,Blob,console,rawCrypto:webcrypto,backend,options});
 const read=source=>vm.runInContext(source,context);
 read(`
 const copy=value=>JSON.parse(JSON.stringify(value));
 const nodes={},make=()=>({hidden:false,disabled:false,value:'',textContent:'',scrollIntoView(){}});
 const $=id=>nodes[id]||(nodes[id]=make());
 const document={getElementById:id=>['save','text','current-book-root','list','download','edit','compare','review-task','writing-task','review-task-box','assistant-task-title','review-task-status','review-findings','review-prompt'].includes(id)?$(id):null,
  createElement:()=>({click(){exported.push({filename:this.download,blob:urls.get(this.href)});}})};
 const docs=new Map(),notes=[],rendered=[],previews=[],actions=[],requests=[],tasks=[],urls=new Map(),exported=[];
 const NativeURL=URL;URL=class extends NativeURL{static createObjectURL(blob){const id='blob:'+urls.size;urls.set(id,blob);return id;}static revokeObjectURL(){}};
 const setTimeout=()=>0,clearTimeout=()=>{},recover=async()=>{},badge=()=>{},rememberView=()=>{},closePanels=()=>{},leaveComparison=()=>{};
 const note=text=>notes.push(text),displayTitle=d=>d.title,versionLabel=()=>'';
 const previewSearch=query=>previews.push(query),dirty=d=>!!d?.editable&&d.value!==d.text;
 let active=null,reloading=false,openSequence=0,holdDigest=false,releaseDigest,releaseOld;
 const crypto={randomUUID:()=>rawCrypto.randomUUID(),subtle:{digest:async(...args)=>{if(holdDigest){holdDigest=false;await new Promise(done=>{releaseDigest=done;});}return rawCrypto.subtle.digest(...args);}}};
 const a={...copy(backend.recovery),value:backend.recovery.text,editing:false},b={...copy(backend.other),value:backend.other.text,editing:false};
 if(!options.empty){docs.set(a.id,a);docs.set(b.id,b);active=a.id;}
 let currentCatalog=options.noCatalog?null:{chapters:[a],files:[],workspace:{book_id:backend.task.book_id,revision:backend.task.brief.revision,next_chapter:backend.task.chapter}};
 const catalog=async()=>{},show=d=>{active=d.id;$('text').value=d.value;rendered.push(d.id);};
 const call=async(action,data={})=>{
  requests.push({action,...data});
  if(action==='writing-task'||action==='review-task')return new Promise((resolve,reject)=>tasks.push({action,resolve,reject}));
  if(action==='catalog')return {chapters:[a],files:[]};
  if(action!=='open')throw Error('Unexpected action '+action);
  if(options.holdFirst&&requests.length===1)return new Promise(done=>{releaseOld=()=>done(copy(backend.recovery));});
  return copy(data.id===a.id?backend.recovery:backend.other);
 };
 window=globalThis;window.top=window;
 const location={href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/',hash:options.hash||''};
 $('edit').onclick=()=>actions.push({action:'edit',active});$('compare').onclick=()=>actions.push({action:'compare',active});
 $('writing-task').onclick=()=>{throw Error('Legacy writing handler ran');};
 $('review-task').onclick=()=>{throw Error('Legacy review handler ran');};
 `);
 read(opening+helper+'\n'+download);
 read('async function reloadDocuments(){}');
 return {read,install:()=>read(asset)};
}
async function flush(){for(let i=0;i<20;i++)await Promise.resolve();}
async function digestStarted(p){for(let i=0;i<100;i++){if(p.read('typeof releaseDigest')==='function')return;await new Promise(done=>setImmediate(done));}throw Error('SHA barrier not reached');}
async function tasksStarted(p,count){for(let i=0;i<100;i++){if(p.read('tasks.length')===count)return;await new Promise(done=>setImmediate(done));}throw Error('Task request not reached');}
'''
        source = ('const backend=' + json.dumps(self.backend, ensure_ascii=False)
                  + ',asset=' + json.dumps(safety_payload()) + ',opening=' + json.dumps(opening)
                  + ',download=' + json.dumps(download) + ',helper=' + json.dumps(helper)
                  + ',writing=' + json.dumps(task) + ',review=' + json.dumps(review) + ';\n'
                  + harness + '\n(async()=>{' + case + '\n})().catch(error=>{console.error(error);process.exitCode=1;});')
        result = subprocess.run([node, '-'], input=source, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_delayed_cached_validation_cannot_reclaim_later_navigation(self):
        self.js(r'''
const p=page();await p.install().ready;p.read('holdDigest=true');
const earlier=p.read("openDoc(a.id,'earlier')");await digestStarted(p);
await p.read("openDoc(b.id,'latest')");p.read('releaseDigest()');await earlier;
assert.equal(p.read('active'),backend.other.id);assert.deepEqual(p.read('Array.from(previews)'),['latest']);
assert.equal(p.read('docs.get(a.id).value'),backend.recovery.text);
''')

    def test_same_hash_new_click_wins_without_late_query_or_reopening(self):
        self.js(r'''
const p=page();await p.install().ready;p.read('holdDigest=true');
const earlier=p.read("openDoc(a.id,'old query')");await digestStarted(p);
await p.read("openDoc(a.id,'new query')");const count=p.read('rendered.length');
p.read('releaseDigest()');await earlier;
assert.equal(p.read('active'),backend.recovery.id);assert.equal(p.read('rendered.length'),count);
assert.deepEqual(p.read('Array.from(previews)'),['new query']);
''')

    def test_download_remains_bound_to_clicked_buffer_after_switch_and_new_input(self):
        self.js(r'''
const p=page();await p.install().ready;p.read('holdDigest=true');
const downloading=p.read("$('download').onclick()");await digestStarted(p);
await p.read('openDoc(b.id)');p.read("b.value='另一稿件的新输入';$('text').value=b.value;releaseDigest()");await downloading;
assert.equal(p.read('active'),backend.other.id);assert.equal(p.read('exported.length'),1);
assert.equal(await p.read('exported[0].blob.text()'),backend.recovery.prefix+backend.recovery.text);
assert.equal(p.read('exported[0].filename'),backend.recovery.title+'.txt');
assert.equal(p.read('b.value'),'另一稿件的新输入');assert.equal(p.read('dirty(b)'),true);
''')

    def test_deferred_edit_and_comparison_never_act_on_other_document(self):
        self.js(r'''
for(const action of ['edit','compare']){
 const p=page();await p.install().ready;p.read('holdDigest=true');
 const pending=p.read(`$('${action}').onclick()`);await digestStarted(p);
 await p.read('openDoc(b.id)');p.read('releaseDigest()');await pending;
 assert.equal(p.read('actions.length'),0);assert.equal(p.read('active'),backend.other.id);
}
''')

    def test_no_hash_default_restarts_cancelled_legacy_open_with_checked_reader(self):
        self.js(r'''
for(const noCatalog of [false,true]){
 const p=page({empty:true,holdFirst:true,noCatalog});const old=p.read('openDoc(a.id)');
 assert.equal(p.read('requests.length'),1);const installed=p.install();assert.equal(await installed.ready,true);
 assert.equal(p.read('active'),backend.recovery.id);assert.equal(p.read('docs.get(a.id).value'),backend.recovery.text);
 const count=p.read('rendered.length');p.read('releaseOld()');await old;assert.equal(p.read('rendered.length'),count);
 assert.equal(p.read("requests.filter(r=>r.action==='open').length"),2);
}
''')

    def test_user_navigation_during_initial_validation_overrides_default(self):
        self.js(r'''
const p=page();p.read('active=null;holdDigest=true');const installed=p.install();await digestStarted(p);
const navigation=p.read('openDoc(b.id)');p.read('releaseDigest()');await installed.ready;await navigation;
assert.equal(p.read('active'),backend.other.id);assert.equal(p.read('rendered.includes(a.id)'),false);
''')

    def test_retained_payload_is_idempotent_and_replaces_both_task_handlers(self):
        self.js(r'''
const p=page();await p.install().ready;p.read("const savedWriting=$('writing-task').onclick,savedDownload=$('download').onclick");
assert.equal(p.install().applied,true);assert.equal(p.read("savedWriting===$('writing-task').onclick&&savedDownload===$('download').onclick"),true);
const old=p.read("$('writing-task').onclick()");await flush();
p.read('currentCatalog.workspace.revision++;currentCatalog.workspace.next_chapter++');
const latest=p.read("$('writing-task').onclick()");await flush();
p.read("tasks[1].resolve({...backend.task,chapter:backend.task.chapter+1,brief:{...backend.task.brief,revision:backend.task.brief.revision+1},prompt:'新的下一章任务'})");await latest;
p.read('tasks[0].resolve(backend.task)');await old;
assert.equal(p.read("$('review-prompt').value"),'新的下一章任务');
assert.equal(p.read("$('assistant-task-title').textContent"),'继续第'+(backend.task.chapter+1)+'章');
''')

    def test_task_context_changes_or_new_input_cancel_delayed_results(self):
        self.js(r'''
for(const change of ["currentCatalog.workspace.revision++", "a.value='生成期间继续输入'", "openSequence++", "docs.set(a.id,{...a})"]){
 const p=page();await p.install().ready;p.read("$('review-prompt').value='保留原任务'");
 const pending=p.read("$('writing-task').onclick()");await flush();p.read(change);p.read('tasks[0].resolve(backend.task)');await pending;
 assert.equal(p.read("$('review-prompt').value"),'保留原任务');
}
''')

    def test_new_review_task_supersedes_older_writing_handoff(self):
        self.js(r'''
for(const native of [false,true]){
 const p=page();if(native)await p.install().ready;else p.read(writing+review);
 const old=p.read("$('writing-task').onclick()");await flush();
 const latest=p.read("$('review-task').onclick()");await tasksStarted(p,2);
 p.read("tasks[1].resolve({prompt:'最新审稿任务',status:'等待审稿',check_note:'本地核对',lint:null})");await latest;
 p.read('tasks[0].resolve(backend.task)');await old;
 assert.equal(p.read("$('review-prompt').value"),'最新审稿任务');assert.equal(p.read("$('assistant-task-title').textContent"),'交给助手审稿');
}
''')

    def test_response_older_than_visible_formal_breakpoint_is_rejected(self):
        self.js(r'''
const p=page();p.read(writing);p.read("currentCatalog.workspace.revision++;$('review-prompt').value='保留当前任务'");
const pending=p.read("$('writing-task').onclick()");await flush();p.read('tasks[0].resolve(backend.task)');await pending;
assert.equal(p.read("$('review-prompt').value"),'保留当前任务');assert.ok(p.read("notes.some(text=>text.includes('正式断点'))"));
''')

    def test_review_waiting_for_sha_cannot_overtake_later_writing_click(self):
        self.js(r'''
const p=page();await p.install().ready;p.read('holdDigest=true');
const old=p.read("$('review-task').onclick()");await digestStarted(p);
const latest=p.read("$('writing-task').onclick()");await tasksStarted(p,1);
assert.equal(p.read('tasks[0].action'),'writing-task');
p.read('tasks[0].resolve(backend.task)');await latest;p.read('releaseDigest()');await old;
assert.equal(p.read('tasks.length'),1);assert.equal(p.read("$('review-prompt').value"),backend.task.prompt);
''')


if __name__ == '__main__':
    unittest.main()
