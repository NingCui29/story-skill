"""Book-wide blockers and strict cleanup handoffs stay inside the workbench."""
import unittest
from unittest.mock import patch

from tests import test_workbench_platform_draft_ui as base
from tests import test_workbench_management as management


class DraftCleanupUITests(unittest.TestCase):
    js = base.PlatformDraftUITests.js
    fixture = r'''
const dialogs=[];Element.prototype.showModal=function(){this.open=true;dialogs.push(this);};
Element.prototype.select=function(){this.selected=true;};
let clipboard='';Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>{clipboard=text;}}}});
const oldTask=(extra={})=>({book_id:'book-one',request_id:'a'.repeat(32),execution_host:'qoder-cn',plan_id:'old-plan',profile_id:'old-profile',mode:'inspect_drafts',chapters:[1,2],created_at:'2026-10-29T11:00:00Z',state:'interrupted',stage:'waiting_host',done:true,execution_active:true,cleanup_pending:true,cleanup_complete:false,cleanup_status:'unverified',cleanup_recovery_eligible:true,cleanup_recovery_reason:'控制器已结束，仍须核验自建标签',assistant_context:{private:'DO-NOT-DISPLAY'},...extra});
let old=[oldTask()],recoveries=[];
blockerHandler=async()=>({ok:true,book_id:'book-one',blockers:old,recoveries});
const recovery=(requestId,extra={})=>({ok:true,book_id:'book-one',request_id:requestId,recovery_id:'c'.repeat(32),recovery_mode:'reconciled_absent',state:'prepared',expires_at:new Date(Date.now()+300000).toISOString(),handoff_prompt:'Synthetic Terra cleanup handoff. No execution yet.',...extra});
const defaultHandler=handler;handler=async(action,payload)=>action.endsWith('/assistant-reconcile-prepare')?recovery(payload.request_id):defaultHandler(action,payload);
const calls=name=>requests.filter(row=>row.action.endsWith('/'+name));
const recoverButton=p=>p.blockerList.children[0].children.find(row=>row.tagName==='button');
const confirmButton=()=>dialogs.at(-1).children[0].children.find(row=>row.tagName==='button');
'''

    def test_default_codex_entry_discovers_old_task_and_refresh_preserves_intent(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(platformDraftHost(p),'codex');assert.equal(p.plan.value,'plan-1');assert.equal(p.run.disabled,true);
assert.ok(textTree(p.blockerArea).includes('Qoder CN'));assert.ok(textTree(p.blockerArea).includes('2026-10-29'));assert.ok(textTree(p.blockerArea).includes('第1—2章'));
assert.ok(textTree(p.blockerArea).includes('a'.repeat(32)));assert.ok(!textTree(p.blockerArea).includes('DO-NOT-DISPLAY'));
await p.refresh.onclick();await p.run.onclick();assert.equal(p.assistantBlockers.length,1);assert.equal(p.run.disabled,true);assert.equal(calls('start').length,0);
assert.equal(platformDraftHost(p),'codex');assert.equal(p.plan.value,'plan-1');assert.ok(p.blockerStatus.textContent.includes('已保留'));
''')

    def test_pre_start_discovery_blocks_newly_appearing_task_without_auto_execution(self):
        self.js(self.fixture + r'''
old=[];await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.run.disabled,false);
old=[oldTask()];await p.run.onclick();assert.equal(calls('start').length,0);assert.equal(p.run.disabled,true);assert.equal(p.plan.value,'plan-1');assert.equal(platformDraftHost(p),'codex');
''')

    def test_real_readonly_inspection_timeout_shape_is_not_an_unknown_write(self):
        self.js(self.fixture + r'''
