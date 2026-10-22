"""Interleaved shelf requests keep batch receipts and the next path input."""
import unittest

import test_workbench_management_open_results as open_results
import test_workbench_shelf_feedback as feedback


class WorkbenchShelfFollowupFixesTests(unittest.TestCase):
    def add_js(self, checks):
        fixture = feedback.WorkbenchShelfFeedbackTests('runTest')
        fixture.setUp()
        script = fixture.page.split('<script>', 1)[1].split('</script>', 1)[0]
        handler = script[script.index("$('refresh').onclick="):
                         script.index('let undoRemoval=')]
        fixture.source += handler
        fixture.js(checks)

    def test_batch_failure_and_retry_survive_other_operation_receipts(self):
        fixture = open_results.WorkbenchManagementOpenResultsTests('runTest')
        fixture.js(r'''
books();storeBooks(['a','b']);const wait=deferred();
reply=async(action,{key})=>{if(key==='a')return wait.promise;throw Error('B 暂不可达');};
const running=$('last-books').onclick();await flush();
const result=$('last-books-result').children[0],link=document.createElement('a');
link.textContent='点击打开：作品 X';$('message').replaceChildren(link);
wait.resolve({url:'http://127.0.0.1/a/'});await running;
assert.strictEqual($('last-books-result').children[0],result);
assert.ok(batchResult().includes('已打开 1 部，失败 1 部'));
assert.ok(batchResult().includes('作品 B：B 暂不可达'));
assert.strictEqual($('message').children[0],link,'batch completion preserves the unrelated open receipt');
$('message').textContent='作品状态已更新';
const retry=retryButton();assert.ok(retry,'later operation receipts keep batch retry reachable');
reply=async(action,{key})=>({url:'http://127.0.0.1/'+key+'/'});await retry.onclick();
assert.deepEqual(requests.map(request=>request.key),['a','b','b']);
assert.ok(batchResult().includes('已打开 1 部，失败 0 部'));
assert.equal($('message').textContent,'作品状态已更新');
''')

    def test_new_batch_replaces_previous_result_without_removing_other_receipt(self):
        fixture = open_results.WorkbenchManagementOpenResultsTests('runTest')
        fixture.js(r'''
books();storeBooks(['a']);reply=async()=>{throw Error('首次失败');};
await $('last-books').onclick();const old=$('last-books-result').children[0];
assert.ok(retryButton());$('message').textContent='其他操作已完成';
storeBooks(['c']);reply=async()=>({url:'http://127.0.0.1/c/'});await $('last-books').onclick();
assert.notStrictEqual($('last-books-result').children[0],old);
assert.ok(batchResult().includes('已打开：\n作品 C'));
assert.ok(!batchResult().includes('首次失败'));assert.equal(retryButton(),undefined);
assert.equal($('message').textContent,'其他操作已完成');
''')

    def test_add_response_keeps_path_typed_while_the_request_was_pending(self):
        self.add_js(r'''
render({books:[]});$('book-path').value='/合成/作品 A';
let resolve;pending=new Promise(done=>{resolve=done;});const adding=$('add').onclick();
assert.equal(requests[0].data.path,'/合成/作品 A');assert.equal($('add').disabled,true);
$('book-path').value='/合成/下一部 B';await $('add').onclick();assert.equal(requests.length,1);
resolve({books:[book('作品 A')]});await adding;
assert.equal($('book-path').value,'/合成/下一部 B');
assert.deepEqual(shelfBooks.map(book=>book.title),['作品 A']);
assert.equal(busy,false);assert.equal($('add').disabled,false);
pending=null;response={books:[book('作品 A'),book('下一部 B')]};await $('add').onclick();
assert.equal(requests[1].data.path,'/合成/下一部 B');assert.equal($('book-path').value,'');
''')

    def test_add_success_clears_its_unchanged_input_and_failure_keeps_new_input(self):
        self.add_js(r'''
render({books:[]});$('book-path').value='/合成/作品 A';response={books:[book('作品 A')]};
await $('add').onclick();assert.equal($('book-path').value,'');
$('book-path').value='/合成/作品 B';let reject;
pending=new Promise((done,deny)=>{reject=deny;});const adding=$('add').onclick();
$('book-path').value='/合成/作品 C';reject(Error('目录暂不可达'));await adding;
assert.equal($('book-path').value,'/合成/作品 C');
assert.ok($('message').textContent.includes('目录暂不可达'));
assert.equal(busy,false);assert.equal($('add').disabled,false);
''')


if __name__ == '__main__':
    unittest.main()
