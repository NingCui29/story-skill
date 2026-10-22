"""The native client reuses authenticated shelves and never kills unknown services."""
from contextlib import contextmanager, redirect_stdout
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
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_workbench as base


RUNNER = base.ROOT / 'desktop/macos/backend_runner.py'
SPEC = importlib.util.spec_from_file_location('desktop_backend_runner_test', RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
BUILD_SPEC = importlib.util.spec_from_file_location(
    'desktop_backend_runner_build_manifest', base.ROOT / 'scripts/build_desktop_macos.py')
builder = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(builder)


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
        for filename in builder.RUNTIME_FILES:
            shutil.copyfile(base.TOOL.parent / filename, scripts / filename)
        shutil.copyfile(RUNNER, resources / RUNNER.name)
        return resources / RUNNER.name

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            return listener.getsockname()[1]

    def bundle_diagnostic(self, process, result=None):
        logs = {}
        try:
            paths = sorted((self.state / 'desktop-client').glob('launch-*.log'))
        except OSError as error:
            logs['read_error'] = str(error)
        else:
            for path in paths:
                try:
                    logs[path.name] = path.read_text(encoding='utf-8', errors='replace')
                except OSError as error:
                    logs[path.name] = {'read_error': str(error)}
        return {'command': process.args, 'returncode': process.returncode,
                'result': result, 'stdout': process.stdout, 'stderr': process.stderr,
                'launch_logs': logs}

    def run_bundle(self, path, port):
        result = subprocess.run([sys.executable, '-B', str(path), '--state-dir', str(self.state),
                                 '--port', str(port)], capture_output=True, text=True,
                                encoding='utf-8', timeout=25)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, self.bundle_diagnostic(result))
        try:
            packet = json.loads(lines[0])
        except ValueError:
            self.fail(f'Bundled launcher output is not JSON: {self.bundle_diagnostic(result)}')
        self.assertIsInstance(packet, dict, self.bundle_diagnostic(result, packet))
        return result, packet

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
        diagnostic = self.bundle_diagnostic(process, result)
        self.assertEqual(process.returncode, 0, diagnostic)
        self.assertTrue(result['ok'], diagnostic)
        self.assertFalse(result['reused'])
        self.assertEqual(result['url'], f'http://127.0.0.1:{port}/')
        before = (self.state / 'service.json').read_bytes(), (self.state / 'library.json').read_bytes()
        state = self.w._library_service_request(self.state)
        self.assertTrue(state['running'])
        self.assertFalse(state['outdated'])
        process, reused = self.run_bundle(path, self.free_port())
        self.assertEqual(process.returncode, 0, self.bundle_diagnostic(process, reused))
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
            expected = {'workbench_port_in_use'}
            if os.name == 'nt':
                # Winsock may report WSAEACCES for an existing exclusive listener.
                expected.add('workbench_port_unavailable')
            self.assertIn(result['code'], expected, result)
            self.assertIn('已被占用', result['message'])
            self.assertTrue(Path(result['log']).is_file())
            self.assertFalse((self.state / 'service.json').exists())
            self.assertFalse((self.state / 'library.json').exists())
            self.assertEqual(listener.getsockname()[1], port)


class ServiceRecordSharingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='desktop-service-sharing-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / '独立书架'
        self.root.mkdir()
        self.story, self.w = base.story, base.story.workbench
        self.api = self.w.api
        self.target = self.root / 'service.json'
        self.backup = self.root / '.backups' / 'previous' / 'service.json'

    @staticmethod
    def sharing_error(number=32):
        error = PermissionError('Windows sharing refusal')
        error.winerror = number
        return error

    def platform(self, name='nt'):
        # Model only the service helper's platform selection; real temporary
        # filesystem operations and core atomic-write behavior stay intact.
        return patch.object(self.w, 'os', SimpleNamespace(**{**vars(os), 'name': name}))

    def move(self):
        with self.story._pinned_directory(self.root) as directory:
            return self.w._service_record_move(
                self.story._BoundFile(directory, 'staged'),
                self.story._BoundFile(directory, 'service.json'))

    def test_first_service_publication_retries_only_the_refused_move(self):
        publish = self.story._publish_no_replace
        calls = []

        def transient(source, target):
            calls.append((source.path, target.path))
            if len(calls) == 1:
                raise self.sharing_error()
            return publish(source, target)

        with self.platform(), patch.object(self.api, '_publish_no_replace', side_effect=transient):
            self.w._write_service_record(self.target, '{"instance":"new"}', set(), self.backup)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(self.target.read_bytes(), b'{"instance":"new"}')
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.root.glob('.story-tmp-*')), [])

    def test_service_revision_retries_each_local_move_and_preserves_old_record(self):
        self.target.write_bytes(b'{"instance":"old","stopped":true}')
        old = self.target.read_text(encoding='utf-8')
        replace, publish = self.story._bound_replace, self.story._publish_no_replace
        counts = {'replace': 0, 'publish': 0}

        def transient(operation, key):
            def invoke(source, target):
                counts[key] += 1
                if counts[key] == 1:
                    raise self.sharing_error(33)
                return operation(source, target)
            return invoke

        with self.platform(), patch.object(self.api, '_bound_replace', side_effect=transient(replace, 'replace')), \
                patch.object(self.api, '_publish_no_replace', side_effect=transient(publish, 'publish')):
            self.w._write_service_record(self.target, '{"instance":"new"}', {self.story.digest(old)}, self.backup)
        self.assertEqual(counts, {'replace': 2, 'publish': 2})
        self.assertEqual(self.target.read_bytes(), b'{"instance":"new"}')
        self.assertEqual(self.backup.read_bytes(), old.encode('utf-8'))

    def test_permanent_sharing_failure_is_bounded_and_leaves_stage_untouched(self):
        (self.root / 'staged').write_bytes(b'complete new record')
        error = self.sharing_error()
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 2]), sleep=Mock())
        with self.platform(), patch.object(self.w, 'time', clock), \
                patch.object(self.api, '_publish_no_replace', side_effect=error) as publish:
            with self.assertRaises(PermissionError) as caught:
                self.move()
        self.assertIs(caught.exception, error)
        self.assertEqual(publish.call_count, 2)
        clock.sleep.assert_called_once_with(.01)
        self.assertFalse(self.target.exists())
        self.assertEqual((self.root / 'staged').read_bytes(), b'complete new record')

    def test_failed_publication_retries_restore_without_losing_original_error_or_backup(self):
        old = '{"instance":"old","stopped":true}'
        self.target.write_bytes(old.encode('utf-8'))
        publish = self.story._publish_no_replace
        failure = self.sharing_error(5)
        restores = []

        def interrupted(source, target):
            if source.name.startswith('.story-tmp-'):
                raise failure
            restores.append((source.path, target.path))
            if len(restores) == 1:
                raise self.sharing_error()
            return publish(source, target)

        with self.platform(), patch.object(self.api, '_publish_no_replace', side_effect=interrupted):
            with self.assertRaises(self.story.StoryError) as caught:
                self.w._write_service_record(self.target, '{"instance":"new"}', {self.story.digest(old)}, self.backup)
        self.assertEqual(caught.exception.code, 'export_io')
        self.assertIs(caught.exception.__context__, failure)
        self.assertEqual(len(restores), 2)
        self.assertEqual(restores[0], restores[1])
        self.assertEqual(self.target.read_bytes(), old.encode('utf-8'))
        self.assertEqual(self.backup.read_bytes(), old.encode('utf-8'))
        self.assertEqual(list(self.root.glob('.story-tmp-*')), [])
        self.assertEqual(list(self.root.glob('.story-restore-*')), [])

    def test_replaced_source_or_new_target_is_never_retried_or_overwritten(self):
        for changed in ('source', 'target'):
            with self.subTest(changed=changed):
                staged = self.root / 'staged'
                staged.write_bytes(b'complete new record')

                def external_change(_delay):
                    if changed == 'source':
                        staged.rename(self.root / 'preserved-stage')
                        staged.write_bytes(b'external replacement')
                    else:
                        self.target.write_bytes(b'external service record')

                with self.platform(), patch.object(self.w, 'time', SimpleNamespace(
                        monotonic=lambda: 0, sleep=external_change)), \
                        patch.object(self.api, '_publish_no_replace', side_effect=self.sharing_error()) as publish:
                    with self.assertRaises(self.story.StoryError) as caught:
                        self.move()
                self.assertEqual(caught.exception.code, 'workbench_service_changed')
                publish.assert_called_once()
                if changed == 'source':
                    self.assertEqual(staged.read_bytes(), b'external replacement')
                    self.assertEqual((self.root / 'preserved-stage').read_bytes(), b'complete new record')
                else:
                    self.assertEqual(self.target.read_bytes(), b'external service record')

    def test_non_windows_and_non_sharing_errors_are_not_retried(self):
        (self.root / 'staged').write_bytes(b'new record')
        for platform, number in (('posix', 32), ('nt', 5)):
            with self.subTest(platform=platform, number=number):
                error = self.sharing_error(number)
                with self.platform(platform), patch.object(self.api, '_publish_no_replace', side_effect=error) as publish, \
                        patch.object(self.w, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=Mock())) as clock:
                    with self.assertRaises(PermissionError) as caught:
                        self.move()
                self.assertIs(caught.exception, error)
                publish.assert_called_once()
                clock.sleep.assert_not_called()

    def test_service_read_retries_pin_entry_but_does_not_replay_read_body(self):
        self.target.write_bytes(b'confirmed service record')
        pinned = self.story._pinned_directory
        calls = []

        @contextmanager
        def transient(path, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise self.sharing_error()
            with pinned(path, **kwargs) as directory:
                yield directory

        with self.platform(), patch.object(self.api, '_pinned_directory', side_effect=transient):
            self.assertEqual(self.w._author_read(self.root, 'service.json', 8192), b'confirmed service record')
        self.assertEqual(len(calls), 2)
        error = self.sharing_error()
        with self.platform(), patch.object(self.api, '_bound_reader', side_effect=error) as read:
            with self.assertRaises(PermissionError) as caught:
                self.w._author_read(self.root, 'service.json', 8192)
        self.assertIs(caught.exception, error)
        read.assert_called_once()

    def test_service_pin_retry_rejects_replaced_parent(self):
        preserved = self.root.with_name('原书架')

        def replace_parent(_delay):
            self.root.rename(preserved)
            self.root.mkdir()
            self.target.write_bytes(b'replacement record')

        with self.platform(), patch.object(self.w, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=replace_parent)), \
                patch.object(self.api, '_pinned_directory', side_effect=self.sharing_error()) as pin:
            with self.assertRaises(self.story.StoryError) as caught:
                self.w._author_read(self.root, 'service.json', 8192)
        self.assertEqual(caught.exception.code, 'workbench_service_changed')
        pin.assert_called_once()
        self.assertEqual(self.target.read_bytes(), b'replacement record')
        self.assertTrue(preserved.is_dir())

    def test_exclusive_backup_pin_failure_keeps_existing_service_record(self):
        old = '{"instance":"old","stopped":true}'
        self.target.write_bytes(old.encode('utf-8'))
        pinned = self.story._pinned_directory
        exclusive_calls = []
        error = self.sharing_error()

        @contextmanager
        def failed_backup(path, **kwargs):
            if kwargs.get('exclusive'):
                exclusive_calls.append(path)
                raise error
            with pinned(path, **kwargs) as directory:
                yield directory

        with self.platform(), patch.object(self.api, '_pinned_directory', side_effect=failed_backup):
            with self.assertRaises(PermissionError) as caught:
                self.w._write_service_record(self.target, '{"instance":"new"}', {self.story.digest(old)}, self.backup)
        self.assertIs(caught.exception, error)
        self.assertEqual(exclusive_calls, [self.backup.parent])
        self.assertEqual(self.target.read_bytes(), old.encode('utf-8'))
        self.assertEqual(list(self.root.glob('.story-tmp-*')), [])
        # A later deliberate launch can retry from the unchanged stopped record.
        self.w._write_service_record(self.target, '{"instance":"new"}', {self.story.digest(old)}, self.backup)
        self.assertEqual(self.backup.read_bytes(), old.encode('utf-8'))

    def test_ordinary_atomic_write_keeps_its_single_attempt_and_cleanup(self):
        error = self.sharing_error()
        with patch.object(self.story, '_publish_no_replace', side_effect=error) as publish:
            with self.assertRaises(PermissionError) as caught:
                self.story.atomic_write(self.target, 'ordinary content', set(), self.backup)
        self.assertIs(caught.exception, error)
        publish.assert_called_once()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.story-tmp-*')), [])

    @unittest.skipUnless(os.name == 'nt', 'Requires native Windows directory sharing')
    def test_native_strict_parent_pin_release_allows_service_publication(self):
        held, release, refused = threading.Event(), threading.Event(), threading.Event()
        failures = []
        publish = self.story._publish_no_replace

        def reader_pin():
            try:
                with self.story._windows_path_handle(self.root, directory=True):
                    held.set()
                    if not release.wait(15):
                        raise AssertionError('Writer never reached the sharing conflict')
            except BaseException as error:
                failures.append(error)

        def observed_publish(source, target):
            try:
                return publish(source, target)
            except OSError as error:
                if getattr(error, 'winerror', None) in (32, 33):
                    refused.set()
                raise

        def write():
            try:
                self.w._write_service_record(self.target, '{"instance":"native"}', set(), self.backup)
            except BaseException as error:
                failures.append(error)

        reader = threading.Thread(target=reader_pin)
        writer = threading.Thread(target=write)
        reader.start()
        try:
            self.assertTrue(held.wait(5), failures)
            with patch.object(self.api, '_publish_no_replace', side_effect=observed_publish):
                writer.start()
                try:
                    self.assertTrue(refused.wait(5), failures)
                finally:
                    release.set()
                    writer.join(5)
        finally:
            release.set()
            reader.join(5)
        self.assertFalse(writer.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.target.read_bytes(), b'{"instance":"native"}')
        self.assertEqual(list(self.root.glob('.story-tmp-*')), [])


if __name__ == '__main__':
    unittest.main()
