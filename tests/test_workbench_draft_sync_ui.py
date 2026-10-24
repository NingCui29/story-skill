import unittest
from tests import test_workbench_platform_draft_ui as base

class DraftSyncUITests(unittest.TestCase):
    js = base.PlatformDraftUITests.js

    def test_sync_mode_prepares_current_formal_range_and_carries_explicit_rules_without_sending(self):
        self.js(r'''
let refreshed=false;
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog(refreshed?[plan('new-plan')]:[plan()]);
 if(action.endsWith('/preview'))return {...preview(payload),mode:payload.update_existing?'sync_existing':'save_draft',comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding};
 if(action.endsWith('/refresh-plan')){refreshed=true;return {ok:true,book_id:'book-one',revision:7,base_plan_id:'plan-1',plan_id:'new-plan',profile_id:profile.id,profile_sha256:profile.sha256,platform:'fanqie',account_id:'account',remote_book_id:'remote',start:1,end:2,chapters:[1,2],scope:'local_formal_refresh_only',remote_accessed:false,submission_performed:false};}
 if(action.endsWith('/start'))return job({plan_id:'new-plan',mode:'sync_existing',comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding});
 throw Error(action);
};
await platformDraftOpen();const p=platformDraftPanel;
p.encoding.value='markdown_paragraphs_v2';p.comparison.value='fanqie_terminal_lf_v1';p.mode.value='sync_existing';await p.mode.onchange();
assert.equal(p.plan.value,'new-plan');assert.equal(p.mode.value,'sync_existing');assert.equal(p.preview.mode,'sync_existing');assert.equal(p.run.disabled,false);
assert.equal(p.encoding.value,'markdown_paragraphs_v2');assert.equal(p.comparison.value,'fanqie_terminal_lf_v1');
assert.equal(requests.find(r=>r.action.endsWith('/refresh-plan')).payload.end,2,'Do not silently extend updates to newly added chapters');
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
await p.run.onclick();const sent=requests.find(r=>r.action.endsWith('/start')).payload;assert.equal(sent.update_existing,true);assert.equal(sent.reconcile_only,false);assert.equal(sent.plan_id,'new-plan');
''')

    def test_latest_durable_success_wins_over_stale_failed_job_and_remains_explicitly_historical(self):
        self.js(r'''
const records=[1,2].map(chapter=>({chapter,state:'saved',title_matches:true,body_matches:true,last_observed_at:'2026-10-24T00:00:00Z',observed_this_run:false}));
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog([plan()],[job({done:true,state:'failed',profile_id:profile.id,result:{history_events_total:1,results:[{chapter:1,state:'unknown'}]}})]);
 if(action.endsWith('/preview')){const p=preview(payload);p.preview={...p.preview,history_events_total:6,results:records};return p;}throw Error(action);
};
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.progress.value,2);assert.ok(p.progressLabel.textContent.includes('最近记录已核对 2 / 2'));
assert.ok(p.history.textContent.includes('本次尚未访问平台'));assert.ok(!p.status.textContent.includes('任务未完成'));
assert.ok(p.results.children.every(row=>row.textContent.includes('历史状态')));assert.equal(p.job,null);
''')

    def test_poll_reads_new_journal_only_and_ignores_stale_selection_response(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({done:true,state:'failed',result:{history_events_total:2,results:[{chapter:1,state:'conflict'}]}}));
handler=async(action,payload)=>{assert.ok(action.endsWith('/history'));return {id:'plan-1',book_id:'book-one',remote_accessed:false,source_matches_current:true,history_events_total:5,results:[1,2].map(chapter=>({chapter,state:'reconciled',title_matches:true,body_matches:true,last_observed_at:'2026-10-24T00:00:00Z'}))};};
await platformDraftHistory(p);assert.equal(p.progress.value,2);assert.ok(p.status.textContent.includes('同步记录已刷新'));
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
let resolve;handler=()=>new Promise(r=>{resolve=r;});const pending=platformDraftHistory(p);p.plan.value='other-plan';resolve({id:'plan-1',book_id:'book-one',remote_accessed:false,history_events_total:8,results:[]});await pending;assert.equal(p.progress.value,2);
''')
