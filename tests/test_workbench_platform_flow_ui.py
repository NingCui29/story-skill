"""Exercise the production draft flow with synthetic targets and delayed receipts."""
import unittest

from tests import test_workbench_formal_refresh_ui as formal_ui
from tests import test_workbench_inspection_ui as inspection_ui


class PlatformFlowUITests(unittest.TestCase):
    js = formal_ui.FormalRefreshUITests.js
    fixture = inspection_ui.InspectionUITests.fixture + r'''
const oldPlan=()=>plan('old-plan',{chapters:[1]});
const recovery=()=>({book_id:'book-one',plan_id:'old-plan',manifest_sha256:'d'.repeat(64),
 target:{platform:'fanqie',account_id:'account',remote_book_id:'remote'},chapters:[1],pending_chapters:[1]});
const blocked=()=>{const j=inspected();j.state='inspection_blocked';j.result.can_execute=false;
 j.result.change_counts={create:0,update:1,unchanged:0,blocked:1};
 j.result.changes=[{chapter:1,title:'第一章',action:'blocked',reason:'unresolved_previous_content'},
 {chapter:2,title:'第二章',action:'update'}];j.result.recovery_plans=[recovery()];return j;};
const recoveryHandler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog([plan(),oldPlan()]);
 if(action.endsWith('/formal-state'))return formal();
 if(action.endsWith('/preview'))return preview(payload);
 if(action.endsWith('/start'))return payload.reconcile_only?job({plan_id:'old-plan',mode:'reconcile_only',done:true,state:'succeeded',verified_chapters:1,total_chapters:1,result:{results:[]}}):inspected();
 throw Error(action);
};
'''

    def test_range_change_prepares_once_then_inspects_without_sync(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);
