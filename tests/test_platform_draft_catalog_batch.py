"""Same-round complete catalogs drive only missing chapters to creation."""
import copy
import unittest
from unittest.mock import patch
from tests import test_platform_draft_sync as sync

story, drafts = sync.story, sync.drafts


class CatalogAdapter(sync.UpdateAdapter):
    def verify_target(self, **target):
        self.target = dict(target)
        super().verify_target(**target)

    def inspect_catalog(self):
        self.call('catalog')
        entries = [{"chapter": row['chapter'], "remote_id": remote if row['state'] == 'draft' else None,
                    "state": 'draft' if row['state'] == 'draft' else 'non_draft'}
                   for remote, row in self.remote.items()] if not self.hide_all else []
        return {"target": dict(self.target), "complete": True, "entries": entries, "proof_id": 'local-test-proof'}


class CatalogBatchTests(unittest.TestCase):
    setUp = sync.ExistingDraftSyncTests.setUp
    execute = sync.ExistingDraftSyncTests.execute

    def inspect(self):
        return drafts.inspect_plan(self.book, self.plan['id'], self.adapter)

    def expand(self, last):
        for chapter in range(2, last + 1):
            self.fixture.commit(chapter)
        self.plan = self.fixture.prepare(tuple(range(1, last + 1)))

    def test_empty_thirty_chapters_have_one_inspection_and_one_sync_entry_scan(self):
        self.expand(30)
        self.adapter = CatalogAdapter(self.book)
        before = self.fixture.writing_snapshot()
        checked = self.inspect()
        self.assertEqual(checked['change_counts'], {'create': 30, 'update': 0, 'unchanged': 0, 'blocked': 0})
        self.assertEqual([call[0] for call in self.adapter.calls], ['verify', 'catalog'])
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertTrue(result['all_chapters_accepted_this_run'], result)
        self.assertEqual(self.adapter.created, 30)
        self.assertEqual(sum(c[0] == 'catalog' for c in self.adapter.calls), 2)
        self.assertFalse(any(c[0] == 'find' for c in self.adapter.calls))
        self.assertEqual(sum(c[0] == 'read' for c in self.adapter.calls), 30)
        self.assertEqual(before, self.fixture.writing_snapshot())

    def test_middle_hole_does_not_hide_later_existing_and_every_existing_body_is_read(self):
        self.expand(3)
        self.adapter = CatalogAdapter(self.book)
        first, _, last = self.plan['manifest']['chapters']
        self.adapter.seed(first, remote_id='first', title='旧章名', body='旧正文')
        self.adapter.seed(last, remote_id='last', **drafts._encoded_content(last, drafts.PLATFORM_BODY_ENCODING, 'fanqie'))
        checked = self.inspect()
        self.assertEqual([r['action'] for r in checked['changes']], ['update', 'create', 'unchanged'])
        self.assertEqual([c for c in self.adapter.calls if c[0] == 'read'], [('read', 'first'), ('read', 'last')])
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertTrue(result['all_chapters_accepted_this_run'], result)
        self.assertEqual((self.adapter.created, self.adapter.updated), (1, 1))
        self.assertFalse(any(c[0] == 'find' for c in self.adapter.calls))

    def test_each_inspection_reads_fresh_directory_and_sync_refuses_new_collision(self):
        self.adapter = CatalogAdapter(self.book)
        checked = self.inspect()
        self.adapter.seed(self.frozen, remote_id='concurrent')
        result = self.execute(sync_drafts=True, expected_remote=checked['_expected_remote'])
        self.assertEqual(result['results'][0]['reason'], 'platform_draft_inspection_changed')
        self.assertEqual(self.adapter.created, 0)
        checked = self.inspect()
        self.assertEqual(checked['changes'][0]['remote_id'], 'concurrent')
        self.assertEqual(sum(c[0] == 'catalog' for c in self.adapter.calls), 3)

    def test_existing_changed_body_rejected_by_direct_read_before_any_update(self):
        self.adapter = CatalogAdapter(self.book)
        self.adapter.seed(self.frozen, remote_id='existing')
        checked = self.inspect()
        self.adapter.remote['existing']['body'] = '后台新改动'
        result = self.execute(sync_drafts=True, expected_remote=checked['_expected_remote'])
        self.assertEqual(result['results'][0]['reason'], 'platform_draft_inspection_changed')
        self.assertEqual((self.adapter.created, self.adapter.updated), (0, 0))

    def test_non_draft_duplicate_incomplete_and_wrong_target_are_not_missing(self):
        for case in ('published', 'duplicate', 'incomplete', 'target'):
            with self.subTest(case=case):
                self.adapter = CatalogAdapter(self.book)
                if case == 'published': self.adapter.seed(self.frozen, state='published')
                if case == 'duplicate':
                    self.adapter.seed(self.frozen, remote_id='a'); self.adapter.seed(self.frozen, remote_id='b')
                original = self.adapter.inspect_catalog
                def observe():
                    value = original()
                    if case == 'incomplete': value['complete'] = False
                    if case == 'target': value['target']['remote_book_id'] = 'different'
                    return value
                with patch.object(self.adapter, 'inspect_catalog', side_effect=observe): checked = self.inspect()
                self.assertFalse(checked['can_execute'])
                self.assertEqual(checked['changes'][0]['action'], 'blocked')
                self.assertEqual(self.adapter.created, 0)

    def test_unknown_missing_is_still_blocked_without_creation(self):
        self.adapter = CatalogAdapter(self.book)
        self.adapter.fail_create = 'after'
        failed = self.execute(sync_drafts=True)
        self.assertEqual(failed['results'][0]['state'], 'unknown')
        self.adapter.fail_create = None
        self.adapter.hide_all = True
        checked = self.inspect()
        self.assertEqual(checked['changes'][0]['reason'], 'previous_attempt_not_found')
        self.assertFalse(checked['can_execute'])
        self.assertEqual(self.adapter.created, 1)


