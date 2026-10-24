"""Run the generated platform panel with isolated DOM/HTTP/timer fixtures."""
import ast
from pathlib import Path
import shutil
import subprocess
import unittest

import test_workbench as base


class FormalRefreshUITests(unittest.TestCase):
    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        fixture = ast.parse((Path(__file__).with_name('test_workbench_platform_draft_ui.py')).read_text())
        mock = next(n.value for n in ast.walk(fixture) if isinstance(n, ast.Constant)
                    and isinstance(n.value, str) and n.value.startswith("\nconst assert=require('assert');"))
        mock = mock.replace("'profile-sha'", "'a'.repeat(64)")
        helpers = r'''
const focusListeners=new Set(),window={addEventListener:(name,fn)=>{assert.equal(name,'focus');focusListeners.add(fn);},removeEventListener:(name,fn)=>focusListeners.delete(fn)};
const formal=(extra={})=>({ok:true,book_id:'book-one',revision:7,formal_first:1,formal_last:13,formal_count:13,max_batch:1000,scope:'local_formal_state_only',remote_accessed:false,submission_performed:false,...extra});
const originalHandler=handler;
handler=async(action,payload)=>action.endsWith('/formal-state')?formal():originalHandler(action,payload);
const refreshed=(payload,extra={})=>({ok:true,book_id:'book-one',revision:payload.expected_revision,base_plan_id:payload.plan_id,plan_id:'new-plan',profile_id:payload.profile_id,profile_sha256:payload.profile_sha256,platform:'fanqie',account_id:'account',remote_book_id:'remote',start:payload.start,end:payload.end,chapters:Array.from({length:payload.end-payload.start+1},(_,i)=>payload.start+i),scope:'local_formal_refresh_only',remote_accessed:false,submission_performed:false,plan_reused:false,...extra});
const count=action=>requests.filter(row=>row.action.endsWith('/'+action)).length;
const idleTimers=()=>[...timers.values()].filter(row=>row.delay===5000);
'''
        source = base.story.workbench._editor_platform_drafts_script()
        program = mock + helpers + source + '\n(async()=>{' + test + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node], input=program, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_open_focus_idle_and_close_read_only_without_polling_leaks(self):
        self.js(r'''
const before=JSON.stringify([...docs]);await platformDraftOpen();const p=platformDraftPanel;
assert.ok(textTree(p.content).includes('已生成批次'));assert.ok(p.formalRange.textContent.includes('第1—13章'));assert.ok(p.selectedRange.textContent.includes('第1—2章'));
assert.equal(p.refreshStart.value,'1');assert.equal(p.refreshEnd.value,'13');assert.equal(p.generate.textContent,'按第1—13章生成新批次');assert.ok(p.run.textContent.includes('所选第1—2章'));
assert.equal(count('formal-state'),1);assert.equal(focusListeners.size,1);assert.equal(idleTimers().length,1);
await [...focusListeners][0]();await idleTimers()[0].fn();assert.equal(count('formal-state'),3);assert.equal(idleTimers().length,1);
assert.equal(count('refresh-plan'),0);assert.equal(count('bind'),0);assert.equal(count('start'),0);assert.equal(JSON.stringify([...docs]),before);
p.dialog.close();assert.equal(focusListeners.size,0);assert.equal(idleTimers().length,0);await platformDraftOpen();assert.equal(focusListeners.size,1);assert.equal(idleTimers().length,1);platformDraftPanel.dialog.close();assert.equal(timers.size,0);
''')

    def test_new_formal_chapters_invalidate_token_without_changing_frozen_plan(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel,old=JSON.stringify(p.plans),previews=count('preview'),catalogs=count('catalog');assert.equal(p.run.disabled,false);
handler=async(action)=>{assert.ok(action.endsWith('/formal-state'));return formal({revision:8,formal_last:14,formal_count:14});};
await platformDraftFormalState(p);assert.equal(p.preview,null);assert.equal(p.previewExpires,0);assert.equal(p.run.disabled,true);assert.equal(p.previewBox.children.length,0);
assert.equal(p.refreshStart.value,'1');assert.equal(p.refreshEnd.value,'14');assert.equal(p.bindStart.value,'13');assert.equal(p.bindEnd.value,'14');assert.ok(p.formalNote.textContent.includes('新增已登记正式章'));
assert.equal(JSON.stringify(p.plans),old);assert.equal(p.plan.value,'plan-1');assert.equal(count('preview'),previews);assert.equal(count('catalog'),catalogs);assert.equal(count('refresh-plan'),0);assert.equal(count('start'),0);
await p.run.onclick();assert.equal(count('start'),0);
handler=async(action,payload)=>preview(payload);await p.recheck.onclick();assert.equal(p.run.disabled,false);assert.ok(p.run.textContent.includes('所选第1—2章'));
''')

    def test_manual_ranges_binding_and_reconciliation_are_not_overwritten(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;p.refreshStart.value='3';p.refreshEnd.value='7';p.refreshEnd.oninput();p.bindStart.value='5';p.bindEnd.value='6';p.bindEnd.oninput();p.bindAccount.value='12345';p.bindAccount.oninput();
let last=14;handler=async()=>formal({revision:last,formal_last:last,formal_count:last});await platformDraftFormalState(p);
assert.equal(p.refreshStart.value,'3');assert.equal(p.refreshEnd.value,'7');assert.equal(p.bindStart.value,'5');assert.equal(p.bindEnd.value,'6');assert.equal(p.bindAccount.value,'12345');assert.equal(p.generate.disabled,true);
p.bindingDirty=false;p.refreshRangeDirty=false;p.bindRangeDirty=false;p.mode.value='reconcile_only';last=15;await platformDraftFormalState(p);assert.equal(p.refreshEnd.value,'7');assert.equal(p.bindEnd.value,'6');assert.equal(p.mode.value,'reconcile_only');assert.equal(p.generate.disabled,true);assert.ok(p.refreshHint.textContent.includes('历史批次'));
p.jobs=[job()];const calls=count('formal-state');last=16;await platformDraftFormalState(p);assert.equal(count('formal-state'),calls);assert.equal(p.formalLast,15);
''')

    def test_explicit_generation_preserves_target_then_selects_and_previews_only(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);p.refreshStart.value='3';p.refreshEnd.value='13';p.refreshStart.oninput();let created=false;
