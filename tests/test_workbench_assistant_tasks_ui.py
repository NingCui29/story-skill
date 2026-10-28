"""Exercise task UI races without modifying manuscript buffers."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base


class AssistantTaskUITests(unittest.TestCase):
    def js(self, case):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        script = base.story.workbench._assistant_tasks_script().replace('__TASK_BOOK_ID__', '"book"')
        harness = r'''
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.open=false;this.value='';this.disabled=false;this.textContent='';}
 set innerHTML(value){throw Error('Task text must not become HTML');}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(name,value){this[name]=value;}
 addEventListener(name,fn){this.listeners[name]=fn;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
 close(){this.open=false;this.listeners.close?.();}
 select(){}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E()),document={createElement:tag=>new E(tag)};
const navigator={clipboard:{writeText:async text=>{copies.push(text);}}},copies=[],opened=[],requests=[];
const currentCatalog={files:[{id:'file:要求.md',path:'要求.md'},{id:'file:文风.md',path:'文风.md'}]};
const docs=new Map([['draft',{value:'未保存正文',text:'旧文字',editable:true}]]);
const dialogs=[];const editorDialog=()=>{const dialog=new E('dialog'),content=new E();dialog.open=true;dialogs.push({dialog,content});return {dialog,content};};
let openResult=true;const openDoc=async(id,q,options)=>{assert.equal(options.expectedSha,'hash');if(options.guard())opened.push(id);return openResult;};
const task={id:'one',book_id:'book',version:1,kind:'review',status:'needs_review',status_label:'结果待核对',prompt:'仅审稿',stale_reasons:[],result:{summary:'读者回报已落到人物行动'},issues:[],output:{path:'.story/drafts/result.md',sha256:'hash'},result_template:{schema:1}};
let handler=async(action,data)=>action==='tasks/list'?{tasks:[{...task,prompt:undefined}],has_more:false}:{task:JSON.parse(JSON.stringify(task))};
const call=async(action,data)=>{requests.push({action,data});return handler(action,data);};
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const find=(root,text)=>root.querySelectorAll('button').find(b=>b.textContent===text);
'''
        result = subprocess.run([node, '-'], input=harness + script + '\n(async()=>{' + case +
                                '\n})().catch(error=>{console.error(error);process.exitCode=1;});',
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_import_and_review_use_current_task_version_and_do_not_change_buffers(self):
        self.js(r'''
const before=JSON.stringify([...docs]);await assistantTasksOpen('one');const p=assistantTaskPanel;
const selects=p.content.querySelectorAll('select');selects[0].value='要求.md';selects[0].onchange();assert.deepEqual(assistantWritingSources(),{request_file:'要求.md'});
const input=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='助手结果 JSON');input.value='{"schema":1}';
handler=async(action,data)=>{assert.equal(action,'tasks/import');assert.equal(data.task_id,'one');assert.equal(data.version,1);assert.equal(data.expected_book_id,'book');return {task:{...task,version:2}};};
await find(p.detail,'核对并导入').onclick();assert.equal(p.task.version,2);assert.equal(JSON.stringify([...docs]),before);
handler=async(action,data)=>{assert.equal(action,'tasks/review');assert.equal(data.version,2);assert.deepEqual(data.issue_statuses,[]);return {task:{...task,version:3,status:'reviewed'}};};
await find(p.detail,'保存逐项核对结果').onclick();assert.equal(p.task.version,3);assert.equal(find(p.detail,'保存逐项核对结果').disabled,true);assert.equal(JSON.stringify([...docs]),before);
''')

    def test_closing_panel_discards_late_result_and_keeps_other_panel(self):
        self.js(r'''
await assistantTasksOpen('one');const old=assistantTaskPanel;let release;
handler=(action,data)=>new Promise(resolve=>{release=resolve;});
const running=find(old.detail,'保存逐项核对结果').onclick();await flush();old.dialog.close();
handler=async(action,data)=>action==='tasks/list'?{tasks:[],has_more:false}:{task};await assistantTasksOpen();const latest=assistantTaskPanel;
release({task:{...task,version:9,status:'reviewed'}});await running;assert.strictEqual(assistantTaskPanel,latest);assert.equal(latest.task,null);assert.equal(docs.get('draft').value,'未保存正文');
''')

    def test_wrong_book_response_cannot_show_or_adopt_result(self):
        self.js(r'''
handler=async(action,data)=>action==='tasks/list'?{tasks:[],has_more:false}:{task:{...task,book_id:'other'}};
await assistantTasksOpen('one');assert.equal(assistantTaskPanel.task,null);assert.ok(assistantTaskPanel.status.textContent.includes('不属于当前作品'));assert.equal(opened.length,0);
''')

    def test_issue_resolution_requires_reason_and_passes_structured_statuses(self):
        self.js(r'''
task.issues=[{id:'risk',severity:'blocker',issue:'前文已交还物件，本章仍持有',evidence:[]}];task.issue_statuses=[{id:'risk',status:'open',note:''}];
await assistantTasksOpen('one');const p=assistantTaskPanel,select=p.detail.querySelectorAll('select')[0],reason=p.detail.querySelectorAll('textarea').find(e=>e['aria-label']==='问题 risk 的核对说明');
select.value='resolved';let calls=requests.length;await find(p.detail,'保存逐项核对结果').onclick();assert.equal(requests.length,calls);assert.ok(p.status.textContent.includes('填写核对依据'));
reason.value='已按交还的前文复核，修订另存候选';handler=async(action,data)=>{assert.deepEqual(data.issue_statuses,[{id:'risk',status:'resolved',note:reason.value}]);return {task:{...task,version:2,status:'reviewed'}};};await find(p.detail,'保存逐项核对结果').onclick();
''')

    def test_stale_result_disables_mutations_but_preserves_evidence_access(self):
        self.js(r'''
task.stale_reasons=[{code:'source_changed',message:'来源稿件变化'}];await assistantTasksOpen('one');const p=assistantTaskPanel;
for(const label of ['确认已交给助手','保存逐项核对结果','核对并导入'])assert.equal(find(p.detail,label).disabled,true);
assert.equal(find(p.detail,'重新读取本任务').disabled,false);assert.equal(docs.get('draft').value,'未保存正文');
''')

    def test_candidate_open_is_version_bound_and_preserves_panel_on_refusal(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;openResult=false;
await find(p.detail,'打开返回的候选稿').onclick();assert.equal(p.dialog.open,true);assert.ok(p.status.textContent.includes('版本已变化'));assert.equal(docs.get('draft').value,'未保存正文');
openResult=true;await find(p.detail,'打开返回的候选稿').onclick();assert.equal(p.dialog.open,false);
''')

    def test_evidence_shows_source_location_and_stale_snapshot(self):
        self.js(r'''
task.issues=[{id:'risk',severity:'advice',issue:'交接',evidence:[{chapter:1,quote:'已交还'}]}];await assistantTasksOpen('one');const p=assistantTaskPanel;
handler=async()=>({path:'第1章.md',line:9,stale:true,text:'原文已交还',evidence:{quote:'已交还'}});
await find(p.detail,'查看依据 1 · 第1章').onclick();const view=dialogs.at(-1).content;assert.ok(view.children.some(e=>e.textContent.includes('旧版快照')));assert.ok(view.children.some(e=>e.textContent.includes('第9行')));assert.ok(view.children.some(e=>e.textContent==='已交还'));
''')

    def test_unknown_mutation_response_requires_readback_before_retry(self):
        self.js(r'''
await assistantTasksOpen('one');const p=assistantTaskPanel;handler=async()=>{throw Error('连接中断，结果未知');};await find(p.detail,'保存逐项核对结果').onclick();
assert.equal(find(p.detail,'保存逐项核对结果').disabled,true);assert.equal(find(p.detail,'重新读取本任务').disabled,false);assert.ok(p.status.textContent.includes('尚未确认保存'));assert.equal(docs.get('draft').value,'未保存正文');
''')


if __name__ == '__main__':
    unittest.main()