p.refreshStart.value='3';p.refreshEnd.value='5';p.refreshEnd.oninput();
assert.equal(p.run.disabled,false);assert.equal(p.run.textContent,'检查差异');assert.equal(p.recheck.hidden,true);
handler=async(action,payload)=>{
 if(action.endsWith('/refresh-plan'))return refreshed(payload);
 if(action.endsWith('/catalog'))return catalog([plan('new-plan',{chapters:[3,4,5]}),plan()]);
 if(action.endsWith('/preview'))return preview(payload);
 if(action.endsWith('/start'))return job({plan_id:'new-plan',mode:'inspect_drafts'});
 throw Error(action);
};await p.run.onclick();assert.equal(count('refresh-plan'),1);assert.equal(starts().length,1);
assert.equal(starts()[0].payload.plan_id,'new-plan');assert.equal(starts()[0].payload.inspect_only,true);
assert.equal(starts()[0].payload.sync_drafts,false);assert.equal(JSON.stringify([...docs]),before);
assert.ok(p.stageLabel.textContent.includes('只读检查'));assert.equal(p.run.disabled,true);
''')

    def test_confirmation_names_target_and_counts_only_writes(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,j=inspected();
j.result.change_counts={create:1,update:1,unchanged:5,blocked:0};platformDraftDrawJob(p,j);
assert.equal(p.run.textContent,'确认同步 2 章');assert.equal(p.confirmationSummary.hidden,false);
for(const text of ['番茄','验收作品','账号 account','作品 remote','第1—2章','新建 1 章','更新 1 章'])assert.ok(p.confirmationSummary.textContent.includes(text),text);
assert.ok(p.stageLabel.textContent.includes('确认同步'));assert.equal(p.recheck.hidden,false);
assert.equal(starts().length,0);j.result.change_counts={create:0,update:0,unchanged:2,blocked:0};platformDraftDrawJob(p,j);
assert.equal(p.run.disabled,true);assert.equal(p.confirmationSummary.hidden,true);assert.ok(p.stageLabel.textContent.includes('无需写入'));
''')

    def test_first_use_has_no_zero_over_zero_progress(self):
        self.js(self.fixture + r'''
handler=async(action)=>action.endsWith('/catalog')?catalog([],[],{profiles:[]}):formal();
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.progressLabel.textContent,'本次尚未开始');assert.equal(p.progress.hidden,true);
assert.equal(p.bindDetails.open,true);assert.equal(p.run.disabled,true);assert.equal(starts().length,0);
''')

    def test_original_batch_reconciliation_returns_to_current_range_without_sync(self):
        self.js(self.fixture + r'''
handler=recoveryHandler;await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);
platformDraftDrawJob(p,blocked());const prior=p.recoveryAction.onclick;
assert.equal(p.run.disabled,true);assert.equal(p.run.hidden,true);assert.ok(p.nextHint.textContent.includes('原批次 第1章'));
await prior();assert.equal(p.plan.value,'old-plan');assert.equal(p.mode.value,'reconcile_only');
assert.equal(p.refreshStart.value,'1');assert.equal(p.refreshEnd.value,'1');assert.equal(p.returnAction.hidden,false);
assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
const calls=requests.length;await prior();assert.equal(requests.length,calls);
await p.run.onclick();assert.equal(starts().length,1);assert.equal(starts()[0].payload.plan_id,'old-plan');
assert.equal(starts()[0].payload.reconcile_only,true);assert.equal(starts()[0].payload.sync_drafts,false);
assert.equal(p.recoveryAction.textContent,'返回原范围并检查');await p.recoveryAction.onclick();
assert.equal(p.plan.value,'plan-1');assert.equal(p.mode.value,'sync_drafts');assert.equal(p.refreshEnd.value,'2');
assert.equal(p.returnSelection,null);assert.equal(count('refresh-plan'),1);assert.equal(starts().length,2);
assert.equal(starts()[1].payload.inspect_only,true);assert.equal(starts()[1].payload.sync_drafts,false);
assert.equal(p.run.textContent,'确认同步 2 章');assert.equal(JSON.stringify([...docs]),before);
''')

    def test_return_without_execution_keeps_manually_selected_range(self):
        self.js(self.fixture + r'''
handler=recoveryHandler;await platformDraftOpen();const p=platformDraftPanel;
p.refreshStart.value='3';p.refreshEnd.value='5';p.refreshRangeDirty=true;platformDraftDrawJob(p,blocked());
await p.recoveryAction.onclick();await p.returnAction.onclick();
assert.equal(p.plan.value,'plan-1');assert.equal(p.refreshStart.value,'3');assert.equal(p.refreshEnd.value,'5');
assert.equal(p.needsPreparation,true);assert.equal(p.run.disabled,false);assert.equal(p.returnSelection,null);
assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
''')

    def test_unverified_recovery_never_selects_or_executes_other_target(self):
        self.js(self.fixture + r'''
for(const wrong of ['book','manifest','target','chapters','missing']){
 handler=recoveryHandler;await platformDraftOpen();const p=platformDraftPanel,j=blocked(),r=j.result.recovery_plans[0];
 if(wrong==='book')r.book_id='another-book';if(wrong==='manifest')r.manifest_sha256='bad';
 if(wrong==='target')r.target.remote_book_id='another-target';if(wrong==='chapters')r.chapters=[9];if(wrong==='missing')r.plan_id='missing-plan';
 platformDraftDrawJob(p,j);assert.equal(p.recoveryAction.hidden,true);assert.ok(p.nextHint.textContent.includes('未能验证原批次'));
 assert.equal(p.plan.value,'plan-1');assert.equal(p.returnSelection,undefined);assert.equal(starts().length,0);p.dialog.close();
}
''')

    def test_profile_changes_during_recovery_refresh_require_manual_review(self):
        self.js(self.fixture + r'''
handler=recoveryHandler;await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,blocked());
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan(),oldPlan()],[],{profiles:[{...profile,sha256:'b'.repeat(64)}]}):recoveryHandler(action,payload);
await p.recoveryAction.onclick();assert.equal(p.preview,null);assert.equal(p.run.disabled,true);
assert.ok(p.status.textContent.includes('配置在生成后已变化'));assert.equal(starts().length,0);
''')

    def test_historical_unknown_inspection_keeps_original_batch_recovery(self):
        self.js(self.fixture + r'''
handler=recoveryHandler;await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,blocked(),true);assert.equal(p.jobHistorical,true);assert.equal(p.progress.value,0);
assert.equal(p.recoveryAction.textContent,'核对原批次未知结果');await p.recoveryAction.onclick();
assert.equal(p.plan.value,'old-plan');assert.equal(p.mode.value,'reconcile_only');assert.equal(starts().length,0);
''')

    def test_new_range_can_inspect_only_after_formal_state_read_recovers(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
handler=async()=>{throw Error('formal unavailable');};await platformDraftFormalState(p);
p.refreshEnd.value='3';p.refreshEnd.oninput();assert.equal(p.run.disabled,true);await p.run.onclick();assert.equal(count('refresh-plan'),0);
handler=async()=>formal();await platformDraftFormalState(p);assert.equal(p.run.disabled,false);assert.equal(starts().length,0);
''')

    def test_failed_refresh_receipt_never_starts_inspection(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.refreshEnd.value='3';p.refreshEnd.oninput();
handler=async(action,payload)=>refreshed(payload,{remote_book_id:'wrong-target'});
await p.run.onclick();assert.equal(count('refresh-plan'),1);assert.equal(starts().length,0);
assert.equal(p.preview,null);assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('回执无法确认'));
''')

    def test_task_started_elsewhere_during_preparation_prevents_another_start(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.refreshStart.value='3';p.refreshEnd.value='5';p.refreshEnd.oninput();
handler=async(action,payload)=>{
 if(action.endsWith('/refresh-plan'))return refreshed(payload);
 if(action.endsWith('/catalog'))return catalog([plan('new-plan',{chapters:[3,4,5]}),plan('other-plan')],[job({id:'other-job',plan_id:'other-plan'})]);
 if(action.endsWith('/preview'))return preview(payload);throw Error(action);
};await p.run.onclick();assert.equal(count('refresh-plan'),1);assert.equal(starts().length,0);
assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('已有草稿任务正在运行'));
assert.ok(p.formalNote.textContent.includes('本地准备已结束'));assert.ok(!p.formalNote.textContent.includes('正在准备'));
await p.refresh.onclick();assert.equal(p.job.id,'other-job');assert.equal(p.plan.value,'other-plan');assert.equal(starts().length,0);
''')

    def test_late_return_to_current_does_not_overwrite_new_range_or_start_inspection(self):
        self.js(self.fixture + r'''
