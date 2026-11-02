"""Synthetic local bridge lifecycle; no model, SDK, browser or platform call."""
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures
from tests.test_platform_browser_preview import configuration

story = fixtures.story
worker = story.chrome_worker
bridge = story.platform_chrome_bridge
drafts = story.platform_drafts


class AssistantSessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book, self.root = self.fixture.book, self.fixture.root
        self.plan = self.fixture.prepare()
        profiles = self.root / drafts.PROFILE_DIRECTORY
        profiles.mkdir()
        self.profile = profiles / 'host.json'
        self.profile.write_text(json.dumps({**configuration(), 'execution_mode': 'host_assisted_cua'}))
        self.binding = bridge._binding(self.book, self.plan['id'], self.profile.name)[0]
        self.context = self.root / 'private-context'
        self.context.mkdir(mode=0o700)
        self.sdk = self.root / 'mock-sdk.mjs'
        self.sdk.write_text('// Never imported or executed in this suite.\n')
        for target, kwargs in [('_sdk_path', {'return_value': self.sdk}),
                               ('_private_context', {'return_value': self.context}),
                               ('_codex_executable', {'side_effect': AssertionError('No model launch')} )]:
            item = patch.object(worker, target, **kwargs)
            item.start()
            self.addCleanup(item.stop)
        item = patch.object(worker.subprocess, 'Popen', side_effect=AssertionError('No subprocess launch'))
        item.start()
        self.addCleanup(item.stop)
        self.packet = None

    def connect(self, packet, *, instance=None):
        self.packet = packet
        config = json.loads(Path(packet['config_path']).read_text())
        self.handle = Path(config['handle_path'])
        bridge.attach(self.book, self.plan['id'], self.profile.name, self.handle)
        challenge = bridge.health(self.book, self.handle)['challenge']
        response = {key: challenge[key] for key in ('schema', 'session_id', 'binding_sha256', 'nonce')}
        response.update(browser={'kind': 'chrome', 'id': 'synthetic-chrome'},
            capabilities={'list_tabs': True, 'get_tab': True, 'dom_snapshot': True},
            worker={'pid': os.getpid(), 'instance': instance or packet['controller_instance']})
        path = self.context / 'health-response.json'
        bridge._external(path, create_value=response)
        bridge.health(self.book, self.handle, path)

    def detach(self):
        bridge.heartbeat(self.book, self.handle, detach=True)

    def final_receipt(self, **changes):
        config = json.loads((self.context / 'config.json').read_text())
        receipt = {'schema':1, 'worker':config['worker'], 'binding_sha256':config['binding_sha256'], 'attached':True,
            'session_id':json.loads(self.handle.read_text())['session_id'], 'done':True,
            'bridge_detached':True, 'cleanup_complete':True, 'state':'stopped',
            'owned_tabs_remaining':0, 'completed_at_ms':int(time.time()*1000), **changes}
        with bridge._pinned(self.context) as directory:
            bridge._write(directory, 'worker-final.json', receipt, replace=True)
        return receipt

    def test_final_receipt_and_detached_bound_session_prove_cleanup_readonly(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0) as session:
            self.detach()
            self.final_receipt()
            before = {str(p):p.read_bytes() for p in self.context.iterdir() if p.is_file()}
            self.assertTrue(worker.assistant_cleanup_status(self.root, self.binding, self.packet)['cleanup_complete'])
            self.assertEqual(before, {str(p):p.read_bytes() for p in self.context.iterdir() if p.is_file()})
        self.assertTrue(session.worker_done)
        self.assertTrue(session.cleanup_complete)
        self.assertEqual(session.cleanup_status, 'complete')

    def test_detached_lease_or_final_without_detach_is_not_cleanup_proof(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0) as session:
            self.final_receipt()
            self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding, self.packet)['cleanup_complete'])
            (self.context / 'worker-final.json').unlink()
            self.detach()
            result = worker.assistant_cleanup_status(self.root, self.binding, self.packet)
            self.assertTrue(result['bridge_detached'])
            self.assertFalse(result['worker_done'])
            self.assertFalse(result['cleanup_complete'])
        self.assertTrue(session.bridge_detached)
        self.assertFalse(session.cleanup_complete)

    def test_foreign_malformed_incomplete_or_old_final_receipt_never_unlocks(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0):
            self.detach()
            for change in ({'worker':{'pid':os.getpid(), 'instance':'f'*32}},
                           {'session_id':'f'*32}, {'binding_sha256':'f'*64}, {'done':False},
                           {'cleanup_complete':False}, {'owned_tabs_remaining':1},
                           {'completed_at_ms':0}, {'completed_at_ms':int(time.time()*1000)+60000},
                           {'done':1}, {'unexpected':True}):
                with self.subTest(change=change):
                    self.final_receipt(**change)
                    self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding, self.packet)['cleanup_complete'])

    def test_late_valid_receipt_confirms_cleanup_without_restarting_any_worker(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0) as session:
            pass
        self.assertFalse(session.cleanup_complete)
        self.detach()
        self.final_receipt()
        self.assertTrue(worker.assistant_cleanup_status(self.root, self.binding, self.packet)['cleanup_complete'])
        self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding,
            {**self.packet, 'controller_pid':os.getpid()+1})['cleanup_complete'])

    def test_symlink_final_receipt_is_never_trusted(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0):
            self.detach()
            self.final_receipt()
            path = self.context / 'worker-final.json'
            other = self.context / 'copied-final.json'
            path.rename(other)
            path.symlink_to(other)
            self.assertFalse(worker.assistant_cleanup_status(self.root, self.binding, self.packet)['cleanup_complete'])

    def test_bootstrap_final_proves_cleanup_without_claiming_attachment_or_detach(self):
        def finished(packet):
            config = json.loads((self.context / 'config.json').read_text())
            bridge._external(self.context / 'worker-final.json', create_value={
                'schema':1, 'worker':config['worker'], 'binding_sha256':config['binding_sha256'],
                'session_id':None, 'attached':False, 'done':True, 'bridge_detached':False,
                'cleanup_complete':True, 'state':'bootstrap_failed', 'owned_tabs_remaining':0,
                'completed_at_ms':int(time.time()*1000)})
            result = worker.assistant_cleanup_status(self.root, self.binding, packet)
            self.assertTrue(result['worker_done'])
            self.assertTrue(result['cleanup_complete'])
            self.assertFalse(result['bridge_detached'])
        with self.assertRaises(worker.WorkerError):
            with worker.assistant_session(self.root, self.binding, on_context=finished,
                    connect_seconds=.01, finish_seconds=0):
                self.fail('No bridge established')

    def prepare_only(self):
        """Form the queue controller's own files; never attach or launch a host."""
        session = worker.AssistantBridgeSession(story, self.root, self.binding, execution_host='codex')
        session.prepare()
        self.packet = session.packet()
        self.handle = session.handle_path
        return session

    def never_started(self, packet=None):
        with patch.object(bridge, '_owner_alive', return_value=False):
            return worker.assistant_session_never_started(self.root, self.binding,
                                                          packet if packet is not None else self.packet)

    def test_a_queue_killed_before_its_host_ran_proves_nothing_started(self):
        self.prepare_only()
        self.assertTrue(self.never_started())
        before = {p.name: p.read_bytes() for p in self.context.iterdir()}
        self.assertTrue(self.never_started())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.context.iterdir()})
        # The controller's own cooperative stop marker is still not a host artifact.
        bridge._external(self.context / 'stop.json', create_value={'schema': 1, 'requested_at_ms': int(time.time() * 1000)})
        self.assertTrue(self.never_started())
        # A live queue controller may still be about to hand off to its host.
        self.assertFalse(worker.assistant_session_never_started(self.root, self.binding, self.packet))

    def test_any_host_artifact_or_attachment_or_changed_binding_denies_the_never_started_proof(self):
        self.prepare_only()
        for name in ('worker-driver-error.json', 'bootstrap-state.json', 'worker-progress-1.json',
                     'outbox-' + 'd' * 32 + '.json', 'operator-evidence.json'):
            with self.subTest(artifact=name):
                path = self.context / name
                bridge._external(path, create_value={'schema': 1})
                self.assertFalse(self.never_started())
                path.unlink()
        # A well-formed handle means the host session began.
        bridge._external(self.handle, create_value={'book_root': str(self.root), 'book_id': self.binding['book_id'],
            'binding_sha256': bridge._digest(self.binding), 'session_id': 'd' * 32, 'token': 'synthetic-token'})
        self.assertFalse(self.never_started())
        self.handle.unlink()
        with patch.object(bridge, 'worker_status', return_value={'attached': True}):
            self.assertFalse(self.never_started())
        for change in ({'controller_instance': 'f' * 32}, {'config_path': str(self.sdk)},
                       {'execution_origin': 'other'}):
            with self.subTest(packet=change):
                self.assertFalse(self.never_started({**self.packet, **change}))
        self.assertFalse(worker.assistant_session_never_started(self.root, {**self.binding, 'book_id': 'other'}, self.packet))

    def recovery_evidence(self, tool='mcp__node_repl__js_reset'):
        now = time.time()
        value = {'schema':1, 'request_id':'a'*32, 'config_path':self.packet['config_path'],
            'controller_instance':self.packet['controller_instance'],
            'session_id':json.loads(self.handle.read_text())['session_id'], 'binding_sha256':'b'*64,
            'operator':'confirmed_terra', 'chrome_quit':{'old_processes':[{'pid':2147483647,'start_identity':'recorded-before-normal-quit'}],
                'observed_none_at':now}, 'official_kernel_reset':{'tool':tool,'completed_at':now},
            'authorized_by_user':{'text':'Synthetic explicit save, normal Chrome quit and official kernel reset confirmation',
                                  'confirmed_at':now}}
        path = self.context / 'operator-evidence.json'
        bridge._external(path, create_value=value)
        return path

    def retire(self, evidence):
        return worker.assistant_retire_readonly_session(self.root, self.binding, self.packet,
            request_id='a'*32, request_binding_sha256='b'*64, evidence_path=evidence)

    def retirement_status(self):
        return worker.assistant_retirement_status(self.root, self.binding, self.packet,
            request_id='a'*32, request_binding_sha256='b'*64)

    def queue_record(self, *, operation='find_drafts', deadline=None):
        sid = json.loads(self.handle.read_text())['session_id']
        record = {'schema':1,'id':'d'*32,'session_id':sid,'binding':self.binding,
            'binding_sha256':bridge._digest(self.binding),'operation':operation,'owner_pid':os.getpid(),
            'deadline_epoch':time.time()-1 if deadline is None else deadline}
        with bridge._directory(self.root,sid) as directory:
            bridge._write(directory,'claimed-'+'d'*32+'.json',record,replace=True)

    def test_catalog_and_empty_inspection_are_readonly_but_recovery_is_not(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        sid=json.loads(self.handle.read_text())['session_id']
        for operation in ('inspect_catalog','inspect_empty_draft'):
            self.queue_record(operation=operation)
            with bridge._directory(self.root,sid) as directory:
                self.assertEqual(len(worker._assistant_readonly_queue(directory,self.binding,os.getpid())),1)
        self.queue_record(operation='resume_empty_draft')
        with bridge._directory(self.root,sid) as directory, self.assertRaises(worker.WorkerError):
            worker._assistant_readonly_queue(directory,self.binding,os.getpid())
        self.detach()

    def test_operator_retirement_revokes_old_protocol_without_claiming_worker_completion(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        self.queue_record()
        evidence=self.recovery_evidence()
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=False):
            receipt=self.retire(evidence)
        self.assertTrue(receipt['execution_retired_by_operator'])
        self.assertFalse(receipt['worker_done'])
        self.assertFalse(receipt['cleanup_complete'])
        self.assertTrue(self.retirement_status())
        self.assertFalse(worker.assistant_cleanup_status(self.root,self.binding,self.packet)['cleanup_complete'])
        response=self.context/'synthetic-response.json'
        bridge._external(response,create_value={'schema':1})
        for callback,args in ((bridge.health,(self.book,self.handle)), (bridge.next_request,(self.book,self.handle)),
                              (bridge.respond,(self.book,self.handle,'d'*32,response))):
            with self.subTest(callback=callback.__name__), self.assertRaises(story.StoryError) as failure:
                callback(*args)
            self.assertEqual(failure.exception.code,'platform_chrome_worker_unavailable')

    def test_retirement_rejects_alive_browser_controller_write_or_unexpired_queue(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        evidence=self.recovery_evidence()
        with patch.object(worker,'_assistant_chrome_processes',return_value=[123]), patch.object(bridge,'_owner_alive',return_value=False):
            with self.assertRaises(worker.WorkerError): self.retire(evidence)
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=True):
            with self.assertRaises(worker.WorkerError): self.retire(evidence)
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=False):
            for operation,deadline in [('create_draft',time.time()-1),('update_draft',time.time()-1),
                                       ('resume_empty_draft',time.time()-1),('find_drafts',time.time()+10)]:
                self.queue_record(operation=operation,deadline=deadline)
                with self.subTest(operation=operation,deadline=deadline), self.assertRaises(worker.WorkerError):
                    self.retire(evidence)
        self.assertFalse(self.retirement_status())
        self.detach()

    def test_retirement_evidence_is_bound_and_status_rejects_changed_queue(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        evidence=self.recovery_evidence()
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=False):
            with self.assertRaises(worker.WorkerError):
                worker.assistant_retire_readonly_session(self.root,self.binding,self.packet,request_id='f'*32,
                    request_binding_sha256='b'*64,evidence_path=evidence)
            self.retire(evidence)
        self.assertTrue(self.retirement_status())
        self.queue_record()
        self.assertFalse(self.retirement_status())

    def test_retirement_rejects_reset_before_authorization_context_or_queue(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        evidence=self.recovery_evidence()
        original=json.loads(evidence.read_text())
        def publish(value):
            with bridge._pinned(self.context) as directory:
                bridge._write(directory,evidence.name,value,replace=True)
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=False):
            # Validly typed but inverted operator event order cannot retire.
            inverted=json.loads(json.dumps(original))
            inverted['official_kernel_reset']['completed_at']-=.001
            publish(inverted)
            with self.assertRaises(worker.WorkerError): self.retire(evidence)
            # Ordered old timestamps still predate this concrete context.
            old=json.loads(json.dumps(original))
            for group,key in [('authorized_by_user','confirmed_at'),('chrome_quit','observed_none_at'),('official_kernel_reset','completed_at')]:
                old[group][key]-=10
            publish(old)
            with self.assertRaises(worker.WorkerError): self.retire(evidence)
            # A read-only claim formed after reset is also a different epoch.
            publish(original)
            self.queue_record()
            sid=json.loads(self.handle.read_text())['session_id']
            path=self.root/bridge.DIRECTORY/sid/('claimed-'+'d'*32+'.json')
            stamp=original['official_kernel_reset']['completed_at']+.01
            os.utime(path,(stamp,stamp))
            with self.assertRaises(worker.WorkerError): self.retire(evidence)
        self.assertFalse(self.retirement_status())
        self.detach()

    def test_qoder_official_node_repl_reset_tool_is_accepted(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        evidence=self.recovery_evidence(tool='mcp__node-repl__node_repl_reset')
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=False):
            receipt=self.retire(evidence)
        self.assertEqual(receipt['evidence']['official_kernel_reset']['tool'],'mcp__node-repl__node_repl_reset')
        self.assertTrue(self.retirement_status())

    def test_older_authorization_is_preserved_with_fresh_quit_and_reset(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        evidence=self.recovery_evidence()
        value=json.loads(evidence.read_text())
        confirmed=time.time()-3600
        value['authorized_by_user']['confirmed_at']=confirmed
        with bridge._pinned(self.context) as directory:
            bridge._write(directory,evidence.name,value,replace=True)
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=False):
            receipt=self.retire(evidence)
        self.assertEqual(receipt['evidence']['authorized_by_user']['confirmed_at'],confirmed)
        self.assertTrue(self.retirement_status())
        self.assertFalse(receipt['worker_done'])
        self.assertFalse(receipt['cleanup_complete'])

    def test_older_authorization_does_not_relax_reset_freshness_or_validity(self):
        with worker.assistant_session(self.root,self.binding,on_context=self.connect,finish_seconds=0):
            pass
        evidence=self.recovery_evidence()
        original=json.loads(evidence.read_text())
        now=time.time()
        # Isolate freshness from context ordering: even a context that predates
        # the old reset cannot make that reset a fresh observation.
        for name in ('config.json','handle.json'):
            os.utime(self.context/name,(now-1800,now-1800))
        cases=[]
        expired=json.loads(json.dumps(original))
        expired['authorized_by_user']['confirmed_at']=now-3600
        expired['chrome_quit']['observed_none_at']=now-1000
        expired['official_kernel_reset']['completed_at']=now-950
        cases.append(expired)
        for invalid in (0, -1, True, float('nan'), float('inf'), now+60):
            value=json.loads(json.dumps(original))
            value['authorized_by_user']['confirmed_at']=invalid
            cases.append(value)
        with patch.object(worker,'_assistant_chrome_processes',return_value=[]), patch.object(bridge,'_owner_alive',return_value=False):
            for value in cases:
                with bridge._pinned(self.context) as directory:
                    bridge._write(directory,evidence.name,value,replace=True)
                with self.subTest(value=value['authorized_by_user']['confirmed_at']), self.assertRaises(worker.WorkerError):
                    self.retire(evidence)
        self.assertFalse(self.retirement_status())
        self.detach()

    def test_context_has_real_queue_pid_and_no_model_attestation_or_auto_launch(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0) as session:
            packet = session.packet()
            self.assertEqual(packet['controller_pid'], os.getpid())
            self.assertEqual(packet['model_verification'], 'executor_runtime_required')
            self.assertNotIn('model_verified', packet)
            self.assertEqual(packet['execution_origin'], 'assistant_session')
            self.assertIn('runChromeWorker', packet['first_call'])
            self.assertNotIn('import(', packet['next_call'])
            self.assertEqual(session.session_id, json.loads(self.handle.read_text())['session_id'])
            controller = worker.ChromeWorkerController(story)
            status = controller.status(self.root, self.plan['id'], self.profile.name, expected_book_id=self.book.meta('id'))
            self.assertFalse(status['ready'])
            self.assertFalse(status['model_verified'])
            self.detach()
        self.assertTrue(session.bridge_detached)
        self.assertTrue((self.context / 'stop.json').is_file())

    def test_waiting_does_not_accept_ordinary_foreign_health(self):
        with self.assertRaises(worker.WorkerError) as result:
            with worker.assistant_session(self.root, self.binding,
                    on_context=lambda p: self.connect(p, instance='b' * 32), finish_seconds=0):
                self.fail('Foreign instance must not execute')
        self.assertEqual(result.exception.code, 'platform_chrome_worker_binding_mismatch')
        self.assertTrue((self.context / 'stop.json').is_file())
        self.detach()

    def test_existing_attachment_is_never_adopted_or_detached(self):
        handle = self.root / 'foreign-handle.json'
        bridge.attach(self.book, self.plan['id'], self.profile.name, handle)
        with self.assertRaises(worker.WorkerError) as result:
            with worker.assistant_session(self.root, self.binding, on_context=lambda _: self.fail('No context')):
                self.fail('No execution')
        self.assertEqual(result.exception.code, 'platform_chrome_worker_busy')
        self.assertTrue(bridge.worker_status(self.root, self.binding)['attached'])
        self.assertFalse((self.context / 'stop.json').exists())
        bridge.heartbeat(self.book, handle, detach=True)

    def test_connect_timeout_stops_only_this_queue_and_never_claims_cleanup(self):
        with self.assertRaises(worker.WorkerError) as result:
            with worker.assistant_session(self.root, self.binding, on_context=lambda p: None,
                    connect_seconds=.02, finish_seconds=0):
                self.fail('No browser is attached')
        self.assertEqual(result.exception.code, 'platform_chrome_worker_connect_timeout')
        self.assertTrue((self.context / 'stop.json').exists())
        self.assertFalse(list(self.root.glob('.story/platform-chrome-bridge/*/outbox-*')))

    def test_cancel_before_health_queues_no_platform_operations(self):
        with self.assertRaises(worker.WorkerError) as result:
            with worker.assistant_session(self.root, self.binding, on_context=lambda p: None,
                    stop_requested=lambda: True, finish_seconds=0):
                self.fail('Cancelled')
        self.assertEqual(result.exception.code, 'platform_draft_stop_requested')
        self.assertFalse(list(self.root.glob('.story/platform-chrome-bridge/*/outbox-*')))

    def test_context_callback_failure_still_marks_own_worker_stop(self):
        def failed(_):
            raise RuntimeError('Synthetic persistence failure')
        with self.assertRaisesRegex(RuntimeError, 'Synthetic persistence failure'):
            with worker.assistant_session(self.root, self.binding, on_context=failed, finish_seconds=0):
                self.fail('No execution')
        self.assertTrue((self.context / 'stop.json').exists())

    def test_binding_change_rejected_before_context_creation(self):
        binding = {**self.binding, 'target': {**self.binding['target'], 'remote_book_id': 'different'}}
        with self.assertRaises(worker.WorkerError) as result:
            with worker.assistant_session(self.root, binding, on_context=lambda _: self.fail('No context')):
                self.fail('No execution')
        self.assertEqual(result.exception.code, 'platform_chrome_worker_binding_mismatch')
        self.assertEqual(list(self.context.iterdir()), [])

    def test_unfinished_attachment_is_not_reported_as_detached(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0) as session:
            pass
        self.assertFalse(session.bridge_detached)
        self.detach()

    def test_core_exception_is_preserved_if_cleanup_also_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'Original core failure'):
            with patch.object(worker.AssistantBridgeSession, 'close', side_effect=ValueError('Cleanup failure')):
                with worker.assistant_session(self.root, self.binding, on_context=self.connect) as session:
                    raise RuntimeError('Original core failure')
        self.assertFalse(session.bridge_detached)
        self.detach()

    def test_invalid_wait_bounds_do_not_create_context(self):
        for kwargs in ({'connect_seconds': True}, {'connect_seconds': 0}, {'connect_seconds': 121},
                       {'finish_seconds': -1}, {'finish_seconds': 31}, {'finish_seconds': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(worker.WorkerError):
                with worker.assistant_session(self.root, self.binding, on_context=lambda _: None, **kwargs):
                    self.fail('Invalid config')
        self.assertEqual(list(self.context.iterdir()), [])

    def test_bootstrap_failure_reports_original_reason_without_waiting_for_timeout(self):
        def diagnostic(packet):
            bridge._external(self.context / 'worker-bootstrap-error.private.json', create_value={
                'schema': 1, 'phase': 'browser_inventory', 'code': 'platform_chrome_worker_browser_transport_closed',
                'error_name': 'Error', 'error_marker': 'native_pipe_closed'})
        start = time.monotonic()
        with self.assertRaises(worker.WorkerError) as result:
            with worker.assistant_session(self.root, self.binding, on_context=diagnostic, finish_seconds=0):
                self.fail('Failed bootstrap must not execute')
        self.assertEqual(result.exception.code, 'platform_chrome_worker_browser_transport_closed')
        self.assertLess(time.monotonic() - start, 2)

    def ownership(self, owned=('synthetic-tab-1', 'synthetic-tab-2'), *, browser_id='synthetic-chrome',
                  session_id=None, observed_at_ms=None):
        sid = session_id or json.loads(self.handle.read_text())['session_id']
        config = json.loads((self.context / 'config.json').read_text())
        value = {'schema':1, 'worker':config['worker'], 'binding_sha256':config['binding_sha256'],
            'session_id':sid, 'browser_id':browser_id, 'owned_tab_ids':list(owned),
            'observed_at_ms':int(time.time() * 1000) if observed_at_ms is None else observed_at_ms}
        with bridge._pinned(self.context) as directory:
            bridge._write(directory, 'worker-ownership-1.json', value, replace=True)
        return value

    def worker_health(self, *, browser_id='synthetic-chrome', nonce='n' * 43):
        sid = json.loads(self.handle.read_text())['session_id']
        config = json.loads((self.context / 'config.json').read_text())
        value = {'schema':1, 'session_id':sid, 'binding_sha256':config['binding_sha256'], 'nonce':nonce,
            'browser':{'kind':'chrome', 'id':browser_id},
            'capabilities':{'list_tabs':True, 'get_tab':True, 'dom_snapshot':True}, 'worker':config['worker']}
        with bridge._pinned(self.context) as directory:
            bridge._write(directory, 'worker-health-' + nonce + '.json', value, replace=True)

    def reconcile_evidence(self, *, checked=None, present=False, browser_id='synthetic-chrome',
                           request_id='a' * 32, request_binding_sha256='b' * 64, **changes):
        sid = json.loads(self.handle.read_text())['session_id']
        tabs = [{'id':tab, 'present':present} for tab in
                (('synthetic-tab-1', 'synthetic-tab-2') if checked is None else checked)]
        value = {'schema':1, 'request_id':request_id, 'config_path':self.packet['config_path'],
            'controller_instance':self.packet['controller_instance'], 'session_id':sid,
            'request_binding_sha256':request_binding_sha256, 'browser_id':browser_id,
            'source':'official_node_repl_browser_use', 'observed_at_ms':int(time.time() * 1000),
            'checked_tabs':tabs, **changes}
        path = self.context / 'reconcile-evidence.json'
        with bridge._pinned(self.context) as directory:
            bridge._write(directory, path.name, value, replace=True)
        return path

    def terminal_readonly_session(self):
        with worker.assistant_session(self.root, self.binding, on_context=self.connect, finish_seconds=0):
            pass
        self.detach()

    def reconcile(self, evidence, *, request_id='a' * 32, request_binding_sha256='b' * 64, alive=False):
        with patch.object(bridge, '_owner_alive', return_value=alive):
            return worker.assistant_reconcile_session(self.root, self.binding, self.packet,
                request_id=request_id, request_binding_sha256=request_binding_sha256, evidence_path=evidence)

    def reconciled_path(self):
        sid = json.loads(self.handle.read_text())['session_id']
        return self.root / bridge.DIRECTORY / sid / 'worker-reconciled.json'

    def cleanup(self, *, request_id='a' * 32, request_binding_sha256='b' * 64, mode='inspect_drafts'):
        with patch.object(bridge, '_owner_alive', return_value=False):
            return worker.assistant_cleanup_status(self.root, self.binding, self.packet,
                request_id=request_id, request_binding_sha256=request_binding_sha256, mode=mode)

    def test_machine_verified_absent_session_is_accepted_but_never_a_worker_success(self):
        self.terminal_readonly_session()
        latest = self.ownership()
        self.worker_health()
        evidence = self.reconcile_evidence(checked=latest['owned_tab_ids'])
        receipt = self.reconcile(evidence)
        self.assertEqual(receipt['kind'], 'machine_verified_absent_session')
        self.assertFalse(receipt['worker_done'])
        self.assertTrue(receipt['cleanup_complete'])
        self.assertFalse(receipt['platform_written'])
        self.assertEqual(receipt['owned_tab_ids'], latest['owned_tab_ids'])
        sid = json.loads(self.handle.read_text())['session_id']
        reconciled_path = self.root / bridge.DIRECTORY / sid / 'worker-reconciled.json'
        receipt_bytes = reconciled_path.read_bytes()
        # Finishing a prior reconciliation is idempotent and never rewrites proof.
        self.assertEqual(self.reconcile(evidence), receipt)
        self.assertEqual(reconciled_path.read_bytes(), receipt_bytes)
        result = self.cleanup()
        self.assertTrue(result['cleanup_complete'])
        self.assertEqual(result['cleanup_status'], 'reconciled_absent')
        self.assertFalse(result['worker_done'])
        # Only read-only modes may be reconciled; a sync task still needs its own receipt.
        self.assertFalse(self.cleanup(mode='sync_drafts')['cleanup_complete'])
        self.assertFalse((self.context / 'worker-final.json').exists())

    def test_reconciliation_rejects_tabs_still_present_or_missing_from_the_observation(self):
        self.terminal_readonly_session()
        latest = self.ownership()
        self.worker_health()
        for checked, present in ((latest['owned_tab_ids'], True),
                                 (latest['owned_tab_ids'][:1], False),
                                 (latest['owned_tab_ids'] + ['synthetic-tab-3'], False)):
            with self.subTest(checked=checked, present=present):
                evidence = self.reconcile_evidence(checked=checked, present=present)
                with self.assertRaises(worker.WorkerError):
                    self.reconcile(evidence)
        self.assertFalse(self.reconciled_path().exists())

    def test_reconciliation_rejects_a_live_controller_live_lease_or_undrained_queue(self):
        self.terminal_readonly_session()
        latest = self.ownership()
        self.worker_health()
        evidence = self.reconcile_evidence(checked=latest['owned_tab_ids'])
        with self.assertRaises(worker.WorkerError) as failure:
            self.reconcile(evidence, alive=True)
        self.assertEqual(failure.exception.code, 'platform_draft_assistant_reconcile_process_alive')
        sid = json.loads(self.handle.read_text())['session_id']
        with bridge._directory(self.root, sid) as directory:
            bridge._write(directory, 'lease.json', {'schema':1, 'session_id':sid,
                'expires':time.time() + 300, 'detached':False}, replace=True)
        with self.assertRaises(worker.WorkerError):
            self.reconcile(evidence)
        with bridge._directory(self.root, sid) as directory:
            bridge._write(directory, 'lease.json', {'schema':1, 'session_id':sid,
                'expires':time.time(), 'detached':True}, replace=True)
        for operation, deadline in (('find_drafts', time.time() + 60), ('create_draft', time.time() - 1)):
            with self.subTest(operation=operation):
                self.queue_record(operation=operation, deadline=deadline)
                with self.assertRaises(worker.WorkerError):
                    self.reconcile(evidence)
                (self.root / bridge.DIRECTORY / sid / ('claimed-' + 'd' * 32 + '.json')).unlink()
        self.assertFalse(self.reconciled_path().exists())

    def test_reconciliation_binds_the_observation_to_the_sessions_own_browser_and_request(self):
        self.terminal_readonly_session()
        latest = self.ownership()
        self.worker_health()
        for evidence in (self.reconcile_evidence(checked=latest['owned_tab_ids'], browser_id='other-chrome'),
                         self.reconcile_evidence(checked=latest['owned_tab_ids'], source='self_reported'),
                         self.reconcile_evidence(checked=latest['owned_tab_ids'], request_id='f' * 32)):
            with self.subTest(evidence=evidence):
                with self.assertRaises(worker.WorkerError):
                    self.reconcile(evidence)
        self.assertFalse(self.reconciled_path().exists())
        # A changed ownership browser id also invalidates the old observation.
        evidence = self.reconcile_evidence(checked=latest['owned_tab_ids'])
        self.ownership(browser_id='different-chrome')
        with self.assertRaises(worker.WorkerError):
            self.reconcile(evidence)

    def test_reconciliation_refuses_a_session_that_already_has_a_final_receipt(self):
        self.terminal_readonly_session()
        latest = self.ownership()
        self.worker_health()
        evidence = self.reconcile_evidence(checked=latest['owned_tab_ids'])
        self.final_receipt()
        with self.assertRaises(worker.WorkerError) as failure:
            self.reconcile(evidence)
        self.assertEqual(failure.exception.code, 'platform_draft_assistant_reconcile_conflict')
        self.assertFalse(self.reconciled_path().exists())

    def test_real_local_queue_is_driven_concurrently_and_detaches_after_stop(self):
        calls, errors, threads = [], [], []
        finish = threading.Event()
        def on_context(packet):
            self.connect(packet)
            def serve():
                book = story.Book(self.root)
                try:
                    while not finish.is_set():
                        if (self.context / 'stop.json').exists():
                            bridge.heartbeat(book, self.handle, detach=True)
                            return
                        claim = bridge.next_request(book, self.handle)
                        if claim.get('request_id'):
                            request = json.loads(Path(claim['request_path']).read_text())
                            calls.append(request['operation'])
                            response = {'schema': 1, 'id': request['id'], 'nonce': request['nonce'],
                                'session_id': request['session_id'], 'binding_sha256': request['binding_sha256'],
                                'ok': True, 'result': request['binding']['target']}
                            response_path = self.context / ('response-' + request['id'] + '.json')
                            bridge._external(response_path, create_value=response)
                            bridge.respond(book, self.handle, claim['request_id'], response_path)
                        finish.wait(.01)
                except BaseException as error:
                    errors.append(repr(error))
                finally:
                    book.close()
            thread = threading.Thread(target=serve, daemon=True)
            threads.append(thread)
            thread.start()
        try:
            with patch.dict(bridge.OP_SECONDS, verify_target=2):
                with worker.assistant_session(self.root, self.binding, on_context=on_context, finish_seconds=2) as session:
                    with bridge.HostAdapter(self.root, self.binding, json.loads(self.profile.read_text())) as adapter:
                        self.assertEqual(adapter.session_id, session.session_id)
                        self.assertEqual(adapter.verify_target(**self.binding['target']), self.binding['target'])
            self.assertTrue(session.bridge_detached)
            self.assertEqual(calls, ['verify_target'])
        finally:
            finish.set()
            for thread in threads:
                thread.join(3)
            if getattr(self, 'handle', None) and bridge.worker_status(self.root, self.binding)['attached']:
                self.detach()
        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))


if __name__ == '__main__':
    unittest.main()
