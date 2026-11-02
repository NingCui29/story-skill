"""Suspended automatic connection leaves preparation local and never retries a worker."""
import unittest

from tests import test_workbench_formal_refresh_ui as formal_ui
from tests import test_workbench_inspection_ui as inspection_ui


class ConnectionSuspensionUITests(unittest.TestCase):
    js = formal_ui.FormalRefreshUITests.js
    fixture = inspection_ui.InspectionUITests.fixture + r'''
const suspension={available:false,retry_blocked:true,reason:'platform_chrome_worker_connection_suspended',
 cause:'platform_chrome_worker_browser_transport_closed',failure_phase:'browser_inventory'};
const blockedPreview=payload=>({...preview(payload),can_start:false,preview_token:null,can_connect:false,connect_token:null,
 automatic_connection:{...suspension}});
const failed=()=>job({mode:'inspect_drafts',done:true,state:'failed',stage:'finished',checked_chapters:0,total_chapters:30,
 error:'platform_chrome_worker_browser_transport_closed',connection_diagnostics:{stage:'connecting_chrome',ready:false,
 retry_blocked:true,failure_phase:'browser_inventory'},result:{results:[]}});
let availablePlans=[plan()],availableJobs=[];
const localHandler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog(availablePlans,availableJobs);
 if(action.endsWith('/formal-state'))return formal();
 if(action.endsWith('/preview')){const packet=blockedPreview(payload),selected=availablePlans.find(row=>row.id===payload.plan_id);return {...packet,preview:{...packet.preview,
 chapters:selected.chapters.map(chapter=>({chapter,title:'合成章 '+chapter,body_characters:10})),
 history_events_total:selected.draft_history?.history_events_total||0,
 results:selected.chapters.map(chapter=>({chapter,state:chapter===1&&selected.draft_history?.unknown_chapters?'unknown':'pending',last_observed_at:null,reason:null}))}};}
 if(action.endsWith('/refresh-plan')){const receipt=refreshed(payload);availablePlans=[plan('new-plan',{chapters:receipt.chapters}),...availablePlans];return receipt;}
 throw Error(action);
};handler=localHandler;
'''

    def test_bootstrap_failure_has_specific_no_write_status_and_readonly_explanation(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failed());const before=requests.length;
assert.ok(p.status.textContent.includes('尚未开始章节检查'));assert.ok(p.status.textContent.includes('初始化时断开'));
assert.ok(!p.status.textContent.includes('操作结果尚未确认'));assert.ok(p.progressLabel.textContent.includes('0 / 30'));
assert.equal(p.recoveryAction.textContent,'查看连接说明');assert.equal(p.run.disabled,true);assert.equal(p.recheck.disabled,true);
await p.recoveryAction.onclick();assert.equal(p.connectionHelp.open,true);assert.equal(requests.length,before);
assert.ok(textTree(p.connectionHelp).includes('GPT-6.1 Terra'));assert.ok(textTree(p.connectionHelp).includes('本面板仍等待核验'));
assert.ok(textTree(p.connectionHelp).includes('已有健康任务的状态仍以任务回执为准'));
await p.run.onclick();await p.recheck.onclick();assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
''')

    def test_preview_block_persists_across_refresh_reopen_and_range_changes(self):
        self.js(self.fixture + r'''
