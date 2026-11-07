"""Workbench selection of existing empty-draft authorizations and isolated reads."""
import unittest
from unittest.mock import patch

from tests import test_workbench_assistant_draft_ui as assistant
from tests import test_workbench_platform_draft_ui as platform


class WorkbenchAttributionUITests(unittest.TestCase):
    js = assistant.AssistantDraftUITests.js
    fixture = assistant.AssistantDraftUITests.fixture + r'''
plans=[plan('original',{chapters:Array.from({length:30},(_,index)=>index+1)})];
const candidate={attribution_id:'a'.repeat(64),chapter:9,authorized_at:'2026-01-02T00:00:00Z',source_created_at:'2026-01-01T00:00:00Z'};
const options=(payload,extra={})=>({ok:true,book_id:'book-one',plan_id:payload.plan_id,profile_id:payload.profile_id,profile_sha256:payload.profile_sha256,options:[candidate],...extra});
const withAttribution=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog(plans,[],{formal_last:30,formal_count:30});
 if(action.endsWith('/formal-state'))return formal({formal_last:30,formal_count:30});
 if(action.endsWith('/assistant-attributions'))return options(payload);
 if(action.endsWith('/assistant-prepare')){const task=packet(payload,{attribution_id:payload.attribution_id||records.find(row=>row.request_id===payload.inspection_id)?.attribution_id||null});records.push(task);return task;}
 return localHandler(action,payload);
};handler=withAttribution;
const choose=async p=>{await p.attributionLoad.onclick();p.attributionSelect.value=candidate.attribution_id;p.attributionSelect.onchange();};
'''

    def test_author_selects_existing_chapter_nine_for_original_batch_and_sync_inherits_inspection(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(calls('assistant-attributions').length,0);assert.equal(p.attributionChoice,null);
await p.attributionLoad.onclick();assert.equal(p.attributionSelect.value,'');assert.equal(p.attributionChoice,null);
assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);assert.ok(textTree(p.attributionSelect).includes('第9章'));
p.attributionSelect.value=candidate.attribution_id;p.attributionSelect.onchange();assert.ok(p.attributionNote.textContent.includes('第9章'));assert.equal(p.run.disabled,false);
await p.run.onclick();const inspect=calls('assistant-prepare')[0].payload;
assert.equal(inspect.plan_id,'original');assert.equal(inspect.attribution_id,candidate.attribution_id);assert.equal(inspect.mode,'inspect_drafts');assert.equal(calls('refresh-plan').length,0);assert.equal(calls('start').length,0);
records[0]=complete(last());records[0].result.change_counts={create:1,update:0,unchanged:29,blocked:0};await p.assistantRefresh.onclick();assert.equal(p.assistantSync.disabled,false);
p.assistantSync.onclick();assert.ok(textTree(dialogs.at(-1)).includes('本次只读检查核对过的空稿归属授权'));await confirmButton().onclick();
const sync=calls('assistant-prepare')[1].payload;assert.equal(sync.mode,'sync_drafts');assert.equal(sync.inspection_id,records[0].request_id);assert.equal(sync.confirmed,true);assert.equal(Object.hasOwn(sync,'attribution_id'),false);assert.equal(p.assistantTask.attribution_id,candidate.attribution_id);
''')

    def test_no_candidate_selected_keeps_ordinary_refresh_and_inspection(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.attributionLoad.onclick();await p.assistantInspect.onclick();
const sent=calls('assistant-prepare')[0].payload;assert.equal(calls('refresh-plan').length,1);assert.equal(sent.plan_id,'new-plan');assert.equal(Object.hasOwn(sent,'attribution_id'),false);assert.equal(p.attributionChoice,null);
''')

    def test_changed_scope_discards_selection_and_late_or_private_option_data(self):
        self.js(self.fixture + r'''
plans.push(plan('other'));await platformDraftOpen();const p=platformDraftPanel;
await choose(p);assert.equal(platformDraftSelectedAttribution(p).chapter,9);p.encoding.value='legacy-encoding';platformDraftButtons(p);assert.equal(p.attributionChoice,null);p.encoding.value=platformDraftRequiredEncoding;platformDraftButtons(p);
let finish;handler=async(action,payload)=>action.endsWith('/assistant-attributions')?new Promise(resolve=>{finish=()=>resolve(options(payload));}):withAttribution(action,payload);
const delayed=p.attributionLoad.onclick();p.plan.value='other';await p.plan.onchange();finish();await delayed;
assert.equal(p.attributionOptions.length,0);assert.equal(p.attributionChoice,null);assert.equal(p.attributionLoading,false);
handler=async(action,payload)=>action.endsWith('/assistant-attributions')?options(payload,{options:[{...candidate,chapter:1,attribution_path:'PRIVATE-PATH'}]}):withAttribution(action,payload);
await p.attributionLoad.onclick();assert.equal(p.attributionOptions.length,0);assert.ok(p.attributionNote.textContent.includes('未能读取'));assert.ok(!textTree(p.assistantArea).includes('PRIVATE-PATH'));assert.equal(calls('assistant-prepare').length,0);
''')

    def test_profile_book_or_mode_changes_clear_the_choice(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
await choose(p);p.bookId='other-book';platformDraftButtons(p);assert.equal(p.attributionChoice,null);p.bookId='book-one';platformDraftButtons(p);
await choose(p);const originalHash=p.profiles[0].sha256;p.profiles[0].sha256='d'.repeat(64);platformDraftButtons(p);assert.equal(p.attributionChoice,null);p.profiles[0].sha256=originalHash;platformDraftButtons(p);
await choose(p);p.mode.value='reconcile_only';await p.mode.onchange();assert.equal(p.attributionChoice,null);assert.equal(p.attributionLoad.disabled,true);await p.assistantReadback.onclick();assert.equal(Object.hasOwn(calls('assistant-prepare')[0].payload,'attribution_id'),false);
''')

    def test_mismatched_prepared_authorization_is_reported_as_unconfirmed(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await choose(p);
handler=async(action,payload)=>{const result=await withAttribution(action,payload);return action.endsWith('/assistant-prepare')?{...result,attribution_id:'b'.repeat(64)}:result;};
await p.assistantInspect.onclick();assert.equal(p.assistantUncertain,true);assert.equal(p.assistantSync.disabled,true);assert.equal(calls('assistant-prepare').length,1);
''')

    def test_retired_readonly_timeout_allows_fresh_inspection_through_same_and_other_host_history(self):
        self.js(self.fixture + r'''
handler=async(action,payload)=>action.endsWith('/refresh-plan')?refreshed(payload,{plan_id:payload.plan_id,plan_reused:true}):withAttribution(action,payload);
for(const oldHost of ['codex','qoder-cn']){
 records=[packet({plan_id:'original',profile_id:profile.id},{execution_host:oldHost,native_model_authorized:oldHost!=='codex',model_policy:oldHost==='codex'?'terra_required':'user_authorized_native',mode:'inspect_drafts',state:'inspection_blocked',stage:'cleanup_pending',done:true,execution_active:false,cleanup_pending:false,cleanup_complete:false,execution_retired_by_operator:true,cleanup_status:'external_session_reset',worker_done:false,can_sync:false,result:{can_execute:false,changes:[{chapter:6,action:'blocked',reason:'platform_chrome_request_timeout'}]}})];
 await platformDraftOpen();const p=platformDraftPanel,prior=calls('assistant-prepare').length;assert.equal(p.assistantSync.disabled,true);assert.equal(p.assistantInspect.disabled,false);
 await p.assistantInspect.onclick();const sent=calls('assistant-prepare');assert.equal(sent.length,prior+1);assert.equal(sent.at(-1).payload.mode,'inspect_drafts');assert.equal(sent.at(-1).payload.execution_host,'codex');assert.equal(calls('start').length,0);p.dialog.close();
}
''')

    def test_unproven_isolation_and_unknown_writes_keep_normal_inspection_blocked(self):
        self.js(self.fixture + r'''
for(const change of [{execution_retired_by_operator:false},{execution_active:true},{cleanup_pending:true},{done:false},{cleanup_status:'pending'},{mode:'sync_drafts',state:'unknown'}]){
 records=[packet({plan_id:'original',profile_id:profile.id},{mode:'inspect_drafts',state:'inspection_blocked',stage:'cleanup_pending',done:true,execution_active:false,cleanup_pending:false,cleanup_complete:false,execution_retired_by_operator:true,cleanup_status:'external_session_reset',result:{changes:[{chapter:6,action:'blocked',reason:'platform_chrome_request_timeout'}]},...change})];
 await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.assistantInspect.disabled,true);await p.assistantInspect.onclick();assert.equal(calls('assistant-prepare').length,0);assert.equal(p.assistantSync.disabled,true);p.dialog.close();
}
''')


