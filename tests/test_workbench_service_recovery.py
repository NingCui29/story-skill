"""Only a vanished POSIX PID and unchanged, idle lease prove a stale editor exit."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_workbench as base


@unittest.skipUnless(os.name == 'posix', 'Automatic dead-service recovery uses POSIX read-only probes')
class WorkbenchServiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-service-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / '作品'
        self.story = base.story
        self.w = self.story.workbench
        self.story.Book.create(self.root, '需要打开的作品', 'long')
        self.entry = self.w._library_entry(self.root)
        self.record_path = self.root / '.story/workbench-service.json'
        self.lock_path = self.root / '.story/workbench-server.lock'

    def record(self, **overrides):
        # A legitimate older record has neither book_id nor runtime_sha256.
        record = {'root': str(self.root), 'port': 54321, 'pid': 999999,
                  'token': 'x' * 43, 'instance': '1' * 32, **overrides}
        raw = json.dumps(record).encode('utf-8')
        self.record_path.write_bytes(raw)
        self.lock_path.write_bytes(b'0')
        return record, raw

    def disconnected(self):
        return SimpleNamespace(request=Mock(side_effect=ConnectionRefusedError('isolated refused port')),
                               close=lambda: None)

    def status(self, pid_effect=ProcessLookupError()):
        with patch.object(self.w.http.client, 'HTTPConnection', return_value=self.disconnected()), \
                patch.object(self.w.os, 'kill', side_effect=pid_effect):
            return self.w._service_request(self.root)

    def contents(self):
        return {str(path.relative_to(self.root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                                  path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file() and not path.is_symlink()}

    def test_dead_legacy_record_with_idle_lock_is_confirmed_without_any_writes(self):
        record, raw = self.record()
        before = self.contents()
        state = self.status()
        self.assertIs(state['running'], False)
        self.assertTrue(state['exit_verified'])
        self.assertEqual(state['record_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.contents(), before)
        self.assertNotIn('book_id', record)
        self.assertNotIn('runtime_sha256', record)

    def test_live_or_reused_pid_and_permission_denied_do_not_prove_exit(self):
        self.record()
        before = self.contents()
        for effect in (None, PermissionError('isolated access denied'), OSError('unknown PID state')):
            with self.subTest(effect=effect):
                state = self.status(effect)
                self.assertIsNone(state['running'])
                self.assertNotIn('exit_verified', state)
        self.assertEqual(self.contents(), before)

    def test_pid_appearing_during_lease_check_invalidates_exit(self):
        self.record()
        with patch.object(self.w.http.client, 'HTTPConnection', return_value=self.disconnected()), \
                patch.object(self.w.os, 'kill', side_effect=[ProcessLookupError(), None]):
            state = self.w._service_request(self.root)
        self.assertIsNone(state['running'])

    def test_missing_empty_symlink_or_hardlinked_lock_does_not_prove_exit(self):
        for kind in ('missing', 'empty', 'symlink', 'hardlink'):
            with self.subTest(kind=kind):
                self.record()
                self.lock_path.unlink()
                target = self.root / ('锁目标-' + kind)
                if kind == 'empty':
                    self.lock_path.write_bytes(b'')
                elif kind in ('symlink', 'hardlink'):
                    target.write_bytes(b'0')
                    if kind == 'symlink':
                        self.lock_path.symlink_to(target)
                    else:
                        os.link(target, self.lock_path)
                state = self.status()
                self.assertIsNone(state['running'])
                self.assertEqual(self.lock_path.exists(), kind != 'missing')
                if self.lock_path.exists():
                    self.lock_path.unlink()

    def test_live_lease_blocks_recovery_even_if_pid_record_is_missing(self):
        self.record()
        before = self.contents()
        with self.w._editor_lease(self.root):
            state = self.status()
        self.assertIsNone(state['running'])
        self.assertEqual(self.contents(), before)

    def test_lock_replacement_during_either_record_read_invalidates_exit(self):
        original = self.w._author_read
        for replace_on in (1, 2):
            with self.subTest(replace_on=replace_on):
                record, raw = self.record()
                seen = 0

                def replace_lock(root, relative, *args):
                    nonlocal seen
                    value = original(root, relative, *args)
                    seen += 1
                    if seen == replace_on:
                        self.lock_path.rename(self.root / ('旧锁-' + str(replace_on)))
                        self.lock_path.write_bytes(b'0')
                    return value

                with patch.object(self.w.os, 'kill', side_effect=ProcessLookupError()), \
                        patch.object(self.w, '_author_read', side_effect=replace_lock):
                    self.assertFalse(self.w._editor_exit_verified(self.root, record, raw))

    def test_lock_content_change_during_verification_invalidates_exit(self):
        record, raw = self.record()
        original = self.w._author_read

        def change_lock(root, relative, *args):
            self.lock_path.write_bytes(b'changed')
            return original(root, relative, *args)

        with patch.object(self.w.os, 'kill', side_effect=ProcessLookupError()), \
                patch.object(self.w, '_author_read', side_effect=change_lock):
            self.assertFalse(self.w._editor_exit_verified(self.root, record, raw))

    def test_record_change_during_verification_invalidates_exit_and_is_preserved(self):
        record, raw = self.record()
        original = self.w._author_read
        replacement = json.dumps({**record, 'instance': '2' * 32}).encode()

        def change_record(root, relative, *args):
            self.record_path.write_bytes(replacement)
            return original(root, relative, *args)

        with patch.object(self.w.os, 'kill', side_effect=ProcessLookupError()), \
                patch.object(self.w, '_author_read', side_effect=change_record):
            self.assertFalse(self.w._editor_exit_verified(self.root, record, raw))
        self.assertTrue(self.record_path.read_bytes() == replacement)

    def test_permission_failure_on_existing_lock_is_unknown(self):
        record, raw = self.record()
        with patch.object(self.w.os, 'kill', side_effect=ProcessLookupError()), \
                patch.object(self.w.api, '_bound_reader', side_effect=PermissionError('isolated denied lock')):
            self.assertFalse(self.w._editor_exit_verified(self.root, record, raw))

    def test_invalid_pid_is_never_probed(self):
        for pid in (None, 0, -1, True, 2**31, '999999'):
            with self.subTest(pid=pid):
                record, raw = self.record(pid=pid)
                with patch.object(self.w.os, 'kill', side_effect=AssertionError('must not probe invalid pid')):
                    self.assertFalse(self.w._editor_exit_verified(self.root, record, raw))

    def test_windows_never_calls_os_kill_zero(self):
        record, raw = self.record()
        with patch.object(self.w.os, 'name', 'nt'), \
                patch.object(self.w.os, 'kill', side_effect=AssertionError('Windows kill zero can terminate')):
            self.assertFalse(self.w._editor_exit_verified(self.root, record, raw))

    def test_bad_record_remains_invalid_without_exit_probe(self):
        self.record_path.write_text('{bad json', encoding='utf-8')
        with patch.object(self.w, '_editor_exit_verified', side_effect=AssertionError('invalid record')):
            with self.assertRaises(self.story.StoryError) as error:
                self.w._service_request(self.root)
        self.assertEqual(error.exception.code, 'workbench_service_invalid')

    def test_http_mismatch_or_bad_json_does_not_use_pid_recovery(self):
        self.record()
        for status, raw in ((403, b'{}'), (200, b'{}'), (200, b'not json')):
            with self.subTest(status=status, malformed=raw != b'{}'):
                response = SimpleNamespace(status=status, read=lambda _limit: raw)
                connection = SimpleNamespace(request=lambda *args, **kwargs: None,
                                             getresponse=lambda: response, close=lambda: None)
                with patch.object(self.w.http.client, 'HTTPConnection', return_value=connection), \
                        patch.object(self.w, '_editor_exit_verified', side_effect=AssertionError('HTTP mismatch')):
                    with self.assertRaises(self.story.StoryError) as error:
                        self.w._service_request(self.root)
                self.assertEqual(error.exception.code, 'workbench_service_mismatch')

    def test_verified_exit_allows_original_start_without_touching_record_itself(self):
        _, raw = self.record()
        before = self.contents()
        proof = {'running': False, 'exit_verified': True, 'record_sha256': hashlib.sha256(raw).hexdigest()}
        ready = {'running': True, 'url': 'http://127.0.0.1:1234/isolated/', 'outdated': False,
                 'book_id': self.entry['book_id']}
        with patch.object(self.w, '_service_request', side_effect=[proof, ready]), \
                patch.object(self.w.subprocess, 'Popen', return_value=SimpleNamespace(poll=lambda: None)) as spawn:
            result = self.w._library_open({self.entry['key']: self.entry}, self.entry['key'])
        self.assertTrue(result['ok'])
        spawn.assert_called_once()
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index('--expected-book-id') + 1], self.entry['book_id'])
        self.assertEqual(self.contents(), before)

    def test_record_changed_after_exit_proof_blocks_spawn(self):
        record, raw = self.record()

        def change_then_return_proof(_root):
            self.record_path.write_text(json.dumps({**record, 'instance': '2' * 32}))
            return {'running': False, 'exit_verified': True, 'record_sha256': hashlib.sha256(raw).hexdigest()}

        with patch.object(self.w, '_service_request', side_effect=change_then_return_proof), \
                patch.object(self.w.subprocess, 'Popen') as spawn:
            with self.assertRaises(self.story.StoryError) as error:
                self.w._library_open({self.entry['key']: self.entry}, self.entry['key'])
        self.assertEqual(error.exception.code, 'workbench_unreachable')
        spawn.assert_not_called()

    def test_unknown_service_and_truthy_nonboolean_stopped_flag_do_not_spawn(self):
        for stopped in (False, 'false', 1):
            with self.subTest(stopped=stopped):
                self.record(stopped=stopped)
                with patch.object(self.w, '_service_request', return_value={'running': None}), \
                        patch.object(self.w.subprocess, 'Popen') as spawn:
                    with self.assertRaises(self.story.StoryError) as error:
                        self.w._library_open({self.entry['key']: self.entry}, self.entry['key'])
                self.assertEqual(error.exception.code, 'workbench_unreachable')
                spawn.assert_not_called()

    def test_book_replacement_during_status_check_blocks_spawn_even_with_same_record(self):
        _, raw = self.record()
        replacement = self.root.parent / '另一本书'
        self.story.Book.create(replacement, '需要打开的作品', 'short')
        original_state = hashlib.sha256((replacement / '.story/state.sqlite3').read_bytes()).hexdigest()

        def replace_then_return_proof(_root):
            self.root.rename(self.root.parent / '原作品移走')
            replacement.rename(self.root)
            self.record_path.write_bytes(raw)
            return {'running': False, 'exit_verified': True, 'record_sha256': hashlib.sha256(raw).hexdigest()}

        with patch.object(self.w, '_service_request', side_effect=replace_then_return_proof), \
                patch.object(self.w.subprocess, 'Popen') as spawn:
            with self.assertRaises(self.story.StoryError) as error:
                self.w._library_open({self.entry['key']: self.entry}, self.entry['key'])
        self.assertEqual(error.exception.code, 'workbench_book_changed')
        spawn.assert_not_called()
        self.assertEqual(hashlib.sha256((self.root / '.story/state.sqlite3').read_bytes()).hexdigest(), original_state)

    def test_replacement_after_parent_check_never_returns_another_books_service(self):
        record, _ = self.record()
        replacement = self.root.parent / '子进程启动前换入的作品'
        self.story.Book.create(replacement, '需要打开的作品', 'short')
        replacement_id = self.w._library_entry(replacement)['book_id']
        spawned = False

        def connection(*args, **kwargs):
            def request(*args, **kwargs):
                if not spawned:
                    raise ConnectionRefusedError('isolated dead service')

            def response():
                raw = json.dumps({**record, 'instance': '2' * 32, 'book_id': replacement_id,
                                  'running': True, 'runtime_sha256': self.w._loaded_runtime_sha256}).encode()
                return SimpleNamespace(status=200, read=lambda _limit: raw)

            return SimpleNamespace(request=request, getresponse=response, close=lambda: None)

        def launch(command, **kwargs):
            nonlocal spawned
            self.root.rename(self.root.parent / '原作品移走')
            replacement.rename(self.root)
            # Model the child's first read after the parent's final check. The
            # service reports its actual replacement ID, not the selected ID.
            self.assertEqual(self.w._editor_packet(self.root)['book']['id'], replacement_id)
            self.record_path.write_text(json.dumps({**record, 'instance': '2' * 32,
                                                   'book_id': replacement_id}))
            spawned = True
            return SimpleNamespace(poll=lambda: None)

        with patch.object(self.w.http.client, 'HTTPConnection', side_effect=connection), \
                patch.object(self.w.os, 'kill', side_effect=ProcessLookupError()), \
                patch.object(self.w.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(self.story.StoryError) as error:
                self.w._library_open({self.entry['key']: self.entry}, self.entry['key'])
        self.assertEqual(error.exception.code, 'workbench_book_changed')
        self.assertTrue(spawned)

    def test_child_cli_rejects_unexpected_book_before_creating_service_files(self):
        self.root.rename(self.root.parent / '原作品移走')
        self.story.Book.create(self.root, '需要打开的作品', 'short')
        for existing_record in (False, True):
            with self.subTest(existing_record=existing_record):
                if existing_record:
                    self.record()
                    self.lock_path.unlink()
                before = self.contents()
                result = subprocess.run([sys.executable, '-B', str(base.TOOL), 'workbench-serve',
                                         '--book', str(self.root), '--expected-book-id', self.entry['book_id']],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('workbench_book_changed', result.stdout + result.stderr)
                self.assertEqual(self.contents(), before)
                self.assertEqual(self.record_path.exists(), existing_record)
                self.assertFalse(self.lock_path.exists())

    def test_child_rechecks_expected_book_when_initial_packet_reads_replacement(self):
        original_packet = self.w._editor_packet
        before = {}

        def replace_before_packet(*args, **kwargs):
            self.root.rename(self.root.parent / '原作品移走')
            self.story.Book.create(self.root, '需要打开的作品', 'short')
            before.update(self.contents())
            return original_packet(*args, **kwargs)

        with patch.object(self.w, '_editor_packet', side_effect=replace_before_packet):
            with self.assertRaises(self.story.StoryError) as error:
                self.w.editor_server(self.root, expected_book_id=self.entry['book_id'])
        self.assertEqual(error.exception.code, 'workbench_book_changed')
        self.assertEqual(self.contents(), before)
        self.assertFalse(self.record_path.exists())
        self.assertFalse(self.lock_path.exists())

    def test_library_requires_selected_identity_in_reused_and_started_service(self):
        for stage in ('reuse', 'start'):
            for reported_id in (None, 'another-book'):
                with self.subTest(stage=stage, reported_id=reported_id):
                    _, raw = self.record()
                    proof = {'running': False, 'exit_verified': True,
                             'record_sha256': hashlib.sha256(raw).hexdigest()}
                    ready = {'running': True, 'url': 'http://127.0.0.1:1234/isolated/'}
                    if reported_id is not None:
                        ready['book_id'] = reported_id
                    states = [ready] if stage == 'reuse' else [proof, ready]
                    before = self.contents()
                    with patch.object(self.w, '_service_request', side_effect=states), \
                            patch.object(self.w.subprocess, 'Popen',
                                         return_value=SimpleNamespace(poll=lambda: None)) as spawn:
                        with self.assertRaises(self.story.StoryError) as error:
                            self.w._library_open({self.entry['key']: self.entry}, self.entry['key'])
                    self.assertEqual(error.exception.code, 'workbench_book_changed')
                    self.assertEqual(spawn.call_count, stage == 'start')
                    self.assertEqual(self.contents(), before)

    def test_actual_abnormal_exit_can_restart_through_library_open_without_book_writes(self):
        program = '''import importlib.util,sys
spec=importlib.util.spec_from_file_location('isolated_dead_editor',sys.argv[1])
story=importlib.util.module_from_spec(spec);spec.loader.exec_module(story)
server=story.workbench.editor_server(sys.argv[2]);print('ready',flush=True);server.serve_forever()
'''
        child = subprocess.Popen([sys.executable, '-B', '-u', '-c', program, str(base.TOOL), str(self.root)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children = []

        def cleanup():
            for process in children:
                if process.poll() is None:
                    state = self.w._service_request(self.root)
                    if state.get('running') is True and state.get('pid') == process.pid:
                        self.w._service_request(self.root, 'stop', True)
                    else:
                        process.terminate()  # Only a process this isolated test created.
                    process.wait(timeout=5)
            if child.poll() is None:
                child.terminate(); child.wait(timeout=5)
            child.stdout.close(); child.stderr.close()

        self.addCleanup(cleanup)
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        child.terminate(); child.wait(timeout=5)
        old_record = json.loads(self.record_path.read_bytes())
        old_record.pop('book_id', None)
        old_record.pop('runtime_sha256', None)
        stale_raw = json.dumps(old_record).encode()
        self.record_path.write_bytes(stale_raw)
        original_state = hashlib.sha256((self.root / '.story/state.sqlite3').read_bytes()).hexdigest()
        status = self.w._service_request(self.root)
        self.assertFalse(status['running'])
        self.assertTrue(status['exit_verified'])
        original_spawn = subprocess.Popen

        def tracked_spawn(*args, **kwargs):
            process = original_spawn(*args, **kwargs)
            children.append(process)
            return process

        with patch.object(self.w.subprocess, 'Popen', side_effect=tracked_spawn):
            opened = self.w._library_open({self.entry['key']: self.entry}, self.entry['key'])
        self.assertTrue(opened['ok'])
        self.assertEqual(len(children), 1)
        self.assertTrue(self.w._service_request(self.root)['running'])
        self.assertNotEqual(json.loads(self.record_path.read_bytes())['instance'], old_record['instance'])
        self.assertTrue(any(path.read_bytes() == stale_raw
                            for path in (self.root / '.story/.workbench-backups').rglob('service.json')))
        self.assertEqual(hashlib.sha256((self.root / '.story/state.sqlite3').read_bytes()).hexdigest(), original_state)

    def test_open_failures_name_selected_book_in_shelf_and_editor(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js required for UI failure attribution')
        packet = self.w._editor_packet(self.root)
        editor = self.w._editor_page(packet, {}, 'isolated').decode()
        editor_handler = editor[editor.index(" $('book-open').onclick=async()=>"):
                                editor.index('\n loadBooks();')]
        shelf = self.w._library_page('isolated').decode()
        shelf_handler = shelf[shelf.index('open.onclick=async()=>'):shelf.index(';row.append(title,time,genres,path,open)')]
        script = r'''
const assert=require('assert'),elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'selected',textContent:''});return elements.get(id);};
const bookChoices=[{key:'selected',title:'选择的作品'}],bookName=book=>book.title;
const call=async()=>{throw Error('无法确认服务');};
''' + editor_handler + r'''
const b={key:'shelf',title:'书架选择作品',available:true},open={disabled:false};
''' + shelf_handler + r''';
(async()=>{await $('book-open').onclick();assert.ok($('book-hint').textContent.includes('选择的作品'));
await open.onclick();assert.ok($('message').textContent.includes('书架选择作品'));assert.equal(open.disabled,false);
})().catch(error=>{console.error(error);process.exit(1);});
'''
        result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
