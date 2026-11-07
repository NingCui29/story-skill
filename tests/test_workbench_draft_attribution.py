"""Selecting a prior, scoped author attestation via the workbench; synthetic books only."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_platform_draft_assistant as assistant
from tests import test_platform_draft_attribution as attribution

story, drafts = assistant.story, assistant.drafts


class WorkbenchAttributionTests(attribution.AttributionEvidence, unittest.TestCase):
    seed_interrupted = attribution.AttributedCatalogRecoveryTests.seed_interrupted
    ledger = attribution.AttributedCandidateTests.ledger
    candidate = attribution.AttributedCandidateTests.candidate
    claim = attribution.AttributedCandidateTests.claim
    assert_code = attribution.AttributedCandidateTests.assert_code

    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='attribution-test-home-')
        self.addCleanup(home.cleanup)
        isolated_home = Path(home.name).resolve()
        home_patch = patch.object(Path, 'home', return_value=isolated_home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.addCleanup(lambda: self.assertFalse((isolated_home / '.story-skill/platform-drafts').exists()))
        self.fx = assistant.AssistantTaskTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        # The reusable evidence fixture otherwise points at its own imported
        # drafts module. Both halves must use the same isolated target directory.
        module = patch.object(attribution, 'drafts', drafts)
        module.start()
        self.addCleanup(module.stop)
        self.assertEqual(attribution.drafts._lock_directory(), self.fx.global_root)
        self.assertEqual(drafts._lock_directory(), self.fx.global_root)
        self.book, self.root, self.plan = self.fx.book, self.fx.root, self.fx.plan
        self.evidence_dir = self.make_evidence_dir()
        self.attempt, self.recovery_attempt, self.remote = 'a' * 32, 'b' * 32, 'private-leftover-id'
        self.path = self.seed_interrupted()
        self.historical = self.fx.prepare(attribution_path=str(self.path))
        self.fx.edit_record(self.historical, state='inspection_blocked', done=True, stage='finished')
        self.profile = self.fx.profile_path.name
        self.profile_sha = drafts._read_profile(self.root, self.profile)[1]

    def discover(self, **changes):
        args = {'expected_book_id': self.book.meta('id'), 'expected_profile_sha256': self.profile_sha,
                'plan_id': self.plan['id'], 'profile_id': self.profile, **changes}
        return drafts.workbench_assistant_attributions(self.root, **args)

    def option(self):
        return self.discover()['options'][0]['attribution_id']

    def private_state(self):
        folders = [self.fx.global_root, self.root / drafts.ASSISTANT_DIRECTORY,
                   self.root / drafts.DIRECTORY, self.evidence_dir]
        return {p: p.read_bytes() for folder in folders for p in folder.rglob('*.json')}

    def test_discovery_is_readonly_scoped_and_returns_only_safe_labels(self):
        before = self.private_state()
        listing = self.discover()
        self.assertEqual(listing['book_id'], self.book.meta('id'))
        self.assertEqual(len(listing['options']), 1)
        option = listing['options'][0]
        self.assertEqual(set(option), {'attribution_id', 'chapter', 'authorized_at', 'source_created_at'})
        self.assertEqual(option['chapter'], 1)
        self.assertNotIn(self.remote, json.dumps(listing))
        self.assertNotIn(str(self.path), json.dumps(listing))
        self.assertEqual(listing, self.discover())
        self.assertEqual(before, self.private_state())
        self.assertEqual(self.fx.operations, [])
        self.assertEqual(self.fx.launcher.calls, [])

    def test_selection_is_explicit_and_rejects_forged_ids_or_wrong_book_profile(self):
        self.assert_code('platform_draft_book_changed', self.discover, expected_book_id='another-book')
        self.assert_code('platform_draft_profile_changed', self.discover, expected_profile_sha256='f' * 64)
        self.assert_code('platform_draft_attribution_invalid', self.fx.prepare, attribution_id=str(self.path))
        self.assert_code('platform_draft_attribution_changed', self.fx.prepare, attribution_id='f' * 64)
        self.assert_code('platform_draft_attribution_invalid', self.fx.prepare, attribution_id=self.option(), mode='reconcile_only')
        ordinary = self.fx.prepare()
        self.assertIsNone(ordinary.get('attribution_id'))
        self.assertIsNone(ordinary.get('attribution_path'))
        self.assertEqual(self.fx.operations, [])

    def test_same_evidence_is_not_discovered_via_foreign_profile_plan_or_target(self):
        alias = self.fx.profile_path.with_name('different-profile.json')
        alias.write_bytes(self.fx.profile_path.read_bytes())
        self.assertEqual(self.discover(profile_id=alias.name)['options'], [])
        with drafts._assistant_directory(self.root) as tasks:
            original = drafts._assistant_read(tasks, self.historical['request_id'])
        for edit in ({'plan_id': 'b' * 32}, {'profile_sha256': 'b' * 64},
                     {'body_encoding': 'verbatim'}, {'comparison_policy': 'fanqie_terminal_lf_v1'}, {'target': True}):
            record = json.loads(json.dumps(original))
            if 'target' in edit:
                record['target']['remote_book_id'] = 'different-work'
                record['binding']['target_sha256'] = drafts._hash(record['target'])
            else:
                record['binding'].update(edit)
            record['binding_sha256'] = drafts._hash(record['binding'])
            with drafts._assistant_directory(self.root) as tasks:
                drafts._assistant_write(tasks, record)
            with patch.object(drafts, '_attributed_empty_draft', side_effect=AssertionError('Foreign path must not be opened')):
                self.assertEqual(self.discover()['options'], [])

    def test_dashboard_selection_revalidates_changed_evidence_before_prepare_and_connect(self):
        identifier = self.option()
        raw = json.loads(self.path.read_text())
        raw['authorized_by_user']['confirmed_at'] = time.time()
        self.path.write_text(json.dumps(raw))
        self.assert_code('platform_draft_attribution_changed', self.fx.prepare, attribution_id=identifier)
        prepared = self.fx.prepare(attribution_id=self.option())
        self.assertNotIn('attribution_path', prepared)
        self.assertEqual(prepared['attribution_id'], self.option())
        raw['authorized_by_user']['text'] += ' changed again'
        self.path.write_text(json.dumps(raw))
        result = self.fx.execute(prepared)
        self.assertEqual(result['error'], 'platform_draft_attribution_changed')
        self.assertFalse(result['remote_accessed'])
        self.assertEqual(self.discover()['options'], [])
        self.assertEqual(self.fx.operations, [])

    def test_already_claimed_intents_remove_the_option(self):
        identifier = self.option()
        with self.ledger() as (manifest, fingerprint, journal, _):
            proof = drafts._attributed_empty_draft(self.book, self.plan['id'], manifest, fingerprint, journal, str(self.path))
        candidate = self.candidate(proof)
        self.claim(candidate, proof)
        self.assertEqual(self.discover()['options'], [])
        self.assert_code('platform_draft_attribution_changed', self.fx.prepare, attribution_id=identifier)

    def test_existing_resolution_removes_the_option_and_old_selection_is_refused(self):
        identifier = self.option()
        with self.ledger() as (manifest, fingerprint, _, guard):
            frozen = manifest['chapters'][0]
            encoded = drafts._encoded_content(frozen, drafts.PLATFORM_BODY_ENCODING, manifest['platform'])
            observed = {**drafts._content(encoded), 'remote_id': self.remote}
            scope = {'book_id': self.book.meta('id'), 'plan_id': self.plan['id'], 'manifest_sha256': fingerprint}
            self.assertTrue(guard.resolve(1, observed, frozen=frozen, body_encoding=drafts.PLATFORM_BODY_ENCODING, scope=scope))
        self.assertEqual(self.discover()['options'], [])
        self.assert_code('platform_draft_attribution_changed', self.fx.prepare, attribution_id=identifier)

    def test_assistant_inspection_and_confirmed_sync_share_one_selected_empty_draft(self):
        identifier = self.option()
        prepared = self.fx.prepare(attribution_id=identifier)
        self.adapter.session_id = 'synthetic-session'
        with patch.object(story.platform_chrome_bridge, 'HostAdapter', side_effect=lambda *a, **kw: nullcontext(self.adapter)):
            inspected = self.fx.execute(prepared)
            self.assertTrue(inspected['can_sync'], inspected.get('error'))
            self.assertEqual(inspected['result']['changes'][0]['reason'], 'resume_empty_draft')
            self.assert_code('platform_draft_attribution_changed', self.fx.prepare, mode='sync_drafts',
                             inspection_id=prepared['request_id'], confirmed=True, attribution_id='f' * 64)
            sync = self.fx.prepare(mode='sync_drafts', inspection_id=prepared['request_id'], confirmed=True)
            self.assertEqual(sync['attribution_id'], identifier)
            self.assertNotIn('attribution_path', sync)
            result = self.fx.execute(sync)
        self.assertEqual(result['state'], 'succeeded', result.get('error'))
        self.assertEqual(result['verified_chapters'], 1)
        self.assertEqual(set(self.adapter.remote), {self.remote})
        self.assertEqual(self.discover()['options'], [])


if __name__ == '__main__':
    unittest.main()
