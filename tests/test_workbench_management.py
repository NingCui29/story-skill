"""Authenticated shelf management across HTTP and real editor service leases."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchManagementHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-management-http-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / '合成书架'
        self.w = base.story.workbench
        self.shelf = self.serve(self.w.library_server(self.state, port=0))

    def serve(self, server):
        def run():
            try:
                server.serve_forever(poll_interval=0.01)
            finally:
                server.server_close()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        def close():
            if thread.is_alive():
                server.shutdown()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)
        server.test_thread = thread
        return server

    def post(self, action, payload=None, headers=None):
        if payload and isinstance(payload.get('key'), str) and 'expected_book_id' not in payload:
            entries, _ = self.w._library_load(self.state)
            entry = entries.get(payload['key'])
            if entry:
                payload = {**payload, 'expected_book_id': entry['book_id']}
        server = self.shelf
        sent = {'Origin': 'http://127.0.0.1:%s' % server.server_port,
                'Content-Type': 'application/json', 'X-Story-Token': server.library_token}
        sent.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('POST', '/api/' + action, json.dumps(payload or {}), sent)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def create(self, title='合成作品', name='合成作品', kind='long'):
        root = self.root / name
        status, result = self.post('create-book', {'title': title, 'path': str(root), 'kind': kind})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['created'])
        return root, next(book for book in result['books'] if book['root'] == str(root))

    def preview(self, book):
        status, result = self.post('trash-preview', {'key': book['key']})
        self.assertEqual(status, 200, result)
        return result

    def native_move(self, path):
        target = self.root / '合成回收站'
        Path(path).rename(target)
        return {'system': 'fixture', 'trash_path': str(target)}

    def test_create_remove_undo_does_not_change_state_database(self):
        path, book = self.create(title='测试 <script> 书名')
        before = (path / '.story/state.sqlite3').read_bytes()
        status, removed = self.post('remove-book', {'key': book['key']})
        self.assertEqual(status, 200, removed)
        self.assertEqual(removed['books'], [])
        self.assertTrue(removed['removed'])
        status, restored = self.post('undo-remove', {'receipt_id': removed['undo_id']})
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored['books'][0]['book_id'], book['book_id'])
        self.assertEqual((path / '.story/state.sqlite3').read_bytes(), before)

    def test_remove_unavailable_book_and_relink_by_identity(self):
        path, book = self.create()
        moved = self.root / '迁移后目录'
        path.rename(moved)
        status, result = self.post('relink-book', {'key': book['key'], 'path': str(moved)})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['books'][0]['book_id'], book['book_id'])
        new_key = result['books'][0]['key']
        moved.rename(self.root / '再次迁移')
        status, result = self.post('remove-book', {'key': new_key})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['books'], [])

    def test_every_new_endpoint_keeps_origin_host_and_token_boundary(self):
        path, book = self.create()
        requests = {
            'create-book': {'path': str(self.root / '未创建'), 'title': '新书', 'kind': 'long'},
            'remove-book': {'key': book['key']},
            'undo-remove': {'receipt_id': 'a' * 32},
            'relink-book': {'key': book['key'], 'path': str(path)},
            'trash-preview': {'key': book['key']},
            'trash-book': {'key': book['key'], 'confirmation': self.preview(book)['confirmation'], 'saved': True},
            'stop-book': {'key': book['key'], 'saved': True},
            'book-service': {'key': book['key']},
            'backup-book': {'key': book['key'], 'path': str(self.root / 'no.zip')},
            'restore-backup': {'archive': str(self.root / 'no.zip'), 'destination': str(self.root / '未恢复')},
            'recoveries': {},
        }
        for action, payload in requests.items():
            for headers in ({'Origin': 'https://evil.example'}, {'X-Story-Token': ''}, {'Host': 'evil.example'}):
                with self.subTest(action=action, headers=headers):
                    self.assertEqual(self.post(action, payload, headers)[0], 403)
        self.assertTrue(path.exists())
        self.assertFalse((self.root / '未创建').exists())
        self.assertFalse((self.root / '未恢复').exists())
        self.assertEqual(len(self.post('books')[1]['books']), 1)

    def test_preview_confirmation_is_echoed_and_required_exactly(self):
        path, book = self.create()
        preview = self.preview(book)
        for field in ('root', 'book_id', 'title', 'directory_id'):
            self.assertEqual(preview[field], preview['confirmation'][field])
        with patch.object(base.story.workbench_books, 'move_to_trash') as native:
            for confirmation in (None, {}, {**preview['confirmation'], 'title': '不同书名'}):
                status, result = self.post('trash-book', {'key': book['key'], 'confirmation': confirmation, 'saved': True})
                self.assertEqual(status, 409, result)
                self.assertEqual(result['error'], 'workbench_delete_confirmation')
            for saved in (False, 'true', 1, None):
                status, result = self.post('trash-book', {'key': book['key'], 'confirmation': preview['confirmation'], 'saved': saved})
                self.assertEqual(status, 409, result)
                self.assertEqual(result['error'], 'workbench_unsaved')
        native.assert_not_called()
        self.assertTrue(path.exists())

    def test_running_editor_must_be_explicitly_stopped_before_trash(self):
        path, book = self.create()
        editor = self.serve(self.w.editor_server(path))
        preview = self.preview(book)
        status, service = self.post('book-service', {'key': book['key']})
        self.assertEqual(status, 200, service)
        self.assertTrue(service['running'])
        self.assertNotIn('url', service)
        self.assertNotIn('instance', service)
        with patch.object(base.story.workbench_books, 'move_to_trash') as native:
            status, result = self.post('trash-book', {'key': book['key'], 'confirmation': preview['confirmation'], 'saved': True})
            self.assertEqual(status, 409, result)
            self.assertEqual(result['error'], 'workbench_running')
        native.assert_not_called()
        status, result = self.post('stop-book', {'key': book['key'], 'saved': False})
        self.assertEqual(status, 409, result)
        self.assertTrue(editor.test_thread.is_alive())
        status, result = self.post('stop-book', {'key': book['key'], 'saved': True})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['stop_requested'])
        editor.test_thread.join(timeout=5)
        self.assertFalse(editor.test_thread.is_alive())
        with patch.object(base.story.workbench_books, 'move_to_trash', side_effect=self.native_move):
            status, result = self.post('trash-book', {'key': book['key'], 'confirmation': preview['confirmation'], 'saved': True})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['trashed'])
        self.assertTrue(result['shelf_removed'])
        self.assertEqual(result['books'], [])
        self.assertTrue((self.root / '合成回收站/.story/state.sqlite3').exists())

    def test_unknown_service_never_triggers_stop_or_native_move(self):
        path, book = self.create()
        with patch.object(self.w, '_service_request', return_value={'ok': True, 'running': None}) as service, patch.object(base.story.workbench_books, 'move_to_trash') as native:
            status, result = self.post('trash-book', {'key': book['key'], 'confirmation': self.preview(book)['confirmation'], 'saved': True})
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error'], 'workbench_running')
        service.assert_called_once_with(path)
        native.assert_not_called()

    def test_create_registry_failure_reports_created_but_unregistered(self):
        path = self.root / '已创建未登记'
        with patch.object(self.w, '_library_store', side_effect=OSError('write failed')):
            status, result = self.post('create-book', {'path': str(path), 'title': '新书', 'kind': 'long'})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['created'])
        self.assertFalse(result['registered'])
        self.assertTrue((path / '.story/state.sqlite3').exists())
        self.assertEqual(result['books'], [])

    def test_trash_registry_failure_reports_completed_move_and_retained_entry(self):
        path, book = self.create()
        preview = self.preview(book)
        with patch.object(base.story.workbench_books, 'move_to_trash', side_effect=self.native_move), patch.object(self.w, '_library_store', side_effect=OSError('write failed')):
            status, result = self.post('trash-book', {'key': book['key'], 'confirmation': preview['confirmation'], 'saved': True})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['trashed'])
        self.assertFalse(result['shelf_removed'])
        self.assertFalse(result['books'][0]['available'])
        self.assertIn('warning', result)
        self.assertFalse(path.exists())

    def test_editor_startup_obeys_outside_lifecycle_guard(self):
        path, book = self.create()
        with base.story.workbench_books.lifecycle_guard(base.story, path):
            with self.assertRaises(base.story.StoryError) as caught:
                self.w.editor_server(path)
        self.assertEqual(caught.exception.code, 'workbench_book_busy')
        self.assertFalse((path / '.story/workbench-service.json').exists())


if __name__ == '__main__':
    unittest.main()
