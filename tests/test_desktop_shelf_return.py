"""Returning to retained native shelves sends a scoped event, never a reload."""
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
WINDOWS = ROOT / 'desktop/windows/client.py'
MACOS = ROOT / 'desktop/macos/StoryWorkbench.swift'
SPEC = importlib.util.spec_from_file_location('windows_shelf_return_client', WINDOWS)
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
SHELF = 'http://127.0.0.1:8765/'
EDITOR = 'http://127.0.0.1:49152/' + 'a' * 32 + '/'


class DesktopShelfReturnTests(unittest.TestCase):
    foundation = native.DesktopNativeTests.foundation

    def check_script(self, script):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        harness = r'''
const assert=require('assert'),vm=require('vm');
function page(changes={},subframe=false){
 const events=[],window={dispatchEvent:event=>events.push(event.type)};window.top=subframe?{}:window;
 const docs=new Map([['hidden',{editable:true,text:'已保存原文',value:'尚未保存的编辑',saving:true}]]);
 const text={value:'尚未保存的编辑',selectionStart:2,selectionEnd:5};
 return {window,events,Event:class{constructor(type){this.type=type;}},location:{protocol:'http:',hostname:'127.0.0.1',port:'8765',pathname:'/',search:'',...changes},docs,text};
}
const shelf=page(),before=JSON.stringify([...shelf.docs]);
assert.equal(vm.runInNewContext(script,shelf),true);assert.deepEqual(shelf.events,['story-shelf-return']);
assert.equal(JSON.stringify([...shelf.docs]),before);assert.deepEqual(shelf.text,{value:'尚未保存的编辑',selectionStart:2,selectionEnd:5});
for(const invalid of [page({protocol:'https:'}),page({hostname:'localhost'}),page({port:'8766'}),page({pathname:'/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/'}),page({search:'?other=1'}),page({},true)]){
 assert.equal(vm.runInNewContext(script,invalid),false);assert.deepEqual(invalid.events,[]);
}
'''
        result = subprocess.run([node, '-e', 'const script=' + json.dumps(script) + ';\n' + harness],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_windows_tab_switch_notifies_only_loaded_same_shelf(self):
        tree = ast.parse(WINDOWS.read_text())
        window = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.ClassDef) and node.name == 'Window')
        method = next(node for node in window.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'notify_shelf_return')
        isolated = ast.Module(body=[ast.ClassDef(name='Host', bases=[], keywords=[],
                                               body=[method], decorator_list=[])], type_ignores=[])
        namespace = {'page_key': client.page_key, 'shelf_return_script': client.shelf_return_script}
        exec(compile(ast.fix_missing_locations(isolated), str(WINDOWS), 'exec'), namespace)
        host = namespace['Host']()
        scripts = []

        def view(address, entry=SHELF, loaded=True):
            return SimpleNamespace(load_complete=loaded, entry_key=client.page_key(entry),
                                   url=lambda: SimpleNamespace(toString=lambda: address),
                                   page=lambda: SimpleNamespace(runJavaScript=scripts.append))

        host.close_pending = False
        host.tabs = SimpleNamespace(widget=lambda index: current)
        for current in (None, view(SHELF, loaded=False), view(EDITOR, entry=EDITOR),
                        view(SHELF, entry=EDITOR), view('http://127.0.0.1:8766/'),
                        view(SHELF + '?other=1'), view('https://example.com/'),
                        view('about:blank', entry='about:blank')):
            host.notify_shelf_return(0)
            self.assertEqual(scripts, [])
        current = view(SHELF + '#section')
        host.close_pending = True
        host.notify_shelf_return(0)
        self.assertEqual(scripts, [])
        host.close_pending = False
        host.notify_shelf_return(-1)
        self.assertEqual(scripts, [])
        host.notify_shelf_return(0)
        self.assertEqual(len(scripts), 1)
        self.check_script(scripts[0])

    def test_macos_retained_shelf_notifies_only_after_controlled_load(self):
        source = MACOS.read_text()
        method = source[source.index('    private func notifyShelfReturn() {'):
                        source.index('    private func configureToolbar(')]
        fixture = r'''
final class ShelfView {
    var url: URL?
    var scripts: [String] = []
    func evaluateJavaScript(_ script: String, completionHandler: Any?) { scripts.append(script) }
}
final class ShelfHost {
    var isControlledPage = true
    var entryURL: URL? = URL(string:"http://127.0.0.1:8765/")!
    let webView = ShelfView()
    func returned() { notifyShelfReturn() }
'''
        checks = r'''
}
let host = ShelfHost()
host.webView.url = URL(string:"http://127.0.0.1:8765/")!
host.isControlledPage = false
host.returned()
precondition(host.webView.scripts.isEmpty)
host.isControlledPage = true
for address in ["http://127.0.0.1:8766/", "http://127.0.0.1:8765/?other=1", "http://127.0.0.1:8765/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/", "https://example.com/"] {
    host.webView.url = URL(string:address)!
    host.returned()
    precondition(host.webView.scripts.isEmpty)
}
host.entryURL = URL(string:"http://127.0.0.1:8765/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/")!
host.webView.url = host.entryURL
host.returned()
precondition(host.webView.scripts.isEmpty)
host.entryURL = URL(string:"http://127.0.0.1:8765/")!
host.webView.url = URL(string:"http://127.0.0.1:8765/#section")!
host.returned()
precondition(host.webView.scripts.count == 1)
let bytes = try JSONSerialization.data(withJSONObject:["script":host.webView.scripts[0]])
print(String(data:bytes,encoding:.utf8)!)
'''
        output = self.foundation(fixture + method + checks)
        self.check_script(json.loads(output)['script'])


if __name__ == '__main__':
    unittest.main()
