"""Bounded retries only read the original draft job; fake DOM and HTTP."""
import unittest

from tests import test_workbench_auto_connect_ui as ui


class WorkbenchDraftPollUITests(unittest.TestCase):
    js = ui.WorkbenchAutoConnectUITests.js

    def test_transient_job_errors_recover_and_success_resets_the_retry_budget(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();
const before=JSON.stringify([...docs]),initial=requests.length;
let reads=0;
handler=async(action,payload)=>{
 assert.equal(action,'platform-drafts/job');assert.deepEqual(payload,{id:'job-one'});
 if([1,2,4].includes(++reads))throw Error('409 workbench_book_changed');
 return {...job(),stage:'chapter_verified',verified_chapters:1};
};
const tick=async()=>{const id=p.pollTimer,t=timers.get(id);assert.ok(t);timers.delete(id);await t.fn();};
await platformDraftPoll(p);assert.equal(p.pollFailures,1);assert.ok(p.status.textContent.includes('重新读取同一任务（1/5）'));
await tick();assert.equal(p.pollFailures,2);
await tick();assert.equal(p.pollFailures,0);assert.equal(p.progress.value,1);
await tick();assert.equal(p.pollFailures,1);assert.ok(p.status.textContent.includes('（1/5）'));
assert.equal(requests.length-initial,4);assert.equal(starts().length,1);
assert.ok(requests.slice(initial).every(row=>row.action==='platform-drafts/job'));
assert.equal(JSON.stringify([...docs]),before);assert.equal(p.run.disabled,true);
''')

    def test_five_retry_limit_stops_refreshing_without_repeating_preview_or_start(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();const initial=requests.length;
handler=async(action,payload)=>{assert.equal(action,'platform-drafts/job');assert.deepEqual(payload,{id:'job-one'});throw Error('409 workbench_book_changed');};
await platformDraftPoll(p);
for(let retry=0;retry<5;retry++){
 const id=p.pollTimer,t=timers.get(id);assert.ok(t);timers.delete(id);await t.fn();
}
assert.equal(requests.length-initial,6);assert.equal(p.pollFailures,6);assert.equal(p.pollTimer,null);
assert.ok(p.status.textContent.includes('请刷新本地状态，勿重复保存'));assert.equal(p.refresh.disabled,false);
assert.equal(p.run.disabled,true);assert.equal(starts().length,1);
platformDraftSchedule(p);assert.equal(p.pollTimer,null);
assert.ok(requests.slice(initial).every(row=>row.action==='platform-drafts/job'));
''')

    def test_terminal_job_after_the_last_retry_stops_and_clears_retry_count(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();let reads=0;
handler=async()=>{if(++reads<=5)throw Error('409');return {...job(),done:true,state:'succeeded',verified_chapters:1};};
await platformDraftPoll(p);
for(let retry=0;retry<5;retry++){const id=p.pollTimer,t=timers.get(id);assert.ok(t);timers.delete(id);await t.fn();}
assert.equal(reads,6);assert.equal(p.job.done,true);assert.equal(p.pollFailures,0);assert.equal(p.pollTimer,null);
assert.ok(p.status.textContent.includes('本批草稿同步一致'));
await platformDraftPoll(p);platformDraftSchedule(p);assert.equal(reads,6);assert.equal(p.pollTimer,null);
assert.equal(starts().length,1);
''')

    def test_closing_panel_cancels_scheduled_retry_and_ignores_inflight_failure(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();
handler=async()=>{throw Error('409');};await platformDraftPoll(p);
const scheduled=timers.get(p.pollTimer);assert.ok(scheduled);const count=requests.length;
p.dialog.close();await scheduled.fn();assert.equal(requests.length,count);assert.equal(platformDraftPanel,null);
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([job()]):job();
await platformDraftOpen();const next=platformDraftPanel;let reject;
handler=()=>new Promise((_,fail)=>{reject=fail;});
const pending=platformDraftPoll(next);const oldStatus=next.status.textContent;
next.dialog.close();reject(Error('409'));await pending;
assert.equal(next.status.textContent,oldStatus);assert.equal(next.pollTimer,null);
assert.equal(starts().length,1);
''')

    def test_switched_job_rejects_old_retry_and_late_response(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();
handler=async()=>{throw Error('409');};await platformDraftPoll(p);
const oldTimer=timers.get(p.pollTimer),count=requests.length;
platformDraftDrawJob(p,{...job(),id:'job-two'});await oldTimer.fn();
assert.equal(requests.length,count);assert.equal(p.job.id,'job-two');assert.equal(p.pollFailures,0);
let release;handler=()=>new Promise(resolve=>{release=resolve;});
const pending=platformDraftPoll(p);
platformDraftDrawJob(p,{...job(),id:'job-three',verified_chapters:1});
const status=p.status.textContent;release({...job(),id:'job-two',done:true,state:'failed'});await pending;
assert.equal(p.job.id,'job-three');assert.equal(p.status.textContent,status);assert.equal(p.pollTimer,null);
assert.equal(starts().length,1);
''')

    def test_completed_job_ignores_late_failure_and_mismatched_response_cannot_switch_jobs(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();
handler=async()=>({...job(),id:'different-job'});await platformDraftPoll(p);
assert.equal(p.job.id,'job-one');assert.equal(p.pollFailures,1);
let reject;handler=()=>new Promise((_,fail)=>{reject=fail;});
const pending=platformDraftPoll(p);
platformDraftDrawJob(p,{...job(),done:true,state:'failed'});const status=p.status.textContent;
reject(Error('409'));await pending;
assert.equal(p.status.textContent,status);assert.equal(p.pollFailures,0);assert.equal(p.pollTimer,null);
assert.equal(starts().length,1);
''')


if __name__ == "__main__":
    unittest.main()
