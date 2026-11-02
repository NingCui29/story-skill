"""WorkBuddy preparation/cleanup contracts; synthetic files only, no browser."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_chrome_assistant_session as base

worker = base.worker


class WorkBuddyHostSessionTests(unittest.TestCase):
    connect = base.AssistantSessionTests.connect
    detach = base.AssistantSessionTests.detach
    final_receipt = base.AssistantSessionTests.final_receipt

    def setUp(self):
        base.AssistantSessionTests.setUp(self)
        self.cli = self.root / 'agent-browser-mock'
        self.cli.write_bytes(b'Synthetic CLI; never executed')
        self.node = self.root / 'node-mock'
        self.node.write_bytes(b'Synthetic Node; never executed')
        for method, value in (('_workbuddy_cli_path', self.cli), ('_workbuddy_node_path', self.node)):
            item = patch.object(worker, method, return_value=value)
            item.start()
            self.addCleanup(item.stop)

    def session(self, **kwargs):
        return worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0,
            execution_host='workbuddy', native_model_authorized=True, **kwargs)

    def test_packet_uses_one_explicit_cli_runner_without_repl_or_model_attestation(self):
        with self.session() as session:
            packet = session.packet()
            config = json.loads(Path(packet['config_path']).read_text())
            self.assertEqual(packet, session.packet())
            self.assertEqual(packet['execution_host'], 'workbuddy')
            self.assertEqual(packet['runtime_kind'], 'agent_browser_cli')
            self.assertEqual(packet['model_policy'], 'user_authorized_native')
            self.assertIs(packet['native_model_authorized'], True)
            self.assertEqual(packet['runner_argv'], [str(self.node),
                str(Path(worker.__file__).with_name('story_workbuddy_worker.mjs')), '--config', packet['config_path']])
            self.assertNotIn('first_call', packet)
            self.assertNotIn('next_call', packet)
            self.assertNotIn('model_verified', packet)
            self.assertEqual(config['sdk_path'], str(Path(worker.__file__).with_name('story_workbuddy_browser.mjs')))
            self.assertEqual(config['workbuddy_cli_sha256'], hashlib.sha256(self.cli.read_bytes()).hexdigest())
            self.assertEqual(config['workbuddy_session'], worker._workbuddy_session(self.binding))
            self.assertEqual(Path(config['workbuddy_state_directory']).name, config['workbuddy_session'])
            self.detach()
            self.final_receipt()
        self.assertTrue(session.cleanup_complete)

    def test_cleanup_requires_exact_workbuddy_cli_digest_session_and_state_binding(self):
        with self.session() as session:
            self.detach()
            self.final_receipt()
            packet = session.packet()
            self.assertTrue(worker.assistant_cleanup_status(self.root, self.binding, packet)['cleanup_complete'])
            for key in ('workbuddy_cli_path', 'workbuddy_cli_sha256', 'workbuddy_session', 'workbuddy_state_directory'):
                with self.subTest(key=key):
                    changed = {**packet, key: packet[key] + '-other'}
                    self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding, changed)['cleanup_complete'])
                    missing = {k: v for k, v in packet.items() if k != key}
                    self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding, missing)['cleanup_complete'])
            for change in ({'execution_host': 'codex'}, {'execution_host': 'qoder-cn'},
                           {'native_model_authorized': False}, {'model_policy': 'terra_required'}):
                self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding, {**packet, **change})['cleanup_complete'])

    def test_missing_cli_or_node_fails_before_private_context_or_attach(self):
        for name in ('_workbuddy_cli_path', '_workbuddy_node_path'):
            with self.subTest(name=name), patch.object(worker, name,
                    side_effect=worker.WorkerError('platform_chrome_worker_native_sdk_unavailable')):
                with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
                    with self.session():
                        self.fail('Missing native runtime must not connect')
            self.assertEqual(list(self.context.iterdir()), [])
            self.assertFalse(base.bridge.worker_status(self.root, self.binding)['attached'])

    def test_native_authorization_is_boolean_and_checked_before_cli_discovery(self):
        with patch.object(worker, '_workbuddy_cli_path', side_effect=AssertionError('No discovery')):
            for value in (False, None, 1, 'true'):
                with self.subTest(value=value), self.assertRaises(worker.WorkerError):
                    worker.AssistantBridgeSession(base.story, self.root, self.binding,
                        execution_host='workbuddy', native_model_authorized=value)
        self.assertEqual(list(self.context.iterdir()), [])

    def test_workbuddy_discovery_and_runner_are_never_used_by_default_codex(self):
        with (patch.object(worker, '_workbuddy_cli_path', side_effect=AssertionError('Codex must not discover CLI')),
              patch.object(worker, '_workbuddy_node_path', side_effect=AssertionError('Codex must not discover Node'))):
            with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0) as session:
                packet = session.packet()
                self.assertEqual(packet['model_policy'], 'terra_required')
                self.assertIn('gpt-6.1-terra', packet['instructions'])
                self.assertNotIn('runner_argv', packet)
                self.assertNotIn('workbuddy_session', packet)
                self.detach()
                self.final_receipt()

    def test_isolation_session_is_stable_across_batches_but_changes_for_book_account_or_target(self):
        expected = worker._workbuddy_session(self.binding)
        self.assertEqual(expected, worker._workbuddy_session({**self.binding, 'plan_id': 'another-plan'}))
        for key in ('account_id', 'remote_book_id'):
            changed = {**self.binding, 'target': {**self.binding['target'], key: '999999'}}
            self.assertNotEqual(expected, worker._workbuddy_session(changed))
        self.assertNotEqual(expected, worker._workbuddy_session({**self.binding, 'book_id': 'another-book'}))


class WorkBuddyCLIDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.package = self.home / '.story-skill/browser-tools/agent-browser/0.41.0/package'
        (self.package / 'bin').mkdir(parents=True)
        self.cli = self.package / 'bin/agent-browser-darwin-arm64'
        self.payload = b'Synthetic public CLI payload'
        self.cli.write_bytes(self.payload)
        self.cli.chmod(0o700)
        self.manifest = self.package / 'package.json'
        self.manifest.write_text(json.dumps({'name': 'agent-browser', 'version': '0.41.0'}))
        for target, kwargs in ((worker.Path, {'attribute': 'home', 'return_value': self.home}),
                               (worker.sys, {'attribute': 'platform', 'new': 'darwin'}),
                               (worker.platform, {'attribute': 'machine', 'return_value': 'arm64'}),
                               (worker.shutil, {'attribute': 'which', 'return_value': None}),
                               (worker.subprocess, {'attribute': 'Popen', 'side_effect': AssertionError('Discovery cannot execute')}),
                               (worker, {'attribute': 'WORKBUDDY_CLI_HASHES', 'new': {('darwin', 'arm64'):
                                   (self.cli.name, hashlib.sha256(self.cli.read_bytes()).hexdigest())}})):
            item = patch.object(target, **kwargs)
            item.start()
            self.addCleanup(item.stop)
        regular = worker._regular

        def isolated_regular(path, **kwargs):
            path = Path(path)
            if not path.is_relative_to(self.home):
                raise FileNotFoundError(path)
            return regular(path, **kwargs)

        item = patch.object(worker, '_regular', side_effect=isolated_regular)
        item.start()
        self.addCleanup(item.stop)

    def global_package(self, directory='npm-prefix/lib/node_modules/agent-browser'):
        package = self.home / directory
        (package / 'bin').mkdir(parents=True, exist_ok=True)
        cli = package / 'bin' / self.cli.name
        cli.write_bytes(self.payload)
        cli.chmod(0o700)
        (package / 'package.json').write_text(json.dumps({'name': 'agent-browser', 'version': '0.41.0'}))
        launcher = package / 'bin/agent-browser.js'
        launcher.write_text('// Public npm launcher fixture. It must never be executed.\n')
        return package, cli, launcher

    def test_exact_pinned_manifest_and_binary_hash_are_required_without_execution(self):
        with patch.object(worker.subprocess, 'Popen', side_effect=AssertionError('Discovery cannot execute')):
            self.assertEqual(worker._workbuddy_cli_path(), self.cli)
            self.cli.write_bytes(b'changed binary')
            with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
                worker._workbuddy_cli_path()

    def test_missing_wrong_manifest_and_symlink_fail_closed(self):
        self.manifest.write_text(json.dumps({'name': 'another-cli', 'version': '0.41.0'}))
        with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
            worker._workbuddy_cli_path()
        self.manifest.write_text(json.dumps({'name': 'agent-browser', 'version': '0.41.0'}))
        self.cli.rename(self.cli.with_suffix('.real'))
        self.cli.symlink_to(self.cli.with_suffix('.real'))
        with self.assertRaises(worker.WorkerError):
            worker._workbuddy_cli_path()
        self.cli.unlink()
        with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
            worker._workbuddy_cli_path()

    @unittest.skipUnless(os.name != 'nt', 'POSIX npm launcher symlink')
    def test_global_npm_path_launcher_resolves_same_package_native_binary_when_managed_missing(self):
        self.cli.unlink()
        package, native, launcher = self.global_package()
        path_bin = self.home / 'npm-prefix/bin'
        path_bin.mkdir()
        path_launcher = path_bin / 'agent-browser'
        path_launcher.symlink_to(launcher)
        with patch.object(worker.shutil, 'which', return_value=str(path_launcher)) as which:
            self.assertEqual(worker._workbuddy_cli_path(), native)
            which.assert_called_once_with('agent-browser')
        self.assertNotEqual(native, launcher)
        self.assertEqual(native.parent.parent, package)

    def test_global_path_native_binary_is_accepted_only_from_its_own_matching_package(self):
        self.cli.unlink()
        package, native, _ = self.global_package()
        with patch.object(worker.shutil, 'which', return_value=str(native)):
            self.assertEqual(worker._workbuddy_cli_path(), native)
            (package / 'package.json').write_text(json.dumps({'name': 'other-package', 'version': '0.41.0'}))
            with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
                worker._workbuddy_cli_path()

    def test_standard_user_global_npm_root_is_available_without_a_path_launcher(self):
        self.cli.unlink()
        _, native, _ = self.global_package('.npm-global/lib/node_modules/agent-browser')
        self.assertEqual(worker._workbuddy_cli_path(), native)

    def test_global_package_name_version_and_exact_native_bytes_are_all_required(self):
        self.cli.unlink()
        package, native, launcher = self.global_package()
        manifest = package / 'package.json'
        with patch.object(worker.shutil, 'which', return_value=str(launcher)):
            for name, version, payload in [('different-package', '0.41.0', self.payload),
                                            ('agent-browser', '0.40.0', self.payload),
                                            ('agent-browser', '0.41.0', b'changed native bytes')]:
                with self.subTest(name=name, version=version, payload_matches=payload == self.payload):
                    manifest.write_text(json.dumps({'name': name, 'version': version}))
                    native.write_bytes(payload)
                    with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
                        worker._workbuddy_cli_path()

    def test_arbitrary_path_launcher_does_not_authorize_an_adjacent_package(self):
        self.cli.unlink()
        _, _, launcher = self.global_package()
        unrecognized = launcher.with_name('private-wrapper.js')
        launcher.rename(unrecognized)
        with patch.object(worker.shutil, 'which', return_value=str(unrecognized)):
            with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
                worker._workbuddy_cli_path()

    def test_invalid_managed_binary_does_not_silently_fall_back_to_a_valid_global_package(self):
        self.cli.write_bytes(b'changed managed binary')
        _, _, launcher = self.global_package()
        with patch.object(worker.shutil, 'which', return_value=str(launcher)):
            with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
                worker._workbuddy_cli_path()


if __name__ == '__main__':
    unittest.main()
