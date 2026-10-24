"""Existing-draft synchronization: private backups, consent, readback and recovery."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from tests import test_platform_drafts as base

story, drafts = base.story, base.drafts

class UpdateAdapter(base.FakeAdapter):
    def __init__(self, book):
        super().__init__(book)
        self.updated = 0
        self.after_write_error = False
        self.dirty_after_write = False
        self.before_update = None

    def update_draft(self, remote_id, chapter, title, body, *, expected_title, expected_body):
        self.call('update', remote_id, chapter)
        backups = list((self.book.root / '.story/platform-draft-update-backups').rglob('*.json'))
        assert len(backups) >= 1
        before = json.loads(max(backups,key=lambda path:path.stat().st_mtime_ns).read_text())['before']
        assert (before['title'], before['body']) == (expected_title, expected_body)
        if os.name != 'nt':
            assert max(backups,key=lambda path:path.stat().st_mtime_ns).stat().st_mode & 0o077 == 0
        if self.before_update:
            self.before_update()
        current = self.remote[remote_id]
        if (current['title'], current['body']) != (expected_title, expected_body):
            story.fail('platform_browser_remote_changed', 'Changed remotely')
        self.updated += 1
        current.update(title=title, body=body, formatting_clean=not self.dirty_after_write)
        if self.after_write_error:
            raise RuntimeError('response lost')
        return remote_id

class ExistingDraftSyncTests(unittest.TestCase):
    setUp = base.PlatformDraftTests.setUp
    execute = base.PlatformDraftTests.execute
    assert_code = base.PlatformDraftTests.assert_code

    def prepare_adapter(self, **changes):
        self.adapter = UpdateAdapter(self.book)
        self.adapter.seed(self.frozen, **changes)

    def test_title_body_and_both_changes_update_same_id_and_leave_creative_data_unchanged(self):
        self.prepare_adapter(title='旧标题', body='旧正文')
        snapshot = self.fixture.writing_snapshot()
        for changes in ({'title':'旧标题'}, {'body':'旧正文'}, {'title':'旧标题','body':'旧正文'}):
            self.adapter.seed(self.frozen, **changes)
            before = self.adapter.updated
            result = self.execute(update_existing=True)
            self.assertTrue(result['ok'], result)
            self.assertEqual(self.adapter.updated, before+1)
            self.assertEqual(result['results'][0]['remote_id'], 'existing')
            self.assertEqual(result['results'][0]['reason'], 'existing_draft_updated')
        self.assertEqual(self.adapter.created, 0)
        self.assertEqual(self.fixture.writing_snapshot(), snapshot)
        self.assertTrue(all(self.frozen['body'] not in p.read_text() for p in (self.fixture.root/drafts.DIRECTORY).rglob('*.json')))

    def test_default_mode_never_overwrites_and_update_requires_exclusive_mode(self):
        self.prepare_adapter(title='不同标题')
        self.assertEqual(self.execute()['results'][0]['state'], 'conflict')
        self.assertEqual(self.adapter.updated, 0)
        self.assert_code('invalid_input', self.execute, update_existing=True, reconcile_only=True)
        self.assert_code('invalid_input', self.execute, update_existing='true')

    def test_unchanged_chapter_is_read_only_and_annotated_chapter_is_cleaned(self):
        self.prepare_adapter()
        self.assertTrue(self.execute(update_existing=True)['ok'])
        self.assertEqual(self.adapter.updated, 0)
        self.adapter.remote['existing']['formatting_clean'] = False
        self.assertEqual(self.execute(reconcile_only=True)['results'][0]['reason'], 'remote_formatting_metadata')
        self.assertTrue(self.execute(update_existing=True)['ok'])
        self.assertEqual(self.adapter.updated, 1)

    def test_readback_with_remaining_annotation_cannot_be_success_or_resolve_intent(self):
        self.prepare_adapter(title='不同标题')
        self.adapter.dirty_after_write = True
        result = self.execute(update_existing=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['results'][0]['reason'], 'saved_formatting_metadata')
        self.assertEqual(result['accepted_chapters'], 0)
        self.adapter.dirty_after_write = False
        self.assertFalse(self.execute(update_existing=True)['ok'])
        self.assertEqual(self.adapter.updated, 1, 'Unresolved write must never be repeated')

    def test_lost_update_receipt_resolves_by_readback_without_resending(self):
        self.prepare_adapter(body='不同正文')
        self.adapter.after_write_error = True
        self.assertEqual(self.execute(update_existing=True)['results'][0]['state'], 'unknown')
        result = self.execute(update_existing=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['results'][0]['state'], 'reconciled')
        self.assertEqual(self.adapter.updated, 1)

    def test_remote_change_after_backup_is_not_overwritten(self):
        self.prepare_adapter(body='旧正文')
        self.adapter.before_update = lambda: self.adapter.remote['existing'].update(body='他处刚刚修改的正文')
        result = self.execute(update_existing=True)
        self.assertFalse(result['ok'])
        self.assertEqual(self.adapter.updated, 0)
        self.assertEqual(self.adapter.remote['existing']['body'], '他处刚刚修改的正文')

    def test_missing_chapters_are_skipped_without_creation_and_later_existing_is_updated(self):
        self.fixture.commit(2)
        plan = self.fixture.prepare((1,2))
        self.adapter = UpdateAdapter(self.book)
        self.adapter.seed(plan['manifest']['chapters'][1], title='第二章旧标题')
        result = self.execute(plan, update_existing=True)
        self.assertEqual([row['state'] for row in result['results']], ['not_found','saved'])
        self.assertEqual(self.adapter.created, 0)
        self.assertEqual(self.adapter.updated, 1)

    def test_published_or_duplicate_chapters_are_not_updated(self):
        self.prepare_adapter(title='旧标题',state='published')
        self.assertFalse(self.execute(update_existing=True)['ok'])
        self.assertEqual(self.adapter.updated,0)
        self.adapter.seed(self.frozen,title='旧标题')
        self.adapter.seed(self.frozen,remote_id='duplicate')
        self.assertEqual(self.execute(update_existing=True)['results'][0]['reason'],'multiple_remote_drafts')
        self.assertEqual(self.adapter.updated,0)

    def test_stale_source_and_backup_failure_stop_before_update(self):
        self.prepare_adapter(title='旧标题')
        original = drafts._write_record
        def fail_backup(directory,name,record,**kwargs):
            if 'platform-draft-update-backups' in str(directory.path):
                raise OSError('backup disk unavailable')
            return original(directory,name,record,**kwargs)
        with patch.object(drafts,'_write_record',side_effect=fail_backup):
            self.assertFalse(self.execute(update_existing=True)['ok'])
        self.assertEqual(self.adapter.updated,0)
        story.publish.cancel(self.book,self.plan['id'])
        self.assert_code('platform_draft_plan_unusable',self.execute,update_existing=True)

    def test_latest_history_reads_new_receipt_and_is_never_current_remote_confirmation(self):
        self.prepare_adapter(title='旧标题')
        self.execute()
        previous=drafts.workbench_history(self.book.root,self.plan['id'],expected_book_id=self.book.meta('id'))
        self.execute(update_existing=True)
        latest=drafts.workbench_history(self.book.root,self.plan['id'],expected_book_id=self.book.meta('id'))
        self.assertGreater(latest['history_events_total'],previous['history_events_total'])
        self.assertEqual(latest['results'][0]['state'],'saved')
        self.assertFalse(latest['remote_accessed'])
        self.assertFalse(latest['results'][0]['observed_this_run'])
        self.assertTrue(latest['source_matches_current'])

if __name__=='__main__': unittest.main()
