"""Book-local rule preferences survive service ports without publishing writes."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures
from tests.test_platform_browser import profile
from tests import test_workbench_auto_connect_ui as ui


PAIR = {'schema': 1, 'comparison_policy': 'fanqie_terminal_lf_v1', 'body_encoding': 'markdown_paragraphs_v2'}


class DraftPreferenceStorageTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(subprocess, 'Popen', side_effect=AssertionError('Preference tests cannot start a process'))
        guard.start(); self.addCleanup(guard.stop)
        self.fixture = fixtures.PublishTests(); self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.plan = self.fixture.prepare()
        self.book, self.root = self.fixture.book, self.fixture.root
        self.w = fixtures.story.workbench
        self.api = self.w.api
        self.profile_dir = self.root / self.api.platform_drafts.PROFILE_DIRECTORY
        self.profile_dir.mkdir(mode=0o700)
        self.profile = self.profile_dir / 'synthetic.json'
        self.profile.write_text(self.api.dumps(profile()), encoding='utf-8')
        self.sha = hashlib.sha256(self.profile.read_bytes()).hexdigest()
        self.directory = self.root / '.story/platform-draft-preferences'

    def rules(self, save=False, **overrides):
        args = {'expected_book_id': self.book.meta('id'), 'save': save,
                'comparison_policy': PAIR['comparison_policy'], 'body_encoding': PAIR['body_encoding'], **overrides}
        return self.w._platform_draft_rules(self.root, self.plan['id'], self.profile.name, self.sha, **args)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(self.api.StoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_preferences_are_private_and_leave_author_database_and_draft_evidence_untouched(self):
        before = self.fixture.writing_snapshot()
        self.assertIsNone(self.rules()['preference'])
        self.assertFalse(self.directory.exists())
        saved = self.rules(save=True)
        self.assertEqual(saved['preference'], PAIR)
        self.assertFalse(saved['remote_accessed'])
        self.assertFalse(saved['submission_performed'])
        self.assertEqual(self.rules()['preference'], PAIR)
        files = list(self.directory.glob('*.json'))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text())
        self.assertEqual(set(record), {'schema', 'book_id', 'plan_id', 'profile_sha256', 'comparison_policy', 'body_encoding'})
        self.assertNotIn('token', files[0].read_text())
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(self.fixture.writing_snapshot(), before)
        self.assertFalse((self.root / '.story/platform-drafts').exists())

    def test_plan_and_actual_profile_fingerprint_are_separate_scopes(self):
        self.rules(save=True)
        before = {p: p.read_bytes() for p in self.directory.glob('*.json')}
        other = self.fixture.prepare(remote_book_id='other-target')
        result = self.w._platform_draft_rules(self.root, other['id'], self.profile.name, self.sha,
                                              expected_book_id=self.book.meta('id'))
        self.assertIsNone(result['preference'])
        self.profile.write_text(json.dumps(profile(), ensure_ascii=False, indent=2), encoding='utf-8')
        old_sha, self.sha = self.sha, hashlib.sha256(self.profile.read_bytes()).hexdigest()
        self.assert_code('platform_draft_profile_changed', self.w._platform_draft_rules,
                         self.root, self.plan['id'], self.profile.name, old_sha,
                         expected_book_id=self.book.meta('id'), save=True)
        self.assertIsNone(self.rules()['preference'])
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        self.rules(save=True)
        self.assertEqual(len(list(self.directory.glob('*.json'))), 2)

    def test_invalid_or_foreign_scope_record_defaults_without_rewriting_and_explicit_save_backs_it_up(self):
        self.rules(save=True)
        path = next(self.directory.glob('*.json'))
        invalid = self.api.dumps({'schema': 1, 'body_encoding': 'markdown_paragraphs_v2', 'token': 'DO_NOT_ACCEPT'})
        path.write_text(invalid, encoding='utf-8')
        packet = self.rules()
        self.assertIsNone(packet['preference'])
        self.assertEqual(packet['warning'], 'platform_draft_preferences_invalid')
        self.assertEqual(path.read_text(), invalid)
        self.rules(save=True)
        self.assertEqual(self.rules()['preference'], PAIR)
        backups = list((self.directory / '.backups').rglob('*.json'))
        self.assertTrue(any(p.read_text() == invalid for p in backups))

    def test_bad_enum_nonfanqie_book_identity_and_profile_paths_fail_before_write(self):
        for values in ({'body_encoding': 'trim'}, {'comparison_policy': None}, {'save': 'yes'}):
            with self.subTest(values=values):
                with self.assertRaises(self.api.StoryError):
                    self.rules(save=True, **values) if 'save' not in values else self.rules(**values)
        self.assert_code('platform_draft_book_changed', self.rules, expected_book_id='other-book')
        with self.assertRaises(self.api.StoryError):
            self.w._platform_draft_rules(self.root, self.plan['id'], '../outside.json', self.sha,
                                         expected_book_id=self.book.meta('id'), save=True)
        other = self.fixture.prepare(platform='qidian')
        other_profile = self.profile_dir / 'qidian.json'
        other_profile.write_text(self.api.dumps(profile('qidian')), encoding='utf-8')
        other_sha = hashlib.sha256(other_profile.read_bytes()).hexdigest()
        self.assert_code('platform_draft_invalid_comparison_policy', self.w._platform_draft_rules,
                         self.root, other['id'], other_profile.name, other_sha, expected_book_id=self.book.meta('id'),
                         save=True, comparison_policy=PAIR['comparison_policy'], body_encoding=PAIR['body_encoding'])
        self.assertFalse(self.directory.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX link and permission contract')
    def test_directory_symlink_file_symlink_and_hardlink_never_touch_outside(self):
        outside = self.root.parent / 'outside-preferences'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'outside-original')
        self.directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, self.api.StoryError)):
            self.rules(save=True)
        self.assertEqual(sentinel.read_bytes(), b'outside-original')
        self.assertEqual(list(outside.iterdir()), [sentinel])
        self.directory.unlink(); self.rules(save=True)
        path = next(self.directory.glob('*.json')); path.unlink(); path.symlink_to(sentinel)
        with self.assertRaises((OSError, self.api.StoryError)):
            self.rules(save=True)
        path.unlink(); os.link(sentinel, path)
        with self.assertRaises((OSError, self.api.StoryError)):
            self.rules(save=True)
        self.assertEqual(sentinel.read_bytes(), b'outside-original')

    def test_cas_race_preserves_racing_record_instead_of_overwriting_it(self):
        self.rules(save=True)
        path = next(self.directory.glob('*.json'))
        racing = self.api.dumps({'racing': True})
        original = self.api.atomic_write
        def replace(*args, **kwargs):
            path.write_text(racing, encoding='utf-8')
            return original(*args, **kwargs)
        with patch.object(self.api, 'atomic_write', side_effect=replace):
            self.assert_code('export_conflict', self.rules, save=True, comparison_policy='strict', body_encoding='verbatim')
        self.assertEqual(path.read_text(), racing)

    def start_server(self):
        server = self.w.editor_server(self.root)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        return server, thread

    def stop_server(self, server, thread):
        server.shutdown(); thread.join(5); server.server_close()

    def post(self, server, action, payload, **headers):
        token = server.editor_url.rstrip('/').split('/')[-1]
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.request('POST', '/'+token+'/api/platform-drafts/'+action, json.dumps(payload),
                           {'Content-Type': 'application/json', 'X-Story-Token': token,
                            'Origin': 'http://127.0.0.1:'+str(server.server_port), **headers})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_two_actual_loopback_service_ports_read_same_book_preference_without_browser_storage(self):
        request = {'plan_id': self.plan['id'], 'profile_id': self.profile.name, 'profile_sha256': self.sha}
        first, thread = self.start_server()
        try:
            status, packet = self.post(first, 'preferences/save', {**request, **{k: PAIR[k] for k in ('comparison_policy', 'body_encoding')}})
            self.assertEqual(status, 200)
            self.assertEqual(packet['preference'], PAIR)
            old_port = first.server_port
        finally:
            self.stop_server(first, thread)
        second, thread = self.start_server()
        try:
            self.assertNotEqual(second.server_port, old_port)
            status, packet = self.post(second, 'preferences', request)
            self.assertEqual(status, 200)
            self.assertEqual(packet['preference'], PAIR)
            for headers in ({'Origin': 'https://outside.invalid'}, {'X-Story-Token': 'bad'}):
                status, _ = self.post(second, 'preferences/save', {**request, 'comparison_policy': 'strict', 'body_encoding': 'verbatim'}, **headers)
                self.assertEqual(status, 403)
            status, _ = self.post(second, 'preferences/save', {**request, **PAIR, 'path': '../outside'})
            self.assertEqual(status, 409)
            self.assertEqual(self.rules()['preference'], PAIR)
        finally:
            self.stop_server(second, thread)


PREFERENCE_UI = r'''
let testBook='book-one',testPlans=[plan()],testProfiles=[profile],testJobs=[],readHook=null,saveHook=null;
const preferenceFiles=new Map(),browserStorage=new Map();
globalThis.localStorage={getItem:key=>browserStorage.get(key)||null,setItem:(key,value)=>browserStorage.set(key,value)};
const preferenceKey=(payload,book=testBook)=>JSON.stringify([book,payload.plan_id,payload.profile_sha256]);
const preferenceReply=(payload,value,saved=false,book=testBook)=>({ok:true,book_id:book,plan_id:payload.plan_id,profile_id:payload.profile_id,profile_sha256:payload.profile_sha256,preference:value,warning:null,saved,remote_accessed:false,submission_performed:false});
const previewReply=payload=>({...ready(payload),book_id:testBook,profile_sha256:payload.profile_id?testProfiles.find(row=>row.id===payload.profile_id)?.sha256:null,comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding});
handler=async(action,payload)=>{
 const book=testBook;
 if(action.endsWith('/catalog'))return {...catalog(testJobs),book_id:book,plans:testPlans,profiles:testProfiles};
 if(action.endsWith('/preferences/save')){const save=()=>{const value={schema:1,comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding};preferenceFiles.set(preferenceKey(payload,book),structuredClone(value));return preferenceReply(payload,value,true,book);};return saveHook?saveHook(payload,save):save();}
 if(action.endsWith('/preferences')){const read=()=>preferenceReply(payload,structuredClone(preferenceFiles.get(preferenceKey(payload,book))||null),false,book);return readHook?readHook(payload,read):read();}
 if(action.endsWith('/preview'))return previewReply(payload);
 if(action.endsWith('/start'))return {...job(),book_id:book,plan_id:payload.plan_id,profile_id:payload.profile_id,comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding};
 if(action.endsWith('/job'))return job();
 throw Error(action);
};
const chooseRules=async p=>{p.encoding.value='markdown_paragraphs_v2';await p.encoding.onchange();p.comparison.value='fanqie_terminal_lf_v1';await p.comparison.onchange();};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const previews=()=>requests.filter(row=>row.action.endsWith('/preview'));
const saves=()=>requests.filter(row=>row.action.endsWith('/preferences/save'));
'''


class DraftPreferenceUITests(unittest.TestCase):
    def js(self, assertions):
        ui.WorkbenchAutoConnectUITests.js(self, PREFERENCE_UI + assertions)

    def test_reload_new_origin_and_empty_browser_storage_restore_book_rules_with_fresh_preview(self):
        self.js(r'''
await platformDraftOpen();const first=platformDraftPanel;await chooseRules(first);
const initial=previews().length,oldToken=first.preview.preview_token,before=JSON.stringify([...docs]);
assert.equal(preferenceFiles.size,1);const stored=[...preferenceFiles.values()][0];
assert.deepEqual(Object.keys(stored).sort(),['body_encoding','comparison_policy','schema']);
assert.ok(!JSON.stringify([...preferenceFiles]).includes(oldToken));assert.equal(browserStorage.size,0);
first.dialog.close();browserStorage.clear();globalThis.location={origin:'http://127.0.0.1:another-port'};
await platformDraftOpen();const reopened=platformDraftPanel;
assert.notEqual(reopened,first);assert.equal(reopened.encoding.value,'markdown_paragraphs_v2');
assert.equal(reopened.comparison.value,'fanqie_terminal_lf_v1');assert.equal(previews().length,initial+1);
assert.equal(reopened.preview.body_encoding,'markdown_paragraphs_v2');assert.equal(starts().length,0);
assert.ok(reopened.rulesNote.textContent.includes('保存在本书内'));assert.equal(JSON.stringify([...docs]),before);
''')

    def test_plan_profile_fingerprint_and_book_changes_never_inherit_another_scope(self):
        self.js(r'''
testPlans=[plan(),plan('plan-two')];await platformDraftOpen();let p=platformDraftPanel;await chooseRules(p);
p.plan.value='plan-two';await p.plan.onchange();assert.equal(p.encoding.value,'verbatim');assert.equal(p.comparison.value,'strict');
p.plan.value='plan-one';await p.plan.onchange();assert.equal(p.encoding.value,'markdown_paragraphs_v2');
testProfiles=[{...profile,sha256:'changed-fingerprint'}];await platformDraftCatalog(p);
assert.equal(p.encoding.value,'verbatim');assert.equal(p.comparison.value,'strict');
testProfiles=[profile];await platformDraftCatalog(p);assert.equal(p.encoding.value,'markdown_paragraphs_v2');
p.dialog.close();testBook='book-two';await platformDraftOpen();p=platformDraftPanel;assert.equal(p.encoding.value,'verbatim');
p.dialog.close();testBook='book-one';await platformDraftOpen();p=platformDraftPanel;assert.equal(p.encoding.value,'markdown_paragraphs_v2');
assert.equal(starts().length,0);assert.equal(saves().length,2);
''')

    def test_completed_strict_or_other_plan_jobs_do_not_replace_explicit_selection(self):
        self.js(r'''
testPlans=[plan(),plan('plan-two')];await platformDraftOpen();const p=platformDraftPanel;await chooseRules(p);
testJobs=[{...job(),done:true,state:'failed',profile_id:profile.id,comparison_policy:'strict',body_encoding:'verbatim'},
 {...job(),id:'other-done',done:true,state:'failed',plan_id:'plan-two',profile_id:profile.id}];
await platformDraftCatalog(p);assert.equal(p.plan.value,'plan-one');assert.equal(p.encoding.value,'markdown_paragraphs_v2');
assert.equal(p.comparison.value,'fanqie_terminal_lf_v1');assert.ok(p.status.textContent.includes('历史任务'));
assert.equal(p.preview,null);const saved=saves().length;await p.recheck.onclick();
assert.equal(p.preview.body_encoding,'markdown_paragraphs_v2');assert.equal(saves().length,saved);
testJobs=[testJobs[1]];await platformDraftCatalog(p);assert.equal(p.plan.value,'plan-one');assert.equal(p.encoding.value,'markdown_paragraphs_v2');
assert.equal(p.preview.body_encoding,'markdown_paragraphs_v2');assert.equal(starts().length,0);
''')

    def test_save_invalidates_old_token_and_serializes_explicit_changes_before_preview(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel,initial=previews().length;let release;
saveHook=(payload,save)=>new Promise(resolve=>{release=()=>resolve(save());});
p.encoding.value='markdown_paragraphs_v2';const first=p.encoding.onchange();await flush();
assert.equal(p.preview,null);assert.equal(p.previewExpires,0);assert.equal(p.run.disabled,true);
await p.run.onclick();assert.equal(starts().length,0);assert.equal(saves().length,1);
p.comparison.value='fanqie_terminal_lf_v1';const second=p.comparison.onchange();await flush();assert.equal(saves().length,1);
const finishFirst=release;saveHook=null;finishFirst();await first;await second;
assert.equal(saves().length,2);assert.equal(previews().length,initial+1);
assert.equal(p.preview.comparison_policy,'fanqie_terminal_lf_v1');assert.equal(p.preview.body_encoding,'markdown_paragraphs_v2');
assert.deepEqual([...preferenceFiles.values()][0],{schema:1,comparison_policy:'fanqie_terminal_lf_v1',body_encoding:'markdown_paragraphs_v2'});
''')

    def test_late_restore_or_save_receipt_cannot_change_the_new_scope(self):
        self.js(r'''
testPlans=[plan(),plan('plan-two')];await platformDraftOpen();const p=platformDraftPanel;let release;
readHook=(payload,read)=>payload.plan_id==='plan-one'?new Promise(resolve=>{release=()=>resolve(preferenceReply(payload,{schema:1,body_encoding:'markdown_paragraphs_v2',comparison_policy:'fanqie_terminal_lf_v1'}));}):read();
const old=p.profile.onchange();await flush();p.plan.value='plan-two';await p.plan.onchange();release();await old;
assert.equal(p.plan.value,'plan-two');assert.equal(p.encoding.value,'verbatim');assert.equal(p.preview.plan_id,'plan-two');
readHook=null;saveHook=(payload,save)=>new Promise(resolve=>{release=()=>resolve(save());});
p.encoding.value='markdown_paragraphs_v2';const saving=p.encoding.onchange();await flush();
p.plan.value='plan-one';await p.plan.onchange();release();await saving;
assert.equal(p.plan.value,'plan-one');assert.equal(p.encoding.value,'verbatim');assert.equal(p.preview.plan_id,'plan-one');
assert.equal(starts().length,0);
''')

    def test_bad_server_preferences_unavailable_storage_and_nonfanqie_use_defaults(self):
        self.js(r'''
for(const value of [{schema:2,body_encoding:'markdown_paragraphs_v2',comparison_policy:'fanqie_terminal_lf_v1'},
 {schema:1,body_encoding:'trim',comparison_policy:'strict'},
 {schema:1,body_encoding:'markdown_paragraphs_v2',comparison_policy:'fanqie_terminal_lf_v1',token:'bad'}]){
 readHook=(payload)=>preferenceReply(payload,value);await platformDraftOpen();const p=platformDraftPanel;
 assert.equal(p.encoding.value,'verbatim');assert.equal(p.comparison.value,'strict');assert.ok(p.rulesNote.textContent.includes('安全默认值'));p.dialog.close();
}
readHook=()=>{throw Error('storage unavailable');};await platformDraftOpen();let p=platformDraftPanel;
assert.equal(p.encoding.value,'verbatim');assert.equal(p.comparison.value,'strict');p.dialog.close();
testPlans=[{...plan(),platform:'qidian'}];testProfiles=[{...profile,platform:'qidian'}];
readHook=payload=>preferenceReply(payload,{schema:1,body_encoding:'markdown_paragraphs_v2',comparison_policy:'fanqie_terminal_lf_v1'});
await platformDraftOpen();p=platformDraftPanel;assert.equal(p.encoding.value,'verbatim');assert.equal(p.comparison.value,'strict');
assert.equal(p.encoding.disabled,true);assert.equal(p.comparison.disabled,true);assert.equal(saves().length,0);assert.equal(starts().length,0);
''')

    def test_preview_marks_conflicts_unknowns_and_their_actual_rules_as_history(self):
        self.js(r'''
const old=handler;handler=async(action,payload)=>{const packet=await old(action,payload);if(action.endsWith('/preview'))packet.preview.results=[
 {chapter:12,state:'conflict',reason:'remote_content_differs',observed_this_run:true,body_encoding:'verbatim',comparison_policy:'strict'},
 {chapter:13,state:'unknown',reason:'interrupted_sending',body_encoding:'markdown_paragraphs_v2',comparison_policy:'fanqie_terminal_lf_v1'}];return packet;};
await platformDraftOpen();const p=platformDraftPanel,rows=p.results.children;
assert.ok(rows[0].textContent.includes('历史状态：内容冲突'));assert.ok(rows[0].textContent.includes('保留原始换行'));
assert.ok(rows[1].textContent.includes('历史状态：结果未知'));assert.ok(rows[1].textContent.includes('去掉开头空段'));
assert.ok(rows.every(row=>row.textContent.includes('本次尚未访问平台')));assert.equal(starts().length,0);
platformDraftDrawJob(p,{...job(),done:true,state:'partial_failure',result:{results:[{chapter:12,state:'reconciled_equivalent',observed_this_run:true,accepted_this_run:true,body_encoding:'markdown_paragraphs_v2'}]}});
assert.ok(p.results.children[0].textContent.includes('本次按段落排版核对'));
assert.ok(!p.results.children[0].textContent.includes('本次尚未访问平台'));
''')

    def test_matching_scoped_history_only_explains_rules_and_never_adopts_or_saves_them(self):
        self.js(r'''
const old=handler;handler=async(action,payload)=>{const packet=await old(action,payload);if(action.endsWith('/preview')){
 packet.manifest_sha256='a'.repeat(64);packet.target_sha256='b'.repeat(64);
 packet.preview.recent_events=[{schema:2,event:'reconciled_equivalent',book_id:'book-one',plan_id:'plan-one',manifest_sha256:packet.manifest_sha256,target_sha256:packet.target_sha256,
 chapter:1,remote_id:'remote',title_matches:true,body_matches:false,body_encoding:'markdown_paragraphs_v2',comparison_policy:'fanqie_terminal_lf_v1',canonical_sha256:'c'.repeat(64),observed_canonical_sha256:'c'.repeat(64)}];}return packet;};
await platformDraftOpen();const p=platformDraftPanel;assert.ok(p.rulesNote.textContent.includes('当前选择与该历史记录不同'));
assert.ok(p.rulesNote.textContent.includes('去掉开头空段'));assert.ok(p.rulesNote.textContent.includes('允许末尾换行数量增加或减少'));
assert.equal(p.encoding.value,'verbatim');assert.equal(p.comparison.value,'strict');assert.equal(starts().length,0);assert.equal(saves().length,0);
p.rulesMemoryMessage='';const packet=structuredClone(p.preview);packet.preview.recent_events[0].book_id='different-book';platformDraftRuleNote(p,packet);
assert.ok(!p.rulesNote.textContent.includes('当前选择与该历史记录不同'));
''')
