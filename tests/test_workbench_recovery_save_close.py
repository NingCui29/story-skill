"""A failed recovery-to-candidate save must survive the installed Mac close probe."""
import json
import unittest

from pathlib import Path
def mac_method():
    source = (Path(__file__).resolve().parents[1] / 'desktop/macos/StoryWorkbench.swift').read_text()
    return 'func confirmLeaving(' + source.split('func confirmLeaving(', 1)[1].split('@objc func goHome()', 1)[0]
import test_workbench_save_status as save_status


class WorkbenchRecoverySaveCloseTests(unittest.TestCase):
    def test_real_recovery_save_failure_blocks_leave_and_retry_releases_mac_client(self):
        fixture = save_status.WorkbenchSaveStatusTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        mac = mac_method().split('let script = """', 1)[1].split('"""', 1)[0]
        setup = 'const macProbe=' + json.dumps(mac) + ';' + r'''
function nativeClose(){
 const previous=document.getElementById;
 document.getElementById=id=>id==='library-heading'?null:previous(id);
 try{return {mac:eval(macProbe)};}
 finally{document.getElementById=previous;}
}
'''
        case = r'''
docs.clear();
const recovery={...copy(backend.recovery_document),value:backend.recovery_document.text,editing:false};
docs.set(recovery.id,recovery);active=recovery.id;badge();
assert.equal(documentIdentity(recovery).kind,'recovery');
assert.equal(dirty(recovery),false);assert.equal(!!recovery.needs_recovery,false);
assert.equal(pendingClose(),false,'merely opening a saved recovery is safe to close');
let closing=nativeClose();assert.equal(closing.mac,'clean');
mode.failSave=true;
assert.equal(await saveDocument(recovery),false);
assert.equal(requests.find(request=>request.action==='save').save_mode,'version');
assert.equal(recovery.failedSaveOptions.newVersion,true,'record actual recovery-to-candidate save intent');
assert.equal(documentSaveState(recovery).failed,true);
assert.equal(pendingClose(),true);
closing=nativeClose();assert.equal(closing.mac,'pending');
const other={...copy(backend.candidate),value:backend.candidate.text,editing:false};
docs.set(other.id,other);active=other.id;badge();
closing=nativeClose();assert.equal(closing.mac,'pending','hidden failed recovery remains pending');
assert.ok($('pending-list').children.some(node=>node.textContent.includes('保存失败')));
docs.delete(other.id);active=recovery.id;badge();mode.failSave=false;
assert.equal(await $('save-retry').onclick(),true);
assert.equal(requests.filter(request=>request.action==='save').length,2);
assert.equal(requests.filter(request=>request.action==='save').at(-1).save_mode,'version');
assert.equal(recovery.saveError,'');assert.equal(recovery.failedSaveOptions,undefined);
assert.equal(recovery.pendingSaveReadback,undefined);
assert.equal(documentSaveState(docs.get(active)).kind,'saved');
assert.equal(pendingClose(),false);
closing=nativeClose();assert.equal(closing.mac,'clean');
'''
        for native in (False, True):
            with self.subTest(native_compatibility_script=native):
                fixture.js(case, native=native, setup=setup)


if __name__ == '__main__':
    unittest.main()
