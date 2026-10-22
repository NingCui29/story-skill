"""Exercise real native close probes and host decisions without opening books."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_desktop_windows_safety as windows_safety


ROOT = Path(__file__).resolve().parents[1]
MAC = ROOT / 'desktop/macos/StoryWorkbench.swift'
SPEC = importlib.util.spec_from_file_location('close_regression_client', ROOT / 'desktop/windows/client.py')
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


def mac_method():
    source = MAC.read_text()
    return 'func confirmLeaving(' + source.split('func confirmLeaving(', 1)[1].split('@objc func goHome()', 1)[0]


class NativeCloseRegressionTests(unittest.TestCase):
    def javascript(self, program):
        node = shutil.which('node')
        if not node:
            bundled = (Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
            node = str(bundled) if bundled.is_file() else None
        if not node:
            self.skipTest('Node is unavailable')
        result = subprocess.run([node, '-e', program], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mac_reads_latest_visible_and_all_hidden_buffers_without_mutating_them(self):
        script = mac_method().split('let script = """', 1)[1].split('"""', 1)[0]
        self.javascript('const script=' + json.dumps(script) + r''';
const assert=require('assert'),vm=require('vm');
function probe(changes={}){
 const records=[['current',{editable:true,text:'原文',value:'原文',editing:true}],['hidden',{editable:true,text:'旧文',value:'旧文'}]];
 const context={Map,Set,docs:new Map(records),active:'current',textbox:{value:'原文'},...changes};
 context.document={getElementById:id=>id==='current-book-root'?{}:id==='text'?context.textbox:null};
 const before=JSON.stringify([...context.docs]);
 const result=vm.runInNewContext(script,context);assert.equal(JSON.stringify([...context.docs]),before);return result;
}
assert.equal(probe(),'clean');
assert.equal(probe({textbox:{value:'最后输入的文字'}}),'pending');
assert.equal(probe({textbox:null}),'unknown');
assert.equal(probe({docs:new Map([['hidden',{editable:true,text:'旧文',value:'隐藏编辑',recovered:'隐藏编辑'}]])}),'pending');
assert.equal(probe({docs:new Map([['pending',{editable:true,text:'待核对',value:'待核对',needs_recovery:true}]])}),'pending');
assert.equal(probe({docs:new Map([['saving',{editable:true,text:'原稿',value:'原稿',saving:true}]])}),'busy');
assert.equal(probe({docs:new Map([['bad',{editable:true,text:'原稿',value:'原稿',saving:1}]])}),'unknown');
''')

    def test_both_native_probes_block_each_shelf_operation_and_fail_closed_when_unknown(self):
        scripts = [mac_method().split('let script = """', 1)[1].split('"""', 1)[0], client.close_state_script()]
        self.javascript('const scripts=' + json.dumps(scripts) + r''';
const assert=require('assert'),vm=require('vm');
for(let index=0;index<scripts.length;index++){
 const probe=changes=>vm.runInNewContext(scripts[index],{Map,Set,busy:false,managerBusy:false,lastBooksBusy:false,statusSaving:new Set(),shelfStatusDrafts:new Map(),document:{getElementById:id=>id==='library-heading'?{}:null},...changes});
 const expected=busy=>index===0?(busy?'busy':'clean'):{kind:'shelf',busy,shelf_pending:false,documents:[]};
 for(const flags of [{},{busy:true},{managerBusy:true},{lastBooksBusy:true},{statusSaving:new Set(['book'])}])assert.equal(JSON.stringify(probe(flags)),JSON.stringify(expected(Object.keys(flags).length>0)));
 for(const flags of [{busy:1},{managerBusy:null},{lastBooksBusy:1},{statusSaving:[]},{shelfStatusDrafts:[]}])assert.equal(probe(flags),index===0?'unknown':null);
 const drafts=probe({shelfStatusDrafts:new Map([['book','completed']])});
 assert.equal(JSON.stringify(drafts),JSON.stringify(index===0?'shelf-pending':{kind:'shelf',busy:false,shelf_pending:true,documents:[]}));
 const unknown=vm.runInNewContext(scripts[index],{document:{getElementById:()=>({})}});
 assert.equal(unknown,index===0?'unknown':null);
}
''')

    def test_windows_busy_state_requires_exact_boolean_and_cannot_be_exported(self):
        for invalid in (None, 0, 1, 'false', [], {}):
            value = {'kind': 'shelf', 'busy': invalid, 'shelf_pending': False, 'documents': []}
            self.assertIsNone(client.checked_close_state(value))
            self.assertIsNone(client.checked_close_state({'kind': 'shelf', 'busy': False, 'shelf_pending': invalid, 'documents': []}))
        self.assertIsNone(client.checked_close_state({'kind': 'shelf', 'documents': []}))
        self.assertTrue(client.checked_close_state(windows_safety.state(kind='shelf', busy=True))['busy'])
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / 'pending.zip'
            with self.assertRaises(ValueError):
                client.export_pending_buffers(target, [windows_safety.state(kind='shelf', busy=True)])
            self.assertFalse(target.exists())

    def test_windows_host_rejects_busy_shelf_for_close_reload_and_update_without_export(self):
        controls = windows_safety.CloseControlsTests()
        for action in ('关闭页面', '重新载入', '启动更新安装', '退出工作台'):
            with self.subTest(action=action):
                view = windows_safety.View(windows_safety.SHELF)
                host, warnings = controls.make_window([view], export=True)
                allowed = []
                host.request_close([view], action, allowed.append)
                view.reply(windows_safety.state(kind='shelf', busy=True))
                self.assertEqual(allowed, [False])
                self.assertEqual(warnings[0][0], '书架操作正在进行')
                self.assertIn(action + '已取消', warnings[0][1])
                self.assertTrue(host.tabs.enabled)
                self.assertFalse(host.close_pending)
                self.assertFalse(view.deleted)
                self.assertFalse(hasattr(host, 'close_export_path'))

    def test_windows_host_rejects_unsaved_shelf_status_without_offering_text_export(self):
        controls = windows_safety.CloseControlsTests()
        view = windows_safety.View(windows_safety.SHELF)
        host, warnings = controls.make_window([view], export=True)
        allowed = []
        host.request_close([view], '重新载入', allowed.append)
        view.reply(windows_safety.state(kind='shelf', shelf_pending=True))
        self.assertEqual(allowed, [False])
        self.assertEqual(warnings[0][0], '作品状态尚未保存')
        self.assertIn('改回原状态', warnings[0][1])
        self.assertFalse(hasattr(host, 'close_export_path'))

    def test_mac_host_cannot_override_busy_unknown_or_failed_probe(self):
        if sys.platform != 'darwin' or not shutil.which('xcrun'):
            self.skipTest('macOS Swift compiler required')
        harness = r'''
import Foundation
enum Response { case alertFirstButtonReturn, alertSecondButtonReturn }
class FakeWindow {}
class NSAlert {
    enum Style { case warning }
    static var instances=[NSAlert](), response=Response.alertSecondButtonReturn
    var messageText="", informativeText="", alertStyle=Style.warning, buttons=[String]()
    init(){Self.instances.append(self)}
    func addButton(withTitle title:String){buttons.append(title)}
    func beginSheetModal(for window:FakeWindow, completion:(Response)->Void){completion(Self.response)}
}
enum WorkbenchURL {
    static func isEditor(_ url:URL)->Bool{url.path != "/"}
    static func samePage(_ first:URL,_ second:URL)->Bool{first==second}
}
class FakeWebView {
    var url=URL(string:"http://127.0.0.1:8765/")
    var result:Any?="clean", error:Error?, probes=0
    func evaluateJavaScript(_ script:String, completion:(Any?,Error?)->Void){probes+=1;completion(result,error)}
}
class FakeApp { func activate(_ host:Host){} }
class Host {
    var editorProcessTerminated=false, loadedPage=true
    var entryURL=URL(string:"http://127.0.0.1:8765/"), window:FakeWindow?=FakeWindow(), app:FakeApp?=FakeApp()
    let webView=FakeWebView()
__METHOD__
}
func check(_ state:Any?, allowed:Bool, count:Int, error:Error?=nil){
    NSAlert.instances=[];NSAlert.response = .alertSecondButtonReturn
    let host=Host();host.webView.result=state;host.webView.error=error
    var result:Bool?;host.confirmLeaving(action:"重新载入"){result=$0}
    precondition(result==allowed);precondition(NSAlert.instances.count==(count==0 ? 0:1))
    if count>0 {precondition(NSAlert.instances[0].buttons.count==count)}
}
check("clean",allowed:true,count:0)
check("pending",allowed:true,count:2)
for state:Any? in ["busy","shelf-pending","unknown",nil,true,"unexpected"]{check(state,allowed:false,count:1)}
check("clean",allowed:false,count:1,error:NSError(domain:"probe",code:1))
NSAlert.response = .alertFirstButtonReturn
let pending=Host();pending.webView.result="pending";var cancelled:Bool?
pending.confirmLeaving(action:"退出"){cancelled=$0};precondition(cancelled==false)
let external=Host();external.webView.url=URL(string:"https://example.com/");var externalAllowed:Bool?
external.confirmLeaving(action:"退出"){externalAllowed=$0};precondition(externalAllowed==true && external.webView.probes==0)
print("native close decisions passed")
'''.replace('__METHOD__', mac_method())
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / 'close.swift'
            executable = Path(raw) / 'close'
            source.write_text(harness)
            result = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source), '-o', str(executable)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