class AllocatedCatalogAdapter(CatalogAdapter):
    def __init__(self, book):
        super().__init__(book)
        self.lose_allocation = True
        self.resumed = 0
        self.fail_resume = False
        self.change_before_empty_check = False

    def create_draft(self, chapter, title, body):
        if not self.lose_allocation:
            return super().create_draft(chapter, title, body)
        self.call('create', chapter)
        self.created += 1
        self.remote['allocated'] = {'chapter': None, 'remote_id': 'allocated', 'state': 'draft', 'title': '', 'body': ''}
        story.fail('platform_browser_unsupported_editor', 'Isolated allocation interrupted', allocated_remote_id='allocated')

    def inspect_empty_draft(self, remote_id, chapter):
        self.call('inspect_empty', remote_id, chapter)
        row = self.remote[remote_id]
        if self.change_before_empty_check:
            row['body'] = '其他窗口输入'
        if row['chapter'] is not None or row['title'] != '' or row['body'] != '':
            story.fail('platform_browser_editor_changed', 'No longer empty')
        return {**copy.deepcopy(row), 'empty': True, 'url': 'https://example.invalid/isolated-empty'}

    def resume_empty_draft(self, remote_id, chapter, title, body, *, recovery):
        self.call('resume', remote_id, chapter)
        self.resumed += 1
        if self.fail_resume:
            raise RuntimeError('uncertain recovery outcome')
        assert recovery['remote_id'] == remote_id and recovery['chapter'] == chapter
        assert recovery['title_sha256'] == drafts._hash_bytes(title.encode())
        assert recovery['body_sha256'] == drafts._hash_bytes(body.encode())
        self.remote[remote_id].update(chapter=chapter, title=title, body=body)
        return remote_id


