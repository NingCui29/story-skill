"""Workbench runtime identity, damaged recovery records and unavailable material roots."""
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import test_workbench as base


class WorkbenchRuntimeRegressions(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.w = base.story.workbench

    def copied_runtime(self):
        path = Path(self.fixture.temp.name).resolve() / 'runtime'
        shutil.copytree(base.TOOL.parent, path)
        spec = importlib.util.spec_from_file_location('workbench_runtime_regression_story', path / 'story.py')
        story = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(story)
        return story, path

    def start_server(self, workbench=None):
        server = (workbench or self.w).editor_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)
        return server

    def post(self, server, action, payload):
        token = server.editor_url.rstrip('/').split('/')[-1]
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.request('POST', '/' + token + '/api/' + action, json.dumps(payload), {
                'Content-Type': 'application/json', 'X-Story-Token': token,
                'Origin': f'http://127.0.0.1:{server.server_port}',
            })
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_runtime_identity_covers_core_and_every_loaded_story_extension(self):
        story, path = self.copied_runtime()
        self.start_server(story.workbench)
        expected = {'story.py', 'story_storage.py', 'story_search.py', 'story_world.py',
                    'story_history.py', 'story_publish.py', 'story_workbench.py',
                    'story_outline.py', 'story_punctuation.py'}
        self.assertEqual({source.name for source in story.workbench._runtime_paths}, expected)
        initial = story.workbench._service_request(self.root)
        self.assertFalse(initial['outdated'])
        for name in sorted(expected):
            with self.subTest(component=name):
                source = path / name
                original = source.read_bytes()
                try:
                    source.write_bytes(original + b'\n# runtime dependency changed\n')
                    changed = story.workbench._service_request(self.root)
                    self.assertTrue(changed['running'])
                    self.assertTrue(changed['outdated'])
                    self.assertEqual(changed['runtime_sha256'], initial['runtime_sha256'])
                finally:
                    source.write_bytes(original)
        self.assertFalse(story.workbench._service_request(self.root)['outdated'])

    def test_status_from_new_process_detects_changed_core_and_extension(self):
        story, path = self.copied_runtime()
        self.start_server(story.workbench)
        for name in ('story.py', 'story_outline.py'):
            with self.subTest(component=name):
                source = path / name
                original = source.read_bytes()
                try:
                    source.write_bytes(original + b'\n# installed runtime upgraded\n')
                    result = subprocess.run(
                        [sys.executable, '-B', str(path / 'story.py'), 'workbench-status', '--book', str(self.root)],
                        capture_output=True, text=True, encoding='utf-8', timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    status = json.loads(result.stdout)
                    self.assertTrue(status['running'])
                    self.assertTrue(status['outdated'])
                finally:
                    source.write_bytes(original)

    def test_server_created_after_disk_update_keeps_its_loaded_identity(self):
        story, path = self.copied_runtime()
        loaded = story.workbench._loaded_runtime_sha256
        source = path / 'story.py'
        source.write_bytes(source.read_bytes() + b'\n# changed after import, before serve\n')
        self.start_server(story.workbench)
        status = story.workbench._service_request(self.root)
        self.assertEqual(status['runtime_sha256'], loaded)
        self.assertTrue(status['outdated'])

    def test_missing_or_unreadable_runtime_dependency_is_never_current(self):
        story, path = self.copied_runtime()
        self.start_server(story.workbench)
        source = path / 'story_outline.py'
        original = source.read_bytes()
        source.unlink()
        try:
            missing = story.workbench._service_request(self.root)
            self.assertTrue(missing['outdated'])
            self.assertIn(source.name, missing['runtime_check_error'])
        finally:
            source.write_bytes(original)
        read_bytes = Path.read_bytes

        def denied(current):
            if current == source:
                raise PermissionError('runtime dependency is unreadable')
            return read_bytes(current)

        with patch.object(Path, 'read_bytes', denied):
            unreadable = story.workbench._service_request(self.root)
        self.assertTrue(unreadable['outdated'])
        self.assertIn('unreadable', unreadable['runtime_check_error'])
        self.assertFalse(story.workbench._service_request(self.root)['outdated'])

    def test_legacy_service_with_only_workbench_hash_is_marked_outdated(self):
        server = self.start_server()
        status = self.w._service_request(self.root)
        status.pop('runtime_sha256')
        status['code_sha256'] = hashlib.sha256(base.TOOL.with_name('story_workbench.py').read_bytes()).hexdigest()
        response = Mock(status=200)
        response.read.return_value = json.dumps(status).encode('utf-8')
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(self.w.http.client, 'HTTPConnection', return_value=connection):
            legacy = self.w._service_request(self.root)
        self.assertTrue(legacy['running'])
        self.assertTrue(legacy['outdated'])
        self.assertEqual(legacy['url'], server.editor_url)

    def test_deep_pending_json_returns_readable_http_error_and_preserves_files(self):
        relative = '.story/drafts/workbench/第1章 受损恢复.md.pending.json'
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (b'{"version":1,"target":' + json.dumps(relative[:-13]).encode('utf-8') +
               b',"meta":{"extra":' + b'[' * 10000 + b'0' + b']' * 10000 + b'},"text":"saved"}')
        target.write_bytes(raw)
        before = self.fixture.authoritative_state()
        server = self.start_server()
        status, damaged = self.post(server, 'open', {'id': 'pending:' + relative})
        self.assertEqual(status, 409)
        self.assertEqual(damaged['error'], 'workbench_pending_invalid')
        self.assertEqual(damaged['details']['path'], relative)
        self.assertEqual(target.read_bytes(), raw)
        status, formal = self.post(server, 'open', {'id': 'formal:1'})
        self.assertEqual(status, 200)
        self.assertEqual(formal['kind'], 'formal')
        self.assertEqual(self.fixture.authoritative_state(), before)

    def material_roots(self):
        outer = Path(self.fixture.temp.name).resolve() / '关联材料'
        outer.mkdir()
        (outer / '提纲.md').write_text('外部独有检索词', encoding='utf-8')
        return outer, self.w._material_roots([outer])

    def assert_unavailable_material_root(self, roots, outer):
        catalog = self.w._editor_catalog(self.root, material_roots=roots)
        self.assertTrue(catalog['chapters'])
        self.assertFalse(any(row.get('external') for row in catalog['related_files']))
        self.assertTrue(any(str(outer) in warning for warning in catalog['warnings']))
        search = self.w._editor_search(self.root, '钥匙', material_roots=roots)
        self.assertGreater(search['total'], 0)
        self.assertFalse(search['complete'])
        self.assertTrue(any(str(outer) in warning for warning in search['warnings']))
        self.assertFalse(any(row.get('external') for row in search['results']))

    def test_moved_material_root_does_not_break_catalog_or_search(self):
        outer, roots = self.material_roots()
        outer.rename(outer.with_name('材料已移走'))
        self.assert_unavailable_material_root(roots, outer)

    def test_unreadable_material_root_does_not_break_catalog_or_search(self):
        outer, roots = self.material_roots()
        iterdir = Path.iterdir

        def denied(current):
            if current == outer:
                raise PermissionError('selected material directory is unreadable')
            return iterdir(current)

        with patch.object(Path, 'iterdir', denied):
            self.assert_unavailable_material_root(roots, outer)

    @unittest.skipIf(os.name == 'nt', 'Symbolic links require Windows privileges')
    def test_material_root_replaced_by_link_never_exposes_its_new_target(self):
        outer, roots = self.material_roots()
        outer.rename(outer.with_name('原始材料'))
        outside = outer.with_name('未授权目录')
        outside.mkdir()
        (outside / 'private.md').write_text('钥匙 外部独有检索词', encoding='utf-8')
        outer.symlink_to(outside, target_is_directory=True)
        self.assert_unavailable_material_root(roots, outer)
        warnings = []
        self.assertEqual(self.w._linked_material_files(roots, warnings), [])
        self.assertTrue(warnings)


if __name__ == '__main__':
    unittest.main()
