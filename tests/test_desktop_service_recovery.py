"""Desktop restart observes process exit and an unchanged idle service lease."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import test_workbench as base

SPEC = importlib.util.spec_from_file_location('desktop_recovery_runner', base.ROOT / 'desktop/macos/backend_runner.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class DesktopServiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='desktop-exit-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name).resolve() / '书架'
        self.state.mkdir()
        self.w = base.story.workbench
        child = subprocess.Popen([sys.executable, '-B', '-c', 'pass'])
        child.wait(timeout=10)
        self.dead_pid = child.pid
        self.record = {'mode': 'library', 'state_dir': str(self.state), 'port': 54321,
                       'token': 'x' * 43, 'instance': '1' * 32, 'pid': child.pid}
        self.raw = json.dumps(self.record).encode()
        (self.state / 'service.json').write_bytes(self.raw)
        (self.state / 'server.lock').write_bytes(b'0')

    def offline_status(self):
        connection = Mock()
        connection.request.side_effect = ConnectionRefusedError('isolated offline service')
        with patch.object(self.w.http.client, 'HTTPConnection', return_value=connection):
            return self.w._library_service_request(self.state)

    def test_dead_process_idle_lease_proves_exit_without_changing_record_or_lock(self):
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.state.iterdir()}
        result = self.offline_status()
        self.assertIs(result['running'], False)
        self.assertTrue(result['exit_verified'])
        self.assertEqual(result['record_sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.state.iterdir()})

    def test_live_pid_or_owned_lock_never_permits_restart(self):
        with patch.object(self.w, '_recorded_process_exited', return_value=False):
            self.assertIsNone(self.offline_status()['running'])
        with self.w._library_lease(self.state):
            self.assertIsNone(self.offline_status()['running'])

    def test_missing_or_empty_lock_does_not_prove_exit(self):
        (self.state / 'server.lock').unlink()
        self.assertIsNone(self.offline_status()['running'])
        self.assertFalse((self.state / 'server.lock').exists())
        (self.state / 'server.lock').write_bytes(b'')
        self.assertIsNone(self.offline_status()['running'])

    def test_changed_record_after_exit_proof_blocks_launcher(self):
        proof = {'running': False, 'exit_verified': True, 'record_sha256': hashlib.sha256(self.raw).hexdigest()}
        (self.state / 'service.json').write_text(json.dumps({**self.record, 'instance': '2' * 32}))
        with patch.object(self.w, '_library_service_request', return_value=proof), patch.object(runner.subprocess, 'Popen') as spawn:
            with self.assertRaises(runner.LauncherError) as error:
                runner.ensure_bookshelf(base.story, base.TOOL, self.state)
        self.assertEqual(error.exception.code, 'client_service_unconfirmed')
        spawn.assert_not_called()

    def test_http_identity_failure_cannot_be_downgraded_to_dead_service(self):
        for status, raw in ((403, b'{}'), (200, b'not json'), (200, b'{}')):
            connection = Mock()
            connection.getresponse.return_value = Mock(status=status, read=lambda _: raw)
            with patch.object(self.w.http.client, 'HTTPConnection', return_value=connection), \
                    patch.object(self.w, '_service_exit_verified', side_effect=AssertionError('identity mismatch')):
                with self.assertRaises(base.story.StoryError) as error:
                    self.w._library_service_request(self.state)
                self.assertEqual(error.exception.code, 'workbench_service_mismatch')

    def test_process_probe_is_read_only_on_windows_and_rejects_live_process(self):
        if os.name == 'nt':
            with patch.object(os, 'kill', side_effect=AssertionError('must not signal Windows PID')):
                self.assertFalse(self.w._recorded_process_exited(os.getpid()))
                self.assertTrue(self.w._recorded_process_exited(self.dead_pid))
        else:
            self.assertFalse(self.w._recorded_process_exited(os.getpid()))
            self.assertTrue(self.w._recorded_process_exited(self.dead_pid))

    def test_actual_abrupt_shelf_exit_restarts_and_preserves_registry(self):
        # Only terminate child processes created by this isolated test.
        (self.state / 'service.json').unlink()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        command = [sys.executable, '-B', str(base.TOOL), 'workbench-library-serve',
                   '--state-dir', str(self.state), '--port', str(port)]
        child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        owned = [child]
        def cleanup():
            for process in owned:
                if process.poll() is None:
                    state = self.w._library_service_request(self.state)
                    if state.get('running') is True and state.get('pid') == process.pid:
                        self.w._library_service_request(self.state, 'stop', True)
                        process.wait(timeout=10)
                    else:
                        process.terminate()
                        process.wait(timeout=10)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = self.w._library_service_request(self.state)
            if state.get('running') is True:
                break
            if child.poll() is not None:
                self.fail('isolated shelf failed to start')
            time.sleep(0.05)
        self.assertTrue(state.get('running'))
        registry = (self.state / 'library.json').read_bytes()
        stale = (self.state / 'service.json').read_bytes()
        child.terminate()
        child.wait(timeout=10)
        self.assertNotIn('stopped', json.loads(stale))
        proof = self.w._library_service_request(self.state)
        self.assertTrue(proof['exit_verified'])
        spawn = subprocess.Popen
        def tracked(*args, **kwargs):
            process = spawn(*args, **kwargs)
            owned.append(process)
            return process
        with patch.object(runner.subprocess, 'Popen', side_effect=tracked):
            started = runner.ensure_bookshelf(base.story, base.TOOL, self.state, port=port)
        self.assertTrue(started['ok'])
        self.assertFalse(started['reused'])
        self.assertEqual((self.state / 'library.json').read_bytes(), registry)
        self.assertNotEqual(json.loads((self.state / 'service.json').read_bytes())['instance'], json.loads(stale)['instance'])
        self.assertTrue(any(p.read_bytes() == stale for p in (self.state / '.backups').rglob('service.json')))


if __name__ == '__main__':
    unittest.main()
