"""Book-bound status drafts and ordered catalog/undo receipts across requests."""
import json
import unittest

import test_workbench as base
import test_workbench_library as library
import test_workbench_shelf_feedback as feedback


def fixture():
    runner = feedback.WorkbenchShelfFeedbackTests('runTest')
    runner.setUp()
    script = runner.page.split('<script>', 1)[1].split('</script>', 1)[0]
    runner.source = script[script.index('function bookName'):script.index('let undoRemoval=')]
    management = base.story.workbench._library_management_script()
    runner.source += r'''
let managerGeneration=0,undoRemoval=null,undoRemovalBusy=false;
const manager={open:false,close(){this.open=false;}},actions=new Map(),fields=[];
function openManager(){manager.open=true;fields.length=0;actions.clear();return ++managerGeneration;}
function managementText(){}function managementField(){const field={value:'',checked:false};fields.push(field);return field;}
function managementFeedback(state,title,detail){$('manager-feedback').dataset.state=state;$('manager-operation').textContent=title;$('manager-detail').textContent=detail;}
function managementButton(label,action){actions.set(label,action);}
async function managementRequest(action,data){return await requestShelfCatalog(action,data);}
''' + management[management.index('function syncUndoRemoval('):management.index('function rememberShelfBook(')]
    runner.source += management[management.index('function showRelinkBook('):management.index('function decorateBookManagement(')]
    runner.source += management[management.index("$('undo-remove').onclick"):management.index('let lastBooksBusy=')]
    return runner


