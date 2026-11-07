"""Assistant handoff is local, explicitly confirmed and isolated from auto workers."""
import unittest
from unittest.mock import patch

from tests import test_workbench_formal_refresh_ui as formal
from tests import test_workbench_platform_draft_ui as platform


class AssistantDraftUITests(unittest.TestCase):
    js = formal.FormalRefreshUITests.js
    fixture = r'''
const dialogs=[];Element.prototype.showModal=function(){this.open=true;dialogs.push(this);};
Element.prototype.select=function(){this.selected=true;};
let clipboard='',clipboardError=false;Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>{if(clipboardError)throw Error('denied');clipboard=text;}}}});
const suspension={available:false,retry_blocked:true,reason:'platform_chrome_worker_connection_suspended'};
let plans=[plan()],records=[],sequence=0,blocked=true;
const packet=(payload,extra={})=>({ok:true,request_id:String(++sequence).padStart(32,'0'),execution_origin:'assistant_session',model_verification:'executor_runtime_required',execution_mode:'host_assisted_cua',book_id:'book-one',plan_id:payload.plan_id,profile_id:payload.profile_id,profile_sha256:profile.sha256,manifest_sha256:'b'.repeat(64),binding_sha256:'c'.repeat(64),target:{platform:'fanqie',account_id:'account',remote_book_id:'remote'},mode:payload.mode||'inspect_drafts',comparison_policy:'strict',body_encoding:'plain_paragraphs_v1',state:'prepared',stage:'awaiting_assistant',done:false,execution_active:false,cleanup_pending:false,cleanup_complete:true,cleanup_status:'not_started',created_at:new Date().toISOString(),expires_at:new Date(Date.now()+600000).toISOString(),inspection_expires_at:null,claimed_at:null,finished_at:null,error:null,checked_chapters:0,verified_chapters:0,total_chapters:plans.find(row=>row.id===payload.plan_id).chapters.length,result:null,source_matches_current:true,can_sync:false,remote_accessed:false,stop_requested:false,handoff_prompt:'Synthetic local request. Copy does not execute.',...extra});
const complete=(task,extra={})=>({...task,state:'inspection_ready',done:true,stage:'finished',checked_chapters:task.total_chapters,finished_at:new Date().toISOString(),inspection_expires_at:new Date(Date.now()+600000).toISOString(),can_sync:true,handoff_prompt:null,result:{can_execute:true,source_matches_current:true,change_counts:{create:1,update:1,unchanged:0,blocked:0},changes:[{chapter:1,action:'update'},{chapter:2,action:'create'}]},...extra});
const last=()=>records.at(-1),calls=name=>requests.filter(row=>row.action.endsWith('/'+name));
const localHandler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog(plans);
 if(action.endsWith('/formal-state'))return formal();
 if(action.endsWith('/preview'))return {...preview(payload),...(blocked?{can_start:false,can_connect:false,automatic_connection:suspension}:{})};
 if(action.endsWith('/refresh-plan')){const receipt=refreshed(payload);plans=[plan('new-plan',{chapters:receipt.chapters}),...plans.filter(row=>row.id!=='new-plan')];return receipt;}
 if(action.endsWith('/assistant-latest'))return {ok:true,request:records.filter(row=>row.plan_id===payload.plan_id&&row.profile_id===payload.profile_id).at(-1)||null,active_request:records.find(row=>!row.done||row.execution_active||row.cleanup_pending)||null};
 if(action.endsWith('/assistant-status'))return records.find(row=>row.request_id===payload.request_id);
 if(action.endsWith('/assistant-prepare')){const task=packet(payload);records.push(task);return task;}
 if(action.endsWith('/assistant-cancel')){const i=records.findIndex(row=>row.request_id===payload.request_id),task=records[i];records[i]={...task,stop_requested:true,...(task.state==='prepared'?{state:'cancelled',done:true,handoff_prompt:null}:{})};return records[i];}
 throw Error('Forbidden request '+action);
};handler=localHandler;
const confirmButton=()=>dialogs.at(-1).children[0].children.find(row=>row.tagName==='button');
'''

    def test_paused_connection_exposes_local_handoff_and_copy_never_executes(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);
