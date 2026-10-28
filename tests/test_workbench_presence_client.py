"""Actual isolated reporter sends metadata only and retains local buffers."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base


class EditorPresenceClientTests(unittest.TestCase):
    def js(self, checks, config=True):
        node = shutil.which('node')
        if not node:self.skipTest('Node.js required')
        configuration = {'page_id': 'server-issued-page', 'page_token': 'ephemeral-page-capability',
                         'heartbeat_ms': 3000} if config else None
        source = base.story.workbench._editor_presence_script().replace('__EDITOR_PRESENCE_CONFIG__', json.dumps(configuration))
        harness = r'''
const assert=require('assert'),TOKEN='service-token',location={pathname:'/service-token/'};
const docs=new Map(),events={window:new Map(),document:new Map()},announcements=[],timers=[];
class CustomEvent{constructor(type,options){this.type=type;this.detail=options.detail;}}
const add=(scope,type,callback)=>{if(!events[scope].has(type))events[scope].set(type,[]);events[scope].get(type).push(callback);};
const window={addEventListener:(type,callback)=>add('window',type,callback),dispatchEvent:event=>announcements.push(event)};
const document={hidden:false,addEventListener:(type,callback)=>add('document',type,callback)};
const emit=(scope,type,event={})=>{event.type=type;for(const callback of events[scope].get(type)||[])callback(event);};
const setInterval=(callback,delay)=>{timers.push({callback,delay});return timers.length;},clearInterval=()=>{};
const dirty=d=>!!(d.editable&&d.value!==d.text),displayTitle=d=>d.title||d.id;
const requests=[];let handler=async body=>({ok:true,json:async()=>({ok:true,accepted:true,summary:{sequence:body.sequence}})});
const fetch=async(url,options)=>{const body=JSON.parse(options.body);requests.push({url,body,options});return await handler(body,options);};
async function settle(){for(let index=0;index<12;index++)await Promise.resolve();}
const doc=(id,title)=>({id,title,text:'PRIVATE ORIGINAL BODY',value:'PRIVATE ORIGINAL BODY',editable:true});
'''
        program = harness + source + '\n(async()=>{await settle();' + checks + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node, '-'], input=program, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_registration_stays_uninitialized_until_ready_and_never_sends_body(self):
        self.js(r'''
assert.equal(requests.length,1);assert.equal(requests[0].body.initialized,false);
const first=doc('formal:1','第一章'),other=doc('candidate:2','隐藏候选');other.value='PRIVATE UNSAVED BODY';other.saving=true;other.recovering=Promise.resolve();other.needs_recovery=true;
docs.set(first.id,first);docs.set(other.id,other);editorPresence.ready();await settle();
const request=requests.at(-1);assert.equal(request.body.initialized,true);assert.equal(request.body.buffers.length,2);
assert.deepEqual(request.body.buffers[1],{id:'candidate:2',title:'隐藏候选',dirty:true,saving:true,recovering:true,needs_recovery:true});
assert.equal(request.url,'/service-token/api/presence');assert.equal(request.options.headers['X-Story-Token'],TOKEN);
assert.ok(!JSON.stringify(requests).includes('PRIVATE'));assert.equal(docs.get('candidate:2').value,'PRIVATE UNSAVED BODY');
''')

    def test_input_only_reports_state_transitions_and_heartbeat_refreshes_sequence(self):
        self.js(r'''
const d=doc('candidate:1','候选');docs.set(d.id,d);editorPresence.ready();await settle();const before=requests.length;
d.value='changed';emit('document','input');await settle();assert.equal(requests.length,before+1);assert.equal(requests.at(-1).body.buffers[0].dirty,true);
for(let index=0;index<100;index++){d.value+='x';emit('document','input');}await settle();assert.equal(requests.length,before+1,'typing body is not a metadata stream');
const sequence=requests.at(-1).body.sequence;timers[0].callback();await settle();assert.equal(requests.at(-1).body.sequence,sequence+1);
d.saving=true;editorPresence.changed();await settle();assert.equal(requests.at(-1).body.buffers[0].saving,true);
d.saving=false;d.recovering=Promise.resolve();editorPresence.changed();await settle();assert.equal(requests.at(-1).body.buffers[0].recovering,true);
''')

    def test_dirty_pagehide_keeps_registration_and_uses_keepalive_without_changing_text(self):
        self.js(r'''
const d=doc('candidate:1','候选');d.value='PRIVATE UNSAVED BODY';docs.set(d.id,d);editorPresence.ready();await settle();
emit('window','pagehide');await settle();assert.equal(requests.at(-1).body.closing,false);assert.equal(requests.at(-1).options.keepalive,true);
assert.equal(requests.at(-1).body.buffers[0].dirty,true);assert.equal(d.value,'PRIVATE UNSAVED BODY');
const count=requests.length;timers[0].callback();await settle();assert.equal(requests.length,count);
''')

    def test_clean_pagehide_closes_and_pageshow_reports_the_same_page_with_new_sequence(self):
        self.js(r'''
const d=doc('candidate:1','候选');docs.set(d.id,d);editorPresence.ready();await settle();emit('window','pagehide');await settle();
assert.equal(requests.at(-1).body.closing,true);const sequence=requests.at(-1).body.sequence;
d.value='new local input';emit('window','pageshow');await settle();assert.equal(requests.at(-1).body.closing,false);
assert.equal(requests.at(-1).body.page_id,'server-issued-page');assert.ok(requests.at(-1).body.sequence>sequence);assert.equal(requests.at(-1).body.buffers[0].dirty,true);
''')

    def test_old_success_or_failure_cannot_replace_a_newer_status_announcement(self):
        self.js(r'''
let release;handler=body=>new Promise(done=>{release=()=>done({ok:true,json:async()=>({ok:true,summary:{state:'old-clean'}})});});
const d=doc('candidate:1','候选');docs.set(d.id,d);editorPresence.ready();await settle();
handler=async()=>({ok:true,json:async()=>({ok:true,summary:{state:'pending'}})});d.value='changed';editorPresence.changed();await settle();
const count=announcements.length;assert.equal(announcements.at(-1).detail.summary.state,'pending');release();await settle();
assert.equal(announcements.length,count);assert.equal(announcements.at(-1).detail.summary.state,'pending');
''')

    def test_failed_transport_is_unknown_and_can_retry_without_editing_or_reloading(self):
        self.js(r'''
handler=async()=>{throw Error('offline');};editorPresence.ready();await settle();assert.equal(announcements.at(-1).detail.state,'unknown');
const before=requests.length;handler=async body=>({ok:true,json:async()=>({ok:true,summary:{sequence:body.sequence}})});
editorPresence.changed();await settle();assert.equal(requests.length,before+1);assert.equal(announcements.at(-1).detail.state,'reported');
''')

    def test_large_or_invalid_buffer_inventory_reports_unknown_instead_of_clean(self):
        self.js(r'''
for(let index=0;index<1001;index++){const d=doc('candidate:'+index,'候选');docs.set(d.id,d);}editorPresence.ready();await settle();
assert.equal(requests.at(-1).body.initialized,false);assert.deepEqual(requests.at(-1).body.buffers,[]);assert.equal(requests.at(-1).body.closing,false);
assert.equal(docs.size,1001);
''')

    def test_unconfirmed_readback_or_failed_new_version_is_pending_even_with_clean_text(self):
        self.js(r'''
const d=doc('candidate:1','候选');docs.set(d.id,d);d.pendingSaveReadback={id:'candidate:2'};editorPresence.ready();await settle();
assert.equal(requests.at(-1).body.buffers[0].dirty,false);assert.equal(requests.at(-1).body.buffers[0].needs_recovery,true);
d.pendingSaveReadback=null;d.saveError='回读未确认';d.failedSaveOptions={newVersion:true};editorPresence.changed();await settle();
assert.equal(editorPresence.capture().buffers[0].needs_recovery,true);
''')

    def test_static_page_without_server_configuration_does_not_claim_presence(self):
        self.js("editorPresence.ready();await settle();assert.equal(requests.length,0);assert.equal(timers.length,0);", config=False)

    def test_fresh_challenge_freezes_controls_before_ack_and_keeps_copy_available(self):
        self.js(r'''
const text={tagName:'TEXTAREA',readOnly:false,disabled:false},button={tagName:'BUTTON',disabled:false},locked={tagName:'TEXTAREA',readOnly:true,disabled:false};
const controls=[text,button,locked];document.querySelectorAll=()=>controls;
const d=doc('candidate:1','候选');docs.set(d.id,d);
const challenge={challenge_id:'fresh-challenge',nonce:'this-page-nonce',expires_in_ms:10000};
handler=async()=>({ok:true,json:async()=>({ok:true,stop_challenge:challenge,summary:{}})});
editorPresence.ready();await settle();assert.equal(editorPresence.isFrozen(),true);
assert.equal(text.readOnly,true);assert.equal(button.disabled,true);
assert.deepEqual(requests.at(-1).body.stop_ack,{challenge_id:'fresh-challenge',nonce:'this-page-nonce'});
let prevented=0;emit('document','keydown',{key:'s',metaKey:true,preventDefault:()=>prevented++,stopImmediatePropagation:()=>{}});assert.equal(prevented,1);
emit('document','keydown',{key:'c',metaKey:true,preventDefault:()=>prevented++,stopImmediatePropagation:()=>{}});assert.equal(prevented,1);
const dynamic={tagName:'TEXTAREA',readOnly:false,disabled:false};controls.push(dynamic);
await editorPresence.report(true);assert.equal(dynamic.readOnly,true);
assert.equal(d.value,'PRIVATE ORIGINAL BODY');
handler=async()=>({ok:true,json:async()=>({ok:true,stop_challenge:null,summary:{}})});
await editorPresence.report(true);assert.equal(editorPresence.isFrozen(),false);
assert.equal(text.readOnly,false);assert.equal(button.disabled,false);assert.equal(locked.readOnly,true);assert.equal(dynamic.readOnly,false);
''')

    def test_lost_transport_keeps_freeze_and_resynchronizes_before_resending_ack(self):
        self.js(r'''
const d=doc('candidate:1','候选');docs.set(d.id,d);
const challenge={challenge_id:'fresh-challenge',nonce:'this-page-nonce'};
handler=async()=>({ok:true,json:async()=>({ok:true,stop_challenge:challenge,summary:{}})});
editorPresence.ready();await settle();assert.equal(editorPresence.isFrozen(),true);
handler=async()=>{throw Error('offline');};await editorPresence.report(true);
assert.equal(editorPresence.isFrozen(),true);assert.equal(announcements.at(-1).detail.state,'unknown');
handler=async()=>({ok:true,json:async()=>({ok:true,stop_challenge:challenge,summary:{}})});
await editorPresence.report(true);assert.equal(requests.at(-1).body.stop_ack,undefined);
await editorPresence.report(true);assert.deepEqual(requests.at(-1).body.stop_ack,{challenge_id:'fresh-challenge',nonce:'this-page-nonce'});
assert.equal(d.value,'PRIVATE ORIGINAL BODY');
''')

    def test_bfcache_resume_stays_frozen_until_server_confirms_current_state(self):
        self.js(r'''
const text={tagName:'TEXTAREA',readOnly:false,disabled:false};document.querySelectorAll=()=>[text];
const d=doc('candidate:1','候选');docs.set(d.id,d);editorPresence.ready();await settle();
emit('window','pagehide');await settle();handler=async()=>{throw Error('suspended transport');};
emit('window','pageshow');await settle();assert.equal(editorPresence.isFrozen(),true);assert.equal(text.readOnly,true);
handler=async()=>({ok:true,json:async()=>({ok:true,stop_challenge:null,summary:{}})});
await editorPresence.report(true);assert.equal(editorPresence.isFrozen(),false);assert.equal(text.readOnly,false);
assert.equal(d.value,'PRIVATE ORIGINAL BODY');
''')


if __name__ == '__main__':
    unittest.main()
