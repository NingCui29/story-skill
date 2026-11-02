"""Production platform panel state across target changes and inspection expiry."""
import unittest

from tests import test_workbench_platform_draft_ui as ui
from tests import test_workbench_inspection_ui as inspection


class PlatformFeedbackTests(unittest.TestCase):
    js = ui.PlatformDraftUITests.js
    fixture = inspection.InspectionUITests.fixture + r'''
const originalFeedbackHandler=handler;
handler=async(action,payload)=>action.endsWith('/formal-state')?catalog():originalFeedbackHandler(action,payload);
const noWrites=()=>assert.equal(requests.filter(row=>row.action.endsWith('/start')).length,0);
const feedbackCleared=p=>{
 assert.equal(p.changeSummary.textContent,'');assert.equal(p.nextHint.textContent,'');
 assert.equal(p.recoveryAction.hidden,true);assert.equal(p.recoveryAction.onclick,null);
 assert.equal(p.permissionHelp.open,false);
};
'''

    def test_batch_change_clears_old_counts_before_delayed_preferences_return(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,inspected());
assert.ok(p.changeSummary.textContent.includes('待新建 1'));p.plans.push(plan('new-plan',{chapters:[9]}));
let release;handler=async(action,payload)=>action.endsWith('/preferences')?new Promise(resolve=>{release=resolve;}):preview(payload);
p.plan.value='new-plan';const changing=p.plan.onchange();feedbackCleared(p);
for(let i=0;i<10&&!release;i++)await Promise.resolve();assert.ok(release);
release({invalid:true});await changing;
feedbackCleared(p);assert.equal(p.job,null);assert.equal(p.inspection,null);assert.equal(p.preview.plan_id,'new-plan');
assert.equal(p.progress.value,0);noWrites();
''')

    def test_profile_change_clears_recovery_and_invalidates_its_old_callback(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({done:true,state:'unknown',error:'platform_chrome_request_timeout'}));
const oldRecovery=p.recoveryAction.onclick;assert.equal(p.recoveryAction.hidden,false);
p.profiles.push({...profile,id:'new-profile',sha256:'new-sha'});const option=platformDraftElement('option','另一个配置');option.value='new-profile';p.profile.append(option);
p.permissionHelp.open=true;p.profile.value='new-profile';let release;
handler=async(action,payload)=>action.endsWith('/preferences')?new Promise(resolve=>{release=resolve;}):preview(payload);
const changing=p.profile.onchange();feedbackCleared(p);
for(let i=0;i<10&&!release;i++)await Promise.resolve();assert.ok(release);const before=requests.length;
await oldRecovery();assert.equal(p.mode.value,'sync_drafts');assert.equal(requests.length,before);
release({invalid:true});await changing;
feedbackCleared(p);assert.equal(p.job,null);noWrites();
''')

    def test_mode_change_and_new_preview_clear_prior_stop_feedback(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({done:true,state:'failed',error:'platform_chrome_worker_origin_permission_required'}));
assert.equal(p.permissionHelp.open,true);assert.equal(p.recoveryAction.hidden,false);
p.mode.value='reconcile_only';await p.mode.onchange();feedbackCleared(p);
assert.equal(p.job,null);assert.equal(p.preview.mode,'reconcile_only');noWrites();
''')

    def test_failed_catalog_refresh_does_not_leave_previous_change_summary(self):
        self.js(self.fixture + r'''
for(const blocked of [true,false]){
 handler=async(action,payload)=>action.endsWith('/formal-state')?catalog():originalFeedbackHandler(action,payload);
 await platformDraftOpen();const p=platformDraftPanel,j=inspected();
 if(blocked){j.state='inspection_blocked';j.result.can_execute=false;j.result.change_counts.blocked=1;j.result.changes[0].reason='platform_browser_unnumbered_draft';}
 platformDraftDrawJob(p,j);assert.equal(p.recoveryAction.hidden, !blocked);assert.ok(p.changeSummary.textContent);
 handler=async()=>{throw Error('catalog unavailable');};await p.refresh.onclick();
 feedbackCleared(p);assert.ok(p.status.textContent.includes('读取清单失败'));assert.equal(p.job,null);
 assert.equal(p.inspection,null);assert.equal(p.run.disabled,true);assert.equal(timers.has(p.expiryTimer),false);noWrites();p.dialog.close();
}
''')

    def test_reopened_expired_inspection_is_history_for_unchanged_and_changed_rows(self):
        self.js(self.fixture + r'''