// Anonymous version of the real failed inspection: five unchanged rows and
// one read timeout. No account, book path, title, prose or browser capability.
const changes=Array.from({length:5},(_,index)=>({chapter:index+1,action:'unchanged',reason:null}));changes.push({chapter:6,action:'blocked',reason:'platform_chrome_request_timeout'});
old=[oldTask({state:'inspection_blocked',stage:'cleanup_pending',mode:'inspect_drafts',done:true,execution_active:true,remote_accessed:true,cleanup_pending:true,cleanup_complete:false,cleanup_status:'pending',cleanup_recovery_eligible:true,cleanup_reset_eligible:true,error:null,chapters:[],total_chapters:30,result:{can_execute:false,changes}})];
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(draftCleanupNeedsReadback(old[0]),true);assert.equal(draftCleanupUnknownWrite(old[0]),false);
assert.equal(recoverButton(p).disabled,false);assert.equal(p.blockerList.children[0].children.find(row=>row.textContent==='保存后结束旧浏览器会话').disabled,false);
assert.ok(textTree(p.blockerArea).includes('浏览器收尾待确认'));assert.ok(!textTree(p.blockerArea).includes('保存结果未知'));assert.equal(p.run.disabled,true);
old=[{...old[0],mode:'sync_drafts'}];await p.blockerRefresh.onclick();assert.equal(recoverButton(p).disabled,true);assert.ok(textTree(p.blockerArea).includes('保存结果未知'));
''')

    def test_pointer_click_survives_passive_poll_without_replacing_the_target(self):
        self.js(self.fixture + r'''
// Model native click dispatch: a detached pointerdown target receives no click.
Element.prototype.append=function(...rows){for(const row of rows){row.parentElement=this;this.children.push(row);}};
Element.prototype.replaceChildren=function(...rows){for(const row of this.children)row.parentElement=null;this.children=[];this.append(...rows);};
Object.defineProperty(Element.prototype,'isConnected',{get(){return this===document.body||!!this.parentElement?.isConnected;}});
const legacy=oldTask({state:'stopped',stage:'finished',error:'platform_draft_stop_requested',legacy_service_blocker:true,not_started_archive_eligible:true,execution_active:false,cleanup_pending:false,cleanup_complete:true,cleanup_status:'not_started',remote_accessed:false,cleanup_recovery_eligible:false});
const packet=()=>({ok:true,book_id:'book-one',blockers:[],legacy_blockers:[structuredClone(legacy)],recoveries:[]});blockerHandler=async()=>packet();
await platformDraftOpen();const p=platformDraftPanel;document.body.append(p.dialog);
const down=p.blockerList.children[0].children.find(row=>row.textContent==='原样归档未启动的旧任务');assert.equal(down.disabled,false);assert.equal(down.isConnected,true);
let finish;blockerHandler=async()=>new Promise(resolve=>{finish=resolve;});const poll=timers.get(p.blockersTimer).fn();
assert.equal(p.blockersLoading,true);assert.equal(p.blockerRefresh.disabled,false);assert.equal(down.isConnected,true);assert.equal(down.disabled,false);
if(down.isConnected&&!down.disabled)down.onclick();assert.equal(p.blockerDialog?.open,true);assert.ok(textTree(dialogs.at(-1)).includes('确认原样归档'));assert.equal(calls('assistant-archive-never-started').length,0);
finish(packet());await poll;assert.equal(down.isConnected,true);assert.equal(p.blockerDialog?.open,true);assert.equal([...timers.values()].filter(row=>row.delay===2500).length,0);
p.blockerDialog.close();assert.equal([...timers.values()].filter(row=>row.delay===2500).length,1);
// The stable node closes over an older object; lookup must use current identity.
down.onclick();assert.equal(p.blockerDialog?.open,true);assert.equal(calls('assistant-archive-never-started').length,0);p.dialog.close();assert.equal(p.blockerDialog,null);
''')

    def test_native_deferred_dialog_close_does_not_leak_poll_or_mutate_closed_parent(self):
        self.js(self.fixture + r'''
const closeEvents=[];Element.prototype.close=function(){this.open=false;closeEvents.push(()=>{for(const fn of this.listeners.close||[])fn();});};
const flushClose=()=>{while(closeEvents.length)closeEvents.shift()();};
await platformDraftOpen();const p=platformDraftPanel;recoverButton(p).onclick();const child=p.blockerDialog;assert.ok(child.open);assert.equal([...timers.values()].filter(row=>row.delay===2500).length,0);
await confirmButton().onclick();assert.equal(child.open,false);assert.equal(calls('assistant-reconcile-prepare').length,1);flushClose();assert.equal(p.blockerDialog,null);assert.equal([...timers.values()].filter(row=>row.delay===2500).length,1);
recoverButton(p).onclick();const abandoned=p.blockerDialog;p.dialog.close();flushClose();assert.equal(abandoned.open,false);assert.equal(p.blockerDialog,null);assert.equal(platformDraftPanel,null);assert.equal(timers.size,0);assert.equal(calls('assistant-reconcile-prepare').length,1);
''')

    def test_busy_race_loads_blocker_and_catalog_refresh_keeps_it_visible(self):
        self.js(self.fixture + r'''
