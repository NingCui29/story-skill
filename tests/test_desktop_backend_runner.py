"""The native client reuses authenticated shelves and never kills unknown services."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_workbench as base


RUNNER = base.ROOT / 'desktop/macos/backend_runner.py'
SPEC = importlib.util.spec_from_file_location('desktop_backend_runner_test', RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class DesktopBackendRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='desktop-backend-runner-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.state = self.area / '独立书架'
        self.story = base.story
        self.w = self.story.workbench

    def running(self, **extra):
        return {'ok': True, 'running': True, 'url': 'http://127.0.0.1:8765/', 'outdated': False, **extra}

    def ensure(self, **kwargs):
        return runner.ensure_bookshelf(self.story, base.TOOL, self.state, **kwargs)

    def staged_runner(self):
        resources = self.area / '客户端 Resources'
        scripts = resources / 'Scripts'
        scripts.mkdir(parents=True)
        for path in base.TOOL.parent.glob('story*.py'):
            shutil.copyfile(path, scripts / path.name)
        shutil.copyfile(RUNNER, resources / RUNNER.name)
        return resources / RUNNER.name

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            return listener.getsockname()[1]

    def run_bundle(self, path, port):
        result = subprocess.run([sys.executable, '-B', str(path), '--state-dir', str(self.state),
                                 '--port', str(port)], capture_output=True, text=True,
                                encoding='utf-8', timeout=25)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result)
        return result, json.loads(lines[0])

    def stop_test_shelf(self):
        state = self.w._library_service_request(self.state)
        if state.get('running') is not True:
            return
        pid, instance = state['pid'], state['instance']
        stopped = self.w._library_service_request(self.state, 'stop', True)
        self.assertEqual(stopped['instance'], instance)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                record = json.loads((self.state / 'service.json').read_text(encoding='utf-8'))
            except (FileNotFoundError, json.JSONDecodeError):
                # Atomic retirement can briefly move the old service record
                # before publishing the final stopped record.
                time.sleep(0.05)
                continue
            final_output = False
            for path in (self.state / 'desktop-client').glob('launch-*.log'):
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict) and item.get('stopped') is True:
                        final_output = True
            if record.get('instance') == instance and record.get('stopped') is True and final_output:
                # Windows os.kill(pid, 0) is not a read-only existence probe.
                # A SYNCHRONIZE handle can observe exit without terminating it.
                if os.name == 'nt':
                    import ctypes
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
                    kernel.OpenProcess.restype = wintypes.HANDLE
                    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
                    kernel.WaitForSingleObject.restype = wintypes.DWORD
                    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
                    kernel.CloseHandle.restype = wintypes.BOOL
                    handle = kernel.OpenProcess(0x00100000, False, pid)
                    if not handle:
                        if ctypes.get_last_error() == 87:  # Process has exited.
                            return
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        wait = kernel.WaitForSingleObject(handle, 0)
                        if wait == 0:  # WAIT_OBJECT_0: process exited.
                            return
                        if wait != 258:  # WAIT_TIMEOUT: still closing.
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        kernel.CloseHandle(handle)
                    time.sleep(0.05)
                    continue
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
                if os.name == 'posix':
                    status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                            capture_output=True, text=True, timeout=2)
                    if not status.stdout.strip() or status.stdout.strip().startswith('Z'):
                        return
            time.sleep(0.05)
        self.fail('Authenticated temporary bookshelf did not finish closing before directory cleanup')

    def test_running_authenticated_shelf_is_reused_without_spawn_or_writes(self):
        with patch.object(self.w, '_library_service_request', return_value=self.running()), \
                patch.object(runner.subprocess, 'Popen') as spawn:
            result = self.ensure()
        self.assertTrue(result['ok'])
        self.assertTrue(result['reused'])
        self.assertEqual(result['url'], 'http://127.0.0.1:8765/')
        spawn.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_older_running_shelf_is_reused_and_explains_manual_restart(self):
        with patch.object(self.w, '_library_service_request', return_value=self.running(outdated=True)), \
                patch.object(runner.subprocess, 'Popen') as spawn:
            result = self.ensure()
        self.assertTrue(result['outdated'])
        self.assertIn('保留', result['message'])
        self.assertIn('手动重启', result['message'])
        spawn.assert_not_called()

    def test_unreachable_registered_service_is_never_replaced(self):
        with patch.object(self.w, '_library_service_request', return_value={'ok': True, 'running': None}), \
                patch.object(runner.subprocess, 'Popen') as spawn:
            with self.assertRaises(runner.LauncherError) as error:
                self.ensure()
        self.assertEqual(error.exception.code, 'client_service_unconfirmed')
        spawn.assert_not_called()

    def test_mismatched_instance_or_corrupt_record_is_never_replaced(self):
        for code in ('workbench_service_mismatch', 'workbench_service_invalid'):
            with self.subTest(code=code):
                with patch.object(self.w, '_library_service_request',
                                  side_effect=self.story.StoryError(code, '实例无法确认')), \
                        patch.object(runner.subprocess, 'Popen') as spawn:
                    with self.assertRaises(self.story.StoryError):
                        self.ensure()
                spawn.assert_not_called()

    def test_start_uses_only_fixed_cli_and_clean_bundled_python_environment(self):
        states = [{'ok': True, 'running': False}, self.running()]
        with patch.object(self.w, '_library_service_request', side_effect=states), \
                patch.dict(os.environ, {'PYTHONHOME': '/wrong/runtime', 'PYTHONPATH': '/wrong/modules'}), \
                patch.object(runner.subprocess, 'Popen', return_value=SimpleNamespace(poll=lambda: None)) as spawn:
            result = self.ensure()
        command = spawn.call_args.args[0]
        self.assertEqual(command, [sys.executable, '-B', str(base.TOOL), 'workbench-library-serve',
                                   '--state-dir', str(self.state), '--port', '8765'])
        options = spawn.call_args.kwargs
        self.assertTrue(options['start_new_session'])
        self.assertNotIn('shell', options)
        self.assertNotIn('PYTHONHOME', options['env'])
        self.assertNotIn('PYTHONPATH', options['env'])
        self.assertEqual(options['env']['PYTHONNOUSERSITE'], '1')
        self.assertTrue(result['ok'])
        self.assertEqual(Path(result['log']).parent, self.state / 'desktop-client')
        if os.name == 'posix':
            self.assertEqual(Path(result['log']).stat().st_mode & 0o777, 0o600)

    def test_start_failure_has_readable_cli_error_and_log_location(self):
        def failed_spawn(_command, **options):
            options['stderr'].write(json.dumps({'ok': False, 'error': 'workbench_port_in_use',
                                              'message': '书架端口已被占用。'}).encode() + b'\n')
            return SimpleNamespace(poll=lambda: 2)

        with patch.object(self.w, '_library_service_request', return_value={'ok': True, 'running': False}), \
                patch.object(runner.subprocess, 'Popen', side_effect=failed_spawn):
            with self.assertRaises(runner.LauncherError) as error:
                self.ensure()
        self.assertEqual(error.exception.code, 'workbench_port_in_use')
        self.assertIn('已被占用', str(error.exception))
        self.assertTrue(Path(error.exception.log).is_file())

    def test_start_timeout_does_not_terminate_even_its_child_service(self):
        process = SimpleNamespace(poll=lambda: None)
        with patch.object(self.w, '_library_service_request', return_value={'ok': True, 'running': False}), \
                patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(runner.time, 'monotonic', side_effect=[0.0, 1.0]):
            with self.assertRaises(runner.LauncherError) as error:
                self.ensure(timeout=0.1)
        self.assertEqual(error.exception.code, 'client_start_pending')

    def test_invalid_port_does_not_touch_service_or_files(self):
        with patch.object(self.w, '_library_service_request') as status:
            for port in (0, -1, 65536, True):
                with self.subTest(port=port), self.assertRaises(runner.LauncherError):
                    self.ensure(port=port)
        status.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_main_has_one_json_line_and_returns_without_service_lifetime_monitor(self):
        output = io.StringIO()
        with patch.object(runner, 'load_story', return_value=(self.story, base.TOOL)), \
                patch.object(runner, 'ensure_bookshelf', return_value={'ok': True, 'url': 'http://127.0.0.1:8765/'}), \
                redirect_stdout(output):
            code = runner.main([])
        self.assertEqual(code, 0)
        self.assertTrue(output.getvalue().endswith('\n'))
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertTrue(json.loads(output.getvalue())['ok'])

    def test_main_reports_missing_runtime_and_invalid_arguments_as_json(self):
        for arguments in ([], ['--port', 'bad'], ['--book', '/not/a/launcher/command']):
            with self.subTest(arguments=arguments):
                output = io.StringIO()
                with patch.object(runner, 'load_story', side_effect=runner.LauncherError(
                        'client_runtime_missing', '客户端内的工作台程序缺失。')), redirect_stdout(output):
                    code = runner.main(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_client_log_directory_cannot_be_a_symlink(self):
        self.state.mkdir()
        outside = self.area / '外部日志'
        outside.mkdir()
        (self.state / 'desktop-client').symlink_to(outside, target_is_directory=True)
        with patch.object(self.w, '_library_service_request', return_value={'ok': True, 'running': False}), \
                patch.object(runner.subprocess, 'Popen') as spawn:
            with self.assertRaises(self.story.StoryError):
                self.ensure()
        spawn.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_bundle_starts_reuses_and_leaves_authenticated_service_running(self):
        path = self.staged_runner()
        self.addCleanup(self.stop_test_shelf)
        port = self.free_port()
        process, result = self.run_bundle(path, port)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['reused'])
        self.assertEqual(result['url'], f'http://127.0.0.1:{port}/')
        before = (self.state / 'service.json').read_bytes(), (self.state / 'library.json').read_bytes()
        state = self.w._library_service_request(self.state)
        self.assertTrue(state['running'])
        self.assertFalse(state['outdated'])
        process, reused = self.run_bundle(path, self.free_port())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['url'], result['url'])
        self.assertEqual(((self.state / 'service.json').read_bytes(),
                          (self.state / 'library.json').read_bytes()), before)
        self.assertTrue(self.w._library_service_request(self.state)['running'])

    def test_real_port_conflict_does_not_replace_listener_or_write_registry(self):
        path = self.staged_runner()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            process, result = self.run_bundle(path, port)
            self.assertEqual(process.returncode, 2)
            self.assertFalse(result['ok'])
            self.assertEqual(result['code'], 'workbench_port_in_use', result)
            self.assertIn('已被占用', result['message'])
            self.assertTrue(Path(result['log']).is_file())
            self.assertFalse((self.state / 'service.json').exists())
            self.assertFalse((self.state / 'library.json').exists())
            self.assertEqual(listener.getsockname()[1], port)


if __name__ == '__main__':
    unittest.main()
