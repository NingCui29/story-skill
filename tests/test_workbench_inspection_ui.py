"""Confirm the two explicit UI actions and recovery without real platform writes."""
import unittest
from tests import test_workbench_platform_draft_ui as ui


class InspectionUITests(unittest.TestCase):
    js = ui.PlatformDraftUITests.js
    fixture = r'''
const inspected=()=>job({id:'inspection-one',profile_id:profile.id,profile_sha256:profile.sha256,
 mode:'inspect_drafts',done:true,state:'inspection_ready',stage:'finished',finished_at:new Date().toISOString(),
 checked_chapters:2,result:{can_execute:true,inspection:true,source_matches_current:true,
 change_counts:{create:1,update:1,unchanged:0,blocked:0},changes:[
 {chapter:1,title:'本地章名',action:'update',difference:{title_changed:true,body_changed:true,
 remote_title:'平台章名',local_title:'本地章名',remote_characters:4,local_characters:6,body_diff:'-旧正文\n+新正文'}},
 {chapter:2,title:'新章',action:'create'}]}});
const withFormal=()=>{const original=handler;handler=(action,payload)=>action.endsWith('/formal-state')?catalog():original(action,payload);};
const starts=()=>requests.filter(r=>r.action.endsWith('/start'));
'''

    def test_prepare_inspects_then_second_explicit_action_executes_exact_inspection(self):
        self.js(self.fixture + r'''
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog();
 if(action.endsWith('/preview'))return preview(payload);
 if(action.endsWith('/start'))return payload.inspect_only?inspected():job();
 throw Error(action);
};
withFormal();await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();
assert.equal(starts().length,1);assert.equal(starts()[0].payload.inspect_only,true);
assert.equal(starts()[0].payload.sync_drafts,false);
assert.equal(p.run.textContent,'确认同步这些变更');assert.equal(p.run.disabled,false);
assert.ok(textTree(p.results).includes('平台章名'));assert.ok(textTree(p.results).includes('-旧正文'));
assert.ok(p.progressLabel.textContent.includes('未写入平台'));
await p.run.onclick();assert.equal(starts().length,2);
assert.equal(starts()[1].payload.sync_drafts,true);assert.equal(starts()[1].payload.inspection_id,'inspection-one');
''')

    def test_reopen_restores_inspection_without_executing_or_counting_history(self):
        self.js(self.fixture + r'''
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan()],[inspected()]):preview(payload);
withFormal();await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.inspection.id,'inspection-one');assert.equal(p.run.textContent,'确认同步这些变更');
p.dialog.close();await platformDraftOpen();assert.equal(starts().length,0);
assert.equal(platformDraftPanel.inspection.id,'inspection-one');
assert.ok(platformDraftPanel.progressLabel.textContent.includes('差异检查 2 / 2'));
''')

    def test_expiry_and_changed_range_require_new_inspection(self):
        self.js(self.fixture + r'''
withFormal();await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,inspected());
const expiry=timers.get(p.expiryTimer);assert.ok(expiry);expiry.fn();
assert.equal(p.inspection,null);assert.ok(p.status.textContent.includes('已过期'));
platformDraftDrawJob(p,inspected());p.refreshEnd.value='3';p.refreshEnd.oninput();
assert.equal(p.inspection,null);assert.equal(p.run.disabled,true);assert.equal(starts().length,0);
''')

    def test_all_identical_needs_no_write_and_blocked_inspection_explains_chapter(self):
        self.js(self.fixture + r'''
withFormal();await platformDraftOpen();const p=platformDraftPanel,j=inspected();j.result.change_counts={create:0,update:0,unchanged:2,blocked:0};
platformDraftDrawJob(p,j);assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('无需同步'));
j.state='inspection_blocked';j.result.can_execute=false;
j.result.changes=[{chapter:2,title:'第二章',action:'blocked',reason:'platform_browser_unnumbered_draft'}];
platformDraftDrawJob(p,j);assert.ok(p.status.textContent.includes('停止原因：第2章'));
assert.ok(p.nextHint.textContent.includes('不会猜测并覆盖'));assert.equal(starts().length,0);
''')

    def test_stop_requests_cooperative_stop_without_resending(self):
        self.js(self.fixture + r'''
withFormal();await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,job());
handler=async(action,payload)=>{assert.equal(action,'platform-drafts/stop');assert.equal(payload.id,'job-one');return job({stop_requested:true});};
await p.stop.onclick();assert.equal(p.stop.disabled,true);assert.equal(p.job.stop_requested,true);
assert.equal(starts().length,0);assert.ok($('platform-drafts-open').textContent.includes('正在停止'));
''')


if __name__ == '__main__':
    unittest.main()
