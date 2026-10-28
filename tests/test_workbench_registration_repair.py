"""An explicit shelf repair never rewrites a manuscript or weakens open guards."""
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

import test_workbench as base


class RegistrationRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-registration-repair-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.state = self.area / '书架'
        self.state.mkdir()
        self.root = self.area / '原作品目录'
        base.story.Book.create(self.root, '旧登记书名', 'long')
        self.w = base.story.workbench
        self.manager = base.story.workbench_books.BookManager(self.w)
        self.old = self.w._library_entry(self.root)
        self.old['shelf_status'] = 'completed'
        self.entries = {self.old['key']: self.old}
        self.digest = self.w._library_store(self.state, self.entries, None)
        self.root.rename(self.area / '旧书库保留')
        base.story.Book.create(self.root, '已确认的新书名', 'long')
        self.current = self.w._library_entry(self.root)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file() and not p.name.startswith('.story-pin-')}

    def preview(self):
        return self.manager.registration_preview(self.state, self.entries, self.digest, self.old['key'])

    def repair(self, preview=None, **kwargs):
        preview = preview or self.preview()
        values = {'confirmation': preview['confirmation'], 'title': self.current['title'], 'confirmed': True, **kwargs}
        return self.manager.repair_registration(self.state, self.entries, self.digest, self.old['key'], **values)

    def error(self, code, action):
        with self.assertRaises(base.story.StoryError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_mismatch_has_its_own_reason_and_preview_is_read_only(self):
        before, registry = self.snapshot(), (self.state / 'library.json').read_bytes()
        row = self.w._library_catalog(self.entries)['books'][0]
        self.assertFalse(row['available'])
        self.assertEqual(row['identity_status'], 'changed')
        self.assertEqual(row['classification']['reason'], 'identity_changed')
        preview = self.preview()
        self.assertEqual(preview['registered']['book_id'], self.old['book_id'])
        self.assertEqual(preview['current']['book_id'], self.current['book_id'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.state / 'library.json').read_bytes(), registry)
        self.error('workbench_book_changed', lambda: self.w._library_entry(self.root, self.old))

    def test_repair_changes_only_registry_and_drops_old_identity_status(self):
        before = self.snapshot()
        entries, digest, result = self.repair()
        new = entries[self.old['key']]
        self.assertEqual((new['book_id'], new['title']), (self.current['book_id'], self.current['title']))
        self.assertNotIn('shelf_status', new)
        self.assertTrue(result['registered'])
        self.assertFalse(result['book_files_changed'])
        self.assertEqual(self.w._library_load(self.state)[1], digest)
        self.assertTrue(self.w._library_catalog(entries)['books'][0]['available'])
        self.assertEqual(self.snapshot(), before)
        receipt = json.loads((self.state / 'operations' / (result['receipt_id'] + '.json')).read_text())
        self.assertEqual(receipt['previous_entry']['book_id'], self.old['book_id'])
        self.assertTrue(list((self.state / '.backups').rglob('library.json')))

    def test_confirmation_and_exact_current_title_are_required(self):
        registry = (self.state / 'library.json').read_bytes()
        for values in ({'confirmed': False}, {'confirmed': 1}, {'title': self.old['title']}, {'confirmation': {}}):
            self.error('workbench_registration_confirmation', lambda: self.repair(**values))
        self.assertEqual((self.state / 'library.json').read_bytes(), registry)

    def test_changed_title_or_replaced_directory_invalidates_preview(self):
        preview = self.preview()
        replacement = self.area / '副本'
        shutil.copytree(self.root, replacement)
        self.root.rename(self.area / '保留当前目录')
        replacement.rename(self.root)
        self.error('workbench_registration_confirmation', lambda: self.repair(preview))
        preview = self.preview()
        book = base.story.Book(self.root)
        try:
            book.set_meta('title', '再一次改名')
            book.db.commit()
        finally:
            book.close()
        self.error('workbench_registration_confirmation', lambda: self.repair(preview))

    def test_stale_registry_and_duplicate_identity_are_rejected(self):
        preview = self.preview()
        copy = self.area / '已登记的副本'
        shutil.copytree(self.root, copy)
        other = self.w._library_entry(copy)
        updated = {**self.entries, other['key']: other}
        digest = self.w._library_store(self.state, updated, self.digest)
        self.error('workbench_library_changed', lambda: self.repair(preview))
        self.entries, self.digest = updated, digest
        self.error('workbench_registration_duplicate', self.preview)

    def test_same_identity_does_not_offer_a_rebind(self):
        self.entries[self.old['key']] = self.current
        self.digest = self.w._library_store(self.state, self.entries, self.digest)
        self.error('workbench_registration_unchanged', self.preview)

    def test_authenticated_http_repair_requires_old_page_identity(self):
        server = self.w.library_server(self.state, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        self.addCleanup(lambda: server.server_close())
        self.addCleanup(lambda: thread.join(5))
        self.addCleanup(server.shutdown)
        token = server.library_token
        def post(action, values):
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                c.request('POST', '/api/' + action, json.dumps(values), {
                    'Origin': 'http://127.0.0.1:' + str(server.server_port),
                    'X-Story-Token': token, 'Content-Type': 'application/json'})
                response = c.getresponse()
                return response.status, json.loads(response.read())
            finally:
                c.close()
        target = {'key': self.old['key'], 'expected_book_id': self.old['book_id']}
        status, preview = post('registration-preview', target)
        self.assertEqual(status, 200, preview)
        payload = {**target, 'confirmation': preview['confirmation'], 'title': self.current['title'], 'confirmed': True}
        code, result = post('repair-registration', {**payload, 'expected_book_id': self.current['book_id']})
        self.assertEqual((code, result['error']), (409, 'workbench_book_changed'))
        before = self.snapshot()
        code, result = post('repair-registration', payload)
        self.assertEqual(code, 200, result)
        self.assertTrue(result['books'][0]['available'])
        self.assertEqual(result['books'][0]['book_id'], self.current['book_id'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(post('repair-registration', payload)[0], 409)


if __name__ == '__main__':
    unittest.main()
