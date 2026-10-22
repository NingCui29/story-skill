"""Reload and recovery round trips preserve the exact editor body."""
import json
import shutil
import subprocess
import threading
import unittest

import test_workbench as base
from test_workbench_editor_async_safety import safety_payload


class WorkbenchReloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.root = self.fixture.root
        self.original = self.w._editor_document(self.root, 'formal:1')
        self.formal_bytes = (self.root / self.original['path']).read_bytes()
        page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'reload-test').decode()
        self.script = page.split('<script>', 1)[1].split('</script>', 1)[0]

    def run_js(self, source, case, backend=None):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        program = 'const backend=' + json.dumps(backend or {}, ensure_ascii=False) + ';\n'
        program += source + '\n(async()=>{' + case + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node, '-'], input=program, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def recovery(self, text, key='reload-recovery-1234567890'):
        saved = self.w._editor_save(self.root, {**self.original, 'text': text, 'recovery_key': key}, recovery=True)
        opened = self.w._editor_document(self.root, saved['id'])
        self.assertEqual((self.root / saved['path']).read_bytes(), text.encode())
        return saved, opened

    def test_recovery_matching_heading_and_blank_lines_are_body_not_storage_prefix(self):
        prefix = self.original['prefix']
        for index, text in enumerate((prefix + '粘贴的正文。\n', prefix + prefix + '重复章头属于输入。\n',
                                      '\ufeff# ' + prefix.rstrip('\n') + '\r\n\r\n正文。\r\n')):
            with self.subTest(text=text):
                _, opened = self.recovery(text, 'heading-recovery-1234567890-' + str(index))
                self.assertEqual(opened['text'], text)
                self.assertEqual(opened['prefix'], prefix)
                self.assertEqual(opened['storage_format'], 'recovery-body-v1')
                candidate = self.w._editor_save(self.root, {**opened, 'text': text + '继续输入。'})
                self.assertEqual((self.root / candidate['path']).read_bytes().decode(), prefix + text + '继续输入。')
                self.assertEqual(self.w._editor_document(self.root, candidate['id'])['text'], text + '继续输入。')
        self.assertEqual((self.root / self.original['path']).read_bytes(), self.formal_bytes)

    def test_manual_candidate_in_recovery_folder_has_explicit_regular_file_format(self):
        relative = '.story/drafts/workbench/自动恢复/第1章 手工候选.md'
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('第1章 手工候选\n\n这是手工文件。')
        opened = self.w._editor_document(self.root, 'file:' + relative)
        self.assertEqual(opened['storage_format'], 'candidate-file-v1')
        self.assertEqual(opened['text'], '这是手工文件。')
        self.assertEqual(opened['prefix'], '第1章 手工候选\n\n')
        saved, material = self.recovery('材料的完整文字。', 'material-recovery-1234567890')
        metadata = self.root / (saved['path'] + '.meta.json')
        packet = json.loads(metadata.read_text())
        packet['content_kind'] = 'material'
        metadata.write_text(json.dumps(packet, ensure_ascii=False))
        material = self.w._editor_document(self.root, saved['id'])
        self.assertEqual(material['text'], '材料的完整文字。')
        self.assertEqual(material['content_kind'], 'material')
        self.assertEqual(material['storage_format'], 'recovery-body-v1')

    def reload_harness(self, native=False):
        script = self.script
        source = script[script.index('function dirty(d)'):script.index('function badge()')]
        source += script[script.index('async function recover'):script.index('async function saveDocument')]
        source += script[script.index("window.addEventListener('beforeunload'"):script.index("window.addEventListener('hashchange'")]
        opening = script[script.index('async function openDoc('):script.index('function pendingEdits(')]
        reload = script[script.index('async function reloadDocuments('):script.index("$('refresh').onclick")]
        harness = r'''
const assert=require('assert');
class Element{constructor(){this.value='';this.hidden=false;this.events={};this.textContent='';}
 addEventListener(type,handler){this.events[type]=handler;}focus(){}}
const nodes={},$=id=>nodes[id]||(nodes[id]=new Element());
const docs=new Map(),listeners={},window=globalThis;window.top=window;window.addEventListener=(type,handler)=>listeners[type]=handler;
const document={getElementById:id=>['save','text','current-book-root','list','reload-source','recover-reload'].includes(id)?$(id):null};
const location={href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/',hash:''};
let active='formal:1',openSequence=0,reloading=false,releaseOpen;
const crypto=require('crypto').webcrypto;
const notes=[],note=value=>notes.push(value),badge=()=>{},pendingEdits=()=>{},scheduleMetrics=()=>{},
 rememberView=()=>{},closePanels=()=>{},previewSearch=()=>{},displayTitle=d=>d.title,versionLabel=()=>'',catalog=async()=>{};
const show=d=>{active=d.id;$('text').value=d.value;};
const original={...backend.original,value:backend.original.text,editing:true,recoveryKey:'http-reload-test-1234567890'};docs.set(active,original);
const closeProbe=()=>{let prevented=false;listeners.beforeunload({preventDefault:()=>{prevented=true;}});return prevented;};
const call=async(action,data={})=>{
 const response=await fetch(backend.entry+'api/'+action,{method:'POST',headers:{'Content-Type':'application/json','Origin':backend.origin,'X-Story-Token':backend.token},body:JSON.stringify(data)});
 const value=await response.json();if(!response.ok)throw Error(value.message||'request failed');
 if(action==='open')return await new Promise(done=>{releaseOpen=()=>done(value);});return value;
};
const originalCall=call;
async function until(predicate){const end=Date.now()+5000;while(!predicate()){if(Date.now()>end)throw Error('probe timed out');await new Promise(done=>setTimeout(done,10));}}
'''
        installation = ''
        if native:
            source += opening + "async function reloadDocuments(){throw Error('legacy reload must be replaced');}\n"
            installation = '\nassert.equal(eval(' + json.dumps(safety_payload()) + ').applied,true);assert.strictEqual(call,originalCall);'
        else:
            source += opening + reload
        return harness + source, installation

    def test_reload_while_input_is_automatically_recovered_keeps_buffer_and_close_guard(self):
        server = self.w.editor_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        backend = {'original': self.original, 'entry': server.editor_url,
                   'origin': f'http://127.0.0.1:{server.server_port}',
                   'token': server.editor_url.rstrip('/').split('/')[-1]}
        for native in (False, True):
            with self.subTest(native=native):
                source, installation = self.reload_harness(native)
                receipt = self.run_js(source, installation + r'''
show(original);assert.equal(closeProbe(),false);
const running=reloadDocuments(false);await until(()=>typeof releaseOpen==='function');
const typed='重新读取等待期间的新输入，自动恢复也不能取消未保存标记。';$('text').value=typed;$('text').events.input();
assert.equal(closeProbe(),true);await until(()=>original.recovered===typed);
releaseOpen();await running;
assert.strictEqual(docs.get(active),original);assert.equal(original.value,typed);assert.equal($('text').value,typed);
assert.equal(closeProbe(),true);assert.equal(reloading,false);assert.ok(notes.at(-1).includes('已保留当前编辑'));
assert.strictEqual(call,originalCall);console.log(JSON.stringify({path:original.recoveryPath,text:typed}));
''', backend)
                opened = self.w._editor_document(self.root, 'file:' + receipt['path'])
                self.assertEqual(opened['text'], receipt['text'])
        self.assertEqual((self.root / self.original['path']).read_bytes(), self.formal_bytes)

    def test_reload_rejects_hidden_buffer_object_and_navigation_changes(self):
        script = self.script
        reload = script[script.index('async function reloadDocuments('):script.index("$('refresh').onclick")]
        harness = r'''
const assert=require('assert');let reloading=false,active='a',openSequence=0,release;
const docs=new Map(),notes=[],note=s=>notes.push(s),dirty=d=>d.value!==d.text;
const recover=async d=>{d.recovered=d.value;},clearTimeout=()=>{},crypto={randomUUID:()=> 'key'},show=()=>{},catalog=async()=>{};
const call=()=>new Promise(done=>{release=done;});
const a={id:'a',text:'old',value:'edited'},b={id:'b',text:'old b',value:'old b'};
docs.set('a',a);docs.set('b',b);
'''
        for mutation in ("b.value='隐藏文件的新输入';b.recovered=b.value;", "docs.set('b',{...b});",
                         "openSequence++;", "docs.delete('b');"):
            with self.subTest(mutation=mutation):
                self.run_js(harness + reload, "const pending=reloadDocuments(true);await Promise.resolve();" + mutation + r'''
release({id:'a',text:'disk'});await pending;
assert.strictEqual(docs.get('a'),a);assert.equal(a.value,'edited');assert.equal(reloading,false);
assert.ok(notes.at(-1).includes('保留'));
''')

    def compatibility_source(self):
        # The production navigation function is also the function shipped by
        # retained pages. Its original closures resolve the global declaration.
        opening = self.script[self.script.index('async function openDoc('):self.script.index('function pendingEdits(')]
        return r'''
const assert=require('assert'),vm=require('vm'),crypto=require('crypto').webcrypto;
function page(packet,options={}){
 const context=vm.createContext({URL,Map,TextEncoder,crypto,console,setTimeout,clearTimeout,Blob});
 vm.runInContext(`
 const response=${JSON.stringify(packet)},options=${JSON.stringify(options)},requests=[],notes=[],rendered=[],downloads=[],addresses=new Map();
 const OriginalURL=URL;URL=class extends OriginalURL{static createObjectURL(blob){const id='blob:'+addresses.size;addresses.set(id,blob);return id;}static revokeObjectURL(){}};
 const nodes={save:{onclick:()=>{}},text:{value:''},'current-book-root':{},list:{},'reload-source':{},'recover-reload':{},edit:{onclick:()=>{docs.get(active).editing=true;}},download:{onclick:()=>docs.get(active).value}};
 const document={getElementById:id=>nodes[id]||null,createElement:()=>({click(){downloads.push(addresses.get(this.href));}})},docs=new Map();
 let active=null,reloading=false,openSequence=0,releaseOld,currentCatalog={chapters:[],files:[]};
 const call=async(action,data)=>{requests.push({action,...data});if(action!=='open')throw Error('unexpected '+action);
  if(options.holdFirst&&requests.length===1)return await new Promise(done=>{releaseOld=()=>done(structuredClone(response));});
  return structuredClone(response);};
 const originalCall=call,dirty=d=>d&&d.editable&&d.value!==d.text,recover=async()=>{},badge=()=>{},
  note=value=>notes.push(value),rememberView=()=>{},catalog=async()=>{},displayTitle=d=>d.title,versionLabel=()=>'',closePanels=()=>{},previewSearch=()=>{};
 const show=d=>{active=d.id;nodes.text.value=d.value;rendered.push(d.value);};
 window=globalThis;window.top=window;location={href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/',hash:options.hash||''};
 function structuredClone(value){return JSON.parse(JSON.stringify(value));}
 `+__OPENING__+`
 async function reloadDocuments(){throw Error('legacy reload must be replaced');}
 // Bound before installation, exactly as the old catalog and recovery buttons.
 const oldDirectoryClick=()=>openDoc(response.id),oldRecoveryClick=()=>openDoc(response.id);
 `,context);
 return {read:code=>vm.runInContext(code,context),install:()=>vm.runInContext(__PAYLOAD__,context)};
}
'''.replace('__OPENING__', json.dumps(opening)).replace('__PAYLOAD__', json.dumps(safety_payload()))

    def test_native_legacy_const_call_recovery_validation_and_non_recovery_boundaries(self):
        text = self.original['prefix'] + '恢复文件中完整保留的正文。\n'
        _, full = self.recovery(text)
        legacy = {key: value for key, value in full.items() if key != 'storage_format'}
        legacy['text'] = text[len(self.original['prefix']):]
        normal = {**legacy, 'category': '候选与草稿', 'path': '.story/drafts/第1章 其他候选.md',
                  'id': 'file:.story/drafts/第1章 其他候选.md'}
        manual = {**legacy, 'path': '.story/drafts/workbench/自动恢复/第1章 手工候选.md',
                  'id': 'file:.story/drafts/workbench/自动恢复/第1章 手工候选.md'}
        self.run_js(self.compatibility_source(), r'''
for(const packet of [backend.legacy,backend.full,{...backend.legacy,text:backend.full.text}]){
 const p=page(packet),installation=p.install();assert.equal(installation.applied,true);assert.equal(await installation.ready,true);
 await p.read('oldDirectoryClick()');assert.equal(p.read('nodes.text.value'),backend.full.text);
 assert.equal(p.read('call===originalCall'),true);assert.equal(p.read('requests.length'),1);
 await p.read('oldRecoveryClick()');assert.equal(p.read('nodes.text.value'),backend.full.text);assert.equal(p.read('requests.length'),1);
}
for(const packet of [backend.normal,{...backend.manual,storage_format:'candidate-file-v1'},
                     {...backend.legacy,content_kind:'material',is_prose:false}]){
 const p=page(packet),installation=p.install();assert.equal(await installation.ready,true);
 await p.read('oldDirectoryClick()');assert.equal(p.read('nodes.text.value'),packet.text);
}
for(const packet of [backend.manual,{...backend.legacy,sha256:'f'.repeat(64)},
                     {...backend.legacy,source:'unverified-source'}, {...backend.legacy,metadata_sha256:null}]){
 const p=page(packet),installation=p.install();assert.equal(await installation.ready,true);
 await p.read('oldDirectoryClick()');assert.equal(p.read('docs.size'),0);
 assert.ok(p.read('notes.at(-1)').includes('暂不能完整核对'));assert.equal(p.read('call===originalCall'),true);
}
''', {'legacy': legacy, 'full': full, 'normal': normal, 'manual': manual})

    def test_native_initial_inflight_and_cached_recovery_are_checked_before_ready(self):
        text = self.original['prefix'] + '初始恢复页中的完整正文。\n'
        _, full = self.recovery(text)
        legacy = {key: value for key, value in full.items() if key != 'storage_format'}
        legacy['text'] = text[len(self.original['prefix']):]
        self.run_js(self.compatibility_source(), r'''
const p=page(backend.legacy,{holdFirst:true,hash:'#'+encodeURIComponent(backend.legacy.id)});
const oldOpen=p.read('oldDirectoryClick()');assert.equal(p.read('requests.length'),1);
const installation=p.install();assert.equal(await installation.ready,true);p.read('releaseOld()');await oldOpen;
assert.equal(p.read('requests.length'),2);assert.equal(p.read('nodes.text.value'),backend.full.text);
assert.equal(p.read('docs.get(active).text'),backend.full.text);assert.equal(p.read('call===originalCall'),true);
const clean=page(backend.legacy);
clean.read('const cached={...response,value:response.text,editing:true};docs.set(cached.id,cached);show(cached);');
assert.equal(await clean.install().ready,true);assert.equal(clean.read('docs.get(active)===cached'),true);
assert.equal(clean.read('cached.text'),backend.full.text);assert.equal(clean.read('cached.value'),backend.full.text);
assert.equal(clean.read('nodes.text.value'),backend.full.text);assert.equal(clean.read('requests.length'),0);
const edited=page(backend.legacy);
edited.read("const cached={...response,value:response.text+'独立的新输入',editing:true};docs.set(cached.id,cached);show(cached);");
assert.equal(await edited.install().ready,true);
assert.equal(edited.read('cached.value'),backend.legacy.text+'独立的新输入');assert.equal(edited.read('cached.text'),backend.legacy.text);
assert.equal(edited.read('nodes.text.value'),backend.legacy.text+'独立的新输入');assert.equal(edited.read('requests.length'),0);
assert.ok(edited.read('notes.at(-1)').includes('独立编辑'));assert.ok(edited.read('notes.at(-1)').includes('原恢复文件仍保留'));
await edited.read('nodes.download.onclick()');assert.equal(await edited.read('downloads.at(-1).text()'),backend.legacy.prefix+backend.legacy.text+'独立的新输入');
const unknown=page({...backend.legacy,sha256:'0'.repeat(64)});
unknown.read("const cached={...response,value:response.text},other={id:'formal:2',text:'other',value:'另稿独立编辑',editable:true};docs.set(other.id,other);docs.set(cached.id,cached);show(cached);");
assert.equal(await unknown.install().ready,true);assert.equal(unknown.read('cached.value'),backend.legacy.text);
assert.equal(unknown.read('requests.length'),0);assert.ok(unknown.read('cached.recoveryCompatibilityBlocked').includes('暂不能完整核对'));
assert.equal(unknown.read('nodes.text.hidden'),true);assert.equal(unknown.read('nodes.edit.disabled'),true);
assert.equal(await unknown.read('nodes.download.onclick()'),undefined);assert.equal(await unknown.read('nodes.save.onclick()'),false);
await unknown.read("openDoc('formal:2')");assert.equal(unknown.read('nodes.text.value'),'另稿独立编辑');
await unknown.read('nodes.download.onclick()');assert.equal(await unknown.read('downloads.at(-1).text()'),'另稿独立编辑');assert.equal(unknown.read('cached.value'),backend.legacy.text);
const unknownEdited=page({...backend.legacy,sha256:'0'.repeat(64)});
unknownEdited.read("const cached={...response,value:response.text+'必须能下载的输入'};docs.set(cached.id,cached);show(cached);");
assert.equal(await unknownEdited.install().ready,true);
await unknownEdited.read('nodes.download.onclick()');assert.equal(await unknownEdited.read('downloads.at(-1).text()'),backend.legacy.prefix+backend.legacy.text+'必须能下载的输入');
assert.ok(unknownEdited.read('notes.at(-1)').includes('原文暂不能完整核对'));
''', {'legacy': legacy, 'full': full})

    def test_native_comparison_reads_verified_legacy_body_and_rejects_blocked_cache(self):
        import test_workbench_management_editor as management
        fixture = management.EditorManagementJavascriptTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        text = self.original['prefix'] + '比较时也须完整核对的恢复文字。\n'
        _, full = self.recovery(text)
        legacy = {key: value for key, value in full.items() if key != 'storage_format'}
        legacy['text'] = text[len(self.original['prefix']):]
        source = fixture.tools_source().replace(
            "const listeners={},window={addEventListener:(name,callback)=>listeners[name]=callback};",
            "const listeners={},window=globalThis;window.top=window;window.addEventListener=(name,callback)=>listeners[name]=callback;"
        )
        source += r'''
const crypto=require('crypto').webcrypto,location={href:'http://127.0.0.1:49152/abcdefghijklmnopqrstuvwxyz012345/',hash:''},reloading=false;
document.getElementById=id=>['list','reload-source','recover-reload'].includes(id)?null:$(id);
const recover=async()=>{},badge=()=>{},rememberView=()=>{},show=()=>{},catalog=async()=>{};
'''
        case = r'''
const current=doc('file:current','当前稿',{value:'当前稿的输入',editing:true});active=current.id;
currentCatalog.files=[backend.legacy];fetched.set(backend.legacy.id,backend.legacy);
const installation=eval(__PAYLOAD__);assert.equal(await installation.ready,true);
let dialog=compareDialog(backend.legacy.id,current.id);await dialog.run.onclick();
assert.equal(drawn.length,1);assert.equal(drawn[0][0],backend.full.prefix+backend.full.text);
const bad=doc('file:blocked','旧缓存',{recoveryCompatibilityBlocked:'此恢复稿原文暂不能完整核对'});
currentCatalog.files.push(bad);dialog=compareDialog(bad.id,current.id);await dialog.run.onclick();
assert.equal(drawn.length,1);assert.ok(dialog.error.textContent.includes('暂不能完整核对'));
assert.equal(current.value,'当前稿的输入');
'''.replace('__PAYLOAD__', json.dumps(safety_payload()))
        self.run_js("const assert=require('assert');\n" + source, case, {'legacy': legacy, 'full': full})


if __name__ == '__main__':
    unittest.main()