class EmptyRecoveryBatchTests(unittest.TestCase):
    setUp = CatalogBatchTests.setUp
    execute = CatalogBatchTests.execute
    inspect = CatalogBatchTests.inspect
    expand = CatalogBatchTests.expand

    def failed_allocation(self, chapters=1):
        if chapters > 1: self.expand(chapters)
        self.adapter = AllocatedCatalogAdapter(self.book)
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING)
        self.assertEqual(result['results'][0]['state'], 'unknown')
        self.assertEqual(result['results'][0]['remote_id'], 'allocated')
        self.adapter.lose_allocation = False
        return result

    def test_owned_empty_id_is_recovered_then_remaining_missing_chapter_is_created(self):
        self.failed_allocation(2)
        checked = self.inspect()
        self.assertTrue(checked['can_execute'], checked)
        self.assertEqual([r['action'] for r in checked['changes']], ['update', 'create'])
        self.assertEqual(checked['changes'][0]['reason'], 'resume_empty_draft')
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertTrue(result['all_chapters_accepted_this_run'], result)
        self.assertEqual((self.adapter.resumed, self.adapter.created), (1, 2))
        self.assertEqual(result['results'][0]['remote_id'], 'allocated')
        self.assertEqual(result['results'][0]['reason'], 'empty_draft_recovered')
        self.assertEqual((result['created_chapters'], result['updated_chapters']), (1, 1))
        self.assertEqual([c for c in self.adapter.calls if c[0] == 'read'], [('read', 'allocated'), ('read', '2')])
        again = self.inspect()
        self.assertEqual(again['change_counts']['unchanged'], 2)

    def test_recovery_precheck_precedes_updates_of_earlier_chapters(self):
        self.expand(2)
        self.adapter = AllocatedCatalogAdapter(self.book)
        first = self.plan['manifest']['chapters'][0]
        self.adapter.seed(first, remote_id='one', title='旧标题', body='旧正文')
        interrupted = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING)
        self.assertEqual(interrupted['results'][1]['state'], 'unknown')
        self.adapter.lose_allocation = False
        self.adapter.remote['one'].update(title='旧标题', body='旧正文')
        checked = self.inspect()
        self.assertTrue(checked['can_execute'], checked)
        self.assertEqual([row['action'] for row in checked['changes']], ['update', 'update'])
        self.adapter.calls.clear()
        self.adapter.updated = self.adapter.resumed = 0
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertTrue(result['all_chapters_accepted_this_run'], result)
        names = [call[:3] for call in self.adapter.calls]
        self.assertLess(names.index(('inspect_empty', 'allocated', 2)), names.index(('update', 'one', 1)))
        self.assertEqual((self.adapter.updated, self.adapter.resumed), (1, 1))

    def test_unowned_empty_id_remains_blocked(self):
        self.adapter = AllocatedCatalogAdapter(self.book)
        self.adapter.remote['unknown'] = {'chapter': None, 'remote_id': 'unknown', 'state': 'draft', 'title': '', 'body': ''}
        checked = self.inspect()
        self.assertFalse(checked['can_execute'])
        self.assertEqual(checked['changes'][0]['reason'], 'platform_browser_unnumbered_draft')
        self.assertFalse(any(c[0] == 'inspect_empty' for c in self.adapter.calls))
        self.assertEqual((self.adapter.resumed, self.adapter.created), (0, 0))

    def test_changed_empty_editor_blocks_before_claim_or_recovery_write(self):
        self.failed_allocation()
        checked = self.inspect()
        self.adapter.change_before_empty_check = True
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertEqual(result['results'][0]['reason'], 'platform_browser_editor_changed')
        self.assertEqual(self.adapter.resumed, 0)
        self.assertEqual(list(self.global_root.glob('empty-recovery-*.claim.json')), [])

    def test_failed_recovery_is_unknown_and_never_automatically_repeated(self):
        self.failed_allocation()
        checked = self.inspect()
        self.adapter.fail_resume = True
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertEqual(result['results'][0]['state'], 'unknown')
        self.assertEqual(result['results'][0]['remote_id'], 'allocated')
        self.assertEqual(len(list(self.global_root.glob('empty-recovery-*.claim.json'))), 1)
        again = self.inspect()
        self.assertFalse(again['can_execute'])
        self.assertEqual(self.adapter.resumed, 1)

    def test_readonly_block_before_claim_can_be_checked_again_without_losing_original_allocation(self):
        self.failed_allocation()
        checked = self.inspect()
        with patch.object(self.adapter, 'inspect_empty_draft', side_effect=RuntimeError('read-only connection interrupted')):
            blocked = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                                   expected_remote=checked['_expected_remote'])
        self.assertEqual(blocked['results'][0]['state'], 'blocked')
        self.assertIsNone(blocked['results'][0]['remote_id'])
        self.assertEqual(list(self.global_root.glob('empty-recovery-*.claim.json')), [])
        fresh = self.inspect()
        self.assertTrue(fresh['can_execute'], fresh)
        recovered = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                                 expected_remote=fresh['_expected_remote'])
        self.assertTrue(recovered['all_chapters_accepted_this_run'], recovered)
        self.assertEqual((self.adapter.created, self.adapter.resumed), (1, 1))


    def test_recovery_write_followed_by_identity_failure_stays_unknown_and_unverified(self):
        self.failed_allocation()
        checked = self.inspect()
        verify = self.adapter.verify_target
        def guard(**target):
            if self.adapter.resumed:
                story.fail('platform_browser_identity_mismatch', 'Account changed after recovery save')
            return verify(**target)
        with patch.object(self.adapter, 'verify_target', side_effect=guard):
            result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                                  expected_remote=checked['_expected_remote'])
        self.assertEqual(result['results'][0]['state'], 'unknown')
        self.assertFalse(result['target_verified'])
        self.assertEqual(result['accepted_chapters'], 0)
        self.assertEqual((self.adapter.created, self.adapter.resumed), (1, 1))


