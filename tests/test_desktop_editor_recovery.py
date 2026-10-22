"""Retry a confirmed lost editor process without weakening ordinary probes."""
import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DesktopEditorRecoveryTests(unittest.TestCase):
    def test_actual_swift_retry_recovers_only_a_confirmed_editor_process_loss(self):
        if sys.platform != 'darwin' or not shutil.which('xcrun'):
            self.skipTest('macOS Swift compiler required')
        source = (ROOT / 'desktop/macos/StoryWorkbench.swift').read_text()
        method = 'func confirmLeaving(' + source.split('func confirmLeaving(', 1)[1].split('@objc func goHome()', 1)[0]
        tree = ast.parse((ROOT / 'tests/test_desktop_close_regressions.py').read_text())
        harness = next(node.value for node in ast.walk(tree)
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)
                       and '__METHOD__' in node.value and 'import Foundation' in node.value)
        harness = harness.replace('__METHOD__', method)
        harness = harness.replace('class FakeApp { func activate(_ host:Host){} }', 'class FakeApp { var shelfStarts=0;func activate(_ host:Host){};func startShelf(){shelfStarts+=1} }')
        retry = 'func retryStartup()' + source.split('@objc private func retryStartup()', 1)[1].split('func webView(', 1)[0]
        reload_page = 'func reloadPage()' + source.split('@objc func reloadPage()', 1)[1].split('@objc func saveCandidate()', 1)[0]
        crash = ('func webViewWebContentProcessDidTerminate' + source.split('func webViewWebContentProcessDidTerminate', 1)[1].split('func webView(', 1)[0]).replace('WKWebView', 'FakeWebView')
        addition = '''var loadCount=0, statusShown=false
func load(_ url:URL){loadCount+=1}
func showStatus(title:String,detail:String,retry:Bool){statusShown=true}
''' + retry + reload_page + crash
        harness = harness.replace('let webView=FakeWebView()', 'let webView=FakeWebView()\n' + addition)
        harness += '''
let editor=URL(string:"http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/")!
let lost=Host();lost.entryURL=editor;lost.webView.url=editor
lost.webViewWebContentProcessDidTerminate(lost.webView)
precondition(!lost.loadedPage && lost.editorProcessTerminated && lost.statusShown)
lost.webView.error=NSError(domain:"WKErrorDomain",code:2)
for _ in 0..<3 {lost.retryStartup()}
precondition(lost.loadCount==3 && lost.webView.probes==0)
var mayClose:Bool?;lost.confirmLeaving(action:"退出客户端"){mayClose=$0}
precondition(mayClose==true && lost.webView.probes==0)
let unknown=Host();unknown.entryURL=editor;unknown.webView.url=editor;unknown.loadedPage=false
unknown.webView.error=NSError(domain:"WKErrorDomain",code:2);unknown.retryStartup()
precondition(unknown.loadCount==0 && unknown.webView.probes==1)
let live=Host();live.entryURL=editor;live.webView.url=editor;live.webView.result="busy"
live.retryStartup();precondition(live.loadCount==0 && live.webView.probes==1)
let shelf=Host();shelf.webViewWebContentProcessDidTerminate(shelf.webView);shelf.retryStartup()
precondition(!shelf.editorProcessTerminated && shelf.loadCount==0 && shelf.app?.shelfStarts==1)
print("editor process recovery and live guards passed")
'''
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            swift, executable = folder / 'recovery.swift', folder / 'recovery'
            swift.write_text(harness)
            result = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(swift), '-o', str(executable)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        did_finish = source.split('func webView(_ webView: WKWebView, didFinish', 1)[1].split('func webView(', 1)[0]
        self.assertIn('editorProcessTerminated = false', did_finish)
