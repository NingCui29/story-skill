"""Native leave guards must agree with the newer editor save lifecycle."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from test_desktop_close_regressions import mac_method
import test_desktop_windows_safety as safety
import test_desktop_windows_install_guard as install_guard


class PendingSaveCloseTests(unittest.TestCase):
    def probes(self):
        node = shutil.which('node')
        if not node:
            bundled = (Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
            node = str(bundled) if bundled.is_file() else None
        if not node:
            self.skipTest('Node.js required')
        mac = mac_method().split('let script = """', 1)[1].split('"""', 1)[0]
        scripts = [mac, safety.client.close_state_script(), safety.client.close_state_script(True)]
        program = r'''
const assert=require('assert'),vm=require('vm'),scripts=__SCRIPTS__;
const changes={
 legacy:{},
 readback:{pendingSaveReadback:{id:'file:已写入的新版本.md',text:'已保存文字'},saveResultPending:true,saveError:'回读失败'},
 version:{saveError:'新版本尚未保存',failedSaveOptions:{newVersion:true}},
 recovery:{recovering:Promise.resolve(true)},
 cleared:{pendingSaveReadback:null,saveResultPending:false,saveError:'',failedSaveOptions:{newVersion:true},recovering:null}
};
const result={};
for(const [name,extra]of Object.entries(changes)){
 const source={id:'file:隐藏稿件.md',title:'隐藏稿件',path:'隐藏稿件.md',editable:true,
  text:'已保存文字',value:'已保存文字',...extra};
 const current={id:'file:当前稿件.md',title:'当前稿件',editable:true,text:'当前文字',value:'当前文字'};
 const docs=new Map([[current.id,current],[source.id,source]]),before=JSON.stringify([...docs]);
 const context={Map,Set,docs,active:current.id,document:{getElementById:id=>id==='current-book-root'?{}:null}};
 const actual=scripts.map(script=>vm.runInNewContext(script,context));
 assert.equal(JSON.stringify([...docs]),before,'close probes must not modify buffers or receipts');
 result[name]={mac:actual[0],windows:actual[1],export:actual[2]};
}
console.log(JSON.stringify(result));
'''.replace('__SCRIPTS__', json.dumps(scripts))
        result = subprocess.run([node, '-'], input=program, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_real_probes_cover_hidden_pending_results_and_legacy_clean_documents(self):
        packets = self.probes()
        for name in ('readback', 'version'):
            with self.subTest(name=name):
                packet = packets[name]
                self.assertEqual(packet['mac'], 'pending')
                checked = safety.client.checked_close_state(packet['windows'])
                self.assertFalse(checked['saving'])
                self.assertEqual([row['id'] for row in checked['pending']], ['file:隐藏稿件.md'])
                self.assertFalse(checked['pending'][0]['dirty'])
                exported = safety.client.checked_close_state(packet['export'], include_text=True)
                self.assertEqual(exported['pending'][0]['text'], '已保存文字')
        for name in ('legacy', 'cleared'):
            with self.subTest(name=name):
                self.assertEqual(packets[name]['mac'], 'clean')
                checked = safety.client.checked_close_state(packets[name]['windows'])
                self.assertFalse(checked['pending'])
                self.assertFalse(checked['saving'])

    def test_inflight_recovery_blocks_native_leave_without_offering_export(self):
        packet = self.probes()['recovery']
        self.assertEqual(packet['mac'], 'busy')
        self.assertTrue(safety.client.checked_close_state(packet['windows'])['saving'])
        for action in ('关闭页面', '重新载入', '启动更新安装', '退出工作台'):
            with self.subTest(action=action):
                view = safety.View(safety.EDITOR)
                host, warnings = safety.CloseControlsTests().make_window([view], export=True)
                allowed = []
                host.request_close([view], action, allowed.append)
                view.reply(packet['windows'])
                self.assertEqual(allowed, [False])
                self.assertIn('恢复', warnings[0][0])
                self.assertFalse(view.deleted)
                self.assertFalse(hasattr(host, 'close_export_path'))
                self.assertTrue(host.tabs.enabled)

    def test_real_windows_pending_results_require_a_decision_for_every_leave_action(self):
        packets = self.probes()
        for name in ('readback', 'version'):
            for action in ('关闭页面', '重新载入', '启动更新安装', '退出工作台'):
                with self.subTest(name=name, action=action):
                    view = safety.View(safety.EDITOR)
                    host, warnings = safety.CloseControlsTests().make_window([view])
                    allowed = []
                    host.request_close([view], action, allowed.append)
                    view.reply(packets[name]['windows'])
                    self.assertEqual(allowed, [False])
                    self.assertFalse(view.deleted)
                    self.assertTrue(host.tabs.enabled)
                    self.assertEqual(warnings, [])

    def test_windows_export_preserves_unchanged_text_that_still_needs_save_confirmation(self):
        packets = self.probes()
        with tempfile.TemporaryDirectory(prefix='pending-save-export-') as directory:
            for name in ('readback', 'version'):
                with self.subTest(name=name):
                    target = Path(directory) / (name + '.zip')
                    view = safety.View(safety.EDITOR)
                    host, warnings = safety.CloseControlsTests().make_window([view], export=True, path=str(target))
                    allowed = []
                    host.request_close([view], '关闭页面', allowed.append)
                    view.reply(packets[name]['windows'])
                    self.assertEqual(allowed, [])
                    self.assertFalse(target.exists())
                    view.reply(packets[name]['export'])
                    self.assertEqual(allowed, [True])
                    with zipfile.ZipFile(target) as archive:
                        manifest = json.loads(archive.read('导出说明.json'))
                        record, = manifest['documents']
                        self.assertTrue(record['needs_recovery'])
                        self.assertEqual(archive.read(record['export_file']).decode('utf-8'), '已保存文字')
                    self.assertEqual(warnings, [])

    def test_windows_install_never_starts_hash_or_process_before_pending_results_are_resolved(self):
        for name, packet in self.probes().items():
            if name not in ('readback', 'version', 'recovery'):
                continue
            with self.subTest(name=name):
                fixture = install_guard.WindowsInstallGuardTests('runTest')
                fixture.setUp()
                try:
                    fixture.window.launch_verified_installer(fixture.update, fixture.dialog)
                    fixture.view.reply(packet['windows'])
                    self.assertIsNone(fixture.dialog.worker)
                    self.assertFalse(fixture.dialog.install_checking)
                    self.assertEqual(fixture.launches, [])
                    self.assertTrue(fixture.window.tabs.enabled)
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