class WorkbenchAttributionHTTPTests(unittest.TestCase):
    setUp = platform.PlatformDraftHTTPTests.setUp
    stop = platform.PlatformDraftHTTPTests.stop
    post = platform.PlatformDraftHTTPTests.post

    def test_candidate_and_selection_routes_bind_editor_identity_and_reject_external_inputs(self):
        backend = platform.base.story.platform_drafts
        scope = {'plan_id': 'original', 'profile_id': 'profile', 'profile_sha256': 'a' * 64,
                 'comparison_policy': 'strict', 'body_encoding': 'plain_paragraphs_v1'}
        with patch.object(backend, 'workbench_assistant_attributions', create=True, return_value={'ok': True, 'options': []}) as lookup:
            self.assertEqual(self.post('assistant-attributions', scope)[0], 200)
            self.assertEqual(lookup.call_args.kwargs['expected_book_id'], self.fixture.book.meta('id'))
            lookup.reset_mock()
            for extra in ({'attribution_path': '/other'}, {'remote_id': 'other'}, {'book_id': 'other'}):
                self.assertEqual(self.post('assistant-attributions', {**scope, **extra})[0], 409)
            self.assertEqual(self.post('assistant-attributions', scope, Origin='https://other.example')[0], 403)
            lookup.assert_not_called()
        with patch.object(backend, 'workbench_assistant_prepare', return_value={'ok': True, 'attribution_id': 'b' * 64,
                                                                               'attribution_path': '/private'}) as prepare:
            payload = {**scope, 'mode': 'inspect_drafts', 'attribution_id': 'b' * 64}
            code, result = self.post('assistant-prepare', payload)
            self.assertEqual(code, 200, result)
            self.assertEqual(prepare.call_args.kwargs['attribution_id'], 'b' * 64)
            self.assertNotIn('attribution_path', result)
            prepare.reset_mock()
            for extra in ({'attribution_id': '/private'}, {'attribution_path': '/private'},
                          {'remote_id': 'other'}, {'mode': 'reconcile_only'}):
                self.assertEqual(self.post('assistant-prepare', {**payload, **extra})[0], 409)
            prepare.assert_not_called()

    def test_legacy_assistant_status_and_latest_keep_private_evidence_paths_on_server(self):
        backend = platform.base.story.platform_drafts
        task = {'request_id': 'old', 'attribution_path': '/private/evidence'}
        with patch.object(backend, 'workbench_assistant_status', return_value=task):
            self.assertEqual(self.post('assistant-status', {'request_id': 'old'})[1], {'request_id': 'old'})
        with patch.object(backend, 'workbench_assistant_latest', return_value={'ok': True, 'request': task, 'active_request': task}):
            _, packet = self.post('assistant-latest', {'plan_id': 'plan', 'profile_id': 'profile'})
            self.assertEqual(packet['request'], {'request_id': 'old'})
            self.assertEqual(packet['active_request'], {'request_id': 'old'})
        with patch.object(backend, 'workbench_assistant_blockers', return_value={'ok': True, 'blockers': [task], 'legacy_blockers': [task]}):
            _, packet = self.post('assistant-blockers', {})
            self.assertEqual(packet['blockers'], [{'request_id': 'old'}])
            self.assertEqual(packet['legacy_blockers'], [{'request_id': 'old'}])
