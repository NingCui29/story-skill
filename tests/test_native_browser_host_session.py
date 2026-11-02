"""Native host preparation contracts; synthetic queues, no model or browser."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_chrome_assistant_session as base

worker = base.worker


class NativeHostSDKDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / 'Qoder CN.app/Contents/Resources/node-repl/node_modules'
        self.alias = self.root / '@qoder-space/browser-use'
        self.sdk = self.root / '@ali/qoder-browser-use-sdk'
        self.alias.mkdir(parents=True)
        self.sdk.mkdir(parents=True)
        (self.alias / 'package.json').write_text(json.dumps({
            'name': '@qoder-space/browser-use', 'type': 'module', 'exports': './index.js'}))
        (self.alias / 'index.js').write_text('export * from "@ali/qoder-browser-use-sdk"\n')
        (self.sdk / 'package.json').write_text(json.dumps({
            'name': '@ali/qoder-browser-use-sdk', 'version': '0.4.2', 'type': 'module', 'exports': './index.js'}))
        (self.sdk / 'index.js').write_text('// Synthetic package. Never imported.\n')
        item = patch.object(worker, '_native_sdk_roots', return_value=(self.root,))
        item.start()
        self.addCleanup(item.stop)

    def test_qoder_public_alias_and_sdk_manifests_are_both_required(self):
        self.assertEqual(worker._native_sdk_path('qoder-cn'), self.alias / 'index.js')
        (self.sdk / 'package.json').unlink()
        with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
            worker._native_sdk_path('qoder-cn')

    def test_alias_cannot_be_replaced_with_an_arbitrary_wrapper(self):
        (self.alias / 'index.js').write_text('export * from "@ali/qoder-browser-use-sdk"; startPrivateTransport();')
        with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
            worker._native_sdk_path('qoder-cn')

    def test_wrong_manifest_or_entry_is_rejected(self):
        path = self.sdk / 'package.json'
        original = json.loads(path.read_text())
        for change in ({'name': 'unrelated'}, {'exports': './private.js'}, {'type': 'commonjs'}, {'version': True}):
            with self.subTest(change=change):
                path.write_text(json.dumps({**original, **change}))
                with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
                    worker._native_sdk_path('qoder-cn')

    def test_oversized_or_non_object_manifest_is_rejected(self):
        path = self.alias / 'package.json'
        for value in (' ' * 65537, '[]'):
            path.write_text(value)
            with self.assertRaises(worker.WorkerError):
                worker._native_sdk_path('qoder-cn')

    def test_symlink_public_entry_is_rejected(self):
        path = self.alias / 'index.js'
        target = self.alias / 'other.js'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
            worker._native_sdk_path('qoder-cn')

    @unittest.skipUnless(os.name != 'nt', 'POSIX ownership and modes')
    def test_world_writable_public_manifest_is_rejected(self):
        (self.alias / 'package.json').chmod(0o666)
        with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
            worker._native_sdk_path('qoder-cn')

    @unittest.skipUnless(hasattr(os, 'getuid'), 'POSIX ownership')
    def test_foreign_owner_is_rejected(self):
        with patch.object(worker.os, 'getuid', return_value=os.getuid() + 100000):
            with self.assertRaisesRegex(worker.WorkerError, 'unsafe_runtime'):
                worker._native_sdk_path('qoder-cn')

    def test_missing_verified_public_roots_fail_closed(self):
        with patch.object(worker, '_native_sdk_roots', return_value=()):
            with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
                worker._native_sdk_path('workbuddy')
            with self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
                worker._native_sdk_path('qoder-cn')


class NativeHostSessionTests(unittest.TestCase):
    setUp = base.AssistantSessionTests.setUp
    connect = base.AssistantSessionTests.connect
    detach = base.AssistantSessionTests.detach
    final_receipt = base.AssistantSessionTests.final_receipt

    def native(self, **kwargs):
        return worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0,
            execution_host='qoder-cn', native_model_authorized=True, **kwargs)

    def test_native_permission_must_be_explicit_boolean_before_preparation(self):
        with patch.object(worker, '_native_sdk_path', side_effect=AssertionError('No SDK discovery')):
            for host in ('qoder-cn', 'workbuddy'):
                for value in (False, None, 1, 'true'):
                    with self.subTest(host=host, value=value), self.assertRaises(worker.WorkerError):
                        with worker.assistant_session(self.root, self.binding, on_context=self.connect,
                                execution_host=host, native_model_authorized=value):
                            self.fail('No execution without explicit authorization')
        self.assertEqual(list(self.context.iterdir()), [])

    def test_invalid_host_or_codex_native_override_cannot_change_model_policy(self):
        for host, authorized in (('other', True), ('qoder', True), ('codex', True)):
            with self.subTest(host=host), self.assertRaisesRegex(worker.WorkerError, 'invalid_config'):
                worker.AssistantBridgeSession(base.story, self.root, self.binding,
                    execution_host=host, native_model_authorized=authorized)

    def test_unavailable_workbuddy_does_not_create_context_or_attach(self):
        with patch.object(worker, '_workbuddy_cli_path', side_effect=worker.WorkerError('platform_chrome_worker_native_sdk_unavailable')), \
                self.assertRaisesRegex(worker.WorkerError, 'native_sdk_unavailable'):
            with worker.assistant_session(self.root, self.binding, on_context=lambda _: self.fail('No packet'),
                    execution_host='workbuddy', native_model_authorized=True):
                self.fail('No native runtime')
        self.assertEqual(list(self.context.iterdir()), [])
        self.assertFalse(base.bridge.worker_status(self.root, self.binding)['attached'])

    def test_qoder_packet_uses_one_factory_and_public_module_in_same_repl(self):
        with patch.object(worker, '_native_sdk_path', return_value=self.sdk) as discover:
            with self.native() as session:
                packet = session.packet()
                config = json.loads(Path(packet['config_path']).read_text())
                self.assertEqual(config['execution_host'], 'qoder-cn')
                self.assertIs(config['native_model_authorized'], True)
                self.assertEqual(config['sdk_path'], str(self.sdk))
                self.assertEqual(packet['execution_host'], 'qoder-cn')
                self.assertEqual(packet['model_policy'], 'user_authorized_native')
                self.assertEqual(packet['tool_timeout_ms'], 240000)
                self.assertEqual(packet['tool_yield_time_ms'], 60000)
                self.assertEqual(packet['model_verification'], 'executor_runtime_required')
                self.assertNotIn('model_verified', packet)
                self.assertNotIn('model_verified', config)
                self.assertNotIn('gpt-6.1-terra', packet['instructions'])
                self.assertIn('官方 node_repl', packet['instructions'])
                self.assertIn('不得在 Codex', packet['instructions'])
                self.assertIn('node_repl_wait 等待同一 cell_id 至工具终态', packet['instructions'])
                self.assertIn('之后才读取 checkpoint', packet['instructions'])
                self.assertIn('工具未完成前不得调用 next_call、重复 first_call 或重置 REPL', packet['instructions'])
                self.assertIn('storyAssistantWorkerModule.setupQoderBrowserRuntime', packet['first_call'])
                self.assertNotIn('story_qoder_browser.mjs', packet['first_call'])
                self.assertIn(self.sdk.as_uri(), packet['first_call'])
                self.assertEqual(packet['first_call'].count('var storyAssistantSDKFactory ='), 1)
                self.assertIn('setupQoderBrowserRuntime(storyAssistantHostSDK)', packet['first_call'])
                self.assertTrue(packet['first_call'].endswith(packet['next_call']))
                self.assertIn('{sdkFactory: storyAssistantSDKFactory}', packet['next_call'])
                self.assertNotIn('import(', packet['next_call'])
                self.assertNotIn('=>', packet['next_call'])
                self.assertEqual(packet, session.packet())
                discover.assert_called_once_with('qoder-cn')
                self.detach()
                self.final_receipt()
        self.assertTrue(session.cleanup_complete)

    def test_native_cleanup_requires_same_host_policy_sdk_and_permission(self):
        with patch.object(worker, '_native_sdk_path', return_value=self.sdk):
            with self.native() as session:
                self.detach()
                self.final_receipt()
                packet = session.packet()
                self.assertTrue(worker.assistant_cleanup_status(self.root, self.binding, packet)['cleanup_complete'])
                for change in ({'execution_host': 'codex'}, {'execution_host': 'workbuddy'},
                               {'native_model_authorized': False}, {'native_model_authorized': 1},
                               {'model_policy': 'terra_required'}, {'sdk_path': '/another/sdk.js'},
                               {'controller_instance': 'f' * 32}):
                    with self.subTest(change=change):
                        result = worker.assistant_cleanup_status(self.root, self.binding, {**packet, **change})
                        self.assertFalse(result['cleanup_complete'])
                for field in ('execution_host', 'native_model_authorized', 'sdk_path', 'model_policy'):
                    missing = {key: value for key, value in packet.items() if key != field}
                    self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding, missing)['cleanup_complete'])

    def test_default_codex_keeps_original_sdk_and_terra_requirement(self):
        with patch.object(worker, '_native_sdk_path', side_effect=AssertionError('Codex never discovers native SDK')):
            with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0) as session:
                packet = session.packet()
                config = json.loads(Path(packet['config_path']).read_text())
                self.assertNotIn('execution_host', config)
                self.assertNotIn('native_model_authorized', config)
                self.assertEqual(packet['execution_host'], 'codex')
                self.assertEqual(packet['model_policy'], 'terra_required')
                self.assertNotIn('tool_yield_time_ms', packet)
                self.assertNotIn('node_repl_wait', packet['instructions'])
                self.assertIn('gpt-6.1-terra', packet['instructions'])
                self.assertNotIn('sdkFactory', packet['first_call'])
                self.detach()
                self.final_receipt()
                legacy = {key: value for key, value in packet.items() if key not in ('execution_host', 'model_policy')}
                self.assertTrue(worker.assistant_cleanup_status(self.root, self.binding, legacy)['cleanup_complete'])
                self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding,
                    {**packet, 'execution_host': 'qoder-cn', 'native_model_authorized': True,
                     'model_policy': 'user_authorized_native', 'sdk_path': str(self.sdk)})['cleanup_complete'])


class NativeHostOfficialPathTests(unittest.TestCase):
    def test_only_known_mac_qoder_paths_are_discovered(self):
        with patch.object(worker.sys, 'platform', 'darwin'):
            roots = worker._native_sdk_roots('qoder-cn')
            self.assertEqual(roots[0], Path('/Applications/Qoder CN.app/Contents/Resources/node-repl/node_modules'))
            self.assertEqual(roots[1], Path.home() / 'Applications/Qoder CN.app/Contents/Resources/node-repl/node_modules')
            self.assertEqual(worker._native_sdk_roots('workbuddy'), ())
        with patch.object(worker.sys, 'platform', 'win32'):
            self.assertEqual(worker._native_sdk_roots('qoder-cn'), ())


if __name__ == '__main__':
    unittest.main()
