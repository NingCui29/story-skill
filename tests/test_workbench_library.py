"""Independent local bookshelf lifecycle, persisted identity and HTTP boundaries."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-library-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / '独立书架状态'
        self.w = base.story.workbench

    def book(self, name='第一本书'):
        path = self.root / name
        base.story.Book.create(path, name, 'long')
        return path

    def start(self, state=None, port=0, books=None, workbench=None):
        server = (workbench or self.w).library_server(
            state_dir=state or self.state, port=port, library_books=books)
        return self.serve(server)

    def serve(self, server):
        def run():
            try:
                server.serve_forever(poll_interval=0.02)
            finally:
                server.server_close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def close():
            if thread.is_alive():
                server.shutdown()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), 'Workbench server did not stop')

        self.addCleanup(close)
        server.test_thread = thread
        return server

    def stop(self, server, workbench=None, state=None):
        result = (workbench or self.w)._library_service_request(
            state or self.state, 'stop', True)
        self.assertTrue(result['stop_requested'])
        server.test_thread.join(timeout=5)
        self.assertFalse(server.test_thread.is_alive())

    def request(self, server, method, path='/', payload=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.request(method, path,
                           None if payload is None else json.dumps(payload), headers or {})
            response = client.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            client.close()

    def post(self, server, action, payload=None, headers=None):
        request_headers = {
            'Content-Type': 'application/json',
            'X-Story-Token': server.library_token,
            'Origin': f'http://127.0.0.1:{server.server_port}',
        }
        request_headers.update(headers or {})
        status, raw, _ = self.request(
            server, 'POST', '/api/' + action, payload or {}, request_headers)
        return status, json.loads(raw)

    def add(self, server, root):
        status, result = self.post(server, 'add-book', {'path': str(root)})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['ok'])
        return result

    def assert_story_error(self, code, call, *args, **kwargs):
        with self.assertRaises(base.story.StoryError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)

    def test_empty_library_starts_without_a_book_and_serves_root_url(self):
        self.assertFalse(self.w._library_service_request(self.state)['running'])
        server = self.start()
        self.assertEqual(server.editor_url, f'http://127.0.0.1:{server.server_port}/')
        status, page, headers = self.request(server, 'GET')
        self.assertEqual(status, 200)
        self.assertIn('书架', page.decode('utf-8'))
        self.assertIn(server.library_token, page.decode('utf-8'))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        status, result = self.post(server, 'books')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['books'], [])
        self.assertIsNone(result['current'])
        state = self.w._library_service_request(self.state)
        self.assertTrue(state['running'])
        self.assertFalse(state['outdated'])
        self.assertEqual(state['url'], server.editor_url)
        self.assertFalse(list(self.root.rglob('state.sqlite3')))

    def test_added_books_survive_restart_at_the_same_root_address(self):
        first, second = self.book(), self.book('第二本书 # 中文')
        before = {path: (path / '.story/state.sqlite3').read_bytes()
                  for path in (first, second)}
        server = self.start(books=[first])
        result = self.add(server, second)
        self.assertEqual({row['root'] for row in result['books']}, {str(first), str(second)})
        duplicate = self.add(server, second)
        self.assertEqual(len(duplicate['books']), 2)
        keys = {row['root']: row['key'] for row in result['books']}
        for path in (first, second):
            self.assertEqual((path / '.story/state.sqlite3').read_bytes(), before[path])
            self.assertFalse((path / '.story/workbench-service.json').exists())
        url, port = server.editor_url, server.server_port
        self.stop(server)
        self.assertFalse(self.w._library_service_request(self.state)['running'])
        restarted = self.start(port=port)
        self.assertEqual(restarted.editor_url, url)
        self.assertNotEqual(restarted.library_token, server.library_token)
        status, result = self.post(restarted, 'books')
        self.assertEqual(status, 200, result)
        self.assertEqual({row['root']: row['key'] for row in result['books']}, keys)
        self.assertTrue(all(row['available'] for row in result['books']))

    def test_registered_books_open_existing_editors_without_changing_shelf_url(self):
        first, second = self.book(), self.book('第二本书')
        editors = {str(root): self.serve(self.w.editor_server(root))
                   for root in (first, second)}
        shelf = self.start(books=[first, second])
        original_url = shelf.editor_url
        status, catalog = self.post(shelf, 'books')
        self.assertEqual(status, 200, catalog)
        with patch.object(self.w.subprocess, 'Popen') as launch:
            for row in catalog['books']:
                status, result = self.post(shelf, 'open-book', {'key': row['key']})
                self.assertEqual(status, 200, result)
                self.assertEqual(result['url'], editors[row['root']].editor_url)
            launch.assert_not_called()
        self.assertEqual(shelf.editor_url, original_url)
        self.assertEqual(self.request(shelf, 'GET')[0], 200)

    def test_unknown_book_key_does_not_start_an_editor(self):
        shelf = self.start()
        with patch.object(self.w.subprocess, 'Popen') as launch:
            status, result = self.post(shelf, 'open-book', {'key': 'not-registered'})
            self.assertEqual(status, 409, result)
            self.assertEqual(result['error'], 'invalid_input')
            launch.assert_not_called()

    def test_foreign_host_origin_and_missing_token_cannot_read_or_mutate_library(self):
        root = self.book()
        shelf = self.start()
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'},
                        {'Origin': ''}, {'X-Story-Token': 'wrong'}, {'X-Story-Token': ''}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(shelf, 'books', headers=headers)[0], 403)
                self.assertEqual(self.post(shelf, 'add-book', {'path': str(root)}, headers)[0], 403)
        self.assertEqual(self.request(shelf, 'GET', headers={'Host': 'evil.example'})[0], 404)
        self.assertEqual(self.post(shelf, 'books')[1]['books'], [])

    def test_link_navigation_opens_shelf_but_cross_site_embedding_is_rejected(self):
        shelf = self.start()
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                   'Sec-Fetch-Dest': 'document'}
        self.assertEqual(self.request(shelf, 'GET', headers=headers)[0], 200)
        for mode, destination in (('navigate', 'iframe'), ('no-cors', 'image'), ('cors', 'empty')):
            with self.subTest(mode=mode, destination=destination):
                status, _, _ = self.request(shelf, 'GET', headers={
                    'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': mode,
                    'Sec-Fetch-Dest': destination})
                self.assertIn(status, (403, 404))

    def test_directory_replacement_cannot_open_a_different_book_under_registered_key(self):
        root = self.book()
        shelf = self.start(books=[root])
        row = self.post(shelf, 'books')[1]['books'][0]
        root.rename(root.with_name('原作品已移动'))
        self.book()
        with patch.object(self.w.subprocess, 'Popen') as launch:
            status, result = self.post(shelf, 'open-book', {'key': row['key']})
            self.assertEqual(status, 409, result)
            self.assertEqual(result['error'], 'workbench_book_changed')
            launch.assert_not_called()
        catalog = self.post(shelf, 'books')[1]
        self.assertEqual(catalog['books'][0]['key'], row['key'])
        self.assertFalse(catalog['books'][0]['available'])

    @unittest.skipIf(os.name == 'nt', 'Symbolic links require Windows privileges')
    def test_directory_replaced_by_symlink_cannot_open_or_reregister_its_target(self):
        root, outside = self.book(), self.book('未登记作品')
        shelf = self.start(books=[root])
        row = self.post(shelf, 'books')[1]['books'][0]
        root.rename(root.with_name('原作品已移动'))
        root.symlink_to(outside, target_is_directory=True)
        with patch.object(self.w.subprocess, 'Popen') as launch:
            status, result = self.post(shelf, 'open-book', {'key': row['key']})
            self.assertEqual(status, 409, result)
            self.assertIn(result['error'], ('workbench_book_changed', 'path_escape'))
            launch.assert_not_called()
        status, result = self.post(shelf, 'add-book', {'path': str(root)})
        self.assertNotEqual(status, 200, result)
        self.assertEqual(len(self.post(shelf, 'books')[1]['books']), 1)

    def test_unavailable_neighbor_does_not_prevent_healthy_book_from_opening(self):
        healthy, unavailable = self.book(), self.book('暂时不可用作品')
        editor = self.serve(self.w.editor_server(healthy))
        shelf = self.start(books=[healthy, unavailable])
        unavailable.rename(unavailable.with_name('移动后的作品'))
        status, catalog = self.post(shelf, 'books')
        self.assertEqual(status, 200, catalog)
        rows = {row['root']: row for row in catalog['books']}
        self.assertTrue(rows[str(healthy)]['available'])
        self.assertFalse(rows[str(unavailable)]['available'])
        self.assertTrue(rows[str(unavailable)]['error'])
        with patch.object(self.w.subprocess, 'Popen') as launch:
            status, result = self.post(shelf, 'open-book', {'key': rows[str(healthy)]['key']})
            self.assertEqual(status, 200, result)
            self.assertEqual(result['url'], editor.editor_url)
            launch.assert_not_called()

    def test_external_registry_change_is_preserved_when_add_book_conflicts(self):
        first, second = self.book(), self.book('第二本书')
        shelf = self.start(books=[first])
        registry = self.state / 'library.json'
        changed = json.dumps({'version': 1, 'books': []}, indent=4).encode('utf-8')
        registry.write_bytes(changed)
        status, result = self.post(shelf, 'add-book', {'path': str(second)})
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error'], 'workbench_library_changed')
        self.assertEqual(registry.read_bytes(), changed)
        self.assertFalse((second / '.story/workbench-service.json').exists())

    def test_stop_requires_saved_confirmation_and_matching_instance(self):
        shelf = self.start()
        state = self.w._library_service_request(self.state)
        self.assert_story_error('workbench_running', self.w.library_server,
                                state_dir=self.state, port=0)
        self.assert_story_error('workbench_unsaved', self.w._library_service_request,
                                self.state, 'stop')
        for payload in ({'instance': state['instance']},
                        {'instance': '0' * 32, 'saved': True}):
            status, _ = self.post(shelf, 'stop', payload)
            self.assertEqual(status, 409)
            self.assertTrue(shelf.test_thread.is_alive())
        self.stop(shelf)
        self.assertFalse(self.w._library_service_request(self.state)['running'])

    def test_conflicting_port_does_not_leak_the_library_lease(self):
        occupied = socket.socket()
        self.addCleanup(occupied.close)
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        with self.assertRaises((base.story.StoryError, OSError)):
            self.w.library_server(state_dir=self.state, port=occupied.getsockname()[1])
        shelf = self.start()
        self.assertTrue(self.w._library_service_request(self.state)['running'])
        self.assertEqual(self.request(shelf, 'GET')[0], 200)

    def test_status_detects_changes_to_a_loaded_runtime_dependency(self):
        runtime = self.root / 'runtime'
        shutil.copytree(base.TOOL.parent, runtime)
        spec = importlib.util.spec_from_file_location('library_runtime_story', runtime / 'story.py')
        story = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(story)
        self.start(workbench=story.workbench)
        initial = story.workbench._library_service_request(self.state)
        self.assertFalse(initial['outdated'])
        source = runtime / 'story_outline.py'
        source.write_bytes(source.read_bytes() + b'\n# changed after the bookshelf started\n')
        changed = story.workbench._library_service_request(self.state)
        self.assertTrue(changed['running'])
        self.assertTrue(changed['outdated'])
        self.assertEqual(changed['runtime_sha256'], initial['runtime_sha256'])


if __name__ == '__main__':
    unittest.main()
