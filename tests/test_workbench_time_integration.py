"""Real bookshelf responses retain author activity dates across service lifecycles."""
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchTimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-time-integration-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.state = self.area / '独立书架'
        self.w = base.story.workbench

    def book(self, name, kind='long'):
        root = self.area / name
        base.story.Book.create(root, '同名作品', kind)
        return root

    def event(self, root, timestamp):
        book = base.story.Book(root)
        try:
            with book.transaction():
                book.event('integration_clock', {'fixture': True})
                book.db.execute('UPDATE events SET created=?', (timestamp,))
        finally:
            book.close()

    def author_file(self, root, timestamp):
        path = root / '创作约定.md'
        path.write_text('# 创作约定\n题材：都市\n', encoding='utf-8')
        moment = datetime.fromisoformat(timestamp).timestamp()
        os.utime(path, (moment, moment))

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

    def start(self, books=None, port=0):
        return self.serve(self.w.library_server(
            state_dir=self.state, port=port, library_books=books))

    def stop(self, server):
        result = self.w._library_service_request(self.state, 'stop', True)
        self.assertTrue(result['stop_requested'])
        server.test_thread.join(timeout=5)
        self.assertFalse(server.test_thread.is_alive())

    def post(self, server, action='books', payload=None):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.request('POST', '/api/' + action, json.dumps(payload or {}), {
                'Content-Type': 'application/json',
                'X-Story-Token': server.library_token,
                'Origin': f'http://127.0.0.1:{server.server_port}',
            })
            response = client.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200, result)
            self.assertTrue(result['ok'])
            return result['books']
        finally:
            client.close()

    def page(self, server):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.request('GET', '/')
            response = client.getresponse()
            page = response.read().decode('utf-8')
            self.assertEqual(response.status, 200)
            return page
        finally:
            client.close()

    def activity(self, rows):
        fields = ('updated_at', 'updated_at_verified', 'updated_at_source', 'updated_at_note')
        return {row['key']: {field: row[field] for field in fields} for row in rows}

    def assert_utc(self, row, timestamp):
        self.assertTrue(row['updated_at_verified'])
        actual = datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00'))
        self.assertEqual(actual.utcoffset().total_seconds(), 0)
        self.assertEqual(actual, datetime.fromisoformat(timestamp))
        self.assertTrue(row['updated_at_source'])

    def assert_unknown(self, row):
        self.assertIsNone(row['updated_at'])
        self.assertFalse(row['updated_at_verified'])
        self.assertIn('updated_at_source', row)
        self.assertTrue(row['updated_at_note'])

    def sorted_keys(self, page, rows, order):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required for rendered bookshelf ordering')
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        source = script[script.index('function bookName'):script.index('async function refresh')]
        mock = r"""
const nodes={},$=id=>nodes[id]||(nodes[id]={value:'',setAttribute(){},replaceChildren(){},append(){}});
const localStorage={getItem(){return null;},setItem(){}};
const document={createElement(){return {setAttribute(){},append(){}};},createTextNode(text){return {textContent:text};}};
"""
        program = mock + source + '\n' + (
            "const input=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "process.stdout.write(JSON.stringify(sortShelfBooks(input.books,input.order).map(b=>b.key)));"
        )
        result = subprocess.run([node, '-e', program],
                                input=json.dumps({'books': rows, 'order': order}),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_same_named_event_and_author_dates_survive_refresh_and_service_restart(self):
        event_book, author_book = self.book('甲目录'), self.book('乙目录', 'analysis')
        self.event(event_book, '2024-02-03 12:00:00')
        self.author_file(author_book, '2024-03-01T08:30:00+00:00')
        states = {root: (root / '.story/state.sqlite3').read_bytes()
                  for root in (event_book, author_book)}
        shelf = self.start([event_book])
        initial = self.post(shelf, 'add-book', {'path': str(author_book)})
        rows = {row['root']: row for row in initial}
        self.assertEqual({row['title'] for row in initial}, {'同名作品'})
        self.assertEqual(len({row['book_id'] for row in initial}), 2)
        self.assert_utc(rows[str(event_book)], '2024-02-03T12:00:00+00:00')
        self.assert_utc(rows[str(author_book)], '2024-03-01T08:30:00+00:00')
        expected = self.activity(initial)
        registry = self.state / 'library.json'
        registry_bytes = registry.read_bytes()
        with patch.object(self.w, '_utc_now', return_value='2099-12-31T23:59:59+00:00'):
            for root in (event_book, author_book):
                self.serve(self.w.editor_server(root))
        for _ in range(2):
            self.assertEqual(self.activity(self.post(shelf)), expected)
            self.assertEqual(registry.read_bytes(), registry_bytes)
        self.assertEqual({root: (root / '.story/state.sqlite3').read_bytes()
                          for root in states}, states)
        port = shelf.server_port
        self.stop(shelf)
        restarted = self.start(port=port)
        self.assertEqual(self.activity(self.post(restarted)), expected)
        self.assertEqual(registry.read_bytes(), registry_bytes)

    def test_unknown_time_stays_last_in_both_orders_without_using_database_mtime(self):
        unknown, earlier, later = self.book('空作品'), self.book('较早作品'), self.book('较晚作品')
        self.event(earlier, '2024-01-01 01:00:00')
        self.author_file(later, '2024-06-01T09:00:00+00:00')
        shelf = self.start([unknown, earlier, later])
        rows = self.post(shelf)
        by_root = {row['root']: row for row in rows}
        self.assertTrue(by_root[str(unknown)]['available'])
        self.assert_unknown(by_root[str(unknown)])
        self.assert_utc(by_root[str(earlier)], '2024-01-01T01:00:00+00:00')
        self.assert_utc(by_root[str(later)], '2024-06-01T09:00:00+00:00')
        page = self.page(shelf)
        self.assertEqual(self.sorted_keys(page, rows, 'newest'),
                         [by_root[str(root)]['key'] for root in (later, earlier, unknown)])
        self.assertEqual(self.sorted_keys(page, rows, 'oldest'),
                         [by_root[str(root)]['key'] for root in (earlier, later, unknown)])

    def test_replaced_or_corrupt_book_cannot_reuse_or_borrow_a_verified_time(self):
        healthy, replaced, corrupt = [self.book(name) for name in ('正常作品', '替换目录', '损坏目录')]
        for root in (healthy, replaced, corrupt):
            self.event(root, '2024-01-01 01:00:00')
        shelf = self.start([replaced, corrupt, healthy])
        original = {row['root']: row for row in self.post(shelf)}
        registry = self.state / 'library.json'
        before = registry.read_bytes()
        replaced.rename(self.area / '原作品已移动')
        new_book = self.book('替换目录', 'short')
        self.event(new_book, '2099-12-31 23:59:59')
        (corrupt / '.story/state.sqlite3').write_bytes(b'not a SQLite database')
        rows = self.post(shelf)
        by_root = {row['root']: row for row in rows}
        for root in (replaced, corrupt):
            row = by_root[str(root)]
            self.assertFalse(row['available'])
            self.assertEqual(row['book_id'], original[str(root)]['book_id'])
            self.assert_unknown(row)
        self.assertNotEqual(by_root[str(replaced)]['book_id'], self.w._library_entry(new_book)['book_id'])
        self.assert_utc(by_root[str(healthy)], '2024-01-01T01:00:00+00:00')
        self.assertEqual(registry.read_bytes(), before)
        page = self.page(shelf)
        for order in ('newest', 'oldest'):
            self.assertEqual(self.sorted_keys(page, rows, order)[0], by_root[str(healthy)]['key'])

    def test_legacy_registry_time_is_rechecked_without_rewriting_or_inventing_time(self):
        empty, active = self.book('旧登记空作品'), self.book('旧登记活动作品')
        self.event(active, '2024-05-01 10:00:00')
        self.state.mkdir()
        legacy = []
        for root in (empty, active):
            entry = self.w._library_entry(root)
            row = {field: entry[field] for field in ('key', 'root', 'book_id', 'title')}
            row.update(updated_at='2099-01-01T00:00:00+00:00', updated_at_verified=True,
                       updated_at_source='obsolete registry cache')
            legacy.append(row)
        registry = self.state / 'library.json'
        raw = json.dumps({'version': 1, 'books': legacy}, ensure_ascii=False, indent=4).encode()
        registry.write_bytes(raw)
        shelf = self.start()
        rows = {row['root']: row for row in self.post(shelf)}
        self.assert_unknown(rows[str(empty)])
        self.assert_utc(rows[str(active)], '2024-05-01T10:00:00+00:00')
        self.assertEqual(registry.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
