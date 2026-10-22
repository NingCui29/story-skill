"""The position-only client cannot steal navigation or send editor content."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchResumeScriptTests(unittest.TestCase):
    def js(self, case):
        node = shutil.which('node')
        if not node:
            self.fail('Node.js is required for resume async acceptance')
        source = base.story.workbench._editor_resume_script()
        harness = r'''
const assert=require('assert');
const timers=new Map();let timer=0;
const setTimeout=fn=>{timers.set(++timer,fn);return timer;},clearTimeout=id=>timers.delete(id);
const copy=value=>JSON.parse(JSON.stringify(value));
const state={version:1,book_id:'book-A',document_id:'file:创作约定.md',document_sha256:'a'.repeat(64),mode:'editing',
 cursor:{start:2,end:4,direction:'backward'},scroll:{main:30,editor:90},buffer_dirty:false,updated_at:'2026-10-20T12:00:00Z'};
const ready={ok:true,book_id:'book-A',status:'ready',state,state_sha256:'b'.repeat(64)};
const calls=[],shown=[],restored=[],notes=[];let hash='',identity='book-A';
const options={bookId:()=>identity,explicitHash:()=>hash,onStatus:packet=>notes.push(packet),
 call:async(action,data)=>{calls.push({action,data:copy(data)});if(action==='resume-state')return copy(ready);return {ok:true,book_id:identity,status:'saved',state_sha256:'c'.repeat(64)};},
 open:async(value,guard)=>{if(!guard())return false;shown.push(value.document_id);hash='#'+encodeURIComponent(value.document_id);return true;},
 restore:async(value,guard)=>{if(guard())restored.push(value);}};
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
'''
        result = subprocess.run([node, '-'], input=harness + source + '\n(async()=>{\n' + case +
                                 '\n})().catch(error=>{console.error(error);process.exitCode=1;});', encoding='utf-8',
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_load_and_apply_preserve_original_mode_positions_and_own_hash(self):
        self.js(r'''
const resume=createEditorResume(options),packet=await resume.load();assert.equal(await resume.apply(packet),true);
assert.deepEqual(shown,[state.document_id]);assert.equal(restored[0].mode,'editing');
assert.deepEqual(restored[0].cursor,state.cursor);assert.deepEqual(restored[0].scroll,state.scroll);
''')

    def test_initial_hash_user_interaction_before_load_and_interaction_during_load_all_win(self):
        self.js(r'''
hash='#formal%3A2';let resume=createEditorResume(options);assert.equal(await resume.apply(await resume.load()),false);
hash='';resume=createEditorResume(options);resume.markUserInteraction();assert.equal(await resume.apply(await resume.load()),false);
let release;resume=createEditorResume({...options,call:()=>new Promise(done=>{release=done;})});
const loading=resume.load();resume.markUserInteraction();release(copy(ready));assert.equal(await resume.apply(await loading),false);
assert.deepEqual(shown,[]);assert.deepEqual(restored,[]);
''')

    def test_same_document_hash_resumes_but_other_malformed_or_later_targets_do_not(self):
        self.js(r'''
hash='#'+encodeURIComponent(state.document_id);let resume=createEditorResume(options);
assert.equal(await resume.apply(await resume.load()),true);
assert.deepEqual(restored[0].cursor,state.cursor);assert.deepEqual(restored[0].scroll,state.scroll);
shown.length=0;restored.length=0;
for(const initial of ['#formal%3A2','#%E0%A4%A']){
 hash=initial;resume=createEditorResume(options);
 assert.equal(await resume.apply(await resume.load()),false);
}
hash='#'+encodeURIComponent(state.document_id);resume=createEditorResume(options);
const packet=await resume.load();hash='#formal%3A2';
assert.equal(await resume.apply(packet),false);
assert.deepEqual(shown,[]);assert.deepEqual(restored,[]);
''')

    def test_matching_legacy_chapter_hash_resumes_only_its_own_chapter(self):
        self.js(r'''
state.document_id='formal:1';hash='#chapter-1';let resume=createEditorResume(options);
assert.equal(await resume.apply(await resume.load()),true);assert.deepEqual(shown,['formal:1']);
shown.length=0;restored.length=0;hash='#chapter-2';resume=createEditorResume(options);
assert.equal(await resume.apply(await resume.load()),false);assert.deepEqual(shown,[]);
''')

    def test_interaction_during_delayed_open_prevents_target_show_and_restore(self):
        self.js(r'''
let release;const resume=createEditorResume({...options,open:async(value,guard)=>{
 await new Promise(done=>{release=done;});if(!guard())return false;shown.push(value.document_id);return true;
}});const applying=resume.apply(await resume.load());await tick();resume.markUserInteraction();release();
assert.equal(await applying,false);assert.deepEqual(shown,[]);assert.deepEqual(restored,[]);
''')

    def test_manual_continue_requires_intent_and_still_yields_to_newer_interaction(self):
        self.js(r'''
hash='#formal%3A2';const resume=createEditorResume(options);const packet=await resume.load();resume.markUserInteraction();
assert.equal(await resume.apply(packet),false);assert.equal(await resume.apply(packet,{manual:true}),true);
assert.deepEqual(shown,[state.document_id]);
''')

    def test_only_whitelisted_position_fields_are_sent_with_loaded_cas(self):
        self.js(r'''
const resume=createEditorResume(options);resume.schedule({...state,text:'正文不可上传',token:'secret',root:'/private',source:'arbitrary'});
assert.equal(await resume.flush(),true);const saved=calls.find(call=>call.action==='resume-save');
assert.deepEqual(Object.keys(saved.data).sort(),['book_id','document_id','document_sha256','mode','cursor','scroll','buffer_dirty','expected_state_sha256'].sort());
assert.equal(saved.data.expected_state_sha256,'b'.repeat(64));assert.equal(saved.data.book_id,'book-A');
assert.ok(!JSON.stringify(saved.data).includes('secret'));assert.ok(!JSON.stringify(saved.data).includes('正文不可上传'));
''')

    def test_inflight_save_retains_latest_position_and_uses_returned_cas(self):
        self.js(r'''
let release,saveCount=0;const resume=createEditorResume({...options,call:async(action,data)=>{
 calls.push({action,data:copy(data)});if(action==='resume-state')return copy(ready);
 if(++saveCount===1)await new Promise(done=>{release=done;});return {ok:true,book_id:identity,state_sha256:(saveCount===1?'c':'d').repeat(64)};
}});await resume.load();resume.schedule(state);const saving=resume.flush();await tick();
resume.schedule({...state,scroll:{main:999,editor:40}});release();assert.equal(await saving,true);
const saves=calls.filter(call=>call.action==='resume-save');assert.equal(saves.length,2);
assert.equal(saves[1].data.scroll.main,999);assert.equal(saves[1].data.expected_state_sha256,'c'.repeat(64));
''')

    def test_conflicts_do_not_silently_overwrite_or_retry_other_window(self):
        self.js(r'''
const resume=createEditorResume({...options,call:async(action,data)=>{
 calls.push({action,data});if(action==='resume-state')return copy(ready);throw Object.assign(Error('另一页面更新'),{code:'workbench_resume_conflict'});
}});resume.schedule(state);assert.equal(await resume.flush(),false);assert.equal(resume.conflicted,true);
assert.equal(resume.schedule({...state,scroll:{main:400,editor:0}}),false);assert.equal(await resume.flush(),false);
assert.equal(calls.filter(call=>call.action==='resume-save').length,1);assert.ok(notes.at(-1).conflict);
''')

    def test_older_backend_and_missing_target_do_not_interfere_with_editor(self):
        self.js(r'''
const resume=createEditorResume({...options,call:async()=>{throw Object.assign(Error('not found'),{status:404});}});
assert.equal((await resume.load()).status,'unsupported');assert.equal(resume.disabled,true);assert.equal(resume.schedule(state),false);
const current=createEditorResume({...options,call:async()=>({...copy(ready),status:'changed'})});
assert.equal(await current.apply(await current.load()),false);assert.deepEqual(shown,[]);assert.deepEqual(restored,[]);
''')

    def test_restore_adapter_can_refuse_changed_hash_or_existing_dirty_buffer(self):
        self.js(r'''
const resume=createEditorResume({...options,restore:async()=>false});
assert.equal(await resume.apply(await resume.load()),false);
assert.deepEqual(restored,[]);
''')


if __name__ == '__main__':
    unittest.main()
