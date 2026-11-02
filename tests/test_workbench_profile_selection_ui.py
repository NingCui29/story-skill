"""Match generated draft-panel profiles to immutable targets using synthetic DOM/HTTP."""
import unittest

from tests import test_workbench_formal_refresh_ui as formal_ui
from tests import test_workbench_inspection_ui as inspection_ui


class ProfileSelectionUITests(unittest.TestCase):
    js = formal_ui.FormalRefreshUITests.js
    fixture = inspection_ui.InspectionUITests.fixture + r'''
const oldProfile={...profile,id:'fanqie-'+ 'a'.repeat(32)+'.json',remote_book_id:'other-remote',remote_book_title:'同名作品'},
 currentProfile={...profile,id:'fanqie-'+ 'b'.repeat(32)+'.json',remote_book_title:'同名作品'},
 oldPlan=()=>plan('old-plan',{remote_book_id:'other-remote'});
let availableProfiles=[oldProfile,currentProfile],availableJobs=[];
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog([plan(),oldPlan()],availableJobs,{profiles:availableProfiles});
 if(action.endsWith('/formal-state'))return formal();
 if(action.endsWith('/preview'))return preview(payload);
 throw Error(action);
};
'''

    def test_initial_foreign_first_profile_is_excluded_before_preview(self):
        self.js(self.fixture + r'''
const before=JSON.stringify([...docs]);await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.plan.value,'plan-1');assert.equal(p.profile.value,currentProfile.id);
assert.deepEqual(p.profile.options.map(row=>row.value),[currentProfile.id]);assert.equal(p.run.disabled,false);
assert.equal(requests.find(row=>row.action.endsWith('/preview')).payload.profile_id,currentProfile.id);
assert.equal(count('refresh-plan'),0);assert.equal(starts().length,0);assert.equal(JSON.stringify([...docs]),before);
''')

    def test_plan_switch_never_retains_profile_for_previous_remote_book(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.plan.value='old-plan';await p.plan.onchange();
assert.equal(p.profile.value,oldProfile.id);assert.deepEqual(p.profile.options.map(row=>row.value),[oldProfile.id]);
assert.equal(requests.filter(row=>row.action.endsWith('/preview')).at(-1).payload.profile_id,oldProfile.id);
p.plan.value='plan-1';await p.plan.onchange();assert.equal(p.profile.value,currentProfile.id);
assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
''')

    def test_same_title_profiles_display_distinct_remote_ids_as_text(self):
        self.js(self.fixture + r'''
currentProfile.remote_book_title='同名\u0000作品\u202e';await platformDraftOpen();const p=platformDraftPanel;
const currentLabel=p.profile.options[0].textContent;assert.ok(currentLabel.includes('作品 ID remote'));
assert.ok(!/[\u0000\u202e]/.test(currentLabel));assert.ok(!currentLabel.includes(currentProfile.id));
p.plan.value='old-plan';await p.plan.onchange();const oldLabel=p.profile.options[0].textContent;
assert.ok(oldLabel.includes('同名作品'));assert.ok(oldLabel.includes('作品 ID other-remote'));assert.notEqual(oldLabel,currentLabel);
assert.equal(p.profile.options[0].children.length,0);
''')

    def test_multiple_matching_profiles_are_stable_and_keep_only_matching_choice(self):
        self.js(self.fixture + r'''
const extra={...currentProfile,id:'fanqie-'+ 'c'.repeat(32)+'.json'};
availableProfiles=[extra,oldProfile,currentProfile];await platformDraftOpen();const p=platformDraftPanel;
assert.deepEqual(p.profile.options.map(row=>row.value),[currentProfile.id,extra.id]);assert.equal(p.profile.value,currentProfile.id);
p.profile.value=extra.id;await p.profile.onchange();availableProfiles=[currentProfile,oldProfile,extra];await p.refresh.onclick();
assert.equal(p.profile.value,extra.id);assert.equal(p.profile.options.length,2);
p.plan.value='old-plan';await p.plan.onchange();assert.equal(p.profile.value,oldProfile.id);
assert.equal(starts().length,0);
''')

    def test_missing_or_unverifiable_target_has_no_fallback_or_platform_request(self):
        self.js(self.fixture + r'''
for(const value of [undefined,null,'','other-remote']){
 availableProfiles=[{...currentProfile,remote_book_id:value}];await platformDraftOpen();const p=platformDraftPanel;
 assert.equal(p.profile.value,'');assert.equal(p.profile.options.length,0);assert.equal(p.profile.disabled,true);
 assert.equal(p.preview,null);assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('当前批次作品 ID remote'));
 assert.ok(p.configNote.textContent.includes('重新绑定'));assert.equal(platformDraftRuleScope(p),null);
 await p.run.onclick();await p.recheck.onclick();assert.equal(count('preview'),0);assert.equal(count('refresh-plan'),0);
 assert.equal(starts().length,0);p.dialog.close();
}
''')

    def test_stale_preview_and_inspection_cannot_authorize_foreign_profile(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.profile.value=oldProfile.id;
p.preview={...p.preview,profile_id:oldProfile.id};assert.equal(platformDraftPreviewReady(p),false);
assert.equal(platformDraftRuleScope(p),null);p.needsPreparation=true;assert.equal(platformDraftCanPrepare(p),false);
const j={...inspected(),profile_id:oldProfile.id};p.inspection=j;assert.equal(platformDraftInspectionValid(p),false);
await p.run.onclick();assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
''')

    def test_restored_inspection_still_requires_exact_profile_and_hash(self):
        self.js(self.fixture + r'''
availableJobs=[{...inspected(),profile_id:currentProfile.id}];await platformDraftOpen();let p=platformDraftPanel;
assert.equal(p.inspection.id,'inspection-one');assert.equal(p.run.textContent,'确认同步 2 章');p.dialog.close();
availableJobs=[{...inspected(),profile_id:oldProfile.id}];await platformDraftOpen();p=platformDraftPanel;
assert.equal(p.profile.value,currentProfile.id);assert.equal(p.inspection,null);assert.equal(p.run.textContent,'检查差异');p.dialog.close();
availableJobs=[{...inspected(),profile_id:currentProfile.id,profile_sha256:'different'}];await platformDraftOpen();p=platformDraftPanel;
assert.equal(p.inspection,null);assert.equal(p.run.textContent,'检查差异');assert.equal(starts().length,0);
''')

    def test_explicit_catalog_selection_rejects_foreign_profile_without_preview(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,previews=count('preview');
await platformDraftCatalog(p,{plan_id:'plan-1',profile_id:oldProfile.id,profile_sha256:oldProfile.sha256});
assert.equal(p.catalogError,true);assert.equal(p.preview,null);assert.equal(p.run.disabled,true);
assert.equal(count('preview'),previews);assert.equal(starts().length,0);assert.equal(count('refresh-plan'),0);
''')

    def test_other_platform_keeps_existing_platform_matching_behavior(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.plans=[plan('qidian-plan',{platform:'qidian'})];p.plan.value='qidian-plan';
p.profiles=[oldProfile,{id:'起点.json',label:'起点页面',platform:'qidian',valid:true,sha256:'d'.repeat(64)}];
platformDraftProfiles(p);assert.equal(p.profile.value,'起点.json');assert.equal(p.profile.options[0].textContent,'起点页面');
assert.ok(platformDraftRuleScope(p));assert.equal(starts().length,0);
''')

    def test_same_title_target_history_is_identifiable_and_does_not_count_for_new_target(self):
        self.js(self.fixture + r'''
const prior=oldPlan();prior.draft_history={matched_chapters:35,unknown_chapters:1,history_events_total:40};
const original=handler;handler=(action,payload)=>action.endsWith('/catalog')?catalog([plan(),prior],[],{profiles:availableProfiles}):original(action,payload);
await platformDraftOpen();const p=platformDraftPanel;
assert.ok(p.plan.options[0].textContent.includes('作品 ID remote'));assert.ok(p.plan.options[1].textContent.includes('作品 ID other-remote'));
assert.ok(textTree(p.previewBox).includes('当前本地记录未确认本目标同步成功'));assert.ok(textTree(p.previewBox).includes('不能据此认定平台草稿为空'));
assert.ok(textTree(p.previewBox).includes('当前范围：第1—2章'));assert.ok(textTree(p.previewBox).includes('另有 1 个不同平台目标'));
assert.ok(p.history.textContent.includes('记录归属：账号 account · 作品 ID remote'));
assert.ok(p.historySummary.textContent.includes('作品 ID remote'));assert.ok(!p.history.textContent.includes('历史有 1 章结果未知'));
assert.equal(p.progress.value,0);assert.equal(p.connectionReadbackPending,false);assert.equal(p.run.disabled,false);assert.equal(starts().length,0);
''')

    def test_new_plan_uses_same_target_history_but_never_same_title_or_other_account(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
p.plans.push(plan('prior-same-target',{draft_history:{matched_chapters:2}}));
assert.ok(platformDraftTargetNote(p,p.plans[0]).includes('本目标已有历史核对记录'));
p.plans.at(-1).account_id='different-account';
assert.ok(platformDraftTargetNote(p,p.plans[0]).includes('当前本地记录未确认本目标同步成功'));
assert.ok(platformDraftTargetNote(p,p.plans[0]).includes('不能据此认定平台草稿为空'));
assert.equal(starts().length,0);
''')

    def test_unknown_readback_is_retained_for_same_target_and_restored_when_returning(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.connectionReadbackPending=true;
p.plans.push(plan('new-plan-same-target'));p.plan.value='new-plan-same-target';await p.plan.onchange();
assert.equal(p.connectionReadbackPending,true);
p.plan.value='old-plan';await p.plan.onchange();assert.equal(p.connectionReadbackPending,false);
p.plan.value='plan-1';await p.plan.onchange();assert.equal(p.connectionReadbackPending,true);
// Changing target cannot bypass a real task still in flight.
p.jobs=[job({plan_id:'plan-1'})];p.plan.value='old-plan';await p.plan.onchange();
assert.equal(platformDraftBusy(p),true);assert.equal(p.run.disabled,true);assert.equal(starts().length,0);
''')

    def test_failed_refresh_normalizes_existing_sentence_punctuation(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.refreshEnd.value='3';p.refreshEnd.oninput();
handler=async()=>{throw Error('所选页面配置属于另一作品。');};await p.run.onclick();
assert.ok(p.status.textContent.includes('另一作品。未启动平台保存'));assert.ok(!p.status.textContent.includes('。。'));
assert.equal(count('refresh-plan'),1);assert.equal(starts().length,0);
''')


if __name__ == '__main__':
    unittest.main()