old=[];await platformDraftOpen();const p=platformDraftPanel;
handler=async(action,payload)=>{if(action.endsWith('/start')){old=[oldTask()];throw Object.assign(Error('当前作品已有助手任务'),{code:'platform_draft_busy'});}return defaultHandler(action,payload);};
await p.run.onclick();assert.equal(calls('start').length,1);assert.equal(p.assistantBlockers.length,1);assert.equal(p.run.disabled,true);
await p.refresh.onclick();assert.equal(p.assistantBlockers.length,1);assert.ok(!p.blockerArea.hidden);assert.equal(calls('start').length,1);assert.equal(platformDraftHost(p),'codex');
''')

    def test_recovery_confirmation_copy_poll_and_multiple_blockers_never_start_work(self):
        self.js(self.fixture + r'''
old.push(oldTask({request_id:'b'.repeat(32)}));await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);
recoverButton(p).onclick();assert.equal(calls('assistant-reconcile-prepare').length,0);assert.ok(textTree(dialogs.at(-1)).includes('不会自动开始'));
await confirmButton().onclick();assert.deepEqual(calls('assistant-reconcile-prepare')[0].payload,{request_id:'a'.repeat(32),confirmed:true,recovery_mode:'reconciled_absent'});
await p.blockerCopy.onclick();assert.equal(clipboard,p.blockerPrompt.value);assert.equal(calls('start').length,0);assert.equal(p.run.disabled,true);
old=old.slice(1);recoveries=[{recovery_id:'c'.repeat(32),request_id:'a'.repeat(32),recovery_mode:'reconciled_absent',state:'completed'}];
await timers.get(p.blockersTimer).fn();assert.equal(p.assistantBlockers.length,1);assert.equal(p.run.disabled,true);assert.ok(p.blockerRecoveryNote.textContent.includes('收尾已核验，原检查仍未完成'));
old=[];await p.blockerRefresh.onclick();assert.equal(p.assistantBlockers.length,0);assert.equal(p.run.disabled,false);assert.equal(calls('start').length,0);assert.equal(calls('assistant-reconcile-complete').length,0);
assert.equal(p.plan.value,'plan-1');assert.equal(platformDraftHost(p),'codex');assert.equal(JSON.stringify([...docs]),before);
''')

    def test_wrong_book_or_failed_refresh_retains_known_blocker_until_fresh_proof(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
blockerHandler=async()=>({ok:true,book_id:'another-book',blockers:[],recoveries:[]});await p.blockerRefresh.onclick();
assert.equal(p.assistantBlockers.length,1);assert.equal(p.blockersUncertain,true);assert.equal(p.run.disabled,true);
blockerHandler=async()=>({ok:true,book_id:'book-one',blockers:[],recoveries:{}});await p.blockerRefresh.onclick();assert.equal(p.assistantBlockers.length,1);assert.equal(p.run.disabled,true);
blockerHandler=async()=>{throw Error('offline');};await p.refresh.onclick();assert.equal(p.assistantBlockers.length,1);assert.equal(p.run.disabled,true);
blockerHandler=async()=>({ok:true,book_id:'book-one',blockers:[],recoveries:[]});await p.blockerRefresh.onclick();assert.equal(p.assistantBlockers.length,0);assert.equal(p.blockersUncertain,false);assert.ok(!p.blockerStatus.textContent.includes('失败'));
''')

    def test_expired_or_wrong_prepare_receipts_never_show_actionable_handoff(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
for(const extra of [{expires_at:new Date(Date.now()-1000).toISOString()},{book_id:'wrong-book'}]){
 handler=async(action,payload)=>action.endsWith('/assistant-reconcile-prepare')?recovery(payload.request_id,extra):defaultHandler(action,payload);
 recoverButton(p).onclick();await confirmButton().onclick();assert.ok(p.blockerPrompt.hidden);assert.equal(p.run.disabled,true);assert.ok(p.blockerMessage.includes('未能准备'));
}
handler=async(action,payload)=>action.endsWith('/assistant-reconcile-prepare')?recovery(payload.request_id):defaultHandler(action,payload);
recoverButton(p).onclick();await confirmButton().onclick();p.blockerRecovery.expires_at=new Date(Date.now()-1000).toISOString();await p.blockerCopy.onclick();
assert.ok(p.blockerPrompt.hidden);assert.equal(clipboard,'');assert.ok(p.blockerRecoveryNote.textContent.includes('已过期'));assert.equal(calls('start').length,0);
''')

    def test_unknown_write_cannot_use_readonly_cleanup_even_with_incorrect_eligibility(self):
        self.js(self.fixture + r'''
old=[oldTask({mode:'sync_drafts',state:'unknown',result:{results:[{chapter:1,state:'unknown'}]}})];await platformDraftOpen();const p=platformDraftPanel;
assert.equal(recoverButton(p).disabled,true);recoverButton(p).onclick();assert.equal(calls('assistant-reconcile-prepare').length,0);assert.ok(textTree(p.blockerArea).includes('原批次只读对账'));assert.equal(p.run.disabled,true);
''')

    def test_close_discards_late_response_and_all_cleanup_poll_timers(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;let finish;blockerHandler=async()=>new Promise(resolve=>{finish=resolve;});
const pending=p.blockerRefresh.onclick();p.dialog.close();finish({ok:true,book_id:'book-one',blockers:[],recoveries:[]});await pending;
assert.equal(p.assistantBlockers.length,1);assert.equal(platformDraftPanel,null);assert.equal(timers.size,0);assert.equal(calls('start').length,0);
''')

    def test_external_reset_requires_saved_content_and_explicit_chrome_exit_authorization(self):
        self.js(self.fixture + r'''
old=[oldTask({cleanup_reset_eligible:true,cleanup_reset_reason:'可在保存后结束旧会话'})];
handler=async(action,payload)=>action.endsWith('/assistant-reconcile-prepare')?recovery(payload.request_id,{recovery_mode:payload.recovery_mode}):defaultHandler(action,payload);
await platformDraftOpen();const p=platformDraftPanel,reset=p.blockerList.children[0].children.find(row=>row.textContent==='保存后结束旧浏览器会话');reset.onclick();
const dialog=dialogs.at(-1),checks=dialog.children[0].children.filter(row=>row.tagName==='label').map(row=>row.children[0]),confirm=confirmButton();
assert.equal(checks.length,2);assert.equal(confirm.disabled,true);await confirm.onclick();assert.equal(calls('assistant-reconcile-prepare').length,0);
checks[0].checked=true;checks[0].onchange();assert.equal(confirm.disabled,true);await confirm.onclick();assert.equal(calls('assistant-reconcile-prepare').length,0);
checks[1].checked=true;checks[1].onchange();assert.equal(confirm.disabled,false);await confirm.onclick();
const payload=calls('assistant-reconcile-prepare')[0].payload;assert.equal(payload.recovery_mode,'external_session_reset');assert.ok(payload.authorization_text.includes('编辑内容已保存'));assert.ok(payload.authorization_text.includes('Chrome 的全部窗口'));assert.ok(payload.authorization_text.includes('官方 Node REPL 内核'));
assert.ok(p.blockerRecoveryNote.textContent.includes('尚未结束或隔离'));assert.equal(calls('start').length,0);assert.equal(platformDraftHost(p),'codex');
old=[];recoveries=[{recovery_id:'c'.repeat(32),request_id:'a'.repeat(32),recovery_mode:'external_session_reset',state:'completed'}];await p.blockerRefresh.onclick();
assert.ok(p.blockerRecoveryNote.textContent.includes('旧会话已隔离'));assert.ok(!p.blockerRecoveryNote.textContent.includes('收尾已核验'));assert.ok(p.blockerRecoveryNote.textContent.includes('未将原任务标为完成'));assert.equal(calls('start').length,0);
''')

    def test_unknown_write_cannot_use_external_reset_or_forge_prepared_mode(self):
        self.js(self.fixture + r'''
old=[oldTask({mode:'sync_drafts',state:'unknown',cleanup_reset_eligible:true})];await platformDraftOpen();const p=platformDraftPanel;
const reset=p.blockerList.children[0].children.find(row=>row.textContent==='保存后结束旧浏览器会话');assert.equal(reset.disabled,true);reset.onclick();assert.equal(calls('assistant-reconcile-prepare').length,0);
old=[oldTask()];await p.blockerRefresh.onclick();handler=async(action,payload)=>action.endsWith('/assistant-reconcile-prepare')?recovery(payload.request_id,{recovery_mode:'external_session_reset'}):defaultHandler(action,payload);
recoverButton(p).onclick();await confirmButton().onclick();assert.ok(p.blockerPrompt.hidden);assert.equal(p.run.disabled,true);assert.ok(p.blockerMessage.includes('身份或有效期'));
''')

    def test_never_started_legacy_task_requires_explicit_byte_preserving_archive(self):
        self.js(self.fixture + r'''
const legacy=oldTask({legacy_service_blocker:true,not_started_archive_eligible:true,not_started_archive_reason:'已严格证实从未启动',execution_active:false,cleanup_pending:false,cleanup_complete:true,remote_accessed:false,cleanup_recovery_eligible:false});
old=[];let archived=false;blockerHandler=async()=>({ok:true,book_id:'book-one',blockers:old,legacy_blockers:archived?[]:[legacy],recoveries:[]});
handler=async(action,payload)=>{if(action.endsWith('/assistant-archive-never-started')){archived=true;return {ok:true,book_id:'book-one',request_id:payload.request_id,state:'archived_never_started'};}return defaultHandler(action,payload);};
await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.run.disabled,true);const archive=p.blockerList.children[0].children.find(row=>row.textContent==='原样归档未启动的旧任务');assert.equal(archive.disabled,false);
archive.onclick();assert.equal(calls('assistant-archive-never-started').length,0);assert.ok(textTree(dialogs.at(-1)).includes('原样保留'));await confirmButton().onclick();
assert.deepEqual(calls('assistant-archive-never-started')[0].payload,{request_id:legacy.request_id,confirmed:true});assert.equal(p.assistantBlockers.length,0);assert.equal(calls('start').length,0);assert.ok(p.blockerMessage.includes('保留历史'));
''')

    def test_shelf_busy_service_uses_shared_recovery_without_opening_or_stopping_editor(self):
        source = base.base.story.workbench._library_management_script()
        cleanup = source[source.index('function managementClearCleanup('):source.index('function openManager(')]
        source = cleanup + source[source.index('async function showBookServiceCleanup('):source.index('function showBookBackup(')]
        self.js(self.fixture + r'''
let managerGeneration=0,managerCleanup=null;const manager=new Element('dialog'),managerBody=new Element('div'),actions=new Map();
const originalShowModal=Element.prototype.showModal;Element.prototype.showModal=function(){if(this.className==='draft-cleanup-dialog')assert.ok(this.children[1].children.some(row=>row.tagName==='button'&&row.textContent.startsWith('确认')),'Fill the confirmation before placing it in the native modal layer');originalShowModal.call(this);};
const bookName=book=>book.title,openManager=()=>{if(managerCleanup)managementClearCleanup();manager.showModal();return ++managerGeneration;};
const managementText=text=>managerBody.append(new Element('p'));
const managementField=()=>{const input=new Element('input');managerBody.append(input);return input;};
const managementButton=(name,fn)=>actions.set(name,fn),managementRequest=(action,payload)=>call(action,payload);
const openRecoveryBook=()=>{throw Error('No editor may be opened');};
let complete=false;handler=async(action,payload)=>{
 if(action==='book-service')return {running:true,outdated:true,platform_activity:{supported:true,busy:!complete},presence:{no_pages:true,counts:{unknown:0,pending_pages:0}}};
 if(action==='book-draft-blockers')return {ok:true,book_id:'book-one',blockers:complete?[]:old,recoveries:complete?[{recovery_id:'c'.repeat(32),request_id:old[0].request_id,recovery_mode:'reconciled_absent',state:'completed'}]:[]};
 if(action==='book-draft-reconcile-prepare')return recovery(payload.request_id);
 throw Error('Forbidden request '+action);
};
''' + source + r'''
manager.addEventListener('close',managementClearCleanup);
await showBookService({key:'book-key',book_id:'book-one',title:'合成书'});const p=managerCleanup;
assert.equal(p.assistantBlockers.length,1);assert.ok(!p.blockerArea.hidden);assert.equal(recoverButton(p).disabled,false);assert.ok(p.blockerStatus.textContent.includes('不启动编辑服务'));
recoverButton(p).onclick();const content=dialogs.at(-1).children[1],confirm=content.children.find(row=>row.tagName==='button');await confirm.onclick();
const prepared=requests.find(row=>row.action==='book-draft-reconcile-prepare').payload;assert.equal(prepared.key,'book-key');assert.equal(prepared.expected_book_id,'book-one');assert.equal(prepared.request_id,'a'.repeat(32));
complete=true;await p.blockerRefresh.onclick();assert.equal(p.assistantBlockers.length,0);assert.ok(p.blockerRecoveryNote.textContent.includes('收尾已核验'));
await showBookService({key:'book-key',book_id:'book-one',title:'合成书'});assert.ok(managerCleanup.blockerRecoveryNote.textContent.includes('收尾已核验'));assert.ok(managerCleanup.blockerPrompt.hidden);
const oldPanel=managerCleanup,previous=handler;let finish,delay=true;handler=async(action,payload)=>{if(action==='book-draft-blockers'&&delay){delay=false;return new Promise(resolve=>{finish=resolve;});}return previous(action,payload);};
const late=oldPanel.blockerRefresh.onclick();await showBookService({key:'book-key',book_id:'book-one',title:'重新打开'});const current=managerCleanup;
finish({ok:true,book_id:'wrong-book',blockers:[],recoveries:[]});await late;assert.equal(current.blockersUncertain,false);assert.ok(current.blockerRecoveryNote.textContent.includes('收尾已核验'));
complete=false;await current.blockerRefresh.onclick();const timer=current.blockersTimer;assert.ok(timers.has(timer));recoverButton(current).onclick();const nested=current.blockerDialog;assert.ok(nested.open);assert.equal(timers.has(timer),false);manager.close();assert.equal(managerCleanup,null);assert.equal(nested.open,false);assert.equal(current.blockerDialog,null);assert.equal(timers.has(timer),false);
assert.ok(!requests.some(row=>['open-book','stop-book','start','book-draft-reconcile-complete'].includes(row.action)));clearTimeout(p.blockersTimer);
''')