await platformDraftOpen();let p=platformDraftPanel;assert.equal(p.connectionRetryBlocked,true);
assert.ok(p.status.textContent.includes('自动浏览器连接已暂停'));assert.equal(p.recoveryAction.textContent,'查看连接说明');
p.refreshEnd.value='3';p.refreshEnd.oninput();assert.equal(p.run.disabled,true);assert.equal(p.generate.disabled,false);
await p.refresh.onclick();assert.equal(p.run.disabled,true);assert.equal(p.recoveryAction.textContent,'查看连接说明');
p.dialog.close();await platformDraftOpen();p=platformDraftPanel;assert.equal(p.connectionRetryBlocked,true);
await p.recoveryAction.onclick();await p.run.onclick();assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
''')

    def test_local_prepare_succeeds_without_browser_readiness_or_start(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);p.refreshStart.value='3';p.refreshEnd.value='5';p.refreshEnd.oninput();
assert.equal(p.generate.hidden,false);assert.equal(p.generate.disabled,false);assert.equal(p.generate.textContent,'准备本地章节');
const prepared=await p.generate.onclick();assert.equal(prepared,true);assert.equal(p.plan.value,'new-plan');
assert.equal(platformDraftPreviewReady(p),false);assert.equal(p.run.disabled,true);assert.equal(p.inspection,null);
assert.ok(p.formalNote.textContent.includes('已生成第3—5章'));assert.ok(p.formalNote.textContent.includes('准备完成不代表已检查或已同步'));
assert.equal(count('refresh-plan'),1);assert.equal(starts().length,0);assert.equal(JSON.stringify([...docs]),before);
assert.ok(p.historyDetails);assert.equal(p.refreshStart.disabled,false);assert.equal(p.plan.disabled,false);
''')

    def test_unknown_and_sending_remain_readback_required_without_reconnecting(self):
        self.js(self.fixture + r'''
for(const state of ['unknown','sending']){
 await platformDraftOpen();const p=platformDraftPanel,j=job({state:state==='sending'?'failed':state,done:true,stage:'finished',
 error:'platform_chrome_worker_browser_transport_closed',connection_diagnostics:{retry_blocked:true,ready:true},
 result:{results:[{chapter:1,state,remote_id:'existing-draft',recorded_this_run:true,reason:'interrupted_sending'}]}});
 platformDraftDrawJob(p,j);const before=requests.length;assert.ok(!p.status.textContent.includes('未写入平台'));
 assert.equal(p.recoveryAction.textContent,'查看核对与连接说明');assert.ok(p.nextHint.textContent.includes('远端可能已保存'));
 assert.ok(p.nextHint.textContent.includes('先只读核对'));await p.recoveryAction.onclick();await p.run.onclick();await p.recheck.onclick();
 assert.equal(requests.length,before);assert.equal(p.connectionHelp.open,true);
 p.refreshEnd.value='3';p.refreshEnd.oninput();assert.equal(await p.generate.onclick(),true);
 assert.equal(p.recoveryAction.textContent,'查看核对与连接说明');assert.ok(p.nextHint.textContent.includes('不能重复发送'));p.dialog.close();
}
assert.equal(starts().length,0);
''')

    def test_historical_failed_job_does_not_restore_direct_retry_button(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,failed(),true);
assert.ok(p.status.textContent.includes('本次尚未开始'));assert.ok(p.historyJob.textContent.includes('初始化时断开'));
assert.equal(p.recoveryAction.textContent,'查看连接说明');const before=requests.length;await p.recoveryAction.onclick();
assert.equal(requests.length,before);assert.equal(p.run.disabled,true);assert.equal(starts().length,0);
''')

    def test_historical_unknown_metadata_is_preserved_in_blocked_preview(self):
        self.js(self.fixture + r'''
