"""Exercise authenticated local refresh routes without any platform execution."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

import test_workbench as base


class FormalRefreshHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.backend = base.story.platform_drafts
        self.server = self.w.editor_server(self.fixture.root)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.token = self.server.editor_url.rstrip('/').split('/')[-1]

    def stop(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def post(self, action, data=None, **overrides):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        headers = {'Content-Type': 'application/json', 'X-Story-Token': self.token,
                   'Origin': f'http://127.0.0.1:{self.server.server_port}', **overrides}
        try:
            client.request('POST', '/' + self.token + '/api/platform-drafts/' + action,
                           json.dumps(data or {}), headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def payload(self):
        return {'plan_id': 'plan', 'profile_id': 'profile.json', 'profile_sha256': 'a' * 64,
                'expected_revision': 8, 'start': 1, 'end': 3}

    def test_routes_bind_the_server_book_and_exact_refresh_inputs(self):
        for action, method, data in [('formal-state', 'workbench_formal_state', {}),
                                     ('refresh-plan', 'workbench_refresh_plan', self.payload())]:
            with self.subTest(action=action), patch.object(self.backend, method, return_value={'ok': True}) as mocked:
                status, body = self.post(action, data)
                self.assertEqual(status, 200, body)
                self.assertEqual(mocked.call_args.args[0], self.fixture.root)
                self.assertEqual(mocked.call_args.kwargs['expected_book_id'], self.fixture.book.meta('id'))
                if action == 'refresh-plan':
                    self.assertEqual(mocked.call_args.args[1:], ('plan', 'profile.json'))
                    self.assertEqual(mocked.call_args.kwargs, {
                        'expected_book_id': self.fixture.book.meta('id'), 'expected_revision': 8,
                        'expected_profile_sha256': 'a' * 64, 'start': 1, 'end': 3})

    def test_identity_origin_token_and_extra_target_fields_fail_before_backend(self):
        for action, method, data in [('formal-state', 'workbench_formal_state', {}),
                                     ('refresh-plan', 'workbench_refresh_plan', self.payload())]:
            with self.subTest(action=action), patch.object(self.backend, method) as mocked:
                self.assertEqual(self.post(action, data, **{'X-Story-Token': 'wrong'})[0], 403)
                self.assertEqual(self.post(action, data, Origin='https://untrusted.example')[0], 403)
                for field in ['root', 'book_id', 'account_id', 'remote_book_id', 'execute']:
                    status, body = self.post(action, {**data, field: 'injected'})
                    self.assertEqual(status, 409, body)
                    self.assertEqual(body['error'], 'invalid_input')
                with patch.object(self.w, '_library_entry', side_effect=base.story.StoryError('workbench_book_changed', '作品已替换')):
                    self.assertEqual(self.post(action, data)[0], 409)
                mocked.assert_not_called()

    def test_formal_state_is_live_read_only_and_never_prepares_or_starts(self):
        with patch.object(base.story.publish, 'prepare', side_effect=AssertionError('No preparation')), \
                patch.object(self.backend, 'workbench_start', side_effect=AssertionError('No platform task')):
            before = self.fixture.authoritative_state()
            status, packet = self.post('formal-state')
            self.assertEqual(status, 200, packet)
            self.assertEqual((packet['formal_first'], packet['formal_last'], packet['formal_count']), (1, 2, 2))
            self.assertEqual(packet['book_id'], self.fixture.book.meta('id'))
            self.assertFalse(packet['remote_accessed'])
            self.assertFalse(packet['submission_performed'])
            self.assertEqual(self.fixture.authoritative_state(), before)
        self.fixture.commit(3)
        with patch.object(base.story.publish, 'prepare', side_effect=AssertionError('No preparation')):
            status, current = self.post('formal-state')
        self.assertEqual(status, 200, current)
        self.assertEqual(current['formal_last'], 3)
        self.assertGreater(current['revision'], packet['revision'])

    def test_actual_refresh_keeps_old_batch_and_target_without_platform_access(self):
        url = 'https://fanqienovel.com/main/writer/chapter-manage/1234567890&%E6%B5%8B%E8%AF%95?type=1'
        bound = self.backend.workbench_bind(self.fixture.root, '12345', url, 1, 2,
            expected_book_id=self.fixture.book.meta('id'), expected_revision=self.fixture.book.meta('revision'))
        old = base.story.publish.inspect(self.fixture.book, bound['plan_id'])
        self.fixture.commit(3)
        data = {'plan_id': bound['plan_id'], 'profile_id': bound['profile_id'],
                'profile_sha256': bound['profile_sha256'], 'expected_revision': self.fixture.book.meta('revision'),
                'start': 1, 'end': 3}
        before = tuple(self.fixture.book.db.iterdump())
        jobs_before = set(self.backend._WORKBENCH_JOBS)
        with patch.object(self.backend, 'workbench_start', side_effect=AssertionError('No platform task')), \
                patch.object(base.story.platform_browser.BrowserAdapter, '__enter__', side_effect=AssertionError('No browser')):
            status, packet = self.post('refresh-plan', data)
        self.assertEqual(status, 200, packet)
        self.assertEqual(packet['chapters'], [1, 2, 3])
        self.assertEqual(packet['account_id'], '12345')
        self.assertEqual(packet['remote_book_id'], '1234567890')
        self.assertEqual(packet['profile_sha256'], bound['profile_sha256'])
        self.assertNotEqual(packet['plan_id'], bound['plan_id'])
        self.assertEqual(packet['scope'], 'local_formal_refresh_only')
        self.assertFalse(packet['remote_accessed'])
        self.assertFalse(packet['submission_performed'])
        self.assertEqual(base.story.publish.inspect(self.fixture.book, bound['plan_id'])['manifest'], old['manifest'])
        self.assertEqual(tuple(self.fixture.book.db.iterdump()), before)
        self.assertEqual(set(self.backend._WORKBENCH_JOBS), jobs_before)

    def test_refresh_stale_revision_and_changed_profile_cannot_create_or_start(self):
        url = 'https://fanqienovel.com/main/writer/chapter-manage/1234567890&%E6%B5%8B%E8%AF%95?type=1'
        bound = self.backend.workbench_bind(self.fixture.root, '12345', url, 1, 2,
            expected_book_id=self.fixture.book.meta('id'), expected_revision=self.fixture.book.meta('revision'))
        data = {'plan_id': bound['plan_id'], 'profile_id': bound['profile_id'],
                'profile_sha256': bound['profile_sha256'], 'expected_revision': self.fixture.book.meta('revision'),
                'start': 1, 'end': 2}
        with patch.object(base.story.publish, 'prepare', side_effect=AssertionError('No preparation')), \
                patch.object(self.backend, 'workbench_start', side_effect=AssertionError('No platform task')):
            for changes, code in [({'expected_revision': data['expected_revision'] + 1}, 'stale_revision'),
                                  ({'profile_sha256': 'b' * 64}, 'platform_draft_profile_changed')]:
                status, packet = self.post('refresh-plan', {**data, **changes})
                self.assertEqual(status, 409, packet)
                self.assertEqual(packet['error'], code)


if __name__ == '__main__':
    unittest.main()
