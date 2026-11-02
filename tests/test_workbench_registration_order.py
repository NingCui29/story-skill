"""Registration repairs join the same ordered catalog stream as shelf refreshes."""
import json
import unittest

import test_workbench_library as library
import test_workbench_shelf_transactions as transactions


class RegistrationOrderTests(unittest.TestCase):
    setUp = library.WorkbenchLibraryTests.setUp
    start = library.WorkbenchLibraryTests.start
    serve = library.WorkbenchLibraryTests.serve
    request = library.WorkbenchLibraryTests.request
    post = library.WorkbenchLibraryTests.post
    book = library.WorkbenchLibraryTests.book

    def replacement(self):
        root = self.book('原登记合成作品')
        server = self.start(books=[root])
        original = self.post(server, 'books')[1]['books'][0]
        root.rename(self.root / '保留原合成作品')
        library.base.story.Book.create(root, '已确认的新合成作品', 'long')
        initial = self.post(server, 'books')[1]
        self.assertFalse(initial['books'][0]['available'])
        current = self.w._library_entry(root)
        return server, original, initial, current

    def test_repair_receipt_advances_generation_and_preserves_service_instance(self):
        server, original, initial, current = self.replacement()
        target = {'key': original['key'], 'expected_book_id': original['book_id']}
        status, preview = self.post(server, 'registration-preview', target)
        self.assertEqual(status, 200, preview)
        status, repaired = self.post(server, 'repair-registration', {
            **target, 'confirmation': preview['confirmation'],
            'title': current['title'], 'confirmed': True})
        self.assertEqual(status, 200, repaired)
        self.assertEqual(type(repaired['catalog_generation']), int)
        self.assertGreater(repaired['catalog_generation'], initial['catalog_generation'])
        self.assertEqual(repaired['catalog_instance'], initial['catalog_instance'])
        self.assertTrue(repaired['books'][0]['available'])
        self.assertEqual(repaired['books'][0]['book_id'], current['book_id'])
        status, refreshed = self.post(server, 'books')
        self.assertEqual(status, 200, refreshed)
        self.assertGreater(refreshed['catalog_generation'], repaired['catalog_generation'])
        self.assertEqual(refreshed['catalog_instance'], repaired['catalog_instance'])
        self.assertEqual(refreshed['books'][0]['book_id'], current['book_id'])

    def test_delayed_real_http_refresh_cannot_revert_repair_and_next_refresh_works(self):
        server, original, initial, current = self.replacement()
        endpoint = {'port': server.server_port,
                    'origin': f'http://127.0.0.1:{server.server_port}',
                    'token': server.library_token}
        transport = 'const endpoint=' + json.dumps(endpoint) + ';' + r'''
let markRefreshRead,releaseRefresh,holdFirstRefresh=true;
const refreshRead=new Promise(resolve=>{markRefreshRead=resolve;}),refreshGate=new Promise(resolve=>{releaseRefresh=resolve;});
const call=async(action,data={})=>{
 requests.push({action,data});calls++;
 const result=await new Promise((resolve,reject)=>{
  const body=JSON.stringify(data),request=require('node:http').request({host:'127.0.0.1',port:endpoint.port,path:'/api/'+action,method:'POST',headers:{Host:'127.0.0.1:'+endpoint.port,'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),Origin:endpoint.origin,'X-Story-Token':endpoint.token}},response=>{
   let raw='';response.setEncoding('utf8');response.on('data',data=>{raw+=data;});response.on('end',()=>{try{const value=JSON.parse(raw);if(response.statusCode!==200)reject(Error(value.error+': '+value.message));else resolve(value);}catch(error){reject(error);}});
  });request.on('error',reject);request.end(body);
 });
 if(action==='books'&&holdFirstRefresh){holdFirstRefresh=false;markRefreshRead();await refreshGate;}
 return result;
};
'''
        runner = transactions.fixture()
        # The receipt button does not participate in catalog ordering.
        runner.source += '\nfunction offerOpenBook(){}\n'
        seed = 'const initial=' + json.dumps(initial, ensure_ascii=False) + ';'
        seed += 'const expected=' + json.dumps(current, ensure_ascii=False) + ';'
        runner.js(seed + r'''
render(initial);const original=shelfBooks[0];
const refreshing=refresh();await refreshRead;
await showRegistrationRepair(original);fields[0].value=expected.title;fields[1].checked=true;
await actions.get('确认更新登记')();
const acceptedGeneration=shelfCatalogGeneration;
assert.ok(acceptedGeneration>initial.catalog_generation);
assert.equal(shelfBooks[0].book_id,expected.book_id);assert.equal(shelfBooks[0].available,true);
assert.equal(manager.open,false);assert.ok($('message').textContent.includes(expected.title));
releaseRefresh();await refreshing;
assert.equal(shelfCatalogGeneration,acceptedGeneration,'an older refresh must not replace the repair receipt');
assert.equal(shelfBooks[0].book_id,expected.book_id);assert.equal(shelfBooks[0].available,true);
await refresh();
assert.ok(shelfCatalogGeneration>acceptedGeneration,'a later normal refresh must still apply');
assert.equal(shelfBooks[0].book_id,expected.book_id);assert.equal(shelfBooks[0].available,true);
assert.deepEqual(requests.map(row=>row.action),['books','registration-preview','repair-registration','books']);
''', call_source=transport)
        stored, _ = self.w._library_load(self.state)
        self.assertEqual(stored[original['key']]['book_id'], current['book_id'])


if __name__ == '__main__':
    unittest.main()
