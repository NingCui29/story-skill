"""Explicit per-panel tool choice, with synthetic DOM and local HTTP only."""
import unittest
from unittest.mock import patch

from tests import test_workbench_assistant_draft_ui as base


class ExecutionHostUITests(unittest.TestCase):
    js = base.AssistantDraftUITests.js
    fixture = base.AssistantDraftUITests.fixture + r'''
let hosts=[{id:'codex',label:'Codex',sdk_available:true,reason:null,model_policy:'terra_required'},
 {id:'qoder-cn',label:'Qoder CN',sdk_available:true,reason:null,model_policy:'user_authorized_native'},
 {id:'workbuddy',label:'WorkBuddy AI',sdk_available:false,reason:'platform_chrome_worker_native_sdk_unavailable',model_policy:'user_authorized_native'}];
const hostPacket=(payload,extra={})=>packet(payload,{execution_host:payload.execution_host||'codex',
 model_policy:payload.execution_host&&payload.execution_host!=='codex'?'user_authorized_native':'terra_required',
 native_model_authorized:payload.native_model_authorized===true,...extra});
const hostHandler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog(plans,[],{browser_runtime:{execution_hosts:hosts}});
 if(action.endsWith('/assistant-prepare')){const task=hostPacket(payload);records.push(task);return task;}
 return localHandler(action,payload);
};handler=hostHandler;
const choose=(panel,host)=>{panel.assistantHost.value=host;return panel.assistantHost.onchange();};
'''

    def test_selector_exposes_all_tools_and_unavailable_selection_never_falls_back(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
assert.deepEqual(p.assistantHost.options.map(row=>row.textContent),['Codex','Qoder CN','WorkBuddy AI']);
const contains=(node,target)=>node===target||node.children.some(child=>contains(child,target));
p.assistantArea.open=false;assert.equal(contains(p.content,p.assistantHost),true);assert.equal(contains(p.content,p.assistantHostNote),true);
assert.equal(contains(p.assistantArea,p.assistantHost),false);assert.equal(contains(p.assistantArea,p.assistantHostNote),false);
assert.equal(p.assistantHost.value,'codex');assert.ok(p.assistantHostNote.textContent.includes('GPT-6.1 Terra'));
assert.ok(p.assistantHostNote.textContent.includes('仍须实际核验'));
choose(p,'workbuddy');assert.equal(p.assistantHost.value,'workbuddy');assert.equal(p.assistantHost.disabled,false);
assert.equal(p.assistantInspect.disabled,true);assert.equal(p.assistantReadback.disabled,true);assert.equal(p.run.disabled,true);
assert.ok(p.assistantHostNote.textContent.includes('未找到所选工具的公开浏览器组件'));
assert.ok(p.assistantHostNote.textContent.includes('不会自动改用其他工具'));
await p.run.onclick();await p.assistantInspect.onclick();assert.equal(calls('start').length,0);assert.equal(calls('assistant-prepare').length,0);
await p.refresh.onclick();assert.equal(p.assistantHost.value,'workbuddy');assert.equal(p.run.disabled,true);
''')

    def test_native_main_button_prepares_selected_host_handoff_without_auto_worker(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');
assert.ok(p.run.textContent.includes('Qoder CN'));assert.equal(p.run.disabled,false);
assert.ok(p.assistantHostNote.textContent.includes('当前模型'));assert.ok(!p.assistantHostNote.textContent.includes('GPT-6.1 Terra'));
await p.run.onclick();const sent=calls('assistant-prepare').at(-1).payload;
assert.equal(sent.execution_host,'qoder-cn');assert.equal(sent.native_model_authorized,true);assert.equal(sent.mode,'inspect_drafts');
assert.equal(calls('start').length,0);assert.equal(p.assistantHost.disabled,true);
assert.ok(p.assistantStateNote.textContent.includes('给 Qoder CN'));assert.ok(p.assistantStateNote.textContent.includes('尚未执行'));
await p.assistantCopy.onclick();assert.equal(clipboard,p.assistantPrompt.value);assert.equal(calls('start').length,0);
assert.ok(p.assistantMessage.textContent.includes('尚未连接浏览器或执行任务'));
choose(p,'codex');assert.equal(p.assistantHost.value,'qoder-cn');assert.equal(p.assistantTask.execution_host,'qoder-cn');
''')

    def test_permission_help_uses_selected_native_host_and_restores_codex_help(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel,help=p.permissionHelp,original=textTree(help),before=requests.length;
assert.ok(original.includes('Codex 官方设置入口'));assert.ok(help.children.some(row=>row.href?.startsWith('https://developers.openai.com/')));
for(const [host,label]of [['qoder-cn','Qoder CN'],['workbuddy','WorkBuddy AI']]){
 choose(p,host);assert.equal(p.permissionHelp,help);const text=textTree(help);
 if(host==='workbuddy'){assert.ok(text.includes('agent-browser'));assert.ok(text.includes('独立浏览器窗口'));assert.ok(text.includes('首次执行时'));}
 else{assert.ok(text.includes(label+' 官方浏览器或连接器'));assert.ok(text.includes('实际显示的授权提示'));}
 assert.ok(text.includes('fanqienovel.com'));assert.ok(text.includes('不会改用其他工具'));
 assert.ok(!text.includes('Codex'));assert.ok(!text.includes('GPT-6.1 Terra'));assert.ok(!text.includes('Settings'));
 assert.ok(!help.children.some(row=>row.tagName==='a'));
}
choose(p,'codex');assert.equal(textTree(help),original);assert.equal(requests.length,before);
assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);
''')

    def test_codex_connection_help_stays_hidden_for_native_even_with_stale_action(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.connectionRetryBlocked=true;platformDraftButtons(p);
const oldAction=p.recoveryAction.onclick,before=requests.length;oldAction();assert.equal(p.connectionHelp.hidden,false);assert.equal(p.connectionHelp.open,true);
choose(p,'qoder-cn');assert.equal(p.connectionRetryBlocked,true);assert.equal(p.connectionHelp.hidden,true);assert.equal(p.connectionHelp.open,false);
assert.equal(p.recoveryAction.hidden,true);assert.ok(!p.nextHint.textContent.includes('Terra'));assert.ok(p.nextHint.textContent.includes('Qoder CN'));
oldAction();platformDraftShowConnectionHelp(p);platformDraftBlockedActions(p,false);
assert.equal(p.connectionHelp.hidden,true);assert.equal(p.connectionHelp.open,false);
choose(p,'codex');assert.equal(p.connectionRetryBlocked,true);assert.equal(p.connectionHelp.hidden,false);assert.equal(p.recoveryAction.hidden,false);
assert.equal(p.run.disabled,true);p.recoveryAction.onclick();assert.equal(p.connectionHelp.open,true);
assert.ok(textTree(p.connectionHelp).includes('GPT-6.1 Terra'));assert.equal(requests.length,before);
assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);
''')

    def test_tool_switch_clears_unused_authorization_and_keeps_completed_result_as_history(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;await p.assistantInspect.onclick();records[0]=complete(last());await p.assistantRefresh.onclick();
const old=records[0];assert.equal(p.assistantSync.disabled,false);p.assistantSync.onclick();const oldConfirm=confirmButton();
p.inspection={done:true,state:'inspection_ready'};p.preview={old:true};p.previewExpires=Date.now()+10000;
choose(p,'qoder-cn');assert.equal(p.assistantTask,null);assert.equal(p.inspection,null);assert.equal(p.preview,null);assert.equal(p.previewExpires,0);
assert.equal(p.assistantPrompt.value,'');assert.equal(p.assistantSync.disabled,true);
assert.ok(textTree(p.assistantHistoryDetails).includes('Codex'));assert.ok(textTree(p.assistantHistoryDetails).includes('不授权当前工具执行'));
await oldConfirm.onclick();assert.equal(calls('assistant-prepare').length,1);assert.equal(oldConfirm.disabled,true);
await p.assistantRefresh.onclick();assert.equal(p.assistantTask,null);assert.equal(p.assistantSync.disabled,true);
assert.equal(p.assistantHistory.get(old.request_id).request_id,old.request_id);
await p.assistantInspect.onclick();assert.equal(calls('assistant-prepare').length,2);assert.equal(last().execution_host,'qoder-cn');
''')

    def test_same_tool_inspection_confirmation_preserves_host_and_authorization(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');await p.assistantInspect.onclick();
records[0]=complete(last());await p.assistantRefresh.onclick();assert.equal(p.assistantSync.disabled,false);
p.run.onclick();assert.ok(textTree(dialogs.at(-1)).includes('执行工具：Qoder CN'));
await confirmButton().onclick();const sent=calls('assistant-prepare').at(-1).payload;
assert.equal(sent.mode,'sync_drafts');assert.equal(sent.execution_host,'qoder-cn');assert.equal(sent.native_model_authorized,true);
assert.equal(sent.inspection_id,records[0].request_id);assert.equal(sent.confirmed,true);assert.equal(calls('start').length,0);
''')

    def test_same_batch_other_host_active_task_blocks_until_user_returns_to_that_tool(self):
        self.js(self.fixture + r'''
records=[hostPacket({plan_id:'plan-1',profile_id:profile.id,execution_host:'qoder-cn',native_model_authorized:true})];
await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.assistantHost.value,'codex');
assert.equal(p.assistantForeignActive.execution_host,'qoder-cn');assert.equal(p.assistantHost.disabled,true);
assert.equal(p.assistantInspect.disabled,true);assert.equal(p.assistantReturn.hidden,false);
assert.equal(calls('assistant-prepare').length,0);await p.assistantReturn.onclick();
assert.equal(p.assistantHost.value,'qoder-cn');assert.equal(p.assistantTask.request_id,records[0].request_id);
assert.equal(calls('assistant-prepare').length,0);await p.assistantCancel.onclick();assert.equal(p.assistantHost.disabled,false);
choose(p,'codex');assert.equal(p.assistantHost.value,'codex');assert.equal(p.assistantTask,null);
''')

    def test_runtime_disappearance_keeps_native_choice_and_disables_actions(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');
hosts=hosts.map(row=>row.id==='qoder-cn'?{...row,sdk_available:false,reason:'platform_chrome_worker_native_sdk_unavailable'}:row);
await p.refresh.onclick();assert.equal(p.assistantHost.value,'qoder-cn');assert.equal(p.assistantInspect.disabled,true);assert.equal(p.run.disabled,true);
await p.assistantInspect.onclick();assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);
''')

    def test_native_reply_cannot_claim_codex_or_missing_model_authorization(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');
handler=async(action,payload)=>action.endsWith('/assistant-prepare')?hostPacket(payload,{execution_host:'codex',native_model_authorized:false,model_policy:'terra_required'}):hostHandler(action,payload);
await p.assistantInspect.onclick();assert.equal(p.assistantTask,null);assert.equal(p.assistantUncertain,true);
assert.ok(p.assistantMessage.textContent.includes('执行工具'));assert.equal(p.assistantHost.disabled,true);assert.equal(calls('start').length,0);
''')

    def test_cleanup_pending_and_lost_response_lock_tool_switch(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');await p.assistantInspect.onclick();
records[0]=complete(last(),{execution_active:true,cleanup_pending:true,cleanup_complete:false});await p.assistantRefresh.onclick();
assert.equal(p.assistantHost.disabled,true);choose(p,'codex');assert.equal(p.assistantHost.value,'qoder-cn');
records[0]={...last(),execution_active:false,cleanup_pending:false,cleanup_complete:true};await p.assistantRefresh.onclick();
assert.equal(p.assistantHost.disabled,false);choose(p,'codex');assert.equal(p.assistantHost.value,'codex');
assert.equal(calls('assistant-prepare').length,1);assert.equal(calls('start').length,0);
''')

    def test_cleaned_unknown_native_result_can_start_fresh_readonly_inspection_not_sync(self):
        self.js(self.fixture + r'''
plans=[plan('new-plan')];records=[hostPacket({plan_id:'new-plan',profile_id:profile.id,execution_host:'qoder-cn',native_model_authorized:true,mode:'sync_drafts'},
 {state:'unknown',done:true,cleanup_complete:true,cleanup_pending:false,execution_active:false,handoff_prompt:null,
 result:{results:[{chapter:1,state:'unknown',reason:'interrupted_sending'}]}})];
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');
await p.assistantRefresh.onclick();const oldId=last().request_id;
assert.equal(p.assistantInspect.disabled,false);assert.equal(p.run.disabled,false);assert.equal(p.assistantSync.disabled,true);
assert.ok(p.assistantStateNote.textContent.includes('先“检查差异”只读核对'));
await platformDraftAssistantPrepare(p,'sync_drafts',oldId);assert.equal(calls('assistant-prepare').length,0);
await p.run.onclick();const sent=calls('assistant-prepare').at(-1).payload;
assert.equal(calls('assistant-prepare').length,1);assert.equal(sent.mode,'inspect_drafts');assert.equal(sent.execution_host,'qoder-cn');
assert.equal(sent.native_model_authorized,true);assert.equal(sent.confirmed,undefined);assert.equal(sent.inspection_id,undefined);
assert.equal(calls('start').length,0);assert.equal(p.assistantSync.disabled,true);
await platformDraftAssistantPrepare(p,'sync_drafts',oldId);assert.equal(calls('assistant-prepare').length,1);
assert.ok(!platformDraftErrorCode('platform_draft_native_model_not_authorized').includes('platform_draft_'));
''')

    def test_unknown_result_without_confirmed_cleanup_or_with_uncertain_reply_stays_locked(self):
        self.js(self.fixture + r'''
plans=[plan('new-plan')];const unknown=hostPacket({plan_id:'new-plan',profile_id:profile.id,execution_host:'qoder-cn',native_model_authorized:true,mode:'sync_drafts'},
 {state:'unknown',done:true,handoff_prompt:null,result:{results:[{chapter:1,state:'unknown',reason:'interrupted_sending'}]}});
records=[unknown];await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');
for(const fields of [{cleanup_complete:false},{cleanup_pending:true},{execution_active:true}]){
 records[0]={...unknown,...fields};await p.assistantRefresh.onclick();assert.equal(p.assistantInspect.disabled,true);
 assert.equal(p.run.disabled,true);assert.equal(p.assistantSync.disabled,true);
 await p.assistantInspect.onclick();await p.run.onclick();assert.equal(calls('assistant-prepare').length,0);
}
records[0]=unknown;await p.assistantRefresh.onclick();p.assistantUncertain=true;platformDraftButtons(p);
assert.equal(p.assistantInspect.disabled,true);assert.equal(p.assistantHost.disabled,true);
await p.assistantInspect.onclick();assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);
''')


class ExecutionHostHTTPTests(unittest.TestCase):
    setUp = base.AssistantDraftHTTPTests.setUp
    stop = base.AssistantDraftHTTPTests.stop
    post = base.AssistantDraftHTTPTests.post

    def test_host_and_boolean_authorization_are_forwarded_without_model_attestation(self):
        payload = {'plan_id': 'plan', 'profile_id': 'profile', 'profile_sha256': 'a' * 64,
                   'mode': 'inspect_drafts', 'comparison_policy': 'strict', 'body_encoding': 'plain_paragraphs_v1',
                   'execution_host': 'qoder-cn', 'native_model_authorized': True}
        with patch.object(base.platform.base.story.platform_drafts, 'workbench_assistant_prepare', return_value={'ok': True}) as call:
            self.assertEqual(self.post('assistant-prepare', payload)[0], 200)
            self.assertEqual(call.call_args.kwargs['execution_host'], 'qoder-cn')
            self.assertIs(call.call_args.kwargs['native_model_authorized'], True)
            self.assertEqual(call.call_args.kwargs['expected_book_id'], self.fixture.book.meta('id'))
            for bad in ({'native_model_authorized': 'true'}, {'native_model_authorized': 1}, {'model_verified': True}):
                call.reset_mock()
                self.assertEqual(self.post('assistant-prepare', {**payload, **bad})[0], 409)
                call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
