import unittest
from tests import test_workbench_platform_draft_ui as base

class DraftSyncUITests(unittest.TestCase):
    js = base.PlatformDraftUITests.js

    def test_latest_batch_defaults_to_unified_sync_and_reports_each_outcome(self):
        self.js(r'''
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog([plan('latest-plan'),plan('old-plan')]);
 if(action.endsWith('/preview'))return preview(payload);
 throw Error(action);
};
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.plan.value,'latest-plan');assert.equal(p.mode.value,'sync_drafts');
assert.deepEqual(p.mode.options.map(option=>option.value),['sync_drafts','reconcile_only']);
assert.equal(p.preview.mode,'sync_drafts');assert.equal(p.run.disabled,false);assert.equal(p.comparison.type,'hidden');assert.equal(p.comparison.value,'strict');
assert.ok(textTree(p.previewBox).includes('缺失章节会新建草稿'));
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
platformDraftDrawJob(p,job({plan_id:'latest-plan',mode:'sync_drafts',done:true,state:'succeeded_equivalent',
 total_chapters:3,accepted_chapters:3,equivalent_chapters:3,result:{created_chapters:1,updated_chapters:1,unchanged_chapters:1,results:[]}}));
assert.ok(p.status.textContent.includes('新增 1 章，更新 1 章，原已一致 1 章'));
''')

    def test_md_blank_line_difference_is_displayed_as_synchronized_after_current_readback(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
const row={chapter:1,state:'reconciled_equivalent',observed_this_run:true,accepted_this_run:true,
 title_matches:true,body_matches:false,rendered_body_matches:true,body_encoding:'plain_paragraphs_v1',comparison_policy:'strict'};
platformDraftDrawJob(p,job({done:true,state:'succeeded_equivalent',total_chapters:1,accepted_chapters:1,
 equivalent_chapters:1,result:{results:[row]}}));
assert.ok(p.results.children[0].textContent.includes('同步一致'));
assert.ok(p.results.children[0].textContent.includes('本地 MD 去空白行后，章名和正文逐字一致'));
assert.ok(p.progressLabel.textContent.includes('本次同步一致 1 / 1'));
assert.ok(p.status.textContent.includes('本批草稿同步一致'));
platformDraftResults(p,[{...row,state:'conflict',accepted_this_run:false,rendered_body_matches:false}]);
assert.ok(!p.results.children[0].textContent.includes('同步一致'));
for(const policy of [undefined,'fanqie_terminal_lf_v1']){
 platformDraftResults(p,[{...row,comparison_policy:policy}]);
 assert.ok(!p.results.children[0].textContent.includes('同步一致'));
}
''')

    def test_sync_mode_prepares_current_formal_range_and_carries_explicit_rules_without_sending(self):
        self.js(r'''
let refreshed=false;
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog(refreshed?[plan('new-plan')]:[plan()]);
 if(action.endsWith('/preview'))return {...preview(payload),mode:payload.sync_drafts?'sync_drafts':'save_draft',comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding};
 if(action.endsWith('/refresh-plan')){refreshed=true;return {ok:true,book_id:'book-one',revision:7,base_plan_id:'plan-1',plan_id:'new-plan',profile_id:profile.id,profile_sha256:profile.sha256,platform:'fanqie',account_id:'account',remote_book_id:'remote',start:1,end:2,chapters:[1,2],scope:'local_formal_refresh_only',remote_accessed:false,submission_performed:false};}
 if(action.endsWith('/start'))return job({plan_id:'new-plan',mode:'sync_drafts',comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding});
 throw Error(action);
};
await platformDraftOpen();const p=platformDraftPanel;
p.encoding.value='plain_paragraphs_v1';p.comparison.value='fanqie_terminal_lf_v1';p.mode.value='sync_drafts';await p.recheck.onclick();
assert.equal(p.plan.value,'new-plan');assert.equal(p.mode.value,'sync_drafts');assert.equal(p.preview.mode,'sync_drafts');assert.equal(p.run.disabled,false);
assert.equal(p.encoding.value,'plain_paragraphs_v1');assert.equal(p.comparison.value,'strict');
assert.equal(requests.find(r=>r.action.endsWith('/refresh-plan')).payload.end,2,'Do not silently extend updates to newly added chapters');
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
await p.run.onclick();const sent=requests.find(r=>r.action.endsWith('/start')).payload;assert.equal(sent.sync_drafts,true);assert.equal(sent.reconcile_only,false);assert.equal(sent.plan_id,'new-plan');
''')

    def test_latest_durable_success_wins_over_stale_failed_job_and_remains_explicitly_historical(self):
        self.js(r'''