class DraftCleanupHTTPTests(base.PlatformDraftHTTPTests):
    def test_shared_cleanup_is_injected_exactly_once_in_each_full_page(self):
        shelf = self.w._library_page('fixture').decode()
        editor = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'fixture').decode()
        for page in (shelf, editor):
            self.assertEqual(page.count('function platformDraftBlockersFields('), 1)
            self.assertEqual(page.count('function platformDraftBlockerConfirm('), 1)
            self.assertEqual(page.count('const draftCleanupHostNames='), 1)

    def test_external_reset_authorization_reaches_core_without_converting_absence_observations(self):
        backend = base.base.story.platform_drafts
        result = {'ok': True, 'state': 'prepared', 'recovery_mode': 'external_session_reset',
                  'handoff_prompt': 'Strict reset handoff.'}
        with patch.object(backend, 'workbench_assistant_reconcile_prepare', create=True, return_value=result) as mocked:
            payload = {'request_id': 'a' * 32, 'confirmed': True, 'recovery_mode': 'external_session_reset',
                       'authorization_text': '已保存并授权正常退出 Chrome 全部窗口及重置官方内核。'}
            code, returned = self.post('assistant-reconcile-prepare', payload)
            self.assertEqual(code, 200, returned)
            self.assertEqual(mocked.call_args.kwargs['authorization_text'], payload['authorization_text'])
            self.assertEqual(mocked.call_args.kwargs['recovery_mode'], 'external_session_reset')
            self.assertIn('退出 Chrome 与官方内核重置的真实回执', returned['handoff_prompt'])
            self.assertNotIn('完整标签清单', returned['handoff_prompt'])
        with patch.object(backend, 'workbench_assistant_reconcile_prepare', create=True) as mocked:
            self.assertEqual(self.post('assistant-reconcile-prepare', {**payload, 'recovery_mode': []})[0], 409)
            mocked.assert_not_called()

    def test_cleanup_routes_bind_server_book_and_append_ephemeral_callback(self):
        backend = base.base.story.platform_drafts
        book_id = self.fixture.book.meta('id')
        with patch.object(backend, 'workbench_assistant_blockers', create=True,
                          return_value={'ok': True, 'book_id': book_id, 'blockers': [], 'recoveries': []}) as mocked:
            self.assertEqual(self.post('assistant-blockers')[0], 200)
            self.assertEqual(mocked.call_args.kwargs, {'expected_book_id': book_id})
        result = {'ok': True, 'book_id': book_id, 'state': 'prepared', 'handoff_prompt': 'Fixed private handoff.'}
        with patch.object(backend, 'workbench_assistant_reconcile_prepare', create=True, return_value=result) as mocked:
            code, returned = self.post('assistant-reconcile-prepare', {'request_id': 'a' * 32, 'confirmed': True})
            self.assertEqual(code, 200, returned)
            self.assertEqual(mocked.call_args.kwargs, {'expected_book_id': book_id, 'confirmed': True,
                                                      'recovery_mode': 'reconciled_absent', 'authorization_text': None})
            self.assertIn('/api/platform-drafts/assistant-reconcile-complete', returned['handoff_prompt'])
            self.assertIn('X-Story-Token: ' + self.token, returned['handoff_prompt'])
            self.assertNotIn(self.token, result['handoff_prompt'])
        observation = {'schema': 1, 'tabs': []}
        with patch.object(backend, 'workbench_assistant_reconcile_complete', create=True, return_value={'ok': True}) as mocked:
            self.assertEqual(self.post('assistant-reconcile-complete', {'recovery_id': 'b' * 32, 'observation': observation})[0], 200)
            self.assertEqual(mocked.call_args.args, (self.fixture.root, 'b' * 32, observation))
            self.assertEqual(mocked.call_args.kwargs, {'expected_book_id': book_id})

    def test_cleanup_routes_reject_identity_paths_flags_and_csrf_before_backend(self):
        backend = base.base.story.platform_drafts
        cases = [
            ('assistant-blockers', 'workbench_assistant_blockers', {}, {'book_id': 'other'}),
            ('assistant-reconcile-prepare', 'workbench_assistant_reconcile_prepare',
             {'request_id': 'a' * 32, 'confirmed': True}, {'request_id': 'a' * 32, 'confirmed': 'true'}),
            ('assistant-reconcile-complete', 'workbench_assistant_reconcile_complete',
             {'recovery_id': 'b' * 32, 'observation': {}}, {'recovery_id': 'b' * 32, 'evidence_path': '/tmp/untrusted', 'cleanup_complete': True}),
        ]
        for action, method, valid, invalid in cases:
            with self.subTest(action=action), patch.object(backend, method, create=True) as mocked:
                self.assertEqual(self.post(action, valid, **{'X-Story-Token': 'wrong'})[0], 403)
                self.assertEqual(self.post(action, valid, Origin='https://untrusted.example')[0], 403)
                self.assertEqual(self.post(action, invalid)[0], 409)
                mocked.assert_not_called()


