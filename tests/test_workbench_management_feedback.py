"""Pending management requests stay visible and never imply an unknown rollback."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchManagementFeedbackTests(unittest.TestCase):
    def setUp(self):
        script = base.story.workbench._library_management_script()
        self.source = (script[script.index('let undoRemoval='):script.index('function shelfMenus')]
                       + script[script.index('function managementText'):script.index('function rememberShelfBook')]
                       + script[script.index('function showCreateBook'):script.index('function decorateBookManagement')])

    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        harness = r'''
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.attributes={};this.dataset={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.open=false;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(type,callback){this.listeners[type]=callback;}
 querySelectorAll(selectors){const tags=selectors.split(',');return this.children.flatMap(node=>[...(tags.includes(node.tag)?[node]:[]),...node.querySelectorAll(selectors)]);}
 showModal(){this.open=true;}close(){this.open=false;this.listeners.close?.();}
}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
$('book-manager').append($('manager-body'),$('manager-actions'));
const document={createElement:tag=>new E(tag)};
let clock=0,timerId=0,closedMenus=0;Date.now=()=>clock;
const timers=new Map(),setInterval=callback=>{const id=++timerId;timers.set(id,callback);return id;},clearInterval=id=>timers.delete(id);
const tick=milliseconds=>{clock+=milliseconds;for(const callback of timers.values())callback();};
function closeShelfMenus(){closedMenus++;}
const requests=[],renders=[],render=value=>renders.push(value),bookName=book=>book.title;
let response={},failure=null,pending=null;
let call=async(action,data={})=>{requests.push({action,...data});if(pending)return await pending;if(failure)throw failure;return response;};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const fields=()=>$('manager-body').children.filter(node=>node.tag==='label').map(node=>node.children[0]);
const action=label=>$('manager-actions').children.find(node=>node.textContent===label);
const book={key:'fixture',title:'合成作品',root:'/fixture/book'};
async function flush(){for(let index=0;index<8;index++)await Promise.resolve();}
'''
        result = subprocess.run([node, '-'], input=harness + self.source + '\n(async()=>{\n' + test
                                + '\n})().catch(error=>{console.error(error);process.exitCode=1;});',
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_progress_markup_is_indeterminate_and_announces_operation(self):
        page = base.story.workbench._library_page('fixture').decode()
        self.assertIn('id="manager-operation" role="status" aria-live="polite"', page)
        self.assertIn('id="manager-elapsed" aria-live="off"', page)
        self.assertIn('<progress id="manager-progress" aria-label="操作进行中"></progress>', page)
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.js('new Function(' + json.dumps(script) + ');')

    def test_pending_backup_locks_form_blocks_reentry_and_esc_and_keeps_elapsed_time(self):
        self.js(r'''
showBookBackup(book);fields()[0].value='/fixture/archive.zip';
const locked=managementField('原本禁用');locked.disabled=true;
const run=action('创建并核验备份'),leave=action('返回书架'),wait=deferred();pending=wait.promise;
const running=run.onclick();await flush();
assert.equal(managerBusy,true);assert.equal(manager.attributes['aria-busy'],'true');assert.equal(requests.length,1);
assert.equal($('manager-feedback').dataset.state,'running');assert.equal($('manager-progress').hidden,false);
assert.ok($('manager-operation').textContent.includes('正在创建并核验备份'));
assert.ok($('manager-detail').textContent.includes('等待本机服务'));assert.equal(timers.size,1);
for(const node of manager.querySelectorAll('button,input,select,textarea'))assert.equal(node.disabled,true);
tick(65000);assert.equal($('manager-elapsed').textContent,'已用 1 分 5 秒');
let cancelled=false;manager.listeners.cancel({preventDefault(){cancelled=true;}});assert.equal(cancelled,true);
await run.onclick();await leave.onclick();assert.equal(requests.length,1);assert.equal(manager.open,true);
const before=managerBody.children.slice();assert.throws(()=>openManager('不得替换'),/仍在进行/);assert.deepEqual(managerBody.children,before);
wait.resolve({path:'/fixture/archive.zip',files:7,sha256:'verified'});await running;
assert.equal(managerBusy,false);assert.equal(manager.attributes['aria-busy'],'false');assert.equal(timers.size,0);assert.equal(managerTimer,null);
assert.equal($('manager-feedback').dataset.state,'success');assert.equal($('manager-progress').hidden,true);
assert.equal(fields()[0].value,'/fixture/archive.zip');assert.equal(fields()[0].disabled,false);assert.equal(locked.disabled,true);
assert.equal(run.textContent,'创建并核验备份');assert.equal(run.disabled,false);assert.equal(leave.disabled,false);
assert.ok(managerBody.children.some(node=>node.textContent.includes('文件数：7')));
cancelled=false;manager.listeners.cancel({preventDefault(){cancelled=true;}});assert.equal(cancelled,false);
''')

    def test_server_failure_retains_fields_clears_timer_and_can_retry_successfully(self):
        self.js(r'''
showBookBackup(book);fields()[0].value='/fixture/archive.zip';
failure=Object.assign(Error('备份检测到文件变化，请重新核对'),{responseReceived:true});
const run=action('创建并核验备份');await run.onclick();
assert.equal($('manager-feedback').dataset.state,'error');assert.equal($('manager-operation').textContent,'操作未完成');
assert.ok($('manager-error').textContent.includes('文件变化'));assert.ok($('manager-detail').textContent.includes('错误响应'));
assert.equal(fields()[0].value,'/fixture/archive.zip');assert.equal(run.disabled,false);assert.equal(timers.size,0);assert.equal(manager.attributes['aria-busy'],'false');
failure=null;response={path:'/fixture/archive.zip',files:2,sha256:'verified'};await run.onclick();
assert.equal(requests.length,2);assert.equal($('manager-error').textContent,'');assert.equal($('manager-feedback').dataset.state,'success');assert.equal(timers.size,0);
''')

    def test_network_failure_is_unknown_and_is_never_automatically_retried(self):
        self.js(r'''
showRestoreBackup();const [archive,destination]=fields();archive.value='/fixture/archive.zip';destination.value='/fixture/new-book';
const wait=deferred();pending=wait.promise;const run=action('核验并恢复副本'),running=run.onclick();await flush();
assert.ok($('manager-operation').textContent.includes('正在核验备份并恢复副本'));
wait.reject(Error('连接中断'));await running;
assert.equal($('manager-feedback').dataset.state,'unknown');assert.equal($('manager-operation').textContent,'执行结果待核对');
assert.ok($('manager-detail').textContent.includes('操作可能已执行'));assert.ok($('manager-detail').textContent.includes('勿直接重复执行'));
assert.ok(!$('manager-operation').textContent.includes('未执行'));assert.equal(requests.length,1);assert.equal(timers.size,0);
assert.equal(archive.value,'/fixture/archive.zip');assert.equal(destination.value,'/fixture/new-book');assert.equal(run.disabled,false);assert.equal(manager.open,true);
''')

    def test_local_validation_failure_reports_not_submitted_and_preserves_confirmation(self):
        self.js(r'''
response={running:true,outdated:false};await showBookService(book);const confirmation=fields()[0],run=action('停止本书编辑服务');
await run.onclick();assert.equal(requests.length,1);assert.equal(requests[0].action,'book-service');
assert.equal($('manager-operation').textContent,'操作未提交');assert.equal($('manager-feedback').dataset.state,'error');
assert.ok($('manager-error').textContent.includes('先保存'));assert.equal(confirmation.checked,false);assert.equal(confirmation.disabled,false);assert.equal(timers.size,0);
confirmation.checked=true;response={stop_requested:false};await run.onclick();
assert.equal(requests.length,2);assert.equal(requests[1].action,'stop-book');assert.ok($('manager-operation').textContent.includes('请核对下方结果'));
assert.ok(managerBody.children.some(node=>node.textContent.includes('停止未确认')));assert.equal(confirmation.checked,true);
''')

    def test_restore_success_publishes_actual_receipt_and_releases_busy_after_dialog_closes(self):
        self.js(r'''
showRestoreBackup();const [archive,destination]=fields();archive.value='/fixture/archive.zip';destination.value='/fixture/restored';
response={root:'/fixture/restored',books:[book],note:'请核对副本'};await action('核验并恢复副本').onclick();
assert.equal(manager.open,false);assert.deepEqual(renders,[response]);assert.ok($('message').textContent.includes('/fixture/restored'));
assert.equal(managerBusy,false);assert.equal(manager.attributes['aria-busy'],'false');assert.equal(timers.size,0);
openManager('重新打开');assert.equal($('manager-feedback').hidden,true);assert.equal($('manager-error').textContent,'');assert.equal($('manager-elapsed').textContent,'');
await action('返回书架').onclick();assert.equal(manager.open,false);assert.equal(timers.size,0);
''')

    def test_create_and_relink_keep_operation_identity_while_awaiting_service(self):
        self.js(r'''
for(const [show,label,operation,expectedAction]of [[showCreateBook,'创建并加入书架','正在创建作品并登记书架','create-book'],[()=>showRelinkBook(book),'核对并更新位置','正在核对作品身份并更新位置','relink-book']]){
 show();fields()[0].value='/fixture/new';const wait=deferred();pending=wait.promise;const run=action(label),running=run.onclick();await flush();
 assert.equal($('manager-operation').textContent,operation);assert.equal(requests.at(-1).action,expectedAction);
 wait.resolve({books:[book],registered:true});await running;assert.equal(managerBusy,false);assert.equal(timers.size,0);assert.equal(manager.attributes['aria-busy'],'false');
}
''')

    def test_retry_resets_elapsed_and_announcement_without_leaking_previous_timer(self):
        self.js(r'''
showBookBackup(book);fields()[0].value='/fixture/archive.zip';
let wait=deferred();pending=wait.promise;const run=action('创建并核验备份');let running=run.onclick();await flush();tick(17000);
wait.reject(Object.assign(Error('备份目标已存在'),{responseReceived:true}));await running;assert.equal(timers.size,0);assert.equal($('manager-elapsed').textContent,'已用 17 秒');
fields()[0].value='/fixture/another.zip';wait=deferred();pending=wait.promise;running=run.onclick();await flush();
assert.equal($('manager-error').textContent,'');assert.equal($('manager-feedback').dataset.state,'running');assert.equal($('manager-elapsed').textContent,'已用 0 秒');assert.equal(timers.size,1);
tick(2000);assert.equal($('manager-elapsed').textContent,'已用 2 秒');wait.resolve({path:'/fixture/another.zip',files:1,sha256:'verified'});await running;
assert.equal(timers.size,0);assert.equal(managerTimer,null);assert.equal(requests.length,2);assert.equal(requests[1].path,'/fixture/another.zip');
''')


if __name__ == '__main__':
    unittest.main()
