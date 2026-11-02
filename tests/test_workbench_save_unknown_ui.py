"""Unknown save replies reconcile one frozen request before any later save."""
import subprocess
import unittest
from unittest.mock import patch

import test_workbench_save_status as ui


TRANSPORT = r'''
const saveOperations=new Map(),serverDocuments=new Map();
const transport={saveFailure:null,statusState:null,statusFailure:false,statusWrongId:false,openError:null,openMismatch:null,holdStatus:false};
let releaseStatus;
const hash=text=>require('crypto').createHash('sha256').update(text).digest('hex');
const call=async(action,data={})=>{
 requests.push({action,...copy(data)});
 if(action==='recover')return copy(backend.recovery);
 if(action==='save'){
  if(transport.saveFailure==='not_received')throw Error('connection failed before request arrived');
  let operation=saveOperations.get(data.request_id);
  if(operation)assert.deepEqual(operation.payload,data);
  else{
   const fresh={...copy(backend.candidate),id:backend.saved.id,text:data.text};
   fresh.sha256=hash((fresh.prefix||'')+data.text);fresh.metadata_sha256=hash('synthetic metadata '+data.text);
   const receipt={...copy(backend.saved),request_id:data.request_id,state:'saved',sha256:fresh.sha256,metadata_sha256:fresh.metadata_sha256,text_sha256:hash(data.text)};
   operation={payload:copy(data),receipt};saveOperations.set(data.request_id,operation);serverDocuments.set(fresh.id,fresh);
  }
  if(transport.saveFailure==='reply_lost')throw Error('successful save reply lost');
  return copy(operation.receipt);
 }
 if(action==='save-status'){
  if(transport.holdStatus)await new Promise(resolve=>releaseStatus=resolve);
  if(transport.statusFailure)throw Error('readback unavailable');
  const request_id=transport.statusWrongId?'different-request-id':data.request_id;
  const operation=saveOperations.get(data.request_id),state=transport.statusState||(operation?'saved':'not_started');
  if(state!=='saved')return {ok:true,request_id,state};
  return {...copy(operation.receipt),request_id};
 }
 if(action==='open'){
  if(transport.openError)throw Object.assign(Error('open rejected after save succeeded'),{code:transport.openError});
  const fresh=copy(serverDocuments.get(data.id));assert.ok(fresh);
  if(transport.openMismatch==='text')fresh.text+='external body change';
  if(transport.openMismatch==='sha256')fresh.sha256='0'.repeat(64);
  if(transport.openMismatch==='metadata_sha256')fresh.metadata_sha256='0'.repeat(64);
  return fresh;
 }
 throw Error('Unexpected action '+action);
};
'''


class SaveUnknownUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = ui.WorkbenchSaveStatusTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def js(self, case):
        run = subprocess.run
        def execute(*args, **kwargs):
            program = kwargs.get('input')
            if isinstance(program, str) and args and 'node' in str(args[0][0]):
                start = program.index('const call=async(action,data={})=>{')
                end = program.index('const timers=', start)
                kwargs['input'] = program[:start] + TRANSPORT + program[end:]
            return run(*args, **kwargs)
        with patch.object(subprocess, 'run', side_effect=execute):
            self.fixture.js(case, setup='original.recoveryKey=crypto.randomUUID();')

    def test_open_rejection_cannot_clear_a_known_successful_save_receipt(self):
        for code in ('invalid_input', 'workbench_changed', 'stale_snapshot'):
            with self.subTest(code=code):
                self.js("transport.openError=" + repr(code) + r''';
assert.equal(await saveDocument(original),false);
assert.ok(original.pendingSaveReadback?.id);assert.equal(original.saveResultPending,true);assert.equal(pendingClose(),true);
transport.openError=null;assert.equal(await saveDocument(original),true);
assert.equal(requests.filter(r=>r.action==='save').length,1);
assert.equal(requests.filter(r=>r.action==='open').length,2);assert.equal(pendingClose(),false);
''')

    def test_lost_success_reply_retries_only_status_and_open(self):
        self.js(r'''
transport.saveFailure='reply_lost';assert.equal(await saveDocument(original),false);
const request=original.pendingSaveReadback.request_id;
assert.ok(request);assert.equal(original.pendingSaveReadback.id,undefined);assert.equal(pendingClose(),true);
transport.saveFailure=null;assert.equal(await saveDocument(original),true);
assert.deepEqual(requests.filter(r=>['save','save-status','open'].includes(r.action)).map(r=>r.action),['save','save-status','open']);
assert.equal(requests.find(r=>r.action==='save-status').request_id,request);assert.equal(saveOperations.size,1);
assert.equal(original.pendingSaveReadback,undefined);assert.equal(pendingClose(),false);
''')

    def test_pending_or_changed_status_preserves_input_and_does_not_write(self):
        for state in ('pending', 'changed'):
            with self.subTest(state=state):
                self.js("transport.statusState=" + repr(state) + r''';
transport.saveFailure='reply_lost';await saveDocument(original);
const pending=original.pendingSaveReadback;original.value+='newer input';
assert.equal(await saveDocument(original),false);assert.equal(await saveDocument(original),false);
assert.strictEqual(original.pendingSaveReadback,pending);assert.ok(original.value.endsWith('newer input'));
assert.equal(requests.filter(r=>r.action==='save').length,1);assert.equal(requests.filter(r=>r.action==='save-status').length,2);
assert.equal(requests.filter(r=>r.action==='open').length,0);assert.equal(pendingClose(),true);
''')

    def test_status_network_failure_preserves_the_same_request_for_readback(self):
        self.js(r'''
transport.saveFailure='reply_lost';await saveDocument(original);const pending=original.pendingSaveReadback;
transport.statusFailure=true;assert.equal(await saveDocument(original),false);
assert.strictEqual(original.pendingSaveReadback,pending);assert.equal(pendingClose(),true);
transport.statusFailure=false;assert.equal(await saveDocument(original),true);
assert.equal(requests.filter(r=>r.action==='save').length,1);assert.equal(saveOperations.size,1);
assert.equal(pendingClose(),false);
''')

    def test_wrong_status_identity_cannot_adopt_another_save(self):
        self.js(r'''
transport.saveFailure='reply_lost';await saveDocument(original);const pending=original.pendingSaveReadback;
transport.statusWrongId=true;assert.equal(await saveDocument(original),false);
assert.strictEqual(original.pendingSaveReadback,pending);assert.equal(original.value,backend.submitted);
assert.equal(requests.filter(r=>r.action==='open').length,0);assert.equal(requests.filter(r=>r.action==='save').length,1);assert.equal(pendingClose(),true);
''')

    def test_readback_with_later_edits_and_another_active_file_preserves_both(self):
        self.js(r'''
transport.saveFailure='reply_lost';await saveDocument(original);
transport.holdStatus=true;const waiting=saveDocument(original);await flush();assert.equal(typeof releaseStatus,'function');
original.value+='newer input while reconciling';
const other={id:'file:other',editable:true,text:'saved elsewhere',value:'new input elsewhere',title:'另一文件',kind:'material'};
docs.set(other.id,other);active=other.id;releaseStatus();assert.equal(await waiting,true);
assert.equal(active,other.id);assert.equal(other.value,'new input elsewhere');
const saved=docs.get(backend.saved.id);assert.equal(saved.text,backend.submitted);assert.equal(saved.value,backend.submitted+'newer input while reconciling');
assert.equal(dirty(saved),true);assert.equal(pendingClose(),true);assert.equal(requests.filter(r=>r.action==='save').length,1);
''')

    def test_not_started_requires_a_separate_click_with_the_same_frozen_payload(self):
        self.js(r'''
transport.saveFailure='not_received';await saveDocument(original);
const first=requests.find(r=>r.action==='save'),pending=original.pendingSaveReadback;
original.value+='newer text after failed connection';
assert.equal(await saveDocument(original),false);assert.equal(pending.not_started,true);
assert.equal(requests.filter(r=>r.action==='save').length,1);assert.equal(requests.filter(r=>r.action==='save-status').length,1);
assert.equal(pendingClose(),true);transport.saveFailure=null;
assert.equal(await saveDocument(original),true);
const writes=requests.filter(r=>r.action==='save');assert.equal(writes.length,2);assert.deepEqual(writes[1],first);
assert.equal(saveOperations.size,1);const saved=docs.get(backend.saved.id);
assert.equal(saved.text,backend.submitted);assert.equal(saved.value,backend.submitted+'newer text after failed connection');assert.equal(dirty(saved),true);assert.equal(pendingClose(),true);
''')

    def test_explicit_new_version_cannot_bypass_unknown_or_not_started_operation(self):
        for failure in ('reply_lost', 'not_received'):
            with self.subTest(failure=failure):
                self.js("transport.saveFailure=" + repr(failure) + r''';
await saveDocument(original,{newVersion:true});const pending=original.pendingSaveReadback;
if(transport.saveFailure==='not_received')await saveDocument(original);
const count=requests.length;assert.equal(await saveDocument(original,{newVersion:true}),false);
assert.equal(requests.length,count);assert.strictEqual(original.pendingSaveReadback,pending);
assert.equal(pending.payload.save_mode,'version');assert.equal(pendingClose(),true);
''')

    def test_changed_open_content_or_hash_cannot_clear_pending_input(self):
        for changed in ('text', 'sha256', 'metadata_sha256'):
            with self.subTest(changed=changed):
                self.js("transport.openMismatch=" + repr(changed) + r''';
assert.equal(await saveDocument(original),false);assert.ok(original.pendingSaveReadback.id);
assert.equal(original.value,backend.submitted);assert.equal(pendingClose(),true);
assert.equal(await saveDocument(original),false);assert.equal(requests.filter(r=>r.action==='save').length,1);
transport.openMismatch=null;assert.equal(await saveDocument(original),true);assert.equal(pendingClose(),false);
''')


if __name__ == '__main__':
    unittest.main()