const records=[1,2].map(chapter=>({chapter,state:'saved',title_matches:true,body_matches:true,last_observed_at:'2026-10-24T00:00:00Z',observed_this_run:false}));
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog([plan()],[job({done:true,state:'failed',profile_id:profile.id,result:{history_events_total:1,results:[{chapter:1,state:'unknown'}]}})]);
 if(action.endsWith('/preview')){const p=preview(payload);p.preview={...p.preview,history_events_total:6,results:records};return p;}throw Error(action);
};
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(Number(p.progress.value),0);assert.ok(p.progressLabel.textContent.includes('本次尚未开始'));assert.equal(p.historyResults.children.length,2);
assert.ok(p.history.textContent.includes('不计入本次进度'));assert.ok(!p.status.textContent.includes('任务未完成'));
assert.ok(p.results.children.every(row=>row.textContent.includes('本次未处理')));assert.ok(p.historyResults.children.every(row=>row.textContent.includes('历史状态')));assert.equal(p.job,null);
''')

    def test_poll_reads_new_journal_only_and_ignores_stale_selection_response(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({done:true,state:'failed',result:{history_events_total:2,results:[{chapter:1,state:'conflict'}]}}));
handler=async(action,payload)=>{assert.ok(action.endsWith('/history'));return {id:'plan-1',book_id:'book-one',remote_accessed:false,source_matches_current:true,history_events_total:5,results:[1,2].map(chapter=>({chapter,state:'reconciled',title_matches:true,body_matches:true,last_observed_at:'2026-10-24T00:00:00Z'}))};};
const originalStatus=p.status.textContent;await platformDraftHistory(p);assert.equal(Number(p.progress.value),0);assert.equal(p.status.textContent,originalStatus);assert.equal(p.historyResults.children.length,2);
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
let resolve;handler=()=>new Promise(r=>{resolve=r;});const pending=platformDraftHistory(p);p.plan.value='other-plan';resolve({id:'plan-1',book_id:'book-one',remote_accessed:false,history_events_total:8,results:[]});await pending;assert.equal(Number(p.progress.value),0);
''')

    def test_current_stop_is_separate_from_unvisited_historical_failure(self):
        self.js(r'''await platformDraftOpen();const p=platformDraftPanel;
const rows=[{chapter:21,state:'blocked',recorded_this_run:true,observed_this_run:false,reason:'platform_chrome_worker_target_tab_changed',last_observed_at:'2026-10-25T01:36:00Z'},
 {chapter:22,state:'reconciled_equivalent',observed_this_run:false,title_matches:true,rendered_body_matches:true,body_encoding:'plain_paragraphs_v1',comparison_policy:'strict',last_observed_at:'2026-10-24T16:15:00Z'},
 {chapter:23,state:'not_found',observed_this_run:false,reason:'remote_draft_not_found',last_observed_at:'2026-10-24T16:15:35Z'},
 {chapter:24,state:'pending',observed_this_run:false}];
platformDraftDrawJob(p,job({done:true,state:'partial_failure',current_chapter:21,total_chapters:35,accepted_chapters:9,equivalent_chapters:9,result:{results:rows}}));
assert.ok(p.status.textContent.includes('第21章'));assert.ok(p.status.textContent.includes('停止原因：绑定的 Chrome 标签页已离开'));
assert.ok(p.results.children[0].textContent.includes('本次未完成核对'));assert.ok(!p.results.children[0].textContent.includes('历史'));
assert.deepEqual(p.results.children.slice(1).map(row=>row.textContent),['第22章 · 本次未处理','第23章 · 本次未处理','第24章 · 本次未处理']);
assert.equal(p.historyResults.children.length,2);assert.ok(p.historyResults.children[1].textContent.includes('当时原因'));assert.ok(!p.historyResults.children[1].textContent.includes('时间未记录'));assert.equal(p.historyDetails.open,false);assert.equal(Number(p.progress.value),9);
const before=p.status.textContent,progress=p.progressLabel.textContent;
platformDraftDrawHistory(p,{history_events_total:50,results:rows});assert.equal(p.status.textContent,before);assert.equal(p.progressLabel.textContent,progress);assert.equal(p.results.children[2].textContent,'第23章 · 本次未处理');
''')

    def test_current_missing_and_unknown_attempts_do_not_require_full_readback_to_show_failure(self):
        self.js(r'''await platformDraftOpen();const p=platformDraftPanel;
for(const [state,reason] of [['not_found','remote_draft_not_found'],['unknown','platform_chrome_worker_target_tab_closed']]){
 platformDraftDrawJob(p,job({mode:'reconcile_only',done:true,state:state==='unknown'?'unknown':'failed',current_chapter:1,result:{results:[{chapter:1,state,reason,recorded_this_run:true,observed_this_run:false}]}}));
 assert.ok(p.status.textContent.includes('停止原因'));assert.ok(!p.results.children[0].textContent.includes('本次未处理'));assert.equal(p.historyResults.children.length,0);assert.equal(Number(p.progress.value),0);
}
assert.ok(p.results.children[0].textContent.includes('须先只读核对'));assert.ok(p.status.textContent.includes('标签页已关闭'));
''')

    def test_new_preview_clears_previous_batch_history_and_never_reuses_success_progress(self):
        self.js(r'''await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawHistory(p,{history_events_total:1,results:[{chapter:23,state:'not_found',reason:'remote_draft_not_found',last_observed_at:'2026-10-24T16:15:35Z'}]});
assert.equal(p.historyResults.children.length,1);await platformDraftPreview(p);
assert.equal(p.historyResults.children.length,0);assert.equal(p.historyDetails.hidden,true);assert.equal(Number(p.progress.value),0);assert.ok(!textTree(p.results).includes('第23章'));
''')

    def test_historical_body_match_does_not_count_a_formatting_conflict_as_success(self):
        self.js(r'''await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawHistory(p,{results:[{chapter:1,state:'conflict',title_matches:true,body_matches:true,reason:'remote_formatting_metadata',last_observed_at:'2026-10-25T00:00:00Z'}],history_events_total:1});
assert.equal(p.historySummary.textContent,'历史核对记录 · 1 章有记录');assert.equal(Number(p.progress.value),0);assert.ok(p.results.children[0].textContent.includes('本次未处理'));
''')