assert.equal(p.assistantArea.open,true);assert.equal(p.assistantInspect.disabled,false);assert.equal(p.run.disabled,true);
assert.ok(p.status.textContent.includes('准备助手检查任务'));
await p.assistantInspect.onclick();assert.equal(calls('assistant-prepare').length,1);assert.equal(calls('start').length,0);
assert.equal(p.assistantTask.state,'prepared');assert.equal(p.plan.disabled,true);assert.equal(p.generate.disabled,true);
assert.ok(p.assistantProgress.textContent.includes('本次助手任务'));assert.ok(p.assistantStateNote.textContent.includes('尚未执行'));
await p.assistantCopy.onclick();assert.equal(clipboard,p.assistantPrompt.value);assert.ok(p.assistantMessage.textContent.includes('尚未连接浏览器或执行任务'));
assert.ok(!textTree(p.assistantArea).includes('模型身份已核验'));assert.equal(JSON.stringify([...docs]),before);
await p.assistantInspect.onclick();await p.run.onclick();assert.equal(calls('assistant-prepare').length,1);assert.equal(calls('start').length,0);
''')

    def test_copy_denial_keeps_and_selects_readonly_prompt_without_claiming_success(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();clipboardError=true;
const before=requests.length;await p.assistantCopy.onclick();assert.equal(p.assistantPrompt.readOnly,true);assert.equal(p.assistantPrompt.selected,true);
assert.ok(p.assistantMessage.textContent.includes('手动复制'));assert.ok(!p.assistantMessage.textContent.includes('已复制'));
assert.equal(requests.length,before);assert.equal(clipboard,'');
''')

    def test_readonly_result_requires_separate_bound_confirmation_to_prepare_sync(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();records[0]=complete(last());await p.assistantRefresh.onclick();
assert.equal(p.assistantSync.disabled,false);assert.equal(p.progress.value,0);assert.equal(p.inspection,null);
assert.ok(p.assistantProgress.textContent.includes('本次助手任务'));assert.equal(calls('assistant-prepare').length,1);
p.assistantSync.onclick();assert.ok(textTree(dialogs.at(-1)).includes('作品 remote'));assert.ok(textTree(dialogs.at(-1)).includes('将新建 1 章，更新 1 章'));
assert.equal(calls('assistant-prepare').length,1);await confirmButton().onclick();
const sent=calls('assistant-prepare').at(-1).payload;assert.equal(sent.mode,'sync_drafts');assert.equal(sent.confirmed,true);assert.equal(sent.inspection_id,records[0].request_id);
assert.equal(p.assistantTask.state,'prepared');assert.equal(calls('start').length,0);
''')

    def test_failed_inspection_count_is_attempted_not_completed(self):
        self.js(self.fixture + r'''
plans=[plan('plan-1',{chapters:Array.from({length:30},(_,i)=>i+1)})];
await platformDraftOpen();const p=platformDraftPanel;
p.assistantTask=packet({plan_id:p.plan.value,profile_id:p.profile.value},{state:'failed',stage:'finished',done:true,
 checked_chapters:3,verified_chapters:0,error:'platform_browser_structure_changed'});
platformDraftAssistantRender(p);assert.ok(p.assistantProgress.textContent.includes('已尝试检查 3 / 30 章'));
assert.ok(!p.assistantProgress.textContent.includes('已完成差异检查'));assert.equal(p.assistantSync.disabled,true);
p.assistantTask=complete(p.assistantTask);platformDraftAssistantRender(p);
assert.ok(p.assistantProgress.textContent.includes('已完成差异检查 30 / 30 章'));
assert.equal(calls('start').length,0);assert.equal(calls('assistant-prepare').length,0);p.dialog.close();
''')

    def test_local_preview_expiry_keeps_current_assistant_progress_visible(self):
        self.js(self.fixture + r'''
const chapters=Array.from({length:30},(_,i)=>i+1);
plans=[plan('plan-1',{chapters})];
handler=async(action,payload)=>{const reply=await localHandler(action,payload);
 if(action.endsWith('/catalog')||action.endsWith('/formal-state'))return {...reply,formal_last:30,formal_count:30};
 if(action.endsWith('/preview'))reply.preview.chapters=chapters.map(chapter=>({chapter,title:'章节'+chapter,body_characters:100}));
 return reply;};
await platformDraftOpen();const p=platformDraftPanel;
timers.get(p.expiryTimer).fn();assert.equal(p.localStatus.hidden,false);assert.ok(p.status.textContent.includes('预览已过期'));
assert.ok(p.progressLabel.textContent.includes('0 / 30'));
await platformDraftPreview(p);await p.assistantInspect.onclick();
records[0]=complete(last());records[0].result.change_counts={create:21,update:1,unchanged:8,blocked:0};
records[0].result.changes=chapters.map(chapter=>({chapter,action:chapter<=8?'unchanged':chapter===9?'update':'create'}));
await p.assistantRefresh.onclick();const expiry=timers.get(p.expiryTimer);expiry.fn();
assert.equal(p.preview,null);assert.equal(p.localStatus.hidden,true);assert.equal(p.assistantArea.open,true);
assert.ok(p.assistantProgress.textContent.includes('已完成差异检查 30 / 30 章'));
assert.equal(p.assistantSync.disabled,false);assert.equal(calls('assistant-prepare').length,1);assert.equal(calls('start').length,0);
p.job=job();platformDraftButtons(p);assert.equal(p.localStatus.hidden,false);p.job=null;
p.refreshEnd.value='3';p.refreshEnd.oninput();assert.equal(p.localStatus.hidden,false);
const status=p.status.textContent;expiry.fn();assert.equal(p.status.textContent,status);assert.equal(p.assistantSync.disabled,true);
p.dialog.close();
''')

    def test_allocated_empty_draft_recovery_reason_is_explicit_without_new_execution(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();
records[0]=complete(last());records[0].result.changes[0].reason='resume_empty_draft';
await p.assistantRefresh.onclick();assert.ok(textTree(p.assistantResults).includes('恢复上次未完成的空草稿'));
records[0].result.changes[0].reason='recovering_empty_draft';await p.assistantRefresh.onclick();
assert.ok(textTree(p.assistantResults).includes('正在恢复上次未完成的空草稿'));
records[0].result.changes[0].reason='empty_draft_recovered';await p.assistantRefresh.onclick();
assert.ok(textTree(p.assistantResults).includes('已恢复原空草稿并完整回读'));
assert.equal(calls('start').length,0);assert.equal(calls('assistant-prepare').length,1);p.dialog.close();
''')

    def test_expired_changed_consumed_or_incomplete_inspection_cannot_prepare_sync(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();const original=complete(last());
for(const delta of [{can_sync:false},{source_matches_current:false},{inspection_expires_at:new Date(Date.now()-1).toISOString()},{checked_chapters:1},{result:{...original.result,source_matches_current:false}}]){
 records[0]={...original,...delta};await p.assistantRefresh.onclick();assert.equal(p.assistantSync.disabled,true);const before=requests.length;p.assistantSync.onclick();assert.equal(requests.length,before);
}
assert.equal(calls('assistant-prepare').length,1);assert.equal(calls('start').length,0);
''')

    def test_confirmation_does_not_survive_changed_range_or_new_result(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();records[0]=complete(last());await p.assistantRefresh.onclick();p.assistantSync.onclick();const button=confirmButton();
p.refreshEnd.value='3';p.refreshEnd.oninput();await button.onclick();assert.equal(calls('assistant-prepare').length,1);assert.equal(button.disabled,true);
assert.equal(calls('start').length,0);
''')

    def test_cleaned_unknown_result_allows_inspection_or_readonly_recovery_but_not_direct_sync(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();records[0]={...last(),mode:'inspect_drafts',state:'unknown',done:true,handoff_prompt:null,result:{results:[{chapter:1,state:'unknown',reason:'interrupted_sending'}]}};
await p.assistantRefresh.onclick();assert.equal(p.assistantInspect.disabled,false);assert.equal(p.assistantReadback.disabled,false);assert.equal(p.assistantSync.disabled,true);
assert.ok(p.assistantStateNote.textContent.includes('不能重复发送'));await p.assistantReadback.onclick();
assert.equal(calls('assistant-prepare').at(-1).payload.mode,'reconcile_only');assert.equal(calls('start').length,0);
''')

    def test_reopen_restores_historical_task_without_counting_as_current_progress(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();records[0]=complete(last());p.dialog.close();
await platformDraftOpen();const q=platformDraftPanel;assert.ok(q.assistantProgress.textContent.includes('上次助手任务'));assert.ok(q.assistantProgress.textContent.includes('历史结果不计入本次进度'));
assert.equal(q.progress.value,0);assert.equal(calls('assistant-prepare').length,1);assert.equal(calls('start').length,0);
''')

    def test_unknown_prepare_receipt_reads_latest_before_another_prepare(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;handler=async(action,payload)=>{const reply=await localHandler(action,payload);if(action.endsWith('/assistant-prepare'))throw Error('lost response');return reply;};
await p.assistantInspect.onclick();assert.equal(p.assistantUncertain,true);assert.equal(p.assistantInspect.disabled,true);assert.equal(p.plan.disabled,true);
await p.assistantInspect.onclick();assert.equal(calls('assistant-prepare').length,1);await p.assistantRefresh.onclick();
assert.equal(p.assistantTask.request_id,last().request_id);assert.equal(p.assistantUncertain,false);assert.equal(calls('assistant-prepare').length,1);
''')

    def test_unknown_sync_prepare_reads_latest_instead_of_old_inspection(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();records[0]=complete(last());await p.assistantRefresh.onclick();
handler=async(action,payload)=>{const reply=await localHandler(action,payload);if(action.endsWith('/assistant-prepare'))throw Error('lost sync response');return reply;};
p.assistantSync.onclick();await confirmButton().onclick();assert.equal(p.assistantUncertain,true);assert.equal(p.assistantTask.mode,'inspect_drafts');
const before=requests.length;await p.assistantRefresh.onclick();assert.ok(requests[before].action.endsWith('/assistant-latest'));
assert.equal(p.assistantTask.mode,'sync_drafts');assert.equal(p.assistantUncertain,false);assert.equal(calls('assistant-prepare').length,2);assert.equal(calls('start').length,0);
''')

    def test_late_prepare_after_close_never_changes_new_panel(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;let release;handler=async(action,payload)=>action.endsWith('/assistant-prepare')?new Promise(resolve=>{release=()=>resolve(packet(payload));}):localHandler(action,payload);
const preparing=p.assistantInspect.onclick();for(let i=0;i<100&&!release;i++)await Promise.resolve();assert.ok(release);p.dialog.close();await platformDraftOpen();const q=platformDraftPanel;
release();await preparing;assert.equal(q.assistantTask,null);assert.equal(q.assistantPrompt.value,'');assert.equal(calls('start').length,0);
''')

    def test_late_status_and_wrong_identity_do_not_replace_current_request(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();const original=p.assistantTask;let release;
handler=async(action,payload)=>action.endsWith('/assistant-status')?new Promise(resolve=>{release=resolve;}):localHandler(action,payload);
const reading=p.assistantRefresh.onclick();p.selectionGeneration=(p.selectionGeneration||0)+1;release(complete(original));await reading;
assert.equal(p.assistantTask,original);handler=async(action,payload)=>action.endsWith('/assistant-status')?{...complete(original),manifest_sha256:'d'.repeat(64)}:localHandler(action,payload);
await p.assistantRefresh.onclick();assert.equal(p.assistantTask,original);assert.ok(p.assistantMessage.textContent.includes('回执身份发生变化'));
assert.equal(calls('start').length,0);
''')

    def test_other_scope_active_task_is_returned_before_creating_second_request(self):
        self.js(self.fixture + r'''
plans=[plan(),plan('old-plan')];records=[packet({plan_id:'old-plan',profile_id:profile.id})];await platformDraftOpen();const p=platformDraftPanel;
assert.ok(p.assistantForeignActive);assert.equal(p.assistantInspect.disabled,true);assert.equal(p.assistantReturn.hidden,false);
assert.ok(p.assistantMessage.textContent.includes('另一批次'));await p.assistantReturn.onclick();assert.equal(p.plan.value,'old-plan');assert.equal(p.assistantTask.request_id,records[0].request_id);
assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);await p.assistantCancel.onclick();assert.equal(p.assistantTask.state,'cancelled');assert.equal(p.assistantInspect.disabled,false);
''')

    def test_foreign_active_task_finishing_elsewhere_is_cleared_by_latest_read(self):
        self.js(self.fixture + r'''
plans=[plan(),plan('old-plan')];records=[packet({plan_id:'old-plan',profile_id:profile.id})];await platformDraftOpen();const p=platformDraftPanel;
assert.ok(p.assistantForeignActive);records[0]={...last(),state:'cancelled',done:true};await p.assistantRefresh.onclick();
assert.equal(p.assistantForeignActive,null);assert.equal(p.assistantInspect.disabled,false);assert.equal(p.assistantReturn.hidden,true);assert.equal(calls('start').length,0);
''')

    def test_claimed_request_waits_for_bridge_without_rendering_execution_context(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();
assert.ok(p.assistantProgress.textContent.includes('等待助手认领'));
records[0]={...last(),state:'running',stage:'waiting_assistant_bridge',handoff_prompt:null,assistant_context:{first_call:'PRIVATE_CONTEXT_MUST_NOT_RENDER',config_path:'/private/context'}};
await p.assistantRefresh.onclick();assert.ok(p.assistantStateNote.textContent.includes('已认领，等待连接浏览器；尚未开始章节检查'));
assert.equal(p.assistantPrompt.hidden,true);assert.ok(!textTree(p.content).includes('PRIVATE_CONTEXT'));assert.equal(calls('start').length,0);
''')

    def test_running_stop_waits_for_cooperative_receipt_without_starting_worker(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();records[0]={...last(),state:'running',handoff_prompt:null};await p.assistantRefresh.onclick();await p.assistantCancel.onclick();
assert.equal(p.assistantTask.done,false);assert.equal(p.assistantTask.stop_requested,true);assert.equal(p.assistantCancel.disabled,true);assert.equal(p.plan.disabled,true);
assert.ok(p.assistantMessage.textContent.includes('安全边界'));assert.equal(calls('start').length,0);
''')

    def test_cancel_race_preserves_finished_result_and_never_claims_revocation(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();
for(const state of ['inspection_ready','inspection_blocked','succeeded','failed']){
 records[0]={...last(),state:'running',done:false};await p.assistantRefresh.onclick();
 handler=async(action,payload)=>action.endsWith('/assistant-cancel')?(records[0]=complete(last(),{state,error:state==='failed'?'platform_chrome_request_timeout':null})):localHandler(action,payload);
 await p.assistantCancel.onclick();assert.equal(p.assistantTask.state,state);assert.ok(!p.assistantMessage.textContent.includes('已撤销'));assert.ok(p.assistantMessage.textContent.includes('保留原结果'));handler=localHandler;
}
assert.equal(calls('start').length,0);
''')

    def test_finished_cleanup_pending_blocks_new_execution_until_refreshed_proof(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();
records[0]=complete(last(),{execution_active:true,cleanup_pending:true,cleanup_complete:false,stage:'cleanup_pending',can_sync:false});await p.assistantRefresh.onclick();
assert.equal(p.assistantTask.done,true);assert.equal(p.assistantInspect.disabled,true);assert.equal(p.assistantReadback.disabled,true);assert.equal(p.assistantSync.disabled,true);assert.equal(p.plan.disabled,true);assert.equal(p.refresh.disabled,true);
assert.equal(p.assistantRefresh.disabled,false);assert.equal(p.assistantCancel.disabled,true);assert.ok(p.assistantStateNote.textContent.includes('浏览器收尾'));assert.ok(p.assistantCancel.textContent.includes('收尾'));
await p.assistantInspect.onclick();await p.assistantReadback.onclick();assert.equal(calls('assistant-prepare').length,1);
records[0]={...last(),execution_active:false,cleanup_pending:false,cleanup_complete:true,stage:'finished',can_sync:true};await p.assistantRefresh.onclick();assert.equal(p.assistantSync.disabled,false);assert.equal(p.assistantInspect.disabled,false);assert.equal(calls('start').length,0);
''')

    def test_other_scope_terminal_cleanup_pending_is_still_active(self):
        self.js(self.fixture + r'''
plans=[plan(),plan('old-plan')];records=[packet({plan_id:'old-plan',profile_id:profile.id},{state:'inspection_blocked',done:true,execution_active:true,cleanup_pending:true,cleanup_complete:false,error:'platform_chrome_request_timeout'})];await platformDraftOpen();const p=platformDraftPanel;
assert.ok(p.assistantForeignActive);assert.equal(p.assistantInspect.disabled,true);assert.ok(p.assistantMessage.textContent.includes('收尾待确认'));await p.assistantReturn.onclick();
assert.equal(p.plan.value,'old-plan');assert.ok(p.assistantStateNote.textContent.includes('停止原因'));assert.equal(p.assistantReadback.disabled,true);assert.equal(calls('assistant-prepare').length,0);
''')

    def test_unknown_write_and_cleanup_are_separate_and_never_offer_early_retry(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();
records[0]={...last(),state:'unknown',done:true,execution_active:true,cleanup_pending:true,cleanup_complete:false,result:{results:[{chapter:1,state:'unknown',reason:'interrupted_sending'}]}};
await p.assistantRefresh.onclick();assert.equal(p.assistantReadback.disabled,true);assert.ok(p.assistantStateNote.textContent.includes('收尾确认后仍须先只读核对'));
records[0]={...last(),execution_active:false,cleanup_pending:false,cleanup_complete:true};await p.assistantRefresh.onclick();assert.equal(p.assistantReadback.disabled,false);assert.equal(p.assistantInspect.disabled,false);assert.equal(p.assistantSync.disabled,true);assert.equal(calls('start').length,0);
''')

    def test_external_session_retirement_is_not_rendered_as_cleanup_success(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();
records[0]=complete(last(),{execution_active:false,cleanup_pending:false,cleanup_complete:false,worker_done:false,cleanup_status:'external_session_reset',execution_retired_by_operator:true,can_sync:false});await p.assistantRefresh.onclick();
assert.equal(p.assistantInspect.disabled,false);assert.equal(p.assistantSync.disabled,true);assert.ok(p.assistantStateNote.textContent.includes('未将旧执行标为自动收尾成功'));assert.ok(p.assistantStateNote.textContent.includes('新的只读检查'));assert.equal(calls('start').length,0);
''')


