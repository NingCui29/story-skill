"""Explicit assistant tasks use isolated books and a synthetic host only."""
import json
import shlex
import shutil
from contextlib import contextmanager
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_platform_draft_auto_connect as base

drafts = base.drafts
story = base.story


class AssistantTaskTests(unittest.TestCase):
    def setUp(self):
        base.PlatformDraftAutoConnectTests.setUp(self)
        @contextmanager
        def session(root, binding, *, on_context, stop_requested):
            on_context({'first_call':'synthetic only','next_call':'synthetic only',
                        'execution_origin':'assistant_session','model_verification':'executor_runtime_required'})
            if not self.health_ready:
                story.fail('platform_draft_assistant_bridge_unavailable', 'Synthetic bridge unavailable')
            yield type('Session', (), {'bridge_detached':True,'session_id':'synthetic-session'})()
        item=patch.object(self.launcher,'assistant_session',side_effect=session,create=True)
        item.start()
        self.addCleanup(item.stop)
        self.cleanup_info = {'worker_done': True, 'bridge_detached': True, 'cleanup_complete': True, 'cleanup_status': 'complete'}
        cleanup = patch.object(self.launcher, 'assistant_cleanup_status', side_effect=lambda *args, **kwargs: dict(self.cleanup_info), create=True)
        cleanup.start()
        self.addCleanup(cleanup.stop)
        self.retired = False
        retirement = patch.object(self.launcher, 'assistant_retirement_status', side_effect=lambda *args, **kwargs: self.retired, create=True)
        retirement.start()
        self.addCleanup(retirement.stop)
        identity=patch.object(story.task_worker,'_process_identity',side_effect=lambda pid: 'process-'+str(pid))
        identity.start()
        self.addCleanup(identity.stop)
    cleanup_jobs = base.PlatformDraftAutoConnectTests.cleanup_jobs
    bridge_status = base.PlatformDraftAutoConnectTests.bridge_status
    def host(self, *args, **kwargs):
        host=base.PlatformDraftAutoConnectTests.host(self,*args,**kwargs)
        host.session_id='synthetic-session'
        return host
    wait_job = base.PlatformDraftAutoConnectTests.wait_job
    assert_code = base.PlatformDraftAutoConnectTests.assert_code

    def prepare(self, **kwargs):
        return drafts.workbench_assistant_prepare(self.root, self.plan['id'], self.profile_path.name,
            expected_book_id=self.book.meta('id'), expected_profile_sha256=drafts._read_profile(self.root, self.profile_path.name)[1],
            **kwargs)

    def status(self, request):
        return drafts.workbench_assistant_status(self.root, request['request_id'], expected_book_id=self.book.meta('id'))

    def execute(self, request):
        self.health_ready = True
        return drafts.assistant_run(self.root, request['request_id'], expected_book_id=request['book_id'])

    def edit_record(self, request, **changes):
        with drafts._assistant_directory(self.root) as directory:
            record = drafts._assistant_read(directory, request['request_id'])
            record.update(changes)
            drafts._assistant_write(directory, record)

    def checked(self):
        return self.execute(self.prepare())

    def test_prepare_is_local_and_same_pending_request_is_reused(self):
        request = self.prepare()
        self.assertEqual((request['state'], request['stage'], request['done']), ('prepared', 'awaiting_assistant', False))
        self.assertFalse(request['remote_accessed'])
        self.assertEqual(request['execution_origin'], 'assistant_session')
        self.assertEqual(request['model_verification'], 'executor_runtime_required')
        self.assertNotIn('model_verified', request)
        self.assertEqual(self.prepare()['request_id'], request['request_id'])
        self.assertEqual(self.operations, [])
        self.assertEqual(self.launcher.calls, [])
        self.assertIn('复制这段文字不会运行任务', request['handoff_prompt'])
        self.assertIn('gpt-6.1-terra', request['handoff_prompt'])
        self.assertIn(request['request_id'], request['handoff_prompt'])
        self.assertFalse(drafts.workbench_activity(self.root, expected_book_id=self.book.meta('id'))['busy'])

    def test_inspection_never_writes_and_explicit_confirmation_is_required(self):
        checked = self.checked()
        self.assertEqual(checked['state'], 'inspection_ready')
        self.assertTrue(checked['can_sync'])
        self.assertEqual(checked['result']['change_counts']['create'], 1)
        self.assertFalse(any(op[0] in {'create', 'update'} for op in self.operations))
        self.assert_code('platform_draft_assistant_confirmation_required', self.prepare,
                         mode='sync_drafts', inspection_id=checked['request_id'])
        self.assertEqual(self.launcher.calls, [])

    def test_sync_consumes_inspection_and_performs_production_create_and_readback(self):
        checked = self.checked()
        prepared = self.prepare(mode='sync_drafts', inspection_id=checked['request_id'], confirmed=True)
        self.assertFalse(self.status(checked)['can_sync'])
        self.assertEqual(self.remote, {})
        result = self.execute(prepared)
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(result['verified_chapters'], 1)
        self.assertEqual(result['result']['created_chapters'], 1)
        self.assertEqual([op for op in self.operations if op[0] == 'create'], [('create', 1)])
        self.assertTrue(any(op[0] == 'read' for op in self.operations))
        self.assert_code('platform_draft_assistant_request_consumed', self.execute, prepared)
        self.assert_code('platform_draft_inspection_required', self.prepare,
                         mode='sync_drafts', inspection_id=checked['request_id'], confirmed=True)

    def test_sync_without_a_current_inspection_is_rejected(self):
        self.assert_code('platform_draft_assistant_request_invalid', self.prepare, mode='sync_drafts', confirmed=True)
        self.assertEqual(self.operations, [])

    def test_attribution_evidence_path_is_prepared_locally_without_reading_it(self):
        evidence = str(self.root / 'operator-evidence.json')
        request = self.prepare(attribution_path=evidence)
        self.assertEqual(request['state'], 'prepared')
        self.assertEqual(request['attribution_path'], evidence)
        self.assertEqual(self.operations, [])
        self.assertEqual(self.launcher.calls, [])
        self.assertFalse((self.root / 'operator-evidence.json').exists())

    def test_sync_must_reuse_the_inspection_attribution_and_reconcile_rejects_one(self):
        evidence = self.root / 'operator-evidence.json'
        self.assert_code('platform_draft_attribution_invalid', self.prepare,
                         mode='reconcile_only', attribution_path=str(evidence))
        self.assert_code('platform_draft_attribution_invalid', self.prepare,
                         attribution_path='relative/evidence.json')
        checked = self.checked()
        self.assertTrue(checked['can_sync'])
        before = list(self.operations)
        self.assert_code('platform_draft_attribution_changed', self.prepare,
                         mode='sync_drafts', inspection_id=checked['request_id'], confirmed=True,
                         attribution_path=str(evidence))
        self.assertEqual(self.operations, before)

    def test_binding_change_is_rejected_before_claim_or_browser(self):
        request = self.prepare()
        self.profile['urls']['drafts'] += '?changed=true'
        self.profile_path.write_text(json.dumps(self.profile), encoding='utf-8')
        self.assertFalse(self.status(request)['source_matches_current'])
        self.assert_code('platform_draft_preview_changed', self.execute, request)
        self.assertEqual(self.operations, [])

    def test_expired_cancelled_and_repeated_requests_never_run(self):
        request = self.prepare()
        self.edit_record(request, expires_epoch=time.time() - 1)
        self.assertEqual(self.status(request)['state'], 'expired')
        self.assert_code('platform_draft_assistant_request_consumed', self.execute, request)
        newer = self.prepare()
        packet = drafts.workbench_assistant_cancel(self.root, newer['request_id'], expected_book_id=self.book.meta('id'))
        self.assertEqual(packet['state'], 'cancelled')
        self.assert_code('platform_draft_assistant_request_consumed', self.execute, newer)
        self.assertEqual(self.operations, [])

    def test_missing_bridge_fails_once_and_never_starts_automatic_worker(self):
        request = self.prepare()
        result = drafts.assistant_run(self.root, request['request_id'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error'], 'platform_draft_assistant_bridge_unavailable')
        self.assertFalse(result['remote_accessed'])
        self.assertEqual(self.launcher.calls, [])
        self.assert_code('platform_draft_assistant_request_consumed', self.execute, request)

    def test_business_done_without_worker_cleanup_stays_busy_until_bound_evidence_arrives(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False, cleanup_complete=False, cleanup_status='pending')
        request = self.checked()
        self.assertEqual(request['state'], 'inspection_ready')
        self.assertTrue(request['done'])
        self.assertTrue(request['execution_active'])
        self.assertEqual(request['stage'], 'cleanup_pending')
        self.assertFalse(request['can_sync'])
        self.assertTrue(drafts.workbench_activity(self.root, expected_book_id=request['book_id'])['busy'])
        self.assert_code('platform_draft_busy', self.prepare, mode='reconcile_only')
        self.assert_code('platform_draft_assistant_request_consumed', self.execute, request)
        latest = drafts.workbench_assistant_latest(self.root, 'f'*32, self.profile_path.name, expected_book_id=request['book_id'])
        self.assertEqual(latest['active_request']['request_id'], request['request_id'])
        operations = list(self.operations)
        self.cleanup_info.update(worker_done=True, bridge_detached=True, cleanup_complete=True, cleanup_status='complete')
        complete = self.status(request)
        self.assertTrue(complete['can_sync'])
        self.assertFalse(complete['execution_active'])
        self.assertEqual(complete['state'], request['state'])
        self.assertEqual(complete['result'], request['result'])
        self.assertEqual(self.operations, operations)
        self.assertFalse(drafts.workbench_activity(self.root, expected_book_id=request['book_id'])['busy'])

    def test_reconciliation_requires_confirmation_and_only_finished_readonly_records(self):
        evidence = str(self.root / 'private-reconcile-evidence.json')
        request = self.prepare()
        self.assert_code('platform_draft_assistant_reconcile_confirmation_required', drafts.assistant_reconcile,
                         self.root, request['request_id'], evidence, expected_book_id=self.book.meta('id'))
        self.assert_code('platform_draft_assistant_reconcile_not_readonly', drafts.assistant_reconcile,
                         self.root, request['request_id'], evidence,
                         expected_book_id=self.book.meta('id'), confirmed=True)
        self.assertEqual(self.operations, [])
        self.assertFalse(any(call[0] == 'start' for call in self.launcher.calls))

    def test_machine_reconciliation_preserves_the_original_result_and_unlocks_the_book(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False,
                                 cleanup_complete=False, cleanup_status='pending')
        request = self.checked()
        self.assertTrue(request['execution_active'])
        self.assert_code('platform_draft_busy', self.prepare, mode='reconcile_only')
        evidence = str(self.root / 'private-reconcile-evidence.json')
        record_path = self.root / drafts.ASSISTANT_DIRECTORY / (request['request_id'] + '.json')
        before = record_path.read_bytes()
        calls = []
        def reconcile_session(*args, **kwargs):
            calls.append((args, kwargs))
            self.cleanup_info.update(worker_done=False, bridge_detached=False,
                                     cleanup_complete=True, cleanup_status='reconciled_absent')
            return {'kind': 'machine_verified_absent_session'}
        with patch.object(self.launcher, 'assistant_reconcile_session',
                          side_effect=reconcile_session, create=True), \
                patch.object(story.platform_chrome_bridge, '_owner_alive', return_value=False):
            packet = drafts.assistant_reconcile(self.root, request['request_id'], evidence,
                                                expected_book_id=self.book.meta('id'), confirmed=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['request_id'], request['request_id'])
        self.assertEqual(calls[0][1]['request_binding_sha256'], request['binding_sha256'])
        self.assertEqual(calls[0][1]['evidence_path'], evidence)
        self.assertEqual(record_path.read_bytes(), before)
        self.assertFalse(packet['worker_done'])
        self.assertTrue(packet['cleanup_complete'])
        self.assertEqual(packet['cleanup_status'], 'reconciled_absent')
        self.assertFalse(packet['execution_active'])
        self.assertEqual(packet['result'], request['result'])
        self.assertFalse(drafts.workbench_activity(self.root,
            expected_book_id=self.book.meta('id'))['busy'])
        self.assertEqual(self.prepare(mode='reconcile_only')['state'], 'prepared')

    def test_detach_without_final_completion_never_unlocks_sync_or_automatic_entry(self):
        self.cleanup_info.update(worker_done=False, cleanup_complete=False, cleanup_status='pending')
        request = self.checked()
        self.assertTrue(request['bridge_detached'])
        self.assertFalse(request['can_sync'])
        preview=drafts.workbench_preview(self.root,self.plan['id'],self.profile_path.name,
             expected_book_id=self.book.meta('id'),body_encoding=drafts.PLATFORM_BODY_ENCODING)
        self.assert_code('platform_draft_busy',drafts.workbench_start,self.root,self.plan['id'],self.profile_path.name,
            expected_book_id=self.book.meta('id'),expected_profile_sha256=preview['profile_sha256'],
            preview_token=preview['connect_token'] or preview['preview_token'],execute=True,auto_connect=True,body_encoding=drafts.PLATFORM_BODY_ENCODING)
        self.assertFalse(any(call[0]=='start' for call in self.launcher.calls))

    def test_cancel_after_result_does_not_relabel_as_cancelled_or_claim_cleanup(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False, cleanup_complete=False, cleanup_status='pending')
        request = self.checked()
        result = drafts.workbench_assistant_cancel(self.root, request['request_id'], expected_book_id=request['book_id'])
        self.assertEqual(result['state'], 'inspection_ready')
        self.assertEqual(result['result'], request['result'])
        self.assertTrue(result['cleanup_pending'])
        self.assertFalse(result['stop_requested'])

    def test_dead_controller_with_host_context_is_still_cleanup_pending(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False, cleanup_complete=False, cleanup_status='unverified')
        request = self.prepare()
        self.edit_record(request, state='running', stage='waiting_host', owner_pid=2147483647,
            assistant_context={'execution_origin':'assistant_session'}, remote_accessed=True)
        result = self.status(request)
        self.assertEqual(result['state'], 'interrupted')
        self.assertTrue(result['cleanup_pending'])
        self.assert_code('platform_draft_busy', self.prepare)

    def test_a_killed_queue_that_never_reached_its_host_stops_blocking_new_work(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False, cleanup_complete=False, cleanup_status='unverified')
        request = self.prepare()
        self.edit_record(request, state='stopped', stage='cleanup_pending', done=True, owner_pid=2147483647,
            assistant_context={'execution_origin':'assistant_session','model_verification':'executor_runtime_required'},
            remote_accessed=False)
        proof = patch.object(self.launcher,'assistant_session_never_started',return_value=True,create=True)
        proof.start()
        self.addCleanup(proof.stop)
        result = self.status(request)
        self.assertEqual(result['cleanup_status'], 'not_started')
        self.assertFalse(result['cleanup_pending'])
        self.assertFalse(result['execution_active'])
        self.assertFalse(drafts.workbench_activity(self.root, expected_book_id=request['book_id'])['busy'])
        self.assertEqual(self.prepare(mode='reconcile_only')['state'], 'prepared')

    def test_the_never_started_proof_never_unlocks_a_record_that_touched_the_platform(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False, cleanup_complete=False, cleanup_status='unverified')
        request = self.prepare()
        self.edit_record(request, state='stopped', stage='cleanup_pending', done=True, owner_pid=2147483647,
            assistant_context={'execution_origin':'assistant_session','model_verification':'executor_runtime_required'},
            remote_accessed=True)
        proof = patch.object(self.launcher,'assistant_session_never_started',return_value=True,create=True)
        proof.start()
        self.addCleanup(proof.stop)
        result = self.status(request)
        self.assertEqual(result['cleanup_status'], 'unverified')
        self.assertTrue(result['cleanup_pending'])
        self.assert_code('platform_draft_busy', self.prepare)

    def test_pre_handoff_cancel_has_no_worker_cleanup_claim(self):
        request = self.prepare()
        result = drafts.workbench_assistant_cancel(self.root, request['request_id'], expected_book_id=request['book_id'])
        self.assertEqual(result['cleanup_status'], 'not_started')
        self.assertIsNone(result['worker_done'])
        self.assertIsNone(result['bridge_detached'])
        self.assertFalse(result['execution_active'])

    def test_latest_does_not_hide_older_pending_cleanup_behind_newer_result(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False, cleanup_complete=False, cleanup_status='pending')
        request = self.checked()
        with drafts._assistant_directory(self.root) as directory:
            original = drafts._assistant_read(directory, request['request_id'])
            newer = {**original, 'request_id':'f'*32, 'created_at':'2999-01-01T00:00:00Z',
                     'assistant_context':None, 'remote_accessed':False}
            drafts._assistant_write(directory, newer, new=True)
        latest = drafts.workbench_assistant_latest(self.root, self.plan['id'], self.profile_path.name,
                                                   expected_book_id=request['book_id'])
        self.assertEqual(latest['request']['request_id'], request['request_id'])
        self.assertEqual(latest['active_request']['request_id'], request['request_id'])

    def test_operator_retirement_preserves_failure_bytes_and_requires_new_readonly_inspection(self):
        self.cleanup_info.update(worker_done=False, bridge_detached=False, cleanup_complete=False, cleanup_status='pending')
        request = self.checked()
        path = self.root/drafts.ASSISTANT_DIRECTORY/(request['request_id']+'.json')
        original = path.read_bytes()
        self.assert_code('platform_draft_assistant_reset_confirmation_required', drafts.assistant_reset,
                         self.root, request['request_id'], '/synthetic/evidence')
        def retire(*args, **kwargs): self.retired = True
        with patch.object(self.launcher,'assistant_retire_readonly_session',side_effect=retire,create=True) as retirement, \
                patch.object(story.platform_chrome_bridge,'_owner_alive',return_value=False):
            result = drafts.assistant_reset(self.root, request['request_id'], '/synthetic/evidence', confirmed=True)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(result['state'], request['state'])
        self.assertTrue(result['execution_retired_by_operator'])
        self.assertFalse(result['execution_active'])
        self.assertFalse(result['worker_done'])
        self.assertFalse(result['cleanup_complete'])
        self.assertFalse(result['can_sync'])
        self.assertEqual(retirement.call_args.kwargs['request_binding_sha256'], request['binding_sha256'])
        self.assert_code('platform_draft_inspection_required', self.prepare, mode='sync_drafts',
                         inspection_id=request['request_id'], confirmed=True)
        self.assertEqual(self.prepare()['mode'], 'inspect_drafts')

    def test_operator_retirement_refuses_write_tasks_and_live_controller(self):
        checked = self.checked()
        writing = self.prepare(mode='sync_drafts', inspection_id=checked['request_id'], confirmed=True)
        self.execute(writing)
        self.assert_code('platform_draft_assistant_reset_not_readonly', drafts.assistant_reset,
                         self.root, writing['request_id'], '/synthetic/evidence', confirmed=True)
        with patch.object(story.platform_chrome_bridge,'_owner_alive',return_value=True):
            self.assert_code('platform_draft_assistant_reset_process_alive', drafts.assistant_reset,
                             self.root, checked['request_id'], '/synthetic/evidence', confirmed=True)

    def test_dead_claim_is_interrupted_not_resumable(self):
        request = self.prepare()
        self.edit_record(request, state='running', stage='waiting_host', owner_pid=2147483647)
        self.assertEqual(self.status(request)['state'], 'interrupted')
        self.assert_code('platform_draft_assistant_request_consumed', self.execute, request)

    def test_unknown_write_needs_fresh_readback_and_confirmed_sync_without_resending(self):
        checked = self.checked()
        writing = self.prepare(mode='sync_drafts', inspection_id=checked['request_id'], confirmed=True)
        self.fail_create = True
        result = self.execute(writing)
        self.assertEqual(result['state'], 'unknown')
        self.assert_code('platform_draft_assistant_request_consumed', self.execute, writing)
        self.fail_create = False
        inspection = self.execute(self.prepare())
        self.assertEqual(inspection['state'], 'inspection_ready')
        self.assertTrue(inspection['can_sync'])
        self.assertEqual(inspection['result']['changes'][0]['action'], 'unchanged')
        recovered = self.execute(self.prepare(mode='sync_drafts', inspection_id=inspection['request_id'], confirmed=True))
        self.assertEqual(recovered['state'], 'succeeded')
        self.assertEqual(len([op for op in self.operations if op[0] == 'create']), 1)

    def test_cooperative_stop_never_relabels_an_unknown_write_as_stopped(self):
        checked = self.checked()
        request = self.prepare(mode='sync_drafts', inspection_id=checked['request_id'], confirmed=True)
        original = drafts.run_plan
        def core(*args, **kwargs):
            if not kwargs.get('execute'):
                return original(*args, **kwargs)
            return {'results':[{'chapter':1, 'state':'unknown', 'recorded_this_run':True}],
                    'stop_requested':True, 'accepted_chapters':0, 'all_chapters_accepted_this_run':False}
        with patch.object(drafts, 'run_plan', side_effect=core):
            result = self.execute(request)
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(result['can_sync'])

    def test_remote_change_after_inspection_blocks_write(self):
        checked = self.checked()
        request = self.prepare(mode='sync_drafts', inspection_id=checked['request_id'], confirmed=True)
        self.remote['existing'] = {'remote_id':'existing','chapter':1,'state':'draft','title':'Changed','body':'Changed'}
        result = self.execute(request)
        self.assertNotEqual(result['state'], 'succeeded')
        self.assertFalse(any(op[0] in {'create','update'} for op in self.operations))

    def test_pending_other_scope_is_busy_and_visible_in_latest(self):
        request = self.prepare()
        self.assert_code('platform_draft_busy', self.prepare, mode='reconcile_only')
        packet = drafts.workbench_assistant_latest(self.root, 'a'*32, self.profile_path.name,
                                                   expected_book_id=self.book.meta('id'))
        self.assertIsNone(packet['request'])
        self.assertEqual(packet['active_request']['request_id'], request['request_id'])

    def test_concurrent_claim_runs_once_and_activity_tracks_the_assistant(self):
        request = self.prepare()
        entered, released = threading.Event(), threading.Event()
        original = drafts.inspect_plan
        def held(*args, **kwargs):
            entered.set()
            if not released.wait(5): raise AssertionError('test did not release')
            return original(*args, **kwargs)
        results=[]
        with patch.object(drafts, 'inspect_plan', side_effect=held):
            self.health_ready=True
            thread=threading.Thread(target=lambda: results.append(self.execute(request)))
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertTrue(drafts.workbench_activity(self.root, expected_book_id=self.book.meta('id'))['busy'])
                self.assert_code('platform_draft_assistant_request_consumed', self.execute, request)
                cancelled=drafts.workbench_assistant_cancel(self.root, request['request_id'], expected_book_id=self.book.meta('id'))
                self.assertTrue(cancelled['stop_requested'])
                self.assertEqual(cancelled['state'],'running')
            finally:
                released.set(); thread.join(5)
        self.assertEqual(results[0]['state'], 'stopped')
        self.assertEqual(self.launcher.calls, [])

    def test_healthy_manual_bridge_does_not_unlock_automatic_entry(self):
        self.health_ready=True
        self.launcher.owned=False
        self.checked()
        runtime=drafts._execution_runtime(self.root,*drafts._preflight(self.root,self.plan['id'],self.profile_path.name,
            self.book.meta('id'),False,'strict',drafts.PLATFORM_BODY_ENCODING,False,True)[1:])
        self.assertFalse(runtime['worker']['ready'])
        self.assertEqual(runtime['worker']['error'],'platform_chrome_worker_model_unverified')

    def test_expired_inspection_cannot_authorize_a_waiting_write_request(self):
        checked=self.checked()
        writing=self.prepare(mode='sync_drafts',inspection_id=checked['request_id'],confirmed=True)
        self.edit_record(checked,inspection_expires_epoch=time.time()-1)
        self.assertFalse(self.status(checked)['can_sync'])
        self.assert_code('platform_draft_inspection_required',self.execute,writing)
        self.assertFalse(any(op[0] in {'create','update'} for op in self.operations))

    def test_exact_assistant_session_is_rechecked_after_host_enter(self):
        request=self.prepare()
        def wrong(*args,**kwargs):
            host=self.host(*args,**kwargs)
            host.session_id='another-session'
            return host
        with patch.object(story.platform_chrome_bridge,'HostAdapter',side_effect=wrong):
            result=self.execute(request)
        self.assertEqual(result['error'],'platform_draft_assistant_bridge_changed')
        self.assertFalse(result['remote_accessed'])
        self.assertFalse(any(op[0] in {'verify','find','create','update','read'} for op in self.operations))

    def test_claimed_pid_reuse_is_interrupted_and_cannot_replay(self):
        import os
        request=self.prepare()
        self.edit_record(request,state='running',stage='waiting_host',owner_pid=os.getpid(),owner_identity='old-process')
        self.assertEqual(self.status(request)['state'],'interrupted')
        self.assert_code('platform_draft_assistant_request_consumed',self.execute,request)

    def test_status_during_core_execution_does_not_reenter_the_journal(self):
        request=self.prepare()
        original=drafts.inspect_plan
        def inspect(*args,**kwargs):
            with patch.object(drafts,'_journal',side_effect=AssertionError('status may not enter journal')):
                packet=self.status(request)
                self.assertEqual(packet['state'],'running')
                self.assertTrue(packet['source_matches_current'])
            return original(*args,**kwargs)
        with patch.object(drafts,'inspect_plan',side_effect=inspect):
            self.assertEqual(self.execute(request)['state'],'inspection_ready')

    def test_journal_failure_after_possible_write_is_unknown(self):
        checked=self.checked()
        request=self.prepare(mode='sync_drafts',inspection_id=checked['request_id'],confirmed=True)
        def failed(*args,**kwargs):
            story.fail('platform_draft_journal_write_failed','Synthetic post-send storage failure')
        with patch.object(drafts,'run_plan',wraps=drafts.run_plan) as core:
            original=core._mock_wraps
            core.side_effect=lambda *args,**kwargs: failed(*args,**kwargs) if kwargs.get('execute') else original(*args,**kwargs)
            result=self.execute(request)
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(result['error'],'platform_draft_journal_write_failed')
        self.assert_code('platform_draft_assistant_request_consumed',self.execute,request)

    def test_strict_record_limits_and_symlink_request_are_rejected(self):
        request=self.prepare()
        self.edit_record(request,checked_chapters=True)
        self.assert_code('platform_draft_assistant_request_invalid',self.status,request)
        path=self.root/drafts.ASSISTANT_DIRECTORY/(request['request_id']+'.json')
        raw=path.read_text()
        path.unlink()
        other=self.root/'outside.json'
        other.write_text(raw)
        path.symlink_to(other)
        with self.assertRaises(story.StoryError): self.status(request)
        self.assertEqual(other.read_text(),raw)

    def test_assistant_context_is_not_a_model_authentication_and_cli_is_registered(self):
        request=self.prepare()
        seen=[]
        self.health_ready=True
        result=drafts.assistant_run(self.root,request['request_id'],on_context=seen.append)
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0]['request_id'],request['request_id'])
        self.assertEqual(result['assistant_context']['model_verification'],'executor_runtime_required')
        self.assertNotIn('model_verified',result['assistant_context'])
        self.assertTrue(result['bridge_detached'])
        parser=story.parser()
        args=parser.parse_args(['publish-draft-assist-run','--book',str(self.root),'--request',request['request_id']])
        self.assertEqual(args.command,'publish-draft-assist-run')

    def test_handoff_command_quotes_paths_as_literal_arguments(self):
        argv=['/path with space/python', '/quote\'$(touch nope)`file.py','--book','A&B%USERPROFILE%']
        self.assertEqual(shlex.split(drafts._assistant_command(argv)),argv)
        self.assertEqual(drafts._assistant_command(argv,windows=True),
                         "& '/path with space/python' '/quote''$(touch nope)`file.py' '--book' 'A&B%USERPROFILE%'")

    def test_malformed_mode_and_non_current_write_rules_are_rejected_locally(self):
        self.assert_code('invalid_input',self.prepare,mode=['sync_drafts'])
        self.assert_code('platform_draft_body_encoding_required',self.prepare,body_encoding='verbatim')
        self.assertEqual(self.operations,[])
        self.assertEqual(self.launcher.calls,[])

    def test_running_assistant_blocks_automatic_start_even_from_another_panel(self):
        import os
        request=self.prepare()
        self.edit_record(request,state='running',stage='waiting_assistant_bridge',owner_pid=os.getpid(),
                         owner_identity='process-'+str(os.getpid()))
        preview=drafts.workbench_preview(self.root,self.plan['id'],self.profile_path.name,
             expected_book_id=self.book.meta('id'),body_encoding=drafts.PLATFORM_BODY_ENCODING)
        self.assert_code('platform_draft_busy',drafts.workbench_start,self.root,self.plan['id'],self.profile_path.name,
            expected_book_id=self.book.meta('id'),expected_profile_sha256=preview['profile_sha256'],
            preview_token=preview['connect_token'] or preview['preview_token'],execute=True,auto_connect=True,body_encoding=drafts.PLATFORM_BODY_ENCODING)
        self.assertFalse(any(call[0]=='start' for call in self.launcher.calls))

    def test_restoring_same_root_prepared_bytes_cannot_replay_a_consumed_request(self):
        request=self.prepare()
        path=self.root/drafts.ASSISTANT_DIRECTORY/(request['request_id']+'.json')
        old=path.read_bytes()
        self.execute(request)
        path.write_bytes(old)
        restored=self.status(request)
        self.assertEqual(restored['state'],'interrupted')
        self.assertEqual(restored['error'],'platform_draft_assistant_request_consumed')
        self.assertTrue(restored['cleanup_pending'])
        self.assertTrue(restored['execution_active'])
        self.assert_code('platform_draft_busy', self.prepare)
        cancelled=drafts.workbench_assistant_cancel(self.root,request['request_id'],expected_book_id=request['book_id'])
        self.assertEqual(cancelled['state'],'interrupted')
        self.assertEqual(path.read_bytes(),old)
        self.assert_code('platform_draft_assistant_request_consumed',self.execute,request)

    def test_restored_inspection_cannot_authorize_another_sync_request(self):
        checked=self.checked()
        path=self.root/drafts.ASSISTANT_DIRECTORY/(checked['request_id']+'.json')
        old=path.read_bytes()
        writing=self.prepare(mode='sync_drafts',inspection_id=checked['request_id'],confirmed=True)
        self.assertEqual(self.execute(writing)['state'],'succeeded')
        path.write_bytes(old)
        self.assertFalse(self.status(checked)['can_sync'])
        self.assert_code('platform_draft_inspection_required',self.prepare,mode='sync_drafts',
                         inspection_id=checked['request_id'],confirmed=True)
        self.assertEqual(len([op for op in self.operations if op[0]=='create']),1)

    def test_copy_to_another_root_keeps_history_but_never_reactivates_request(self):
        import os
        request=self.prepare()
        self.edit_record(request,state='running',stage='waiting_assistant_bridge',owner_pid=os.getpid(),
                         owner_identity='process-'+str(os.getpid()))
        clone=self.root.parent/'restored-copy'
        shutil.copytree(self.root,clone)
        packet=drafts.workbench_assistant_status(clone,request['request_id'],expected_book_id=request['book_id'])
        self.assertTrue(packet['origin_mismatch'])
        self.assertEqual(packet['state'],'interrupted')
        self.assertFalse(drafts.workbench_activity(clone,expected_book_id=request['book_id'])['busy'])
        self.assert_code('platform_draft_book_changed',drafts.assistant_run,clone,request['request_id'])
        self.assertEqual(self.operations,[])


if __name__ == '__main__':
    unittest.main()