handler=async(action,payload)=>{
 if(action.endsWith('/refresh-plan')){created=true;return refreshed(payload);}
 if(action.endsWith('/catalog'))return catalog([plan(),plan('new-plan',{chapters:Array.from({length:11},(_,i)=>i+3)})]);
 if(action.endsWith('/preview'))return preview(payload);
 throw Error(action);
};await p.generate.onclick();assert.ok(created);assert.equal(count('refresh-plan'),1);const sent=requests.find(row=>row.action.endsWith('/refresh-plan')).payload;
assert.deepEqual(sent,{plan_id:'plan-1',profile_id:profile.id,profile_sha256:profile.sha256,expected_revision:7,start:3,end:13});assert.equal(p.plan.value,'new-plan');assert.equal(p.profile.value,profile.id);assert.equal(p.preview.plan_id,'new-plan');assert.equal(p.run.disabled,false);assert.ok(p.run.textContent.includes('第3—13章'));assert.ok(p.formalNote.textContent.includes('尚未访问平台'));assert.equal(count('start'),0);assert.equal(count('bind'),0);assert.equal(JSON.stringify([...docs]),before);
''')

    def test_blank_invalid_gapped_or_oversized_ranges_never_generate(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
for(const [start,end]of [['','13'],['1',''],['0','2'],['1.5','2'],['4','3'],['1','14']]){p.refreshStart.value=start;p.refreshEnd.value=end;p.refreshStart.oninput();assert.equal(p.generate.disabled,true);await p.generate.onclick();}
p.formalLast=2000;p.formalCount=2000;p.refreshStart.value='1';p.refreshEnd.value='1001';p.refreshEnd.oninput();assert.equal(p.generate.disabled,true);await p.generate.onclick();
p.formalLast=13;p.formalCount=12;p.refreshEnd.value='13';p.refreshEnd.oninput();assert.equal(p.generate.disabled,true);await p.generate.onclick();
p.formalFirst=null;p.formalLast=null;p.formalCount=0;platformDraftFormalRange(p);platformDraftButtons(p);assert.equal(p.generate.disabled,true);assert.ok(p.formalRange.textContent.includes('暂无'));assert.equal(count('refresh-plan'),0);
''')

    def test_profile_plan_hash_and_mode_must_match_preview_before_start(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel,snapshot=p.preview;
for(const field of ['book_id','plan_id','profile_id','profile_sha256','mode']){p.preview={...snapshot,[field]:'different'};assert.equal(platformDraftCanStart(p),false);await p.run.onclick();}
p.preview=snapshot;p.profiles=[{...profile,sha256:'b'.repeat(64)}];assert.equal(platformDraftCanStart(p),false);await p.run.onclick();assert.equal(count('start'),0);
''')

    def test_unsupported_platform_and_wrong_generation_receipt_never_switch_or_start(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
p.plans=[plan('other',{platform:'qidian'})];p.plan.value='other';platformDraftFormalRange(p);platformDraftButtons(p);assert.equal(p.generate.disabled,true);assert.ok(p.refreshHint.textContent.includes('仅支持'));await p.generate.onclick();assert.equal(count('refresh-plan'),0);
p.plans=[plan()];p.plan.value='plan-1';platformDraftFormalRange(p,true);
handler=async(action,payload)=>refreshed(payload,{account_id:'other-account'});await p.generate.onclick();assert.equal(p.plan.value,'plan-1');assert.equal(p.preview,null);assert.ok(p.formalNote.textContent.includes('回执无法确认'));assert.equal(count('start'),0);
''')

    def test_late_preview_and_formal_response_cannot_revive_token_or_closed_panel(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;let releasePreview;
handler=async(action,payload)=>action.endsWith('/preview')?new Promise(resolve=>{releasePreview=()=>resolve(preview(payload));}):formal({revision:8,formal_last:14,formal_count:14});
const pending=platformDraftPreview(p);await platformDraftFormalState(p);releasePreview();await pending;assert.equal(p.preview,null);assert.equal(p.run.disabled,true);
let release;handler=async()=>new Promise(resolve=>{release=resolve;});const reading=platformDraftFormalState(p);p.dialog.close();release(formal({revision:9,formal_last:15,formal_count:15}));await reading;assert.equal(platformDraftPanel,null);assert.equal(focusListeners.size,0);assert.equal(idleTimers().length,0);assert.equal(count('start'),0);
''')

    def test_duplicate_generate_and_closed_result_do_not_resume_or_access_platform(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;let release;handler=async(action,payload)=>new Promise(resolve=>{release=()=>resolve(refreshed(payload));});
const pending=p.generate.onclick();await p.generate.onclick();assert.equal(count('refresh-plan'),1);p.dialog.close();release();await pending;assert.equal(platformDraftPanel,null);assert.equal(count('start'),0);assert.equal(count('catalog'),1);assert.equal(idleTimers().length,0);
''')

    def test_config_or_target_change_after_generation_blocks_automatic_preview(self):
        self.js(r'''
for(const change of ['profile','target','chapters']){
 handler=async(action,payload)=>action.endsWith('/formal-state')?formal():originalHandler(action,payload);await platformDraftOpen();const p=platformDraftPanel,previews=count('preview');
 handler=async(action,payload)=>{
  if(action.endsWith('/refresh-plan'))return refreshed(payload);
  if(action.endsWith('/catalog'))return catalog([plan(),plan('new-plan',{chapters:change==='chapters'?[1,2]:Array.from({length:13},(_,i)=>i+1),...(change==='target'?{account_id:'other-account'}:{})})],[],{profiles:[{...profile,sha256:change==='profile'?'b'.repeat(64):profile.sha256}]});
  throw Error('No automatic preview: '+action);
 };await p.generate.onclick();assert.equal(count('preview'),previews);assert.equal(p.preview,null);assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('未自动预览'));assert.equal(count('start'),0);p.dialog.close();
}
''')

    def test_successful_retry_clears_only_its_read_failure_note(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;p.refreshEnd.value='7';p.refreshEnd.oninput();handler=async()=>{throw Error('local temporary failure');};
await platformDraftFormalState(p);assert.ok(p.formalNote.textContent.includes('暂未刷新'));assert.equal(p.plan.value,'plan-1');assert.equal(p.refreshEnd.value,'7');assert.equal(idleTimers().length,1);
handler=async()=>formal();await platformDraftFormalState(p);assert.equal(p.formalNote.textContent,'');assert.equal(p.refreshEnd.value,'7');p.formalNote.textContent='已生成本地批次';await platformDraftFormalState(p);assert.equal(p.formalNote.textContent,'已生成本地批次');assert.equal(count('refresh-plan'),0);assert.equal(count('start'),0);
''')


if __name__ == '__main__':
    unittest.main()
