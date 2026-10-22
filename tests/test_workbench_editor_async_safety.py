"""Candidate readback must not replace a document opened during the save."""
import json
import importlib.util
import shutil
import subprocess
import unittest

import test_workbench as base
import test_workbench_management_editor as management


def safety_payload():
    path = base.ROOT / 'scripts/editor_safety_payload.py'
    spec = importlib.util.spec_from_file_location('async_safety_payload', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.editor_safety_payload()


class WorkbenchEditorAsyncSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        w = base.story.workbench
        original = w._editor_document(self.fixture.root, 'formal:1')
        submitted = '点击保存时的文字'
        recovery = w._editor_save(self.fixture.root, {
            **original, 'text': submitted, 'recovery_key': 'async-safety-recovery-key-1234'
        }, recovery=True)
        saved = w._editor_save(self.fixture.root, {**original, 'text': submitted})
        candidate = w._editor_document(self.fixture.root, saved['id'])
        catalog = w._editor_catalog(self.fixture.root)
        row = next(row for row in catalog['files'] if row['id'] == saved['id'])
        self.assertEqual((self.fixture.root / row['path']).read_text(), original['prefix'] + submitted)
        self.backend = {'original': original, 'other': w._editor_document(self.fixture.root, 'formal:2'),
                        'submitted': submitted, 'recovery': recovery, 'saved': saved,
                        'candidate': candidate, 'catalog_row': row}
        script = w._editor_page(w.snapshot(self.fixture.book), {}, 'async-safety').decode()
        script = script.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = script[script.index('function dirty(d)'):script.index('function pendingEdits()')]
        self.source += script[script.index('async function recover'):script.index("$('save').onclick")]
        self.source += script[script.index("window.addEventListener('beforeunload'"):script.index("window.addEventListener('hashchange'")]
        self.source += script[script.index('function documentIdentity'):script.index('function documentLocation')]
        self.source += script[script.index('function chapterNumber'):script.index('function simpleCount')]
        self.save_source = script[script.index('async function saveDocument'):script.index("$('save').onclick")]

    def js(self, case, native=False):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        harness = r'''
const assert=require('assert');
class Element {
 constructor(){this.value='';this.hidden=false;this.events={};this.textContent='';this.scrollTop=0;this.dataset={};}
 set disabled(value){this._disabled=!!value;}
 get disabled(){return !!this._disabled;}
 addEventListener(type,handler){this.events[type]=handler;}
 setAttribute(name,value){this[name]=value;}
 focus(){}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new Element());
const docs=new Map(),listeners={},window={addEventListener:(type,handler)=>listeners[type]=handler};
const document={querySelectorAll:()=>[]};
let active='formal:1',openSequence=0,reloading=false,releaseReadback,releaseNavigation,readbackStarted=false,holdNavigation=false;
const location={hash:''},crypto={randomUUID:()=> 'isolated-client-recovery-key-1234'},notes=[];
const note=value=>notes.push(value),pendingEdits=()=>{},rememberView=()=>{},
 closePanels=()=>{},previewSearch=()=>{},displayTitle=d=>d.title,versionLabel=()=>'',
 leaveComparison=()=>{},readingView=()=>{},updateChapterPicker=()=>{},restoreView=()=>{},scheduleMetrics=()=>{};
const timers=new Map();let timerID=0;
const setTimeout=(callback,delay)=>{timers.set(++timerID,{callback,delay});return timerID;},clearTimeout=id=>timers.delete(id);
const copy=value=>JSON.parse(JSON.stringify(value));
const catalog=async()=>({files:[backend.catalog_row]});
const call=async(action,data)=>{
 if(action==='recover')return copy(backend.recovery);
 if(action==='save')return copy(backend.saved);
 if(action==='open'&&data.id===backend.saved.id){
  if(!readbackStarted){readbackStarted=true;return await new Promise(done=>{releaseReadback=()=>done(copy(backend.candidate));});}
  if(holdNavigation)return await new Promise(done=>{releaseNavigation=()=>done(copy(backend.candidate));});
  return copy(backend.candidate);
 }
 if(action==='open'&&data.id===backend.other.id)return copy(backend.other);
 throw Error('unexpected '+action+' '+data.id);
};
const original={...copy(backend.original),value:backend.submitted,editing:true};docs.set(active,original);
const closeProbe=()=>{let prevented=false;const event={preventDefault:()=>{prevented=true;}};listeners.beforeunload(event);return prevented;};
const type=value=>{$('text').value=value;$('text').events.input();};
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
async function beginSave(){const saving=saveDocument(original);await flush();assert.equal(typeof releaseReadback,'function');return {saving};}
async function openCandidate(){const listing=await catalog();assert.equal(listing.files[0].id,backend.saved.id);await openDoc(listing.files[0].id);if(!docs.get(active).editing)$('edit').onclick();return docs.get(active);}
'''
        source = self.source
        installation = ''
        if native:
            source = source.replace(self.save_source, '')
            harness = harness.replace(
                "const docs=new Map(),listeners={},window={addEventListener:(type,handler)=>listeners[type]=handler};",
                "const docs=new Map(),listeners={},window=globalThis;window.top=window;window.addEventListener=(type,handler)=>listeners[type]=handler;"
            ).replace(
                "const document={querySelectorAll:()=>[]};",
                "const document={querySelectorAll:()=>[],getElementById:id=>['save-all','compare-choose','reload-source','recover-reload','list'].includes(id)?null:$(id)};"
            ).replace("const location={hash:''}", "const location={hash:'',href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/'}")
            harness = harness.replace('const saving=saveDocument(original)', "const saving=$('save').onclick()")
            installation = r'''
assert.equal(typeof saveDocument,'undefined');let legacyCalls=0;
$('save').onclick=async()=>{legacyCalls++;throw Error('legacy inline save must be replaced');};
const legacy=$('save').onclick;
const installation=eval(__PAYLOAD__);assert.equal(installation.applied,true);
assert.notStrictEqual($('save').onclick,legacy);assert.equal(typeof saveDocument,'function');
assert.equal(document.getElementById('save-all'),null);assert.equal(document.getElementById('compare-choose'),null);
'''.replace('__PAYLOAD__', json.dumps(safety_payload()))
        program = 'const backend=' + json.dumps(self.backend, ensure_ascii=False) + ';\n' + harness + source
        program += '\n(async()=>{' + installation + 'show(original);' + case
        if native:
            program += '\nassert.equal(legacyCalls,0);'
        program += '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node, '-e', program], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_late_readback_preserves_opened_candidate_edits_across_switch_and_close_guard(self):
        self.js(r'''
const {saving}=await beginSave(),opened=await openCandidate();type('新候选中刚输入的未保存文字');
opened.positions={editing:{start:3,end:5,editor:81,main:23}};const positions=opened.positions,key=opened.recoveryKey;
releaseReadback();await saving;
assert.strictEqual(docs.get(backend.saved.id),opened);assert.strictEqual(opened.positions,positions);assert.equal(opened.recoveryKey,key);
assert.equal(opened.value,'新候选中刚输入的未保存文字');assert.equal($('text').value,opened.value);
assert.equal(dirty(opened),true);assert.equal($('save').disabled,false);assert.equal(closeProbe(),true);
await openDoc(backend.other.id);assert.equal(closeProbe(),true);await openDoc(backend.saved.id);
assert.equal($('text').value,'新候选中刚输入的未保存文字');assert.equal(closeProbe(),true);assert.equal($('save').disabled,false);
assert.equal(original.value,original.text);assert.ok(notes.some(note=>note.includes('当前文件仍有未完成编辑')));
''')

    def test_independent_source_and_target_edits_remain_two_pending_buffers(self):
        self.js(r'''
const {saving}=await beginSave();type('来源在提交后继续输入的文字');
const opened=await openCandidate();type('目标候选独立输入的文字');releaseReadback();await saving;
assert.strictEqual(docs.get(backend.saved.id),opened);assert.equal(opened.value,'目标候选独立输入的文字');
assert.equal(original.value,'来源在提交后继续输入的文字');assert.equal(original.saving,false);
assert.deepEqual([...docs.values()].filter(dirty).map(d=>d.id).sort(),[original.id,opened.id].sort());
await openDoc(original.id);assert.equal($('text').value,'来源在提交后继续输入的文字');assert.equal(closeProbe(),true);
await openDoc(opened.id);assert.equal($('text').value,'目标候选独立输入的文字');assert.equal(closeProbe(),true);
''')

    def test_cached_target_keeps_its_own_save_recovery_and_reading_state(self):
        for changes in ({'saving': True}, {'needs_recovery': True}, {'editing': False}):
            with self.subTest(changes=changes):
                self.js('const changes=' + json.dumps(changes) + ';\n' + r'''
const {saving}=await beginSave(),opened=await openCandidate();Object.assign(opened,changes);
opened.positions={reading:{main:123}};const before=JSON.stringify(opened);
releaseReadback();await saving;
assert.strictEqual(docs.get(backend.saved.id),opened);assert.equal(JSON.stringify(opened),before);
assert.equal(original.value,original.text);assert.equal(original.saving,false);
''')

    def test_pending_navigation_cannot_replace_source_edits_transferred_to_new_candidate(self):
        self.js(r'''
const {saving}=await beginSave();holdNavigation=true;const navigating=openDoc(backend.saved.id);await flush();
assert.equal(typeof releaseNavigation,'function');type('保存期间新增的来源文字');
releaseReadback();await saving;const candidate=docs.get(backend.saved.id);
assert.equal(candidate.value,'保存期间新增的来源文字');assert.equal(dirty(candidate),true);
releaseNavigation();await navigating;
assert.strictEqual(docs.get(backend.saved.id),candidate);assert.equal($('text').value,'保存期间新增的来源文字');
assert.equal(closeProbe(),true);assert.equal($('save').disabled,false);
''')

    def test_native_payload_patches_legacy_inline_save_without_optional_controls_or_function(self):
        self.js(r'''
const {saving}=await beginSave(),opened=await openCandidate();type('旧服务的新候选未保存文字');
releaseReadback();await saving;
assert.strictEqual(docs.get(backend.saved.id),opened);assert.equal(dirty(opened),true);assert.equal(closeProbe(),true);
await openDoc(backend.other.id);await openDoc(opened.id);
assert.equal($('text').value,'旧服务的新候选未保存文字');assert.equal($('save').disabled,false);assert.equal(closeProbe(),true);
''', native=True)

    def test_native_payload_replaces_comparison_handler_and_ignores_cancelled_response(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        fixture = management.EditorManagementJavascriptTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = fixture.tools_source().replace(
            "const listeners={},window={addEventListener:(name,callback)=>listeners[name]=callback};",
            "const listeners={},window=globalThis;window.top=window;window.addEventListener=(name,callback)=>listeners[name]=callback;"
        )
        case = r'''
(async()=>{
 const location={href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/'},reloading=false;
 document.getElementById=id=>['list','reload-source','recover-reload'].includes(id)?null:$(id);
 const recover=async()=>{},badge=()=>{},rememberView=()=>{},show=()=>{},catalog=async()=>{};
 let resolve;call=()=>new Promise(done=>{resolve=done;});
 $('compare-choose').onclick=()=>{throw Error('legacy comparison must be replaced');};
 const installation=eval(__PAYLOAD__);assert.equal(installation.applied,true);
 const current=doc('file:current','原稿',{value:'当前未保存',editing:true});active=current.id;
 currentCatalog.files=[{id:'file:remote',title:'未缓存'}];
 const comparison=compareDialog('file:remote',current.id),running=comparison.run.onclick();await flush();
 comparison.dialog.children[2].onclick();current.value='取消后继续写下的新字';
 $('document-body').hidden=false;$('difference').hidden=true;
 resolve({id:'file:remote',text:'迟到的另稿'});await running;
 assert.equal(drawn.length,0);assert.equal($('document-body').hidden,false);assert.equal($('difference').hidden,true);
 assert.equal(current.value,'取消后继续写下的新字');assert.strictEqual(docs.get(current.id),current);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace('__PAYLOAD__', json.dumps(safety_payload()))
        result = subprocess.run([node, '-e', "const assert=require('assert');\n" + source + case],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
