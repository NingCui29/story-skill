"""Persist successful target bindings without migrating old profiles or evidence."""
import json
import unittest
from unittest.mock import patch

from tests import test_workbench_platform_binding as binding
from tests import test_workbench_profile_selection_ui as selection_ui


class ProfilePreferenceTests(unittest.TestCase):
    setUp = binding.PlatformBindingTests.setUp
    bind = binding.PlatformBindingTests.bind
    profiles = binding.PlatformBindingTests.profiles

    def selection(self, plan_id):
        packet = binding.drafts.workbench_catalog(self.root, expected_book_id=self.book_id)
        return next(plan for plan in packet['plans'] if plan['id'] == plan_id)['profile_selection']

    def old_and_new(self):
        original = binding.story.platform_browser.fanqie_profile_from_book_url
        def previous(url):
            result = original(url)
            result['profile']['chapter_lists'][0]['empty'] = None
            return result
        with patch.object(binding.story.platform_browser, 'fanqie_profile_from_book_url', side_effect=previous):
            old = self.bind()
        before = {p: p.read_bytes() for p in self.profiles()}
        new = self.bind()
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        return old, new

    def test_new_binding_persists_across_fresh_book_reads_and_preserves_profiles_history(self):
        old, new = self.old_and_new()
        self.assertEqual(old['plan_id'], new['plan_id'])
        self.assertNotEqual(old['profile_id'], new['profile_id'])
        expected = {'status': 'selected', 'profile_id': new['profile_id'],
                    'profile_sha256': new['profile_sha256']}
        writing = self.fixture.writing_snapshot()
        journal = binding.drafts.status(self.book, new['plan_id'])
        self.assertEqual(self.selection(new['plan_id']), expected)
        # Catalog opens a fresh Book; no process-local preference registry is used.
        self.assertEqual(self.selection(new['plan_id']), expected)
        self.assertEqual(self.fixture.writing_snapshot(), writing)
        self.assertEqual(binding.drafts.status(self.book, new['plan_id']), journal)
        files = list((self.root / '.story/platform-draft-preferences').glob('profile-*.json'))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text())
        self.assertEqual(record['source_plan_id'], new['plan_id'])
        self.assertEqual(record['book_id'], self.book_id)
        if binding.os.name == 'posix':
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(files[0].parent.stat().st_mode & 0o777, 0o700)

    def test_new_plan_and_range_inherit_exact_target_preference(self):
        _, new = self.old_and_new()
        self.fixture.commit(chapter=2)
        refreshed = binding.drafts.workbench_refresh_plan(
            self.root, new['plan_id'], new['profile_id'], expected_book_id=self.book_id,
            expected_revision=self.book.meta('revision'), expected_profile_sha256=new['profile_sha256'],
            start=1, end=2)
        self.assertNotEqual(refreshed['plan_id'], new['plan_id'])
        self.assertEqual(self.selection(refreshed['plan_id']), self.selection(new['plan_id']))

    def test_same_title_other_account_and_target_have_independent_preferences(self):
        generator = binding.story.platform_browser.fanqie_profile_from_book_url
        def previous(url):
            result = generator(url)
            result['profile']['chapter_lists'][0]['empty'] = None
            return result
        with patch.object(binding.story.platform_browser, 'fanqie_profile_from_book_url', side_effect=previous):
            original = self.bind()
            other_account = self.bind(account_id='999999999')
            other_target = self.bind(book_management_url=binding.URL.replace('1234567890&', '9876543210&'))
        updated = self.bind()
        self.assertNotEqual(original['profile_id'], updated['profile_id'])
        self.assertEqual(self.selection(original['plan_id'])['profile_id'], updated['profile_id'])
        self.assertEqual(self.selection(other_account['plan_id'])['profile_id'], other_account['profile_id'])
        self.assertEqual(self.selection(other_target['plan_id'])['profile_id'], other_target['profile_id'])
        self.assertEqual(len(list((self.root / '.story/platform-draft-preferences').glob('profile-*.json'))), 3)

    def test_missing_changed_and_invalid_profile_do_not_fall_back(self):
        old, new = self.old_and_new()
        path = self.profile_dir / new['profile_id']
        raw = path.read_bytes()
        path.unlink()
        self.assertEqual(self.selection(new['plan_id'])['status'], 'missing')
        path.write_bytes(raw + b'\n')
        self.assertEqual(self.selection(new['plan_id'])['status'], 'changed')
        path.write_text('{}')
        self.assertEqual(self.selection(new['plan_id'])['status'], 'invalid')
        self.assertTrue((self.profile_dir / old['profile_id']).is_file())

    def test_invalid_or_foreign_preference_is_blocked_and_never_imported(self):
        new = self.bind()
        path = next((self.root / '.story/platform-draft-preferences').glob('profile-*.json'))
        record = json.loads(path.read_text())
        for key in ('book_id', 'account_id', 'remote_book_id'):
            altered = {**record, key: 'another'}
            path.write_text(binding.story.dumps(altered))
            self.assertEqual(self.selection(new['plan_id']), {'status': 'invalid'})
        path.write_text('{broken')
        self.assertEqual(self.selection(new['plan_id']), {'status': 'invalid'})

    def test_failed_prepare_does_not_replace_preference_and_rules_files_coexist(self):
        old = self.bind()
        rules = binding.story.workbench._platform_draft_rules(
            self.root, old['plan_id'], old['profile_id'], old['profile_sha256'],
            expected_book_id=self.book_id, save=True, comparison_policy='strict', body_encoding='plain_paragraphs_v1')
        before = self.selection(old['plan_id'])
        with patch.object(binding.story.publish, 'prepare', side_effect=binding.story.StoryError('synthetic', 'safe')):
            self.fixture.assert_code('synthetic', self.bind)
        self.assertEqual(self.selection(old['plan_id']), before)
        restored = binding.story.workbench._platform_draft_rules(
            self.root, old['plan_id'], old['profile_id'], old['profile_sha256'], expected_book_id=self.book_id)
        self.assertEqual(restored['preference'], rules['preference'])

    def test_explicit_rebind_repairs_bad_or_noncanonical_json_and_keeps_exact_backup(self):
        new = self.bind()
        path = next((self.root / '.story/platform-draft-preferences').glob('profile-*.json'))
        valid = json.loads(path.read_text())
        for raw in (b'{broken', b'', json.dumps(valid, ensure_ascii=False, indent=2).encode() + b'\n'):
            with self.subTest(raw=raw[:20]):
                path.write_bytes(raw)
                self.assertEqual(self.selection(new['plan_id']), {'status': 'invalid'})
                rebound = self.bind()
                self.assertEqual(self.selection(new['plan_id'])['profile_id'], rebound['profile_id'])
                backups = list((path.parent / '.backups').glob('*/' + path.name))
                self.assertIn(raw, [backup.read_bytes() for backup in backups])

    def test_repair_never_follows_preference_symlink(self):
        new = self.bind()
        path = next((self.root / '.story/platform-draft-preferences').glob('profile-*.json'))
        outside = self.root.parent / 'private-preference-victim'
        outside.write_bytes(b'untouched')
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(binding.story.StoryError):
            self.bind()
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_bytes(), b'untouched')


