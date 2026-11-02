"""Delayed receipt reads, non-mutating examples, and interrupted progress polls."""
import unittest

import test_workbench_assistant_tasks_ui as ui


class TaskInputRaceTests(unittest.TestCase):
    def js(self, case):
        ui.AssistantTaskUITests.js(self, case)

    def test_older_file_cannot_replace_newer_file_or_manual_input(self):
        self.js(r'''
await assistantTasksOpen('one');let p=assistantTaskPanel;
const file=p.detail.querySelectorAll('input').find(e=>e.type==='file');
const input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
let release;file.files=[{size:20,text:()=>new Promise(resolve=>release=resolve)}];
const first=file.onchange();await flush();
file.files=[{size:20,text:async()=>'{"file":"second"}'}];await file.onchange();
assert.equal(input.value,'{"file":"second"}');assert.equal(assistantTaskFormBuffers()[0].dirty,true);
input.value='{"file":"second","note":"new input"}';input.listeners.input();
release('{"file":"first"}');await first;assert.equal(input.value,'{"file":"second","note":"new input"}');
p.dialog.close();await assistantTasksOpen('one');p=assistantTaskPanel;
assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'{"file":"second","note":"new input"}');
assert.ok(requests.every(r=>['tasks/list','tasks/get'].includes(r.action)));
''')

    def test_typing_during_file_read_keeps_new_input(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;
const file=p.detail.querySelectorAll('input').find(e=>e.type==='file'),input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
input.value='original';let release;file.files=[{size:20,text:()=>new Promise(resolve=>release=resolve)}];
const reading=file.onchange();input.value='new text';input.listeners.input();release('file text');await reading;
assert.equal(input.value,'new text');assert.ok(p.status.textContent.includes('已保留当前输入'));
''')

    def test_late_read_cannot_restore_discarded_form(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;
const file=p.detail.querySelectorAll('input').find(e=>e.type==='file'),input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
input.value='discard this input';input.listeners.input();let release;
file.files=[{size:20,text:()=>new Promise(resolve=>release=resolve)}];const reading=file.onchange();
find(p.detail,'放弃本任务在本页的未提交输入').onclick();release('late file');await reading;
assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'');assert.deepEqual(assistantTaskFormBuffers(),[]);
''')

    def test_closed_panel_ignores_late_file_read(self):
        self.js(r'''
await assistantTasksOpen('one');let p=assistantTaskPanel;
const file=p.detail.querySelectorAll('input').find(e=>e.type==='file'),input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
input.value='keep before close';let release;file.files=[{size:20,text:()=>new Promise(resolve=>release=resolve)}];const reading=file.onchange();
p.dialog.close();await assistantTasksOpen('one');p=assistantTaskPanel;release('late file');await reading;
assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'keep before close');
''')

    def test_file_errors_and_invalid_selection_preserve_existing_input(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;
const file=p.detail.querySelectorAll('input').find(e=>e.type==='file'),input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');input.value='original';
file.files=[{size:20,text:async()=>{throw Error('unreadable')}}];await file.onchange();assert.equal(input.value,'original');assert.ok(p.status.textContent.includes('读取失败'));
let release;file.files=[{size:20,text:()=>new Promise(resolve=>release=resolve)}];const reading=file.onchange();
file.files=[{size:2*1024*1024,text:async()=>{throw Error('must not read')}}];await file.onchange();release('late input');await reading;
assert.equal(input.value,'original');assert.ok(p.status.textContent.includes('超过 1 MiB'));
''')

    def test_format_example_is_separate_and_never_changes_receipt_draft(self):
        self.js(r'''
await assistantTasksOpen('one');let p=assistantTaskPanel;
const input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON'),button=find(p.detail,'查看回执格式');
button.onclick();assert.equal(input.value,'');assert.deepEqual(assistantTaskFormBuffers(),[]);assert.equal(button['aria-expanded'],'true');
const format=p.detail.querySelectorAll('pre').find(e=>e['aria-label']==='助手回执格式示例');assert.equal(format.hidden,false);assert.deepEqual(JSON.parse(format.textContent),task.result_template);
input.value='{"summary":"keep this receipt"}';input.listeners.input();button.onclick();assert.equal(format.hidden,true);assert.equal(button['aria-expanded'],'false');button.onclick();
p.dialog.close();await assistantTasksOpen('one');p=assistantTaskPanel;
assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'{"summary":"keep this receipt"}');
assert.ok(requests.every(r=>['tasks/list','tasks/get'].includes(r.action)));
''')

    def poll_js(self, case):
        self.js(r'''
const timers=new Map();let sequence=0;
globalThis.setTimeout=callback=>{timers.set(++sequence,callback);return sequence;};globalThis.clearTimeout=id=>timers.delete(id);
const tick=async()=>{assert.equal(timers.size,1);const [id,callback]=[...timers][0];timers.delete(id);callback();await flush();};
task.host_execution={id:'attempt',state:'running',status_label:'正在执行',can_cancel:true};
await assistantTasksOpen('one');const p=assistantTaskPanel;
''' + case)

    def test_progress_poll_resumes_after_slow_list_refresh(self):
        self.poll_js(r'''
let release;handler=action=>action==='tasks/list'?new Promise(resolve=>release=resolve):Promise.resolve({task:{...task,host_execution:{id:'attempt',state:'succeeded',status_label:'执行完成'}}});
const refresh=assistantTasksList(p,true);await flush();await tick();await tick();
assert.equal(requests.filter(r=>r.action==='tasks/execution').length,0);assert.equal(timers.size,1);
release({tasks:[task],has_more:false});await refresh;await tick();
assert.equal(requests.filter(r=>r.action==='tasks/execution').length,1);assert.equal(p.task.host_execution.state,'succeeded');assert.equal(timers.size,0);
''')

    def test_progress_poll_resumes_after_failed_list_refresh(self):
        self.poll_js(r'''
let reject;handler=action=>action==='tasks/list'?new Promise((resolve,fail)=>reject=fail):Promise.resolve({task:{...task,host_execution:{id:'attempt',state:'succeeded'}}});
const refresh=assistantTasksList(p,true);await flush();await tick();reject(Error('list temporarily unavailable'));await refresh;await tick();
assert.equal(requests.filter(r=>r.action==='tasks/execution').length,1);assert.equal(timers.size,0);
''')

    def test_closing_panel_cancels_deferred_poll(self):
        self.poll_js(r'''
let release;handler=()=>new Promise(resolve=>release=resolve);const refresh=assistantTasksList(p,true);await flush();await tick();p.dialog.close();
assert.equal(timers.size,0);release({tasks:[task],has_more:false});await refresh;
assert.equal(requests.filter(r=>r.action==='tasks/execution').length,0);assert.equal(timers.size,0);
''')


if __name__ == '__main__':
    unittest.main()
