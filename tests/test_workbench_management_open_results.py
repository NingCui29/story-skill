"""Recovery opens belong to their dialog; batch opens retain partial results."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchManagementOpenResultsTests(unittest.TestCase):
    def js(self, checks):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        script = base.story.workbench._library_management_script()
        source = (script[:script.index('function shelfMenus')]
                  + script[script.index('function managementText'):
                           script.index('function decorateBookManagement')]
                  + script[script.index('let lastBooksBusy='):])
        harness = r'''
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.attributes={};this.listeners={};this.dataset={};this._text='';this.open=false;this.disabled=false;this.hidden=false;}
 set textContent(value){this._text=String(value);this.children=[];}get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this._text='';this.children=nodes;}
 setAttribute(key,value){this.attributes[key]=value;}addEventListener(type,callback){this.listeners[type]=callback;}
 querySelectorAll(selectors){return this.children.flatMap(node=>[...(selectors.split(',').includes(node.tag)?[node]:[]),...node.querySelectorAll(selectors)]);}
 showModal(){this.open=true;}close(){this.open=false;}click(){openedURLs.push(this.href);}
}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
$('book-manager').append($('manager-body'),$('manager-actions'));$('last-books').textContent='重新打开上次作品';
const document={createElement:tag=>new E(tag)},openedURLs=[],requests=[],storage=new Map();
const localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
function closeShelfMenus(){}function render(){}const bookName=book=>book.title;
let shelfBooks=[],reply=async()=>({});async function call(action,data={}){requests.push({action,...data});return await reply(action,data);}
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
async function flush(){for(let index=0;index<8;index++)await Promise.resolve();}
const recovery={id:'file:.story/drafts/自动恢复/恢复稿.md',title:'恢复稿',path:'.story/drafts/自动恢复/恢复稿.md'};
async function recoveryRow(){reply=async()=>({books:[{key:'a',book_id:'identity-a',title:'作品 A',recoveries:[recovery]}]});await showRecoveries();return managerBody.children.find(node=>node.className==='recovery-row');}
const batchResult=()=>$('last-books-result').textContent;
const retryButton=()=>$('last-books-result').children[0].children.find(node=>node.tag==='button');
function storeBooks(keys){storage.set('story-last-books',JSON.stringify(keys.map(key=>({key,book_id:'identity-'+key,title:shelfBooks.find(book=>book.key===key)?.title||'未登记作品'}))));}
function books(){shelfBooks=['a','b','c','d'].map(key=>({key,title:'作品 '+key.toUpperCase(),book_id:'identity-'+key,available:true}));storeBooks(['a','b','c','d','missing']);}
'''
        result = subprocess.run([node, '-'], input=harness + source + '\n(async()=>{\n' + checks
                                + '\n})().catch(error=>{console.error(error);process.exitCode=1;});',
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_recovery_failure_cannot_write_into_a_new_dialog(self):
        self.js(r'''
const row=await recoveryRow(),button=row.children[0],wait=deferred();reply=()=>wait.promise;
const opening=button.onclick();await flush();manager.close();openManager('新建作品');
wait.reject(Error('旧恢复稿的错误'));await opening;
assert.equal($('manager-title').textContent,'新建作品');assert.equal($('manager-error').textContent,'');
assert.equal(openedURLs.length,0);await button.onclick();assert.equal(requests.length,2,'detached row cannot submit again');
''')

    def test_recovery_success_after_close_or_new_generation_does_not_open_a_page(self):
        self.js(r'''
for(const replace of [false,true]){
 const row=await recoveryRow(),wait=deferred();reply=()=>wait.promise;const opening=row.children[0].onclick();
 manager.close();if(replace)openManager('从整书备份恢复');
 wait.resolve({url:'http://127.0.0.1/old-recovery'});await opening;
 assert.equal(openedURLs.length,0);assert.equal($('manager-error').textContent,'');
 assert.equal(localStorage.getItem('story-last-books'),null,'stale success does not update recent books');
}
''')

    def test_recovery_pending_prevents_double_open_and_announces_actual_success(self):
        self.js(r'''
const row=await recoveryRow(),button=row.children[0],detail=row.children[1],wait=deferred();reply=()=>wait.promise;
const opening=button.onclick();await flush();assert.equal(button.disabled,true);assert.ok(button.textContent.includes('正在打开'));assert.ok(detail.textContent.includes('正在打开恢复稿'));
await button.onclick();assert.equal(requests.length,2);
wait.resolve({url:'http://127.0.0.1/editor/'});await opening;
assert.equal(button.disabled,false);assert.equal(button.textContent,'作品 A · 恢复稿');assert.ok(detail.textContent.includes('已打开恢复稿'));
assert.deepEqual(openedURLs,['http://127.0.0.1/editor/#'+encodeURIComponent(recovery.id)]);
''')

    def test_recovery_failure_can_retry_and_clears_its_previous_error(self):
        self.js(r'''
const row=await recoveryRow(),button=row.children[0];reply=async()=>{throw Error('作品暂不可达');};await button.onclick();
assert.equal(button.disabled,false);assert.ok($('manager-error').textContent.includes('作品暂不可达'));assert.ok(row.children[1].textContent.includes('打开失败'));
reply=async()=>({url:'http://127.0.0.1/retry/'});await button.onclick();assert.equal($('manager-error').textContent,'');assert.ok(row.children[1].textContent.includes('已打开恢复稿'));assert.equal(openedURLs.length,1);
''')

    def test_batch_continues_after_failure_reports_unavailable_and_retries_only_failure(self):
        self.js(r'''
books();shelfBooks[3].available=false;
reply=async(action,{key})=>{if(key==='b')throw Error('目录已迁移');return {url:'http://127.0.0.1/'+key};};
await $('last-books').onclick();
assert.deepEqual(requests.map(request=>request.key),['a','b','c']);assert.equal(openedURLs.length,2);
const result=batchResult();assert.ok(result.includes('已打开 2 部，失败 1 部，不可用 2 部'));assert.ok(result.includes('作品 B：目录已迁移'));assert.ok(result.includes('作品 D：作品当前不可用'));assert.ok(result.includes('未登记作品：作品已不在书架中'));
const retry=retryButton();assert.equal(retry.textContent,'重试失败作品（1）');reply=async(action,{key})=>({url:'http://127.0.0.1/'+key});await retry.onclick();
assert.deepEqual(requests.map(request=>request.key),['a','b','c','b']);assert.ok(batchResult().includes('已打开 1 部，失败 0 部，不可用 0 部'));assert.equal(retryButton(),undefined);
''')

    def test_batch_and_retry_buttons_cannot_reenter_while_a_request_is_pending(self):
        self.js(r'''
books();storeBooks(['a','b']);let wait=deferred();reply=()=>wait.promise;
const running=$('last-books').onclick();await flush();assert.equal($('last-books').disabled,true);assert.ok(batchResult().includes('1 / 2'));
await $('last-books').onclick();assert.equal(requests.length,1);wait.reject(Error('连接失败'));await running;
assert.equal(requests.length,2);assert.equal($('last-books').disabled,false);assert.equal($('last-books').textContent,'重新打开上次作品');
const retry=retryButton();wait=deferred();reply=()=>wait.promise;const retrying=retry.onclick();await flush();await retry.onclick();await $('last-books').onclick();assert.equal(requests.length,3);
wait.resolve({url:'http://127.0.0.1/retry/'});await retrying;assert.equal(requests.length,4);assert.equal($('last-books').disabled,false);assert.equal(lastBooksBusy,false);
''')

    def test_retry_rechecks_current_book_identity_availability_and_registration(self):
        self.js(r'''
books();storeBooks(['a','b','c']);reply=async()=>{throw Error('首次失败');};await $('last-books').onclick();
const retry=retryButton();shelfBooks[0]={...shelfBooks[0],book_id:'replacement'};shelfBooks[1].available=false;shelfBooks=shelfBooks.filter(book=>book.key!=='c');
await retry.onclick();assert.equal(requests.length,3,'no stale identity is sent to open-book');
const result=batchResult();assert.ok(result.includes('已打开 0 部，失败 0 部，不可用 3 部'));assert.ok(result.includes('作品身份已变化'));assert.ok(result.includes('作品当前不可用'));assert.ok(result.includes('作品已不在书架中'));assert.equal(retryButton(),undefined);
''')

    def test_invalid_or_empty_recent_records_do_not_leave_the_control_locked(self):
        self.js(r'''
for(const stored of ['{}','not-json','[]','[null,3]']){storage.set('story-last-books',stored);await $('last-books').onclick();assert.equal(requests.length,0);assert.equal(lastBooksBusy,false);assert.equal($('last-books').disabled,false);assert.ok($('message').textContent);}
books();storeBooks(['a','a']);reply=async()=>({url:'http://127.0.0.1/a'});await $('last-books').onclick();assert.equal(requests.length,1);assert.ok(batchResult().includes('已打开 1 部，失败 0 部，不可用 0 部'));
''')

    def test_legacy_recent_keys_cannot_follow_a_replacement_and_normal_open_upgrades_a_record(self):
        self.js(r'''
books();storage.set('story-last-books','["a"]');await $('last-books').onclick();assert.equal(requests.length,0);assert.ok(batchResult().includes('旧版打开记录没有作品身份'));
rememberShelfBook('a','identity-a','作品 A');const saved=JSON.parse(storage.get('story-last-books'));assert.deepEqual(saved,[{key:'a',book_id:'identity-a',title:'作品 A'}]);
shelfBooks[0]={...shelfBooks[0],book_id:'replacement'};await $('last-books').onclick();assert.equal(requests.length,0);assert.ok(batchResult().includes('作品身份已变化'));
''')

    def test_recovery_and_batch_open_requests_keep_the_captured_book_identity(self):
        self.js(r'''
const row=await recoveryRow();reply=async()=>({url:'http://127.0.0.1/editor'});await row.children[0].onclick();assert.equal(requests.at(-1).expected_book_id,'identity-a');
books();storeBooks(['a']);await $('last-books').onclick();assert.equal(requests.at(-1).expected_book_id,'identity-a');assert.equal(JSON.parse(storage.get('story-last-books'))[0].book_id,'identity-a');
''')


if __name__ == '__main__':
    unittest.main()
