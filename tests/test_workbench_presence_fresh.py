"""Cross-process stop fencing, bounded recovery and author-data preservation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import test_workbench as base


class FreshStopLeaseTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='story-fresh-stop-')
        self.addCleanup(self.area.cleanup)
        self.parent = Path(self.area.name).resolve()
        self.root = self.parent / '隔离的作品'
        base.story.Book.create(self.root, '停机验证作品', 'long')
        book = base.story.Book(self.root)
        try:
            self.book_id = book.meta('id')
        finally:
            book.close()
        self.w, self.tasks = base.story.workbench, base.story.tasks
        self.registry = self.w._EditorPresence('a' * 32, self.book_id, root=self.root)
        self.addCleanup(self.release)
        self.lease = self.root / '.story/workbench-stop-lease.json'

    def release(self):
        # Tests which deliberately corrupt a temporary lease retain it until
        # their isolated fixture is removed; production never deletes a lock.
        try:
            self.registry.release_stop()
        except (base.story.StoryError, OSError):
            pass

    def error(self, code, action):
        with self.assertRaises(base.story.StoryError) as raised:
            action()
        self.assertEqual(raised.exception.code, code, raised.exception.details)

    def dispatch_probe(self):
        program = r'''
import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('fresh_stop_probe',sys.argv[1])
story=importlib.util.module_from_spec(spec);spec.loader.exec_module(story)
try:
    with story.tasks._book(sys.argv[2],write=True) as book:
        story.workbench._require_dispatch_not_stopping(book)
except story.StoryError as error:
    print(json.dumps({'blocked':error.code}));raise SystemExit(0)
print(json.dumps({'blocked':None}))
'''
        result = subprocess.run([sys.executable, '-B', '-c', program, str(base.TOOL), str(self.root)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_stop_prepare_fences_a_separate_process_and_cancel_releases_it(self):
        before = (self.root / '.story/state.sqlite3').read_bytes()
        token = self.registry.prepare_stop(True)['stop_challenge']
        self.assertEqual(self.dispatch_probe()['blocked'], 'workbench_presence_stopping')
        self.registry.cancel_stop(token)
        self.assertIsNone(self.dispatch_probe()['blocked'])
        self.assertEqual((self.root / '.story/state.sqlite3').read_bytes(), before)

    def test_another_instance_cannot_clear_the_persisted_stop_lease(self):
        self.registry.prepare_stop(True)
        original = self.lease.read_bytes()
        with self.tasks._book(self.root, self.book_id, write=True) as book:
            lease, _ = self.w._stop_lease_read(book)
            self.assertFalse(self.w._stop_lease_clear(book, 'b' * 32, lease['challenge_id']))
            self.assertFalse(self.w._stop_lease_clear(book, lease['instance'], 'b' * 32))
        self.assertEqual(self.lease.read_bytes(), original)

    def test_lease_cas_preserves_a_competing_file_instead_of_overwriting_it(self):
        self.registry.prepare_stop(True)
        with self.tasks._book(self.root, self.book_id, write=True) as book:
            lease, expected = self.w._stop_lease_read(book)
            competing = b'{"external-change":"must survive"}\n'
            self.lease.write_bytes(competing)
            self.error('workbench_presence_lease_changed',
                       lambda: self.w._stop_lease_publish(book, lease, expected))
        self.assertEqual(self.lease.read_bytes(), competing)
        self.assertFalse(list(self.lease.parent.glob('.stop-lease-stage-*')))

    def test_foreign_monotonic_origin_is_unknown_instead_of_a_multi_day_lease(self):
        self.registry.prepare_stop(True)
        with self.tasks._book(self.root, self.book_id, write=True) as book:
            lease, expected = self.w._stop_lease_read(book)
            lease['created_monotonic'] += 10**9
            lease['expires_monotonic'] += 10**9
            self.w._stop_lease_publish(book, lease, expected)
            self.error('workbench_presence_lease_unknown',
                       lambda: self.w._require_dispatch_not_stopping(book))

    def test_backup_restore_omits_runtime_lease_but_preserves_same_named_author_material(self):
        self.registry.prepare_stop(True)
        author = self.root / '参考资料/workbench-stop-lease.json'
        author.parent.mkdir()
        author.write_text('这是作者资料，不是运行锁。', encoding='utf-8')
        archive, restored = self.parent / '备份.zip', self.parent / '恢复的作品'
        packet = base.story.workbench_archives.create_archive(self.root, archive, self.book_id)
        self.assertTrue(packet['ok'])
        with zipfile.ZipFile(archive) as saved:
            self.assertNotIn('book/.story/workbench-stop-lease.json', saved.namelist())
            self.assertEqual(saved.read('book/参考资料/workbench-stop-lease.json'), author.read_bytes())
        result = base.story.workbench_archives.restore_archive(archive, restored)
        self.assertEqual(result['book_id'], self.book_id)
        self.assertFalse((restored / '.story/workbench-stop-lease.json').exists())
        self.assertEqual((restored / '参考资料/workbench-stop-lease.json').read_bytes(), author.read_bytes())

    def test_new_singleton_service_archives_an_old_stop_lease_before_dispatch(self):
        self.registry.prepare_stop(True)
        previous = self.lease.read_bytes()
        server = self.w.editor_server(self.root)
        self.addCleanup(server.server_close)
        self.assertFalse(self.lease.exists())
        copies = list((self.root / '.story/assistant-tasks/retired-stop-leases').glob('*.json'))
        self.assertEqual(len(copies), 1)
        receipt = json.loads(copies[0].read_text())
        self.assertEqual(receipt['previous_stop_lease'], json.loads(previous))
        self.assertEqual(receipt['reason'], 'exclusive_editor_service_lock_acquired')
        self.assertNotEqual(receipt['new_instance'], self.registry.instance)
        self.assertIsNone(self.dispatch_probe()['blocked'])


if __name__ == '__main__':
    unittest.main()
