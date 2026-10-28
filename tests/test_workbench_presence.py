"""Ordered service-local page metadata and explicit unknown-page exit consent."""
import json
import unittest

import test_workbench as base


class EditorPresenceTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.pages = []
        self.w = base.story.workbench
        self.registry = self.w._EditorPresence('instance-one', 'book-one', clock=lambda: self.now,
                                               stale_after=10, max_pages=8, max_buffers=4)

    def issue(self):
        page = self.registry.register_page()
        self.pages.append(page)
        return page

    def send(self, page, sequence=0, *, initialized=True, closing=False, stop_ack=None, **flags):
        buffer = {'id': 'candidate:1', 'title': '候选第一章', **dict.fromkeys(self.registry.FLAGS, False), **flags}
        payload = {'page_id': page['page_id'], 'page_token': page['page_token'],
                   'sequence': sequence, 'initialized': initialized,
                   'closing': closing, 'buffers': [buffer]}
        if stop_ack is not None:
            payload['stop_ack'] = {key: stop_ack[key] for key in ('challenge_id', 'nonce')}
        return self.registry.report(payload)

    def acknowledge(self, page, sequence):
        challenge = self.send(page, sequence)['stop_challenge']
        self.assertIsNotNone(challenge)
        return self.send(page, sequence + 1, stop_ack=challenge)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(base.story.StoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def confirm(self):
        summary = self.registry.summary()
        return self.registry.confirm_unknown(summary['fingerprint'], confirmed=True)['unknown_confirmation']

    def test_no_pages_is_distinct_from_automatic_verification_of_clean_pages(self):
        empty = self.registry.summary()
        self.assertTrue(empty['no_pages'])
        self.assertFalse(empty['automatic_verified'])
        self.assertEqual(empty['state'], 'no_pages')
        self.assertFalse(self.registry.require_stop(True)['automatic_verified'])
        self.send(self.issue())
        clean = self.registry.summary()
        self.assertFalse(clean['no_pages'])
        self.assertTrue(clean['automatic_verified'])
        self.assertEqual(clean['state'], 'clean')
        self.error('workbench_presence_saved_required', self.registry.require_stop)

    def test_get_registration_is_unknown_until_initialized_not_saved_by_assertion(self):
        page = self.issue()
        summary = self.registry.summary()
        self.assertEqual(summary['state'], 'unknown')
        self.assertEqual(summary['pages'][0]['unknown_reason'], 'not_initialized')
        self.error('workbench_presence_unknown', self.registry.require_stop, True)
        self.send(page, initialized=False)
        self.assertEqual(self.registry.summary()['state'], 'unknown')
        self.send(page, 1)
        self.error('workbench_presence_fresh_required', self.registry.require_stop, True)
        prepared = self.registry.prepare_stop(True)
        self.acknowledge(page, 2)
        self.assertTrue(self.registry.require_stop(True,
            stop_challenge=prepared['stop_challenge'])['automatic_verified'])

    def test_each_fresh_pending_flag_blocks_stop_and_manual_unknown_override(self):
        page = self.issue()
        self.issue()
        for sequence, flag in enumerate(self.registry.FLAGS):
            with self.subTest(flag=flag):
                self.send(page, sequence, **{flag: True})
                summary = self.registry.summary()
                self.assertEqual(summary['counts'][flag], 1)
                self.error('workbench_presence_pending', self.registry.require_stop, True)
                self.error('workbench_presence_pending', self.registry.confirm_unknown,
                           summary['fingerprint'], confirmed=True)

    def test_stale_last_pending_is_unknown_and_requires_specific_manual_confirmation(self):
        page = self.issue(); self.send(page, dirty=True, recovering=True)
        self.now += 10
        summary = self.registry.summary()
        self.assertEqual(summary['state'], 'unknown')
        self.assertTrue(summary['pages'][0]['last_known_pending'])
        self.assertTrue(summary['pages'][0]['buffers'][0]['dirty'])
        self.assertEqual(summary['counts']['pending_pages'], 0)
        self.error('workbench_presence_unknown', self.registry.require_stop, True)
        token = self.confirm()
        result = self.registry.require_stop(True, token)
        self.assertTrue(result['manual_unknown_confirmation'])
        self.assertFalse(result['automatic_verified'])
        self.assertTrue(result['presence']['pages'][0]['last_known_pending'])
        self.error('workbench_presence_unknown', self.registry.require_stop, True, token)

    def test_older_sequence_cannot_clear_dirty_or_extend_heartbeat(self):
        page = self.issue(); self.send(page, 2, dirty=True)
        before = self.registry.summary(); self.now += 6
        result = self.send(page, 1)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['summary']['fingerprint'], before['fingerprint'])
        self.now += 4
        self.assertEqual(self.registry.summary()['state'], 'unknown')
        self.assertTrue(self.registry.summary()['pages'][0]['last_known_pending'])

    def test_unknown_reinitialization_cannot_erase_last_known_buffers(self):
        page = self.issue(); self.send(page, dirty=True)
        result = self.send(page, 1, initialized=False)
        self.assertEqual(result['summary']['state'], 'unknown')
        self.assertTrue(result['summary']['pages'][0]['buffers'][0]['dirty'])
        self.send(page, 2)
        self.assertEqual(self.registry.summary()['state'], 'clean')

    def test_only_initialized_clean_pagehide_closes_and_pageshow_can_reopen(self):
        page = self.issue()
        self.assertFalse(self.send(page, closing=True, dirty=True)['closed'])
        self.assertFalse(self.send(page, 1, closing=True, initialized=False)['closed'])
        self.assertTrue(self.send(page, 2, closing=True)['closed'])
        self.assertEqual(self.registry.summary()['counts']['closed'], 1)
        self.assertTrue(self.registry.require_stop(True)['automatic_verified'])
        self.assertFalse(self.send(page, 3, dirty=True)['closed'])
        self.assertEqual(self.registry.summary()['state'], 'pending')

    def test_new_get_invalidates_current_unknown_confirmation(self):
        self.issue(); token = self.confirm(); self.issue()
        self.error('workbench_presence_unknown', self.registry.require_stop, True, token)

    def test_every_new_report_even_same_clean_heartbeat_invalidates_confirmation(self):
        self.issue(); known = self.issue(); self.send(known)
        token = self.confirm(); self.send(known, 1)
        self.error('workbench_presence_unknown', self.registry.require_stop, True, token)

    def test_time_transition_of_another_page_invalidates_confirmation(self):
        self.issue(); known = self.issue(); self.send(known)
        token = self.confirm(); self.now += 10
        self.error('workbench_presence_unknown', self.registry.require_stop, True, token)

    def test_uninitialized_page_crossing_stale_also_invalidates_confirmation(self):
        self.issue(); token = self.confirm(); self.now += 10
        self.assertTrue(self.registry.summary()['pages'][0]['stale'])
        self.error('workbench_presence_unknown', self.registry.require_stop, True, token)

    def test_confirmed_token_expires_and_stale_fingerprint_is_not_accepted(self):
        self.issue(); token = self.confirm(); self.now += 31
        self.error('workbench_presence_unknown', self.registry.require_stop, True, token)
        old = self.registry.summary()['fingerprint']; self.issue()
        self.error('workbench_presence_changed', self.registry.confirm_unknown, old, confirmed=True)
        self.error('workbench_presence_changed', self.registry.confirm_unknown,
                   self.registry.summary()['fingerprint'], confirmed=False)

    def test_schema_rejects_body_unknown_fields_and_forgery_without_mutation(self):
        page = self.issue(); self.send(page, dirty=True)
        original = self.registry.summary()['fingerprint']
        payload = {'page_id': page['page_id'], 'page_token': page['page_token'], 'sequence': 1,
                   'initialized': True, 'buffers': []}
        for invalid in ({**payload, 'text': 'PRIVATE BODY'},
                        {**payload, 'buffers': [{'id': 'x', 'title': 'x', 'text': 'PRIVATE BODY',
                                               **dict.fromkeys(self.registry.FLAGS, False)}]},
                        {**payload, 'sequence': True}, {**payload, 'sequence': -1}):
            self.error('workbench_presence_invalid', self.registry.report, invalid)
        self.error('workbench_presence_page_unknown', self.registry.report, {**payload, 'page_token': 'forged'})
        self.assertEqual(self.registry.summary()['fingerprint'], original)
        self.assertNotIn(page['page_token'], json.dumps(self.registry.summary()))
        self.assertFalse(self.registry.summary()['legacy_services_covered'])

    def test_caps_do_not_silently_discard_pending_pages(self):
        page = self.issue(); self.send(page, dirty=True)
        for unused in range(7):self.issue()
        self.error('workbench_presence_limit', self.issue)
        buffer = {'id': 'a', 'title': 'title', **dict.fromkeys(self.registry.FLAGS, False)}
        payload = {'page_id': page['page_id'], 'page_token': page['page_token'], 'sequence': 1,
                   'initialized': True, 'buffers': [buffer] * 5}
        self.error('workbench_presence_invalid', self.registry.report, payload)
        self.assertEqual(self.registry.summary()['counts']['pending_pages'], 1)

    def test_new_stop_requires_each_page_nonce_and_does_not_accept_cross_page_ack(self):
        first, second = self.issue(), self.issue()
        self.send(first); self.send(second)
        prepared = self.registry.prepare_stop(True)
        challenge_a = self.send(first, 1)['stop_challenge']
        challenge_b = self.send(second, 1)['stop_challenge']
        self.assertNotEqual(challenge_a['nonce'], challenge_b['nonce'])
        self.error('workbench_presence_fresh_ack_invalid', self.send, first, 2, stop_ack=challenge_b)
        self.send(first, 2, stop_ack=challenge_a)
        status = self.registry.stop_status(prepared['stop_challenge'])['fresh_stop']
        self.assertFalse(status['ready'])
        self.assertEqual(status['waiting_pages'], [second['page_id']])
        self.send(second, 2, stop_ack=challenge_b)
        result = self.registry.require_stop(True, stop_challenge=prepared['stop_challenge'])
        self.assertTrue(result['fresh_verified'])
        self.assertTrue(result['automatic_verified'])
        self.error('workbench_presence_fresh_expired', self.registry.require_stop, True,
                   stop_challenge=prepared['stop_challenge'])

    def test_dirty_after_ack_invalidates_that_stop_even_after_text_becomes_clean(self):
        page = self.issue(); self.send(page)
        token = self.registry.prepare_stop(True)['stop_challenge']
        self.acknowledge(page, 1)
        self.send(page, 3, dirty=True)
        self.send(page, 4)
        self.acknowledge(page, 5)
        self.assertTrue(self.registry.stop_status(token)['fresh_stop']['failed'])
        self.error('workbench_presence_fresh_pending', self.registry.require_stop, True,
                   stop_challenge=token)
        self.assertTrue(self.registry.cancel_stop(token)['cancelled'])
        renewed = self.registry.prepare_stop(True)['stop_challenge']
        self.acknowledge(page, 7)
        self.assertTrue(self.registry.require_stop(True, stop_challenge=renewed)['fresh_verified'])

    def test_normal_report_after_ack_requires_another_fresh_ack(self):
        page = self.issue(); self.send(page)
        token = self.registry.prepare_stop(True)['stop_challenge']
        response = self.acknowledge(page, 1)
        self.assertTrue(self.registry.stop_status(token)['fresh_stop']['ready'])
        self.send(page, 3)
        self.assertFalse(self.registry.stop_status(token)['fresh_stop']['ready'])
        self.send(page, 4, stop_ack=response['stop_challenge'])
        self.assertTrue(self.registry.stop_status(token)['fresh_stop']['ready'])

    def test_expired_challenge_cannot_be_replayed_or_unlock_a_new_challenge(self):
        page = self.issue(); self.send(page)
        first = self.registry.prepare_stop(True)['stop_challenge']
        old_ack = self.send(page, 1)['stop_challenge']
        self.now += 13
        self.error('workbench_presence_fresh_expired', self.registry.stop_status, first)
        self.assertIsNone(self.registry.summary()['fresh_stop'])
        self.send(page, 2)
        second = self.registry.prepare_stop(True)['stop_challenge']
        self.assertNotEqual(first, second)
        self.error('workbench_presence_fresh_expired', self.registry.cancel_stop, first)
        self.error('workbench_presence_fresh_ack_invalid', self.send, page, 3, stop_ack=old_ack)
        self.acknowledge(page, 3)
        self.assertTrue(self.registry.require_stop(True, stop_challenge=second)['fresh_verified'])

    def test_prepare_blocks_new_pages_and_edit_actions_until_cancelled(self):
        page = self.issue(); self.send(page)
        token = self.registry.prepare_stop(True)['stop_challenge']
        self.error('workbench_presence_preparing', self.issue)
        self.error('workbench_presence_preparing', self.registry.guard_action, 'api/save')
        self.assertEqual(self.registry.summary()['counts']['registered'], 1)
        self.registry.cancel_stop(token)
        self.issue()
        self.assertEqual(self.registry.summary()['counts']['registered'], 2)

    def test_manual_unknown_scope_does_not_waive_a_reconnected_dirty_page(self):
        unknown, known = self.issue(), self.issue()
        self.send(known)
        token = self.registry.prepare_stop(True, self.confirm())['stop_challenge']
        self.acknowledge(known, 1)
        status = self.registry.stop_status(token)['fresh_stop']
        self.assertTrue(status['ready'])
        self.assertEqual(status['manually_confirmed_pages'], [unknown['page_id']])
        self.send(unknown, 0, dirty=True)
        self.error('workbench_presence_fresh_pending', self.registry.require_stop, True,
                   stop_challenge=token)
        self.assertEqual(self.registry.summary()['counts']['dirty'], 1)


if __name__ == '__main__':
    unittest.main()