class AssistantDraftHTTPTests(unittest.TestCase):
    setUp = platform.PlatformDraftHTTPTests.setUp
    stop = platform.PlatformDraftHTTPTests.stop
    post = platform.PlatformDraftHTTPTests.post

    def test_routes_bind_server_book_and_do_not_accept_success_or_model_payloads(self):
        backend = platform.base.story.platform_drafts
        common = {'plan_id': 'plan', 'profile_id': 'profile', 'profile_sha256': 'a' * 64,
                  'mode': 'inspect_drafts', 'comparison_policy': 'strict', 'body_encoding': 'plain_paragraphs_v1'}
        for action, method, payload in (
            ('assistant-prepare', 'workbench_assistant_prepare', common),
            ('assistant-latest', 'workbench_assistant_latest', {'plan_id': 'plan', 'profile_id': 'profile'}),
            ('assistant-status', 'workbench_assistant_status', {'request_id': '1' * 32}),
            ('assistant-cancel', 'workbench_assistant_cancel', {'request_id': '1' * 32}),
        ):
            with self.subTest(action=action), patch.object(backend, method, return_value={'ok': True}) as call:
                self.assertEqual(self.post(action, payload)[0], 200)
                self.assertEqual(call.call_args.args[0], self.fixture.root)
                self.assertEqual(call.call_args.kwargs['expected_book_id'], self.fixture.book.meta('id'))
                for extra in ({'book_id': 'other'}, {'root': '/other'}, {'result': {'ok': True}}, {'model_verified': True}):
                    call.reset_mock()
                    self.assertEqual(self.post(action, {**payload, **extra})[0], 409)
                    call.assert_not_called()

    def test_csrf_origin_and_invalid_confirmation_fail_before_prepare(self):
        payload = {'plan_id': 'plan', 'profile_id': 'profile', 'profile_sha256': 'a' * 64,
                   'mode': 'sync_drafts', 'comparison_policy': 'strict', 'body_encoding': 'plain_paragraphs_v1',
                   'inspection_id': '1' * 32, 'confirmed': True}
        with patch.object(platform.base.story.platform_drafts, 'workbench_assistant_prepare', return_value={'ok': True}) as call:
            self.assertEqual(self.post('assistant-prepare', payload)[0], 200)
            self.assertTrue(call.call_args.kwargs['confirmed'])
            self.assertEqual(call.call_args.kwargs['inspection_id'], '1' * 32)
            call.reset_mock()
            self.assertEqual(self.post('assistant-prepare', {**payload, 'confirmed': 'true'})[0], 409)
            self.assertEqual(self.post('assistant-prepare', payload, **{'X-Story-Token': 'wrong'})[0], 403)
            self.assertEqual(self.post('assistant-prepare', payload, Origin='https://outside.invalid')[0], 403)
            call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
