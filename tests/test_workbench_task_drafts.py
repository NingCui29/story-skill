"""Task form drafts survive local navigation without replaying server writes."""
import inspect
import unittest

import test_workbench_assistant_tasks_ui as ui
import test_workbench as base


class TaskDraftTests(unittest.TestCase):
    def js(self, case):
        ui.AssistantTaskUITests.js(self, "globalThis.crypto=require('crypto');\n" + case)

    def test_switching_tasks_keeps_each_receipt_and_review_note(self):
        self.js(r'''
task.issues=[{id:'risk',severity:'advice',issue:'核对交接',evidence:[]}];
handler=async(action,data)=>action==='tasks/list'?{tasks:[task,{...task,id:'two'}],has_more:false}:{task:{...task,id:data.task_id}};
await assistantTasksOpen('one');const p=assistantTaskPanel;
const field=label=>p.detail.querySelectorAll('textarea').find(e=>e['aria-label']===label);
field('助手结果 JSON').value='任务一未提交回执';field('问题 risk 的核对说明').value='任务一原文依据';p.detail.querySelectorAll('select')[0].value='resolved';
await assistantTaskShow(p,'two');field('助手结果 JSON').value='任务二未提交回执';
await assistantTaskShow(p,'one');assert.equal(field('助手结果 JSON').value,'任务一未提交回执');assert.equal(field('问题 risk 的核对说明').value,'任务一原文依据');assert.equal(p.detail.querySelectorAll('select')[0].value,'resolved');
await assistantTaskShow(p,'two');assert.equal(field('助手结果 JSON').value,'任务二未提交回执');assert.equal(assistantTaskFormBuffers().length,2);
assert.ok(requests.every(row=>['tasks/list','tasks/get'].includes(row.action)));
''')

    def test_closing_and_reopening_preserves_input_and_exit_protection(self):
        self.js(r'''
await assistantTasksOpen('one');let p=assistantTaskPanel;
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='关闭前未提交输入';p.dialog.close();
assert.equal(assistantTaskPanel,null);assert.equal(assistantTaskFormBuffers()[0].dirty,true);
await assistantTasksOpen();p=assistantTaskPanel;assert.ok(p.list.children[0].textContent.includes('本页保留输入'));await assistantTaskShow(p,'one');
assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'关闭前未提交输入');assert.ok(p.draftNotice.textContent.includes('当前页面'));assert.ok(p.draftNotice.textContent.includes('不删除'));
''')

    def test_changed_versions_keep_all_inputs_copyable_without_replaying(self):
        self.js(r'''
await assistantTasksOpen('one');let p=assistantTaskPanel;
const input=()=>p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
input().value='版本一输入';p.dialog.close();task.version=2;await assistantTasksOpen('one');p=assistantTaskPanel;
assert.equal(input().value,'');assert.ok(p.detail.querySelectorAll('pre').some(e=>e.textContent.includes('版本一输入')));input().value='版本二输入';p.dialog.close();
task.version=3;await assistantTasksOpen('one');p=assistantTaskPanel;assert.equal(input().value,'');
for(const value of ['版本一输入','版本二输入'])assert.ok(p.detail.querySelectorAll('pre').some(e=>e.textContent.includes(value)));
assert.equal(assistantTaskFormBuffers()[0].dirty,true);assert.ok(requests.every(row=>['tasks/list','tasks/get'].includes(row.action)));
''')

    def test_explicit_discard_clears_only_local_draft_for_selected_task(self):
        self.js(r'''
handler=async(action,data)=>action==='tasks/list'?{tasks:[task,{...task,id:'two'}],has_more:false}:{task:{...task,id:data.task_id}};
await assistantTasksOpen('one');const p=assistantTaskPanel,input=()=>p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');
input().value='任务一';input().listeners.input();assert.equal(p.draftNotice.hidden,false);
await assistantTaskShow(p,'two');input().value='任务二';input().listeners.input();const before=requests.length;
await find(p.detail,'放弃本任务在本页的未提交输入').onclick();assert.equal(input().value,'');assert.equal(requests.length,before);assert.equal(assistantTaskFormBuffers().length,1);
await assistantTaskShow(p,'one');assert.equal(input().value,'任务一');
''')

    def test_successful_import_does_not_discard_unsubmitted_review_notes(self):
        self.js(r'''
task.issues=[{id:'risk',severity:'advice',issue:'核对交接',evidence:[]}];await assistantTasksOpen('one');const p=assistantTaskPanel;
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='问题 risk 的核对说明').value='尚未提交的核对依据';
handler=async()=>({task:{...task,version:2}});await find(p.detail,'核对并导入').onclick();
const retained=p.detail.querySelectorAll('pre').map(e=>e.textContent).join('\n');assert.ok(retained.includes('尚未提交的核对依据'));assert.ok(!retained.includes('result_json'));assert.equal(assistantTaskFormBuffers()[0].dirty,true);
''')

    def test_typing_during_save_remains_available_after_response(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel,input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');input.value='{"schema":1}';let release;
handler=()=>new Promise(resolve=>release=resolve);const saving=find(p.detail,'核对并导入').onclick();await flush();input.value='保存过程中补充的回执';
release({task:{...task,version:2}});await saving;assert.ok(p.detail.querySelectorAll('pre').some(e=>e.textContent.includes('保存过程中补充的回执')));assert.equal(assistantTaskFormBuffers()[0].dirty,true);assert.equal(assistantTaskFormBuffers()[0].saving,false);
''')

    def test_late_success_clears_submitted_draft_after_close(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel; p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';let release;
handler=()=>new Promise(resolve=>release=resolve);const saving=find(p.detail,'核对并导入').onclick();await flush();p.dialog.close();assert.equal(assistantTaskFormBuffers()[0].saving,true);
release({task:{...task,version:2}});await saving;assert.deepEqual(assistantTaskFormBuffers(),[]);
''')

    def test_reopening_while_save_is_pending_cannot_resubmit(self):
        self.js(r'''
await assistantTasksOpen('one');const old=assistantTaskPanel;old.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';let release;
handler=()=>new Promise(resolve=>release=resolve);const saving=find(old.detail,'核对并导入').onclick();await flush();old.dialog.close();
handler=async(action)=>action==='tasks/list'?{tasks:[task],has_more:false}:{task};await assistantTasksOpen('one');const p=assistantTaskPanel;
assert.equal(assistantTaskFormBuffers()[0].saving,true);assert.equal(find(p.detail,'核对并导入').disabled,true);assert.equal(find(p.detail,'放弃本任务在本页的未提交输入').disabled,true);const count=requests.length;await find(p.detail,'核对并导入').onclick();assert.equal(requests.length,count);
release({task:{...task,version:2}});await saving;assert.deepEqual(assistantTaskFormBuffers(),[]);assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'{"schema":1}');p.dialog.close();assert.deepEqual(assistantTaskFormBuffers(),[]);
''')

    def test_delayed_close_event_cannot_restore_successfully_saved_input(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel; p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';let release;
handler=()=>new Promise(resolve=>release=resolve);const saving=find(p.detail,'核对并导入').onclick();await flush();p.dialog.open=false;
release({task:{...task,version:2}});await saving;assert.deepEqual(assistantTaskFormBuffers(),[]);p.dialog.listeners.close();assert.deepEqual(assistantTaskFormBuffers(),[]);assert.equal(p.formSaving,false);
''')

    def test_reopened_task_new_input_survives_late_save_settlement(self):
        self.js(r'''
await assistantTasksOpen('one');const old=assistantTaskPanel; old.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';let release;
handler=()=>new Promise(resolve=>release=resolve);const saving=find(old.detail,'核对并导入').onclick();await flush();old.dialog.close();
handler=async(action)=>action==='tasks/list'?{tasks:[task],has_more:false}:{task};await assistantTasksOpen('one');const p=assistantTaskPanel,input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');input.value='重新打开后补充的输入';
release({task:{...task,version:2}});await saving;assert.equal(input.value,'重新打开后补充的输入');assert.equal(assistantTaskFormBuffers()[0].dirty,true);assert.equal(assistantTaskFormBuffers()[0].saving,false);p.dialog.close();
task.version=2;await assistantTasksOpen('one');assert.ok(assistantTaskPanel.detail.querySelectorAll('pre').some(e=>e.textContent.includes('重新打开后补充的输入')));
''')

    def test_actual_beforeunload_handler_checks_hidden_task_inputs_and_cleanup(self):
        source = inspect.getsource(base.story.workbench._editor_page)
        handler = next(line for line in source.splitlines()
                       if line.startswith("window.addEventListener('beforeunload',event=>"))
        self.js(r'''
const listeners={},window={addEventListener:(type,fn)=>listeners[type]=fn};
const dirty=d=>d.editable&&d.value!==d.text;
docs.get('draft').value=docs.get('draft').text;
''' + handler + r'''
const prevented=()=>{let blocked=false;const event={preventDefault:()=>blocked=true};listeners.beforeunload(event);assert.equal(event.returnValue,blocked?'':undefined);return blocked;};
assert.equal(prevented(),false);await assistantTasksOpen('one');let p=assistantTaskPanel;
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='只在任务中未保存';p.dialog.close();assert.equal(prevented(),true);
await assistantTasksOpen('one');p=assistantTaskPanel;await find(p.detail,'放弃本任务在本页的未提交输入').onclick();assert.equal(prevented(),false);
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';let reject;handler=()=>new Promise((resolve,fail)=>reject=fail);const saving=find(p.detail,'核对并导入').onclick();await flush();p.dialog.close();assert.equal(prevented(),true);reject(Error('未知回执'));await saving;assert.equal(prevented(),true);
handler=async(action)=>action==='tasks/list'?{tasks:[task],has_more:false}:{task};await assistantTasksOpen('one');await find(assistantTaskPanel.detail,'放弃本任务在本页的未提交输入').onclick();assert.equal(prevented(),false);
''')

    def test_late_failure_keeps_draft_unknown_until_independent_readback(self):
        self.js(r'''
await assistantTasksOpen('one');let p=assistantTaskPanel;p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';let reject;
handler=()=>new Promise((resolve,fail)=>reject=fail);const saving=find(p.detail,'核对并导入').onclick();await flush();p.dialog.close();reject(Error('连接中断'));await saving;
assert.equal(assistantTaskFormBuffers()[0].saving,false);assert.equal(assistantTaskFormBuffers()[0].needs_recovery,true);
handler=async(action)=>action==='tasks/list'?{tasks:[task],has_more:false}:{task};await assistantTasksOpen('one');p=assistantTaskPanel;
assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'{"schema":1}');assert.equal(assistantTaskFormBuffers()[0].needs_recovery,false);assert.equal(assistantTaskFormBuffers()[0].dirty,true);
''')

    def test_execution_readback_preserves_unsubmitted_form(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel; p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='只读刷新前的输入';
handler=async()=>({task:{...task}});await find(p.detail,'刷新执行状态').onclick();assert.equal(p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value,'只读刷新前的输入');assert.equal(assistantTaskFormBuffers()[0].dirty,true);
''')


if __name__ == '__main__':
    unittest.main()
