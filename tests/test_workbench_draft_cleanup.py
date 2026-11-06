"""Workbench cleanup recovery using real local protocols and synthetic browser evidence."""
import json
import hashlib
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from tests import test_chrome_assistant_session as fixtures

story, drafts, worker, bridge = fixtures.story, fixtures.drafts, fixtures.worker, fixtures.bridge


class WorkbenchDraftCleanupTests(unittest.TestCase):
    def setUp(self):
        self.session = fixtures.AssistantSessionTests()
        self.session.setUp()
        self.addCleanup(self.session.doCleanups)
        self.root, self.book = self.session.root, self.session.book
        self.book_id = self.book.meta('id')
        self.make_session()
        self.request_id = 'a' * 32
        binding = {key: value for key, value in self.session.binding.items() if key != 'target'}
        binding['mode'] = 'inspect_drafts'
        self.record = {'schema': 1, 'request_id': self.request_id, 'book_root': str(self.root),
            'binding': binding, 'binding_sha256': drafts._hash(binding), 'target': self.session.binding['target'],
            'execution_origin': 'assistant_session', 'model_verification': 'executor_runtime_required',
            'execution_host': 'codex', 'native_model_authorized': False,
            'state': 'inspection_blocked', 'stage': 'finished', 'done': True, 'created_at': drafts._now(),
            'expires_at': drafts._now(), 'expires_epoch': time.time() - 1, 'stop_requested': False,
            'checked_chapters': 0, 'verified_chapters': 0, 'total_chapters': 1, 'remote_accessed': True,
            'owner_pid': os.getpid(), 'assistant_context': self.session.packet}
        self.save_task()
        self.task_path = self.root / drafts.ASSISTANT_DIRECTORY / (self.request_id + '.json')
        self.original = self.task_path.read_bytes()
        owner = patch.object(bridge, '_owner_alive', return_value=False)
        owner.start()
        self.addCleanup(owner.stop)

    def make_session(self):
        self.session.terminal_readonly_session()
        self.session.ownership()
        self.session.worker_health()
        self.session.queue_record()

    def save_task(self):
        with drafts._assistant_directory(self.root, create=True) as directory:
            drafts._assistant_write(directory, self.record)

    def prepare(self, **kwargs):
        return drafts.workbench_assistant_reconcile_prepare(self.root, self.request_id,
            expected_book_id=self.book_id, **{'confirmed': True, **kwargs})

    def blockers(self):
        return drafts.workbench_assistant_blockers(self.root, expected_book_id=self.book_id)

    def observation(self, prepared, **changes):
        return {'schema': 1, 'recovery_id': prepared['recovery_id'],
                'source': 'official_node_repl_browser_use', 'browser_id': 'synthetic-chrome',
                'observed_at_ms': int(time.time() * 1000), 'tabs': [{'id': 'unrelated-user-tab'}], **changes}

    def complete(self, prepared, observation=None):
        return drafts.workbench_assistant_reconcile_complete(self.root, prepared['recovery_id'],
            self.observation(prepared) if observation is None else observation, expected_book_id=self.book_id)

    def assert_code(self, code, action, *args, **kwargs):
        with self.assertRaises(story.StoryError) as error:
            action(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_blockers_preflight_and_prepare_are_readonly_and_reuse_one_private_handoff(self):
        rows = self.blockers()
        self.assertEqual(len(rows['blockers']), 1)
        self.assertTrue(rows['blockers'][0]['cleanup_recovery_eligible'])
        self.assertNotIn('assistant_context', rows['blockers'][0])
        self.assert_code('platform_draft_assistant_reconcile_confirmation_required', self.prepare, confirmed=False)
        prepared = self.prepare()
        self.assertEqual(prepared['recovery_id'], self.prepare()['recovery_id'])
        self.assertEqual(prepared['state'], 'prepared')
        self.assertIn('gpt-6.1-terra', prepared['handoff_prompt'])
        self.assertNotIn('python', prepared['handoff_prompt'])
        directory = self.root / drafts.ASSISTANT_RECOVERY_DIRECTORY
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.original, self.task_path.read_bytes())
        self.assertFalse(self.session.reconciled_path().exists())

    def test_actual_reconcile_protocol_preserves_task_and_idempotently_returns_verified_receipt(self):
        prepared = self.prepare()
        observation = self.observation(prepared)
        completed = self.complete(prepared, observation)
        self.assertEqual(completed['state'], 'completed')
        self.assertEqual(completed['cleanup_status'], 'reconciled_absent')
        self.assertFalse(completed['worker_done'])
        self.assertFalse(completed['platform_written'])
        self.assertEqual(completed['blockers'], [])
        receipt = self.session.reconciled_path().read_bytes()
        self.assertEqual(self.complete(prepared, observation), completed)
        self.assertEqual(receipt, self.session.reconciled_path().read_bytes())
        self.assertEqual(self.original, self.task_path.read_bytes())
        self.assert_code('platform_draft_assistant_reconcile_conflict', self.complete, prepared,
                         {**observation, 'tabs': [{'id': 'a-different-user-tab'}]})
        raw = self.root / drafts.ASSISTANT_RECOVERY_DIRECTORY / (prepared['recovery_id'] + '-inventory.json')
        self.assertEqual(json.loads(raw.read_text()), observation)

    def test_unknown_write_is_visible_but_never_recoverable(self):
        self.record['binding']['mode'] = 'sync_drafts'
        self.record['binding_sha256'] = drafts._hash(self.record['binding'])
        self.record['state'] = 'unknown'
        self.save_task()
        row = self.blockers()['blockers'][0]
        self.assertFalse(row['cleanup_recovery_eligible'])
        self.assert_code('platform_draft_assistant_reconcile_not_readonly', self.prepare)
        self.assertFalse(self.session.reconciled_path().exists())

    def test_task_cannot_self_assert_the_reserved_archived_marker(self):
        self.record['binding']['mode'] = 'sync_drafts'
        self.record['binding_sha256'] = drafts._hash(self.record['binding'])
        self.record.update(state='unknown')
        for derived in ({'archived_never_started': True, 'archive_path': 'self-asserted'}, {'origin_mismatch': True}):
            with self.subTest(derived=derived):
                self.task_path.write_text(json.dumps({**self.record, **derived}))
                self.assert_code('platform_draft_assistant_request_invalid', self.blockers)
                self.assert_code('platform_draft_assistant_request_invalid', drafts.workbench_assistant_status,
                                 self.root, self.request_id, expected_book_id=self.book_id)

    def test_self_asserted_cleanup_wrong_source_and_wrong_browser_are_rejected(self):
        prepared = self.prepare()
        for change in ({'cleanup': True}, {'source': 'self_reported'}, {'browser_id': 'other'},
                       {'tabs': [{'id': 'synthetic-tab-1', 'present': False}]},
                       {'tabs': [{'id': 'x'}, {'id': 'x'}]}, {'tabs': [{'id': 'x' * 201}]}):
            with self.subTest(change=change):
                self.assert_code('platform_draft_assistant_reconcile_observation_invalid', self.complete,
                                 prepared, self.observation(prepared, **change))
        self.assertFalse(self.session.reconciled_path().exists())

    def test_present_owned_tab_and_expired_observation_keep_blocking(self):
        prepared = self.prepare()
        self.assert_code('platform_draft_assistant_reconcile_tabs_present', self.complete, prepared,
                         self.observation(prepared, tabs=[{'id': 'synthetic-tab-1'}]))
        self.assert_code('platform_draft_assistant_reconcile_observation_expired', self.complete, prepared,
                         self.observation(prepared, observed_at_ms=int((time.time() - 121) * 1000)))
        self.assertEqual(len(self.blockers()['blockers']), 1)

    def test_expired_handoff_cannot_complete_but_new_confirmation_creates_another(self):
        prepared = self.prepare()
        future = time.time() + drafts.ASSISTANT_RECOVERY_SECONDS + 1
        with patch.object(drafts.time, 'time', return_value=future):
            self.assert_code('platform_draft_assistant_reconcile_expired', self.complete, prepared)
            self.assertEqual(self.blockers()['recoveries'][0]['state'], 'expired')
            another = self.prepare()
        self.assertNotEqual(another['recovery_id'], prepared['recovery_id'])

    def test_completion_rechecks_task_bytes_and_session_ownership(self):
        prepared = self.prepare()
        self.task_path.write_bytes(self.original + b'\n')
        self.assert_code('platform_draft_assistant_reconcile_changed', self.complete, prepared)
        self.task_path.write_bytes(self.original)
        self.session.ownership(owned=('synthetic-tab-3',))
        self.assert_code('platform_draft_assistant_reconcile_changed', self.complete, prepared)
        self.assertFalse(self.session.reconciled_path().exists())

    def test_task_and_queue_changes_during_completion_are_rechecked_under_existing_locks(self):
        prepared = self.prepare()
        original = worker.assistant_reconcile_session
        def changed(*args, **kwargs):
            self.session.queue_record(deadline=time.time() - 2)
            return original(*args, **kwargs)
        with patch.object(worker, 'assistant_reconcile_session', side_effect=changed):
            self.assert_code('platform_draft_assistant_reconcile_changed', self.complete, prepared)
        self.assertFalse(self.session.reconciled_path().exists())
        self.assertEqual(self.original, self.task_path.read_bytes())

    def test_existing_reconciled_receipt_cannot_be_claimed_by_a_different_observation(self):
        prepared = self.prepare()
        prior = self.session.reconcile_evidence(request_binding_sha256=self.record['binding_sha256'])
        self.session.reconcile(prior, request_binding_sha256=self.record['binding_sha256'])
        observation = self.observation(prepared, observed_at_ms=int(time.time() * 1000) + 1)
        self.assert_code('platform_draft_assistant_reconcile_conflict', self.complete, prepared, observation)
        self.assertEqual(self.blockers()['recoveries'][0]['state'], 'prepared')

    def test_completion_rechecks_live_controller_live_lease_and_write_queue(self):
        prepared = self.prepare()
        with patch.object(bridge, '_owner_alive', return_value=True):
            self.assert_code('platform_draft_assistant_reconcile_process_alive', self.complete, prepared)
        sid = json.loads(self.session.handle.read_text())['session_id']
        with bridge._directory(self.root, sid) as directory:
            bridge._write(directory, 'lease.json', {'schema': 1, 'session_id': sid,
                'expires': time.time() + 60, 'detached': False}, replace=True)
        self.assert_code('platform_draft_assistant_reconcile_invalid', self.complete, prepared)
        self.session.detach()
        self.session.queue_record(operation='create_draft')
        self.assert_code('platform_draft_assistant_reset_not_readonly', self.complete, prepared)
        self.assertFalse(self.session.reconciled_path().exists())

    def test_completion_never_trusts_caller_book_identity(self):
        prepared = self.prepare()
        self.assert_code('platform_draft_book_changed', drafts.workbench_assistant_reconcile_complete,
                         self.root, prepared['recovery_id'], self.observation(prepared), expected_book_id='foreign')

    def prepare_reset(self, **kwargs):
        with patch.object(worker, '_assistant_chrome_processes', return_value=[2147483647]), \
                patch.object(story.task_worker, '_process_identity', return_value='synthetic-process-before-normal-quit'):
            return self.prepare(recovery_mode='external_session_reset',
                **{'authorization_text': '作者已保存 Chrome 内全部编辑，并授权正常退出 Chrome 和重置官方内核。', **kwargs})

    def reset_observation(self, prepared, **changes):
        now = time.time()
        return {'schema': 1, 'recovery_id': prepared['recovery_id'], 'source': 'official_node_repl_browser_use',
            'chrome_quit': {'old_processes': [{'pid': 2147483647, 'start_identity': 'synthetic-process-before-normal-quit'}],
                            'observed_none_at': now},
            'official_kernel_reset': {'tool': 'mcp__node_repl__js_reset', 'completed_at': now}, **changes}

    def test_external_reset_requires_author_authorization_and_pins_real_process_inventory(self):
        self.assert_code('platform_draft_assistant_reset_confirmation_required', self.prepare_reset, authorization_text=None)
        with patch.object(worker, '_assistant_chrome_processes', return_value=[]):
            self.assert_code('platform_draft_assistant_reset_invalid', self.prepare,
                             recovery_mode='external_session_reset', authorization_text='明确正常退出授权')
        prepared = self.prepare_reset()
        self.assertEqual(prepared['recovery_mode'], 'external_session_reset')
        self.assertIn('禁止强制杀进程', prepared['handoff_prompt'])
        self.assertIn('synthetic-process-before-normal-quit', prepared['handoff_prompt'])
        self.assertNotEqual(prepared['recovery_id'], self.prepare()['recovery_id'])
        self.assertEqual(self.original, self.task_path.read_bytes())

    def test_external_reset_reuses_existing_strict_protocol_and_never_claims_cleanup_success(self):
        prepared = self.prepare_reset()
        observation = self.reset_observation(prepared)
        with patch.object(worker, '_assistant_chrome_processes', return_value=[]):
            result = self.complete(prepared, observation)
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(result['cleanup_status'], 'external_session_reset')
            self.assertFalse(result['cleanup_complete'])
            self.assertFalse(result['worker_done'])
            self.assertFalse(result['platform_written'])
            self.assertTrue(result['execution_retired_by_operator'])
            self.assertEqual(result['blockers'], [])
            self.assertEqual(self.complete(prepared, observation), result)
        self.assertEqual(self.original, self.task_path.read_bytes())
        self.assertFalse(self.session.reconciled_path().exists())

    def test_external_reset_rejects_processes_still_running_and_forged_reset_or_authorization(self):
        prepared = self.prepare_reset()
        observation = self.reset_observation(prepared)
        with patch.object(worker, '_assistant_chrome_processes', return_value=[12]):
            self.assert_code('platform_draft_assistant_reset_process_alive', self.complete, prepared, observation)
        for changes in ({'authorized_by_user': {'text': 'self asserted'}},
                        {'official_kernel_reset': {'tool': 'ordinary_browser_reconnect', 'completed_at': time.time()}},
                        {'chrome_quit': {'old_processes': [], 'observed_none_at': time.time()}}):
            self.assert_code('platform_draft_assistant_reconcile_observation_invalid', self.complete, prepared,
                             self.reset_observation(prepared, **changes))
        self.assertEqual(len(self.blockers()['blockers']), 1)

    def test_external_reset_cannot_release_an_unknown_write_or_changed_queue(self):
        prepared = self.prepare_reset()
        self.session.queue_record(operation='create_draft')
        with patch.object(worker, '_assistant_chrome_processes', return_value=[]):
            self.assert_code('platform_draft_assistant_reset_not_readonly', self.complete, prepared,
                             self.reset_observation(prepared))
        self.record['binding']['mode'] = 'sync_drafts'
        self.record['binding_sha256'] = drafts._hash(self.record['binding'])
        self.record['state'] = 'unknown'
        self.save_task()
        self.assert_code('platform_draft_assistant_reconcile_not_readonly', self.prepare_reset)


