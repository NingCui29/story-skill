"""Visible save status follows the current text through asynchronous saves."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base
from test_workbench_editor_async_safety import safety_payload


class WorkbenchSaveStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        w = base.story.workbench
        original = w._editor_document(self.fixture.root, 'formal:1')
        submitted = original['text'] + '手动保存时的新增文字。\n'
        recovered = w._editor_save(self.fixture.root, {**original, 'text': submitted,
                                   'recovery_key': 'save-status-recovery-1234567890'}, recovery=True)
        saved = w._editor_save(self.fixture.root, {**original, 'text': submitted})
        self.backend = {'original': original, 'submitted': submitted, 'recovery': recovered, 'saved': saved,
                        'candidate': w._editor_document(self.fixture.root, saved['id']),
                        'recovery_document': w._editor_document(self.fixture.root, recovered['id'])}
        page = w._editor_page(w.snapshot(self.fixture.book), {}, 'save-status-test').decode()
        self.page, self.script = page, page.split('<script>', 1)[1].split('</script>', 1)[0]

    def js(self, case, native=False, setup=''):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        script = self.script
        source = script[script.index('function documentIdentity('):script.index('function documentLocation(')]
        source += script[script.index('function dirty('):script.index('function show(d)')]
        source += script[script.index('function pendingEdits('):script.index('function item(')]
        source += script[script.index('async function recover('):script.index("$('save').onclick=")]
        source += script[script.index("$('save').onclick="):script.index("$('compare').onclick=")]
        source += script[script.index('function downloadDocument('):script.index('async function reloadDocuments(')]
        source += script[script.index("window.addEventListener('beforeunload'"):script.index("window.addEventListener('hashchange'")]
        harness = r"""
const assert=require('assert'),copy=value=>JSON.parse(JSON.stringify(value));
class E {
 constructor(){this.children=[];this.dataset={};this.events={};this.hidden=false;this.value='';this.textContent='';this.disabled=false;}
 append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}
 setAttribute(name,value){this[name]=value;}addEventListener(name,handler){this.events[name]=handler;}focus(){}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E()),docs=new Map(),notes=[],requests=[],listeners={},exports=[],urls=new Map();