handler=recoveryHandler;await platformDraftOpen();const p=platformDraftPanel;
p.refreshStart.value='3';p.refreshEnd.value='5';p.refreshRangeDirty=true;platformDraftDrawJob(p,blocked());await p.recoveryAction.onclick();
let release;handler=async(action,payload)=>action.endsWith('/preview')&&payload.plan_id==='plan-1'?new Promise(resolve=>{release=()=>resolve(preview(payload));}):recoveryHandler(action,payload);
const saved=p.returnSelection,returning=platformDraftReturnToCurrent(p,true);
for(let i=0;i<80&&!release;i++)await Promise.resolve();assert.ok(release);assert.equal(p.refreshStart.disabled,false);
p.refreshStart.value='7';p.refreshEnd.value='9';p.refreshEnd.oninput();release();await returning;
assert.equal(p.refreshStart.value,'7');assert.equal(p.refreshEnd.value,'9');assert.equal(p.returnSelection,saved);
assert.equal(p.preview,null);assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
''')

    def test_failed_return_catalog_retains_bookmark_without_starting(self):
        self.js(self.fixture + r'''
handler=recoveryHandler;await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,blocked());await p.recoveryAction.onclick();const saved=p.returnSelection;
handler=async()=>{throw Error('catalog unavailable');};await p.returnAction.onclick();
assert.equal(p.returnSelection,saved);assert.equal(p.preview,null);assert.equal(starts().length,0);
assert.ok(p.status.textContent.includes('读取清单失败'));assert.equal(p.run.disabled,true);
''')


if __name__ == '__main__':
    unittest.main()
