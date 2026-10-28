"""Synthetic HTTP pages, shelf forwarding and the actual service stop gate."""
import hashlib
import json
import re
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import test_workbench as base
import test_workbench_library as library


class WorkbenchPresenceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = library.WorkbenchLibraryTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = self.fixture.w
        self.root = self.fixture.book('合成跨窗口作品')
        self.entry = self.w._library_entry(self.root)
        self.server = self.fixture.serve(self.w.editor_server(self.root))
        self.route = urlsplit(self.server.editor_url).path
        self.token = self.route.strip('/')
        self.identity = json.loads((self.root / '.story/workbench-service.json').read_text())

    def post(self, action, payload, *, headers=None):
        sent = {'Origin': f'http://127.0.0.1:{self.server.server_port}',
                'Content-Type': 'application/json', 'X-Story-Token': self.token}
        sent.update(headers or {})
        status, raw, _ = self.fixture.request(self.server, 'POST', self.route + 'api/' + action,
                                               payload, sent)
        return status, json.loads(raw)

    def page(self):
        status, raw, _ = self.fixture.request(self.server, 'GET', self.route)
        self.assertEqual(status, 200, raw[:200])
        match = re.search(r'const config=(\{"protocol"[^\n]+\});', raw.decode())
        self.assertIsNotNone(match)
        return json.loads(match[1])

    def report(self, page, sequence=0, closing=False, stop_ack=None, **flags):
        payload = {'page_id': page['page_id'], 'page_token': page['page_token'],
                   'sequence': sequence, 'initialized': True, 'closing': closing,
                   'buffers': [{'id': 'file:候选.md', 'title': '合成候选',
                                **dict.fromkeys(self.server.presence.FLAGS, False), **flags}]}
        if stop_ack is not None:
            payload['stop_ack'] = {key: stop_ack[key] for key in ('challenge_id', 'nonce')}
        return self.post('presence', payload)

    def session(self):
        return self.w._service_request(self.root, expected_book_id=self.entry['book_id'])

    def test_get_issues_distinct_unknown_pages_and_authenticated_report_orders_states(self):
        first, second = self.page(), self.page()
        self.assertNotEqual(first['page_id'], second['page_id'])
        self.assertEqual(self.session()['presence']['counts']['unknown'], 2)
        status, result = self.report(first, 2, dirty=True)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['root'], str(self.root))
        self.assertEqual(result['instance'], self.identity['instance'])
        self.assertEqual(result['book_id'], self.entry['book_id'])
        status, result = self.report(first, 1)
        self.assertEqual(status, 200); self.assertFalse(result['accepted'])
        self.assertEqual(self.session()['presence']['counts']['dirty'], 1)
        bad = {**first, 'page_token': 'forged'}
        status, result = self.report(bad, 3)
        self.assertEqual(status, 409); self.assertEqual(result['error'], 'workbench_presence_page_unknown')
        self.assertEqual(result['instance'], self.identity['instance'])

    def test_stop_saved_assertion_cannot_bypass_unknown_or_any_fresh_pending_flag(self):
        page = self.page()
        with self.assertRaises(base.story.StoryError) as caught:
            self.w._service_request(self.root, 'stop', True)
        self.assertEqual(caught.exception.code, 'workbench_presence_unknown')
        for sequence, flag in enumerate(self.server.presence.FLAGS):
            self.report(page, sequence, **{flag: True})
            with self.assertRaises(base.story.StoryError) as caught:
                self.w._service_request(self.root, 'stop', True)
            self.assertEqual(caught.exception.code, 'workbench_presence_pending')
            self.assertTrue(self.session()['running'])

    def test_unknown_confirmation_is_current_single_use_and_normal_clean_stop_is_gated(self):
        page = self.page(); fingerprint = self.session()['presence']['fingerprint']
        confirmation = self.w._service_request(self.root, 'presence-confirm', fingerprint=fingerprint, confirmed=True)
        self.report(page, 0, dirty=True)
        with self.assertRaises(base.story.StoryError) as caught:
            self.w._service_request(self.root, 'stop', True, unknown_confirmation=confirmation['unknown_confirmation'])
        self.assertEqual(caught.exception.code, 'workbench_presence_pending')
        self.report(page, 1, closing=True)
        # Keep the loop alive briefly to test the post-authorization GET gate.
        with patch.object(self.server, 'shutdown', return_value=None):
            result = self.w._service_request(self.root, 'stop', True)
            self.assertTrue(result['stop_requested'])
            self.assertTrue(result['automatic_verified'])
            count = self.server.presence.summary()['counts']['registered']
            status, raw, _ = self.fixture.request(self.server, 'GET', self.route)
            self.assertEqual(status, 409, raw[:200])
            self.assertEqual(self.server.presence.summary()['counts']['registered'], count)

    def test_unknown_stop_path_can_be_explicitly_confirmed_without_pretending_automatic_save(self):
        self.page()
        status = self.session()
        consent = self.w._service_request(self.root, 'presence-confirm', fingerprint=status['presence']['fingerprint'], confirmed=True)
        stopped = self.w._service_request(self.root, 'stop', True, unknown_confirmation=consent['unknown_confirmation'])
        self.assertTrue(stopped['stop_requested'])
        self.assertTrue(stopped['manual_unknown_confirmation'])
        self.assertFalse(stopped['automatic_verified'])

    def test_service_summary_can_exceed_old_eight_kib_limit_and_checks_expected_book(self):
        page = self.page()
        buffers = [{'id': 'file:文稿' + str(index), 'title': '长标题' * 60,
                    **dict.fromkeys(self.server.presence.FLAGS, False)} for index in range(100)]
        status, result = self.post('presence', {'page_id': page['page_id'], 'page_token': page['page_token'],
                                               'sequence': 0, 'initialized': True, 'buffers': buffers})
        self.assertEqual(status, 200, result)
        result = self.session()
        self.assertTrue(result['presence_supported'])
        self.assertEqual(len(result['presence']['pages'][0]['buffers']), 100)
        self.assertGreater(len(json.dumps(result).encode()), 8192)
        with self.assertRaises(base.story.StoryError) as caught:
            self.w._service_request(self.root, expected_book_id='another-book')
        self.assertEqual(caught.exception.code, 'workbench_book_changed')

    def test_shelf_unknown_confirmation_forwarding_does_not_expose_service_capability(self):
        self.page()
        shelf = self.fixture.start(books=[self.root])
        target = {'key': self.entry['key'], 'expected_book_id': self.entry['book_id']}
        status, state = self.fixture.post(shelf, 'book-service', target)
        self.assertEqual(status, 200, state)
        self.assertNotIn('url', state); self.assertNotIn('token', state)
        status, denied = self.fixture.post(shelf, 'stop-book', {**target, 'saved': True})
        self.assertEqual(status, 409); self.assertEqual(denied['error'], 'workbench_presence_unknown')
        status, consent = self.fixture.post(shelf, 'confirm-book-unknown',
                                           {**target, 'fingerprint': state['presence']['fingerprint'], 'confirmed': True})
        self.assertEqual(status, 200, consent)
        status, stopped = self.fixture.post(shelf, 'stop-book',
                                           {**target, 'saved': True, 'unknown_confirmation': consent['unknown_confirmation']})
        self.assertEqual(status, 200, stopped); self.assertTrue(stopped['stop_requested'])
        self.assertFalse(stopped['automatic_verified'])

    def test_http_prepare_freezes_writes_until_each_page_has_its_own_fresh_ack(self):
        first, second = self.page(), self.page()
        self.report(first); self.report(second)
        status, prepared = self.post('stop-prepare', {'instance': self.identity['instance'], 'saved': True})
        self.assertEqual(status, 200, prepared)
        self.assertFalse(prepared['fresh_stop']['ready'])
        token = prepared['stop_challenge']
        status, blocked = self.post('save', {'chapter': 1, 'text': '不得写入的正文'})
        self.assertEqual(status, 409, blocked)
        self.assertEqual(blocked['error'], 'workbench_presence_preparing')
        ack_first = self.report(first, 1)[1]['stop_challenge']
        ack_second = self.report(second, 1)[1]['stop_challenge']
        self.assertNotEqual(ack_first['nonce'], ack_second['nonce'])
        status, denied = self.report(first, 2, stop_ack=ack_second)
        self.assertEqual(status, 409, denied)
        self.assertEqual(denied['error'], 'workbench_presence_fresh_ack_invalid')
        self.report(first, 2, stop_ack=ack_first)
        status, waiting = self.post('stop-status', {'instance': self.identity['instance'], 'stop_challenge': token})
        self.assertEqual(status, 200, waiting)
        self.assertFalse(waiting['fresh_stop']['ready'])
        self.report(second, 2, stop_ack=ack_second)
        self.assertTrue(self.server.presence.stop_status(token)['fresh_stop']['ready'])
        observed = json.dumps(self.session(), ensure_ascii=False)
        self.assertNotIn(ack_first['nonce'], observed)
        self.assertNotIn(first['page_token'], observed)
        status, cancelled = self.post('stop-cancel', {'instance': self.identity['instance'], 'stop_challenge': token})
        self.assertEqual(status, 200, cancelled)
        self.assertTrue(cancelled['cancelled'])
        self.assertFalse((self.root / '.story/workbench-stop-lease.json').exists())

    def test_service_stop_round_trip_releases_http_between_nonce_and_ack(self):
        page = self.page(); self.report(page)
        done, errors = threading.Event(), []

        def heartbeat():
            sequence, challenge = 0, None
            try:
                while not done.wait(0.03):
                    sequence += 1
                    status, response = self.report(page, sequence, stop_ack=challenge)
                    if status == 409 and response.get('error') == 'workbench_presence_stopping':
                        return
                    if status != 200:
                        errors.append(response)
                        return
                    challenge = response.get('stop_challenge')
            except OSError:
                # Once the successful stop closes the listener, a final
                # already-scheduled read-only report may lose its transport.
                if not done.is_set() and not self.server.stopping:
                    errors.append('Heartbeat transport ended before stop completed')

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            result = self.w._service_request(self.root, 'stop', True, _fresh_timeout=3)
            self.assertTrue(result['stop_requested'])
            self.assertTrue(result['fresh_verified'])
            self.assertTrue(result['automatic_verified'])
        finally:
            done.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_resume_http_routes_use_the_current_service_book_and_never_persist_body_or_tokens(self):
        source = self.root / '创作约定.md'; source.write_text('中文😀原文。', encoding='utf-8')
        status, empty = self.post('resume-state', {})
        self.assertEqual(status, 200, empty); self.assertEqual(empty['status'], 'empty')
        payload = {'book_id': self.entry['book_id'], 'expected_state_sha256': None,
                   'document_id': 'file:创作约定.md', 'document_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                   'mode': 'reading', 'cursor': {'start': 0, 'end': 0, 'direction': 'none'},
                   'scroll': {'main': 20, 'editor': 0}, 'buffer_dirty': False}
        status, saved = self.post('resume-save', payload)
        self.assertEqual(status, 200, saved)
        status, loaded = self.post('resume-state', {})
        self.assertEqual(status, 200, loaded); self.assertEqual(loaded['status'], 'ready')
        raw = (self.root / '.story/workbench-resume.json').read_text()
        self.assertNotIn('中文', raw); self.assertNotIn(self.token, raw)
        status, denied = self.post('resume-save', {**payload, 'book_id': 'wrong-book',
                                                 'expected_state_sha256': saved['state_sha256']})
        self.assertEqual(status, 409); self.assertEqual(denied['error'], 'workbench_book_changed')


if __name__ == '__main__':
    unittest.main()
