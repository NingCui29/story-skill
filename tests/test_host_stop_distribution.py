"""Freeze old payload layouts and exercise the newly shipped host CLI boundary.

These tests do not call a model or operate a platform page. The synthetic worker
only proves that the four parsed CLI commands reach the persisted controller.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_package import package, fixture_suite, ROOT
from test_install_suite import installer
from test_package_npm import npm
import test_workbench as base


story = base.story
WORKER = 'story-skill/scripts/story_task_worker.py'
# SHA-256 of the exact ordered tuple encoded as compact JSON. These names were
# frozen before introducing the 0.6.29 worker; old versions must not gain it.
FROZEN_LAYOUTS = {
    '0.6.27': (69, '9c9af366aca8f30f7338568dff61ab98fe8d52abce358aa7260474f5c7c1c25b'),
    '0.6.28': (72, '67c14c8aa08594a66d1905a0d9d196842a2a42ca04c1bd79ad6441a4b94c7a4c'),
}


class HostStopDistributionTests(unittest.TestCase):
    def test_old_layouts_stay_frozen_and_new_layout_only_adds_worker(self):
        for module, files in ((package, package.suite_files),
                              (installer, installer.suite_files),
                              (npm, npm.payload_files)):
            with self.subTest(module=module.__name__):
                for version, (count, expected_sha) in FROZEN_LAYOUTS.items():
                    manifest = files(version)
                    self.assertEqual(len(manifest), count)
                    self.assertEqual(hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode()).hexdigest(), expected_sha)
                    self.assertNotIn(WORKER, manifest)
                self.assertEqual(len(files('0.6.29')), 73)
                self.assertEqual(set(files('0.6.29')) - set(files('0.6.28')), {WORKER})
                self.assertFalse(set(files('0.6.28')) - set(files('0.6.29')))
                self.assertEqual(module.SKILL_NAMES_V0629, module.SKILL_NAMES_V0628)
        self.assertEqual(package.SUITE_FILES_V0629, installer.SUITE_FILES_V0629)
        self.assertEqual(package.SUITE_FILES_V0629, npm.SUITE_FILES_V0629)

    def test_worker_is_required_and_cannot_be_added_to_old_payload(self):
        for version in ('0.6.28', '0.6.29'):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as area:
                source = Path(area) / 'skills'
                fixture_suite(source, version)
                if version == '0.6.29':
                    (source / WORKER).unlink()
                else:
                    (source / WORKER).write_text('unreviewed extra worker', encoding='utf-8')
                output = Path(area) / f'story-skill-{version}.zip'
                with patch.object(package, 'SOURCE', source):
                    with self.assertRaisesRegex(ValueError, 'reviewed file manifest'):
                        package.package(output)
                self.assertFalse(output.exists())

    def test_loader_worker_and_desktop_builder_resolve_the_same_runtime_files(self):
        spec = importlib.util.spec_from_file_location('host_stop_desktop_builder', ROOT / 'scripts/build_desktop_macos.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        runtime_root = ROOT / 'skills/story-skill/scripts'
        self.assertEqual(Path(story.task_worker.__file__).resolve(), runtime_root / 'story_task_worker.py')
        self.assertIs(story.tasks.api.task_worker, story.task_worker)
        self.assertIs(story.task_worker.api.tasks, story.tasks)
        for name in ('story.py', 'story_tasks.py', 'story_task_worker.py'):
            self.assertIn(name, builder.RUNTIME_FILES)
            self.assertIn('story-skill/scripts/' + name, package.SUITE_FILES_V0629)
            self.assertTrue((runtime_root / name).is_file())
        with tempfile.TemporaryDirectory() as area:
            workspace = Path(area)
            ledger = {'id': 'execution-id', 'book_id': 'book-id', 'nonce': 'private-nonce'}
            with patch.object(story.task_worker.subprocess, 'Popen', return_value=SimpleNamespace(pid=1)) as popen:
                story.task_worker._spawn(workspace / 'book', 'task-id', ledger, workspace)
            command = popen.call_args.args[0]
            self.assertEqual(command[:3], [sys.executable, '-B', str(runtime_root / 'story_task_worker.py')])
            self.assertEqual(popen.call_args.kwargs['cwd'], str(workspace))

    def test_new_wrapper_keeps_development_identity(self):
        _, files = npm.wrapper_files('0.6.29')
        readme = files['README.md'].decode()
        self.assertIn('Unreleased development snapshot', readme)
        self.assertNotIn('/blob/v0.6.29/', readme)
        self.assertNotIn('/releases/download/v0.6.29/', readme)

    def test_all_four_cli_commands_parse_and_run_without_a_model_call(self):
        fixture = base.WorkbenchTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task = story.workbench._editor_review_task(fixture.root, 'formal:2')['task']
        before = fixture.authoritative_state()
        root_arguments = ['--book', str(fixture.root), '--task-id', task['id']]

        def run(command, *arguments):
            args = story.parser().parse_args([command, *root_arguments, *arguments])
            return story.run(args)

        worker = story.task_worker
        with patch.object(worker, '_base', return_value=fixture.root.parent / 'private-workers'), \
                patch.object(worker, '_executable', return_value=Path(sys.executable).resolve()), \
                patch.object(worker, '_model_defaults', return_value={}), \
                patch.object(worker, '_spawn', return_value=SimpleNamespace(pid=os.getpid())) as spawn:
            started = run('task-execute', '--version', str(task['version']),
                          '--request-id', 'distribution-cli-smoke', '--execute', '--timeout-seconds', '15')
            execution_id = started['execution']['id']
            self.assertEqual(started['execution']['state'], 'starting')
            observed = run('task-execution')
            self.assertEqual(observed['execution']['id'], execution_id)
            cancelled = run('task-cancel', '--execution-id', execution_id)
            self.assertEqual(cancelled['execution']['state'], 'cancel_requested')
            reconciled = run('task-reconcile', '--execution-id', execution_id)
            self.assertEqual(reconciled['execution']['id'], execution_id)
            self.assertFalse(reconciled['execution']['result_verified'])
            self.assertIsNone(reconciled['task']['applied'])
            self.assertEqual(spawn.call_count, 1)
        self.assertEqual(before, fixture.authoritative_state())


if __name__ == '__main__':
    unittest.main()
