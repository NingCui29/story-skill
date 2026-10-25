"""Pause real shelf UI actions while another client replaces or moves a book."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_workbench as base
import test_workbench_library as library
import test_workbench_shelf_feedback as feedback
import test_workbench_shelf_transactions as transactions


class WorkbenchShelfActionIdentityTests(unittest.TestCase):
    setUp = library.WorkbenchLibraryTests.setUp
    start = library.WorkbenchLibraryTests.start
    serve = library.WorkbenchLibraryTests.serve
    request = library.WorkbenchLibraryTests.request
    post = library.WorkbenchLibraryTests.post

    def script(self, server, initial, body, delay_relink=False):
        module = ast.parse(Path(feedback.__file__).read_text())
        mock = next(node.value.value for node in ast.walk(module) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'mock' for target in node.targets))
        original = "const call=async(action,data={})=>{requests.push({action,data});calls++;if(failure)throw Error(failure);if(pending)return pending;return response;};"
        endpoint = {'port': server.server_port, 'origin': f'http://127.0.0.1:{server.server_port}',
                    'token': server.library_token}
        transport = 'const endpoint=' + json.dumps(endpoint) + ';const delayRelink=' + json.dumps(delay_relink) + ';' + r'''
const waitForHost=()=>new Promise(done=>{process.stdin.once('data',raw=>{process.stdin.pause();done(JSON.parse(raw.toString()));});process.stdin.resume();});
const call=async(action,data={})=>{requests.push({action,data});calls++;const value=await new Promise((resolve,reject)=>{
 const body=JSON.stringify(data),request=require('node:http').request({host:'127.0.0.1',port:endpoint.port,path:'/api/'+action,method:'POST',headers:{Host:'127.0.0.1:'+endpoint.port,'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),Origin:endpoint.origin,'X-Story-Token':endpoint.token}},response=>{
  let raw='';response.setEncoding('utf8');response.on('data',data=>raw+=data);response.on('end',()=>{try{const value=JSON.parse(raw);if(response.statusCode!==200)reject(Error(value.error+': '+value.message));else resolve(value);}catch(error){reject(error);}});
 });request.on('error',reject);request.end(body);
});if(delayRelink&&action==='relink-book'){console.log('READY');await waitForHost();await refresh();}return value;};
require('node:timers').setTimeout(()=>{console.error('temporary UI probe timed out');process.exit(2);},10000).unref();
'''
        self.assertIn(original, mock)
        management = self.w._library_management_script()
        source = transactions.fixture().source
        source += management[management.index('async function openRecoveryBook('):management.index('function showCreateBook(')]
        source += management[management.index('async function showBookService('):management.index('function showRelinkBook(')]
        source += r'''
const managerBody=new E(),openedURLs=[];E.prototype.click=function(){openedURLs.push(this.href);};
function openManager(title){manager.open=true;fields.length=0;actions.clear();managerBody.replaceChildren();return ++managerGeneration;}
function managementText(text){const node=new E('p');node.textContent=text;managerBody.append(node);return node;}
'''
        seed = 'const initial=' + json.dumps(initial, ensure_ascii=False) + ';'
        return mock.replace(original, transport) + source + '\n(async()=>{' + seed + body + \
            '\n})().catch(error=>{console.error(error);process.exitCode=1;});'

    def paused_js(self, source, while_paused):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        process = subprocess.Popen([node, '-e', source], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = process.stdout.readline().strip()
            if ready != 'READY':
                self.fail('UI probe failed before pause: ' + ready + process.stderr.read())
            while_paused()
            output, error = process.communicate('{}\n', timeout=15)
            self.assertEqual(process.returncode, 0, output + error)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def replacement_action(self, operation):
        path = self.root / 'same-path'
        base.story.Book.create(path, 'Original A', 'long')
        relative = Path('.story/drafts/workbench/自动恢复/same.txt')
        recovery = path / relative
        recovery.parent.mkdir(parents=True)
        recovery.write_text('original recovery')
        old_editor = self.serve(self.w.editor_server(path)) if operation == 'stop' else None
        server = self.start(books=[path])
        initial = self.post(server, 'books')[1]
        original = initial['books'][0]
        archive = self.root / 'should-not-exist.zip'
        prepare = {
            'backup': "showBookBackup(original);fields[0].value=" + json.dumps(str(archive)) + ';',
            'remove': 'showRemoveBook(original);',
            'open': "const openButton=$('books').children[0].children.find(node=>node.tagName==='BUTTON');",
            'recovery': "await showRecoveries(original);const descendants=node=>[node,...node.children.flatMap(descendants)];const row=descendants(managerBody).find(node=>node.className==='recovery-row');assert.ok(row);",
            'stop': "await showBookService(original);assert.ok(actions.has('停止本书编辑服务'));fields[0].checked=true;",
            'trash': 'await showTrashBook(original);fields[0].value=original.title;fields[1].checked=true;',
        }[operation]
        action = {
            'backup': "await assert.rejects(actions.get('创建并核验备份')(),/workbench_book_changed/);",
            'remove': "await assert.rejects(actions.get('移出书架')(),/workbench_book_changed/);assert.equal(undoRemoval,null);",
            'open': "await openButton.onclick();assert.equal(openedURLs.length,0);assert.ok($('message').textContent.includes('作品身份'));",
            'recovery': "await row.children[0].onclick();assert.equal(openedURLs.length,0);assert.ok(row.children[1].textContent.includes('作品身份'));",
            'stop': "await assert.rejects(actions.get('停止本书编辑服务')(),/workbench_book_changed/);",
            'trash': "await assert.rejects(actions.get('移入系统回收站')(),/workbench_book_changed/);",
        }[operation]
        body = 'render(initial);const original=initial.books[0];' + prepare + \
            "console.log('READY');await waitForHost();" + action
        replacement = {}

        def replace():
            self.assertEqual(self.post(server, 'remove-book', {'key': original['key']})[0], 200)
            if old_editor and sys.platform == 'win32':
                old_editor.shutdown()
                old_editor.test_thread.join(timeout=5)
                self.assertFalse(old_editor.test_thread.is_alive())
            path.rename(self.root / 'Original-A-preserved')
            base.story.Book.create(path, 'Replacement B', 'long')
            candidate = path / relative
            candidate.parent.mkdir(parents=True)
            candidate.write_text('replacement recovery')
            replacement.update(self.post(server, 'add-book', {'path': str(path)})[1]['books'][0])
            replacement['db_before'] = hashlib.sha256((path / '.story/state.sqlite3').read_bytes()).hexdigest()
            if operation in ('open', 'recovery', 'stop'):
                replacement['editor'] = self.serve(self.w.editor_server(path))

        self.paused_js(self.script(server, initial, body), replace)
        catalog = self.post(server, 'books')[1]
        self.assertEqual(catalog['books'][0]['book_id'], replacement['book_id'])
        self.assertEqual(len(catalog['books']), 1)
        self.assertFalse(archive.exists())
        self.assertEqual(hashlib.sha256((path / '.story/state.sqlite3').read_bytes()).hexdigest(), replacement['db_before'])
        self.assertEqual((self.root / 'Original-A-preserved' / relative).read_text(), 'original recovery')
        if replacement.get('editor'):
            self.assertTrue(replacement['editor'].test_thread.is_alive(), 'stale A action must never stop B')

    def test_old_backup_dialog_cannot_archive_replacement(self):
        self.replacement_action('backup')

    def test_old_remove_dialog_cannot_remove_or_mislabel_replacement(self):
        self.replacement_action('remove')

    def test_old_book_card_cannot_open_replacement(self):
        self.replacement_action('open')

    def test_old_recovery_row_cannot_open_same_path_in_replacement(self):
        self.replacement_action('recovery')

    def test_old_stop_dialog_cannot_stop_replacement_editor(self):
        self.replacement_action('stop')

    def test_old_trash_confirmation_still_cannot_recycle_replacement(self):
        self.replacement_action('trash')

    def test_all_key_actions_require_identity_and_global_recovery_rows_include_it(self):
        path = self.root / 'book'
        base.story.Book.create(path, 'One book', 'long')
        server = self.start(books=[path])
        entry = self.post(server, 'books')[1]['books'][0]
        editor = self.serve(self.w.editor_server(path))
        operations = {
            'open-book': {}, 'remove-book': {}, 'relink-book': {'path': str(self.root / 'missing')},
            'backup-book': {'path': str(self.root / 'unexpected.zip')}, 'book-service': {},
            'stop-book': {'saved': True}, 'recoveries': {}, 'trash-preview': {},
        }
        before = (self.state / 'library.json').read_bytes()
        for action, fields in operations.items():
            for expected in (None, 'another-book'):
                payload = {'key': entry['key'], **fields}
                if expected is not None:
                    payload['expected_book_id'] = expected
                with self.subTest(action=action, expected=expected):
                    status, result = self.post(server, action, payload, include_identity=False)
                    self.assertEqual(status, 409, result)
                    self.assertEqual(result['error'], 'workbench_book_changed')
        self.assertEqual((self.state / 'library.json').read_bytes(), before)
        self.assertTrue(editor.test_thread.is_alive())
        self.assertFalse((self.root / 'unexpected.zip').exists())
        self.assertEqual(self.post(server, 'recoveries')[1]['books'][0]['book_id'], entry['book_id'])
        preview = self.post(server, 'trash-preview', {'key': entry['key']})[1]
        status, result = self.post(server, 'trash-book', {
            'key': entry['key'], 'confirmation': preview['confirmation'], 'saved': False}, include_identity=False)
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error'], 'workbench_unsaved', 'the legacy identity-bearing confirmation remains supported')

    def test_late_relink_receipt_preserves_draft_at_the_latest_accepted_location(self):
        original = self.root / 'original'
        middle, latest = self.root / 'middle', self.root / 'latest'
        base.story.Book.create(original, 'One book', 'long')
        server = self.start(books=[original])
        initial = self.post(server, 'books')[1]
        original.rename(middle)
        body = 'render(initial);const original=initial.books[0];chooseStatus(original.key,\'completed\');showRelinkBook(original);fields[0].value=' + json.dumps(str(middle)) + r''';
managerBusy=true;await actions.get('核对并更新位置')();managerBusy=false;const current=shelfBooks[0];
assert.equal(statusSelect(current.key).value,'completed');assert.equal(shelfStatusDrafts.get(shelfStatusDraftKey(current)).value,'completed');assert.equal(shelfStatusDrafts.size,1);
assert.equal($('manager-feedback').dataset.state,'unknown');assert.ok($('manager-detail').textContent.includes('勿直接重复执行'));assert.equal(manager.open,true);
'''

        def move_again():
            entry = self.post(server, 'books')[1]['books'][0]
            middle.rename(latest)
            status, result = self.post(server, 'relink-book', {'key': entry['key'], 'path': str(latest)})
            self.assertEqual(status, 200, result)

        self.paused_js(self.script(server, initial, body, delay_relink=True), move_again)
        self.assertEqual(self.post(server, 'books')[1]['books'][0]['root'], str(latest))


if __name__ == '__main__':
    unittest.main()
