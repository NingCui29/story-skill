"""Native compatibility installs shipped handlers without touching buffers."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('editor_safety_payload_test', ROOT / 'scripts/editor_safety_payload.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class DesktopEditorSafetyTests(unittest.TestCase):
    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        harness = r"""
const assert=require('assert'),vm=require('vm');
function page(address='http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/'){
 const context=vm.createContext({URL,Map,console});
 vm.runInContext(`
 const nodes={save:{onclick:()=>{}},text:{},'current-book-root':{}};
 const document={getElementById:id=>nodes[id]||null};
 const docs=new Map();let active='original',reloading=false,sideEffects=0;
 const call=()=>{sideEffects++;throw Error('Unexpected service request');};
 const dirty=()=>{throw Error('Unexpected buffer inspection');};
 const recover=()=>{throw Error('Unexpected recovery write');};
 const badge=()=>{},note=()=>{},rememberView=()=>{},show=()=>{},catalog=()=>{};
 docs.set('original',Object.defineProperty({},'value',{get(){throw Error('Buffer text read at install');},set(){throw Error('Buffer text replaced at install');}}));
 window=globalThis;window.top=window;location=new URL(${JSON.stringify(address)});
 const originalHandler=nodes.save.onclick;
 `,context);
 return {context,run:()=>vm.runInContext(asset,context),read:code=>vm.runInContext(code,context)};
}
"""
        program = 'const asset=' + json.dumps(builder.editor_safety_payload()) + ';\n' + harness + test
        result = subprocess.run([node, '-'], input=program, encoding='utf-8', capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_legacy_inline_save_installs_without_reading_or_writing_buffers(self):
        self.js("const p=page();assert.equal(p.run().applied,true);assert.equal(p.read('sideEffects'),0);assert.equal(p.read('typeof saveDocument'),'function');assert.equal(p.read('nodes.save.onclick===originalHandler'),false);assert.equal(p.read('docs.size'),1);")

    def test_installation_is_idempotent_and_uses_the_same_tested_functions(self):
        self.js("const p=page();p.run();p.read('firstHandler=nodes.save.onclick;firstSave=saveDocument');assert.equal(p.run().applied,true);assert.ok(p.read('firstHandler===nodes.save.onclick && firstSave===saveDocument'));assert.equal(p.read('sideEffects'),0);")

    def test_external_nested_unknown_and_malformed_pages_remain_unchanged(self):
        self.js(r"""
for(const url of ['https://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/','http://localhost:12345/abcdefghijklmnopqrstuvwxyz012345/','http://127.0.0.1:12345/','http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/?next=1','http://name:pass@127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/']){
 const p=page(url);assert.equal(p.run().applied,false);assert.ok(p.read('nodes.save.onclick===originalHandler'));assert.equal(p.read('sideEffects'),0);
}
for(const mutate of ['window.top={}','delete nodes.text','delete nodes["current-book-root"]']){
 const p=page();p.read(mutate);assert.equal(p.run().applied,false);assert.ok(p.read('nodes.save.onclick===originalHandler'));
}
""")

    def test_build_includes_safety_asset_and_hash(self):
        source = (ROOT / 'scripts/build_desktop_macos.py').read_text()
        self.assertIn("(resources / 'editor-safety.js').write_text(editor_safety_payload(REPO)", source)
        self.assertIn("'editor_safety_sha256'", source)
        self.assertNotIn('__SAVE_DOCUMENT__', builder.editor_safety_payload())


if __name__ == '__main__':
    unittest.main()
