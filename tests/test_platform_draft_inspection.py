"""Full-batch inspection, stale-observation protection and cooperative stops."""
import copy
import threading
import unittest

from tests import test_platform_draft_sync as sync
from tests import test_platform_draft_auto_connect as auto

drafts = sync.drafts


class DraftInspectionTests(unittest.TestCase):
    setUp = sync.ExistingDraftSyncTests.setUp
    execute = sync.ExistingDraftSyncTests.execute
    assert_code = sync.ExistingDraftSyncTests.assert_code

    def inspect(self):
        return drafts.inspect_plan(self.book, self.plan['id'], self.adapter)

    def test_full_batch_reads_past_changes_and_missing_without_writes(self):
        self.fixture.commit(2)
        self.fixture.commit(3)
        self.plan = self.fixture.prepare((1, 2, 3))
        self.adapter = sync.UpdateAdapter(self.book)
        a, b, c = self.plan['manifest']['chapters']
        self.adapter.seed(a, title='旧章名', remote_id='one')
        encoded = drafts._encoded_content(c, 'plain_paragraphs_v1', 'fanqie')
        self.adapter.seed(c, title=encoded['title'], body=encoded['body'], remote_id='three')
        before = self.fixture.writing_snapshot()
        result = self.inspect()
        self.assertEqual([r['action'] for r in result['changes']], ['update', 'create', 'unchanged'])
        self.assertTrue(result['can_execute'])
        self.assertEqual(result['checked_chapters'], 3)
        self.assertEqual((self.adapter.created, self.adapter.updated), (0, 0))
        self.assertEqual(self.fixture.writing_snapshot(), before)
        self.assertFalse((self.book.root / drafts.DIRECTORY).exists())

    def test_remote_change_after_inspection_blocks_before_update(self):
        self.adapter = sync.UpdateAdapter(self.book)
        self.adapter.seed(self.frozen, body='第一次检查的正文')
        inspected = self.inspect()
        self.adapter.remote['existing']['body'] = '作者在后台刚刚改动'
        result = self.execute(sync_drafts=True, body_encoding='plain_paragraphs_v1',
                              expected_remote=inspected['_expected_remote'])
        self.assertEqual(result['results'][0]['reason'], 'platform_draft_inspection_changed')
        self.assertEqual(self.adapter.updated, 0)

    def test_remote_blank_lines_are_a_real_difference(self):
        self.adapter = sync.UpdateAdapter(self.book)
        encoded = drafts._encoded_content(self.frozen, 'plain_paragraphs_v1', 'fanqie')
        self.adapter.seed(self.frozen, body=encoded['body'] + '\n\n')
        row = self.inspect()['changes'][0]
        self.assertEqual(row['action'], 'update')
        self.assertTrue(row['difference']['body_changed'])
        self.assertFalse(row['difference']['title_changed'])

    def test_previous_unknown_missing_draft_is_not_classified_create(self):
        self.adapter = sync.UpdateAdapter(self.book)
        self.adapter.fail_create = 'after'
        self.execute(sync_drafts=True)
        self.adapter.hide_all = True
        result = self.inspect()
        self.assertFalse(result['can_execute'])
        self.assertEqual(result['changes'][0]['reason'], 'previous_attempt_not_found')

    def test_stop_during_write_finishes_readback_and_skips_next_chapter(self):
        self.fixture.commit(2)
        self.plan = self.fixture.prepare((1, 2))
        self.adapter = sync.UpdateAdapter(self.book)
        stop = threading.Event()
        self.adapter.on_create = lambda: stop.set()
        result = self.execute(sync_drafts=True, stop_requested=stop.is_set)
        self.assertTrue(result['stop_requested'])
        self.assertEqual(self.adapter.created, 1)
        self.assertEqual(result['accepted_chapters'], 1)
        self.assertEqual(result['results'][1]['state'], 'pending')


class InspectionJobTests(unittest.TestCase):
    def setUp(self):
        self.f = auto.PlatformDraftAutoConnectTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_inspection_required_consumed_and_stop_is_identity_scoped(self):
        f = self.f
        encoding = 'plain_paragraphs_v1'
        sync_preview = f.preview(sync_drafts=True, body_encoding=encoding)
        f.assert_code('platform_draft_inspection_required', f.start, sync_preview,
                      sync_drafts=True, body_encoding=encoding)
        preview = f.preview(inspect_only=True, body_encoding=encoding)
        first = f.start(preview, inspect_only=True, body_encoding=encoding)
        completed = f.wait_job(first['id'])
        self.assertEqual(completed['state'], 'inspection_ready', completed)
        self.assertEqual(completed['result']['change_counts']['create'], 1)
        self.assertFalse(any(op[0] in ('create', 'update') for op in f.operations))
        preview = f.preview(sync_drafts=True, body_encoding=encoding)
        job = f.start(preview, sync_drafts=True, body_encoding=encoding, inspection_id=first['id'])
        self.assertIn(f.wait_job(job['id'])['state'], ('succeeded', 'succeeded_equivalent'))
        f.assert_code('platform_draft_inspection_required', f.start, preview,
                      sync_drafts=True, body_encoding=encoding, inspection_id=first['id'])
        with self.assertRaises(auto.story.StoryError):
            auto.drafts.workbench_stop(f.root, first['id'], expected_book_id='other')


if __name__ == '__main__':
    unittest.main()
