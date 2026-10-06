"""Book type and identity remain distinct across shelves, registries and workspaces."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchBookIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-book-identity-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.w = base.story.workbench
        self.title = '同名作品'

    def book(self, name, kind):
        root = self.area / name
        base.story.Book.create(root, self.title, kind)
        return root

    def same_named_books(self):
        return {kind: self.book(kind, kind) for kind in ('long', 'short', 'analysis')}

    def state_bytes(self, roots):
        return {str(root): (root / '.story/state.sqlite3').read_bytes() for root in roots}

    def assert_windows_rename_blocked(self, root, target):
        def files():
            # The lease byte is locked against reads through a second Windows handle.
            return {path.relative_to(root).as_posix(): (
                        None if path.name == 'workbench-server.lock' else path.read_bytes(),
                        path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ino)
                    for path in root.rglob('*') if path.is_file()}

        before = files()
        entry = self.w._library_entry(root)
        with self.assertRaises(PermissionError) as caught:
            root.rename(target)
        self.assertEqual(caught.exception.winerror, 32)
        self.assertFalse(target.exists())
        self.assertEqual(self.w._library_entry(root)['book_id'], entry['book_id'])
        self.assertEqual(files(), before)

    def registry(self, rows):
        state = self.area / '独立书架'
        state.mkdir(exist_ok=True)
        raw = json.dumps({'version': 1, 'books': rows}, ensure_ascii=False).encode('utf-8')
        (state / 'library.json').write_bytes(raw)
        return state, raw

    def legacy_entry(self, root):
        row = self.w._library_entry(root)
        return {key: row[key] for key in ('key', 'root', 'book_id', 'title')}

    def serve(self, server):
        def run():
            try:
                server.serve_forever(poll_interval=0.02)
            finally:
                server.server_close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def stop():
            if thread.is_alive():
                server.shutdown()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        self.addCleanup(stop)
        server.test_thread = thread
        return server

    def editor_post(self, server, action, payload=None):
        token = server.editor_url.rstrip('/').split('/')[-1]
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.request('POST', '/' + token + '/api/' + action, json.dumps(payload or {}), {
                'Content-Type': 'application/json', 'X-Story-Token': token,
                'Origin': f'http://127.0.0.1:{server.server_port}',
            })
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_same_named_long_short_and_analysis_books_have_distinct_identities(self):
        roots = self.same_named_books()
        before = self.state_bytes(roots.values())
        library = self.w._library_roots(roots['long'], [roots['short'], roots['analysis']])
        self.assertEqual(len(library), 3)
        self.assertEqual(len({row['key'] for row in library.values()}), 3)
        self.assertEqual(len({row['book_id'] for row in library.values()}), 3)
        self.assertEqual({row['title'] for row in library.values()}, {self.title})
        self.assertEqual({row['kind'] for row in library.values()}, set(roots))
        self.assertTrue(all(row['kind_verified'] for row in library.values()))
        self.assertEqual(self.state_bytes(roots.values()), before)

    def test_workspace_reports_actual_book_id_and_kind_for_each_type(self):
        roots = self.same_named_books()
        for kind, root in roots.items():
            with self.subTest(kind=kind):
                entry = self.w._library_entry(root)
                catalog = self.w._editor_catalog(root)
                workspace = catalog['workspace']
                self.assertEqual(workspace['title'], self.title)
                self.assertEqual(workspace['book_id'], entry['book_id'])
                self.assertEqual(workspace['kind'], kind)
                self.assertTrue(workspace['kind_verified'])
                self.assertEqual(catalog['book_root'], str(root))

    def test_editor_books_and_catalog_http_preserve_type_and_identity(self):
        roots = self.same_named_books()
        before = self.state_bytes(roots.values())
        server = self.serve(self.w.editor_server(roots['long'], library_books=[roots['short'], roots['analysis']]))
        status, books = self.editor_post(server, 'books')
        self.assertEqual(status, 200, books)
        rows = {row['kind']: row for row in books['books']}
        self.assertEqual(set(rows), set(roots))
        for kind, root in roots.items():
            self.assertEqual(rows[kind]['root'], str(root))
            self.assertEqual(rows[kind]['book_id'], self.w._library_entry(root)['book_id'])
            self.assertTrue(rows[kind]['kind_verified'])
        status, catalog = self.editor_post(server, 'catalog')
        self.assertEqual(status, 200, catalog)
        self.assertEqual(catalog['workspace']['kind'], 'long')
        self.assertEqual(catalog['workspace']['book_id'], rows['long']['book_id'])
        self.assertEqual(self.state_bytes(roots.values()), before)

    def test_version_one_registry_without_kind_reads_real_types_without_rewriting(self):
        roots = self.same_named_books()
        before = self.state_bytes(roots.values())
        legacy = [self.legacy_entry(root) for root in roots.values()]
        state, raw = self.registry(legacy)
        entries, digest = self.w._library_load(state)
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual({row['kind'] for row in entries.values()}, set(roots))
        self.assertTrue(all(not row['kind_verified'] for row in entries.values()))
        catalog = self.w._library_catalog(entries)
        self.assertTrue(all(row['available'] and row['kind_verified'] for row in catalog['books']))
        self.assertEqual({row['book_id'] for row in catalog['books']}, {row['book_id'] for row in legacy})
        self.assertEqual((state / 'library.json').read_bytes(), raw)
        self.assertEqual(self.state_bytes(roots.values()), before)

    def test_unavailable_legacy_book_has_unknown_type_instead_of_inference(self):
        root = self.book('旧版长篇目录', 'analysis')
        legacy = self.legacy_entry(root)
        state, raw = self.registry([legacy])
        root.rename(self.area / '作品已移走')
        entries, _ = self.w._library_load(state)
        saved = entries[legacy['key']]
        self.assertIsNone(saved['kind'])
        self.assertFalse(saved['kind_verified'])
        row = self.w._library_catalog(entries)['books'][0]
        self.assertFalse(row['available'])
        self.assertFalse(row['kind_verified'])
        self.assertIsNone(row['kind'])
        self.assertEqual(row['book_id'], legacy['book_id'])
        self.assertEqual((state / 'library.json').read_bytes(), raw)

    def test_unavailable_book_keeps_recorded_type_but_marks_it_unverified(self):
        root = self.book('作品分析目录', 'short')
        entry = self.w._library_entry(root)
        root.rename(self.area / '作品已移走')
        row = self.w._library_catalog({entry['key']: entry})['books'][0]
        self.assertEqual(row['kind'], 'short')
        self.assertFalse(row['kind_verified'])
        self.assertFalse(row['available'])
        self.assertEqual(row['book_id'], entry['book_id'])

    def test_replaced_directory_cannot_open_another_same_named_book(self):
        root = self.book('同名目录', 'long')
        library = self.w._library_roots(root)
        entry = next(iter(library.values()))
        root.rename(self.area / '原作品')
        replacement = self.book('同名目录', 'short')
        self.assertNotEqual(self.w._library_entry(replacement)['book_id'], entry['book_id'])
        with patch.object(self.w.subprocess, 'Popen') as launch, patch.object(self.w, '_service_request') as status:
            self.fixture_assert_error('workbench_book_changed', self.w._library_open, library, entry['key'])
            launch.assert_not_called()
            status.assert_not_called()
        row = self.w._library_catalog(library)['books'][0]
        self.assertFalse(row['available'])
        self.assertFalse(row['kind_verified'])
        self.assertEqual(row['kind'], 'long')
        self.assertEqual(row['book_id'], entry['book_id'])

    def test_legacy_registry_cannot_backfill_type_from_a_replaced_book(self):
        root = self.book('同名目录', 'long')
        entry = self.legacy_entry(root)
        state, raw = self.registry([entry])
        root.rename(self.area / '原作品')
        self.book('同名目录', 'analysis')
        entries, _ = self.w._library_load(state)
        self.assertIsNone(entries[entry['key']]['kind'])
        row = self.w._library_catalog(entries)['books'][0]
        self.assertFalse(row['available'])
        self.assertFalse(row['kind_verified'])
        self.assertIsNone(row['kind'])
        self.assertEqual(row['book_id'], entry['book_id'])
        self.assertEqual((state / 'library.json').read_bytes(), raw)

    def test_registry_rejects_unknown_saved_kind(self):
        root = self.book('作品', 'long')
        entry = self.w._library_entry(root)
        entry['kind'] = 'guessed_from_folder'
        state, raw = self.registry([entry])
        self.fixture_assert_error('workbench_library_invalid', self.w._library_load, state)
        self.assertEqual((state / 'library.json').read_bytes(), raw)

    def test_legacy_in_memory_library_without_book_id_keeps_its_opening_contract(self):
        root = self.area / '旧版目录'
        library = {'legacy': {'key': 'legacy', 'root': str(root), 'title': self.title}}
        with patch.object(self.w, '_service_request', return_value={'running': True, 'url': 'http://127.0.0.1:1234/old/'}):
            opened = self.w._library_open(library, 'legacy')
        self.assertEqual(opened['url'], 'http://127.0.0.1:1234/old/')

    def test_open_editor_rejects_replacement_book_reads_and_recovery_but_can_stop(self):
        root = self.book('同名目录', 'long')
        source_path = '.story/drafts/第1章 候选.md'
        source = root / source_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('原作品的候选正文。', encoding='utf-8')
        server = self.serve(self.w.editor_server(root))
        record = json.loads((root / '.story/workbench-service.json').read_bytes())
        status, opened = self.editor_post(server, 'open', {'id': 'file:' + source_path})
        self.assertEqual(status, 200, opened)
        current_key = self.w._library_entry(root)['key']
        if os.name == 'nt':
            # Windows prevents replacing the directory while the editor owns its lease.
            self.assert_windows_rename_blocked(root, self.area / '原作品已移动')
            before = self.state_bytes([root]), source.read_bytes()
            status, catalog = self.editor_post(server, 'catalog')
            self.assertEqual(status, 200, catalog)
            self.assertEqual(catalog['workspace']['book_id'], record['book_id'])
            status, reopened = self.editor_post(server, 'open', {'id': 'file:' + source_path})
            self.assertEqual(status, 200, reopened)
            self.assertEqual(reopened['text'], opened['text'])
            self.assertEqual(reopened['sha256'], opened['sha256'])
            self.assertEqual((self.state_bytes([root]), source.read_bytes()), before)
            return
        root.rename(self.area / '原作品已移动')
        self.book('同名目录', 'short')
        (root / source_path).parent.mkdir(parents=True, exist_ok=True)
        (root / source_path).write_text('替换作品的候选正文。', encoding='utf-8')

        def files():
            return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in root.rglob('*') if path.is_file()}

        before = files()
        editing = {**opened, 'text': '旧页面中仍待保存的编辑。', 'recovery_key': 'original-book-recovery'}
        requests = {
            'catalog': {}, 'open': {'id': 'file:' + source_path},
            'recover': editing, 'save': editing, 'metrics': editing,
            'review-task': {'id': opened['id'], 'sha256': opened['sha256']},
            'search': {'query': '替换作品'}, 'books': {}, 'open-book': {'key': current_key},
        }
        for action, payload in requests.items():
            with self.subTest(action=action):
                status, result = self.editor_post(server, action, payload)
                self.assertEqual(status, 409, result)
                self.assertEqual(result['error'], 'workbench_book_changed')
                self.assertEqual(files(), before)
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            route = '/' + server.editor_url.rstrip('/').split('/')[-1] + '/'
            client.request('GET', route)
            response = client.getresponse()
            self.assertEqual(response.status, 409)
            self.assertEqual(json.loads(response.read())['error'], 'workbench_book_changed')
        finally:
            client.close()
        status, session = self.editor_post(server, 'session', {'instance': record['instance']})
        self.assertEqual(status, 200, session)
        self.assertTrue(session['running'])
        self.assertEqual(session['book_id'], record['book_id'])
        self.assertNotEqual(session['book_id'], self.w._library_entry(root)['book_id'])
        status, unsaved = self.editor_post(server, 'stop', {'instance': record['instance'], 'saved': False})
        self.assertEqual(status, 409, unsaved)
        status, stopped = self.editor_post(server, 'stop', {'instance': record['instance'], 'saved': True})
        self.assertEqual(status, 200, stopped)
        self.assertTrue(stopped['stop_requested'])
        server.test_thread.join(timeout=5)
        self.assertFalse(server.test_thread.is_alive())
        self.assertEqual(files(), before)

    def test_editor_startup_rejects_a_book_replaced_after_initial_snapshot(self):
        root = self.book('同名目录', 'long')
        library_roots = self.w._library_roots

        def replace_before_library(*args, **kwargs):
            root.rename(self.area / '原作品已移动')
            self.book('同名目录', 'short')
            return library_roots(*args, **kwargs)

        with patch.object(self.w, '_library_roots', side_effect=replace_before_library):
            self.fixture_assert_error('workbench_book_changed', self.w.editor_server, root)
        self.assertFalse((root / '.story/workbench-service.json').exists())
        self.assertFalse((root / '.story/workbench-server.lock').exists())

    def test_stopping_old_editor_does_not_update_copied_record_in_replacement_book(self):
        root = self.book('同名目录', 'long')
        server = self.serve(self.w.editor_server(root))
        service_path = '.story/workbench-service.json'
        raw = (root / service_path).read_bytes()
        record = json.loads(raw)
        if os.name == 'nt':
            self.assert_windows_rename_blocked(root, self.area / '原作品已移动')
            before = self.state_bytes([root])
            status, stopped = self.editor_post(server, 'stop', {'instance': record['instance'], 'saved': True})
            self.assertEqual(status, 200, stopped)
            self.assertTrue(stopped['stop_requested'])
            server.test_thread.join(timeout=5)
            self.assertFalse(server.test_thread.is_alive())
            stopped_record = json.loads((root / service_path).read_bytes())
            self.assertTrue(stopped_record['stopped'])
            self.assertEqual(stopped_record['book_id'], record['book_id'])
            self.assertEqual(self.w._library_entry(root)['book_id'], record['book_id'])
            self.assertEqual(self.state_bytes([root]), before)
            return
        root.rename(self.area / '原作品已移动')
        self.book('同名目录', 'short')
        # A copied stale record has the same instance, but belongs to another book.
        (root / service_path).write_bytes(raw)

        def files():
            return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in root.rglob('*') if path.is_file()}

        before = files()
        status, stopped = self.editor_post(server, 'stop', {'instance': record['instance'], 'saved': True})
        self.assertEqual(status, 200, stopped)
        self.assertTrue(stopped['stop_requested'])
        server.test_thread.join(timeout=5)
        self.assertFalse(server.test_thread.is_alive())
        self.assertEqual(files(), before)
        self.assertEqual((root / service_path).read_bytes(), raw)
        self.assertFalse((root / '.story/.workbench-backups').exists())

    def test_replacement_before_service_registration_is_rejected_and_releases_lease(self):
        root = self.book('同名目录', 'long')
        moved = self.area / '原作品已移动'
        author_read = self.w._author_read
        replacement = {}
        original_id = self.w._library_entry(root)['book_id']
        original_state = self.state_bytes([root])

        def files():
            return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in root.rglob('*') if path.is_file()}

        def replace_before_registration(read_root, relative, *args, **kwargs):
            if Path(read_root) == root and relative == '.story/workbench-service.json' and not replacement:
                if os.name == 'nt':
                    self.assert_windows_rename_blocked(root, moved)
                    replacement['blocked'] = True
                    return author_read(read_root, relative, *args, **kwargs)
                root.rename(moved)
                self.book('同名目录', 'short')
                replacement['before'] = files()
                raise FileNotFoundError('registration record is absent in the replacement book')
            return author_read(read_root, relative, *args, **kwargs)

        with patch.object(self.w, '_author_read', side_effect=replace_before_registration):
            if os.name == 'nt':
                started = self.w.editor_server(root)
            else:
                self.fixture_assert_error('workbench_book_changed', self.w.editor_server, root)
        if os.name == 'nt':
            try:
                self.assertTrue(replacement['blocked'])
                record = json.loads((root / '.story/workbench-service.json').read_bytes())
                self.assertEqual(record['book_id'], original_id)
                self.assertEqual(self.state_bytes([root]), original_state)
            finally:
                started.server_close()
            restarted = self.w.editor_server(root)
            restarted.server_close()
            self.assertEqual(self.w._library_entry(root)['book_id'], original_id)
            self.assertEqual(self.state_bytes([root]), original_state)
            return
        self.assertEqual(files(), replacement['before'])
        self.assertFalse((root / '.story/workbench-service.json').exists())
        self.assertFalse((root / '.story/.workbench-backups').exists())
        # The failed startup held the moved original lock; it must be released.
        restarted = self.w.editor_server(moved)
        restarted.server_close()
        self.assertEqual(files(), replacement['before'])

    def fixture_assert_error(self, code, call, *args):
        with self.assertRaises(base.story.StoryError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code, caught.exception.details)


if __name__ == '__main__':
    unittest.main()
