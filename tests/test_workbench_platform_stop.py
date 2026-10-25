"""Real HTTP shutdown cannot truncate a synthetic in-flight platform draft."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from tests import test_workbench_platform_drafts as fixture


class WorkbenchPlatformStopTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.WorkbenchPlatformDraftTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = fixture.story.workbench
        self.server = self.w.editor_server(self.fixture.root)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        page = self.server.presence.register_page()
        self.server.presence.report({**{key: page[key] for key in ('page_id', 'page_token')},
                                     'sequence': 1, 'initialized': True, 'buffers': []})

    def close_server(self):
        self.fixture.release.set()
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(2)
        self.server.server_close()

    def status(self):
        return self.w._service_request(self.fixture.root)

    def test_running_job_blocks_stop_even_with_clean_pages_and_saved_confirmation(self):
        self.fixture.release.clear()
        job = self.fixture.start()
        # __enter__ runs before verify_target's waiting_login callback. Wait
        # until that callback has completed and the fake worker is blocked,
        # so it cannot overwrite the synthetic stages asserted below.
        self.assertTrue(self.fixture.waiting_login.wait(2))
        for stage in ('queued', 'waiting_login', 'waiting_host', 'saving_draft', 'reading_back'):
            with self.subTest(stage=stage):
                fixture.drafts._update_job(job['id'], stage=stage)
                status = self.status()
                self.assertEqual(status['presence']['state'], 'clean')
                self.assertEqual(status['platform_activity']['jobs'][0]['stage'], stage)
                with self.assertRaises(fixture.story.StoryError) as caught:
                    self.w._service_request(self.fixture.root, 'stop', True)
                self.assertEqual(caught.exception.code, 'workbench_platform_busy')
                self.assertTrue(caught.exception.details['platform_activity']['busy'])
                self.assertTrue(self.thread.is_alive())
                self.assertFalse(self.server.stopping)
        self.fixture.release.set()
        self.fixture.wait_job(job['id'])
        self.assertFalse(self.status()['platform_activity']['busy'])
        self.assertTrue(self.w._service_request(self.fixture.root, 'stop', True)['stop_requested'])
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def test_activity_is_book_scoped_and_does_not_expose_accounts_or_manuscripts(self):
        self.fixture.release.clear()
        job = self.fixture.start()
        self.assertTrue(self.fixture.entered.wait(2))
        summary = self.status()['platform_activity']
        self.assertEqual(summary['jobs'][0]['id'], job['id'])
        self.assertNotIn('account_id', json.dumps(summary))
        self.assertNotIn('remote_book_id', json.dumps(summary))
        self.assertNotIn('profile', json.dumps(summary))
        self.assertFalse(fixture.drafts.workbench_activity(
            self.fixture.root, expected_book_id='different-book')['busy'])
        self.assertFalse(fixture.drafts.workbench_activity(
            self.fixture.root.parent / 'different-root', expected_book_id=job['book_id'])['busy'])

    def test_failed_finished_job_does_not_permanently_block_shutdown(self):
        self.fixture.fail_create = True
        job = self.fixture.start()
        result = self.fixture.wait_job(job['id'])
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(self.status()['platform_activity']['busy'])
        self.assertTrue(self.w._service_request(self.fixture.root, 'stop', True)['stop_requested'])

    def test_after_stop_authorization_new_start_is_rejected_before_creating_a_job(self):
        with patch.object(self.server, 'shutdown', return_value=None):
            self.assertTrue(self.w._service_request(self.fixture.root, 'stop', True)['stop_requested'])
            route = urlsplit(self.server.editor_url).path
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            try:
                connection.request('POST', route + 'api/platform-drafts/start', '{}',
                    {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.server.server_port}',
                     'X-Story-Token': route.strip('/')})
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 409)
                self.assertEqual(result['error'], 'workbench_presence_stopping')
                self.assertFalse(self.fixture.calls)
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