availablePlans=[plan('plan-1',{draft_history:{unknown_chapters:1,history_events_total:1}})];
await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.recoveryAction.textContent,'查看核对与连接说明');
assert.ok(p.nextHint.textContent.includes('尚未执行核对'));assert.ok(textTree(p.historyResults).includes('未知'));
const before=requests.length;await p.recoveryAction.onclick();assert.equal(requests.length,before);assert.equal(starts().length,0);
''')

    def test_historical_unknown_does_not_turn_bootstrap_failure_into_current_unknown(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,j=failed();j.result={results:[{chapter:1,state:'unknown',
 remote_id:'old-draft',recorded_this_run:false,observed_this_run:false,reason:'interrupted_sending'}]};
platformDraftDrawJob(p,j);assert.ok(p.status.textContent.includes('尚未开始章节检查；未写入平台'));
assert.ok(!p.status.textContent.includes('远端结果未知'));assert.ok(p.progressLabel.textContent.includes('0 / 30'));
assert.ok(p.nextHint.textContent.includes('历史批次'));assert.ok(p.nextHint.textContent.includes('不代表本次发生了保存'));
assert.equal(p.recoveryAction.textContent,'查看核对与连接说明');assert.equal(starts().length,0);
''')

    def test_late_preparation_receipt_does_not_replace_new_range(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;let release;
handler=async(action,payload)=>action.endsWith('/refresh-plan')?new Promise(resolve=>{release=()=>resolve(refreshed(payload));}):localHandler(action,payload);
p.refreshStart.value='3';p.refreshEnd.value='5';p.refreshEnd.oninput();const preparing=p.generate.onclick();
p.refreshStart.value='7';p.refreshEnd.value='9';p.refreshEnd.oninput();release();assert.equal(await preparing,false);
assert.equal(p.plan.value,'plan-1');assert.equal(p.refreshStart.value,'7');assert.equal(p.refreshEnd.value,'9');
assert.ok(!p.formalNote.textContent.includes('已生成'));assert.equal(starts().length,0);
''')

    def test_late_catalog_preview_does_not_claim_new_selection_was_prepared(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;let release;
handler=async(action,payload)=>action.endsWith('/preview')&&payload.plan_id==='new-plan'?new Promise(resolve=>{release=()=>resolve(blockedPreview(payload));}):localHandler(action,payload);
p.refreshStart.value='3';p.refreshEnd.value='5';p.refreshEnd.oninput();const preparing=p.generate.onclick();
for(let i=0;i<80&&!release;i++)await Promise.resolve();assert.ok(release);
p.refreshStart.value='7';p.refreshEnd.value='9';p.refreshEnd.oninput();release();assert.equal(await preparing,false);
assert.equal(p.refreshStart.value,'7');assert.equal(p.refreshEnd.value,'9');assert.equal(p.preview,null);
assert.ok(!p.formalNote.textContent.includes('已生成'));assert.equal(starts().length,0);
''')

    def test_closing_during_prepare_does_not_resume_or_change_new_panel(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const old=platformDraftPanel;let release;
handler=async(action,payload)=>action.endsWith('/refresh-plan')?new Promise(resolve=>{release=()=>resolve(refreshed(payload));}):localHandler(action,payload);
const preparing=old.generate.onclick();old.dialog.close();await platformDraftOpen();const current=platformDraftPanel;
release();await preparing;assert.notEqual(current,old);assert.equal(current.plan.value,'plan-1');assert.equal(current.run.disabled,true);
assert.equal(starts().length,0);assert.ok(!current.formalNote.textContent.includes('已生成'));
''')

    def test_single_chapter_prepare_and_expanded_batch_never_count_as_inspection(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.refreshStart.value='1';p.refreshEnd.value='1';p.refreshEnd.oninput();
assert.equal(await p.generate.onclick(),true);assert.deepEqual(p.plans.find(row=>row.id===p.plan.value).chapters,[1]);
assert.equal(p.inspection,null);assert.ok(p.progressLabel.textContent.includes('0 / 1'));
p.refreshEnd.value='3';p.refreshEnd.oninput();assert.equal(p.inspection,null);assert.equal(p.run.disabled,true);
assert.equal(count('refresh-plan'),1);assert.equal(starts().length,0);assert.ok(!p.status.textContent.includes('同步完成'));
''')

    def test_only_boolean_server_block_is_used_and_untrusted_details_not_rendered(self):
        self.js(self.fixture + r'''
handler=async(action,payload)=>action.endsWith('/preview')?{...preview(payload),automatic_connection:{retry_blocked:'true',reason:'PRIVATE_TOKEN',failure_phase:'PRIVATE_BODY'}}:localHandler(action,payload);
await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.connectionRetryBlocked,undefined);assert.equal(p.run.disabled,false);
handler=async(action,payload)=>action.endsWith('/preview')?{...blockedPreview(payload),automatic_connection:{...suspension,reason:'PRIVATE_TOKEN',failure_phase:'PRIVATE_BODY'}}:localHandler(action,payload);
await platformDraftPreview(p);assert.equal(p.run.disabled,true);assert.ok(!textTree(p.content).includes('PRIVATE_'));
assert.ok(platformDraftErrorCode('platform_chrome_worker_connection_suspended').includes('暂停'));assert.equal(starts().length,0);
''')

    def test_malformed_prepare_receipt_never_reports_local_success(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
handler=async(action,payload)=>action.endsWith('/refresh-plan')?refreshed(payload,{remote_book_id:'another-target'}):localHandler(action,payload);
assert.notEqual(await p.generate.onclick(),true);assert.equal(p.plan.value,'plan-1');assert.ok(p.formalNote.textContent.includes('回执无法确认'));
assert.equal(p.inspection,null);assert.equal(starts().length,0);
''')

    def test_start_preview_suspension_after_preparation_requires_matching_identity_and_never_starts(self):
        self.js(self.fixture + r'''
for(const matching of [false,true]){
 availablePlans=[plan()];let inspectPreviews=0;
 handler=async(action,payload)=>{
  if(action.endsWith('/preview')){
   if(!payload.inspect_only)return preview(payload);
   inspectPreviews++;return {...blockedPreview(payload),...(matching?{}:{book_id:'another-book'})};
  }
  return localHandler(action,payload);
 };
 await platformDraftOpen();const p=platformDraftPanel,prepares=count('refresh-plan');assert.equal(p.run.disabled,false);
 await p.run.onclick();assert.equal(count('refresh-plan'),prepares+1);assert.equal(inspectPreviews,1);assert.equal(starts().length,0);
 if(matching){
  assert.equal(p.connectionRetryBlocked,true);assert.equal(p.run.disabled,true);assert.equal(p.recheck.disabled,true);
  assert.ok(p.status.textContent.includes('本次未启动新的检查或保存'));assert.equal(p.recoveryAction.textContent,'查看连接说明');
  const before=requests.length;await p.run.onclick();await p.recheck.onclick();await p.recoveryAction.onclick();
  assert.equal(requests.length,before);assert.equal(p.connectionHelp.open,true);
 }else{
  assert.equal(p.connectionRetryBlocked,undefined);assert.ok(p.status.textContent.includes('本次目标或连接状态尚未确认'));
  assert.ok(!p.status.textContent.includes('本次未启动新的检查或保存'));
 }
 p.dialog.close();
}
''')

    def test_server_suspends_between_preview_and_start_without_allowing_another_click(self):
        self.js(self.fixture + r'''
handler=async(action,payload)=>{
 if(action.endsWith('/preview'))return preview(payload);
 if(action.endsWith('/start'))throw Object.assign(Error('暂停新的自动连接'),{code:'platform_chrome_worker_connection_suspended'});
 return localHandler(action,payload);
};await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.run.disabled,false);await p.run.onclick();
assert.equal(starts().length,1);assert.equal(p.connectionRetryBlocked,true);assert.equal(p.run.disabled,true);
assert.ok(p.status.textContent.includes('本次未启动新的检查或保存'));assert.equal(p.recoveryAction.textContent,'查看连接说明');
const before=requests.length;await p.run.onclick();await p.recheck.onclick();await p.recoveryAction.onclick();
assert.equal(requests.length,before);assert.equal(p.connectionHelp.open,true);
await platformDraftPreview(p);assert.equal(p.preview.can_start,true);assert.equal(p.run.disabled,true);assert.equal(starts().length,1);
''')

    def test_transport_failure_after_sync_request_is_not_reported_as_admission_rejection(self):
        self.js(self.fixture + r'''
let rejectStart;handler=async(action,payload)=>{
 if(action.endsWith('/preview'))return preview(payload);
 if(action.endsWith('/start'))return new Promise((resolve,reject)=>{rejectStart=reject;});
 return localHandler(action,payload);
};await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,inspected());
const saving=p.run.onclick();for(let i=0;i<80&&!rejectStart;i++)await Promise.resolve();assert.ok(rejectStart);
platformDraftObserveConnection(p,{retry_blocked:true});rejectStart(Error('连接响应丢失'));await saving;
assert.equal(starts()[0].payload.sync_drafts,true);assert.ok(p.status.textContent.includes('未能确认任务启动'));
assert.ok(!p.status.textContent.includes('本次未启动'));assert.ok(!p.status.textContent.includes('未写入平台'));
assert.equal(p.run.disabled,true);await p.run.onclick();assert.equal(starts().length,1);
''')


if __name__ == '__main__':
    unittest.main()
