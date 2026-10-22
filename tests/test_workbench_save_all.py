"""All-buffer candidate saving must preserve hidden edits and partial failures."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchSaveAllTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        workbench = base.story.workbench
        page = workbench._editor_page(workbench.snapshot(self.fixture.book), {}, 'test').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = (script[script.index('function documentIdentity('):script.index('function documentLocation(')]
                       + script[script.index('function dirty(d)'):script.index('function badge()')]
                       + script[script.index('async function saveDocument'):script.index("$('compare').onclick")])

    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
const controls={},$=id=>controls[id]||(controls[id]={});
const docs=new Map(),notes=[],calls=[],recoveries=[],shown=[],scheduled=[];
let active='active-clean',reloading=false,saveError=null,openError=null,saveCount=0,heldSave=null;
const location={hash:''},crypto={randomUUID:()=>String(saveCount)+'-fixture-key'};
const clearTimeout=()=>{},setTimeout=(callback,delay)=>{scheduled.push({callback,delay});return scheduled.length;};
const badge=()=>{},note=value=>notes.push(value),rememberView=()=>{},displayTitle=document=>document.title;
const catalog=async()=>{},recover=async document=>{recoveries.push(document.id);document.recovered=document.value;};
const show=document=>{shown.push(document.id);active=document.id;};
const saved=new Map();
const call=async(action,data)=>{
 calls.push({action,data:{...data}});
 if(action==='save'){
  if(saveError===data.id)throw Error('合成保存失败');
  const id='candidate:'+ ++saveCount;saved.set(id,data.text);
  if(heldSave)await heldSave;
  return {id};
 }
 if(action==='open'){
  if(openError)throw Error('保存后读取未确认');
  return {id:data.id,text:saved.get(data.id),editable:true,sha256:'fresh-sha',positions:{}};
 }
 throw Error('Unexpected save boundary: '+action);
};
const make=(id,text,value,extra={})=>({id,text,value,editable:true,editing:true,title:id,path:id+'.md',
 sha256:'source-sha',snapshot:'snapshot',metadata_sha256:'metadata-sha',positions:{main:17},...extra});
docs.set(active,make(active,'当前已保存','当前已保存'));
const pending=()=>[...docs.values()].filter(document=>dirty(document)||document.needs_recovery);
"""
        program = mock + self.source + '\n(async()=>{' + test + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node, '-e', program], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_save_all_includes_hidden_dirty_and_recovery_buffers_without_switching_active_document(self):
        self.js(r"""
const hidden=make('hidden','旧文字','隐藏修改'),recovery=make('recovery','恢复文字','恢复文字',{needs_recovery:true});
docs.set(hidden.id,hidden);docs.set(recovery.id,recovery);
await $('save-all').onclick();
assert.deepEqual(calls.filter(call=>call.action==='save').map(call=>call.data.id),['hidden','recovery']);
assert.deepEqual([...saved.values()],['隐藏修改','恢复文字']);
assert.equal(active,'active-clean');assert.deepEqual(shown,[]);assert.equal(location.hash,'');
assert.equal(pending().length,0);assert.ok(notes.at(-1).includes('已保存 2 份候选'));assert.ok(notes.at(-1).includes('尚未正式采用'));
await $('save-all').onclick();assert.equal(saveCount,2,'saving again must not duplicate the restored candidate');
""")

    def test_save_all_stops_at_failed_buffer_and_keeps_remaining_hidden_text(self):
        self.js(r"""
const first=make('first','原一','新一'),second=make('second','原二','新二'),third=make('third','原三','新三');
for(const item of [first,second,third])docs.set(item.id,item);saveError='second';
await $('save-all').onclick();
assert.deepEqual(calls.filter(call=>call.action==='save').map(call=>call.data.id),['first','second']);
assert.equal(first.value,first.text);assert.equal(second.value,'新二');assert.equal(third.value,'新三');
assert.equal(second.saving,false);assert.equal(active,'active-clean');assert.equal(saved.size,1);
assert.deepEqual(pending().map(document=>document.id),['second','third']);
assert.ok(notes.at(-1).includes('1 份已保存'));assert.ok(notes.at(-1).includes('其余文字保留在页面中'));
""")

    def test_inflight_save_prevents_duplicate_all_buffer_submissions(self):
        self.js(r"""
const hidden=make('hidden','原稿','未保存',{saving:true});docs.set(hidden.id,hidden);
await $('save-all').onclick();assert.equal(calls.length,0);assert.ok(notes.at(-1).includes('候选正在保存'));
assert.equal(await saveDocument(hidden),false);assert.equal(hidden.value,'未保存');assert.equal(hidden.saving,true);
""")

    def test_save_all_final_message_preserves_failed_document_and_concrete_reason(self):
        self.js(r"""
const first=make('first','原一','新一'),second=make('second','原二','新二');
docs.set(first.id,first);docs.set(second.id,second);saveError='second';
await $('save-all').onclick();
assert.ok(notes.at(-1).includes('保存全部已停止；1 份已保存'));
assert.ok(notes.at(-1).includes('失败原因（second）：合成保存失败'));
assert.equal(second.value,'新二');assert.equal(dirty(second),true);assert.deepEqual(pending().map(d=>d.id),['second']);
saveError=null;await $('save-all').onclick();assert.ok(!notes.at(-1).includes('合成保存失败'));assert.equal(pending().length,0);
""")

    def test_typing_during_save_keeps_newer_text_dirty_in_the_new_candidate(self):
        self.js(r"""
let release;heldSave=new Promise(resolve=>{release=resolve;});
const hidden=make('hidden','原稿','点击保存时的文字');docs.set(hidden.id,hidden);
const saving=saveDocument(hidden);assert.equal(hidden.saving,true);await Promise.resolve();
hidden.value='保存期间又输入的文字';release();assert.equal(await saving,true);
const candidate=docs.get('candidate:1');assert.equal(candidate.text,'点击保存时的文字');
assert.equal(candidate.value,'保存期间又输入的文字');assert.equal(dirty(candidate),true);
assert.equal(hidden.value,hidden.text);assert.equal(hidden.saving,false);assert.equal(active,'active-clean');
assert.ok(scheduled.some(item=>item.delay===1200));
""")

    def test_save_all_reports_edits_added_while_its_snapshot_was_saving(self):
        self.js(r"""
let release;heldSave=new Promise(resolve=>{release=resolve;});
const hidden=make('hidden','原稿','点击保存时的文字');docs.set(hidden.id,hidden);
const saving=$('save-all').onclick();await Promise.resolve();hidden.value='刚输入的更新文字';release();await saving;
assert.equal(pending().length,1);assert.equal(pending()[0].value,'刚输入的更新文字');
assert.ok(notes.at(-1).includes('已保存 1 份候选'));assert.ok(notes.at(-1).includes('另有 1 份新增或未完成编辑'));
""")

    def test_readback_failure_retains_original_buffer_without_claiming_save_complete(self):
        self.js(r"""
const hidden=make('hidden','原稿','当前全部文字');docs.set(hidden.id,hidden);openError=true;
assert.equal(await saveDocument(hidden),false);assert.equal(hidden.saving,false);
assert.equal(hidden.value,'当前全部文字');assert.equal(dirty(hidden),true);assert.equal(saved.size,1);
assert.equal(docs.has('candidate:1'),false);assert.ok(notes.at(-1).includes('当前文字仍在编辑器中'));
""")


if __name__ == '__main__':
    unittest.main()
