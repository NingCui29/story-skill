"""Failed task reads preserve the visible task's single status poll and drafts."""
import unittest

import test_workbench_assistant_tasks_ui as ui


class TaskReadFailureTests(unittest.TestCase):
    def js(self, case, *, running=True):
        ui.AssistantTaskUITests.js(self, r'''
const timers=new Map();let timerSequence=0;
globalThis.setTimeout=callback=>{timers.set(++timerSequence,callback);return timerSequence;};
globalThis.clearTimeout=id=>timers.delete(id);
const tick=async()=>{assert.equal(timers.size,1);const [id,callback]=[...timers][0];timers.delete(id);callback();await flush();};
''' + (r'''
task.host_execution={id:'attempt',state:'running',status_label:'正在执行',can_cancel:true};
await assistantTasksOpen('one');const p=assistantTaskPanel;assert.equal(timers.size,1);
''' if running else '') + case)

    def test_failed_reread_restores_one_read_only_poll(self):
        self.js(r'''
handler=async(action)=>{if(action==='tasks/get')throw Error('temporary read failure');return {task:{...task,host_execution:{id:'attempt',state:'succeeded'}}};};
for(let i=0;i<3;i++){
 await find(p.detail,'重新读取本任务').onclick();
 assert.equal(p.task.host_execution.state,'running');assert.equal(p.busy,false);assert.equal(timers.size,1);
}
await tick();assert.equal(p.task.host_execution.state,'succeeded');assert.equal(timers.size,0);
assert.equal(requests.filter(row=>row.action==='tasks/execution').length,1);
assert.ok(requests.every(row=>['tasks/list','tasks/get','tasks/execution'].includes(row.action)));
''')

    def test_failed_switch_preserves_old_inputs_and_resumes_old_task(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;
const input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
input.value='retained before execution began elsewhere';input.listeners.input();
task.host_execution={id:'attempt',state:'running',can_cancel:true};
await assistantTaskShow(p,'one');
const retained=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
assert.equal(retained.value,input.value);assert.equal(retained.readOnly,true);
handler=async(action,data)=>{if(action==='tasks/get')throw Error('second task unavailable');return {task:{...task,host_execution:{id:'attempt',state:'succeeded'}}};};
await assistantTaskShow(p,'two');assert.equal(p.task.id,'one');assert.equal(timers.size,1);
assert.strictEqual(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON'),retained);
assert.equal(retained.value,input.value);assert.equal(assistantTaskFormBuffers()[0].dirty,true);
await tick();const poll=requests.find(row=>row.action==='tasks/execution');assert.equal(poll.data.task_id,'one');
assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,input.value);
''', running=False)

    def test_successful_read_switches_poll_to_new_task_once(self):
        self.js(r'''
handler=async(action,data)=>({task:{...task,id:data.task_id,host_execution:action==='tasks/execution'?{id:'second',state:'succeeded'}:{id:'second',state:'running'}}});
await assistantTaskShow(p,'two');assert.equal(p.task.id,'two');assert.equal(timers.size,1);
await tick();assert.equal(timers.size,0);assert.equal(p.task.host_execution.state,'succeeded');
const polls=requests.filter(row=>row.action==='tasks/execution');assert.equal(polls.length,1);assert.equal(polls[0].data.task_id,'two');
''')

    def test_successful_terminal_read_does_not_restore_old_poll(self):
        self.js(r'''
handler=async()=>({task:{...task,host_execution:{id:'attempt',state:'succeeded'}}});
await assistantTaskShow(p,'one');assert.equal(timers.size,0);assert.equal(p.task.host_execution.state,'succeeded');
assert.equal(requests.filter(row=>row.action==='tasks/execution').length,0);
''')

    def test_failed_read_of_terminal_task_does_not_start_poll(self):
        self.js(r'''
task.host_execution={id:'attempt',state:'succeeded'};await assistantTasksOpen('one');const p=assistantTaskPanel;
assert.equal(timers.size,0);handler=async()=>{throw Error('temporary read failure');};
await assistantTaskShow(p,'one');assert.equal(p.task.host_execution.state,'succeeded');assert.equal(timers.size,0);
''', running=False)

    def test_closed_panel_does_not_resume_poll_after_late_failure(self):
        self.js(r'''
let reject;handler=()=>new Promise((resolve,fail)=>reject=fail);
const reading=assistantTaskShow(p,'one');await flush();assert.equal(timers.size,0);p.dialog.close();
reject(Error('late read failure'));await reading;assert.equal(timers.size,0);assert.equal(assistantTaskPanel,null);
''')

    def test_late_failure_cannot_replace_new_panels_poll(self):
        self.js(r'''
let reject;handler=()=>new Promise((resolve,fail)=>reject=fail);
const reading=assistantTaskShow(p,'one');await flush();p.dialog.close();
handler=async(action,data)=>action==='tasks/list'?{tasks:[{...task,id:'two'}],has_more:false}:{task:{...task,id:data.task_id,host_execution:action==='tasks/execution'?{id:'second',state:'succeeded'}:{id:'second',state:'running'}}};
await assistantTasksOpen('two');const latest=assistantTaskPanel;assert.equal(timers.size,1);
reject(Error('old read failed'));await reading;assert.strictEqual(assistantTaskPanel,latest);assert.equal(timers.size,1);
await tick();assert.equal(latest.task.id,'two');assert.equal(latest.task.host_execution.state,'succeeded');
const polls=requests.filter(row=>row.action==='tasks/execution');assert.equal(polls.length,1);assert.equal(polls[0].data.task_id,'two');
''')

    def test_unknown_mutation_is_not_automatically_replayed(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';
handler=async()=>{throw Error('save receipt unknown');};await find(p.detail,'核对并导入').onclick();
assert.equal(timers.size,0);assert.equal(assistantTaskFormBuffers()[0].needs_recovery,true);
assert.equal(requests.filter(row=>row.action==='tasks/import').length,1);
assert.equal(find(p.detail,'核对并导入').disabled,true);
''', running=False)


if __name__ == '__main__':
    unittest.main()