class CompatibleUnknownInspectionTests(unittest.TestCase):
    setUp = CatalogBatchTests.setUp
    execute = CatalogBatchTests.execute
    inspect = CatalogBatchTests.inspect
    expand = CatalogBatchTests.expand

    def seed_unknowns(self, *, scope_changes=None, changed_content=False,
                      encoding=None, policy='strict', observed_remote='allocated', dirty=False):
        self.expand(2)
        self.adapter = CatalogAdapter(self.book)
        self.first = self.plan['manifest']['chapters'][0]
        self.adapter.seed(self.first, remote_id=observed_remote, formatting_clean=not dirty,
                          **drafts._encoded_content(self.first, drafts.PLATFORM_BODY_ENCODING, 'fanqie'))
        with drafts._checked_plan(self.book, self.plan['id']) as (manifest, fingerprint, _):
            scope = {'book_id': self.book.meta('id'), 'plan_id': self.plan['id'], 'manifest_sha256': fingerprint}
        with drafts._target_lock(manifest) as guard, drafts._journal(
                self.book, self.plan['id'], manifest, fingerprint, create=True) as journal:
            for index, attempt in enumerate(('a' * 32, 'b' * 32)):
                body_encoding = encoding if index and encoding else drafts.PLATFORM_BODY_ENCODING
                comparison_policy = policy if index else 'strict'
                content = {**self.first, 'body': self.first['body'] + '另一版本'} if index and changed_content else self.first
                guard.intent(1, attempt, content, scope={**scope, **(scope_changes or {})} if index else scope,
                             body_encoding=body_encoding, comparison_policy=comparison_policy)
                journal.append(1, 'sending', attempt_id=attempt,
                               remote_id='allocated' if index else None, body_encoding=body_encoding,
                               comparison_policy=comparison_policy)
                journal.append(1, 'unknown', attempt_id=attempt, remote_id='allocated',
                               body_encoding=body_encoding, comparison_policy=comparison_policy,
                               reason='platform_browser_save_unverified')

    def records(self):
        paths = list(self.global_root.glob('*.json'))
        paths += list((self.book.root / drafts.DIRECTORY).rglob('*.json'))
        return {path: path.read_bytes() for path in paths}

    def assert_blocked(self):
        before = self.records()
        checked = self.inspect()
        self.assertFalse(checked['can_execute'], checked)
        self.assertEqual(checked['changes'][0]['action'], 'blocked')
        self.assertEqual(checked['changes'][0]['reason'], 'unresolved_previous_content')
        self.assertEqual(self.records(), before)
        self.assertEqual((self.adapter.created, self.adapter.updated), (0, 0))

    def test_same_batch_original_and_recovery_intents_flow_through_inspection_to_sync(self):
        self.seed_unknowns()
        before = self.records()
        manuscript = self.fixture.writing_snapshot()
        checked = self.inspect()
        self.assertTrue(checked['can_execute'], checked)
        self.assertEqual([row['action'] for row in checked['changes']], ['unchanged', 'create'])
        self.assertEqual(checked['recovery_plans'], [])
        self.assertEqual(self.records(), before, 'Inspection must not publish resolutions or rewrite unknown events')
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertTrue(result['all_chapters_accepted_this_run'], result)
        self.assertEqual((self.adapter.created, self.adapter.updated), (1, 0))
        self.assertEqual([call for call in self.adapter.calls if call[0] == 'create'], [('create', 2)])
        self.assertFalse(any(call[0] == 'find' for call in self.adapter.calls))
        self.assertEqual(sum(call[0] == 'catalog' for call in self.adapter.calls), 2)
        self.assertEqual([call for call in self.adapter.calls if call[0] == 'read'],
                         [('read', 'allocated'), ('read', 'allocated'), ('read', '1')])
        with drafts._target_lock(self.plan['manifest']) as guard:
            intents, resolved = guard.attempts(1)
            self.assertEqual(set(intents), {'a' * 32, 'b' * 32})
            self.assertEqual(set(resolved), set(intents))
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))
        self.assertEqual(self.fixture.writing_snapshot(), manuscript)

    def test_other_book_scope_remains_blocked(self):
        self.seed_unknowns(scope_changes={'book_id': 'other-book'})
        self.assert_blocked()

    def test_other_plan_scope_remains_blocked(self):
        self.seed_unknowns(scope_changes={'plan_id': 'c' * 32})
        self.assert_blocked()

    def test_other_manifest_scope_remains_blocked(self):
        self.seed_unknowns(scope_changes={'manifest_sha256': 'c' * 64})
        self.assert_blocked()

    def test_other_content_remains_blocked(self):
        self.seed_unknowns(changed_content=True)
        self.assert_blocked()

    def test_other_encoding_remains_blocked(self):
        self.seed_unknowns(encoding='verbatim')
        self.assert_blocked()

    def test_other_comparison_policy_remains_blocked(self):
        self.seed_unknowns(policy='fanqie_terminal_lf_v1')
        self.assert_blocked()

    def test_other_remote_identity_remains_blocked(self):
        self.seed_unknowns(observed_remote='different-draft')
        self.assert_blocked()

    def test_unclean_formatting_remains_blocked(self):
        self.seed_unknowns(dirty=True)
        self.assert_blocked()

    def test_change_after_inspection_cannot_overwrite_or_resolve_unknowns(self):
        self.seed_unknowns()
        checked = self.inspect()
        self.assertTrue(checked['can_execute'])
        self.adapter.remote['allocated']['body'] += '平台之后修改'
        result = self.execute(sync_drafts=True, body_encoding=drafts.PLATFORM_BODY_ENCODING,
                              expected_remote=checked['_expected_remote'])
        self.assertEqual(result['results'][0]['reason'], 'platform_draft_inspection_changed')
        self.assertEqual((self.adapter.created, self.adapter.updated), (0, 0))
        with drafts._target_lock(self.plan['manifest']) as guard:
            intents, resolved = guard.attempts(1)
            self.assertEqual(set(intents), {'a' * 32, 'b' * 32})
            self.assertEqual(resolved, {})
