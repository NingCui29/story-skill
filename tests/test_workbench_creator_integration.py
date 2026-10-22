"""The real creator adapters preserve navigation, buffers and editor positions."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base
from test_workbench_editor_async_safety import safety_payload


class WorkbenchCreatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        w = base.story.workbench
        self.packet = w.snapshot(self.fixture.book)
        self.page = w._editor_page(self.packet, {}, 'creator-integration').decode()
        self.script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.backend = {'book_id': self.packet['book']['id'],
                        'documents': [w._editor_document(self.fixture.root, 'formal:1'),
                                      w._editor_document(self.fixture.root, 'formal:2')],
                        'catalog': w._editor_catalog(self.fixture.root)}

    def js(self, case, setup='', native=False):
        node = shutil.which('node')
        if not node:
            self.fail('Node.js is required for creator integration acceptance')
        script = self.script
        source = script[script.index('function documentIdentity('):script.index('function documentLocation(')]
        source += script[script.index('let searchPreview=false'):script.index('function searchRanges(')]
        source += script[script.index('function leaveComparison('):script.index('function stepDiff(')]
        source += script[script.index('function dirty('):script.index('function item(')]
        source += script[script.index('async function catalog('):script.index("$('save').onclick=")]
        source += script[script.index("$('compare').onclick="):script.index('function downloadDocument(')]
        source += script[script.index('async function reloadDocuments('):script.index("$('refresh').onclick=")]
        source += base.story.workbench._editor_tools_script()
        source += base.story.workbench._editor_resume_script()
        # Take the already substituted adapter from the actual served page.
        source += script[script.index('let creatorResume='):script.index('setupWorkspace();setupCreatorWorkspace();catalog();')]
        harness = r'''
const assert=require('assert'),copy=value=>JSON.parse(JSON.stringify(value));
class E{
 constructor(){this.hidden=false;this.open=false;this.value='';this.textContent='';this.dataset={};this.events={};this.children=[];this.scrollTop=0;this.selectionStart=0;this.selectionEnd=0;this.selectionDirection='none';this.parentElement={scrollTop:0};}
 addEventListener(name,handler){(this.events[name]??=[]).push(handler);}
 dispatch(name,event={}){for(const handler of this.events[name]||[])handler(event);}
 setAttribute(name,value){this[name]=value;}
 setSelectionRange(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;}
 querySelectorAll(){return [];}replaceChildren(...children){this.children=children;}append(...children){this.children.push(...children);}focus(){}
 showModal(){this.open=true;}close(){this.open=false;this.dispatch('close');}remove(){}scrollIntoView(){}
}
const nodes={},$=id=>nodes[id]??(nodes[id]=new E()),main=new E(),documentEvents={},windowEvents={};
const classes=new Set(),body=new E();body.classList={add:value=>classes.add(value),remove:value=>classes.delete(value),contains:value=>classes.has(value)};
const document={hidden:false,body,querySelector:()=>main,querySelectorAll:()=>[],createElement:()=>new E(),
 addEventListener(name,handler){(documentEvents[name]??=[]).push(handler);}};
const window={addEventListener(name,handler){(windowEvents[name]??=[]).push(handler);}};
const dispatch=(name,event={})=>{for(const handler of documentEvents[name]||[])handler(event);};
const crypto=require('crypto').webcrypto,timers=new Map();let timer=0;
const setTimeout=(fn,delay)=>{timers.set(++timer,{fn,delay});return timer;},clearTimeout=id=>timers.delete(id);
const docs=new Map(),serverDocs=new Map(backend.documents.map(d=>[d.id,copy(d)])),calls=[],notes=[],shown=[],drawn=[];
let active=null,openSequence=0,offset=0,loading=false,reloadPending=false,pendingFocus=false,queryTimer,reloading=false;
let compareSequence=0,diffTargets=[];
let currentCatalog=null,viewMode='chapters',releaseOpen=null,releaseResume=null,holdOpen=null,holdResume=false,failResumeSave=false;
const LIMIT=100,location={hash:''};
const target=backend.documents[0],other=backend.documents[1];
let resumeState={version:1,book_id:backend.book_id,document_id:target.id,document_sha256:target.sha256,
 mode:'editing',cursor:{start:3,end:8,direction:'backward'},scroll:{main:123,editor:45},buffer_dirty:false,updated_at:'2026-10-20T12:00:00Z'};
const ready=()=>({ok:true,status:'ready',book_id:backend.book_id,state:copy(resumeState),state_sha256:'a'.repeat(64)});
const note=value=>notes.push(value),displayTitle=d=>d.title||d.id,versionLabel=()=>'',documentLocation=d=>d.path||d.id;
const chapterNumber=d=>d.context?.chapter||d.chapter||null;
const closePanels=()=>{},previewSearch=()=>{},readingView=()=>{},updateChapterPicker=()=>{},scheduleMetrics=()=>{},locateActive=()=>{},drawDiff=()=>{};
const drawCatalog=c=>{currentCatalog=c;drawn.push(c);};
let presenceChanged=0,presenceReady=0;
const editorPresence={changed(){presenceChanged++;},ready(){presenceReady++;}};
async function call(action,data={}){
 calls.push({action,data:copy(data)});
 if(action==='resume-state'){if(holdResume)await new Promise(done=>{releaseResume=done;});return ready();}
 if(action==='resume-save'){
  if(failResumeSave)throw Object.assign(Error('另一个窗口已更新位置'),{code:'workbench_resume_conflict'});
  resumeState={...copy(data),version:1};return {ok:true,status:'saved',book_id:backend.book_id,state:copy(resumeState),state_sha256:'b'.repeat(64)};
 }
 if(action==='catalog')return copy(backend.catalog);
 if(action==='open'){if(holdOpen===data.id)await new Promise(done=>{releaseOpen=done;});return copy(serverDocs.get(data.id));}
 if(action==='recover')return {path:'isolated-recovery.txt',recovery_sha256:'c'.repeat(64),recovery_metadata_sha256:'d'.repeat(64)};
 if(action==='save'){
  const id='file:.story/drafts/workbench/创作者集成_候选_0123456789ab.md';
  serverDocs.set(id,{...copy(serverDocs.get(data.id)),id,path:id.slice(5),kind:'candidate',text:data.text,sha256:'e'.repeat(64),source:data.id});
  return {ok:true,id};
 }
 throw Error('Unexpected creator action: '+action);
}
async function tick(){for(let i=0;i<50;i++)await Promise.resolve();}
'''
        if native:
            harness = harness.replace(
                "const window={addEventListener(name,handler){(windowEvents[name]??=[]).push(handler);}};",
                "const window=globalThis;window.top=window;window.addEventListener=(name,handler)=>{(windowEvents[name]??=[]).push(handler);};"
            ).replace(
                "const document={hidden:false,body,querySelector:",
                "const document={hidden:false,body,getElementById:id=>$(id),querySelector:"
            ).replace(
                "const LIMIT=100,location={hash:''};",
                "const LIMIT=100,location={hash:'',href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/'};"
            )
            source += "\nasync function installNative(){const result=eval(" + json.dumps(safety_payload()) + ");assert.equal(result.applied,true);assert.equal(await result.ready,true);return result;}\n"
        program = 'const backend=' + json.dumps(self.backend, ensure_ascii=False) + ';\n' + harness + source
        program += '\n(async()=>{\n' + setup + '\nsetupCreatorWorkspace();\n' + case
        program += '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node, '-'], input=program, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_catalog_restores_actual_document_mode_selection_scroll_and_book_identity(self):
        self.js(r'''
await catalog();assert.equal(active,target.id);assert.equal(creatorResumeApplied,true);
assert.equal(docs.get(active).editing,true);assert.equal($('text').hidden,false);
assert.equal($('text').selectionStart,3);assert.equal($('text').selectionEnd,8);assert.equal($('text').selectionDirection,'backward');
assert.equal($('text').scrollTop,45);assert.equal(main.scrollTop,123);assert.equal(presenceReady,1);
assert.equal(location.hash,encodeURIComponent(target.id));assert.ok(drawn.length>0);
creatorResume.schedule();assert.equal(await creatorResume.flush(),true);
const payload=calls.find(row=>row.action==='resume-save').data;
assert.equal(payload.book_id,backend.book_id);assert.equal(payload.document_id,target.id);assert.equal(payload.mode,'editing');
assert.deepEqual(payload.cursor,{start:3,end:8,direction:'backward'});assert.deepEqual(payload.scroll,{main:123,editor:45});
assert.ok(!Object.hasOwn(payload,'text'));assert.ok(!Object.hasOwn(payload,'value'));
''')

    def test_initial_hash_wins_over_resume_and_does_not_apply_its_positions(self):
        self.js(r'''
await catalog();assert.equal(active,other.id);assert.equal(docs.get(active).editing,false);
assert.equal(main.scrollTop,0);assert.equal($('text').selectionStart,0);
assert.deepEqual(calls.filter(row=>row.action==='open').map(row=>row.data.id),[other.id]);
''', setup="location.hash='#'+encodeURIComponent(other.id);")

    def test_refresh_with_same_document_hash_preserves_editing_selection_and_scroll(self):
        for initial in ("'#'+encodeURIComponent(target.id)", "'#chapter-1'"):
            with self.subTest(initial=initial):
                self.js(r'''
await catalog();assert.equal(active,target.id);assert.equal(docs.get(active).editing,true);
assert.equal($('text').selectionStart,3);assert.equal($('text').selectionEnd,8);
assert.equal($('text').selectionDirection,'backward');
assert.equal(main.scrollTop,123);assert.equal($('text').scrollTop,45);
creatorResume.schedule();await creatorResume.flush();
assert.equal(resumeState.mode,'editing');assert.equal(resumeState.cursor.start,3);
assert.deepEqual(resumeState.scroll,{main:123,editor:45});
''', setup='location.hash='+initial+';')

    def test_refreshed_hash_still_yields_to_navigation_during_delayed_restore(self):
        self.js(r'''
const initializing=catalog();await tick();assert.equal(typeof releaseOpen,'function');
dispatch('pointerdown',{isTrusted:true});await openDoc(other.id);
releaseOpen();await initializing;await tick();
assert.equal(active,other.id);assert.equal(docs.has(target.id),false);
assert.equal(docs.get(active).editing,false);
''', setup="location.hash='#'+encodeURIComponent(target.id);holdOpen=target.id;")

    def test_native_safety_does_not_take_initial_hash_away_from_delayed_resume(self):
        for fragment in ("''", "'#'+encodeURIComponent(target.id)", "'#chapter-1'"):
            with self.subTest(fragment=fragment):
                self.js(r'''
const initializing=catalog();await tick();assert.equal(typeof releaseResume,'function');
const sequence=openSequence;await installNative();
assert.equal(active,null);assert.equal(openSequence,sequence);
releaseResume();await initializing;await tick();
assert.equal(active,target.id);assert.equal(docs.get(active).editing,true);
assert.equal($('text').selectionStart,3);assert.equal($('text').selectionEnd,8);
assert.equal($('text').selectionDirection,'backward');assert.equal(main.scrollTop,123);
assert.equal($('text').scrollTop,45);
''', setup='holdResume=true;location.hash='+fragment+';', native=True)

    def test_native_safety_does_not_cancel_current_page_inflight_resume_open(self):
        self.js(r'''
const initializing=catalog();await tick();assert.equal(typeof releaseOpen,'function');
const sequence=openSequence;await installNative();assert.equal(openSequence,sequence);assert.equal(active,null);
releaseOpen();await initializing;await tick();
assert.equal(active,target.id);assert.equal(docs.get(active).editing,true);
assert.equal($('text').selectionStart,3);assert.equal($('text').selectionEnd,8);
assert.equal(main.scrollTop,123);assert.equal($('text').scrollTop,45);
''', setup="location.hash='#'+encodeURIComponent(target.id);holdOpen=target.id;", native=True)

    def test_native_guarded_opener_forwards_hash_check_result_and_late_interaction_guard(self):
        self.js(r'''
await creatorResumeLoading;await installNative();
assert.equal(await openDoc(target.id,'',{expectedSha:'f'.repeat(64)}),false);
assert.equal(active,null);assert.equal(docs.has(target.id),false);
let permitted=true;holdOpen=target.id;
const opening=openDoc(target.id,'',{expectedSha:target.sha256,guard:()=>permitted});
await tick();assert.equal(typeof releaseOpen,'function');permitted=false;releaseOpen();
assert.equal(await opening,false);assert.equal(active,null);assert.equal(docs.has(target.id),false);
holdOpen=null;assert.equal(await openDoc(other.id),true);assert.equal(active,other.id);
''', native=True)

    def test_native_resume_still_yields_to_explicit_other_target(self):
        self.js(r'''
await installNative();await catalog();await tick();
assert.equal(active,other.id);assert.equal(docs.get(active).editing,false);
assert.equal($('text').selectionStart,0);assert.equal(main.scrollTop,0);
assert.equal(docs.has(target.id),false);
''', setup="location.hash='#'+encodeURIComponent(other.id);", native=True)

    def test_comparison_close_persists_document_position_instead_of_diff_scroll(self):
        for mode in ('reading', 'editing'):
            for compare in ('formal', 'two-documents'):
                with self.subTest(mode=mode, compare=compare):
                    action = ("await $('compare').onclick();" if compare == 'formal' else r'''
$('compare-choose').onclick();const dialog=document.body.children.at(-1),content=dialog.children[1];
content.children[0].children[0].value=other.id;content.children[1].children[0].value=target.id;
await content.children[2].onclick();
''')
                    self.js(r'''
await catalog();const original=docs.get(active),expected=creatorSnapshot();
''' + action + r'''
assert.ok(document.body.classList.contains('comparing'));assert.equal(main.scrollTop,0);
main.scrollTop=700;main.dispatch('scroll');await creatorResume.flush();
assert.equal(resumeState.scroll.main,123);assert.equal(original.positions[resumeState.mode].main,123);
for(const handler of windowEvents.pagehide||[])handler();await tick();
assert.deepEqual(resumeState.scroll,expected.scroll);assert.deepEqual(resumeState.cursor,expected.cursor);
assert.equal(resumeState.mode,expected.mode);
leaveComparison();assert.equal(main.scrollTop,123);assert.equal($('text').scrollTop,expected.scroll.editor);
if(original.editing)assert.equal($('text').selectionDirection,'backward');
''', setup="resumeState.mode='"+mode+"';")

    def test_save_from_comparison_keeps_document_position_for_the_new_candidate(self):
        self.js(r'''
await catalog();const original=docs.get(active);original.value+='新输入。';
await $('compare').onclick();main.scrollTop=700;
assert.equal(await saveDocument(original),true);assert.notEqual(active,original.id);
assert.equal(docs.get(active).editing,true);assert.equal(main.scrollTop,123);
assert.equal($('text').scrollTop,45);assert.equal($('text').selectionDirection,'backward');
creatorResume.schedule();await creatorResume.flush();assert.equal(resumeState.document_id,active);
assert.deepEqual(resumeState.scroll,{main:123,editor:45});assert.equal(resumeState.cursor.start,3);
''')

    def test_search_preview_still_cannot_overwrite_document_resume_position(self):
        self.js(r'''
await catalog();creatorResume.schedule();await creatorResume.flush();const expected=copy(resumeState);
searchPreview=true;main.scrollTop=700;
assert.equal(creatorSnapshot(),null);assert.equal(creatorResume.schedule(),false);
for(const handler of windowEvents.pagehide||[])handler();await tick();
assert.deepEqual(resumeState,expected);
''')

    def test_reading_resume_restores_reading_scroll_without_turning_on_editing(self):
        self.js(r'''
await catalog();assert.equal(active,target.id);assert.equal(docs.get(active).editing,false);
assert.equal($('text').hidden,true);assert.equal($('prose').hidden,false);assert.equal(main.scrollTop,123);
assert.deepEqual(docs.get(active).positions.reading,{start:3,end:8,direction:'backward',main:123,editor:45});
creatorResume.schedule();await creatorResume.flush();assert.equal(resumeState.mode,'reading');assert.equal(resumeState.scroll.main,123);
''', setup="resumeState.mode='reading';")

    def test_readonly_document_never_enters_editing_when_saved_mode_requested_it(self):
        self.js(r'''
await catalog();assert.equal(active,target.id);assert.equal(docs.get(active).editing,false);
assert.equal($('text').hidden,true);assert.equal($('edit').disabled,true);assert.equal($('save').disabled,true);
assert.equal(main.scrollTop,123);
''', setup='serverDocs.get(target.id).editable=false;')

    def test_explicit_chapter_hash_wins_over_resume(self):
        self.js("await catalog();assert.equal(active,other.id);assert.equal(docs.get(active).editing,false);",
                setup="location.hash='#chapter-2';")

    def test_delayed_resume_open_cannot_replace_later_navigation(self):
        self.js(r'''
const initializing=catalog();await tick();assert.equal(typeof releaseOpen,'function');
dispatch('pointerdown',{isTrusted:true});await openDoc(other.id);assert.equal(active,other.id);
releaseOpen();await initializing;await tick();assert.equal(active,other.id);assert.equal(docs.has(target.id),false);
assert.equal(location.hash,encodeURIComponent(other.id));assert.equal($('text').hidden,true);
''', setup="holdOpen=target.id;")

    def test_delayed_resume_load_cannot_replace_user_open(self):
        self.js(r'''
const initializing=catalog();await tick();assert.equal(typeof releaseResume,'function');
dispatch('keydown',{isTrusted:true});await openDoc(other.id);releaseResume();await initializing;
assert.equal(active,other.id);assert.equal(docs.has(target.id),false);assert.equal(docs.get(active).editing,false);
''', setup='holdResume=true;')

    def test_cached_dirty_text_and_mode_are_never_overwritten_by_resume(self):
        self.js(r'''
const unsaved=docs.get(target.id);await catalog();assert.strictEqual(docs.get(target.id),unsaved);
assert.equal(unsaved.value,'页面中已存在的未保存文字');assert.equal(unsaved.editing,true);
assert.equal(calls.some(row=>row.action==='open'&&row.data.id===target.id),false);
assert.deepEqual(unsaved.positions.editing,{start:21,end:21,direction:'none',main:300,editor:80});
await openDoc(target.id);
assert.equal($('text').value,unsaved.value);assert.equal($('text').selectionStart,21);assert.equal(main.scrollTop,300);
assert.equal(dirty(unsaved),true);
''', setup=r'''
docs.set(target.id,{...copy(target),value:'页面中已存在的未保存文字',editing:true,
 positions:{editing:{start:21,end:21,direction:'none',main:300,editor:80}}});
''')

    def test_changed_sha_refuses_resume_but_normal_default_document_remains_available(self):
        self.js(r'''
await catalog();assert.equal(active,backend.catalog.chapters[0].id);assert.equal(docs.get(active).editing,false);
assert.equal(main.scrollTop,0);assert.equal($('text').selectionStart,0);
assert.equal(calls.filter(row=>row.action==='open').length,2);
''', setup="serverDocs.get(target.id).sha256='f'.repeat(64);")

    def test_dirty_previous_session_is_a_persistent_recovery_reminder_without_dirtying_disk_text(self):
        self.js(r'''
await catalog();const current=docs.get(active);assert.equal(current.resumeNeedsRecovery,true);assert.equal(dirty(current),false);
assert.equal($('resume-status').hidden,false);assert.ok($('resume-status').textContent.includes('恢复中心'));
creatorResume.schedule();await creatorResume.flush();assert.equal(resumeState.buffer_dirty,true);
assert.equal($('resume-status').hidden,false);assert.ok($('resume-status').textContent.includes('恢复中心'));
''', setup='resumeState.buffer_dirty=true;')

    def test_saving_a_new_candidate_updates_resume_identity_and_clears_old_recovery_reminder(self):
        self.js(r'''
await catalog();const original=docs.get(active);original.value+='本页新输入。';
assert.equal(await saveDocument(original),true);const fresh=docs.get(active);assert.notEqual(fresh.id,original.id);
assert.equal(fresh.value,fresh.text);assert.equal(!!fresh.resumeNeedsRecovery,false);
creatorResume.schedule();assert.equal(await creatorResume.flush(),true);
assert.equal(resumeState.document_id,fresh.id);assert.equal(resumeState.document_sha256,fresh.sha256);assert.equal(resumeState.buffer_dirty,false);
assert.equal($('resume-status').hidden,true);assert.equal(calls.find(row=>row.action==='save').data.save_mode,'working');
''', setup='resumeState.buffer_dirty=true;')

    def test_explicit_remember_reloads_conflicted_position_before_saving_current_position(self):
        self.js(r'''
await catalog();failResumeSave=true;creatorResume.schedule();assert.equal(await creatorResume.flush(),false);assert.equal(creatorResume.conflicted,true);
failResumeSave=false;$('text').setSelectionRange(10,12,'forward');main.scrollTop=500;
await $('resume-remember').onclick();assert.equal(creatorResume.conflicted,false);assert.equal(resumeState.scroll.main,500);
assert.deepEqual(resumeState.cursor,{start:10,end:12,direction:'forward'});assert.ok(notes.at(-1).includes('已记住'));
''')


if __name__ == '__main__':
    unittest.main()
