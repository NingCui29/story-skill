"""Lost HTTP save replies reconcile through the real server without another write."""
import json
import socket
import subprocess
import unittest
from urllib.parse import urlparse
from unittest.mock import patch

import test_workbench_library as library
import test_workbench_save_status as save_status


class SaveResponseHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = save_status.WorkbenchSaveStatusTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = save_status.base.story.workbench
        self.root = self.fixture.fixture.root
        self.server = library.WorkbenchLibraryTests.serve(self, self.w.editor_server(self.root))
        self.drop_before_save = False
        self.drop_after_save = True
        self.writes = []
        self.dropped = []
        handler = self.server.RequestHandlerClass
        original_post, original_reply = handler.do_POST, handler.reply

        def disconnect(request):
            request.close_connection = True
            request.connection.shutdown(socket.SHUT_RDWR)

        def post(request):
            if request.path.endswith('/api/save') and self.drop_before_save:
                self.drop_before_save = False
                disconnect(request)
                return
            return original_post(request)

        def reply(request, status, data, content_type='application/json; charset=utf-8'):
            if request.path.endswith('/api/save') and status == 200:
                receipt = json.loads(data)
                stored = self.w._editor_document(self.root, receipt['id'])
                self.writes.append({'receipt': receipt, 'document': stored})
                if self.drop_after_save:
                    self.drop_after_save = False
                    self.dropped.append(receipt)
                    # The manuscript and request receipt are already durable.
                    # Only this synthetic server loses the response headers.
                    disconnect(request)
                    return
            return original_reply(request, status, data, content_type)

        handler.do_POST, handler.reply = post, reply

    def candidates(self):
        return set((self.root / '.story/drafts/workbench').glob('*_候选_*.md'))

    def working_source(self):
        document = self.w._editor_document(self.root, self.fixture.backend['candidate']['id'])
        self.fixture.backend.update(original=document, submitted=document['text'] + '工作稿新增文字。\n')
        return document

    def js(self, checks, setup=''):
        url = urlparse(self.server.editor_url)
        endpoint = {'port': self.server.server_port, 'prefix': url.path,
                    'origin': f'http://127.0.0.1:{self.server.server_port}',
                    'token': url.path.strip('/')}
        transport = 'const endpoint=' + json.dumps(endpoint) + ';' + r'''
const httpResults=[];let holdSaveStatus=false,markStatusRead,releaseStatus;
const statusRead=new Promise(resolve=>{markStatusRead=resolve;}),statusGate=new Promise(resolve=>{releaseStatus=resolve;});
const call=async(action,data={})=>{
 requests.push({action,...data});
 const result=await new Promise((resolve,reject)=>{
  const body=JSON.stringify(data),request=require('node:http').request({host:'127.0.0.1',port:endpoint.port,path:endpoint.prefix+'api/'+action,method:'POST',headers:{Host:'127.0.0.1:'+endpoint.port,'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),Origin:endpoint.origin,'X-Story-Token':endpoint.token}},response=>{
   let raw='';response.setEncoding('utf8');response.on('data',data=>{raw+=data;});response.on('end',()=>{try{const value=JSON.parse(raw);httpResults.push({action,status:response.statusCode,state:value.state,error:value.error});if(response.statusCode!==200)reject(Object.assign(Error(value.message||value.error),{code:value.error,responseReceived:true}));else resolve(value);}catch(error){reject(error);}});
  });request.on('error',error=>{httpResults.push({action,transport_error:error.code});reject(error);});request.end(body);
 });
 if(action==='save-status'&&holdSaveStatus){holdSaveStatus=false;markStatusRead();await statusGate;}
 return result;
};
'''
        original_run = subprocess.run

        def with_http(*args, **kwargs):
            # Reuse the production-function/DOM fixture, replacing only its
            # transport stub. No browser or native window is started.
            program = kwargs['input']
            start = program.index('const call=async(action,data={})=>{')
            end = program.index('const timers=', start)
            kwargs['input'] = program[:start] + transport + program[end:]
            return original_run(*args, **kwargs)

        with patch.object(save_status.subprocess, 'run', side_effect=with_http):
            self.fixture.js(checks, setup='original.recoveryKey=crypto.randomUUID();' + setup)

    def test_formal_save_lost_reply_reconciles_to_exactly_one_candidate(self):
        before = self.candidates()
        self.js(r'''
assert.equal(original.kind,'formal');assert.equal(await saveDocument(original),false);
assert.equal($('state').textContent,'保存结果未知 · 请先核对');
assert.equal(original.saveResultPending,true);assert.ok(original.pendingSaveReadback.request_id);
assert.equal(original.pendingSaveReadback.id,undefined);assert.equal($('save-retry').textContent,'重试核对');
assert.equal(pendingClose(),true);
const requestID=original.pendingSaveReadback.request_id;
assert.equal(await $('save-retry').onclick(),true);
assert.deepEqual(requests.map(row=>row.action),['recover','save','save-status','open']);
assert.equal(requests.filter(row=>row.action==='save').length,1);
assert.equal(requests.find(row=>row.action==='save-status').request_id,requestID);
assert.equal($('state').textContent,'已保存候选');assert.equal(pendingClose(),false);
assert.equal(docs.get(active).text,backend.submitted);assert.equal(original.pendingSaveReadback,undefined);
''')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(len(self.dropped), 1)
        self.assertEqual(len(self.candidates() - before), 1)
        self.assertEqual(self.writes[0]['document']['text'], self.fixture.backend['submitted'])

    def test_working_save_lost_reply_reconciles_in_place_without_stale_retry(self):
        original = self.working_source()
        before = self.candidates()
        self.js(r'''
const oldSHA=original.sha256;assert.equal(await saveDocument(original),false);
assert.equal($('state').textContent,'保存结果未知 · 请先核对');assert.equal(pendingClose(),true);
assert.equal(await $('save-retry').onclick(),true);
assert.deepEqual(requests.map(row=>row.action),['recover','save','save-status','open']);
assert.equal(requests.filter(row=>row.action==='save').length,1);
assert.equal(httpResults.some(row=>row.error==='stale_snapshot'),false);
assert.equal(active,backend.original.id);assert.notEqual(docs.get(active).sha256,oldSHA);
assert.equal(docs.get(active).text,backend.submitted);assert.equal($('state').textContent,'已保存候选');
assert.equal(pendingClose(),false);
''')
        self.assertEqual(self.candidates(), before)
        self.assertEqual(len(self.writes), 1)
        self.assertTrue(self.writes[0]['receipt']['updated_existing'])
        self.assertEqual(self.writes[0]['receipt']['id'], original['id'])

    def assert_input_and_active_document_preserved(self):
        other = self.w._editor_document(self.root, 'formal:2')
        self.js(r'''
assert.equal(await saveDocument(original),false);holdSaveStatus=true;
const reconciling=$('save-retry').onclick();await statusRead;
original.value+='核对期间继续输入。';const latest=original.value;
docs.set(other.id,{...other,value:other.text});await openDoc(other.id);
releaseStatus();assert.equal(await reconciling,true);
assert.equal(active,other.id,'a completed background reconciliation cannot steal the active document');
const candidate=docs.get(original.savedCandidate);
assert.equal(candidate.text,backend.submitted);assert.equal(candidate.value,latest);assert.equal(dirty(candidate),true);
assert.equal(requests.filter(row=>row.action==='save').length,1);
assert.equal(original.pendingSaveReadback,undefined);assert.equal(pendingClose(),true);
''', setup='const other=' + json.dumps(other, ensure_ascii=False) + ';')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]['document']['text'], self.fixture.backend['submitted'])

    def test_formal_input_and_other_active_document_survive_pending_reconciliation(self):
        self.assert_input_and_active_document_preserved()

    def test_working_input_and_other_active_document_survive_pending_reconciliation(self):
        self.working_source()
        self.assert_input_and_active_document_preserved()

    def test_not_started_status_never_automatically_resends_save(self):
        self.drop_before_save = True
        self.drop_after_save = False
        before = self.candidates()
        self.js(r'''
assert.equal(await saveDocument(original),false);
assert.equal($('state').textContent,'保存结果未知 · 请先核对');
const requestID=original.pendingSaveReadback.request_id;
assert.equal(await $('save-retry').onclick(),false);
assert.deepEqual(requests.map(row=>row.action),['recover','save','save-status']);
assert.equal(requests.filter(row=>row.action==='save').length,1);
assert.ok(httpResults.some(row=>row.action==='save-status'&&row.state==='not_started'));
assert.equal(original.pendingSaveReadback.not_started,true);assert.equal(original.saveResultPending,false);
assert.equal(original.pendingSaveReadback.request_id,requestID);
assert.ok(original.saveError.includes('尚未开始'));assert.equal(original.value,backend.submitted);assert.equal(pendingClose(),true);
// A separate explicit click retries the same operation, never a new identity.
assert.equal(await $('save').onclick(),true);
const writes=requests.filter(row=>row.action==='save');assert.equal(writes.length,2);
assert.equal(writes[1].request_id,requestID);assert.equal($('state').textContent,'已保存候选');
''')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.dropped, [])
        self.assertEqual(len(self.candidates() - before), 1)


if __name__ == '__main__':
    unittest.main()
