"""Explicit local-host controls, uncertain replies and unsubmitted form inputs."""
import unittest

import test_workbench_assistant_tasks_ui as ui


class HostExecutionUITests(unittest.TestCase):
    def js(self, case):
        ui.AssistantTaskUITests.js(self, "globalThis.crypto=require('crypto');\n" + case)

    def test_explicit_start_and_cancel_keep_owned_attempt_and_buffers(self):
        self.js(r'''
task.status='generated';task.result=null;task.output=null;
await assistantTasksOpen('one');const p=assistantTaskPanel,before=JSON.stringify([...docs]);let requestId;
handler=async(action,data)=>{assert.equal(action,'tasks/start');assert.equal(data.execute,true);assert.equal(data.version,1);assert.equal(data.expected_book_id,'book');assert.match(data.request_id,/^[0-9a-f]{32}$/);requestId=data.request_id;return {task:{...task,host_execution:{id:'attempt',request_id:requestId,state:'starting',status_label:'正在启动',can_cancel:true,can_start:false}}};};
await find(p.detail,'在本机助手中执行').onclick();assert.equal(p.task.host_execution.id,'attempt');assert.equal(find(p.detail,'在本机助手中执行').disabled,true);
handler=async(action,data)=>{assert.equal(action,'tasks/cancel');assert.equal(data.execution_id,'attempt');return {task:{...task,host_execution:{id:'attempt',request_id:requestId,state:'cancel_requested',can_cancel:true,can_start:false}}};};
await find(p.detail,'请求取消本次执行').onclick();assert.equal(p.task.host_execution.state,'cancel_requested');assert.equal(JSON.stringify([...docs]),before);p.dialog.close();
''')

    def test_unknown_start_does_not_retry_and_readback_reuses_original_key(self):
        self.js(r'''
task.status='generated';task.result=null;task.output=null;await assistantTasksOpen('one');const p=assistantTaskPanel;let key;
handler=async(action,data)=>{key=data.request_id;throw Error('连接中断');};await find(p.detail,'在本机助手中执行').onclick();const count=requests.length;await flush();assert.equal(requests.length,count);assert.equal(find(p.detail,'在本机助手中执行').disabled,true);
handler=async(action,data)=>{assert.equal(action,'tasks/get');return {task:{...task}};};await find(p.detail,'重新读取本任务').onclick();
handler=async(action,data)=>{assert.equal(action,'tasks/start');assert.equal(data.request_id,key);return {task:{...task,host_execution:{id:'attempt',request_id:key,state:'unknown',can_reconcile:true,can_start:false}}};};await find(p.detail,'在本机助手中执行').onclick();assert.equal(find(p.detail,'在本机助手中执行').disabled,true);assert.ok(find(p.detail,'核对已有执行回执'));p.dialog.close();
''')

    def test_unsaved_task_form_blocks_start_and_survives_failed_import_readback(self):
        self.js(r'''
task.status='generated';task.result=null;task.output=null;await assistantTasksOpen('one');const p=assistantTaskPanel,input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');input.value='{"schema":1}';
assert.equal(assistantTaskFormBuffers()[0].dirty,true);const count=requests.length;await find(p.detail,'在本机助手中执行').onclick();assert.equal(requests.length,count);
handler=async()=>{throw Error('连接中断');};await find(p.detail,'核对并导入').onclick();assert.equal(assistantTaskFormBuffers()[0].needs_recovery,true);
handler=async()=>({task:{...task}});await find(p.detail,'重新读取本任务').onclick();const retained=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');assert.equal(retained.value,'{"schema":1}');assert.equal(assistantTaskFormBuffers()[0].dirty,true);p.dialog.close();
''')

    def test_changed_version_retains_original_form_as_copyable_input(self):
        self.js(r'''
task.status='generated';task.result=null;await assistantTasksOpen('one');const p=assistantTaskPanel,input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');input.value='尚未提交的原回执';
handler=async()=>({task:{...task,version:2}});await find(p.detail,'重新读取本任务').onclick();assert.ok(p.detail.querySelectorAll('pre').some(e=>e.textContent.includes('尚未提交的原回执')));assert.equal(assistantTaskFormBuffers()[0].dirty,true);await find(p.detail,'清除此处保留的输入').onclick();assert.deepEqual(assistantTaskFormBuffers(),[]);p.dialog.close();
''')

    def test_readonly_execution_poll_cannot_replace_closed_or_other_task_panel(self):
        self.js(r'''
task.status='generated';task.result=null;task.host_execution={id:'attempt',state:'running',can_cancel:true};await assistantTasksOpen('one');const old=assistantTaskPanel;let release;
handler=()=>new Promise(resolve=>release=resolve);const waiting=find(old.detail,'刷新执行状态').onclick();await flush();old.dialog.close();
handler=async(action)=>action==='tasks/list'?{tasks:[],has_more:false}:{task};await assistantTasksOpen();const current=assistantTaskPanel;release({task:{...task,version:9}});await waiting;assert.strictEqual(assistantTaskPanel,current);assert.equal(current.task,null);assert.equal(docs.get('draft').value,'未保存正文');current.dialog.close();
''')


if __name__ == '__main__':
    unittest.main()