class LibraryCleanupHTTPTests(unittest.TestCase):
    setUp = management.WorkbenchManagementHTTPTests.setUp
    serve = management.WorkbenchManagementHTTPTests.serve
    post = management.WorkbenchManagementHTTPTests.post
    create = management.WorkbenchManagementHTTPTests.create

    def test_shelf_routes_use_registered_identity_and_share_recovery_adapter_without_editor(self):
        root, book = self.create()
        target = {'key': book['key'], 'expected_book_id': book['book_id']}
        backend = base.base.story.platform_drafts
        before = (root / '.story/state.sqlite3').read_bytes()
        with patch.object(self.w, 'editor_server') as editor, patch.object(self.w, '_service_request') as service:
            with patch.object(backend, 'workbench_assistant_blockers', create=True,
                              return_value={'ok': True, 'book_id': book['book_id'], 'blockers': [], 'recoveries': []}) as mocked:
                self.assertEqual(self.post('book-draft-blockers', target)[0], 200)
                self.assertEqual(mocked.call_args.args, (str(root),))
                self.assertEqual(mocked.call_args.kwargs, {'expected_book_id': book['book_id']})
            with patch.object(backend, 'workbench_assistant_reconcile_prepare', create=True,
                              return_value={'ok': True, 'state': 'prepared', 'handoff_prompt': 'Strict recovery.'}) as mocked:
                code, receipt = self.post('book-draft-reconcile-prepare', {**target, 'request_id': 'a' * 32, 'confirmed': True})
                self.assertEqual(code, 200, receipt)
                prompt = receipt['handoff_prompt']
                self.assertIn('/api/book-draft-reconcile-complete', prompt)
                self.assertIn(book['key'], prompt)
                self.assertIn(book['book_id'], prompt)
                self.assertIn('X-Story-Token: ' + self.shelf.library_token, prompt)
                self.assertEqual(mocked.call_args.kwargs['expected_book_id'], book['book_id'])
            with patch.object(backend, 'workbench_assistant_reconcile_complete', create=True,
                              return_value={'ok': True, 'state': 'completed', 'book_id': book['book_id'], 'blockers': []}) as mocked:
                observation = {'schema': 1, 'tabs': []}
                code, receipt = self.post('book-draft-reconcile-complete', {**target, 'recovery_id': 'c' * 32, 'observation': observation})
                self.assertEqual(code, 200, receipt)
                self.assertEqual(receipt['state'], 'completed')
                self.assertEqual(mocked.call_args.args, (str(root), 'c' * 32, observation))
            with patch.object(backend, 'workbench_assistant_archive_never_started', create=True,
                              return_value={'ok': True, 'state': 'archived_never_started'}) as mocked:
                self.assertEqual(self.post('book-draft-archive-never-started', {**target, 'request_id': 'a' * 32, 'confirmed': True})[0], 200)
                self.assertEqual(mocked.call_args.kwargs, {'expected_book_id': book['book_id'], 'confirmed': True})
            editor.assert_not_called()
            service.assert_not_called()
        self.assertEqual((root / '.story/state.sqlite3').read_bytes(), before)
        self.assertFalse((root / '.story/workbench-service.json').exists())

    def test_shelf_cleanup_rejects_key_book_csrf_root_and_unconfirmed_payload(self):
        _, book = self.create()
        target = {'key': book['key'], 'expected_book_id': book['book_id']}
        backend = base.base.story.platform_drafts
        cases = [('blockers', 'workbench_assistant_blockers', {}),
                 ('reconcile-prepare', 'workbench_assistant_reconcile_prepare', {'request_id': 'a' * 32, 'confirmed': True}),
                 ('reconcile-complete', 'workbench_assistant_reconcile_complete', {'recovery_id': 'b' * 32, 'observation': {}}),
                 ('archive-never-started', 'workbench_assistant_archive_never_started', {'request_id': 'a' * 32, 'confirmed': True})]
        for action, method, data in cases:
            with self.subTest(action=action), patch.object(backend, method, create=True) as mocked:
                route = 'book-draft-' + action
                self.assertEqual(self.post(route, {**target, **data}, {'X-Story-Token': 'wrong'})[0], 403)
                self.assertEqual(self.post(route, {**target, **data}, {'Origin': 'https://untrusted.example'})[0], 403)
                self.assertNotEqual(self.post(route, {**target, **data, 'key': 'another-key'})[0], 200)
                self.assertNotEqual(self.post(route, {**target, **data, 'expected_book_id': 'another-book'})[0], 200)
                self.assertNotEqual(self.post(route, {**target, **data, 'root': '/other'})[0], 200)
                mocked.assert_not_called()


if __name__ == '__main__':
    unittest.main()
