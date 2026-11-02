"""A delayed stop receipt cannot replace a newer platform-job observation."""
import unittest

import test_workbench_platform_draft_ui as ui


class PlatformStopRaceTests(unittest.TestCase):
    js = ui.PlatformDraftUITests.js
    fixture = r'''
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
const running=(extra={})=>job({profile_id:profile.id,profile_sha256:profile.sha256,stage:'reading_back',...extra});
const terminal=(extra={})=>running({done:true,state:'succeeded',stage:'finished',finished_at:new Date().toISOString(),verified_chapters:2,accepted_chapters:2,result:{source_matches_current:true,results:[]},...extra});
const originalHandler=handler;
const baseHandler=async(action,payload)=>action.endsWith('/formal-state')?catalog():action.endsWith('/history')?{id:payload.plan_id,book_id:'book-one',remote_accessed:false,history_events_total:0,results:[]}:originalHandler(action,payload);
handler=baseHandler;
const stopCount=()=>requests.filter(row=>row.action==='platform-drafts/stop').length;
const noStarts=()=>assert.equal(requests.filter(row=>row.action==='platform-drafts/start').length,0);
'''

    def test_late_stop_success_cannot_revive_a_terminal_job(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());let release;
handler=async(action,payload)=>action.endsWith('/stop')?new Promise(resolve=>release=resolve):action.endsWith('/job')?terminal():baseHandler(action,payload);
const stopping=p.stop.onclick();await flush();await platformDraftPoll(p);
const finished=p.job,status=p.status.textContent;assert.equal(finished.done,true);assert.equal(Number(p.progress.value),2);
release(running({stop_requested:true}));await stopping;
assert.strictEqual(p.job,finished);assert.equal(p.status.textContent,status);assert.equal(Number(p.progress.value),2);
assert.equal(p.stop.hidden,true);assert.equal(p.pollTimer,null);assert.equal(p.stopRequest,null);assert.equal(stopCount(),1);noStarts();
''')

    def test_late_stop_failure_cannot_overwrite_a_terminal_status(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());let reject;
handler=async(action,payload)=>action.endsWith('/stop')?new Promise((resolve,fail)=>reject=fail):action.endsWith('/job')?terminal():baseHandler(action,payload);
const stopping=p.stop.onclick();await flush();await platformDraftPoll(p);const status=p.status.textContent;
reject(Error('late stop failure'));await stopping;
assert.equal(p.job.done,true);assert.equal(p.status.textContent,status);assert.equal(Number(p.progress.value),2);assert.equal(p.stop.hidden,true);assert.equal(p.stopRequest,null);noStarts();
''')

    def test_newer_running_poll_survives_late_success_and_can_be_stopped_again(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());let release;
handler=async(action,payload)=>action.endsWith('/stop')?new Promise(resolve=>release=resolve):action.endsWith('/job')?running({verified_chapters:1,accepted_chapters:1,stage:'chapter_verified'}):baseHandler(action,payload);
const stopping=p.stop.onclick();await flush();await platformDraftPoll(p);
const observed=p.job,status=p.status.textContent,poll=p.pollTimer;assert.equal(Number(p.progress.value),1);
// Button rendering may run while the first network request is still pending.
await p.stop.onclick();assert.equal(stopCount(),1);
release(running({stop_requested:true}));await stopping;
assert.strictEqual(p.job,observed);assert.equal(p.status.textContent,status);assert.equal(Number(p.progress.value),1);assert.equal(p.pollTimer,poll);assert.ok(timers.has(poll));assert.equal(p.stopRequest,null);
handler=async(action,payload)=>{assert.equal(action,'platform-drafts/stop');return {...observed,stop_requested:true};};
await p.stop.onclick();assert.equal(stopCount(),2);assert.equal(p.job.stop_requested,true);assert.equal(p.stop.disabled,true);noStarts();
''')

    def test_newer_running_poll_survives_late_failure_without_losing_its_timer(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());let reject;
handler=async(action,payload)=>action.endsWith('/stop')?new Promise((resolve,fail)=>reject=fail):action.endsWith('/job')?running({verified_chapters:1,stage:'chapter_verified'}):baseHandler(action,payload);
const stopping=p.stop.onclick();await flush();await platformDraftPoll(p);
const current=p.job,status=p.status.textContent,timer=p.pollTimer;reject(Error('old failure'));await stopping;
assert.strictEqual(p.job,current);assert.equal(p.status.textContent,status);assert.equal(p.pollTimer,timer);assert.ok(timers.has(timer));assert.equal(p.stopRequest,null);noStarts();
''')

    def test_in_place_terminal_transition_also_rejects_both_late_outcomes(self):
        self.js(self.fixture + r'''
for(const fails of [false,true]){
 handler=baseHandler;await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());let release,reject;
 handler=()=>new Promise((resolve,fail)=>{release=resolve;reject=fail;});const stopping=p.stop.onclick();await flush();
 const same=p.job;same.done=true;p.status.textContent='terminal observed on the same object';const status=p.status.textContent;
 if(fails)reject(Error('late failure'));else release(running({stop_requested:true}));await stopping;
 assert.strictEqual(p.job,same);assert.equal(p.job.done,true);assert.equal(p.status.textContent,status);assert.equal(p.stopRequest,null);p.dialog.close();
}
noStarts();
''')

    def test_late_failure_cannot_contaminate_a_new_batch_preview(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());p.plans.push(plan('next-plan',{chapters:[3,4]}));let reject;
