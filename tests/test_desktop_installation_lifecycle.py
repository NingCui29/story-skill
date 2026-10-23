"""Installation handoff regressions; all services and payloads are isolated fixtures."""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('installation_lifecycle', ROOT / 'desktop/windows/installation_lifecycle.py')
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)


class InstallationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.app = self.root / '工作台 空格'
        self.app.mkdir()
        self.state = self.root / 'isolated-state'
        self.state.mkdir()
        self.journal = self.root / 'transaction.json'
        self.registration = [['Inno Setup: App Path', str(self.app), 1], ['DisplayName', '写作工作台', 1]]
        post_move = patch.object(lifecycle, 'check_moved_payload')
        self.post_move = post_move.start()
        self.addCleanup(post_move.stop)
        for name in ('StoryWorkbench.exe', 'backend_runner.py', 'runtime/python.exe', 'Scripts/story.py', 'unins000.exe'):
            path = self.app / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(('old ' + name).encode())
        self.manifest = {'platform': 'windows-x86_64', 'client_version': '0.1.12', 'files': {
            p.relative_to(self.app).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.app.rglob('*') if p.is_file() and p.name != 'unins000.exe'}}
        self.write_manifest()

    def write_manifest(self):
        (self.app / 'build-manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def record(self, pid=1234, library=True):
        root = self.state if library else self.root / 'book'
        path = root / 'service.json' if library else root / '.story/workbench-service.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {'pid': pid, 'state_dir' if library else 'root': str(root), 'port': 12345, 'token': 't' * 43, 'instance': 'a' * 32}
        if library:
            record['mode'] = 'library'
        else:
            record['book_id'] = 'fixture-book'
            (self.state / 'library.json').write_text(json.dumps({'version': 1, 'books': [{'root': str(root)}]}))
        path.write_text(json.dumps(record))
        return path, record, root

    def process(self, pid=1234):
        return {pid: os.path.normcase(str(self.app / 'runtime/python.exe'))}

    def test_complete_legacy_identity_and_unknown_author_files_are_preserved(self):
        author = self.app / '用户误放的文件.txt'
        author.write_text('preserve me')
        verified = lifecycle.verify_install(self.app, self.registration)
        transaction = lifecycle.prepare_upgrade(self.app, self.journal, self.registration, verified)
        self.assertFalse(self.app.exists())
        self.assertEqual((Path(transaction['backup']) / author.name).read_text(), 'preserve me')
        self.assertTrue(list(self.root.glob('*.migration-*.json')))

    def test_unregistered_or_changed_payload_never_moves(self):
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.verify_install(self.app, [])
        (self.app / 'runtime/python.exe').write_bytes(b'changed')
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.verify_install(self.app, self.registration)
        self.assertTrue(self.app.exists())
        self.assertFalse(self.journal.exists())

    def test_manifest_escape_and_linked_unknown_file_are_rejected(self):
        self.manifest['files']['../outside'] = 'a' * 64
        self.write_manifest()
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.verify_install(self.app, self.registration)
        del self.manifest['files']['../outside']
        self.write_manifest()
        try:
            (self.app / 'linked').symlink_to(self.state, target_is_directory=True)
        except OSError as error:
            if sys.platform == 'win32':
                self.skipTest('Windows fixture user cannot create symbolic links: ' + str(error))
            raise
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.verify_install(self.app, self.registration)

    def test_directory_rename_failure_leaves_original_intact(self):
        verified = lifecycle.verify_install(self.app, self.registration)
        with patch.object(lifecycle.os, 'rename', side_effect=PermissionError('in use')):
            with self.assertRaises(PermissionError):
                lifecycle.prepare_upgrade(self.app, self.journal, self.registration, verified)
        restored = []
        result = lifecycle.finish_upgrade(self.journal, registry_restore=restored.append)
        self.assertEqual(result['state'], 'restored')
        self.assertTrue((self.app / 'StoryWorkbench.exe').is_file())

    def test_failed_install_restores_whole_old_tree_registry_and_shortcut(self):
        shortcut = self.root / 'Desktop' / '写作工作台.lnk'
        shortcut.parent.mkdir()
        shortcut.write_bytes(b'old shortcut')
        original = {str(p.relative_to(self.app)): p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        verified = lifecycle.verify_install(self.app, self.registration)
        lifecycle.prepare_upgrade(self.app, self.journal, self.registration, verified, [shortcut])
        self.app.mkdir()
        (self.app / 'partial-new-file').write_bytes(b'keep incomplete')
        shortcut.unlink()
        restored = []
        result = lifecycle.finish_upgrade(self.journal, registry_restore=restored.append)
        self.assertEqual(result['state'], 'restored')
        self.assertEqual(restored, [self.registration])
        self.assertEqual(shortcut.read_bytes(), b'old shortcut')
        self.assertEqual({str(p.relative_to(self.app)): p.read_bytes() for p in self.app.rglob('*') if p.is_file()}, original)
        self.assertEqual((Path(result['incomplete']) / 'partial-new-file').read_bytes(), b'keep incomplete')
        lifecycle.finish_upgrade(self.journal, registry_restore=lambda _: self.fail('must be idempotent'))

    def test_restore_can_retry_after_registration_failure(self):
        verified = lifecycle.verify_install(self.app, self.registration)
        lifecycle.prepare_upgrade(self.app, self.journal, self.registration, verified)
        with self.assertRaises(OSError):
            lifecycle.finish_upgrade(self.journal, registry_restore=lambda _: (_ for _ in ()).throw(OSError('registry busy')))
        restored = []
        self.assertEqual(lifecycle.finish_upgrade(self.journal, registry_restore=restored.append)['state'], 'restored')
        self.assertEqual(restored, [self.registration])

    def test_changed_backup_identity_is_not_restored(self):
        verified = lifecycle.verify_install(self.app, self.registration)
        transaction = lifecycle.prepare_upgrade(self.app, self.journal, self.registration, verified)
        backup = Path(transaction['backup'])
        os.rename(backup, self.root / 'real-backup')
        backup.mkdir()
        shutil.copy2(self.root / 'real-backup/build-manifest.json', backup / 'build-manifest.json')
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.finish_upgrade(self.journal, registry_restore=lambda _: self.fail('registry must stay untouched'))
        self.assertTrue((self.root / 'real-backup/StoryWorkbench.exe').is_file())

    def test_commit_keeps_backup_and_does_not_restore_it(self):
        verified = lifecycle.verify_install(self.app, self.registration)
        transaction = lifecycle.prepare_upgrade(self.app, self.journal, self.registration, verified)
        self.app.mkdir()
        (self.app / 'build-manifest.json').write_text('{}')
        self.assertEqual(lifecycle.finish_upgrade(self.journal, commit=True)['state'], 'committed')
        self.assertTrue(Path(transaction['backup']).is_dir())
        self.assertEqual(lifecycle.finish_upgrade(self.journal)['state'], 'committed')

    def test_process_starting_during_directory_move_restores_old_tree_before_copy(self):
        self.post_move.side_effect = lifecycle.LifecycleError('late legacy launcher')
        verified = lifecycle.verify_install(self.app, self.registration)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.prepare_upgrade(self.app, self.journal, self.registration, verified)
        self.assertTrue((self.app / 'runtime/python.exe').is_file())
        self.assertEqual(lifecycle._json(self.journal)['state'], 'restored')
        self.assertFalse(list(self.root.glob('*.previous-*')))

    def test_running_authenticated_service_requires_save_confirmation(self):
        self.record()
        calls = []
        with self.assertRaises(lifecycle.SaveConfirmationRequired):
            lifecycle.quiesce(self.app, self.state, process_list=lambda _: self.process(),
                request=lambda *a, **kw: calls.append(kw.get('stop', False)), lock_probe=lambda *a, **k: self.fail('still running'))
        self.assertEqual(calls, [False])

    def test_unknown_process_blocks_before_any_stop(self):
        self.record()
        calls = []
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.quiesce(self.app, self.state, saved=True,
                process_list=lambda _: {**self.process(), 999: str(self.app / 'unexpected.exe')},
                request=lambda *a, **kw: calls.append(kw.get('stop', False)))
        self.assertEqual(calls, [False])

    def test_library_and_book_both_authenticate_before_saved_stop(self):
        self.record(1234)
        self.record(1235, library=False)
        running = {**self.process(1234), **self.process(1235)}
        events = []
        def request(record, root, library, stop=False):
            events.append(('stop' if stop else 'auth', record['pid']))
            if stop:
                running.pop(record['pid'])
        lifecycle.quiesce(self.app, self.state, saved=True, process_list=lambda _: dict(running),
                          request=request, lock_probe=lambda *a, **k: events.append(('locks', 0)))
        self.assertEqual(events, [('auth', 1234), ('auth', 1235), ('stop', 1234), ('stop', 1235), ('locks', 0)])

    def test_changed_record_after_auth_never_receives_stop(self):
        path, record, _ = self.record()
        def request(*args, **kwargs):
            self.assertFalse(kwargs.get('stop'))
            path.write_text(json.dumps({**record, 'instance': 'b' * 32}))
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.quiesce(self.app, self.state, saved=True, process_list=lambda _: self.process(), request=request)

    def test_stopped_record_does_not_hide_live_process(self):
        path, record, _ = self.record()
        path.write_text(json.dumps({**record, 'stopped': True}))
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.quiesce(self.app, self.state, saved=True, process_list=lambda _: self.process(),
                              request=lambda *a, **k: self.fail('stale record must not authenticate'))

    def test_stop_timeout_is_bounded_and_never_probes_or_kills(self):
        self.record()
        ticks = [0]
        calls = []
        def sleep(seconds):
            ticks[0] += seconds
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.quiesce(self.app, self.state, saved=True, process_list=lambda _: self.process(),
                request=lambda *a, **k: calls.append(k.get('stop', False)), timeout=0.3,
                clock=lambda: ticks[0], sleep=sleep, lock_probe=lambda *a, **k: self.fail('busy'))
        self.assertEqual(calls, [False, True])
        self.assertLessEqual(ticks[0], 0.4)

    def test_uninstaller_process_is_excluded_but_other_executables_are_not(self):
        self.record()
        running = {**self.process(), 55: os.path.normcase(str(self.app / 'unins000.exe'))}
        def request(record, root, library, stop=False):
            if stop:
                running.pop(record['pid'])
        locks = []
        lifecycle.quiesce(self.app, self.state, saved=True, uninstall=True,
            process_list=lambda _: dict(running), request=request, lock_probe=lambda *a, **k: locks.append(k))
        self.assertEqual(locks, [{'uninstall': True}])

    def test_http_identity_and_stop_receipt_checked_against_authenticated_record(self):
        _, record, root = self.record()
        requests = []
        response_identity = {'ok': True, 'running': True, 'mode': 'library', 'state_dir': str(root),
                             'instance': record['instance'], 'pid': record['pid']}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body, self.headers['X-Story-Token']))
                result = {**response_identity, 'stop_requested': self.path == '/api/stop'}
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *_):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            record['port'] = server.server_port
            lifecycle._service_request(record, root, True)
            lifecycle._service_request(record, root, True, stop=True)
            response_identity['pid'] += 1
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle._service_request(record, root, True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.assertEqual(requests[0], ('/api/session', {'instance': record['instance'], 'saved': False}, record['token']))
        self.assertEqual(requests[1][1]['saved'], True)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows native process and file-sharing probe')
    def test_windows_native_process_inventory_and_locked_payload(self):
        # A child owns a fixture payload handle without FILE_SHARE_DELETE. This
        # is real Win32 sharing behavior, not a mocked PermissionError.
        ready = self.root / 'ready'
        release = self.root / 'release'
        target = self.app / 'backend_runner.py'
        program = """
import ctypes, pathlib, time
from ctypes import wintypes
k=ctypes.WinDLL('kernel32', use_last_error=True)
k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
k.CreateFileW.restype=wintypes.HANDLE
k.CloseHandle.argtypes=[wintypes.HANDLE]
h=k.CreateFileW(TARGET,0x80000000,1,None,3,0,None)
assert h != ctypes.c_void_p(-1).value
pathlib.Path(READY).write_text('ready')
try:
 deadline=time.monotonic()+20
 while not pathlib.Path(RELEASE).exists() and time.monotonic()<deadline: time.sleep(.02)
finally: k.CloseHandle(h)
"""
        script = 'TARGET=' + repr(str(target)) + '\nREADY=' + repr(str(ready)) + '\nRELEASE=' + repr(str(release)) + '\n' + program
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists(), 'fixture child did not acquire lock')
            self.assertIn(child.pid, lifecycle.processes_in(Path(sys.executable).parent))
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.probe_file_locks(self.app)
        finally:
            release.write_text('release')
            output, errors = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, errors.decode(errors='replace'))
        lifecycle.probe_file_locks(self.app)

    def test_real_backend_service_requires_saved_confirmation_then_exits_normally(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        child = subprocess.Popen([sys.executable, '-B', str(ROOT / 'skills/story-skill/scripts/story.py'),
            'workbench-library-serve', '--state-dir', str(self.state), '--port', str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        record = None
        try:
            deadline = time.monotonic() + 15
            while child.poll() is None and time.monotonic() < deadline:
                try:
                    record = lifecycle._json(self.state / 'service.json', 8192)
                    lifecycle._service_request(record, self.state, True)
                    break
                except (OSError, ValueError, lifecycle.LifecycleError):
                    time.sleep(.05)
            self.assertIsNotNone(record)
            self.assertNotIn('root', record)  # Real shelf records use state_dir.
            self.assertEqual(record['pid'], child.pid)
            process_list = lambda _: self.process(child.pid) if child.poll() is None else {}
            with self.assertRaises(lifecycle.SaveConfirmationRequired):
                lifecycle.quiesce(self.app, self.state, process_list=process_list, lock_probe=lambda *a, **k: None)
            self.assertIsNone(child.poll())
            lifecycle.quiesce(self.app, self.state, saved=True, process_list=process_list,
                              lock_probe=lambda *a, **k: None)
            child.wait(timeout=10)
            self.assertEqual(child.returncode, 0)
            self.assertTrue(lifecycle._json(self.state / 'service.json')['stopped'])
        finally:
            if child.poll() is None:
                if record is not None:
                    lifecycle._service_request(record, self.state, True, stop=True)
                else:
                    # This is our own isolated test child, never an author service.
                    child.terminate()
            _, stderr = child.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