class ProfilePreferenceUITests(unittest.TestCase):
    js = selection_ui.ProfileSelectionUITests.js
    fixture = selection_ui.ProfileSelectionUITests.fixture + r'''
const renewed={...currentProfile,id:'fanqie-'+ 'd'.repeat(32)+'.json'};
let savedChoice={status:'selected',profile_id:renewed.id,profile_sha256:renewed.sha256};
availableProfiles=[renewed,currentProfile];
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog([plan('plan-1',{profile_selection:savedChoice}),oldPlan()],availableJobs,{profiles:availableProfiles});
 if(action.endsWith('/formal-state'))return formal();
 if(action.endsWith('/preview'))return preview(payload);
 throw Error(action);
};
'''

    def test_fresh_open_and_reopen_prefer_successful_binding_not_random_id(self):
        self.js(self.fixture + r'''
await platformDraftOpen();let p=platformDraftPanel;
assert.equal(p.profile.value,renewed.id);assert.ok(p.profile.options.at(-1).textContent.includes('最近绑定'));
p.dialog.close();await platformDraftOpen();p=platformDraftPanel;
assert.equal(p.profile.value,renewed.id);assert.equal(starts().length,0);p.dialog.close();
''')

    def test_explicit_old_profile_lasts_only_current_panel(self):
        self.js(self.fixture + r'''
await platformDraftOpen();let p=platformDraftPanel;p.profile.value=currentProfile.id;await p.profile.onchange();
assert.equal(p.profile.value,currentProfile.id);await p.refresh.onclick();assert.equal(p.profile.value,currentProfile.id);
p.dialog.close();await platformDraftOpen();p=platformDraftPanel;assert.equal(p.profile.value,renewed.id);
assert.equal(starts().length,0);p.dialog.close();
''')

    def test_refresh_observes_other_window_rebinding_unless_manually_selected(self):
        self.js(self.fixture + r'''
savedChoice={status:'selected',profile_id:currentProfile.id,profile_sha256:currentProfile.sha256};
await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.profile.value,currentProfile.id);
savedChoice={status:'selected',profile_id:renewed.id,profile_sha256:renewed.sha256};
await p.refresh.onclick();assert.equal(p.profile.value,renewed.id);assert.equal(p.inspection,null);
assert.equal(starts().length,0);p.dialog.close();
''')

    def test_unavailable_or_changed_saved_profile_requires_explicit_reselection(self):
        self.js(self.fixture + r'''
for(const status of ['missing','changed','invalid']){
 savedChoice={status,profile_id:renewed.id,profile_sha256:'f'.repeat(64)};
 await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.profile.value,'');assert.equal(p.preview,null);
 assert.equal(p.inspection,null);assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('重新选择'));
 p.profile.value=currentProfile.id;await p.profile.onchange();assert.equal(p.profile.value,currentProfile.id);
 assert.ok(p.preview);assert.equal(p.inspection,null);assert.equal(starts().length,0);p.dialog.close();
}
''')

    def test_changed_explicit_profile_does_not_silently_fall_back_to_preference(self):
        self.js(self.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;p.profile.value=currentProfile.id;await p.profile.onchange();
availableProfiles=[renewed,{...currentProfile,sha256:'f'.repeat(64)}];await p.refresh.onclick();
assert.equal(p.profile.value,'');assert.equal(p.inspection,null);assert.equal(p.run.disabled,true);
assert.ok(p.status.textContent.includes('变化'));p.dialog.close();
''')

    def test_old_profile_inspection_is_not_recovered_for_new_preferred_profile(self):
        self.js(self.fixture + r'''
availableJobs=[{...inspected(),profile_id:currentProfile.id,profile_sha256:currentProfile.sha256}];
await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.profile.value,renewed.id);
assert.equal(p.inspection,null);assert.ok(!p.run.textContent.includes('确认同步'));
assert.equal(starts().length,0);p.dialog.close();
''')

    def test_invalid_preference_cannot_select_foreign_target_and_active_job_stays_bound(self):
        self.js(self.fixture + r'''
savedChoice={status:'selected',profile_id:oldProfile.id,profile_sha256:oldProfile.sha256};
availableProfiles=[renewed,currentProfile,oldProfile];await platformDraftOpen();let p=platformDraftPanel;
assert.equal(p.profile.value,'');assert.equal(p.run.disabled,true);p.dialog.close();
savedChoice={status:'selected',profile_id:renewed.id,profile_sha256:renewed.sha256};
availableJobs=[job({profile_id:currentProfile.id,profile_sha256:currentProfile.sha256})];
await platformDraftOpen();p=platformDraftPanel;assert.equal(p.profile.value,currentProfile.id);
assert.equal(p.job.id,availableJobs[0].id);assert.equal(starts().length,0);p.dialog.close();
''')


if __name__ == '__main__':
    unittest.main()