for(const unchanged of [true,false]){
 const j=inspected();j.finished_at=new Date(Date.now()-3600000).toISOString();
 if(unchanged){j.result.change_counts={create:0,update:0,unchanged:2,blocked:0};j.result.changes=j.result.changes.map(row=>({...row,action:'unchanged',difference:null}));}
 handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan()],[j]):action.endsWith('/formal-state')?catalog():preview(payload);
 await platformDraftOpen();const p=platformDraftPanel;
 assert.equal(p.jobHistorical,true);assert.equal(p.inspection,null);assert.equal(p.progress.value,0);
 assert.ok(p.progressLabel.textContent.includes('本次尚未开始'));assert.ok(p.status.textContent.includes('已过期'));
 assert.ok(!p.status.textContent.includes('全部一致'));assert.equal(p.changeSummary.textContent,'');
 assert.equal(p.expiryTimer,null);assert.equal(p.run.disabled,true);assert.equal(p.historyDetails.hidden,false);
 assert.ok(p.historyJob.textContent.includes(platformDraftStamp(j.finished_at)));
 assert.ok(textTree(p.historyInspection).includes(unchanged?'当时检查一致':'当时待更新'));
 assert.ok(p.results.children.every(row=>row.textContent.includes('本次未处理')));
 platformDraftDrawHistory(p,{history_events_total:100,results:[{chapter:1,state:'reconciled',last_observed_at:new Date().toISOString()}]});
 assert.ok(p.status.textContent.includes('已过期'));assert.ok(p.historyInspection.children.length);assert.equal(p.progress.value,0);
 noWrites();p.dialog.close();
}
''')

    def test_current_inspection_expiry_moves_results_out_of_current_progress(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,j=inspected();platformDraftDrawJob(p,j);
const timeout=timers.get(p.expiryTimer);assert.ok(timeout);assert.equal(p.run.disabled,false);
timeout.fn();assert.equal(p.jobHistorical,true);assert.equal(p.inspection,null);assert.equal(p.progress.value,0);
assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('已过期'));assert.equal(p.changeSummary.textContent,'');
assert.ok(textTree(p.historyInspection).includes('当时待更新'));noWrites();
''')

    def test_missing_invalid_future_time_and_changed_source_cannot_restore_current_consistency(self):
        self.js(self.fixture + r'''
for(const finished of [null,'invalid',new Date(Date.now()+3600000).toISOString()]){
 await platformDraftOpen();const p=platformDraftPanel,j=inspected();j.finished_at=finished;platformDraftDrawJob(p,j);
 assert.equal(p.jobHistorical,true);assert.equal(p.inspection,null);assert.equal(p.progress.value,0);assert.equal(p.expiryTimer,null);
 assert.ok(p.status.textContent.includes('失效'));noWrites();p.dialog.close();
}
await platformDraftOpen();const p=platformDraftPanel,j=inspected();j.result.source_matches_current=false;platformDraftDrawJob(p,j);
assert.equal(p.jobHistorical,true);assert.equal(p.inspection,null);assert.equal(p.run.disabled,true);noWrites();
''')

    def test_valid_inspection_reopens_and_requires_explicit_sync_confirmation(self):
        self.js(self.fixture + r'''
const j=inspected();handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan()],[j]):action.endsWith('/formal-state')?catalog():action.endsWith('/start')?job():preview(payload);
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.jobHistorical,false);assert.equal(p.inspection.id,j.id);assert.equal(p.progress.value,2);
assert.equal(p.run.textContent,'确认同步 2 章');assert.equal(p.run.disabled,false);assert.equal(p.historyInspection.children.length,0);noWrites();
await p.run.onclick();assert.equal(starts().length,1);assert.equal(starts()[0].payload.inspection_id,j.id);assert.equal(starts()[0].payload.sync_drafts,true);
''')

    def test_reopened_blocked_inspection_keeps_reason_in_history(self):
        self.js(self.fixture + r'''
const j=inspected();j.state='inspection_blocked';j.result.can_execute=false;j.result.changes[0].reason='platform_browser_unnumbered_draft';
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan()],[j]):action.endsWith('/formal-state')?catalog():preview(payload);
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.jobHistorical,true);assert.equal(p.progress.value,0);assert.ok(p.status.textContent.includes('本次尚未开始'));
assert.ok(p.historyJob.textContent.includes('无法核对章号'));assert.ok(textTree(p.historyInspection).includes('无法核对章号'));noWrites();
''')

    def test_recheck_expired_inspection_starts_only_a_new_readonly_inspection(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,j=inspected();j.finished_at=new Date(Date.now()-3600000).toISOString();platformDraftDrawJob(p,j);
const before=JSON.stringify([...docs]);assert.equal(p.recoveryAction.hidden,false);const recover=p.recoveryAction.onclick;
handler=async(action,payload)=>action.endsWith('/catalog')?catalog():action.endsWith('/start')?job({mode:'inspect_drafts'}):preview(payload);
await recover();assert.equal(p.job.mode,'inspect_drafts');assert.equal(p.inspection,null);assert.equal(p.historyInspection.children.length,0);
assert.equal(p.historyJob.textContent,'');assert.equal(p.progress.value,0);assert.equal(p.recoveryAction.hidden,true);
assert.equal(starts().length,1);assert.equal(starts()[0].payload.inspect_only,true);assert.equal(starts()[0].payload.sync_drafts,false);
assert.equal(requests.filter(row=>row.action.endsWith('/refresh-plan')).length,1);
const calls=requests.length;await recover();assert.equal(requests.length,calls);assert.equal(JSON.stringify([...docs]),before);
''')


if __name__ == '__main__':
    unittest.main()
