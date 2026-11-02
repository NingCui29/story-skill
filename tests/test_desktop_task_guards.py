"""Exercise native probes against actual task drafts without starting a GUI."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import test_workbench_assistant_tasks_ui as ui


ROOT = Path(__file__).resolve().parents[1]


class DesktopTaskGuardTests(unittest.TestCase):
    def setUp(self):
        mac = (ROOT / 'desktop/macos/StoryWorkbench.swift').read_text()
        windows = (ROOT / 'desktop/windows/StoryWorkbench/BrowserSafety.cs').read_text()
        self.mac_probe = mac.split('func confirmLeaving(', 1)[1].split('let script = """', 1)[1].split('"""', 1)[0]
        self.windows_probe = windows.split('public static string ProbeScript(bool includeText = true) => """', 1)[1].split('"""', 1)[0].replace('__TEXT__', 'true')

    def task_js(self, case):
        probes = 'const macProbe=' + json.dumps(self.mac_probe) + ',windowsProbe=' + json.dumps(self.windows_probe) + ';\n'
        ui.AssistantTaskUITests.js(self, probes + r'''
globalThis.crypto=require('crypto');
const window=globalThis;window.top=window;
document.getElementById=id=>['current-book-root','assistant-tasks-open'].includes(id)?{}:null;
docs.get('draft').value=docs.get('draft').text;
const native=()=>({mac:eval(macProbe),windows:eval(windowsProbe)});
const assertClean=()=>{const state=native();assert.equal(state.mac,'clean');assert.equal(state.windows.busy,false);assert.equal(state.windows.pending_task,false);};
const assertPending=()=>{const state=native();assert.equal(state.mac,'task-pending');assert.equal(state.windows.busy,false);assert.equal(state.windows.pending_task,true);assert.equal(state.windows.documents.some(d=>d.dirty),false);};
''' + case)

    def test_closed_task_inputs_block_native_exit_until_explicit_discard(self):
        self.task_js(r'''
assertClean();await assistantTasksOpen('one');let p=assistantTaskPanel;
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='只在任务中的未提交输入';
assertPending();p.dialog.close();assert.equal(assistantTaskPanel,null);assertPending();
await assistantTasksOpen('one');p=assistantTaskPanel;await find(p.detail,'放弃本任务在本页的未提交输入').onclick();assertClean();
''')

    def test_task_save_in_flight_and_unknown_receipt_cannot_be_discarded_by_native_exit(self):
        self.task_js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;
p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON').value='{"schema":1}';let reject;
handler=()=>new Promise((resolve,fail)=>reject=fail);const saving=find(p.detail,'核对并导入').onclick();await flush();p.dialog.close();
assert.equal(native().mac,'busy');assert.equal(native().windows.busy,true);
reject(Error('回执未知'));await saving;assertPending();
assert.equal(assistantTaskFormBuffers()[0].needs_recovery,true);
handler=async(action)=>action==='tasks/list'?{tasks:[task],has_more:false}:{task};await assistantTasksOpen('one');
assertPending();await find(assistantTaskPanel.detail,'放弃本任务在本页的未提交输入').onclick();assertClean();
''')

    def test_native_probes_fail_closed_for_invalid_task_states_and_allow_legacy_pages(self):
        self.task_js(r'''
const valid={id:'task-form:one',dirty:false,saving:false,recovering:false,needs_recovery:false};
for(const field of ['dirty','saving','recovering','needs_recovery']){
 const row={...valid,[field]:true};assistantTaskFormBuffers=()=>[row];
 const state=native();assert.equal(state.mac,['saving','recovering'].includes(field)?'busy':'task-pending');
 assert.equal(state.windows.busy,['saving','recovering'].includes(field));
 assert.equal(state.windows.pending_task,['dirty','needs_recovery'].includes(field));
}
for(const invalid of [null,{},[null],[{...valid,id:''}],[valid,valid],Array(1001).fill(valid),
 ...['dirty','saving','recovering','needs_recovery'].flatMap(field=>[{...valid,[field]:1},{...valid,[field]:undefined}]).map(row=>[row])]){
 assistantTaskFormBuffers=()=>invalid;assert.equal(native().mac,'unknown');assert.equal(native().windows,null);
}
assistantTaskFormBuffers=()=>{throw Error('cannot inspect')};assert.equal(native().mac,'unknown');assert.equal(native().windows,null);
assistantTaskFormBuffers=undefined;assert.equal(native().mac,'unknown');assert.equal(native().windows,null);
document.getElementById=id=>id==='current-book-root'?{}:null;assertClean();
assistantTaskFormBuffers={};assert.equal(native().mac,'unknown');assert.equal(native().windows,null);
''')

    def test_windows_shelf_keeps_filtered_status_drafts_in_native_guard(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        program = 'const probe=' + json.dumps(self.windows_probe) + ';\n' + r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const window={};window.top=window;let current=true;const fields=[];
const context={window,Map,document:{getElementById:id=>id==='library-heading'||(current&&id==='shelf-draft-feedback')?{}:null,querySelectorAll:()=>fields},shelfStatusDrafts:new Map()};
const read=()=>vm.runInNewContext(probe,context);
assert.equal(read().pending_form,false);
context.shelfStatusDrafts.set('hidden',{value:'completed'});assert.equal(fields.length,0);assert.equal(read().pending_form,true);
context.shelfStatusDrafts.clear();assert.equal(read().pending_form,false);
fields.push({tagName:'SELECT',value:'completed',dataset:{bookStatusOriginal:'serializing'}});assert.equal(read().pending_form,true);fields.length=0;
context.shelfStatusDrafts={size:0};assert.equal(read(),null);
delete context.shelfStatusDrafts;assert.equal(read(),null);
current=false;assert.equal(read().pending_form,false,'legacy shelf still checks its visible form');
'''
        result = subprocess.run([node, '-'], input=program, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
