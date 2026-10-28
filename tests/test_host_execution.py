"""Synthetic controller receipts exercise host idempotency; no model calls here."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_workbench as base

story = base.story
REAL_MODEL_DEFAULTS = story.task_worker._model_defaults


class ManagedHostTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.book = self.fixture.root, self.fixture.book
        self.worker, self.tasks = story.task_worker, story.tasks
        self.cache = self.root.parent / 'private-workers'
        self.addCleanup(patch.stopall)
        patch.object(self.worker, '_base', return_value=self.cache).start()
        patch.object(self.worker, '_executable', return_value=Path(sys.executable).resolve()).start()
        patch.object(self.worker, '_model_defaults', return_value={}).start()
        self.spawn = patch.object(self.worker, '_spawn', return_value=type('Process', (), {'pid': os.getpid()})()).start()
        self.task = story.workbench._editor_review_task(self.root, 'formal:2')['task']
        self.before = self.fixture.authoritative_state()

    def start(self, request='request-one', **kwargs):
        return self.tasks.execution_start(self.root, self.task['id'], self.task['version'], request, **kwargs)

    def expect(self, code, callback):
        with self.assertRaises(story.StoryError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code, raised.exception.details)

    def ledger(self, execution_id=None):
        with self.tasks._book(self.root) as book:
            value = self.worker._head(book, self.task['id'])
            if execution_id:
                value = self.worker._read_ledger(book, self.task['id'], execution_id)[0]
            return copy.deepcopy(value)

    def update(self, changes):
        with self.tasks._book(self.root, write=True) as book:
            value, sha = self.worker._read_ledger(book, self.task['id'], self.ledger_id)
            value.update(changes)
            self.worker._save_ledger(book, value, sha)

    def native_receipt(self, execution, state='completed', value=None, exit_code=None):
        ident = execution['id']; self.ledger_id = ident
        ledger = self.ledger(ident); workspace = self.worker._workspace(ident)
        if value is None:
            value = {'summary': '已读所给原文，钥匙交接仍需作者结合前章选择。', 'issues': [], 'candidate_text': None}
        text = json.dumps(value, ensure_ascii=False)
        events = [{'type': 'thread.started', 'thread_id': 'synthetic-native-session'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}},
                  {'type': 'turn.completed' if state == 'completed' else 'turn.failed'}]
        trace = ('\n'.join(json.dumps(v, ensure_ascii=False) for v in events) + '\n').encode()
        self.worker._write_new(workspace, 'trace.jsonl', trace)
        self.worker._write_new(workspace, 'final.json', text.encode())
        receipt = {key: ledger[key] for key in ('id', 'task_id', 'book_id', 'binding_sha256', 'nonce')}
        receipt.update(state=state, exit_code=(0 if state == 'completed' else 1) if exit_code is None else exit_code,
                       trace_sha256=hashlib.sha256(trace).hexdigest(), result_sha256=hashlib.sha256(text.encode()).hexdigest(),
                       error=None, finished=self.worker._now())
        self.worker._write_new(workspace, 'process.json', story.dumps(receipt).encode())
        return workspace

    def finish(self, packet, **kwargs):
        self.native_receipt(packet['execution'], **kwargs)
        return self.tasks.execution_reconcile(self.root, self.task['id'], packet['execution']['id'])

    def test_empty_host_read_does_not_create_execution_files(self):
        response = self.tasks.execution_get(self.root, self.task['id'])
        self.assertIsNone(response['execution'])
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])

    def test_repeated_runtime_heartbeats_do_not_create_unbounded_book_backups(self):
        import time
        response = self.start(); self.ledger_id = response['execution']['id']
        for index in range(50):
            self.update({'updated_epoch': time.time() + index / 1000})
        directory = self.root / self.tasks.DIRECTORY / 'executions'
        self.assertFalse(any(path.name.startswith('.previous-') for path in directory.iterdir()))

    def test_verified_history_survives_later_controller_source_changes(self):
        response = self.start(); self.finish(response)
        with patch.object(self.worker, '_file_hash', return_value='changed-after-completion'):
            observed = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(observed['state'], 'completed')
        self.assertTrue(observed['evidence_available'])
        self.assertTrue(observed['result_verified'])

    def test_readers_wait_for_atomic_ledger_replacement_without_partial_records(self):
        import threading
        response = self.start(); self.ledger_id = response['execution']['id']
        target_name = self.task['id'] + '-' + self.ledger_id + '.json'
        original = self.tasks.api._publish_no_replace
        for read in (self.tasks.execution_get, self.tasks.get):
            with self.subTest(reader=read.__name__):
                entered, release, finished = threading.Event(), threading.Event(), threading.Event()
                errors = []
                def staged_publication(staged, target):
                    if target.name == target_name:
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError('Test did not release the staged publication')
                    return original(staged, target)
                def write():
                    try:
                        self.update({'updated_epoch': 1.0})
                    except BaseException as error:
                        errors.append(error)
                def observe():
                    try:
                        read(self.root, self.task['id'])
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        finished.set()
                with patch.object(self.tasks.api, '_publish_no_replace', side_effect=staged_publication):
                    writer = threading.Thread(target=write); writer.start()
                    self.assertTrue(entered.wait(3))
                    reader = threading.Thread(target=observe); reader.start()
                    try:
                        self.assertFalse(finished.wait(0.05))
                    finally:
                        release.set(); writer.join(5); reader.join(5)
                self.assertFalse(writer.is_alive()); self.assertFalse(reader.is_alive())
                self.assertEqual(errors, [])

    def test_start_is_persistent_idempotent_and_isolated_read_only(self):
        response = self.start(); execution = response['execution']; self.assertEqual(execution['state'], 'starting')
        self.assertFalse(execution['execution_observed']); self.assertFalse(execution['result_verified'])
        again = self.start(); self.assertTrue(again['unchanged']); self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(again['execution']['id'], execution['id'])
        workspace = self.worker._workspace(execution['id'])
        self.assertNotIn(str(self.root), (workspace / 'input.json').read_text())
        launch = json.loads((workspace / 'launch.json').read_text())
        self.assertIn('read-only', launch['command']); self.assertIn('--ignore-user-config', launch['command'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', launch['command'])
        self.assertFalse(any(p.name == 'state.sqlite3' for p in workspace.rglob('*')))
        self.assertEqual(self.before, self.fixture.authoritative_state())
        self.expect('task_execution_idempotency_conflict', lambda: self.start(timeout_seconds=901))
        self.expect('task_execution_unresolved', lambda: self.start('request-two'))

    def test_actual_stop_lease_gate_runs_before_new_intent(self):
        with patch.object(story.workbench, '_require_dispatch_not_stopping', create=True,
                          side_effect=story.StoryError('workbench_presence_stopping', 'stopping')) as gate:
            self.expect('workbench_presence_stopping', self.start)
        gate.assert_called_once(); self.spawn.assert_not_called()

    def test_wrong_book_and_version_never_spawn(self):
        self.expect('wrong_book', lambda: self.start(expected_book_id='another-book'))
        self.expect('task_version_changed', lambda: self.tasks.execution_start(self.root, self.task['id'], 99, 'request-new'))
        self.spawn.assert_not_called()

    def test_proven_not_started_is_not_an_unknown_exit_or_an_automatic_retry(self):
        self.spawn.side_effect = self.worker.DispatchNotStarted('worker unavailable')
        failed = self.start()
        self.assertEqual(failed['execution']['state'], 'failed')
        self.assertIsNone(failed['execution']['exit_code'])
        self.assertFalse(failed['execution']['execution_observed'])
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.assertTrue(self.start()['unchanged'])
        self.assertEqual(self.spawn.call_count, 1)
        self.spawn.side_effect = None
        again = self.start('explicit-next-request')
        self.assertNotEqual(again['execution']['id'], failed['execution']['id'])
        self.assertEqual(self.spawn.call_count, 2)

    def test_uncertain_launch_error_still_blocks_another_dispatch(self):
        self.spawn.side_effect = OSError('outcome cannot be established')
        failed = self.start()
        self.assertEqual(failed['execution']['state'], 'unknown')
        repeated = self.start()
        self.assertTrue(repeated['unchanged'])
        self.assertEqual(repeated['execution']['id'], failed['execution']['id'])
        self.assertEqual(repeated['execution']['state'], 'unknown')
        self.expect('task_execution_unresolved', lambda: self.start('another-request'))
        self.assertEqual(self.spawn.call_count, 1)

    def test_old_or_unidentified_editor_service_does_not_receive_new_dispatch(self):
        service = self.root / '.story/workbench-service.json'
        service.write_text(json.dumps({'root': str(self.root), 'book_id': self.task['book_id'],
                                       'runtime_sha256': 'old-runtime'}))
        self.expect('task_execution_service_outdated', self.start)
        self.spawn.assert_not_called()
        service.write_text(json.dumps({'root': str(self.root), 'book_id': 'another-book', 'stopped': True}))
        self.expect('task_execution_service_unknown', self.start)
        self.spawn.assert_not_called()

    def test_lost_worker_is_unknown_and_blocks_new_request(self):
        response = self.start(); self.ledger_id = response['execution']['id']
        workspace = self.worker._workspace(self.ledger_id)
        heartbeat = json.loads((workspace / 'heartbeat.json').read_text()); heartbeat['updated_epoch'] = 1
        (workspace / 'heartbeat.json').write_text(json.dumps(heartbeat))
        state = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(state['state'], 'unknown'); self.assertFalse(state['can_start'])
        self.expect('task_execution_unresolved', lambda: self.start('different-request'))
        self.assertTrue(self.tasks.execution_busy(self.root)['busy'])
        reconciled = self.tasks.execution_reconcile(self.root, self.task['id'], state['id'])
        self.assertEqual(reconciled['execution']['state'], 'unknown'); self.assertEqual(self.spawn.call_count, 1)

    def test_cancel_request_does_not_release_dispatch_or_claim_exit(self):
        response = self.start(); execution = response['execution']
        result = self.tasks.execution_cancel(self.root, self.task['id'], execution['id'])
        self.assertEqual(result['execution']['state'], 'cancel_requested')
        again = self.tasks.execution_cancel(self.root, self.task['id'], execution['id'])
        self.assertTrue(again['unchanged'])
        self.expect('task_execution_unresolved', lambda: self.start('request-after-cancel'))
        self.assertTrue(self.tasks.execution_busy(self.root)['busy'])

    def test_cancelled_native_exit_is_required_before_explicit_new_attempt(self):
        response = self.start(); self.tasks.execution_cancel(self.root, self.task['id'], response['execution']['id'])
        done = self.finish(response, state='cancelled', exit_code=-15)
        self.assertEqual(done['execution']['state'], 'cancelled')
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.assertEqual(self.start('explicit-next-attempt')['execution']['state'], 'starting')
        self.assertEqual(self.spawn.call_count, 2)

    def test_completed_review_imports_result_without_formal_adoption(self):
        response = self.start(); done = self.finish(response)
        self.assertEqual(done['execution']['state'], 'completed'); self.assertTrue(done['execution']['result_verified'])
        self.assertEqual(done['task']['status'], 'needs_review'); self.assertEqual(done['task']['execution']['status'], 'unverified')
        self.assertIsNone(done['task']['applied']); self.assertEqual(self.before, self.fixture.authoritative_state())
        again = self.tasks.execution_reconcile(self.root, self.task['id'], response['execution']['id'])
        self.assertTrue(again['unchanged']); self.assertEqual(again['task']['version'], done['task']['version'])

    def test_completed_candidate_is_exclusive_and_bound_to_task_chapter(self):
        with self.tasks._book(self.root) as book:
            record, _ = self.tasks._record(book, self.task['id'])
            refs = record['binding']['sources']
        self.task = self.tasks.create(self.root, 'writing', '写第2章候选，不采用。', refs, chapter=2)['task']
        response = self.start()
        value = {'summary': '按既有范围保存候选，等待作者复查。', 'issues': [],
                 'candidate_text': '第2章 雨夜\n沈禾把唯一的钥匙交给守门人。\n'}
        done = self.finish(response, value=value)
        self.assertEqual(done['execution']['state'], 'completed')
        path = self.root / done['task']['output']['path']; self.assertEqual(path.read_text(), value['candidate_text'])
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_wrong_chapter_or_unfrozen_evidence_keeps_result_for_diagnosis(self):
        with self.tasks._book(self.root) as book:
            record, _ = self.tasks._record(book, self.task['id'])
            refs = record['binding']['sources']
        self.task = self.tasks.create(self.root, 'writing', '写第2章候选，不采用。', refs, chapter=2)['task']
        response = self.start()
        value = {'summary': '候选不应被采用。', 'issues': [], 'candidate_text': '第9章 错误\n测试文字。\n'}
        done = self.finish(response, value=value)
        self.assertEqual(done['execution']['state'], 'result_conflict'); self.assertFalse(done['execution']['result_verified'])
        self.assertIsNone(done['task']['result']); self.assertFalse(self.tasks.execution_busy(self.root)['busy'])

    def test_review_cannot_silently_publish_an_extra_rewrite(self):
        response = self.start()
        value = {'summary': '未经授权的改稿', 'issues': [], 'candidate_text': '第2章 雨夜\n额外改写。\n'}
        before = set((self.root / '.story/drafts').glob('*'))
        done = self.finish(response, value=value)
        self.assertEqual(done['execution']['state'], 'result_conflict')
        self.assertIsNone(done['task']['result'])
        self.assertEqual(before, set((self.root / '.story/drafts').glob('*')))
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def _writing_scope_task(self, *, complete):
        if complete:
            plan = copy.deepcopy(story.TEMPLATES['plan'])
            plan.update(title='门内', volume_dir=self.book.get_plan(2)['volume_dir'], goal='推门进入屋内',
                        beats=[{'choice': '推开门', 'change': '进入屋内'}],
                        stop='停在门内', constraints=[], requires=[], tags=[])
            self.book.save_plan(3, plan, self.book.meta('revision'))
        self.task = self.tasks.create(self.root, 'writing',
            '按计划写第三章完整正文。' if complete else '只写第三章一个片段，不添加完整章头。',
            [], chapter=3, complete_chapter=complete)['task']
        self.before = self.fixture.authoritative_state()

    def _assert_controller_refuses_native_dispatch(self, response):
        execution = response['execution']; ledger = self.ledger(execution['id'])
        with patch.object(self.worker, '_process_identity', return_value=ledger['worker_identity']), \
                patch.object(self.worker.subprocess, 'Popen',
                          side_effect=OSError('regression sentinel: no native child')) as native:
            returned = self.worker.run(self.root, self.task['id'], execution['id'],
                                       ledger['book_id'], ledger['nonce'])
        native.assert_not_called()
        self.assertNotEqual(returned, 0)
        refused = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(refused['state'], 'failed')
        self.assertTrue(self.ledger(execution['id'])['not_started'])
        self.assertIsNone(refused['exit_code'])
        self.assertIsNone(refused['session_id'])
        self.assertFalse(refused['execution_observed'])
        self.assertFalse(refused['result_verified'])
        self.assertTrue(refused['can_start'])
        workspace = self.worker._workspace(execution['id'])
        self.assertFalse((workspace / 'process.json').exists())
        self.assertFalse((workspace / 'trace.jsonl').exists())
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        reconciled = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
        self.assertTrue(reconciled['unchanged'])
        self.assertEqual(reconciled['execution']['state'], 'failed')
        repeated = self.start(execution['request_id'])
        self.assertTrue(repeated['unchanged'])
        self.assertEqual(repeated['execution']['id'], execution['id'])
        self.assertEqual(repeated['execution']['state'], 'failed')
        self.assertEqual(self.spawn.call_count, 1)

    def test_controller_rechecks_frozen_binding_before_native_dispatch(self):
        self._writing_scope_task(complete=True)
        response = self.start('queued-binding-change')
        path = self.root / self.tasks.DIRECTORY / (self.task['id'] + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        record['binding'].update(chapter=4, complete_chapter=False)
        record['binding_sha256'] = story.digest(story.dumps(record['binding']))
        path.write_bytes(story.dumps(record).encode('utf-8'))
        self.assertEqual(record['version'], self.task['version'])
        with self.tasks._book(self.root) as book:
            self.assertEqual(self.tasks._stale(book, record), [])
        self._assert_controller_refuses_native_dispatch(response)
        next_attempt = self.start('explicit-new-binding')
        self.assertNotEqual(next_attempt['execution']['id'], response['execution']['id'])
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_controller_rechecks_frozen_request_before_native_dispatch(self):
        self._writing_scope_task(complete=True)
        response = self.start('queued-request-change')
        path = self.root / self.tasks.DIRECTORY / (self.task['id'] + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        record['prompt'] = '本次请把第三章对话改为更克制的语气。\n' + record['prompt']
        path.write_bytes(story.dumps(record).encode('utf-8'))
        self.assertEqual(record['version'], self.task['version'])
        self.assertEqual(record['binding_sha256'], self.task['binding_sha256'])
        with self.tasks._book(self.root) as book:
            self.assertEqual(self.tasks._stale(book, record), [])
        self._assert_controller_refuses_native_dispatch(response)
        next_attempt = self.start('explicit-new-request')
        self.assertNotEqual(next_attempt['execution']['id'], response['execution']['id'])
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_controller_honors_queued_cancel_before_native_dispatch(self):
        self._writing_scope_task(complete=False)
        response = self.start('queued-then-cancelled')
        cancelled = self.tasks.execution_cancel(self.root, self.task['id'], response['execution']['id'])
        self.assertEqual(cancelled['execution']['state'], 'cancel_requested')
        self._assert_controller_refuses_native_dispatch(response)
        next_attempt = self.start('explicit-after-queued-cancel')
        self.assertNotEqual(next_attempt['execution']['id'], response['execution']['id'])
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_controller_records_known_no_invocation_after_plan_revision_changes(self):
        self._writing_scope_task(complete=True)
        response = self.start('queued-plan-revision')
        plan = copy.deepcopy(self.book.get_plan(3)); plan['stop'] = '修订后停在门内'
        self.book.save_plan(3, plan, self.book.meta('revision'))
        self.before = self.fixture.authoritative_state()
        self._assert_controller_refuses_native_dispatch(response)
        self.expect('stale_snapshot', lambda: self.start('stale-task-still-refused'))
        self.assertEqual(self.spawn.call_count, 1)
        self.task = self.tasks.create(self.root, 'writing', '按当前计划写第三章完整正文。',
                                      [], chapter=3, complete_chapter=True)['task']
        next_attempt = self.start('explicit-current-revision')
        self.assertNotEqual(next_attempt['execution']['id'], response['execution']['id'])
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_controller_cannot_redispatch_signed_not_started_after_request_is_restored(self):
        self._writing_scope_task(complete=False)
        response = self.start('consumed-no-invocation-key')
        path = self.root / self.tasks.DIRECTORY / (self.task['id'] + '.json')
        original = path.read_bytes()
        record = json.loads(original)
        record['prompt'] = '把这个片段的语气改为更克制。\n' + record['prompt']
        path.write_bytes(story.dumps(record).encode('utf-8'))
        self._assert_controller_refuses_native_dispatch(response)
        # Removing the original reason for refusal does not replenish the
        # consumed execution key or authorize another invocation.
        path.write_bytes(original)
        execution = response['execution']; ledger = self.ledger(execution['id'])
        with patch.object(self.worker, '_process_identity', return_value=ledger['worker_identity']), \
                patch.object(self.worker.subprocess, 'Popen',
                             side_effect=OSError('regression sentinel: no native child')) as native:
            returned = self.worker.run(self.root, self.task['id'], execution['id'],
                                       ledger['book_id'], ledger['nonce'])
        native.assert_not_called()
        self.assertNotEqual(returned, 0)
        observed = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(observed['state'], 'failed')
        self.assertTrue(self.ledger(execution['id'])['not_started'])
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_controller_reentry_preserves_a_completed_native_execution(self):
        response = self.start('completed-execution-reentry')
        completed = self.finish(response)
        execution = completed['execution']; ledger = self.ledger(execution['id'])
        with patch.object(self.worker, '_process_identity', return_value=ledger['worker_identity']), \
                patch.object(self.worker.subprocess, 'Popen',
                             side_effect=OSError('regression sentinel: no native child')) as native:
            returned = self.worker.run(self.root, self.task['id'], execution['id'],
                                       ledger['book_id'], ledger['nonce'])
        native.assert_not_called()
        self.assertNotEqual(returned, 0)
        observed = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(observed, execution)
        self.assertTrue(observed['result_verified'])
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_controller_keeps_prior_invocation_signals_unknown_without_redispatch(self):
        ledger_signals = {'child_pid': os.getpid(), 'session_id': 'prior-native-session', 'exit_code': 0,
                          'trace_sha256': '0' * 64, 'result_sha256': '0' * 64, 'finished': self.worker._now()}
        signals = ('trace.jsonl', 'stderr.log', 'process.json', 'final.json', *ledger_signals)
        before = self.fixture.authoritative_state()
        for index, signal in enumerate(signals):
            with self.subTest(signal=signal):
                self.task = self.tasks.create(self.root, 'writing', '只写第三章一个片段。',
                                              [], chapter=3, complete_chapter=False)['task']
                response = self.start('prior-signal-' + str(index))
                execution = response['execution']; ledger = self.ledger(execution['id'])
                workspace = self.worker._workspace(execution['id'])
                if signal in ledger_signals:
                    self.ledger_id = execution['id']
                    self.update({signal: ledger_signals[signal]})
                else:
                    # Even an interrupted reservation is uncertain, not proof
                    # that no native invocation could have occurred.
                    self.worker._write_new(workspace, signal, b'{}' if signal.endswith('.json') else b'')
                self.tasks.execution_cancel(self.root, self.task['id'], execution['id'])
                with patch.object(self.worker, '_process_identity', return_value=ledger['worker_identity']), \
                        patch.object(self.worker.subprocess, 'Popen',
                                     side_effect=OSError('regression sentinel: no native child')) as native:
                    returned = self.worker.run(self.root, self.task['id'], execution['id'],
                                               ledger['book_id'], ledger['nonce'])
                native.assert_not_called()
                self.assertNotEqual(returned, 0)
                current = self.ledger(execution['id'])
                self.assertEqual(current['state'], 'unknown')
                self.assertFalse(current.get('not_started', False))
                self.assertTrue(self.tasks.execution_busy(self.root)['busy'])
                repeated = self.start(execution['request_id'])
                self.assertTrue(repeated['unchanged'])
                self.assertEqual(repeated['execution']['id'], execution['id'])
                self.assertEqual(repeated['execution']['state'], 'unknown')
                self.expect('task_execution_unresolved', lambda: self.start('new-request-' + str(index)))
                self.assertEqual(self.spawn.call_count, index + 1)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_controller_preserves_existing_native_receipt_for_reconciliation(self):
        self._writing_scope_task(complete=False)
        response = self.start('native-exited-before-reentry')
        execution = response['execution']; ledger = self.ledger(execution['id'])
        workspace = self.native_receipt(execution, value={
            'summary': '已返回片段，随后收到取消。', 'issues': [], 'candidate_text': '她停在门边。'})
        evidence = {name: (workspace / name).read_bytes() for name in ('trace.jsonl', 'final.json', 'process.json')}
        self.tasks.execution_cancel(self.root, self.task['id'], execution['id'])
        with patch.object(self.worker, '_process_identity', return_value=ledger['worker_identity']), \
                patch.object(self.worker.subprocess, 'Popen',
                             side_effect=OSError('regression sentinel: no native child')) as native:
            returned = self.worker.run(self.root, self.task['id'], execution['id'],
                                       ledger['book_id'], ledger['nonce'])
        native.assert_not_called()
        self.assertNotEqual(returned, 0)
        self.assertEqual(self.ledger(execution['id'])['state'], 'unknown')
        self.assertFalse(self.ledger(execution['id']).get('not_started', False))
        self.assertEqual({name: (workspace / name).read_bytes() for name in evidence}, evidence)
        self.assertTrue(self.tasks.execution_busy(self.root)['busy'])
        done = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
        self.assertEqual(done['execution']['state'], 'result_conflict')
        self.assertEqual(done['execution']['exit_code'], 0)
        self.assertEqual(done['execution']['session_id'], 'synthetic-native-session')
        self.assertIsNone(done['task']['result'])
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_native_fragment_scope_freezes_and_returns_headingless_text_without_expansion(self):
        self._writing_scope_task(complete=False)
        response = self.start('fragment-scope')
        workspace = self.worker._workspace(response['execution']['id'])
        frozen = json.loads((workspace / 'input.json').read_text(encoding='utf-8'))
        prompt = (workspace / 'prompt.txt').read_text(encoding='utf-8')
        self.assertEqual(frozen['writing_scope'], 'fragment')
        self.assertNotIn('写作任务在candidate_text中交完整指定章正文', prompt)
        self.assertIn('片段', prompt)
        candidate = '她在门边停住，没有把已经收好的钥匙再交出去。'
        done = self.finish(response, value={'summary': '完成请求中的片段，未扩写整章。',
                                          'issues': [], 'candidate_text': candidate})
        self.assertEqual(done['execution']['state'], 'completed')
        self.assertTrue(done['execution']['result_verified'])
        self.assertEqual(done['task']['status'], 'needs_review')
        saved = self.root / done['task']['output']['path']
        self.assertEqual(saved.read_text(encoding='utf-8'), candidate)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_native_complete_scope_still_requires_a_heading_before_saving_candidate(self):
        self._writing_scope_task(complete=True)
        response = self.start('complete-scope')
        workspace = self.worker._workspace(response['execution']['id'])
        frozen = json.loads((workspace / 'input.json').read_text(encoding='utf-8'))
        self.assertEqual(frozen['writing_scope'], 'complete_chapter')
        before = set((self.root / '.story/drafts').glob('*'))
        done = self.finish(response, value={'summary': '返回整章但遗漏章头。', 'issues': [],
                                          'candidate_text': '她推开门。' * 600})
        self.assertEqual(done['execution']['state'], 'result_conflict')
        self.assertIsNone(done['task']['result'])
        self.assertEqual(before, set((self.root / '.story/drafts').glob('*')))
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_native_fragment_with_explicit_wrong_heading_is_rejected_before_saving_candidate(self):
        self._writing_scope_task(complete=False)
        response = self.start('fragment-wrong-chapter')
        before = set((self.root / '.story/drafts').glob('*'))
        done = self.finish(response, value={'summary': '候选包含错误章号。', 'issues': [],
                                          'candidate_text': '第4章 错位\n她在门边停住。'})
        self.assertEqual(done['execution']['state'], 'result_conflict')
        self.assertIsNone(done['task']['result'])
        self.assertEqual(before, set((self.root / '.story/drafts').glob('*')))
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_native_candidate_never_overwrites_conflicting_text_meta_or_pending(self):
        before = self.fixture.authoritative_state()
        for index, suffix in enumerate(('', '.meta.json', '.pending.json')):
            with self.subTest(existing=suffix or 'text'):
                self._writing_scope_task(complete=False)
                response = self.start('candidate-conflict-' + str(index))
                execution = response['execution']
                path = self.root / '.story/drafts/workbench' / (
                    '助手候选-' + self.task['id'] + '-' + execution['id'] + '.md')
                target = Path(str(path) + suffix)
                target.parent.mkdir(parents=True, exist_ok=True)
                original = '作者已有材料，不能被助手回流覆盖。'.encode('utf-8')
                target.write_bytes(original)
                done = self.finish(response, value={
                    'summary': '返回指定片段。', 'issues': [], 'candidate_text': '她在门边停下。'})
                self.assertEqual(done['execution']['state'], 'result_conflict')
                self.assertFalse(done['execution']['result_verified'])
                self.assertIsNone(done['task']['result'])
                self.assertEqual(target.read_bytes(), original)
                self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
                repeated = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
                self.assertEqual(repeated['execution']['state'], 'result_conflict')
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(self.spawn.call_count, index + 1)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_native_candidate_publication_resumes_identical_interrupted_save(self):
        before = self.fixture.authoritative_state()
        for index, interrupted_at in enumerate(('meta', 'pending_cleanup')):
            with self.subTest(interrupted_at=interrupted_at):
                self._writing_scope_task(complete=False)
                response = self.start('candidate-interrupted-' + str(index))
                execution = response['execution']
                path = self.root / '.story/drafts/workbench' / (
                    '助手候选-' + self.task['id'] + '-' + execution['id'] + '.md')
                candidate = '她在门边停下，没有再把钥匙交出去。'
                value = {'summary': '返回指定片段。', 'issues': [], 'candidate_text': candidate}
                if interrupted_at == 'meta':
                    original = self.worker._candidate_file
                    def interrupted_meta(book, name, raw):
                        if name.endswith('.meta.json'):
                            raise OSError('synthetic metadata publication interruption')
                        return original(book, name, raw)
                    with patch.object(self.worker, '_candidate_file', side_effect=interrupted_meta):
                        interrupted = self.finish(response, value=value)
                    self.assertFalse(Path(str(path) + '.meta.json').exists())
                else:
                    with patch.object(story.workbench, '_finish_pending_save',
                                      side_effect=OSError('synthetic pending cleanup interruption')):
                        interrupted = self.finish(response, value=value)
                    self.assertTrue(Path(str(path) + '.meta.json').exists())
                self.assertEqual(interrupted['execution']['state'], 'result_conflict')
                self.assertIsNone(interrupted['task']['result'])
                self.assertEqual(path.read_text(encoding='utf-8'), candidate)
                self.assertTrue(Path(str(path) + '.pending.json').exists())
                done = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
                self.assertEqual(done['execution']['state'], 'completed')
                self.assertTrue(done['execution']['result_verified'])
                self.assertEqual((self.root / done['task']['output']['path']).read_text(encoding='utf-8'), candidate)
                meta = json.loads(Path(str(path) + '.meta.json').read_text(encoding='utf-8'))
                self.assertEqual(meta['chapter'], 3)
                self.assertEqual(meta['writing_scope'], 'fragment')
                self.assertFalse(meta['adopted'])
                self.assertFalse(Path(str(path) + '.pending.json').exists())
                self.assertEqual(self.spawn.call_count, index + 1)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_native_task_attachment_recovers_without_duplicate_task_or_dispatch(self):
        before = self.fixture.authoritative_state()
        for index, interrupted_at in enumerate(('task_save', 'publication')):
            with self.subTest(interrupted_at=interrupted_at):
                self._writing_scope_task(complete=False)
                response = self.start('task-attachment-interrupted-' + str(index))
                execution = response['execution']; original_version = self.task['version']
                candidate = '她收回手，终于听见门后的应答。'
                value = {'summary': '已返回指定片段。', 'issues': [], 'candidate_text': candidate}
                if interrupted_at == 'task_save':
                    with patch.object(self.tasks, '_save', side_effect=OSError('synthetic task attachment failure')):
                        interrupted = self.finish(response, value=value)
                    self.assertIsNone(interrupted['task']['result'])
                    self.assertEqual(interrupted['task']['version'], original_version)
                else:
                    original_write = self.worker._write_new
                    def interrupted_publication(workspace, name, raw):
                        if name == 'publication.json':
                            raise OSError('synthetic final publication receipt failure')
                        return original_write(workspace, name, raw)
                    with patch.object(self.worker, '_write_new', side_effect=interrupted_publication):
                        interrupted = self.finish(response, value=value)
                    self.assertIsNotNone(interrupted['task']['result'])
                    self.assertEqual(interrupted['task']['version'], original_version + 1)
                self.assertEqual(interrupted['execution']['state'], 'result_conflict')
                workspace = self.worker._workspace(execution['id'])
                self.assertTrue((workspace / 'attachment.json').exists())
                self.assertFalse((workspace / 'publication.json').exists())
                drafts = set((self.root / '.story/drafts/workbench').glob('助手候选-*.md'))
                with patch.object(self.worker, '_now', return_value='2099-01-01T00:00:00Z'):
                    done = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
                self.assertEqual(done['execution']['state'], 'completed')
                self.assertTrue(done['execution']['result_verified'])
                self.assertEqual(done['task']['version'], original_version + 1)
                self.assertEqual((self.root / done['task']['output']['path']).read_text(encoding='utf-8'), candidate)
                self.assertEqual(set((self.root / '.story/drafts/workbench').glob('助手候选-*.md')), drafts)
                self.assertTrue((workspace / 'publication.json').exists())
                repeated = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
                self.assertTrue(repeated['unchanged'])
                self.assertEqual(repeated['task']['version'], original_version + 1)
                self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
                self.assertEqual(self.spawn.call_count, index + 1)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_native_attachment_checkpoint_cannot_authorize_changed_task_or_result(self):
        before = self.fixture.authoritative_state()
        for index, changed in enumerate(('prompt', 'result')):
            with self.subTest(changed=changed):
                self._writing_scope_task(complete=False)
                response = self.start('task-attachment-changed-' + str(index))
                execution = response['execution']
                original_write = self.worker._write_new
                def interrupted_publication(workspace, name, raw):
                    if name == 'publication.json':
                        raise OSError('synthetic final publication receipt failure')
                    return original_write(workspace, name, raw)
                with patch.object(self.worker, '_write_new', side_effect=interrupted_publication):
                    interrupted = self.finish(response, value={
                        'summary': '已返回指定片段。', 'issues': [], 'candidate_text': '她听见门后的应答。'})
                self.assertEqual(interrupted['execution']['state'], 'result_conflict')
                self.assertIsNotNone(interrupted['task']['result'])
                path = self.root / self.tasks.DIRECTORY / (self.task['id'] + '.json')
                record = json.loads(path.read_text(encoding='utf-8'))
                if changed == 'prompt':
                    record['prompt'] = '作者另补的要求。\n' + record['prompt']
                else:
                    record['result']['summary'] = '作者已修改此任务的结果摘要。'
                self.assertEqual(record['version'], response['task']['version'] + 1)
                path.write_bytes(story.dumps(record).encode('utf-8'))
                changed_raw = path.read_bytes()
                done = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
                self.assertEqual(done['execution']['state'], 'result_conflict')
                self.assertFalse(done['execution']['result_verified'])
                self.assertEqual(path.read_bytes(), changed_raw)
                self.assertFalse((self.worker._workspace(execution['id']) / 'publication.json').exists())
                self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
                self.assertEqual(self.spawn.call_count, index + 1)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_malformed_native_finished_never_mutates_task_or_publishes_candidate(self):
        before = self.fixture.authoritative_state()
        malformed = (('missing', None), ('null', None), ('true', True), ('false', False),
                     ('empty', ''), ('too_long', 'x' * 81), ('object', {}), ('array', []), ('number', 1))
        for index, (label, finished) in enumerate(malformed):
            with self.subTest(finished=label):
                self._writing_scope_task(complete=False)
                response = self.start('malformed-finished-' + str(index))
                execution = response['execution']
                task_path = self.root / self.tasks.DIRECTORY / (self.task['id'] + '.json')
                original_task = task_path.read_bytes()
                workspace = self.native_receipt(execution, value={
                    'summary': '返回指定片段。', 'issues': [], 'candidate_text': '她轻轻敲了两下门。'})
                receipt_path = workspace / 'process.json'
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
                if label == 'missing':
                    receipt.pop('finished')
                else:
                    receipt['finished'] = finished
                receipt_path.write_bytes(story.dumps(receipt).encode('utf-8'))
                result = self.tasks.execution_reconcile(self.root, self.task['id'], execution['id'])
                self.assertEqual(result['execution']['state'], 'unknown')
                self.assertFalse(result['execution']['result_verified'])
                self.assertIsNone(result['task']['result'])
                self.assertEqual(task_path.read_bytes(), original_task)
                readable = self.tasks.get(self.root, self.task['id'])
                self.assertTrue(readable['ok'])
                self.assertEqual(readable['task']['id'], self.task['id'])
                self.assertEqual(readable['task']['version'], self.task['version'])
                self.assertIsNone(readable['task']['result'])
                self.assertFalse((workspace / 'attachment.json').exists())
                self.assertFalse((workspace / 'publication.json').exists())
                candidate = self.root / '.story/drafts/workbench' / (
                    '助手候选-' + self.task['id'] + '-' + execution['id'] + '.md')
                self.assertFalse(candidate.exists())
                self.assertFalse(Path(str(candidate) + '.meta.json').exists())
                self.assertFalse(Path(str(candidate) + '.pending.json').exists())
                self.assertTrue(self.tasks.execution_busy(self.root)['busy'])
                self.assertEqual(self.spawn.call_count, index + 1)
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_rebound_task_cannot_attach_a_frozen_host_result_to_a_different_scope(self):
        self._writing_scope_task(complete=True)
        response = self.start('original-complete-scope')
        path = self.root / self.tasks.DIRECTORY / (self.task['id'] + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        old_binding = record['binding_sha256']
        record['binding'].update(chapter=4, complete_chapter=False)
        record['binding_sha256'] = story.digest(story.dumps(record['binding']))
        self.assertNotEqual(old_binding, record['binding_sha256'])
        # A public task file must not redirect the already-frozen invocation,
        # even if its self-consistent hash and unchanged version look valid.
        path.write_bytes(story.dumps(record).encode('utf-8'))
        before = set((self.root / '.story/drafts').glob('*'))
        done = self.finish(response, value={'summary': '返回重绑后范围的候选。', 'issues': [],
                                          'candidate_text': '第4章 门内\n' + '她推开门。' * 600})
        self.assertEqual(done['execution']['state'], 'result_conflict')
        self.assertFalse(done['execution']['result_verified'])
        self.assertIsNone(done['task']['result'])
        self.assertEqual(before, set((self.root / '.story/drafts').glob('*')))
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        unchanged = self.start('original-complete-scope')
        self.assertTrue(unchanged['unchanged'])
        self.assertEqual(unchanged['execution']['id'], response['execution']['id'])
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_legacy_prompt_scope_change_cannot_relabel_a_frozen_host_result(self):
        from outline_fixture import bind_adopted_outline
        self._writing_scope_task(complete=True)
        bind_adopted_outline(story, self.book, 3)
        self.task = story.workbench._editor_writing_task(self.root)['task']
        path = self.root / self.tasks.DIRECTORY / (self.task['id'] + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertIs(record['binding'].pop('complete_chapter'), True)
        old_binding = record['binding_sha256']
        record['binding_sha256'] = story.digest(story.dumps(record['binding']))
        record['prompt'] = record['prompt'].replace(old_binding, record['binding_sha256'])
        path.write_bytes(story.dumps(record).encode('utf-8'))
        self.before = self.fixture.authoritative_state()
        response = self.start('legacy-frozen-request')
        # Old tasks derive their complete scope from their original brief.
        # Prompt edits do not change binding_sha256 and therefore need a check
        # against the private frozen request as well as the binding fingerprint.
        record['prompt'] = '只写第3章一小段片段，不写完整章。\n' + record['prompt']
        path.write_bytes(story.dumps(record).encode('utf-8'))
        before = set((self.root / '.story/drafts').glob('*'))
        done = self.finish(response, value={'summary': '返回冻结时请求的完整第三章。', 'issues': [],
                                          'candidate_text': '第3章 门内\n' + '她推开门。' * 600})
        self.assertEqual(done['execution']['state'], 'result_conflict')
        self.assertFalse(done['execution']['result_verified'])
        self.assertIsNone(done['task']['result'])
        self.assertTrue(done['task']['prompt'].startswith('只写第3章一小段片段'))
        self.assertEqual(before, set((self.root / '.story/drafts').glob('*')))
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_native_completed_pending_result_survives_same_machine_book_move(self):
        import shutil
        from outline_fixture import bind_adopted_outline
        self._writing_scope_task(complete=True)
        bind_adopted_outline(story, self.book, 3)
        self.task = story.workbench._editor_writing_task(self.root)['task']
        self.before = self.fixture.authoritative_state()
        self.assertIn(str(self.root), self.task['prompt'])
        response = self.start('restore-pending-native-result')
        candidate = '第3章 门内\n' + '她推开门。' * 600
        workspace = self.native_receipt(response['execution'], value={
            'summary': '完成冻结时请求的第三章候选。', 'issues': [], 'candidate_text': candidate})
        launch = json.loads((workspace / 'launch.json').read_text(encoding='utf-8'))
        self.assertIn('task_request_sha256', launch)
        restored = self.root.parent / 'restored-pending-book'
        shutil.copytree(self.root, restored)
        authoritative = ['.story/state.sqlite3', '.story/publishing.sqlite3'] + [
            self.book.chapter_path(chapter) for chapter in (1, 2)]
        before = {name: (restored / name).read_bytes() for name in authoritative}
        record_path = restored / self.tasks.DIRECTORY / (self.task['id'] + '.json')
        restored_record = json.loads(record_path.read_text(encoding='utf-8'))
        frozen = json.loads((workspace / 'input.json').read_text(encoding='utf-8'))
        with self.tasks._book(restored) as book:
            self.assertEqual(self.tasks._stale(book, restored_record), [])
            self.assertNotEqual(frozen['request'], self.worker._request_text(book, restored_record))
        done = self.tasks.execution_reconcile(restored, self.task['id'], response['execution']['id'])
        self.assertEqual(done['execution']['state'], 'completed')
        self.assertTrue(done['execution']['result_verified'])
        self.assertEqual(done['task']['status'], 'needs_review')
        self.assertEqual(done['task']['book_id'], self.book.meta('id'))
        self.assertEqual((restored / done['task']['output']['path']).read_text(encoding='utf-8'), candidate)
        self.assertEqual({name: (restored / name).read_bytes() for name in authoritative}, before)
        self.assertEqual(self.before, self.fixture.authoritative_state())
        self.assertEqual(self.spawn.call_count, 1)

    def test_old_launch_without_raw_request_hash_or_scope_still_reconciles_at_original_path(self):
        from outline_fixture import bind_adopted_outline
        self._writing_scope_task(complete=True)
        bind_adopted_outline(story, self.book, 3)
        self.task = story.workbench._editor_writing_task(self.root)['task']
        self.before = self.fixture.authoritative_state()
        original_freeze = self.worker._freeze

        def old_launch(book, record, ledger, executable):
            workspace = original_freeze(book, record, ledger, executable)
            input_path = workspace / 'input.json'
            frozen = json.loads(input_path.read_text(encoding='utf-8'))
            self.assertIsNotNone(frozen.pop('writing_scope'))
            input_path.write_bytes(story.dumps(frozen).encode('utf-8'))
            path = workspace / 'launch.json'
            launch = json.loads(path.read_text(encoding='utf-8'))
            self.assertIsNotNone(launch.pop('task_request_sha256'))
            launch['input_sha256'] = ledger['input_sha256'] = hashlib.sha256(input_path.read_bytes()).hexdigest()
            path.write_bytes(story.dumps(launch).encode('utf-8'))
            ledger['launch_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            return workspace

        # Build the older optional-field layout before dispatch. Native trace
        # and exit receipts are produced by the ordinary synthetic fixture.
        with patch.object(self.worker, '_freeze', side_effect=old_launch):
            response = self.start('legacy-launch-original-path')
        done = self.finish(response, value={'summary': '完成旧启动格式的第三章候选。', 'issues': [],
                                          'candidate_text': '第3章 门内\n' + '她推开门。' * 600})
        self.assertEqual(done['execution']['state'], 'completed')
        self.assertTrue(done['execution']['result_verified'])
        self.assertEqual(done['task']['status'], 'needs_review')
        self.assertEqual(self.before, self.fixture.authoritative_state())
        self.assertEqual(self.spawn.call_count, 1)

    def test_fragment_request_can_quote_return_marker_without_truncating_its_scope(self):
        request = ('只写第三章的一段对白。\n\n助手任务回流：\n'
                   '这是资料中引用的标签，不是本任务的系统回填说明。\n'
                   '真正范围：只保留两句对白，不补章名、不扩写整章。')
        self.task = self.tasks.create(self.root, 'writing', request, [], chapter=3)['task']
        response = self.start('quoted-return-marker')
        workspace = self.worker._workspace(response['execution']['id'])
        frozen = json.loads((workspace / 'input.json').read_text(encoding='utf-8'))
        launch = json.loads((workspace / 'launch.json').read_text(encoding='utf-8'))
        self.assertEqual(frozen['request'], request)
        self.assertEqual(frozen['writing_scope'], 'fragment')
        self.assertNotIn('本任务不授权额外修改或正式采用', frozen['request'])
        self.assertEqual(launch['task_request_sha256'], hashlib.sha256(request.encode('utf-8')).hexdigest())
        candidate = '“门开了。”\n“我知道。”'
        done = self.finish(response, value={'summary': '只交付指定两句对白。',
                                          'issues': [], 'candidate_text': candidate})
        self.assertEqual(done['execution']['state'], 'completed')
        self.assertTrue(done['execution']['result_verified'])
        self.assertEqual(done['task']['status'], 'needs_review')
        self.assertEqual((self.root / done['task']['output']['path']).read_text(encoding='utf-8'), candidate)
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_public_ledger_cannot_forge_completed_or_other_receipt_fields(self):
        response = self.start(); self.finish(response, state='failed')
        self.update({'state': 'completed', 'result_verified': True})
        self.assertEqual(self.tasks.execution_get(self.root, self.task['id'])['execution']['state'], 'unknown')
        self.assertTrue(self.tasks.execution_busy(self.root)['busy'])

    def test_changed_native_trace_or_input_removes_verified_status(self):
        response = self.start(); done = self.finish(response)
        workspace = self.worker._workspace(response['execution']['id'])
        (workspace / 'trace.jsonl').write_text('{}\n')
        observed = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(observed['state'], 'completed'); self.assertFalse(observed['evidence_available']); self.assertFalse(observed['result_verified'])
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.expect('task_version_changed', lambda: self.start('not-safe-to-repeat'))

    def test_stale_task_and_cancelled_late_result_do_not_revive_task(self):
        response = self.start()
        self.tasks.mark_handoff(self.root, self.task['id'], self.task['version'])
        done = self.finish(response)
        self.assertEqual(done['execution']['state'], 'result_conflict'); self.assertIsNone(done['task']['result'])
        self.assertEqual(self.before, self.fixture.authoritative_state())

    def test_missing_registered_ledger_is_unknown_not_idle(self):
        response = self.start(); ident = response['execution']['id']
        (self.root / self.tasks.DIRECTORY / 'executions' / (self.task['id'] + '-' + ident + '.json')).unlink()
        state = self.tasks.execution_busy(self.root)
        self.assertTrue(state['busy']); self.assertEqual(state['unknown'], 1)

    def test_missing_head_cannot_hide_active_attempt_or_permit_duplicate_dispatch(self):
        response = self.start(); ident = response['execution']['id']
        (self.root / self.tasks.DIRECTORY / 'execution-heads' / (self.task['id'] + '.json')).unlink()
        self.assertTrue(self.tasks.execution_busy(self.root)['busy'])
        self.expect('task_execution_unresolved', lambda: self.start('new-request-after-head-loss'))
        self.assertEqual(self.spawn.call_count, 1)
        self.native_receipt(response['execution'])
        reconciled = self.tasks.execution_reconcile(self.root, self.task['id'], ident)
        self.assertEqual(reconciled['execution']['state'], 'result_conflict')
        self.assertIsNone(reconciled['task']['result'])
        self.assertFalse(self.tasks.execution_busy(self.root)['busy'])

    def test_caller_supplied_json_still_does_not_certify_host_execution(self):
        result = {**self.task['result_template'], 'summary': '完成手动回填，仅作为候选意见。'}
        value = self.tasks.import_result(self.root, self.task['id'], self.task['version'], result)
        self.assertEqual(value['task']['execution']['status'], 'unverified')
        self.assertIsNone(value['task']['host_execution'])

    @unittest.skipUnless(os.name != 'nt', 'POSIX process fork for synthetic concurrent controller')
    def test_two_real_processes_share_one_start_intent(self):
        import multiprocessing
        context = multiprocessing.get_context('fork')
        queue = context.Queue(); marker = self.root.parent / 'dispatch-calls.txt'
        parent_pid = os.getpid()
        def spawn(*_):
            with marker.open('ab') as stream:
                stream.write(b'launched\n')
            return type('Process', (), {'pid': parent_pid})()
        def invoke():
            try:
                with patch.object(self.worker, '_spawn', side_effect=spawn):
                    value = self.start()
                queue.put({'id': value['execution']['id']})
            except BaseException as error:
                queue.put({'error': str(error)})
        children = [context.Process(target=invoke) for _ in range(2)]
        for child in children: child.start()
        for child in children:
            child.join(12)
            if child.is_alive(): child.terminate(); child.join(3)
            self.assertEqual(child.exitcode, 0)
        rows = [queue.get(timeout=3) for _ in children]
        self.assertTrue(all('id' in row for row in rows), rows)
        self.assertEqual(rows[0]['id'], rows[1]['id'])
        self.assertEqual(marker.read_bytes(), b'launched\n')

    def test_model_defaults_inherit_only_selected_local_scalars(self):
        import tempfile
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'config.toml'
            path.write_text('model = "configured-model"\nmodel_reasoning_effort = "medium"\n[mcp_servers.private]\ncommand = "must-not-inherit"\n')
            with patch.dict(os.environ, {'CODEX_HOME': area}):
                selected = REAL_MODEL_DEFAULTS()
            self.assertEqual(selected, {'model': 'configured-model', 'model_reasoning_effort': 'medium'})

    def test_public_running_claim_requires_private_live_controller_and_native_session(self):
        response = self.start(); self.ledger_id = response['execution']['id']
        self.update({'state': 'running', 'session_id': 'made-up-session', 'updated_epoch': 99999999999})
        observed = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertNotEqual(observed['state'], 'running')
        self.assertFalse(observed['execution_observed'])
        workspace = self.worker._workspace(self.ledger_id)
        heartbeat = json.loads((workspace / 'heartbeat.json').read_text()); heartbeat['worker_identity'] = 'other-process'
        (workspace / 'heartbeat.json').write_text(json.dumps(heartbeat))
        self.assertEqual(self.tasks.execution_get(self.root, self.task['id'])['execution']['state'], 'unknown')

    def test_completed_lifecycle_survives_workspace_loss_and_same_host_restore(self):
        import shutil
        response = self.start(); self.finish(response)
        workspace = self.worker._workspace(response['execution']['id']); shutil.rmtree(workspace)
        observed = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(observed['state'], 'completed'); self.assertFalse(observed['evidence_available'])
        self.assertFalse(observed['result_verified']); self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        restored = self.root.parent / 'restored-book'; shutil.copytree(self.root, restored)
        view = self.tasks.execution_get(restored, self.task['id'])['execution']
        self.assertEqual(view['state'], 'completed'); self.assertFalse(self.tasks.execution_busy(restored)['busy'])

    def test_completed_lifecycle_cannot_be_forged_by_editing_public_files(self):
        response = self.start(); self.finish(response)
        path = self.root / self.tasks.DIRECTORY / 'executions' / (self.task['id'] + '-' + response['execution']['id'] + '.lifecycle.json')
        value = json.loads(path.read_text()); value['payload']['exit_code'] = 999
        path.write_text(json.dumps(value))
        # Private full evidence still proves the original outcome; the forged
        # summary never creates a different certified exit.
        observed = self.tasks.execution_get(self.root, self.task['id'])['execution']
        self.assertEqual(observed['exit_code'], 0)
        self.assertEqual(observed['state'], 'completed')
        import shutil
        shutil.rmtree(self.worker._workspace(response['execution']['id']))
        self.assertEqual(self.tasks.execution_get(self.root, self.task['id'])['execution']['state'], 'unknown')
        self.assertTrue(self.tasks.execution_busy(self.root)['busy'])

    def test_confirmed_exit_with_stale_result_is_not_runtime_busy(self):
        response = self.start()
        self.tasks.mark_handoff(self.root, self.task['id'], self.task['version'])
        done = self.finish(response)
        self.assertEqual(done['execution']['state'], 'result_conflict')
        self.assertFalse(done['execution']['can_start']); self.assertFalse(self.tasks.execution_busy(self.root)['busy'])
        self.expect('task_execution_unresolved', lambda: self.start('new-dispatch-must-not-happen'))

    def test_native_issue_references_only_frozen_sources(self):
        response = self.start()
        value = {'summary': '检查可定位引用。', 'candidate_text': None, 'issues': [{'id': 'key', 'severity': 'advice',
                 'issue': '核对钥匙交接。', 'evidence': [{'source_id': 'source-0', 'quote': '沈禾把唯一的钥匙交给守门人。', 'line': 2}]}]}
        done = self.finish(response, value=value)
        self.assertEqual(done['execution']['state'], 'completed')
        self.assertEqual(done['task']['issues'][0]['evidence'][0]['chapter'], 2)


if __name__ == '__main__':
    unittest.main()