class WorkbenchNeverStartedArchiveTests(unittest.TestCase):
    save_task = WorkbenchDraftCleanupTests.save_task
    blockers = WorkbenchDraftCleanupTests.blockers
    assert_code = WorkbenchDraftCleanupTests.assert_code

    def make_session(self):
        self.session.prepare_only()

    def setUp(self):
        WorkbenchDraftCleanupTests.setUp(self)
        self.record.update(state='stopped', remote_accessed=False)
        self.save_task()
        self.original = self.task_path.read_bytes()

    def archive(self, **kwargs):
        return drafts.workbench_assistant_archive_never_started(self.root, self.request_id,
            expected_book_id=self.book_id, **{'confirmed': True, **kwargs})

    def test_explicit_archive_keeps_exact_history_and_old_service_scan_is_empty(self):
        packet = self.blockers()
        self.assertEqual(packet['blockers'], [])
        self.assertEqual(len(packet['legacy_blockers']), 1)
        self.assertFalse(packet['legacy_blockers'][0]['execution_active'])
        self.assert_code('platform_draft_assistant_archive_confirmation_required', self.archive, confirmed=False)
        result = self.archive()
        archived = Path(result['archive_path'])
        self.assertEqual(result['state'], 'archived_never_started')
        self.assertEqual(result['original_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(archived.read_bytes(), self.original)
        self.assertFalse(self.task_path.exists())
        self.assertEqual(list(self.task_path.parent.glob('[0-9a-f]' * 32 + '.json')), [])
        self.assertEqual(result['legacy_blockers'], [])
        with drafts._assistant_directory(self.root) as directory:
            rows = drafts._assistant_records(directory)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]['archived_never_started'])
            self.assertEqual(rows[0]['state'], 'stopped')
            self.assert_code('platform_draft_assistant_request_archived', drafts._assistant_write, directory, rows[0])
        self.assert_code('platform_draft_assistant_request_consumed', drafts.assistant_run,
                         self.root, self.request_id, expected_book_id=self.book_id)
        self.assertEqual(self.archive(), result)

    def test_active_remote_unknown_write_and_live_controller_cannot_archive(self):
        original = json.loads(self.original)
        for changes in ({'state': 'running', 'done': False}, {'state': 'prepared', 'done': False},
                        {'remote_accessed': True}, {'remote_accessed': None}):
            self.record = {**original, **changes}
            self.save_task()
            self.assert_code('platform_draft_assistant_archive_not_unstarted', self.archive)
        self.record = json.loads(self.original)
        self.record['binding']['mode'] = 'sync_drafts'
        self.record['binding_sha256'] = drafts._hash(self.record['binding'])
        self.record['state'] = 'unknown'
        self.save_task()
        self.assert_code('platform_draft_assistant_archive_not_unstarted', self.archive)
        self.record = json.loads(self.original)
        self.save_task()
        with patch.object(bridge, '_owner_alive', return_value=True):
            self.assert_code('platform_draft_assistant_archive_not_unstarted', self.archive)
        self.assertEqual(self.original, self.task_path.read_bytes())

    def test_any_host_artifact_or_attached_session_prevents_archive(self):
        path = self.session.context / 'worker-progress-1.json'
        bridge._external(path, create_value={'schema': 1})
        self.assert_code('platform_draft_assistant_archive_not_unstarted', self.archive)
        path.unlink()
        with patch.object(bridge, 'worker_status', return_value={'attached': True}):
            self.assert_code('platform_draft_assistant_archive_not_unstarted', self.archive)
        self.assertEqual(self.original, self.task_path.read_bytes())

    def test_failed_atomic_move_retains_source_and_same_receipt_can_finish(self):
        with patch.object(drafts, '_atomic_publish', side_effect=OSError('synthetic move failure')):
            with self.assertRaises(OSError):
                self.archive()
        self.assertEqual(self.original, self.task_path.read_bytes())
        self.assertEqual(len(self.blockers()['legacy_blockers']), 1)
        result = self.archive()
        self.assertEqual(Path(result['archive_path']).read_bytes(), self.original)

    def test_altered_archived_bytes_or_duplicate_live_record_are_not_accepted(self):
        result = self.archive()
        path = Path(result['archive_path'])
        path.write_bytes(self.original + b'\n')
        self.assert_code('platform_draft_assistant_archive_invalid', drafts.workbench_assistant_status,
                         self.root, self.request_id, expected_book_id=self.book_id)
        path.write_bytes(self.original)
        self.task_path.write_bytes(self.original)
        self.assert_code('platform_draft_assistant_request_invalid', self.blockers)


if __name__ == '__main__':
    unittest.main()
