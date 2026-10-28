"""Actual authenticated host routes and their shared stop-dispatch fence."""
import http.client
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_workbench as base


class HostExecutionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.book = self.fixture.root, self.fixture.book
        self.story = base.story
        self.task = self.story.workbench._editor_review_task(self.root, 'formal:2')['task']
        self.before = self.fixture.authoritative_state()
        worker = self.story.task_worker
        self.patches = [patch.object(worker, '_base', return_value=self.root.parent / 'isolated-workers'),
                        patch.object(worker, '_model_defaults', return_value={}),
                        patch.object(worker, '_executable', return_value=Path(sys.executable).resolve()),
                        patch.object(worker, '_spawn', return_value=SimpleNamespace(pid=os.getpid()))]
        for patched in self.patches:
            patched.start(); self.addCleanup(patched.stop)
        self.spawn = self.patches[-1].get_original()[0]
        self.server = self.story.workbench.editor_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.token = self.server.editor_url.rstrip('/').split('/')[-1]
        self.instance = json.loads((self.root / '.story/workbench-service.json').read_text())['instance']

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def post(self, operation, payload):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            client.request('POST', '/' + self.token + '/api/' + operation, json.dumps(payload),
                           {'Origin': 'http://127.0.0.1:' + str(self.server.server_port),
                            'Content-Type': 'application/json', 'X-Story-Token': self.token})
            response = client.getresponse(); return response.status, json.loads(response.read())
        finally:
            client.close()

    def start_payload(self):
        return {'task_id': self.task['id'], 'version': self.task['version'], 'request_id': 'http-explicit-request',
                'execute': True, 'expected_book_id': self.task['book_id']}

    def test_host_start_idempotency_status_cancel_and_busy_stop_are_real_routes(self):
        status, first = self.post('tasks/start', self.start_payload())
        self.assertEqual(status, 200, first)
        status, again = self.post('tasks/start', self.start_payload())
        self.assertEqual(status, 200, again); self.assertTrue(again['unchanged'])
        self.assertEqual(first['execution']['id'], again['execution']['id'])
        status, observed = self.post('tasks/execution', {'task_id': self.task['id']})
        self.assertEqual(status, 200, observed)
        self.assertEqual(observed['execution']['state'], 'starting')
        status, denied = self.post('stop-prepare', {'instance': self.instance, 'saved': True})
        self.assertEqual(status, 409, denied); self.assertEqual(denied['error'], 'workbench_task_busy')
        self.assertFalse(self.server.stopping)
        status, cancelled = self.post('tasks/cancel', {'task_id': self.task['id'], 'execution_id': first['execution']['id']})
        self.assertEqual(status, 200, cancelled)
        self.assertEqual(cancelled['execution']['state'], 'cancel_requested')
        self.assertEqual(self.fixture.authoritative_state(), self.before)

    def test_no_implicit_or_wrong_book_dispatch_and_fresh_stop_allows_only_reads(self):
        status, denied = self.post('tasks/start', {**self.start_payload(), 'execute': False})
        self.assertEqual(status, 409); self.assertEqual(denied['error'], 'task_execution_execute_required')
        status, denied = self.post('tasks/start', {**self.start_payload(), 'expected_book_id': 'another-book'})
        self.assertEqual(status, 409); self.assertEqual(denied['error'], 'wrong_book')
        status, prepared = self.post('stop-prepare', {'instance': self.instance, 'saved': True})
        self.assertEqual(status, 200, prepared)
        for operation in ('start', 'reconcile'):
            payload = self.start_payload() if operation == 'start' else {'task_id': self.task['id'], 'execution_id': '0' * 32}
            status, denied = self.post('tasks/' + operation, payload)
            self.assertEqual(status, 409); self.assertEqual(denied['error'], 'workbench_presence_preparing')
        status, observed = self.post('tasks/execution', {'task_id': self.task['id']})
        self.assertEqual(status, 200, observed); self.assertIsNone(observed['execution'])
        self.post('stop-cancel', {'instance': self.instance, 'stop_challenge': prepared['stop_challenge']})
        self.assertEqual(self.fixture.authoritative_state(), self.before)


if __name__ == '__main__':
    unittest.main()