class WorkbenchShelfTransactionTests(unittest.TestCase):
    def js(self, checks):
        fixture().js(checks)

    def test_same_path_new_identity_does_not_inherit_or_erase_an_old_draft(self):
        self.js(r'''
const original=book('path','long','serializing',{book_id:'original',title:'原作品'}),replacement={...original,book_id:'replacement',title:'新作品'};
render({books:[original],catalog_generation:1});chooseStatus('path','completed');const old=shelfStatusDrafts.get(shelfStatusDraftKey(original));
render({books:[replacement],catalog_generation:2});assert.equal(statusSelect('path').value,'serializing');assert.strictEqual(shelfStatusDrafts.get(shelfStatusDraftKey(original)),old);assert.equal(shelfStatusDrafts.has(shelfStatusDraftKey(replacement)),false);
chooseStatus('path','completed');assert.equal(shelfStatusDrafts.size,2);response={books:[{...replacement,writing_status:{value:'completed',verified:true,source:'author_mark'}}],catalog_generation:3};await statusSave('path').onclick();
assert.equal(requests[0].data.expected_book_id,'replacement');assert.equal(shelfStatusDrafts.size,1);assert.strictEqual(shelfStatusDrafts.get(shelfStatusDraftKey(original)),old);
''')

    def test_relink_moves_only_same_identity_draft_and_never_recaptures_old_select(self):
        self.js(r'''
const original=book('old','long','serializing',{book_id:'same',root:'/old'}),target={...original,key:'new',root:'/new'},other=book('other','short','completed',{book_id:'other-id'});
render({books:[original,other],catalog_generation:1});chooseStatus('old','completed');chooseStatus('other','serializing');showRelinkBook(original);fields[0].value='/new';
response={books:[target,other],root:'/new',action:'relink',catalog_generation:2};await actions.get('核对并更新位置')();
assert.equal(shelfStatusDrafts.has(shelfStatusDraftKey(original)),false);assert.equal(shelfStatusDrafts.get(shelfStatusDraftKey(target)).value,'completed');assert.equal(statusSelect('new').value,'completed');assert.equal(statusSelect('other').value,'serializing');assert.equal(shelfStatusDrafts.size,2);
renderShelf();assert.equal(shelfStatusDrafts.size,2,'the detached old select must not revive a second draft');assert.equal(statusSelect('old'),undefined);
''')

    def test_late_add_catalog_cannot_revert_a_confirmed_status(self):
        self.js(r'''
render({books,catalog_generation:1});let resolveAdd,resolveSave;pending=new Promise(done=>{resolveAdd=done;});$('book-path').value='/new';const adding=$('add').onclick();
chooseStatus('连载稿','completed');pending=new Promise(done=>{resolveSave=done;});const saving=statusSave('连载稿').onclick();const latest={books:[book('连载稿','long','completed'),...books.slice(1),book('新增')],catalog_generation:3};resolveSave(latest);await saving;
resolveAdd({books:[...books,book('新增')],catalog_generation:2});await adding;assert.equal(shelfBooks[0].writing_status.value,'completed');assert.equal(statusSelect('连载稿').value,'completed');assert.equal(shelfStatusDrafts.size,0);assert.equal(shelfCatalogGeneration,3);
''')

    def test_unversioned_mutation_falls_back_to_request_revision(self):
        self.js(r'''
render({books});let resolve;pending=new Promise(done=>{resolve=done;});$('book-path').value='/new';const adding=$('add').onclick();render({books:[book('已确认的新状态','long','completed')]});resolve({books});await adding;
assert.equal(shelfBooks[0].title,'已确认的新状态');assert.equal(shelfRefreshPending,true);
''')

    def test_snapshot_generations_are_bound_to_the_page_service_instance(self):
        self.js(r'''
render({books:[book('当前服务')],catalog_generation:4,catalog_instance:'current'});
assert.equal(render({books:[book('其他服务')],catalog_generation:100,catalog_instance:'other'}),false);assert.equal(shelfBooks[0].title,'当前服务');
assert.equal(render({books:[book('旧快照')],catalog_generation:3,catalog_instance:'current'}),false);assert.equal(shelfCatalogGeneration,4);
''')
        self.js(r'''
assert.equal(render({books:[book('重新载入后的新服务')],catalog_generation:1,catalog_instance:'other'}),true);assert.equal(shelfCatalogGeneration,1);assert.equal(shelfCatalogInstance,'other');
''')

    def test_undo_is_single_flight_and_an_old_response_cannot_clear_a_later_receipt(self):
        self.js(r'''
const first=book('first'),second=book('second'),third=book('third');render({books:[second,third],catalog_generation:1});undoRemoval={id:'undo-first',key:first.key,title:first.title};syncUndoRemoval();
let resolveUndo;pending=new Promise(done=>{resolveUndo=done;});const undoing=$('undo-remove').onclick();await $('undo-remove').onclick();assert.equal(calls,1);assert.equal(busy,true);assert.equal($('undo-remove').disabled,true);
pending=null;response={books:[first,third],removed:true,undo_id:'undo-second',catalog_generation:3};showRemoveBook(second);await actions.get('移出书架')();const latest=undoRemoval;assert.equal(latest.id,'undo-second');assert.ok($('undo-remove').textContent.includes('second'));
resolveUndo({books:[first,second,third],restored:true,catalog_generation:2});await undoing;
assert.strictEqual(undoRemoval,latest);assert.equal($('undo-remove').hidden,false);assert.equal($('undo-remove').disabled,false);assert.equal(busy,false);assert.deepEqual(shelfBooks.map(book=>book.key),['first','third']);assert.ok($('message').textContent.includes('first'));
response={books:[first,second,third],restored:true,catalog_generation:4};await $('undo-remove').onclick();assert.equal(undoRemoval,null);assert.equal($('undo-remove').hidden,true);assert.equal(requests.at(-1).data.receipt_id,'undo-second');
''')

    def test_failed_old_undo_keeps_new_receipt_and_identifies_the_failed_book(self):
        self.js(r'''
render({books});undoRemoval={id:'old',title:'旧作品'};syncUndoRemoval();let reject;pending=new Promise((done,deny)=>{reject=deny;});const undoing=$('undo-remove').onclick();undoRemoval={id:'new',title:'新作品'};syncUndoRemoval();reject(Error('目录已变化'));await undoing;
assert.equal(undoRemoval.id,'new');assert.ok($('undo-remove').textContent.includes('新作品'));assert.ok($('message').textContent.includes('旧作品'));assert.equal($('undo-remove').disabled,false);assert.equal(busy,false);
''')

    def test_stale_remove_receipt_cannot_clear_a_new_draft_or_overwrite_a_later_undo(self):
        self.js(r'''
const original=book('a','long','serializing',{book_id:'same'});render({books:[original],catalog_generation:1});chooseStatus('a','completed');showRemoveBook(original);
let resolve;pending=new Promise(done=>{resolve=done;});const removing=actions.get('移出书架')();render({books:[original],catalog_generation:3});chooseStatus('a','unknown');const draft=shelfStatusDrafts.get(shelfStatusDraftKey(original));undoRemoval={id:'newer',title:'更晚移出的作品'};const receipt=undoRemoval;
resolve({books:[],removed:true,undo_id:'older',catalog_generation:2});await removing;
assert.strictEqual(shelfStatusDrafts.get(shelfStatusDraftKey(original)),draft);assert.equal(statusSelect('a').value,'unknown');assert.strictEqual(undoRemoval,receipt);assert.equal(shelfBooks[0].book_id,'same');assert.equal($('manager-feedback').dataset.state,'unknown');assert.equal(manager.open,true);
''')


