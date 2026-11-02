"""Native handoff keeps its click intent through local preparation; no GUI."""
import unittest

from tests import test_workbench_execution_host_ui as host


class NativeHandoffRaceTests(unittest.TestCase):
    js = host.ExecutionHostUITests.js
    fixture = host.ExecutionHostUITests.fixture

    def test_native_prepare_suppresses_only_interleaved_formal_poll_and_creates_one_handoff(self):
        self.js(self.fixture + r'''
for(const host of ['qoder-cn','workbuddy']){
 hosts=hosts.map(row=>row.id===host?{...row,sdk_available:true,reason:null}:row);records=[];
 await platformDraftOpen();const p=platformDraftPanel;choose(p,host);
 const upstream=handler,beforeFormal=calls('formal-state').length,beforePrepare=calls('assistant-prepare').length;
 let inject=true,pollCalls=0;
 handler=async(action,payload)=>{
  if(action.endsWith('/preview')&&inject){
   inject=false;assert.ok(p.nativeInspectionPreparing);assert.equal(p.refreshingPlan,false);
   await platformDraftFormalState(p);pollCalls++;assert.equal(p.formalLoading,false);
   await platformDraftAssistantPrepare(p,'inspect_drafts');
  }
  return upstream(action,payload);
 };
 await p.run.onclick();assert.equal(pollCalls,1);assert.equal(calls('formal-state').length,beforeFormal);
 assert.equal(calls('assistant-prepare').length,beforePrepare+1);assert.equal(last().execution_host,host);
 assert.equal(last().state,'prepared');assert.ok(p.assistantPrompt.value);assert.equal(p.nativeInspectionPreparing,null);
 assert.equal(calls('start').length,0);assert.ok(p.formalNote.textContent.includes('本地批次'));
 p.dialog.close();handler=hostHandler;
}
''')

    def test_native_revision_range_or_host_change_during_preparation_does_not_create_task(self):
        self.js(self.fixture + r'''
for(const change of ['revision','range','host']){
 plans=[plan()];records=[];handler=hostHandler;await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');
 const upstream=handler;let inject=true;
 handler=async(action,payload)=>{
  if(action.endsWith('/preview')&&inject){
   inject=false;
   if(change==='revision')platformDraftApplyFormal(p,formal({revision:8}));
   else if(change==='range'){p.refreshEnd.value='3';p.refreshEnd.oninput();}
   else choose(p,'codex');
  }
  return upstream(action,payload);
 };
 await p.run.onclick();assert.equal(p.nativeInspectionPreparing,null);
 assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);assert.equal(p.assistantTask,null);
 if(change==='revision')assert.equal(p.revision,8);
 if(change==='range')assert.equal(p.refreshEnd.value,'3');
 if(change==='host')assert.equal(p.assistantHost.value,'codex');
 p.dialog.close();
}
''')

    def test_actual_activity_discovered_during_native_preparation_still_blocks(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');const upstream=handler;let inject=true;
handler=async(action,payload)=>{
 if(action.endsWith('/preview')&&inject){inject=false;p.jobs=[job({done:false,state:'running'})];}
 return upstream(action,payload);
};
await p.run.onclick();assert.equal(p.nativeInspectionPreparing,null);assert.equal(p.run.disabled,true);
assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);assert.equal(p.assistantTask,null);
''')

    def test_failed_native_preparation_releases_marker_and_preserves_error(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;choose(p,'qoder-cn');const upstream=handler;
handler=async(action,payload)=>{if(action.endsWith('/refresh-plan'))throw Error('Synthetic local preparation failure');return upstream(action,payload);};
await p.run.onclick();assert.equal(p.nativeInspectionPreparing,null);assert.equal(calls('assistant-prepare').length,0);
assert.ok(p.status.textContent.includes('Synthetic local preparation failure'));const before=calls('formal-state').length;
handler=hostHandler;await platformDraftFormalState(p);assert.equal(calls('formal-state').length,before+1);
''')

    def test_native_idle_status_follows_selected_host_and_refresh_without_hiding_errors(self):
        self.js(self.fixture + r'''
blocked=false;const upstream=handler;
handler=async(action,payload)=>action.endsWith('/preview')?{...preview(payload),can_start:false,can_connect:true,connect_token:'9999999999.synthetic',automatic_connection:{available:true}}:upstream(action,payload);
await platformDraftOpen();const p=platformDraftPanel,codex=p.status.textContent;
assert.ok(codex.includes('Codex 自动连接'));assert.ok(codex.includes('Codex 额度'));
const before=requests.length;choose(p,'qoder-cn');assert.equal(requests.length,before);
assert.ok(p.status.textContent.includes('Qoder CN'));assert.ok(p.status.textContent.includes('尚未连接、检查或保存'));
assert.ok(!p.status.textContent.includes('Codex'));await p.refresh.onclick();assert.ok(!p.status.textContent.includes('Codex'));
choose(p,'workbuddy');assert.ok(p.status.textContent.includes('WorkBuddy AI'));assert.ok(p.status.textContent.includes('组件不可用'));
choose(p,'codex');assert.equal(p.status.textContent,codex);
p.status.textContent='具体错误必须保留';choose(p,'qoder-cn');assert.equal(p.status.textContent,'具体错误必须保留');
assert.equal(calls('assistant-prepare').length,0);assert.equal(calls('start').length,0);
''')

    def test_codex_automatic_inspect_and_formal_poll_keep_original_paths(self):
        self.js(self.fixture + r'''
blocked=false;await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.assistantHost.value,'codex');
const before=calls('formal-state').length;await platformDraftFormalState(p);assert.equal(calls('formal-state').length,before+1);
assert.equal(Object.hasOwn(p,'nativeInspectionPreparing'),false);
const upstream=handler;handler=async(action,payload)=>action.endsWith('/start')?job({mode:'inspect_drafts',plan_id:payload.plan_id,profile_id:payload.profile_id}):upstream(action,payload);
await p.run.onclick();assert.equal(calls('start').length,1);assert.equal(calls('assistant-prepare').length,0);
assert.equal(calls('start')[0].payload.inspect_only,true);assert.equal(calls('start')[0].payload.execute,true);
assert.equal(Object.hasOwn(p,'nativeInspectionPreparing'),false);
''')


if __name__ == '__main__':
    unittest.main()