handler=async(action,payload)=>action.endsWith('/stop')?new Promise((resolve,fail)=>reject=fail):action.endsWith('/job')?terminal():baseHandler(action,payload);
const stopping=p.stop.onclick();await flush();await platformDraftPoll(p);
p.plan.value='next-plan';await p.plan.onchange();const preview=p.preview,status=p.status.textContent;
reject(Error('previous batch stop failure'));await stopping;
assert.equal(p.job,null);assert.strictEqual(p.preview,preview);assert.equal(p.preview.plan_id,'next-plan');assert.equal(p.status.textContent,status);assert.equal(p.stopRequest,null);noStarts();
''')

    def test_late_success_cannot_replace_a_new_task_in_the_same_panel(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());let release;
handler=()=>new Promise(resolve=>release=resolve);const stopping=p.stop.onclick();await flush();
platformDraftDrawJob(p,running({id:'another-job',verified_chapters:1}));platformDraftSchedule(p);const current=p.job,status=p.status.textContent,timer=p.pollTimer;
release(running({stop_requested:true}));await stopping;
assert.strictEqual(p.job,current);assert.equal(p.status.textContent,status);assert.equal(p.pollTimer,timer);assert.equal(p.stopRequest,null);noStarts();
''')

    def test_closed_old_panel_does_not_modify_a_reopened_panel(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const old=platformDraftPanel;platformDraftDrawJob(old,running());let reject;
handler=()=>new Promise((resolve,fail)=>reject=fail);const stopping=old.stop.onclick();await flush();old.dialog.close();
handler=baseHandler;await platformDraftOpen();const current=platformDraftPanel,status=current.status.textContent;
reject(Error('closed panel failure'));await stopping;
assert.strictEqual(platformDraftPanel,current);assert.equal(current.status.textContent,status);assert.equal(current.job,null);assert.equal(old.stopRequest,null);noStarts();
''')

    def test_current_stop_failure_allows_a_new_explicit_stop_request(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());handler=async()=>{throw Error('stop request unavailable');};
await p.stop.onclick();assert.ok(p.status.textContent.includes('停止请求未确认'));assert.equal(p.stop.disabled,false);assert.equal(p.stopRequest,null);
handler=async()=>running({stop_requested:true});await p.stop.onclick();assert.equal(stopCount(),2);assert.equal(p.job.stop_requested,true);assert.equal(p.stop.disabled,true);assert.ok(timers.has(p.pollTimer));noStarts();
''')

    def test_mismatched_stop_packet_is_not_drawn_as_another_job(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;platformDraftDrawJob(p,running());const observed=p.job;
handler=async()=>running({id:'different-job',stop_requested:true});await p.stop.onclick();
assert.strictEqual(p.job,observed);assert.ok(p.status.textContent.includes('返回的停止状态与当前任务不一致'));assert.equal(p.stopRequest,null);assert.equal(p.stop.disabled,false);noStarts();
''')


if __name__ == '__main__':
    unittest.main()