const document={querySelectorAll:()=>[],getElementById:id=>['list','save-all','compare-choose','reload-source','recover-reload','writing-task'].includes(id)?null:$(id),createElement:()=>({click(){exports.push({name:this.download,blob:urls.get(this.href)});},append(){}})};
const window=globalThis;window.top=window;window.addEventListener=(name,handler)=>listeners[name]=handler;
const location={href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/',hash:''};
const crypto=require('crypto').webcrypto,URL={createObjectURL(blob){const id='blob:'+urls.size;urls.set(id,blob);return id;},revokeObjectURL(){}};
// Keep URL construction available for the native entry-point guard.
const NativeURL=require('url').URL;const savedCreate=URL.createObjectURL;const savedRevoke=URL.revokeObjectURL;
let active=backend.original.id,openSequence=0,reloading=false,releaseSave,releaseRecovery,releaseRead,lastSaved,notifications=0;
const mode={failRecover:false,failSave:false,failRead:false,failCatalog:false,holdSave:false,holdRecover:false,holdRead:false,sameID:false};
const original={...copy(backend.original),value:backend.submitted,editing:true};docs.set(active,original);
const note=text=>notes.push(text),rememberView=()=>{},scheduleMetrics=()=>{},leaveComparison=()=>{},closePanels=()=>{},previewSearch=()=>{};
const displayTitle=d=>d.title||d.id,documentLocation=d=>d.path||d.id,versionLabel=()=>'',downloadName=d=>d.id+'.txt';
const notifyCreatorState=()=>notifications++;
const show=d=>{active=d.id;badge();};
async function openDoc(id){active=id;badge();}
async function catalog(){if(mode.failCatalog)throw Error('目录暂不可读');}
const call=async(action,data={})=>{
 requests.push({action,...data});
 if(action==='recover'){
  if(mode.holdRecover)await new Promise(done=>{releaseRecovery=done;});
  if(mode.failRecover)throw Error('恢复目录不可写');return copy(backend.recovery);
 }
 if(action==='save'){
  if(mode.holdSave)await new Promise(done=>{releaseSave=done;});
  if(mode.failSave)throw Error('候选保存被拒绝');lastSaved=data;return {...copy(backend.saved),id:mode.sameID?data.id:backend.saved.id};
 }
 if(action==='open'){if(mode.holdRead)await new Promise(done=>{releaseRead=done;});if(mode.failRead)throw Error('候选回读暂不可用');return {...copy(backend.candidate),id:data.id,text:lastSaved?.text??backend.candidate.text};}
 throw Error('Unexpected action '+action);
};
const timers=new Map();let timer=0;
const setTimeout=(fn,delay)=>{timers.set(++timer,{fn,delay});return timer;},clearTimeout=id=>timers.delete(id);
const pendingClose=()=>{let blocked=false;listeners.beforeunload({preventDefault(){blocked=true;}});return blocked;};
async function flush(){for(let i=0;i<30;i++)await Promise.resolve();}
"""
        if native:
            harness = harness.replace("const crypto=require('crypto').webcrypto,URL={", "const crypto=require('crypto').webcrypto,downloadURL={")
            harness = harness.replace('const savedCreate=URL.createObjectURL;const savedRevoke=URL.revokeObjectURL;',
                                      'const URL=NativeURL;URL.createObjectURL=downloadURL.createObjectURL;URL.revokeObjectURL=downloadURL.revokeObjectURL;')
        installation = ''
        if native:
            installation = 'const installed=eval(' + json.dumps(safety_payload()) + ');assert.equal(installed.applied,true);await installed.ready;'
        program = 'const backend=' + json.dumps(self.backend, ensure_ascii=False) + ';\n' + harness + source + '\n' + setup
        program += '\n(async()=>{' + installation + 'badge();' + case + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node, '-'], input=program, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_recovery_success_is_distinct_from_a_manually_saved_candidate(self):
        self.js(r"""
assert.equal($('state').dataset.saveState,'unsaved');assert.equal($('state').textContent,'未保存');
await recover(original);assert.equal($('state').dataset.saveState,'recovered');
assert.ok($('state').textContent.includes('尚未保存候选'));assert.equal(pendingClose(),true);
await saveDocument(original);assert.equal($('state').dataset.saveState,'saved');
assert.equal($('state').textContent,'已保存候选');assert.equal(pendingClose(),false);
assert.equal(docs.get(active).kind,'candidate');assert.equal(original.kind,'formal');assert.ok(notifications>0);
assert.equal(requests.find(row=>row.action==='save').save_mode,'working');
""")

    def test_failed_recovery_overrides_old_success_and_retry_updates_visible_state(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.js(r"""
await recover(original);assert.equal(original.recovered,original.value);mode.failRecover=true;
await recover(original,true);assert.equal(original.recovered,original.value);
assert.equal($('state').dataset.saveState,'error');assert.ok($('state').textContent.includes('恢复副本保存失败'));
assert.equal($('save-retry').hidden,false);assert.equal($('save-status-download').hidden,false);
assert.ok(!$('pending-list').children[0].textContent.includes('已写入恢复稿'));
await $('save-status-download').onclick();assert.equal(await exports[0].blob.text(),original.prefix+original.value);
mode.failRecover=false;await $('save-retry').onclick();assert.equal($('state').dataset.saveState,'recovered');
assert.equal($('save-retry').hidden,true);assert.equal(original.recoveryError,'');assert.equal(pendingClose(),true);
""", native=native)

    def test_saving_new_input_remains_unsaved_after_captured_text_is_saved(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.js(r"""
mode.holdSave=true;const saving=$('save').onclick();await flush();assert.equal(typeof releaseSave,'function');
assert.equal($('state').dataset.saveState,'saving');assert.equal(pendingClose(),true);
original.value+='保存期间新增。';badge();assert.ok($('state').textContent.includes('新输入仍未保存'));
releaseSave();await saving;assert.equal(requests.find(row=>row.action==='save').text,backend.submitted);
assert.equal(docs.get(active).value,backend.submitted+'保存期间新增。');
assert.equal($('state').dataset.saveState,'unsaved');assert.equal($('pending-list').children.length,1);
assert.equal($('save').disabled,false);assert.equal(pendingClose(),true);
""", native=native)

    def test_save_failure_stays_visible_across_navigation_and_can_be_retried(self):
        self.js(r"""
mode.failSave=true;await saveDocument(original);assert.equal($('state').dataset.saveState,'error');
const other={id:'file:other',title:'另稿',kind:'candidate',editable:true,text:'已保存',value:'已保存'};docs.set(other.id,other);
await openDoc(other.id);assert.equal($('state').textContent,'已保存候选');assert.ok($('pending-list').children[0].textContent.includes('保存失败'));
await openDoc(original.id);assert.equal($('save-retry').hidden,false);mode.failSave=false;await $('save-retry').onclick();
assert.equal($('state').textContent,'已保存候选');assert.equal($('save-retry').hidden,true);assert.equal(pendingClose(),false);
""")

    def test_catalog_refresh_failure_never_relabels_confirmed_recovery_or_save_as_failed(self):
        self.js(r"""
mode.failCatalog=true;await recover(original);assert.equal(original.recoveryError,'');
assert.equal($('state').dataset.saveState,'recovered');assert.equal($('save-retry').hidden,true);
const saved=await saveDocument(original);assert.equal(saved,true);assert.equal($('state').textContent,'已保存候选');
assert.equal(original.saveError,'');assert.equal(pendingClose(),false);
""")

    def test_readback_failure_reports_written_candidate_without_claiming_complete_save(self):
        self.js(r"""
mode.failRead=true;await saveDocument(original);assert.equal(original.saveResultPending,true);
assert.equal($('state').textContent,'候选已写入 · 回读未确认');assert.equal($('save-retry').hidden,false);
assert.equal($('save-new-version').disabled,true);
assert.equal(original.value,backend.submitted);assert.equal(pendingClose(),true);
assert.equal($('save-retry').textContent,'重试核对');original.value+='回读失败后继续写。';mode.failRead=false;
await $('save-retry').onclick();assert.equal(requests.filter(row=>row.action==='save').length,1);
assert.equal(docs.get(active).value,backend.submitted+'回读失败后继续写。');assert.equal($('state').dataset.saveState,'unsaved');
assert.equal(pendingClose(),true);
""")

    def test_hidden_saves_and_pending_recovery_records_remain_visible_and_protected(self):
        self.js(r"""
original.value=original.text;const saving={id:'file:saving',title:'保存中稿件',editable:true,text:'同文',value:'同文',saving:true,savingText:'同文'};
const pending={id:'pending:record',title:'待核对稿件',editable:true,text:'已保留',value:'已保留',needs_recovery:true};
docs.set(saving.id,saving);docs.set(pending.id,pending);badge();
assert.equal($('pending-list').children.length,2);assert.equal(pendingClose(),true);
assert.ok($('pending-list').children.some(row=>row.textContent.includes('保存中')));
assert.ok($('pending-list').children.some(row=>row.textContent.includes('保存结果待核对')));
""")

    def test_unchanged_recovery_can_be_saved_as_a_new_candidate(self):
        self.js(r"""
const recovery={...copy(backend.recovery_document),value:backend.recovery_document.text,editing:false};docs.set(recovery.id,recovery);
await openDoc(recovery.id);assert.equal($('state').dataset.saveState,'recovered');assert.equal($('save').disabled,false);
await $('save').onclick();assert.equal($('state').textContent,'已保存候选');assert.equal(docs.get(active).kind,'candidate');
assert.equal(requests.find(row=>row.action==='save').id,recovery.id);
assert.equal(requests.find(row=>row.action==='save').save_mode,'version');
""")

    def test_explicit_new_version_accepts_unchanged_text_and_is_forwarded_by_native_wrapper(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.js(r"""
const candidate={...copy(backend.candidate),value:backend.candidate.text};docs.set(candidate.id,candidate);
await openDoc(candidate.id);assert.equal($('save').disabled,true);assert.equal($('save-new-version').disabled,false);
await $('save-new-version').onclick();const request=requests.find(row=>row.action==='save');
assert.equal(request.save_mode,'version');assert.equal(request.text,candidate.text);
assert.equal($('state').textContent,'已保存候选');
""", native=native)

    def test_working_save_same_identity_keeps_new_input_during_readback(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.js(r"""
const candidate={...copy(backend.candidate),value:backend.candidate.text+'继续修改。',editing:true};docs.set(candidate.id,candidate);
await openDoc(candidate.id);mode.sameID=true;mode.holdRead=true;
const submitted=candidate.value,saving=saveDocument(candidate);await flush();assert.equal(typeof releaseRead,'function');
candidate.value+='回读期间新增。';badge();assert.ok($('state').textContent.includes('新输入仍未保存'));
releaseRead();await saving;const current=docs.get(candidate.id);
assert.equal(active,candidate.id);assert.equal(current.text,submitted);assert.equal(current.value,submitted+'回读期间新增。');
assert.equal($('state').dataset.saveState,'unsaved');assert.equal(pendingClose(),true);
assert.equal(requests.find(row=>row.action==='save').save_mode,'working');
""", native=native)

    def test_retained_version_is_labeled_without_claiming_formal_adoption(self):
        self.js(r"""
const checkpoint={...copy(backend.candidate),checkpoint:true,value:backend.candidate.text};docs.set(checkpoint.id,checkpoint);await openDoc(checkpoint.id);
assert.equal($('document-role').textContent,'保留版本');assert.equal($('state').textContent,'已保存候选 · 保留版本');
assert.ok($('save').title.includes('另建工作稿'));assert.ok(!$('state').textContent.includes('已采用'));
""")

    def test_failed_explicit_version_of_unchanged_text_remains_retryable(self):
        self.js(r"""
const candidate={...copy(backend.candidate),value:backend.candidate.text};docs.set(candidate.id,candidate);await openDoc(candidate.id);
mode.failSave=true;await $('save-new-version').onclick();assert.equal($('state').dataset.saveState,'error');
assert.equal($('save-retry').hidden,false);assert.equal($('pending-list').children.length,2);
mode.failSave=false;await $('save-retry').onclick();const writes=requests.filter(row=>row.action==='save');
assert.equal(writes.length,2);assert.ok(writes.every(row=>row.save_mode==='version'));assert.equal($('state').textContent,'已保存候选');
""")

    def test_failed_forced_recovery_cannot_reload_using_an_old_success_marker(self):
        reload_source = self.script[self.script.index('async function reloadDocuments('):self.script.index("$('refresh').onclick=")]
        self.js('eval(' + json.dumps(reload_source) + ');\n' + r"""
await recover(original);mode.failRecover=true;const before=original.value;
await reloadDocuments(true);assert.equal(original.value,before);assert.strictEqual(docs.get(original.id),original);
assert.ok(original.recoveryError);assert.equal(requests.filter(row=>row.action==='open').length,0);
assert.ok(notes.some(text=>text.includes('已停止重新载入')));assert.equal(pendingClose(),true);
""")

    def test_status_and_failure_actions_stay_outside_folded_version_navigation(self):
        self.assertIn('<p id="state" role="status" aria-live="polite"></p>', self.page)
        self.assertIn('id="save-retry" class="save-state-action" hidden', self.page)
        self.assertIn('id="save-status-download" class="save-state-action" hidden', self.page)
        self.assertIn('<summary>同章版本</summary>', self.page)

    def test_native_payload_upgrades_version_groups_when_the_old_page_has_controls(self):
        setup = r"""
function chapterNumber(d){return d.context?.chapter||d.chapter||null;}
function filePurpose(d){return d.kind==='plan'?'章计划':'材料';}
function updateChapterPicker(){throw Error('Old version picker must be replaced');}
document.createElement=()=>new E();
const checkpoint={...copy(backend.candidate),checkpoint:true,value:backend.candidate.text};
docs.set(checkpoint.id,checkpoint);active=checkpoint.id;
const currentCatalog={workspace:{groups:[{chapter:1,items:[backend.original,checkpoint]}]}};
"""
        self.js(r"""
updateChapterPicker();const groups=$('chapter-version').children;
assert.deepEqual(groups.map(group=>group.label),['正式稿','保留版本']);
const current=groups[1].children[0];assert.equal(current.selected,true);assert.ok(current.textContent.startsWith('当前 · 保留版本'));
assert.equal($('state').textContent,'已保存候选 · 保留版本');
""", native=True, setup=setup)


if __name__ == '__main__':
    unittest.main()
