"""Recovery links select documents in retained pages without reloading buffers."""
import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import unittest

import test_desktop_native as native


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('target_client', ROOT / 'desktop/windows/client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class DesktopDocumentTargetTests(unittest.TestCase):
    foundation = native.DesktopNativeTests.foundation

    def run_script(self, script):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
function context(changes={}){
 const window={},calls=[],docs=new Map([['formal:2',{value:'未保存的合成文字',editing:true}]]);
 return vm.createContext({window,location:{protocol:'http:',hostname:'127.0.0.1',port:'12345',
 pathname:'/abcdefghijklmnopqrstuvwxyz012345/',search:'',...changes},active:'formal:2',docs,calls,
 openDoc:id=>calls.push(id)});
}
const current=context();current.window.top=current.window;
assert.equal(vm.runInContext(script,current),true);
assert.deepEqual(Array.from(current.calls),['file:.story/drafts/workbench/恢复稿.txt']);
assert.equal(decodeURIComponent(current.location.hash.replace(/^#/,'')),'file:.story/drafts/workbench/恢复稿.txt');
assert.equal(current.docs.get('formal:2').value,'未保存的合成文字');
current.calls.length=0;current.active='file:.story/drafts/workbench/恢复稿.txt';
assert.equal(vm.runInContext(script,current),true);assert.equal(current.calls.length,1);
for(const changes of [{port:'12346'},{hostname:'localhost'},{protocol:'https:'},
 {pathname:'/ZYXWVUTSRQPONMLKJIHGFEDCBA543210/'},{search:'?other=1'}]){
 const page=context(changes);page.window.top=page.window;
 assert.equal(vm.runInContext(script,page),false);assert.equal(page.calls.length,0);
}
const nested=context();nested.window.top={};assert.equal(vm.runInContext(script,nested),false);
'''
        result = subprocess.run([node, '-'], input='const script=' + json.dumps(script) + ';\n' + harness, encoding='utf-8',
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_macos_cached_target_waits_for_safety_and_keeps_the_last_request(self):
        source = native.SOURCE.read_text()
        target_methods = source[source.index('    func openDocumentTarget('):source.index('    func confirmLeaving(')]
        show_editor = source[source.index('    func showEditor(_ url: URL)'):source.index('    private var currentWindow:')]
        checks = r'''
class FixtureWindow {}
class FixtureWebView {
 var scripts:[String]=[]
 func evaluateJavaScript(_ script:String,completionHandler:Any?){scripts.append(script)}
}
class WorkbenchWindow {
 var entryURL:URL?,isReady=false,loadCount=0,value="未保存的合成文字"
 var pendingDocumentURL:URL?
 let webView=FixtureWebView()
 init(app:Host,window:FixtureWindow,entryURL:URL){self.entryURL=entryURL}
 func load(_ url:URL){entryURL=url;loadCount+=1}
 func ready(){isReady=true;openPendingDocument()}
__TARGET_METHODS__
}
class Host {
 let editorPages=WorkbenchPageCache<WorkbenchWindow>()
 var activePage:WorkbenchWindow?
 func hostWindow()->FixtureWindow{FixtureWindow()}
 func activate(_ page:WorkbenchWindow){activePage=page}
__SHOW_EDITOR__
}
let base="http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/"
let host=Host(),first=host.showEditor(URL(string:base)!)!
_ = host.showEditor(URL(string:base+"#formal%3A1")!)
let recovery=URL(string:base+"#file%3A.story%2Fdrafts%2Fworkbench%2F%E6%81%A2%E5%A4%8D%E7%A8%BF.txt")!
precondition(host.showEditor(recovery) === first)
precondition(first.webView.scripts.isEmpty && first.loadCount==1)
first.ready()
precondition(first.webView.scripts.count==1 && first.value=="未保存的合成文字")
let script=WorkbenchAppearance.documentScript(for:recovery)!
precondition(first.webView.scripts[0]==script)
_ = host.showEditor(URL(string:base)!)
precondition(first.webView.scripts.count==1 && first.loadCount==1)
precondition(WorkbenchAppearance.documentScript(for:URL(string:base)!)==nil)
precondition(WorkbenchAppearance.documentScript(for:URL(string:"https://example.com/#formal%3A1")!)==nil)
let freshHost=Host(),fresh=freshHost.showEditor(recovery)!
precondition(fresh.entryURL==recovery && fresh.loadCount==1 && fresh.pendingDocumentURL==nil)
fresh.ready()
precondition(fresh.webView.scripts.isEmpty)
_ = freshHost.showEditor(URL(string:base+"#formal%3A1")!)
precondition(fresh.webView.scripts.count==1 && fresh.loadCount==1)
print(String(data:try! JSONSerialization.data(withJSONObject:["script":script]),encoding:.utf8)!)
'''.replace('__TARGET_METHODS__', target_methods).replace('__SHOW_EDITOR__', show_editor)
        output = self.foundation(checks)
        self.run_script(json.loads(output)['script'])

    def test_windows_popup_routes_to_cached_page_and_queues_before_load(self):
        tree = ast.parse((ROOT / 'desktop/windows/client.py').read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        classes = {n.name: n for n in main.body if isinstance(n, ast.ClassDef)}
        def extract(class_name, method):
            node = next(n for n in classes[class_name].body if isinstance(n, ast.FunctionDef) and n.name == method)
            return ast.ClassDef(name=class_name, bases=[], keywords=[], body=[node], decorator_list=[])
        selected, scripts, cleanups = [], [], []
        page = SimpleNamespace(runJavaScript=scripts.append)
        view = SimpleNamespace(entry_key=(12345, '/abcdefghijklmnopqrstuvwxyz012345/'),
                               load_complete=False, pending_document=None, page=lambda: page,
                               value='未保存的合成文字')
        window = SimpleNamespace(close_pending=False, find_page=lambda *a, **k: view,
                                 tabs=SimpleNamespace(setCurrentWidget=selected.append),
                                 remove_page=lambda value: None)
        scope = dict(editor_target_script=client.editor_target_script, page_key=client.page_key,
                     local_url=client.local_url, window=window,
                     QTimer=SimpleNamespace(singleShot=lambda delay, callback: cleanups.append(callback)))
        module = ast.Module(body=[extract('Window', 'open_document_target'),
                                  extract('Page', 'acceptNavigationRequest')], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), '<production document routing>', 'exec'), scope)
        window.open_document_target = lambda v, address: scope['Window'].open_document_target(window, v, address)
        popup = scope['Page']()
        popup.view = SimpleNamespace(entry_key=None, navigation_approved=None)
        address = ('http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/'
                   '#file%3A.story%2Fdrafts%2Fworkbench%2F%E6%81%A2%E5%A4%8D%E7%A8%BF.txt')
        self.assertFalse(popup.acceptNavigationRequest(SimpleNamespace(toString=lambda: address), None, True))
        self.assertEqual(selected, [view])
        self.assertEqual(view.pending_document, address)
        self.assertEqual(scripts, [])
        view.load_complete = True
        window.open_document_target(view, view.pending_document)
        self.assertIsNone(view.pending_document)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(view.value, '未保存的合成文字')
        self.assertEqual(len(cleanups), 1)
        self.run_script(scripts[0])

    def test_windows_recovery_target_survives_initial_catalog_and_hashchange(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        source = (ROOT / 'skills/story-skill/scripts/story_workbench.py').read_text()
        start = source.index('async function openDoc(')
        opener = source[start:source.index('\n', start)]
        start = source.index('  if(!active){', source.index('async function catalog('))
        initial = source[start:source.index('\n }catch', start)]
        start = source.index("window.addEventListener('hashchange',", source.index('async function openDoc('))
        hashchange = source[start:source.index('\n', start)]
        base = 'http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/#'
        recovery = 'file:.story/drafts/workbench/恢复稿.txt'
        latest = 'file:.story/drafts/workbench/最新恢复稿.txt'
        from urllib.parse import quote
        packet = {'opener': opener, 'initial': initial, 'hashchange': hashchange,
                  'recovery': recovery, 'latest': latest,
                  'first': client.editor_target_script(base + quote(recovery, safe='')),
                  'second': client.editor_target_script(base + quote(latest, safe=''))}
        harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(process.argv[1]);
async function check(scripts,wanted){
 const pending=[],events=[],retained={id:'formal:2',text:'原文',value:'未保存的合成文字',editing:true};
 const location={protocol:'http:',hostname:'127.0.0.1',port:'12345',
  pathname:'/abcdefghijklmnopqrstuvwxyz012345/',search:''};
 let hash='';
 Object.defineProperty(location,'hash',{get:()=>hash,set:value=>{
  const next=value?'#'+String(value).replace(/^#/,''):'';
  if(next===hash)return;
  hash=next;Promise.resolve().then(()=>events.forEach(handler=>handler()));
 }});
 const page={window:{addEventListener:(name,handler)=>{if(name==='hashchange')events.push(handler);}},
  location,docs:new Map([['formal:2',retained]]),active:null,openSequence:0,
  crypto:{randomUUID:()=> 'fixture'},closePanels(){},note(){},
  displayTitle:d=>d.id,versionLabel:()=>'',previewSearch(){},catalog:async()=>{},
  call:(action,{id})=>new Promise(resolve=>pending.push({id,resolve}))};
 page.window.top=page.window;
 const context=vm.createContext(page);context.show=d=>context.active=d.id;
 vm.runInContext(input.opener,context);vm.runInContext(input.hashchange,context);
 // loadFinished dispatched the retained target while the first catalog HTTP
 // response and target open are still pending. Execute that real selection
 // branch before answering any of the document requests.
 for(const script of scripts)assert.equal(vm.runInContext(script,context),true);
 const initial=vm.runInContext('(async()=>{const c={chapters:[{id:"formal:1"}],files:[]};'+input.initial+'})()',context);
 await Promise.resolve();
 assert(pending.length>=2);
 assert(!pending.some(item=>item.id==='formal:1'),'initial catalog must respect the explicit target');
 for(const item of pending)item.resolve({id:item.id,text:'恢复原文'});
 await initial;await Promise.resolve();await Promise.resolve();
 assert.equal(context.active,wanted);
 assert.equal(decodeURIComponent(location.hash.slice(1)),wanted);
 assert.equal(context.docs.get('formal:2'),retained);
 assert.equal(retained.value,'未保存的合成文字');assert.equal(retained.editing,true);
}
(async()=>{
 await check([input.first],input.recovery);
 await check([input.first,input.second],input.latest);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', harness, json.dumps(packet)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
