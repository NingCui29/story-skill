"""Native policy checks run without launching windows or touching book services."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_desktop_editor_appearance import HARNESS as APPEARANCE_HARNESS


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'desktop/macos/StoryWorkbench.swift'


class DesktopNativeTests(unittest.TestCase):
    def foundation(self, checks):
        if sys.platform != 'darwin' or not shutil.which('xcrun'):
            self.skipTest('macOS Swift compiler required')
        source = SOURCE.read_text()
        policy = source[source.index('enum WorkbenchURL {'):source.index('\n@MainActor')]
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            swift = folder / 'policy.swift'
            executable = folder / 'policy'
            swift.write_text('import Foundation\n' + policy + checks)
            compiled = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(swift), '-o', str(executable)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            return ran.stdout

    def test_navigation_policy_accepts_only_explicit_local_workbench_pages(self):
        checks = r'''
let shelf=URL(string:"http://127.0.0.1:8765/")!
let editor=URL(string:"http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/")!
precondition(WorkbenchURL.isShelf(shelf))
precondition(WorkbenchURL.isEditor(editor))
precondition(WorkbenchURL.samePage(editor,URL(string:editor.absoluteString+"#formal%3A1")!))
for address in ["http://localhost:8765/", "http://[::1]:8765/", "http://127.0.0.1/", "http://127.0.0.1:0/", "http://127.0.0.1:65536/", "http://user:secret@127.0.0.1:8765/", "https://127.0.0.1:8765/", "file:///tmp/book.html", "http://127.0.0.1:8765/?url=evil"] {
    let url=URL(string:address)!
    precondition(!WorkbenchURL.isShelf(url) && !WorkbenchURL.isEditor(url),address)
}
for address in ["http://127.0.0.1:8765/api/", "http://127.0.0.1:8765/short/", "http://127.0.0.1:8765/%61bcdefghijklmnopqrstuvwxyz012345/", "http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/extra"] {
    precondition(!WorkbenchURL.isEditor(URL(string:address)!))
}
precondition(!WorkbenchURL.samePage(editor,shelf))
precondition(!WorkbenchURL.samePage(editor,URL(string:"http://127.0.0.1:11111/abcdefghijklmnopqrstuvwxyz012345/")!))
precondition(WorkbenchURL.isExternalLink(URL(string:"https://example.com/help")!))
precondition(WorkbenchURL.isExternalLink(URL(string:"mailto:writer@example.com")!))
precondition(!WorkbenchURL.isExternalLink(URL(string:"file:///tmp/book.html")!))
precondition(!WorkbenchURL.isExternalLink(URL(string:"javascript:alert(1)")!))
precondition(!WorkbenchURL.isExternalLink(URL(string:"http://localhost:8080/")!))
print("navigation policy passed")
'''
        self.foundation(checks)

    def test_editor_self_test_options_fail_closed_before_application_start(self):
        self.foundation(r'''
let editor="http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/"
let normal=try WorkbenchLaunchOptions.parse(["app"])
precondition(normal.report == nil && normal.editorURL == nil)
let shelf=try WorkbenchLaunchOptions.parse(["app","--self-test","/tmp/report.json"])
precondition(shelf.report?.path == "/tmp/report.json" && shelf.editorURL == nil)
let selected=try WorkbenchLaunchOptions.parse(["app","--self-test-editor-url",editor,"--self-test","/tmp/report.json"])
precondition(selected.editorURL?.absoluteString == editor && selected.report?.path == "/tmp/report.json")
var invalid=[["app","--self-test"],["app","--self-test",""],["app","--self-test-editor-url",editor],["app","--self-test","/tmp/a","--self-test","/tmp/b"],["app","--self-test","/tmp/a","--self-test-editor-url"],["app","--self-test-editor-url","--self-test","/tmp/a"]]
for address in ["http://127.0.0.1:12345/", "http://localhost:12345/abcdefghijklmnopqrstuvwxyz012345/", "http://127.0.0.1:12345/short/", "http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/?write=yes", "file:///tmp/book.html", "https://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/"] {
    invalid.append(["app","--self-test","/tmp/a","--self-test-editor-url",address])
}
for arguments in invalid {
    var rejected=false
    do { _ = try WorkbenchLaunchOptions.parse(arguments) } catch { rejected=true }
    precondition(rejected)
}
print("self-test options passed")
''')

    def test_appearance_injection_is_limited_to_matching_main_frame(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        output = self.foundation(r'''
let editor=URL(string:"http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/")!
precondition(WorkbenchAppearance.script(for:URL(string:"http://127.0.0.1:12345/")!,source:"") == nil)
precondition(WorkbenchAppearance.script(for:URL(string:"https://example.com/")!,source:"") == nil)
let script=WorkbenchAppearance.script(for:editor,source:"(() => { globalThis.appearanceCalls=(globalThis.appearanceCalls||0)+1; return {applied:true}; })();")!
let bytes=try JSONSerialization.data(withJSONObject:["script":script])
print(String(data:bytes,encoding:.utf8)!)
''')
        script = json.loads(output)['script']
        harness = r'''
const assert=require('assert'),vm=require('vm');
function context(changes={},frame=false){
 const window={};window.top=frame?{}:window;
 const docs=new Map([['inactive',{editable:true,text:'原稿',value:'未保存文字',saving:true}]]);
 const text={value:'未保存文字',selectionStart:2,selectionEnd:4};
 return {window,location:{protocol:'http:',hostname:'127.0.0.1',port:'12345',pathname:'/abcdefghijklmnopqrstuvwxyz012345/',...changes},docs,text};
}
const current=context(),before=JSON.stringify([...current.docs]);
assert.equal(vm.runInNewContext(script,current),true);assert.equal(current.appearanceCalls,1);
assert.equal(JSON.stringify([...current.docs]),before);assert.deepEqual(current.text,{value:'未保存文字',selectionStart:2,selectionEnd:4});
assert.equal(vm.runInNewContext(script,current),true);assert.equal(current.appearanceCalls,2);
for(const invalid of [context({protocol:'https:'}),context({hostname:'localhost'}),context({port:'12346'}),context({pathname:'/another_abcdefghijklmnopqrstuvwxyz/'}),context({},true)]){
 assert.equal(vm.runInNewContext(script,invalid),false);assert.equal(invalid.appearanceCalls,undefined);
}
'''
        result = subprocess.run([node, '-'], input='const script=' + json.dumps(script) + ';\n' + harness, encoding='utf-8', capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_editor_safety_waits_for_readiness_and_checks_the_matching_main_frame(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        output = self.foundation(r'''
let editor=URL(string:"http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/")!
precondition(WorkbenchAppearance.safetyScript(for:URL(string:"https://example.com/")!,source:"") == nil)
let script=WorkbenchAppearance.safetyScript(for:editor,source:"(() => { globalThis.installCalls=(globalThis.installCalls||0)+1; return installed; })();")!
print(String(data:try JSONSerialization.data(withJSONObject:["script":script]),encoding:.utf8)!)
''')
        script = json.loads(output)['script']
        program = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
function page(installed,changes={},nested=false){
 const window={};window.top=nested?{}:window;
 return vm.createContext({window,installed,setTimeout,clearTimeout,location:{protocol:'http:',hostname:'127.0.0.1',port:'12345',pathname:'/abcdefghijklmnopqrstuvwxyz012345/',...changes}});
}
const run=context=>vm.runInContext('(async()=>{'+script+'})()',context);
(async()=>{
 let release;const ready=new Promise(resolve=>release=resolve),pending=page({applied:true,ready});let finished=false;
 const check=run(pending).then(value=>{finished=true;return value;});
 await Promise.resolve();assert.equal(finished,false,'native page must remain hidden while verification is pending');
 release(true);assert.equal(await check,true);assert.equal(pending.installCalls,1);
 for(const installed of [{applied:true},{applied:false,ready:Promise.resolve(true)},{applied:true,ready:Promise.resolve(false)}])assert.equal(await run(page(installed)),false);
 const rejected=Promise.reject(Error('synthetic integrity failure'));rejected.catch(()=>{});
 await assert.rejects(run(page({applied:true,ready:rejected})),/integrity failure/);
 const stalled=page({applied:true,ready:new Promise(()=>{})}),timers=[];let cleared=false;
 stalled.setTimeout=(callback,delay)=>{assert.equal(delay,15000);timers.push(callback);return 1;};stalled.clearTimeout=id=>{assert.equal(id,1);cleared=true;};
 const timeout=run(stalled);assert.equal(timers.length,1);timers[0]();assert.equal(await timeout,false);assert.ok(cleared);
 for(const context of [page({applied:true,ready:Promise.resolve(true)},{port:'12346'}),page({applied:true,ready:Promise.resolve(true)},{pathname:'/other_abcdefghijklmnopqrstuvwxyz/'}),page({applied:true,ready:Promise.resolve(true)},{},true)]){
  assert.equal(await run(context),false);assert.equal(context.installCalls,undefined);
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-'], input='const script=' + json.dumps(script) + ';\n' + program, encoding='utf-8',
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_switch_self_test_requires_isolated_shelf_and_distinct_optional_second_editor(self):
        self.foundation(r'''
let shelf="http://127.0.0.1:12345/"
let first="http://127.0.0.1:12346/abcdefghijklmnopqrstuvwxyz012345/"
let second="http://127.0.0.1:12347/another_abcdefghijklmnopqrstuvwxyz/"
let flags=["app","--self-test","/tmp/switch.json","--self-test-shelf-url",shelf,"--self-test-switch-editor-url",first]
let parsed=try WorkbenchLaunchOptions.parse(flags)
precondition(parsed.editorURL == nil && parsed.shelfURL?.absoluteString == shelf && parsed.switchEditorURL?.absoluteString == first)
let two=try WorkbenchLaunchOptions.parse(flags+["--self-test-second-editor-url",second])
precondition(two.secondEditorURL?.absoluteString == second)
var invalid=[
    ["app","--self-test","/tmp/switch.json","--self-test-switch-editor-url",first],
    ["app","--self-test-shelf-url",shelf,"--self-test-switch-editor-url",first],
    flags+["--self-test-editor-url",first],
    flags+["--self-test-second-editor-url",first+"#same-page"],
    ["app","--self-test","/tmp/switch.json","--self-test-second-editor-url",second],
    flags+["--self-test-switch-editor-url",first]
]
for address in ["file:///tmp/book.html","http://localhost:12345/","https://127.0.0.1:12345/","http://127.0.0.1:12345/?unsafe=yes"] {
    invalid.append(["app","--self-test","/tmp/switch.json","--self-test-shelf-url",address,"--self-test-switch-editor-url",first])
}
for arguments in invalid {
    var rejected=false
    do { _ = try WorkbenchLaunchOptions.parse(arguments) } catch { rejected=true }
    precondition(rejected)
}
print("isolated switch options passed")
''')

    def test_client_book_hint_changes_only_the_original_plain_instruction(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        source = SOURCE.read_text()
        block = source.split('private func adaptBookHint()', 1)[1].split('@objc private func chooseBookDirectory', 1)[0]
        script = block.split('let script = """', 1)[1].split('"""', 1)[0]
        script = script.replace(r'\(port)', '12345').replace(r'\(path)', '["/abcdefghijklmnopqrstuvwxyz012345/"]')
        harness = r'''
const assert=require('assert'),vm=require('vm');
function page(text,children=[]){
 const hint={textContent:text,children,dataset:{}};
 const window={};window.top=window;
 const current={window,hint,location:{protocol:'http:',hostname:'127.0.0.1',port:'12345',pathname:'/abcdefghijklmnopqrstuvwxyz012345/'},document:{getElementById:id=>id==='book-hint'?hint:null},observations:0};
 current.MutationObserver=class{constructor(callback){current.callback=callback;}observe(){current.observations++;}};
 return current;
}
const original='作品会在新标签页打开，当前编辑保留。',updated='作品在当前界面切换，已打开作品的编辑保留。';
const delayed=page('');vm.runInNewContext(script,delayed);assert.equal(delayed.observations,1);
delayed.hint.textContent=original;delayed.callback();assert.equal(delayed.hint.textContent,updated);
delayed.callback();assert.equal(delayed.hint.textContent,updated);vm.runInNewContext(script,delayed);assert.equal(delayed.observations,1);
for(const p of [page('打开失败（某书）：无法确认已退出'),page('目前只登记本书；启动服务时可添加其他作品。'),page(original,[{tag:'a'}])]){
 const before=p.hint.textContent;vm.runInNewContext(script,p);p.callback();assert.equal(p.hint.textContent,before);
}
const external=page(original);external.location.hostname='example.com';vm.runInNewContext(script,external);assert.equal(external.observations,0);
const frame=page(original);frame.window.top={};vm.runInNewContext(script,frame);assert.equal(frame.observations,0);
'''
        result = subprocess.run([node, '-'], input='const script=' + json.dumps(script) + ';\n' + harness, encoding='utf-8',
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_appearance_result_uses_real_asset_acceptance(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        asset = json.dumps(str(ROOT / 'desktop/macos/editor-appearance.js'))
        output = self.foundation('''
let editor=URL(string:"http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/")!
let source=try String(contentsOfFile:''' + asset + ''',encoding:.utf8)
let script=WorkbenchAppearance.script(for:editor,source:source)!
let strict=WorkbenchAppearance.script(for:editor,source:"(() => ({applied:'true'}))();")!
let bytes=try JSONSerialization.data(withJSONObject:["script":script,"strict":strict])
print(String(data:bytes,encoding:.utf8)!)
''')
        scripts = json.loads(output)
        harness = APPEARANCE_HARNESS + r'''
function nativePage(known,changes={},frame=false){
 // A book-info ID alone is not proof that the real appearance asset accepted the page.
 // Use complete controls, protected textarea state and the shared DOM fixture instead.
 const fixture=page({known,modern:true});
 const window={};window.top=frame?{}:window;
 const record={editable:true,text:'原文',value:'未保存文字',saving:true};
 const current={window,URL,location:{href:'http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/',protocol:'http:',hostname:'127.0.0.1',port:'12345',pathname:'/abcdefghijklmnopqrstuvwxyz012345/',...changes},document:fixture.document,docs:new Map([['inactive',record]]),text:{value:'未保存文字',selectionStart:1,selectionEnd:3},reads:0,fixture};
 const find=current.document.getElementById.bind(current.document),query=current.document.querySelector.bind(current.document);
 current.document.getElementById=id=>{current.reads++;return find(id);};
 current.document.querySelector=selector=>{current.reads++;return query(selector);};
 return current;
}
for(const known of [false,true]){
 const current=nativePage(known),before=JSON.stringify([...current.docs]),fixture=current.fixture,desk=JSON.stringify(fixture.serialize(fixture.desk));
 assert.equal(vm.runInNewContext(scripts.script,current),known);
 assert.ok(current.reads>0);
 assert.equal(!!current.document.getElementById('story-editor-appearance'),known);
 assert.equal(fixture.body.classList.contains('native-compact-editor'),known);
 assert.equal(JSON.stringify([...current.docs]),before);
 assert.deepEqual(current.text,{value:'未保存文字',selectionStart:1,selectionEnd:3});
 assert.equal(JSON.stringify(fixture.serialize(fixture.desk)),desk);
 assert.strictEqual(current.document.activeElement,fixture.text);
}
assert.equal(vm.runInNewContext(scripts.strict,nativePage(true)),false);
for(const invalid of [nativePage(true,{port:'12346'}),nativePage(true,{pathname:'/another_abcdefghijklmnopqrstuvwxyz/'}),nativePage(true,{},true)]){
 assert.equal(vm.runInNewContext(scripts.script,invalid),false);assert.equal(invalid.reads,0);
}
'''
        result = subprocess.run([node, '-'], input='const scripts=' + json.dumps(scripts) + ';\n' + harness, encoding='utf-8', capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_leaving_check_includes_inactive_buffers_and_pending_save(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        source = SOURCE.read_text()
        method = source.split('func confirmLeaving(', 1)[1].split('@objc func goHome()', 1)[0]
        script = method.split('let script = """', 1)[1].split('"""', 1)[0]
        harness = r'''
const assert=require('assert'),vm=require('vm');
const evaluate=records=>vm.runInNewContext(script,{Map,docs:new Map(records.map((record,index)=>[String(index),record])),document:{getElementById:id=>id==='current-book-root'?{}:null}});
assert.equal(vm.runInNewContext(script,{document:{getElementById:()=>null}}),'unknown');
assert.equal(evaluate([]),'clean');
assert.equal(evaluate([{editable:true,text:'saved',value:'saved'}]),'clean');
assert.equal(evaluate([{editable:true,text:'saved',value:'saved'},{editable:true,text:'old',value:'inactive edit'}]),'pending');
assert.equal(evaluate([{editable:true,text:'saved',value:'saved',saving:true}]),'busy');
assert.equal(evaluate([{editable:true,text:'original',value:'edited',recovered:'edited'}]),'pending');
assert.equal(evaluate([{editable:true,text:'saved',value:'saved',needs_recovery:true}]),'pending');
assert.equal(evaluate([{editable:false,text:'old',value:'readonly'}]),'clean');
assert.equal(evaluate([{editable:true,text:'old'}]),'unknown');
'''
        result = subprocess.run([node, '-'], input='const script=' + json.dumps(script) + ';\n' + harness, encoding='utf-8', capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bundle_metadata_uses_local_networking_and_current_binary(self):
        with (ROOT / 'desktop/macos/Info.plist').open('rb') as handle:
            settings = plistlib.load(handle)
        self.assertEqual(settings['CFBundleExecutable'], 'StoryWorkbench')
        self.assertEqual(settings['CFBundleIconFile'], 'AppIcon')
        self.assertEqual(settings['LSMinimumSystemVersion'], '13.0')
        self.assertEqual(settings['NSAppTransportSecurity'], {'NSAllowsLocalNetworking': True})


if __name__ == '__main__':
    unittest.main()