class WorkbenchShelfHTTPTransactionTests(unittest.TestCase):
    setUp = library.WorkbenchLibraryTests.setUp
    start = library.WorkbenchLibraryTests.start
    serve = library.WorkbenchLibraryTests.serve
    request = library.WorkbenchLibraryTests.request
    post = library.WorkbenchLibraryTests.post
    book = library.WorkbenchLibraryTests.book

    def js(self, server, initial, checks, extras=None):
        endpoint = {'port': server.server_port, 'origin': f'http://127.0.0.1:{server.server_port}',
                    'token': server.library_token}
        transport = 'const endpoint=' + json.dumps(endpoint) + ';' + r'''
let markAddRead,releaseAdd;const addRead=new Promise(done=>{markAddRead=done;}),addGate=new Promise(done=>{releaseAdd=done;});
const call=async(action,data={})=>{requests.push({action,data});calls++;const result=await new Promise((resolve,reject)=>{
 const body=JSON.stringify(data),request=require('node:http').request({host:'127.0.0.1',port:endpoint.port,path:'/api/'+action,method:'POST',headers:{Host:'127.0.0.1:'+endpoint.port,'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),Origin:endpoint.origin,'X-Story-Token':endpoint.token}},response=>{
  let raw='';response.setEncoding('utf8');response.on('data',data=>raw+=data);response.on('end',()=>{try{const value=JSON.parse(raw);if(response.statusCode!==200)reject(Error(value.error+': '+value.message));else resolve(value);}catch(error){reject(error);}});
 });request.on('error',reject);request.end(body);
});if(action==='add-book'){markAddRead();await addGate;}return result;};
'''
        seed = 'const initial=' + json.dumps(initial, ensure_ascii=False) + ';const extras=' + json.dumps(extras or {}) + ';'
        fixture().js(seed + checks, call_source=transport)

    def test_actual_http_same_path_replacement_rejects_old_identity_and_keeps_drafts_separate(self):
        path = self.book('同路径')
        server = self.start(books=[path])
        initial = self.post(server, 'books')[1]
        old = initial['books'][0]
        self.assertEqual(self.post(server, 'remove-book', {'key': old['key']})[0], 200)
        path.rename(self.root / '保留原作品')
        base.story.Book.create(path, '替换后的作品', 'long')
        new = self.post(server, 'add-book', {'path': str(path)})[1]['books'][0]
        before = (path / '.story/state.sqlite3').read_bytes()
        self.js(server, initial, r'''
render(initial);const old=initial.books[0],key=old.key;chooseStatus(key,'completed');await refresh();const replacement=shelfBooks[0];
assert.notEqual(replacement.book_id,old.book_id);assert.equal(statusSelect(key).value,'unknown');assert.equal(shelfStatusDrafts.get(shelfStatusDraftKey(old)).value,'completed');
await assert.rejects(call('set-book-status',{key,value:'completed',expected_book_id:old.book_id}),/workbench_book_changed/);
assert.equal((await call('books')).books[0].writing_status.value,'unknown');chooseStatus(key,'completed');await statusSave(key).onclick();assert.equal(shelfBooks[0].writing_status.value,'completed');assert.equal(shelfStatusDrafts.size,1);
''')
        stored, _ = self.w._library_load(self.state)
        self.assertEqual(stored[new['key']]['book_id'], new['book_id'])
        self.assertEqual(stored[new['key']]['shelf_status'], 'completed')
        self.assertEqual((path / '.story/state.sqlite3').read_bytes(), before)

    def test_actual_http_late_add_snapshot_does_not_regress_saved_status(self):
        path, new_path = self.book(), self.book('新登记作品')
        server = self.start(books=[path])
        initial = self.post(server, 'books')[1]
        self.js(server, initial, r'''
render(initial);const key=initial.books[0].key;$('book-path').value=extras.newPath;const adding=$('add').onclick();await addRead;
chooseStatus(key,'completed');await statusSave(key).onclick();const latest=shelfCatalogGeneration;releaseAdd();await adding;
assert.equal(shelfBooks.find(book=>book.key===key).writing_status.value,'completed');assert.equal(statusSelect(key).value,'completed');assert.equal(shelfCatalogGeneration,latest);assert.equal(shelfStatusDrafts.size,0);
''', {'newPath': str(new_path)})
        result = self.post(server, 'books')[1]
        self.assertEqual(next(book for book in result['books'] if book['root'] == str(path))['writing_status']['value'], 'completed')


if __name__ == '__main__':
    unittest.main()
